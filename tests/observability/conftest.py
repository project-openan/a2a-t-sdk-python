"""Shared pytest fixtures: OTel global isolation for per-test providers.

The opentelemetry-api set_tracer_provider / set_meter_provider are once-per-process;
per-test fixtures that create fresh providers must reset these globals first.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


def _reset_once(lock: object) -> None:
    if lock is None:
        return
    for attr in ("_set", "_done"):
        if hasattr(lock, attr):
            setattr(lock, attr, False)  # type: ignore[attr-defined]


@pytest.fixture(autouse=True)
def _reset_otel_globals() -> Iterator[None]:
    import opentelemetry.metrics._internal as metrics_internal
    import opentelemetry.trace as trace_api

    trace_api._TRACER_PROVIDER = None  # type: ignore[assignment]
    # The meter globals live on the private _internal module; the public
    # opentelemetry.metrics package does not re-export them.
    metrics_internal._METER_PROVIDER = None  # type: ignore[assignment]
    _reset_once(getattr(trace_api, "_TRACER_PROVIDER_SET_ONCE", None))
    _reset_once(getattr(metrics_internal, "_METER_PROVIDER_SET_ONCE", None))
    yield
