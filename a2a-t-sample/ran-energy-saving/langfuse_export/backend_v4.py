"""Langfuse v4 backend (observations-first public API)."""

from __future__ import annotations

from typing import Any

from .backend import Backend
from .client import LangfuseClient

OBSERVATION_FIELDS = ",".join(
    ["core", "basic", "time", "io", "metadata", "model", "usage", "prompt", "metrics", "trace_context"]
)
SCORE_FIELDS = "details,subject,annotation"

OBSERVATION_MAX_PAGES = 50
SCORE_MAX_PAGES = 20


class V4Backend(Backend):
    kind = "v4"
    api_version = "v4"

    def __init__(self, client: LangfuseClient, version: str = "") -> None:
        super().__init__(client)
        self.version = version
        self.truncated = False

    def _fetch_cursor_rows(self, path: str, params: dict[str, Any], max_pages: int) -> list[dict[str, Any]]:
        """Collect cursor pages; flag truncation only when the cap cut off a live cursor."""
        rows: list[dict[str, Any]] = []
        pages = 0
        cursor_left: str | None = None
        for body in self.client.iter_cursor_pages(path, params, max_pages):
            pages += 1
            rows.extend(body.get("data") or [])
            cursor_left = (body.get("meta") or {}).get("cursor")
        if pages >= max_pages and cursor_left:
            self.truncated = True
        return rows

    def fetch_observations(
        self,
        trace_id: str,
        from_time: str | None = None,
        to_time: str | None = None,
        expand_metadata: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"traceId": trace_id, "fields": OBSERVATION_FIELDS, "limit": 1000}
        if from_time:
            params["fromStartTime"] = from_time
        if to_time:
            params["toStartTime"] = to_time
        if expand_metadata:
            params["expandMetadata"] = ",".join(expand_metadata)
        return self._fetch_cursor_rows("/api/public/v2/observations", params, OBSERVATION_MAX_PAGES)

    def fetch_scores(self, trace_id: str) -> list[dict[str, Any]]:
        params = {"traceId": trace_id, "fields": SCORE_FIELDS, "limit": 100}
        return self._fetch_cursor_rows("/api/public/v3/scores", params, SCORE_MAX_PAGES)

    def fetch_media(self, media_id: str) -> dict[str, Any]:
        return self.client.get_json(f"/api/public/media/{media_id}")

    def fetch_project(self) -> tuple[str | None, str | None]:
        try:
            body = self.client.get_json("/api/public/projects")
        except Exception as exc:  # optional: never fatal
            self.warnings.append(f"project lookup failed: {type(exc).__name__}")
            return None, None
        data = body.get("data") or []
        if not data:
            return None, None
        return data[0].get("id"), data[0].get("name")

    def fetch_session_observations(self, session_id: str) -> list[dict[str, Any]]:
        params = {"sessionId": session_id, "fields": OBSERVATION_FIELDS, "limit": 1000}
        return self._fetch_cursor_rows("/api/public/v2/observations", params, OBSERVATION_MAX_PAGES)
