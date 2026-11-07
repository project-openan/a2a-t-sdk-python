"""Static contract tests of the Run Explorer assets (no network, no browser).

The page is a no-build ES-module app, so the tests assert the parts a bundler would otherwise
guarantee: every module is served, the import graph is complete, payload rendering never uses
``innerHTML``, and the visual tokens/responsive rules of the spec are present.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from demo.web.app import STATIC_DIR, create_app
from shared.config import SampleConfig

#: JavaScript modules of the explorer.
MODULES: tuple[str, ...] = (
    "api",
    "dom",
    "filters",
    "format",
    "i18n/en",
    "i18n/index",
    "i18n/zh-CN",
    "inspector",
    "main",
    "shortcuts",
    "store",
    "timeline",
)

#: The public API operations the UI contract relies on.
API_ROUTES: tuple[str, ...] = (
    "/api/meta",
    "/api/tasks",
    "/api/client/runs",
    "/api/client/conversations",
    "/api/runs",
    "/api/runs/${encodeURIComponent(runId)}",
    "/events",
    "/decisions",
    "/spans/",
    "/trace.json",
    "/transcript.json",
    "/transcript.md",
)

@pytest.fixture
async def static_client(
    offline_config: SampleConfig,
    tmp_path: Path,
) -> AsyncIterator[httpx.AsyncClient]:
    """HTTP client bound to the ASGI app (no run is started)."""
    app = create_app(
        offline_config,
        runs_dir=tmp_path,
        finished_grace_seconds=0.0,
        ui_mode="explorer",
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        yield client


def _module_source(name: str) -> str:
    """Read one JavaScript module from the static directory."""
    return (STATIC_DIR / "js" / f"{name}.js").read_text(encoding="utf-8")


def _imports(source: str, module: str = "") -> set[str]:
    """Return the module names imported by one ES module source.

    ``i18n/index.js`` imports its dictionaries relatively, so a module inside a sub-directory
    resolves bare targets against that directory.
    """
    directory = f"{module.rsplit('/', 1)[0]}/" if "/" in module else ""
    found: set[str] = set()
    for target in re.findall(r'from\s+"\./([\w/-]+)\.js"', source):
        found.add(target if "/" in target or not directory else f"{directory}{target}")
    return found


def _function_source(source: str, name: str) -> str:
    """Return the text of one top-level JavaScript function."""
    marker = f"function {name}("
    start = source.index(marker)
    end = len(source)
    for next_marker in ("\nfunction ", "\nasync function "):
        index = source.find(next_marker, start + len(marker))
        if index != -1:
            end = min(end, index)
    return source[start:end]


def _method_source(source: str, name: str) -> str:
    """Return the text of one two-space-indented JavaScript class method."""
    start = source.index(f"{name}(")
    end = source.find("\n  }", start)
    return source[start : end + len("\n  }")]


async def test_every_asset_is_served(static_client: httpx.AsyncClient) -> None:
    css = await static_client.get("/static/app.css")
    assert css.status_code == 200
    assert "text/css" in css.headers["content-type"]
    assert "--bg-canvas" in css.text

    for name in MODULES:
        response = await static_client.get(f"/static/js/{name}.js")
        assert response.status_code == 200, name
        assert "javascript" in response.headers["content-type"]
        assert "export " in response.text, name


async def test_the_module_graph_is_complete(static_client: httpx.AsyncClient) -> None:
    page = (await static_client.get("/")).text
    assert "/static/js/main.js" in page

    reached: set[str] = {"main"}
    pending = ["main"]
    while pending:
        name = pending.pop()
        for imported in _imports(_module_source(name), name):
            if imported not in reached:
                reached.add(imported)
                pending.append(imported)
    assert set(MODULES) <= reached
    for name in reached:
        assert (STATIC_DIR / "js" / f"{name}.js").is_file(), name
        assert (await static_client.get(f"/static/js/{name}.js")).status_code == 200


def test_payloads_are_never_rendered_with_innerhtml() -> None:
    for name in MODULES:
        source = _module_source(name)
        assert "innerHTML" not in source, name
        assert "outerHTML" not in source, name
        assert "insertAdjacentHTML" not in source, name
    # Views must build nodes and set text explicitly.
    for name in ("timeline", "inspector", "main"):
        assert "textContent" in _module_source(name), name
    main_source = _module_source("main")
    assert "replaceChildren" in main_source
    assert "renderTaskTable" in main_source
    assert "refreshTasks" in main_source
    assert "refreshClientRuns" in main_source
    assert "renderClientRunTable" in main_source
    assert "renderClientRunDetail" in main_source


def test_api_module_covers_the_backend_contract() -> None:
    source = _module_source("api")
    assert "EventSource" in source
    assert "seen" in source and "seq" in source
    assert "onDuplicate" in source
    for route in API_ROUTES:
        assert route in source, route
    assert "getSpan" in source
    assert "getTasks" in source
    assert "getClientRuns" in source
    assert "getClientRun" in source
    assert "getConversations" in source


def test_store_module_deduplicates_and_exposes_the_phase_model() -> None:
    source = _module_source("store")
    assert "if (this.frames.has(seq)) return false;" in source
    for phase in (
        "dispatch",
        "task_prompt",
        "task_validation",
        "negotiation",
        "resolution",
        "execution",
        "report",
        "done",
    ):
        assert phase in source, phase
    assert "phaseStates" in source
    assert "decisionLog" in source
    assert "anomalies" in source
    assert "value.notAvailableScripted" in _module_source("format")


def test_inspector_declares_all_tabs_and_lazy_lookup() -> None:
    source = _module_source("inspector")
    for tab in ("summary", "protocol", "llm-request", "llm-response", "tool", "raw"):
        assert f'key: "{tab}"' in source, tab
    assert "getSpan" not in source  # the API stays in api.js/main.js
    assert "fetchSpan" in source
    assert "systemInstruction" in source
    assert "jsonSchema" in source
    assert "functionCalls" in source
    assert "parseError" in source
    assert "args" in source and "result" in source


def test_timeline_renders_phases_rows_and_nesting() -> None:
    source = _module_source("timeline")
    assert "phase-card" in source
    assert "tl-row" in source
    assert "actor-badge" in source
    assert "sideBadge" in source
    assert "side-badge" in _module_source("dom")
    assert "parentSpanId" in source
    assert "waterfall" in source
    assert "round-chip" in source
    assert "formatTokens" in source
    assert "shortModel" in source


def test_shortcuts_exist_for_every_documented_key() -> None:
    source = _module_source("shortcuts")
    for key in ("j", "k", "Enter", "Escape", "/", "f", "?", "1", "6", "[", "]"):
        assert f'"{key}"' in source, key
    for handler in ("next", "previous", "open", "escape", "search", "follow", "tab", "help", "submitDecision"):
        assert handler in source, handler
    assert "Cmd/Ctrl + Enter" in source


def test_filters_cover_the_five_chips_and_follow_states() -> None:
    source = _module_source("filters")
    for chip in ("all", "protocol", "llm", "tools", "errors"):
        assert f'key: "{chip}"' in source, chip
    assert "class FollowController" in source
    assert "header.pausedNew" in source


async def test_index_shell_is_accessible_and_complete(static_client: httpx.AsyncClient) -> None:
    page = (await static_client.get("/")).text
    assert 'class="skip-link"' in page
    assert 'role="banner"' in page
    assert "<main" in page
    assert 'aria-label="Run controls"' in page
    assert 'aria-label="Step inspector"' in page
    assert 'role="tablist"' in page
    assert 'aria-live="polite"' in page
    assert "<dialog" in page
    assert "prefers-color-scheme" not in page  # theme lives in CSS
    assert page.count("data-field=") == 10  # both manual panels carry all five fields
    assert 'data-role="countdown"' in page
    assert 'data-role="error"' in page
    assert 'id="history"' in page
    assert 'id="history-toggle"' in page
    assert "data-history-tab=" in page
    assert page.count("data-history-tab=") == 3
    assert 'id="task-table"' in page
    assert 'id="task-table-body"' in page
    assert 'id="client-run-table"' in page
    assert 'id="client-run-table-body"' in page
    assert 'id="client-run-detail"' in page
    assert 'id="conversation-table"' in page
    assert 'id="conversation-table-body"' in page
    assert 'id="conversation-detail"' in page
    assert "Client runs" in page
    assert 'id="locale-select"' in page
    for phase in (
        "dispatch",
        "task_prompt",
        "task_validation",
        "negotiation",
        "resolution",
        "execution",
        "report",
        "done",
    ):
        assert f'data-phase="{phase}"' in page, phase


def test_every_element_id_used_by_main_exists_in_the_shell() -> None:
    """The page is wiring-only: a missing id would only surface as a runtime TypeError."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    source = _module_source("main")
    used = set(re.findall(r'\$\("([\w-]+)"\)', source))
    assert used
    missing = sorted(name for name in used if f'id="{name}"' not in page)
    assert not missing, missing


def test_the_ui_names_the_client_oss_and_the_execution_side_nms() -> None:
    """OSS is the client layer; the executing side is the RAN network management system."""
    page = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    en = _module_source("i18n/en")
    zh = _module_source("i18n/zh-CN")

    # The history tab and side chips use the corrected role names.
    assert 'tasks: "NMS tasks"' in en
    assert 'tasks: "网管任务"' in zh
    assert 'client: "OSS"' in en and 'server: "NMS"' in en
    assert 'client: "OSS"' in zh and 'server: "网管"' in zh
    # The old, wrong naming never reaches the page or the copy.
    for source in (page, en, zh):
        assert "OSS task" not in source
        assert "OSS tasks" not in source
        assert "OSS 任务" not in source
        assert "OSS execution" not in source
        assert "OSS 执行" not in source


def test_css_implements_the_visual_spec() -> None:
    css = (STATIC_DIR / "app.css").read_text(encoding="utf-8")
    for token, value in (
        ("--bg-canvas", "#f6f7f9"),
        ("--bg-surface", "#ffffff"),
        ("--text", "#1a1f26"),
        ("--accent", "#1d4ed8"),
        ("--actor-client", "#1d4ed8"),
        ("--actor-server", "#0b6b4a"),
        ("--actor-llm", "#6d28d9"),
        ("--actor-tool", "#92400e"),
        ("--actor-human", "#0e7490"),
    ):
        assert token in css
        assert value in css.lower(), token
    assert "@media (prefers-color-scheme: dark)" in css
    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ":focus-visible" in css
    assert "grid-template-columns: var(--rail-width) minmax(0, 1fr) var(--inspector-width)" in css
    for breakpoint in ("max-width: 1359px", "max-width: 1119px", "max-width: 899px", "max-width: 639px"):
        assert breakpoint in css, breakpoint
    for anatomy in (
        ".tl-row",
        ".tl-node",
        ".actor-badge",
        ".side-badge",
        ".round-chip",
        ".status-pill",
        ".inspector-tabs",
        ".task-table",
        ".client-run-detail",
        ".client-event-table",
    ):
        assert anatomy in css, anatomy
    assert '.connection-state[data-state="closed"]' in css


def test_raw_json_copy_and_download_keep_the_full_payload() -> None:
    format_source = _module_source("format")
    assert "value.length > 20000" not in format_source
    assert "truncated ${value.length" not in format_source
    assert "formatJsonForDisplay" in format_source
    assert "format.displayTruncated" in format_source
    inspector_source = _module_source("inspector")
    assert "formatJsonForDisplay(raw)" in inspector_source
    assert inspector_source.count('const text = typeof value === "string" ? value : formatJson(value);') == 2


def _node_binary() -> str:
    """Return the Node.js executable, skipping the test cleanly when it is absent."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is not installed")
    return node


def test_every_js_module_passes_node_syntax_check() -> None:
    """``node --check`` parses every served module (the only syntax gate of a build-free app)."""
    node = _node_binary()
    modules = sorted(path for path in STATIC_DIR.joinpath("js").rglob("*.js"))
    assert modules
    for module in modules:
        result = subprocess.run(
            [node, "--check", str(module)],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        assert result.returncode == 0, f"{module.name}: {result.stderr or result.stdout}"


def test_in_repo_store_harness_runs_in_node() -> None:
    """The store/filter harness lives in the repo and runs against the real modules."""
    node = _node_binary()
    harness = Path(__file__).resolve().parent / "js" / "store.test.mjs"
    assert harness.is_file()

    result = subprocess.run(
        [node, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "store/filters runtime checks passed" in result.stdout


def test_i18n_harness_runs_in_node() -> None:
    """The copy harness proves key parity and that every referenced key exists."""
    node = _node_binary()
    harness = Path(__file__).resolve().parent / "js" / "i18n.test.mjs"
    assert harness.is_file()

    result = subprocess.run(
        [node, str(harness)],
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )

    assert result.returncode == 0, result.stderr or result.stdout
    assert "i18n runtime checks passed" in result.stdout


def test_format_module_behavior_for_large_payloads_and_offline_tokens() -> None:
    """Run the pure formatters in Node when it is available (no browser or DOM needed)."""
    node = _node_binary()
    module_url = json.dumps((STATIC_DIR / "js" / "format.js").as_uri())
    script = "\n".join(
        [
            "import assert from 'node:assert/strict';",
            f"import {{ formatJson, formatJsonForDisplay, formatTokens }} from {module_url};",
            "const big = 'x'.repeat(30000);",
            "const full = formatJson({ prompt: big });",
            "assert.ok(full.length > 30000);",
            "assert.ok(!full.includes('truncated'));",
            "assert.equal(JSON.parse(full).prompt.length, 30000);",
            "const shown = formatJsonForDisplay({ prompt: big }, { maxChars: 2000 });",
            "assert.ok(shown.includes('display truncated'));",
            "assert.ok(shown.length < 3000);",
            "assert.equal(formatTokens(null, { scripted: true }), 'n/a (scripted)');",
            "assert.equal(formatTokens(null), '\\u2014');",
            "assert.equal(formatTokens({ prompt: 1, completion: 2, total: 3 }), '1\\u21922 tok');",
            "console.log('format-ok');",
        ]
    )
    result = subprocess.run(
        [node, "--input-type=module", "-e", script],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr or result.stdout
    assert "format-ok" in result.stdout


def test_run_form_exposes_the_user_request_editor() -> None:
    """The rail lets the user edit the original request used by the Task-T generator."""
    html = (STATIC_DIR / "index.html").read_text(encoding="utf-8")
    assert 'id="user-request-input"' in html
    assert 'id="reset-request-button"' in html

    source = _module_source("main")
    assert "userRequest:" in _function_source(source, "startRun")
    assert "fillUserRequest" in _function_source(source, "populateMeta")


def test_switching_hash_runs_closes_the_previous_stream_and_resets_decision_state() -> None:
    source = _module_source("main")
    resume = _function_source(source, "resumeRun")
    assert resume.index("stopSubscription()") < resume.index("store.beginRun")
    assert "resetRunUiState()" in resume
    for name in ("startRun", "resumeRun"):
        assert "resetRunUiState()" in _function_source(source, name), name
    reset = _function_source(source, "resetRunUiState")
    for key in (
        "decisionSeq",
        "decisionAt",
        "decisionTimeout",
        "decisionTimedOut",
        "decisionSubmitting",
        "disconnected",
        "fatalStream",
        "finalizing",
        "pendingScrollKey",
    ):
        assert key in reset, key
    # connect() must close anything left over before subscribing.
    connect = _function_source(source, "connect")
    assert connect.index("stopSubscription()") < connect.index("api.subscribe")


def test_run_summary_only_enables_the_summary_tab() -> None:
    source = _module_source("inspector")
    assert 'if (!this.item) return ["summary"];' in source


def test_finished_grace_comes_from_the_backend_and_late_spans_merge_on_finalize() -> None:
    """The client grace must cover the server window, and finalize must not lose a late batch."""
    source = _module_source("main")
    grace = _function_source(source, "finishedGraceMs")
    assert "finishedGraceMs" in grace
    assert "Math.max" in grace
    finalize = _function_source(source, "finalizeRun")
    assert "api.getTrace" in finalize
    assert "store.mergeSpans" in finalize
    store = _module_source("store")
    merge = _method_source(store, "mergeSpans")
    assert "knownSpans.has" in merge
    assert "knownSeq.has" in merge


def test_offline_llm_rows_label_absent_tokens_as_scripted() -> None:
    timeline = _module_source("timeline")
    assert 'scripted: store.runMeta().modelMode === "offline"' in timeline
    inspector = _module_source("inspector")
    tokens = _method_source(inspector, "_tokens")
    assert 'modelMode === "offline"' in tokens
    assert "formatTokens(item.tokens, { scripted })" in tokens


def test_programmatic_selection_scrolls_the_timeline() -> None:
    source = _module_source("main")
    assert "pendingScrollKey" in source
    assert "timeline.scrollToItem(pendingKey)" in source
    assert "selectItem(entry.key, { scroll: true })" in source
    assert "selectItem(anomaly.key, { scroll: true })" in source
    assert "onSelectKey: (key) => selectItem(key, { scroll: true })" in source
    assert "app.pendingScrollKey = store.selectedKey;" in source
    timeline = _module_source("timeline")
    assert "this.collapsed.delete(item.phase)" in timeline


def test_stepper_states_are_not_colour_only() -> None:
    source = _module_source("main")
    assert 'const marker = { pending: "○", skipped: "–", failed: "✕", done: "✓", active: "●" }' in source
    assert "meta.textContent = `${marker} ${t(`phaseState.${state}`)}`" in source
    assert "meta.textContent = `${marker} ${parts.join(\" · \")}`" in source


def test_menu_aria_expanded_is_reset_on_every_close_path() -> None:
    source = _module_source("main")
    assert "function setMenuOpen(open)" in source
    assert 'setMenuOpen($("menu-panel").hidden)' in source
    assert source.count("setMenuOpen(false)") >= 3


def test_closed_stream_has_a_distinct_fatal_state() -> None:
    source = _module_source("main")
    assert "fatalStream" in source
    assert 'state = "closed"' in source
    assert "clearStreamStateIfTerminal" in source
    finalize = _function_source(source, "finalizeRun")
    assert "clearStreamStateIfTerminal(snapshot)" in finalize
    assert 't("connection.closed")' in source
    api = _module_source("api")
    assert "handlers.onError?.(new Error(t(\"notify.eventStreamClosed\")), true)" in api
