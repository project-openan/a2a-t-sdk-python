"""Helpers shared by tests: turn fixture rows into a TraceBundle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from langfuse_export.models import MediaAsset, TraceBundle
from langfuse_export.normalize import build_tree, derive_summary, normalize_scores

TRACE_ID = "9f2c1a7de4b34c0e8a5f2d1c6b9e3a70"


def flatten(roots):
    values = []
    stack = list(roots)
    while stack:
        node = stack.pop()
        values.append(node)
        stack.extend(node.children)
    return values


def build_bundle(rows: list[dict], scores: list[dict], with_media: bool = True) -> TraceBundle:
    roots, warnings = build_tree(rows, TRACE_ID)
    observations = flatten(roots)
    score_objects = normalize_scores(scores)
    summary, summary_warnings = derive_summary(observations, roots, len(score_objects), TRACE_ID)
    warnings = warnings + summary_warnings
    media_assets = []
    if with_media:
        media_assets.append(
            MediaAsset(
                media_id="med_7c3a91f2",
                content_type="image/png",
                content_length=18432,
                url_expiry="2026-11-07T10:35:00Z",
                local_path="media/med_7c3a91f2.png",
                status="downloaded",
            )
        )
    return TraceBundle(
        trace_id=TRACE_ID,
        backend="v4",
        host="https://cloud.langfuse.com",
        api_version="v4",
        host_source="default",
        summary=summary,
        observations=observations,
        roots=roots,
        scores=score_objects,
        warnings=warnings,
        request_log=[],
        raw_observations=rows,
        raw_scores=scores,
        media_assets=media_assets,
    )


FIXED_EXPORTED_AT = datetime(2026, 11, 7, 10, 20, 11, tzinfo=timezone(timedelta(hours=8)))
