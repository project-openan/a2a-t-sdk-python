"""Tests of the dependency-free metrics registry and the ``/metrics`` route."""

from __future__ import annotations

import httpx
import pytest
from shared.http_security import install_metrics_route
from shared.metrics import Counter, Gauge, Summary, render_prometheus, reset_metrics
from starlette.applications import Starlette


@pytest.fixture(autouse=True)
def _reset_metrics() -> None:
    """Keep every test independent of values recorded by an earlier one."""
    reset_metrics()


def test_counter_gauge_and_summary_render_prometheus_text() -> None:
    counter = Counter("test_counter_total", "A counter.", ("kind",))
    gauge = Gauge("test_gauge", "A gauge.")
    summary = Summary("test_duration_seconds", "A summary.")

    counter.inc({"kind": 'a"b'})
    counter.inc({"kind": 'a"b'})
    gauge.set(3)
    summary.observe(1.5)
    summary.observe(0.5)

    text = render_prometheus()

    assert "# HELP test_counter_total A counter." in text
    assert "# TYPE test_counter_total counter" in text
    assert 'test_counter_total{kind="a\\"b"} 2' in text
    assert "test_gauge 3" in text
    assert "test_duration_seconds_sum 2" in text
    assert "test_duration_seconds_count 2" in text


def test_gauges_can_decrease_and_render_after_reset() -> None:
    gauge = Gauge("test_in_flight", "In flight.")
    gauge.inc()
    gauge.inc()
    gauge.dec()
    assert "test_in_flight 1" in render_prometheus()

    reset_metrics()
    assert "test_in_flight 1" not in render_prometheus()


async def test_metrics_route_serves_the_text_exposition() -> None:
    app = Starlette(routes=[])
    install_metrics_route(app)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    assert "a2at_client_runs_total" in response.text
