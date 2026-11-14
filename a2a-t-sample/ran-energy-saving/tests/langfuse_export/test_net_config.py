import pytest

from langfuse_export import cli
from langfuse_export.config import ConfigError, load_config, proxy_label
from net_support import clean_net_env, workdir

CREDS = {"LANGFUSE_PUBLIC_KEY": "pk-lf-test", "LANGFUSE_SECRET_KEY": "sk-lf-test"}
TRACE = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"


@pytest.fixture
def cwd():
    with workdir("config") as path:
        yield path


def _environ(**overrides):
    environ = dict(CREDS)
    environ["LANGFUSE_HOST"] = "https://example.test"
    environ.update(overrides)
    return environ


def test_defaults_are_off(cwd):
    config = load_config(environ=_environ(), cwd=cwd)
    assert config.proxy is None
    assert config.no_proxy is False
    assert config.cacert is None
    assert config.insecure is False


def test_flag_beats_env_for_proxy(cwd):
    config = load_config(
        proxy="http://flag.proxy:8080",
        environ=_environ(LANGFUSE_PROXY="http://env.proxy:9999"),
        cwd=cwd,
    )
    assert config.proxy == "http://flag.proxy:8080"


def test_env_beats_dotenv(cwd):
    (cwd / ".env").write_text("LANGFUSE_PROXY=http://dotenv.proxy:7000\n", encoding="utf-8")
    assert load_config(environ=_environ(LANGFUSE_PROXY="http://env.proxy:9999"), cwd=cwd).proxy == "http://env.proxy:9999"
    assert load_config(environ=_environ(), cwd=cwd).proxy == "http://dotenv.proxy:7000"


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "On"])
def test_true_booleans(cwd, value):
    assert load_config(environ=_environ(LANGFUSE_INSECURE=value), cwd=cwd).insecure is True


@pytest.mark.parametrize("value", ["0", "false", "No", "OFF"])
def test_false_booleans(cwd, value):
    assert load_config(environ=_environ(LANGFUSE_INSECURE=value), cwd=cwd).insecure is False


def test_blank_bool_is_unset(cwd):
    assert load_config(environ=_environ(LANGFUSE_INSECURE="   "), cwd=cwd).insecure is False


@pytest.mark.parametrize("value", ["maybe", "2", "example.com"])
def test_invalid_boolean_raises(cwd, value):
    with pytest.raises(ConfigError):
        load_config(environ=_environ(LANGFUSE_INSECURE=value), cwd=cwd)


def test_invalid_boolean_exits_1_via_cli(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    monkeypatch.setenv("LANGFUSE_INSECURE", "maybe")
    assert cli.main([TRACE]) == 1
    assert "invalid value" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["socks5://proxy:1080", "socks://proxy:1080"])
def test_socks_is_rejected(cwd, bad):
    with pytest.raises(ConfigError) as info:
        load_config(environ=_environ(LANGFUSE_PROXY=bad), cwd=cwd)
    assert "requests[socks]" in str(info.value)


def test_proxy_without_scheme_is_rejected(cwd):
    with pytest.raises(ConfigError):
        load_config(environ=_environ(LANGFUSE_PROXY="127.0.0.1:8888"), cwd=cwd)


def test_missing_port_is_normalised(cwd):
    cases = {
        "http://proxy.corp": "http://proxy.corp:80",
        "https://proxy.corp": "https://proxy.corp:443",
        "http://proxy.corp:3128": "http://proxy.corp:3128",
    }
    for raw, expected in cases.items():
        assert load_config(environ=_environ(LANGFUSE_PROXY=raw), cwd=cwd).proxy == expected


def test_cacert_must_exist_and_be_a_file(cwd):
    with pytest.raises(ConfigError):
        load_config(environ=_environ(LANGFUSE_CACERT=str(cwd / "missing.pem")), cwd=cwd)
    with pytest.raises(ConfigError):
        load_config(environ=_environ(LANGFUSE_CACERT=str(cwd)), cwd=cwd)
    pem = cwd / "ca.pem"
    pem.write_text("-----BEGIN CERTIFICATE-----\n", encoding="utf-8")
    assert load_config(environ=_environ(LANGFUSE_CACERT=str(pem)), cwd=cwd).cacert == str(pem)


def test_proxy_and_no_proxy_flags_conflict(monkeypatch, capsys):
    clean_net_env(monkeypatch)
    monkeypatch.setenv("LANGFUSE_HOST", "https://example.test")
    monkeypatch.setenv("LANGFUSE_PUBLIC_KEY", "pk-lf-test")
    monkeypatch.setenv("LANGFUSE_SECRET_KEY", "sk-lf-test")
    assert cli.main([TRACE, "--proxy", "http://p:8080", "--no-proxy"]) == 1
    assert "cannot be used together" in capsys.readouterr().err


def test_proxy_and_no_proxy_env_conflict(cwd):
    with pytest.raises(ConfigError):
        load_config(environ=_environ(LANGFUSE_PROXY="http://p:8080", LANGFUSE_NO_PROXY="1"), cwd=cwd)


def test_cross_level_conflict_is_not_an_error(cwd):
    config = load_config(proxy="http://flag.proxy:8080", environ=_environ(LANGFUSE_NO_PROXY="1"), cwd=cwd)
    assert config.proxy == "http://flag.proxy:8080"
    assert config.no_proxy is False

    config = load_config(no_proxy=True, environ=_environ(LANGFUSE_PROXY="http://env.proxy:8080"), cwd=cwd)
    assert config.proxy is None
    assert config.no_proxy is True


def test_dotenv_is_cwd_only(cwd):
    parent = cwd.parent
    (parent / ".env").write_text("LANGFUSE_PROXY=http://parent.proxy:7000\n", encoding="utf-8")
    try:
        assert load_config(environ=_environ(), cwd=cwd).proxy is None
    finally:
        (parent / ".env").unlink()


def test_proxy_label_masks_credentials_and_keeps_ipv6():
    assert proxy_label("http://user:pass@proxy.corp:8080") == "proxy.corp:8080"
    assert proxy_label("http://[::1]:8888") == "[::1]:8888"
    assert proxy_label(None) is None
