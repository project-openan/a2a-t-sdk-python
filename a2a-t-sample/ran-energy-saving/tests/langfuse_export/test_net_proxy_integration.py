import pytest

from net_support import (
    PROXY_SECRET,
    FakeLangfuse,
    FakeProxy,
    clean_net_env,
    run_cli,
    unused_port,
    workdir,
)

TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"


def _argv(langfuse, out_dir, *extra):
    return [
        TRACE,
        "--host",
        langfuse.base,
        "--public-key",
        "pk",
        "--secret-key",
        "sk",
        "--out",
        str(out_dir),
        *extra,
    ]


def _fast_retries(monkeypatch):
    monkeypatch.setattr("langfuse_export.client.time.sleep", lambda *_: None)


def test_explicit_proxy_routes_api_and_media(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    with FakeLangfuse() as langfuse, FakeProxy() as proxy, workdir("net_proxy") as tmpdir:
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out", "--proxy", proxy.url))
        assert code == 0, capsys.readouterr().err
        proxy_paths = [entry[0] for entry in proxy.seen]
        assert any(path.endswith("/api/public/health") for path in proxy_paths)
        assert any("blob" in path for path in proxy_paths)
        assert all(headers.get("X-Via-Proxy") == "1" for _path, headers in langfuse.seen)
        exports = list((tmpdir / "out").glob("*"))
        assert exports and (exports[0] / "media").is_dir()


def test_no_proxy_ignores_env_proxy(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    with FakeLangfuse() as langfuse, FakeProxy() as proxy, workdir("net_noproxy") as tmpdir:
        monkeypatch.setenv("HTTP_PROXY", proxy.url)
        monkeypatch.setenv("HTTPS_PROXY", proxy.url)
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out", "--no-proxy"))
        assert code == 0, capsys.readouterr().err
        assert proxy.seen == []
        assert all("X-Via-Proxy" not in headers for _path, headers in langfuse.seen)


def test_explicit_proxy_ignores_no_proxy_env(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    with FakeLangfuse() as langfuse, FakeProxy() as proxy, workdir("net_no_proxy_env") as tmpdir:
        monkeypatch.setenv("NO_PROXY", "*")
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out", "--proxy", proxy.url))
        assert code == 0, capsys.readouterr().err
        assert proxy.seen


def test_proxy_407_exits_3(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    with FakeLangfuse() as langfuse, FakeProxy(mode="407") as proxy, workdir("net_407") as tmpdir:
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out", "--proxy", f"http://user:{PROXY_SECRET}@127.0.0.1:{proxy.port}"))
        assert code == 3
        captured = capsys.readouterr()
        assert "proxy authentication failed" in captured.err
        assert PROXY_SECRET not in captured.err + captured.out


def test_unreachable_proxy_exits_4(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    port = unused_port()
    with FakeLangfuse() as langfuse, workdir("net_unreachable") as tmpdir:
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out", "--proxy", f"http://127.0.0.1:{port}"))
        assert code == 4
        captured = capsys.readouterr()
        assert "network error" in captured.err
        assert "password" not in captured.err.lower()


def test_explicit_proxy_overrides_env_http_proxy(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    with (
        FakeLangfuse() as langfuse,
        FakeProxy() as explicit,
        FakeProxy() as envproxy,
        workdir("net_proxy_beats_env") as tmpdir,
    ):
        monkeypatch.setenv("HTTP_PROXY", envproxy.url)
        monkeypatch.setenv("HTTPS_PROXY", envproxy.url)
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out", "--proxy", explicit.url))
        assert code == 0, capsys.readouterr().err
        assert explicit.seen, "explicit proxy was not used"
        assert envproxy.seen == [], f"env proxy must not be used: {envproxy.seen}"


def test_langfuse_proxy_env_overrides_http_proxy_env(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    with (
        FakeLangfuse() as langfuse,
        FakeProxy() as explicit,
        FakeProxy() as envproxy,
        workdir("net_langfuse_proxy_env") as tmpdir,
    ):
        monkeypatch.setenv("LANGFUSE_PROXY", explicit.url)
        monkeypatch.setenv("HTTP_PROXY", envproxy.url)
        monkeypatch.setenv("HTTPS_PROXY", envproxy.url)
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out"))
        assert code == 0, capsys.readouterr().err
        assert explicit.seen
        assert envproxy.seen == []


def test_proxy_credentials_never_leak_to_artifacts(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    _fast_retries(monkeypatch)
    password = "TopSecretProxy"
    with FakeLangfuse() as langfuse, FakeProxy() as proxy, workdir("net_redact") as tmpdir:
        url = f"http://user:{password}@127.0.0.1:{proxy.port}"
        code = run_cli(monkeypatch, _argv(langfuse, tmpdir / "out", "--proxy", url, "--json"))
        assert code == 0, capsys.readouterr().err
        captured = capsys.readouterr()
        (export,) = list((tmpdir / "out").glob("*"))
        blobs = [captured.out, captured.err]
        for path in export.rglob("*"):
            if path.is_file():
                blobs.append(path.read_text(encoding="utf-8", errors="replace"))
        joined = "\n".join(blobs)
        assert password not in joined
        assert "user:" not in joined
        assert "X-Amz-Signature" not in joined
