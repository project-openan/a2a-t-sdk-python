"""Small standard-library JSON client for the tester\'s local HTTP API."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Mapping


class HttpError(RuntimeError):
    """One HTTP exchange failed or returned an unexpected status."""

    def __init__(self, message: str, *, status: int | None = None, body: object = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class JsonClient:
    """Call one local HTTP service and decode JSON bodies.

    Cookies are sent on every request so the tester can associate the CLI\'s batches with a stable
    owner. The client never follows cross-host redirects and never retries: a failed batch must be
    visible to the operator, not hidden behind an automatic replay.
    """

    def __init__(
        self,
        base_url: str,
        *,
        cookie: Mapping[str, str] | None = None,
        timeout: float = 15.0,
    ) -> None:
        self._base = base_url.rstrip("/")
        self._cookie = "; ".join(f"{key}={value}" for key, value in (cookie or {}).items())
        self._timeout = timeout

    @property
    def base_url(self) -> str:
        """Return the service base URL."""
        return self._base

    def get_json(self, path: str) -> tuple[int, Any]:
        """GET ``path`` and return ``(status, decoded body)``."""
        return self._request("GET", path)

    def post_json(self, path: str, payload: Mapping[str, Any] | None = None) -> tuple[int, Any]:
        """POST ``payload`` as JSON and return ``(status, decoded body)``."""
        return self._request("POST", path, payload)

    def get_text(self, path: str) -> tuple[int, str]:
        """GET ``path`` and return ``(status, text)``."""
        status, body = self._request("GET", path, raw=True)
        return status, body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)

    def _request(
        self,
        method: str,
        path: str,
        payload: Mapping[str, Any] | None = None,
        *,
        raw: bool = False,
    ) -> tuple[int, Any]:
        url = f"{self._base}{path}"
        data = None
        headers = {"Accept": "application/json"}
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        if self._cookie:
            headers["Cookie"] = self._cookie
        request = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                body = response.read().decode("utf-8", errors="replace")
                status = response.status
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            status = exc.code
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise HttpError(f"{method} {url} failed: {exc}") from exc
        if raw:
            return status, body
        if not body:
            return status, None
        try:
            return status, json.loads(body)
        except ValueError:
            return status, body

    @staticmethod
    def quote(segment: str) -> str:
        """Return one URL path segment, quoted."""
        return urllib.parse.quote(segment, safe="")
