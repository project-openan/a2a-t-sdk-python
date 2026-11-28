"""Regression tests for the P0 media credential leak and the P1-1 media semantics.

Covers (task-4 items 1-4):
- presigned downloads never carry Authorization/Cookie, only a User-Agent;
- oversize (declared or streamed) -> status "skipped", exact skip warning, no retry;
- 401/403 -> refresh the media metadata once and retry once;
- network failure -> warning, no partial file left behind;
- no failure path leaves a file in ``media/`` and failed media never reaches the manifest;
- download requests are recorded as ``media/<mediaId>`` without the presigned URL.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from langfuse_export import cli
from langfuse_export import client as client_module
from langfuse_export.client import ApiError, AuthError, LangfuseClient, MediaTooLarge, NetworkError
from langfuse_export.models import MediaRef

from fix_support import (
    PRESIGNED_URL,
    TRACE,
    FakeBackend,
    FakeResponse,
    install_media_router,
    install_session_router,
    make_client,
    make_row,
    media_token,
    set_env,
    wire_cli_backend,
    workdir,
)

MEDIA_ID = "med_1"


def media_ref(media_id: str = MEDIA_ID, mime: str = "image/png", field: str = "metadata") -> MediaRef:
    return MediaRef(
        media_id=media_id,
        mime_type=mime,
        source_type="base64_data_uri",
        field=field,
        raw_token=media_token(media_id, mime),
    )


def private_media(**overrides) -> dict:
    info = {
        "url": PRESIGNED_URL,
        "contentType": "image/png",
        "contentLength": 3,
        "urlExpiry": "2026-11-07T10:35:00Z",
    }
    info.update(overrides)
    return info


# -- LangfuseClient.download: security --------------------------------------
def test_download_never_sends_credentials(monkeypatch):
    client = make_client(monkeypatch)
    calls: list[tuple[str, dict]] = []

    def fake_get(url, **kwargs):
        calls.append((url, dict(kwargs)))
        return FakeResponse(200, chunks=[b"abc"])

    # The authenticated session must not be used for presigned (third-party) URLs.
    def forbidden(*args, **kwargs):
        raise AssertionError("download must not reuse the authenticated session")

    monkeypatch.setattr(client.session, "get", forbidden)
    monkeypatch.setattr(client_module.requests, "get", fake_get)

    with workdir("media_security") as tmp:
        dest = tmp / "m.png"
        assert client.download(PRESIGNED_URL, dest, max_bytes=1024) == 3
        assert dest.read_bytes() == b"abc"

    url, kwargs = calls[0]
    assert url == PRESIGNED_URL
    headers = kwargs["headers"]
    assert "Authorization" not in headers
    assert "Cookie" not in headers
    assert headers.get("User-Agent") == client_module.USER_AGENT
    assert kwargs["stream"] is True


def test_download_records_label_instead_of_presigned_url(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setattr(client_module.requests, "get", lambda url, **kw: FakeResponse(200, chunks=[b"x"]))
    with workdir("media_record") as tmp:
        client.download(PRESIGNED_URL, tmp / "m.bin", max_bytes=1024, label=f"media/{MEDIA_ID}")
    record = client.records[-1]
    assert record.endpoint == f"media/{MEDIA_ID}"
    assert record.status == 200
    assert "X-Amz-Signature" not in record.endpoint
    assert PRESIGNED_URL not in record.endpoint


def test_media_metadata_get_is_recorded(monkeypatch):
    client = make_client(monkeypatch)
    install_session_router(
        monkeypatch,
        client,
        lambda url, params: FakeResponse(200, {"url": PRESIGNED_URL, "contentType": "image/png"}),
    )
    from langfuse_export.backend_v4 import V4Backend

    V4Backend(client).fetch_media(MEDIA_ID)
    assert client.records[-1].endpoint == f"/api/public/media/{MEDIA_ID}"


# -- LangfuseClient.download: failure mapping ------------------------------
def test_download_declared_length_too_large_raises_and_cleans_up(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setattr(
        client_module.requests,
        "get",
        lambda url, **kw: FakeResponse(200, headers={"Content-Length": "5000"}, chunks=[b"abc"]),
    )
    with workdir("media_declared_download") as tmp:
        dest = tmp / "m.png"
        with pytest.raises(MediaTooLarge):
            client.download(PRESIGNED_URL, dest, max_bytes=1000)
        assert not dest.exists()
    assert client.records[-1].status == 200
    assert client.records[-1].error == "MediaTooLarge"


def test_download_streamed_length_too_large_leaves_no_partial_file(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setattr(
        client_module.requests,
        "get",
        lambda url, **kw: FakeResponse(200, chunks=[b"a" * 64 * 1024, b"b" * 64 * 1024]),
    )
    with workdir("media_stream_download") as tmp:
        dest = tmp / "m.bin"
        with pytest.raises(MediaTooLarge):
            client.download(PRESIGNED_URL, dest, max_bytes=1000)
        assert not dest.exists()


def test_download_403_maps_to_api_error_with_status(monkeypatch):
    client = make_client(monkeypatch)
    monkeypatch.setattr(client_module.requests, "get", lambda url, **kw: FakeResponse(403))
    with workdir("media_403_download") as tmp:
        with pytest.raises(ApiError) as info:
            client.download(PRESIGNED_URL, tmp / "m.bin", max_bytes=1024)
        assert not (tmp / "m.bin").exists()
    assert info.value.status == 403
    assert client.records[-1].status == 403


def test_download_network_error_maps_to_network_error(monkeypatch):
    client = make_client(monkeypatch)

    def explode(url, **kwargs):
        raise requests.Timeout("read timeout")

    monkeypatch.setattr(client_module.requests, "get", explode)
    with workdir("media_net_download") as tmp:
        with pytest.raises(NetworkError):
            client.download(PRESIGNED_URL, tmp / "m.bin", max_bytes=1024)
        assert not (tmp / "m.bin").exists()
    assert client.records[-1].status is None
    assert client.records[-1].error == "Timeout"


# -- _download_media: oversize is "skipped", never retried ------------------
def test_media_declared_oversize_is_skipped_without_download(monkeypatch):
    client = make_client(monkeypatch)
    client.download = lambda *a, **kw: pytest.fail("declared oversize must not be downloaded")
    backend = FakeBackend(media={MEDIA_ID: private_media(contentLength=5000)}, client=client)
    warnings: list[str] = []
    with workdir("media_declared_skip") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 1000, warnings)
        assert list(media_dir.iterdir()) == []
    asset = assets[0]
    assert asset.status == "skipped"
    assert asset.error
    assert warnings == [f"media skipped for {MEDIA_ID}: exceeds 1000 bytes"]
    # Metadata fetched once, no refresh, no presigned request at all.
    assert backend.fetch_media_calls == [MEDIA_ID]


def test_media_streamed_oversize_is_skipped_without_refresh(monkeypatch):
    client = make_client(monkeypatch)
    calls = install_media_router(
        monkeypatch,
        [FakeResponse(200, chunks=[b"a" * 64 * 1024, b"b" * 64 * 1024])],
    )
    backend = FakeBackend(media={MEDIA_ID: private_media(contentLength=None)}, client=client)
    warnings: list[str] = []
    with workdir("media_stream_skip") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 1000, warnings)
        assert list(media_dir.iterdir()) == []
    assert assets[0].status == "skipped"
    assert warnings == [f"media skipped for {MEDIA_ID}: exceeds 1000 bytes"]
    assert len(calls) == 1  # downloaded once, no refresh/retry
    assert backend.fetch_media_calls == [MEDIA_ID]
    assert client.records[-1].endpoint == f"media/{MEDIA_ID}"


# -- _download_media: 403 refresh, network failure, empty body --------------
def test_media_403_refreshes_once_then_succeeds(monkeypatch):
    client = make_client(monkeypatch)
    calls = install_media_router(
        monkeypatch,
        [FakeResponse(403), FakeResponse(200, chunks=[b"hello"])],
    )
    backend = FakeBackend(
        media={MEDIA_ID: [private_media(contentLength=None), private_media(url="https://bucket.example.test/fresh")]},
        client=client,
    )
    warnings: list[str] = []
    with workdir("media_refresh") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 10_000, warnings)
        assert (media_dir / f"{MEDIA_ID}.png").read_bytes() == b"hello"
    asset = assets[0]
    assert asset.status == "downloaded"
    assert asset.local_path == f"media/{MEDIA_ID}.png"
    assert warnings == []
    assert backend.fetch_media_calls == [MEDIA_ID, MEDIA_ID]  # one refresh
    assert len(calls) == 2
    assert [r.status for r in client.records] == [403, 200]


def test_media_second_403_fails_with_warning_and_no_file(monkeypatch):
    client = make_client(monkeypatch)
    install_media_router(monkeypatch, [FakeResponse(403), FakeResponse(403)])
    backend = FakeBackend(media={MEDIA_ID: [private_media(), private_media()]}, client=client)
    warnings: list[str] = []
    with workdir("media_403_twice") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 10_000, warnings)
        assert list(media_dir.iterdir()) == []
    assert assets[0].status == "failed"
    assert assets[0].local_path is None
    assert backend.fetch_media_calls == [MEDIA_ID, MEDIA_ID]
    assert len(warnings) == 1
    assert warnings[0].startswith(f"media download failed for {MEDIA_ID}")


def test_media_metadata_failure_warns_and_leaves_no_file(monkeypatch):
    client = make_client(monkeypatch)
    backend = FakeBackend(media={MEDIA_ID: ApiError("HTTP 500", status=500)}, client=client)
    warnings: list[str] = []
    with workdir("media_meta_fail") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 10_000, warnings)
        assert list(media_dir.iterdir()) == []
    assert assets[0].status == "failed"
    assert assets[0].local_path is None
    assert warnings == [f"media metadata failed for {MEDIA_ID}: ApiError"]


def test_media_http_500_fails_without_refresh(monkeypatch):
    client = make_client(monkeypatch)
    calls = install_media_router(monkeypatch, [FakeResponse(500)])
    backend = FakeBackend(media={MEDIA_ID: private_media()}, client=client)
    warnings: list[str] = []
    with workdir("media_500") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 10_000, warnings)
        assert list(media_dir.iterdir()) == []
    assert assets[0].status == "failed"
    assert backend.fetch_media_calls == [MEDIA_ID]  # only 401/403 may refresh
    assert len(calls) == 1
    assert warnings[0].startswith(f"media download failed for {MEDIA_ID}")
    assert client.records[-1].endpoint == f"media/{MEDIA_ID}"
    assert client.records[-1].status == 500


def test_media_network_error_warns_and_leaves_no_file(monkeypatch):
    client = make_client(monkeypatch)
    install_media_router(monkeypatch, [requests.Timeout("slow")])
    backend = FakeBackend(media={MEDIA_ID: private_media()}, client=client)
    warnings: list[str] = []
    with workdir("media_network") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 10_000, warnings)
        assert list(media_dir.iterdir()) == []
    assert assets[0].status == "failed"
    assert len(warnings) == 1
    assert "network error" in warnings[0]
    assert backend.fetch_media_calls == [MEDIA_ID]  # no refresh for a transport error


def test_media_empty_body_fails_without_file(monkeypatch):
    client = make_client(monkeypatch)
    install_media_router(monkeypatch, [FakeResponse(200, chunks=[])])
    backend = FakeBackend(media={MEDIA_ID: private_media(contentLength=None)}, client=client)
    warnings: list[str] = []
    with workdir("media_empty") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(backend, [media_ref()], media_dir, 10_000, warnings)
        assert list(media_dir.iterdir()) == []
    assert assets[0].status == "failed"
    assert "empty" in assets[0].error


def test_media_metadata_auth_error_propagates(monkeypatch):
    client = make_client(monkeypatch)
    backend = FakeBackend(media={MEDIA_ID: AuthError("bad keys")}, client=client)
    with workdir("media_auth") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        with pytest.raises(AuthError):
            cli._download_media(backend, [media_ref()], media_dir, 10_000, [])


def test_media_is_deduplicated_by_id(monkeypatch):
    client = make_client(monkeypatch)
    calls = install_media_router(monkeypatch, [FakeResponse(200, chunks=[b"abc"])])
    backend = FakeBackend(media={MEDIA_ID: private_media()}, client=client)
    with workdir("media_dedup") as tmp:
        media_dir = tmp / "media"
        media_dir.mkdir()
        assets = cli._download_media(
            backend, [media_ref(), media_ref(field="input")], media_dir, 10_000, []
        )
        assert len(assets) == 1
    assert backend.fetch_media_calls == [MEDIA_ID]
    assert len(calls) == 1


# -- run_export: nothing partial reaches the manifest ----------------------
def test_failed_media_never_reaches_manifest(monkeypatch):
    set_env(monkeypatch)
    client = make_client(monkeypatch)
    install_media_router(monkeypatch, [FakeResponse(403), FakeResponse(403)])
    rows = [make_row(metadata={"image": media_token("med_big")})]
    backend = FakeBackend(
        rows=rows,
        media={"med_big": [private_media(contentLength=None), private_media(contentLength=None)]},
        client=client,
    )
    wire_cli_backend(monkeypatch, backend)

    with workdir("media_manifest") as tmp:
        args = cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out")])
        result = cli.run_export(args)
        export_dir = Path(result["exportDir"])
        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
        transcript = (export_dir / "transcript.md").read_text(encoding="utf-8")

        paths = [entry["path"] for entry in manifest["files"]]
        assert not any(path.startswith("media/") for path in paths)
        assert not (export_dir / "media").exists()
        assert manifest["warnings"], "media failure must surface a warning"
        assert "SECRETSIG" not in json.dumps(manifest)
        assert "SECRETSIG" not in transcript
        assert PRESIGNED_URL not in transcript


def test_oversize_media_never_reaches_manifest(monkeypatch):
    set_env(monkeypatch)
    client = make_client(monkeypatch)
    requests_calls = install_media_router(monkeypatch, [FakeResponse(200, chunks=[b"x"])])
    rows = [make_row(metadata={"image": media_token("med_huge")})]
    backend = FakeBackend(
        rows=rows,
        media={"med_huge": private_media(contentLength=10**6)},
        client=client,
    )
    wire_cli_backend(monkeypatch, backend)

    with workdir("media_manifest_skip") as tmp:
        args = cli.build_parser().parse_args(
            [TRACE, "--out", str(tmp / "out"), "--max-media-bytes", "1000"]
        )
        result = cli.run_export(args)
        export_dir = Path(result["exportDir"])
        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))
        transcript = (export_dir / "transcript.md").read_text(encoding="utf-8")

        assert requests_calls == []  # declared oversize is never downloaded
        assert any(
            warning == "media skipped for med_huge: exceeds 1000 bytes"
            for warning in manifest["warnings"]
        )
        assert manifest["files"]
        assert not any(entry["path"].startswith("media/") for entry in manifest["files"])
        # The transcript Media table shows the reason instead of a bare dash.
        assert "exceeds limit" in transcript


def test_downloaded_media_is_manifested_without_presigned_url(monkeypatch):
    set_env(monkeypatch)
    client = make_client(monkeypatch)
    install_media_router(monkeypatch, [FakeResponse(200, chunks=[b"abc"])])
    rows = [make_row(metadata={"image": media_token("med_ok")})]
    backend = FakeBackend(rows=rows, media={"med_ok": private_media()}, client=client)
    wire_cli_backend(monkeypatch, backend)

    with workdir("media_manifest_ok") as tmp:
        args = cli.build_parser().parse_args([TRACE, "--out", str(tmp / "out")])
        result = cli.run_export(args)
        export_dir = Path(result["exportDir"])
        manifest = json.loads((export_dir / "manifest.json").read_text(encoding="utf-8"))

        paths = [entry["path"] for entry in manifest["files"]]
        assert "media/med_ok.png" in paths
        endpoints = [entry["endpoint"] for entry in manifest["requests"]]
        assert "media/med_ok" in endpoints
        assert all("X-Amz-Signature" not in endpoint for endpoint in endpoints)
        assert "SECRETSIG" not in json.dumps(manifest)
