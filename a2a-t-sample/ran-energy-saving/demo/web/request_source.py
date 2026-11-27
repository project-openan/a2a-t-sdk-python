"""Build the request-origin document recorded on a run or a tester batch.

The UI process serves several entries: the presentation page, the Run Explorer, the tester console
and the plain HTTP API. Each records where a request came from so an operator can answer "who
started this" without reading access logs. The document is bounded and never contains credentials;
a referer's ``token`` query parameter is removed before storage.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Final
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from shared.run_source import normalize_run_source
from starlette.requests import Request

#: Default channel label of each source kind.
SOURCE_CHANNELS: Final[dict[str, str]] = {
    "presentation": "presentation-page",
    "explorer": "explorer-page",
    "tester": "tester-console",
    "gateway": "a2a-gateway",
    "cli": "cli",
    "api": "http-api",
}

#: Longest header value kept in a source document.
SOURCE_TEXT_LIMIT: Final[int] = 1000

#: Query keys stripped from a stored referer so a shared `?token=…` link never leaks.
_SOURCE_SECRET_KEYS: Final[frozenset[str]] = frozenset({"token", "auth", "authorization"})


def request_source(request: Request, declared: object) -> dict[str, object]:
    """Build the source document from the page declaration and HTTP metadata.

    The page declares its kind (``presentation`` / ``explorer`` / ``tester``); the server adds the
    network origin so an internal console can tell a page request from a scripted API call.
    Header values are bounded, and a referer's ``token`` query parameter is removed before storage.
    """
    base: dict[str, object] = {}
    kind: object = None
    if isinstance(declared, Mapping):
        base = {
            str(key): value
            for key, value in declared.items()
            if isinstance(value, (str, int, float, bool))
        }
        kind = base.get("kind")
    elif isinstance(declared, str):
        kind = declared
    if kind is not None:
        base["kind"] = str(kind)
    headers = request.headers
    if request.client is not None and request.client.host:
        base["remoteAddress"] = request.client.host
    for field, header in (
        ("realIp", "x-real-ip"),
        ("forwardedFor", "x-forwarded-for"),
        ("userAgent", "user-agent"),
        ("origin", "origin"),
        ("acceptLanguage", "accept-language"),
        ("host", "host"),
    ):
        value = headers.get(header)
        if value:
            base[field] = value[:SOURCE_TEXT_LIMIT]
    referer = _redacted_url(headers.get("referer"))
    if referer:
        base["referer"] = referer
    request_id = headers.get("x-request-id") or headers.get("x-correlation-id")
    if request_id:
        base["requestId"] = request_id[:SOURCE_TEXT_LIMIT]
    base["entryPath"] = request.url.path
    normalized = normalize_run_source(base)
    normalized.setdefault("channel", SOURCE_CHANNELS.get(str(normalized.get("kind")), "http-api"))
    return normalized


def _redacted_url(value: str | None) -> str | None:
    """Return one URL with credential-like query keys removed and a bounded length."""
    if not value:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return value[:SOURCE_TEXT_LIMIT]
    if not parsed.query:
        return value[:SOURCE_TEXT_LIMIT]
    pairs = [
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() not in _SOURCE_SECRET_KEYS
    ]
    return urlunsplit(parsed._replace(query=urlencode(pairs)))[:SOURCE_TEXT_LIMIT]


__all__ = ["SOURCE_CHANNELS", "SOURCE_TEXT_LIMIT", "request_source"]
