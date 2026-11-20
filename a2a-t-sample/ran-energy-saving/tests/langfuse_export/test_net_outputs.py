import json

import pytest

from fix_support import FakeBackend, make_row, set_env, wire_cli_backend, workdir
from langfuse_export import cli

TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"
INSECURE_NOTICE = "TLS verification disabled (--insecure); exported data may be tampered with"


def _row():
    return make_row(totalCost=1.25, inputUsage=10, outputUsage=5, totalUsage=15)


@pytest.fixture
def backend(monkeypatch):
    fake = FakeBackend(rows=[_row()])
    wire_cli_backend(monkeypatch, fake)
    return fake


def _run(monkeypatch, backend, tmpdir, *extra):
    set_env(monkeypatch, "https://example.test")
    out = tmpdir / "out"
    argv = [TRACE, "--out", str(out), *extra]
    return cli.main(argv), out


def _manifest(out_dir):
    (export,) = list(out_dir.glob("*"))
    return json.loads((export / "manifest.json").read_text(encoding="utf-8")), export


def test_default_network_artifacts(monkeypatch, backend):
    with workdir("net_out_default") as tmpdir:
        code, out = _run(monkeypatch, backend, tmpdir)
        assert code == 0
        manifest, export = _manifest(out)
        assert manifest["network"] == {"proxy": None, "tlsVerify": True, "caBundle": None}
        assert manifest["notices"] == []
        transcript = (export / "transcript.md").read_text(encoding="utf-8")
        assert "直连（默认）" in transcript
        assert "TLS 校验=开启" in transcript


def test_proxy_is_recorded_without_credentials(monkeypatch, backend, capsys):
    with workdir("net_out_proxy") as tmpdir:
        code, out = _run(monkeypatch, backend, tmpdir, "--proxy", "http://user:Sup3rSecret@proxy.corp:8080")
        assert code == 0
        manifest, export = _manifest(out)
        assert manifest["network"]["proxy"] == "proxy.corp:8080"
        assert "Sup3rSecret" not in json.dumps(manifest)
        transcript = (export / "transcript.md").read_text(encoding="utf-8")
        assert "代理 `proxy.corp:8080`" in transcript
        captured = capsys.readouterr()
        assert "Sup3rSecret" not in captured.out + captured.err


def test_no_proxy_shows_in_transcript(monkeypatch, backend):
    with workdir("net_out_noproxy") as tmpdir:
        code, out = _run(monkeypatch, backend, tmpdir, "--no-proxy")
        assert code == 0
        manifest, export = _manifest(out)
        assert manifest["network"]["proxy"] is None
        transcript = (export / "transcript.md").read_text(encoding="utf-8")
        assert "直连（--no-proxy）" in transcript


def test_insecure_records_notice_and_prints_once(monkeypatch, backend, capsys):
    with workdir("net_out_insecure") as tmpdir:
        code, out = _run(monkeypatch, backend, tmpdir, "--insecure")
        assert code == 0
        manifest, export = _manifest(out)
        assert manifest["network"]["tlsVerify"] is False
        assert INSECURE_NOTICE in manifest["notices"]
        transcript = (export / "transcript.md").read_text(encoding="utf-8")
        assert "TLS 校验=关闭（--insecure）" in transcript
        captured = capsys.readouterr()
        assert captured.err.count("notice: TLS verification disabled") == 1
        assert "InsecureRequestWarning" not in captured.err


def test_cacert_records_basename_only(monkeypatch, backend):
    with workdir("net_out_cacert") as tmpdir:
        pem = tmpdir / "corp-root.pem"
        pem.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        code, out = _run(monkeypatch, backend, tmpdir, "--cacert", str(pem))
        assert code == 0
        manifest, export = _manifest(out)
        assert manifest["network"]["caBundle"] == "corp-root.pem"
        assert "custom CA bundle in use: corp-root.pem" in manifest["notices"]
        rendered = json.dumps(manifest) + (export / "transcript.md").read_text(encoding="utf-8")
        assert str(pem) not in rendered


def test_insecure_overrides_cacert(monkeypatch, backend, capsys):
    with workdir("net_out_both") as tmpdir:
        pem = tmpdir / "corp-root.pem"
        pem.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
        code, out = _run(monkeypatch, backend, tmpdir, "--insecure", "--cacert", str(pem))
        assert code == 0
        manifest, _export = _manifest(out)
        assert manifest["network"]["tlsVerify"] is False
        assert "insecure overrides cacert" in manifest["notices"]
        assert capsys.readouterr().err.count("notice: TLS verification disabled") == 1


def test_json_result_has_network_and_notices(monkeypatch, backend):
    with workdir("net_out_json") as tmpdir:
        set_env(monkeypatch, "https://example.test")
        args = cli.build_parser().parse_args(
            [TRACE, "--out", str(tmpdir / "out"), "--json", "--insecure"]
        )
        result = cli.run_export(args)
        assert result["network"]["tlsVerify"] is False
        assert result["notices"] == [INSECURE_NOTICE]


def test_quiet_prints_only_the_export_directory(monkeypatch, backend, capsys):
    with workdir("net_out_quiet") as tmpdir:
        set_env(monkeypatch, "https://example.test")
        code = cli.main([TRACE, "--out", str(tmpdir / "out"), "--quiet", "--insecure"])
        assert code == 0
        captured = capsys.readouterr()
        assert captured.out.strip().count("\n") == 0
        printed = captured.out.strip()
        assert printed.startswith(str(tmpdir / "out"))
        assert TRACE in printed
        assert "notice:" in captured.err


def test_notices_do_not_feed_strict(monkeypatch, backend):
    with workdir("net_out_strict") as tmpdir:
        set_env(monkeypatch, "https://example.test")
        args = cli.build_parser().parse_args([TRACE, "--out", str(tmpdir / "out"), "--insecure"])
        result = cli.run_export(args)
        assert INSECURE_NOTICE in result["notices"]
        assert all(INSECURE_NOTICE not in warning for warning in result["warnings"])
        assert cli.main([TRACE, "--out", str(tmpdir / "out2"), "--insecure", "--strict"]) == 0
