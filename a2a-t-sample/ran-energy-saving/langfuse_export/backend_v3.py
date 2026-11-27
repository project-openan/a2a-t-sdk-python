"""Langfuse v3 backend (legacy public API, self-hosted < 4)."""

from __future__ import annotations

import re
from typing import Any

from .backend import Backend
from .client import LangfuseClient

PAGE_LIMIT = 100
MAX_PAGES = 200
SCORE_MAX_PAGES = 20


def _at_least(version: str, major: int, minor: int) -> bool:
    match = re.search(r"(\d+)\.(\d+)", version or "")
    if not match:
        return False
    return (int(match.group(1)), int(match.group(2))) >= (major, minor)


class V3Backend(Backend):
    kind = "v3"
    api_version = "v3"

    def __init__(self, client: LangfuseClient, version: str = "") -> None:
        super().__init__(client)
        self.version = version
        self.truncated = False

    def fetch_legacy_trace(self, trace_id: str) -> dict[str, Any] | None:
        return self.client.get_json(f"/api/public/traces/{trace_id}")

    def _fetch_page_rows(self, path: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Collect page-based rows; flag truncation only when the page cap cut us off."""
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            page_params = dict(params)
            page_params["page"] = page
            body = self.client.get_json(path, page_params)
            data = body.get("data") or []
            rows.extend(data)
            meta = body.get("meta") or {}
            total_pages = meta.get("totalPages")
            if not data:
                return rows
            if isinstance(total_pages, int) and page >= total_pages:
                return rows
            if len(data) < PAGE_LIMIT:
                return rows
            if page == MAX_PAGES:
                self.truncated = True
        return rows

    def fetch_observations(
        self,
        trace_id: str,
        from_time: str | None = None,
        to_time: str | None = None,
        expand_metadata: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"traceId": trace_id, "limit": PAGE_LIMIT}
        if from_time:
            params["fromStartTime"] = from_time
        if to_time:
            params["toStartTime"] = to_time
        return self._fetch_page_rows("/api/public/observations", params)

    def fetch_scores(self, trace_id: str) -> list[dict[str, Any]]:
        if _at_least(self.version, 3, 179):
            rows: list[dict[str, Any]] = []
            params = {"traceId": trace_id, "fields": "details,subject,annotation", "limit": 100}
            pages = 0
            cursor_left: str | None = None
            for page in self.client.iter_cursor_pages("/api/public/v3/scores", params, SCORE_MAX_PAGES):
                pages += 1
                rows.extend(page.get("data") or [])
                cursor_left = (page.get("meta") or {}).get("cursor")
            if pages >= SCORE_MAX_PAGES and cursor_left:
                self.truncated = True
            return rows
        return self._fetch_page_rows("/api/public/scores", {"traceId": trace_id, "limit": PAGE_LIMIT})

    def fetch_media(self, media_id: str) -> dict[str, Any]:
        return self.client.get_json(f"/api/public/media/{media_id}")

    def fetch_project(self) -> tuple[str | None, str | None]:
        return None, None

    def fetch_session_observations(self, session_id: str) -> list[dict[str, Any]]:
        return self._fetch_page_rows(
            "/api/public/observations", {"sessionId": session_id, "limit": PAGE_LIMIT}
        )
