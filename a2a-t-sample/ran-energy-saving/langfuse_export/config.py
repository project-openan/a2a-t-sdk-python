"""Configuration resolution: CLI flags > environment > .env (cwd only) > default.

Besides the Langfuse endpoint and credentials, the same matrix resolves the
network settings (proxy / no-proxy / custom CA bundle / insecure TLS). Nothing is
configured by default, so a plain run behaves exactly like 0.1.0.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

DEFAULT_HOST = "https://cloud.langfuse.com"

_TRUE_VALUES = {"1", "true", "yes", "on"}
_FALSE_VALUES = {"0", "false", "no", "off"}
_PROXY_SCHEMES = {"http", "https"}
_LEVEL_RANK = {"flag": 3, "env": 2, "dotenv": 1}


class ConfigError(Exception):
    pass


@dataclass
class Config:
    host: str
    public_key: str
    secret_key: str
    host_source: str  # flag | env | dotenv | default
    api_version: str = "auto"
    proxy: str | None = None  # normalised: scheme://[user:pass@]host:port
    no_proxy: bool = False  # True = never use a proxy (ignores env/.env proxy settings)
    cacert: str | None = None  # PEM bundle that replaces the default trust store
    insecure: bool = False  # True = no TLS verification at all (wins over cacert)


def _read_dotenv(cwd: Path) -> dict[str, str]:
    """Read .env in the current working directory only (never walk upwards)."""
    path = cwd / ".env"
    if not path.is_file():
        return {}
    try:
        from dotenv import dotenv_values

        values = dotenv_values(path)
    except Exception:  # dotenv missing or unreadable: fall back to a tiny parser
        values = {}
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
    return {k: v for k, v in values.items() if v is not None}


def _env_name(name: str) -> str:
    return f"LANGFUSE_{name.upper()}"


def _resolve_text(
    name: str,
    flag: str | None,
    environ: dict[str, str],
    dotenv: dict[str, str],
) -> tuple[str | None, str]:
    """Resolve one string setting; ``(None, "")`` when no level sets it.

    An empty (or whitespace-only) value counts as "not set" so a deliberate blank
    in one level never masks a lower-precedence value.
    """
    env_name = _env_name(name)
    for value, source in ((flag, "flag"), (environ.get(env_name), "env"), (dotenv.get(env_name), "dotenv")):
        text = (value or "").strip()
        if text:
            return text, source
    return None, ""


def _parse_bool(value: str, env_name: str) -> bool:
    text = value.strip().lower()
    if text in _TRUE_VALUES:
        return True
    if text in _FALSE_VALUES:
        return False
    raise ConfigError(
        f"invalid value for {env_name}: {value!r} "
        "(expected 1/true/yes/on or 0/false/no/off, case-insensitive)"
    )


def _resolve_bool(
    name: str,
    flag: bool | None,
    environ: dict[str, str],
    dotenv: dict[str, str],
) -> tuple[bool, str]:
    """Resolve one boolean setting; an explicit flag wins over env/.env.

    Returns ``(value, source)`` where source is flag/env/dotenv/"" (unset).
    """
    env_name = _env_name(name)
    if flag is not None:
        return bool(flag), "flag"
    for value, source in ((environ.get(env_name), "env"), (dotenv.get(env_name), "dotenv")):
        text = (value or "").strip()
        if text:
            return _parse_bool(text, env_name), source
    return False, ""


def _proxy_conflict_message(source: str) -> str:
    if source == "flag":
        return "--proxy and --no-proxy cannot be used together"
    if source == "env":
        return "LANGFUSE_PROXY and LANGFUSE_NO_PROXY cannot be used together (unset one)"
    return ".env sets both LANGFUSE_PROXY and LANGFUSE_NO_PROXY (remove one)"


def normalize_proxy(value: str) -> str:
    """Validate and canonicalise an HTTP(S) proxy URL.

    Returns ``scheme://[user:pass@]host:port`` with the port always explicit
    (80 for http, 443 for https). Credentials are kept here because requests
    needs them for the ``407 proxy authentication required`` path; every output
    path must go through :func:`proxy_label` first.
    """
    raw = value.strip()
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    if scheme.startswith("socks"):
        raise ConfigError(
            f"proxy scheme {scheme!r} is not supported: SOCKS needs requests[socks], "
            "which this tool does not ship; use an http:// or https:// proxy"
        )
    if scheme not in _PROXY_SCHEMES:
        raise ConfigError(
            f"invalid proxy {raw!r}: expected an http:// or https:// URL "
            "(for example http://proxy.example:8080)"
        )
    host = parts.hostname
    if not host:
        raise ConfigError(f"invalid proxy {raw!r}: missing host")
    try:
        port = parts.port
    except ValueError:
        raise ConfigError(f"invalid proxy {raw!r}: bad port") from None
    if port is None:
        port = 443 if scheme == "https" else 80
    # Keep the userinfo byte-for-byte: never decode (and never re-encode) proxy
    # credentials, they are only ever shown to requests.
    userinfo = ""
    if "@" in parts.netloc:
        userinfo = parts.netloc.rsplit("@", 1)[0] + "@"
    host_text = f"[{host}]" if ":" in host else host
    return f"{scheme}://{userinfo}{host_text}:{port}"


def proxy_label(proxy: str | None) -> str | None:
    """``host:port`` only: proxy credentials must never reach logs or artifacts."""
    if not proxy:
        return None
    parts = urlsplit(proxy)
    host = parts.hostname
    if not host:
        return None
    try:
        port = parts.port
    except ValueError:
        port = None
    host_text = f"[{host}]" if ":" in host else host
    return f"{host_text}:{port}" if port is not None else host_text


def _validate_cacert(value: str) -> str:
    path = Path(value).expanduser()
    if not path.exists():
        raise ConfigError(f"cacert not found: {value}")
    if not path.is_file():
        raise ConfigError(f"cacert is not a file: {value}")
    return str(path)


def network_notices(insecure: bool, cacert: str | None) -> list[str]:
    """ASCII notices for a weakened TLS setup.

    Notices are informational: they never feed ``--strict`` and never change the
    exit code (they only live in ``manifest.json``/``transcript.md``/``--json``).
    """
    notices: list[str] = []
    if insecure:
        notices.append("TLS verification disabled (--insecure); exported data may be tampered with")
        if cacert:
            notices.append("insecure overrides cacert")
    elif cacert:
        notices.append(f"custom CA bundle in use: {Path(cacert).name}")
    return notices


def network_summary(config: Config) -> str:
    """One credential-free line describing the effective network settings."""
    proxy = proxy_label(config.proxy) or "direct"
    tls = "off" if config.insecure else "on"
    ca = Path(config.cacert).name if config.cacert else "none"
    env = "ignore" if config.no_proxy else "inherit"
    return (
        f"network: proxy={proxy} noProxy={str(config.no_proxy).lower()} "
        f"tlsVerify={tls} caBundle={ca} envProxy={env}"
    )


def load_config(
    host: str | None = None,
    public_key: str | None = None,
    secret_key: str | None = None,
    api_version: str = "auto",
    proxy: str | None = None,
    no_proxy: bool | None = None,
    cacert: str | None = None,
    insecure: bool | None = None,
    environ: dict[str, str] | None = None,
    cwd: Path | None = None,
) -> Config:
    environ = dict(os.environ if environ is None else environ)
    cwd = Path.cwd() if cwd is None else Path(cwd)
    dotenv = _read_dotenv(cwd)

    host_value, host_source = _resolve_text("host", host, environ, dotenv)
    if not host_value:
        host_value, host_source = DEFAULT_HOST, "default"

    pk, _ = _resolve_text("public_key", public_key, environ, dotenv)
    sk, _ = _resolve_text("secret_key", secret_key, environ, dotenv)
    missing = [n for n, v in (("LANGFUSE_PUBLIC_KEY", pk), ("LANGFUSE_SECRET_KEY", sk)) if not v]
    if missing:
        raise ConfigError(
            "missing credentials: " + ", ".join(missing) + " (set env vars, .env, or --public-key/--secret-key)"
        )

    proxy_value, proxy_source = _resolve_text("proxy", proxy, environ, dotenv)
    cacert_value, _ = _resolve_text("cacert", cacert, environ, dotenv)
    no_proxy_value, no_proxy_source = _resolve_bool("no_proxy", no_proxy, environ, dotenv)
    insecure_value, _ = _resolve_bool("insecure", insecure, environ, dotenv)
    if proxy_value and no_proxy_value:
        # "use this proxy" and "never use a proxy" are mutually exclusive at the
        # same precedence level (the flags included). Across levels the usual
        # flag > env > .env rule decides, so only the winning one survives.
        if proxy_source == no_proxy_source:
            raise ConfigError(_proxy_conflict_message(proxy_source))
        if _LEVEL_RANK[proxy_source] > _LEVEL_RANK[no_proxy_source]:
            no_proxy_value = False
        else:
            proxy_value = None

    return Config(
        host=str(host_value).rstrip("/"),
        public_key=str(pk),
        secret_key=str(sk),
        host_source=host_source,
        api_version=api_version,
        proxy=normalize_proxy(proxy_value) if proxy_value else None,
        no_proxy=no_proxy_value,
        cacert=_validate_cacert(cacert_value) if cacert_value else None,
        insecure=insecure_value,
    )


def region_of(host: str) -> str:
    h = host.lower()
    if "us.cloud.langfuse.com" in h:
        return "US"
    if "jp.cloud.langfuse.com" in h:
        return "JP"
    if "hipaa.cloud.langfuse.com" in h:
        return "HIPAA"
    if "cloud.langfuse.com" in h:
        return "EU"
    return "self-hosted"
