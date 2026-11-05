"""Contract tests of the audience-facing presentation page.

The page is the demo default. These tests pin what it must expose (scenario presets, the stage,
the negotiation rail, the evidence panels and the human decision card), what must never leak into
the business view (transport, model or webhook wording) and that the Run Explorer stays available
behind ``--ui-mode explorer``.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from demo.web.app import PRESENTATION_DIR, create_app
from shared.config import SampleConfig
from shared.trace import PHASE_TASK_MODIFY, PHASES

#: Modules the page loads directly or transitively.
PRESENTATION_MODULES = (
    "copy.js",
    "js/main.js",
    "js/state.js",
    "js/scenes.js",
    "js/player.js",
    "js/stage.js",
    "js/rail.js",
    "js/trace.js",
    "js/intent.js",
    "js/decisions.js",
    "js/wire.js",
    "js/dom.js",
)


@pytest.fixture
async def client(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client bound to the default (presentation) app."""
    app = create_app(offline_config, runs_dir=tmp_path, finished_grace_seconds=0.0)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        yield http_client


def test_run_explorer_phase_vocabulary_matches_the_python_contract() -> None:
    """The Run Explorer's JS phase list must not drift from the Python trace vocabulary."""
    source = (PRESENTATION_DIR.parent / "static" / "js" / "store.js").read_text(encoding="utf-8")
    base_block = re.search(r"export const PHASES = \[(.*?)\];", source, re.S)
    optional_block = re.search(r"export const OPTIONAL_PHASES = \[(.*?)\];", source, re.S)
    assert base_block is not None
    assert optional_block is not None
    base = tuple(re.findall(r'key: "([^"]+)"', base_block.group(1)))
    optional = tuple(re.findall(r'key: "([^"]+)"', optional_block.group(1)))
    assert base == PHASES
    assert PHASE_TASK_MODIFY in optional


async def test_root_serves_the_presentation_shell(client: httpx.AsyncClient) -> None:
    response = await client.get("/")

    assert response.status_code == 200
    page = response.text
    assert "/static/app.css" in page
    assert "/static/js/main.js" in page
    for marker in (
        'id="scenario-select"',
        'id="theme-toggle"',
        'id="start-quick"',
        'id="request-text"',
        'id="drawer"',
        'id="start"',
        'id="steps"',
        'id="stage"',
        'id="a-eng"',
        'id="a-oss"',
        'id="a-nms"',
        'id="a-approval"',
        'id="a-cells"',
        'id="say"',
        'id="choices"',
        'id="p-intent"',
        'id="p-nego"',
        'id="p-trace"',
        'id="protocol-switch"',
        'id="turns"',
        'id="wire"',
        'id="wire-t"',
        'id="wire-x"',
        'id="wire-a"',
        'id="trace-calls"',
        'id="trace-calls-label"',
        'id="booth-title"',
        'id="footer"',
        'id="trace-rows"',
        'id="trace-splitter"',
        'id="continuity"',
        'id="continuity-badge"',
        'id="continuity-change"',
        'aria-live="polite"',
    ):
        assert marker in page, marker
    # One agent serves both request forms, so the page carries the in-page switch.
    assert 'id="protocol-switch"' in page
    assert 'data-protocol="a2a-t"' in page
    assert 'data-protocol="pure"' in page
    # The module entry plus the tiny inline brand bootstrap (no payload rendering).
    assert page.count("<script") == 2
    assert "a2at-default-brand" in page
    assert "innerHTML" not in page


async def test_the_page_never_mentions_transport_or_webhook_details(
    client: httpx.AsyncClient,
) -> None:
    page = (await client.get("/")).text.lower()

    for word in ("transport", "model mode", "webhook", "实时", "inspector"):
        assert word not in page, word


async def test_presentation_assets_are_served(client: httpx.AsyncClient) -> None:
    css = await client.get("/static/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "--accent:" in css.text
    assert "--orange:" in css.text
    assert '[data-brand="huawei"]' in css.text

    for name in PRESENTATION_MODULES:
        response = await client.get(f"/static/{name}")
        assert response.status_code == 200, name
        assert "javascript" in response.headers["content-type"]


async def test_meta_reports_the_presentation_mode_and_the_seven_scenarios(
    client: httpx.AsyncClient,
) -> None:
    meta = (await client.get("/api/meta")).json()

    assert meta["uiMode"] == "presentation"
    assert meta["defaultBrand"] == "orange"
    assert meta["brands"] == ["orange", "huawei"]
    assert meta["scenarios"] == [
        "night",
        "information",
        "unknown_area",
        "target",
        "feasibility",
        "reject",
        "abort",
        "outage",
    ]
    assert set(meta["scenarioRequests"]) == set(meta["scenarios"])
    assert all(meta["scenarioRequests"][name] for name in meta["scenarios"])
    assert meta["decisionMode"] == "auto"
    assert meta["intentReview"] is False
    assert meta["approvalGate"] is False
    assert meta["approvalTimeoutSeconds"] == 86400.0
    assert "confirm" in meta["modes"]


async def test_pure_meta_exposes_its_scenarios_and_mie(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        protocol="pure",
        mie_url="http://127.0.0.1:8002",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        meta = (await http_client.get("/api/meta")).json()
        page = (await http_client.get("/")).text

    assert meta["protocol"] == "pure"
    assert meta["protocols"] == ["pure"]
    assert meta["defaultProtocol"] == "pure"
    assert meta["scenarios"] == ["night", "information", "target"]
    assert meta["pureScenarios"] == ["night", "information", "target"]
    assert meta["mieEnabled"] is True
    assert 'id="protocol-switch"' in page


def test_phone_breakpoint_wraps_the_header_meta() -> None:
    """The brand row must not widen the document on a phone (390 px audit)."""
    css = (PRESENTATION_DIR / "app.css").read_text(encoding="utf-8")
    block = re.search(r"@media \(max-width: 720px\) \{(.*?)\n\}", css, re.S)
    assert block is not None
    assert ".top { flex-wrap: wrap" in block.group(1)
    assert ".top-meta { flex: 1 1 100%; flex-wrap: wrap; }" in block.group(1)


def test_huawei_theme_has_no_pointer_hover_effects() -> None:
    """The Huawei skin stays static on pointer hover; only explicit states may change."""
    css = (PRESENTATION_DIR / "app.css").read_text(encoding="utf-8")

    assert re.search(r'\[data-brand="huawei"\][^{]*:hover', css) is None
    unscoped = [
        line
        for line in css.splitlines()
        if ":hover" in line and '[data-brand="orange"]' not in line
    ]
    assert unscoped == []
    # The stage adjacency focus was a pointer-driven dimming effect; it must not come back.
    stage = (PRESENTATION_DIR / "js" / "stage.js").read_text(encoding="utf-8")
    for marker in (".stage.focusing", "bindStageFocus", "pointerover", "pointerout", "pointerleave", "is-adjacent"):
        assert marker not in css
        assert marker not in stage


def test_huawei_brand_css_keeps_the_semantic_and_focus_layers() -> None:
    css = (PRESENTATION_DIR / "app.css").read_text(encoding="utf-8")

    for marker in (
        '[data-brand="huawei"]',
        "--canvas: #faf9f5",
        "--accent-strong: #1f5faf",
        "--role-negotiation: #d97757",
        "--role-success: #558a42",
        "--role-success-strong: #3f6b31",
        "--role-danger: #d92128",
        "#a-oss",
        "#lane-approval",
        "--zone-it: #f7f2e9",
        "--zone-net: #edf4fa",
        '[data-brand="huawei"] .zone-it',
        '[data-brand="huawei"] .zone-net',
        ".continuity",
        ".continuity-badge",
        ".continuity-change",
        ".trace-filter",
        ".tag.task-t",
        ".tag.neg-t",
    ):
        assert marker in css, marker


async def test_unknown_ui_mode_is_rejected(offline_config: SampleConfig, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported ui mode"):
        create_app(offline_config, runs_dir=tmp_path, ui_mode="cinema")


async def test_unknown_brand_is_rejected(offline_config: SampleConfig, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Unsupported presentation brand"):
        create_app(offline_config, runs_dir=tmp_path, default_brand="nokia")


async def test_configured_brand_is_injected_into_the_page_and_meta(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        default_brand="huawei",
        finished_grace_seconds=0.0,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        page = (await http_client.get("/")).text
        meta = (await http_client.get("/api/meta")).json()

    assert '<meta name="a2at-default-brand" content="huawei">' in page
    assert meta["defaultBrand"] == "huawei"


async def test_explorer_mode_still_serves_the_run_explorer(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    app = create_app(offline_config, runs_dir=tmp_path, ui_mode="explorer")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        page = (await http_client.get("/")).text

    assert "/static/js/main.js" in page


def _node_binary() -> str:
    """Return the Node executable, skipping the test when Node is absent."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    return node


def test_presentation_modules_pass_node_syntax_check() -> None:
    node = _node_binary()

    for name in PRESENTATION_MODULES:
        result = subprocess.run(
            [node, "--check", str(PRESENTATION_DIR / name)],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert result.returncode == 0, f"{name}: {result.stderr}"


def test_presentation_model_harness_runs_in_node() -> None:
    node = _node_binary()
    harness = Path(__file__).resolve().parent / "js" / "presentation.test.mjs"

    result = subprocess.run(
        [node, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
    )

    assert result.returncode == 0, result.stderr
    assert "presentation model harness: ok" in result.stdout


def test_presentation_sources_never_build_payloads_with_innerhtml() -> None:
    for name in PRESENTATION_MODULES:
        source = (PRESENTATION_DIR / name).read_text(encoding="utf-8")
        assert "innerHTML" not in source, name
        assert "outerHTML" not in source, name
        assert "insertAdjacentHTML" not in source, name


async def test_meta_reports_the_approval_gate(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        approval_gate=True,
        server_url="http://127.0.0.1:9",
        approval_timeout_seconds=90.0,
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as http_client:
        meta = (await http_client.get("/api/meta")).json()

    assert meta["approvalGate"] is True
    assert meta["approvalTimeoutSeconds"] == 90.0


def test_non_loopback_bind_needs_the_insecure_opt_in(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match="Refusing to bind"):
        create_app(offline_config, runs_dir=tmp_path, bind_host="0.0.0.0")

    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        bind_host="0.0.0.0",
        allow_insecure_bind=True,
    )
    assert app is not None
