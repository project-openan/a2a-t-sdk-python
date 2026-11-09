"""Data models shared by collectors, normalizer and renderer."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any


@dataclass
class RequestRecord:
    endpoint: str
    status: int | None
    duration_ms: int
    pages: int = 1
    error: str | None = None


@dataclass
class Usage:
    input: int | None = None
    output: int | None = None
    total: int | None = None
    cost_total: Decimal | None = None
    cost_details: dict[str, Decimal] = field(default_factory=dict)
    raw_usage: dict[str, Any] = field(default_factory=dict)
    raw_cost: dict[str, Any] = field(default_factory=dict)

    def has_any(self) -> bool:
        tokens = any(v is not None and v != 0 for v in (self.input, self.output, self.total))
        return tokens or self.cost_total is not None


@dataclass
class NetworkInfo:
    """Effective network settings, recorded in the manifest, transcript and --json.

    ``proxy`` is already sanitised to ``host:port`` (never scheme/credentials);
    ``ca_bundle`` keeps only the file name so a local path never leaks.
    """

    proxy: str | None = None
    tls_verify: bool = True
    ca_bundle: str | None = None
    # Render-only: the manifest schema is frozen to the three keys above, but the
    # transcript should still say *why* the export went direct.
    no_proxy: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "proxy": self.proxy,
            "tlsVerify": self.tls_verify,
            "caBundle": self.ca_bundle,
        }


@dataclass
class MediaRef:
    media_id: str
    mime_type: str
    source_type: str
    field: str
    raw_token: str


@dataclass
class MediaAsset:
    media_id: str
    content_type: str | None = None
    content_length: int | None = None
    url_expiry: str | None = None
    local_path: str | None = None
    status: str = "pending"  # pending | downloaded | skipped | failed
    error: str | None = None


@dataclass
class Observation:
    id: str
    trace_id: str | None
    parent_id: str | None
    type: str
    name: str
    start_time: datetime | None
    end_time: datetime | None
    completion_start_time: datetime | None
    latency: float | None
    level: str | None
    status_message: str | None
    is_root: bool
    model: str | None
    model_parameters: dict[str, Any]
    prompt_name: str | None
    prompt_version: str | None
    input_raw: str | None
    output_raw: str | None
    metadata: dict[str, Any]
    environment: str | None
    version: str | None
    usage: Usage
    media_refs: list[MediaRef] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)
    children: list["Observation"] = field(default_factory=list)
    depth: int = 0

    def is_shell(self, trace_id: str) -> bool:
        return self.id == f"t-{trace_id}"


@dataclass
class Score:
    id: str | None
    name: str
    value: Any
    data_type: str | None
    source: str | None
    timestamp: str | None
    subject_kind: str | None = None
    subject_id: str | None = None
    subject_trace_id: str | None = None
    comment: str | None = None
    author_user_id: str | None = None
    config_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class TraceSummary:
    trace_id: str
    name: str | None
    user_id: str | None
    session_id: str | None
    tags: list[str]
    release: str | None
    environment: str | None
    start_time: datetime | None
    end_time: datetime | None
    latency_ms: int | None
    total_cost: Decimal | None
    input_tokens: int | None
    output_tokens: int | None
    total_tokens: int | None
    observation_count: int
    score_count: int
    source: str = "derived"

    def to_dict(self) -> dict[str, Any]:
        return {
            "traceId": self.trace_id,
            "name": self.name,
            "source": self.source,
            "userId": self.user_id,
            "sessionId": self.session_id,
            "tags": self.tags,
            "release": self.release,
            "environment": self.environment,
            "startTime": _iso(self.start_time),
            "endTime": _iso(self.end_time),
            "latencyMs": self.latency_ms,
            "totalCost": float(self.total_cost) if self.total_cost is not None else None,
            "inputTokens": self.input_tokens,
            "outputTokens": self.output_tokens,
            "totalTokens": self.total_tokens,
            "observationCount": self.observation_count,
            "scoreCount": self.score_count,
        }


@dataclass
class TraceBundle:
    trace_id: str
    backend: str
    host: str
    api_version: str
    summary: TraceSummary
    observations: list[Observation]
    roots: list[Observation]
    scores: list[Score]
    warnings: list[str]
    request_log: list[RequestRecord]
    raw_observations: list[dict[str, Any]]
    raw_scores: list[dict[str, Any]]
    host_source: str = "default"
    media_assets: list[MediaAsset] = field(default_factory=list)
    session_rows: list[dict[str, Any]] = field(default_factory=list)
    legacy_trace: dict[str, Any] | None = None
    project_id: str | None = None
    project_name: str | None = None
    metadata_truncated: bool = False
    truncated: bool = False
    network: NetworkInfo = field(default_factory=NetworkInfo)
    notices: list[str] = field(default_factory=list)


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    utc = value.astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S.") + f"{utc.microsecond // 1000:03d}Z"
