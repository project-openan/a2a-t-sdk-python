"""Tool-level tests of the corpus CLI tools: the INDEX.md determinism of ``tools/corpus_index.py``
(D25: regeneration is byte-identical to the committed index), the ``--check`` freshness gates of
``tools/corpus_index.py`` and ``tools/generate_golden.py`` (both must exit 0 against the committed
artifacts), and the report rendering of ``tools/live_transcript_export.py`` (verified against a
synthetic transcript, so no live endpoint is needed).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Final

import pytest

from tests.corpus.conftest import CORPUS_INDEX_FILE, CORPUS_ROOT
from tests.corpus.live.results import LiveCaseResult, Outcome
from tests.corpus.live.transcript import LiveTranscript
from tests.corpus.llm_stub import LiveLlmCall

#: Repository root (the tools live under ``tools/`` and resolve paths relative to it).
REPO_ROOT: Final[Path] = Path(__file__).resolve().parents[2]


def _load_tool(module_name: str) -> ModuleType:
    """Load one tools script as a module (``tools/`` is not a package)."""
    path = REPO_ROOT / "tools" / f"{module_name}.py"
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


corpus_index = _load_tool("corpus_index")
live_transcript_export = _load_tool("live_transcript_export")


# --------------------------------------------------------------------- corpus_index


def test_corpus_index_regeneration_is_byte_identical_to_the_committed_index() -> None:
    """The committed INDEX.md is exactly what the tool regenerates from the corpus JSONs."""
    rendered = corpus_index.render_index(CORPUS_ROOT)

    committed = CORPUS_INDEX_FILE.read_text(encoding="utf-8")
    assert rendered == committed.replace("\r\n", "\n"), (
        "INDEX.md drifted from the corpus JSONs; regenerate it with: uv run python tools/corpus_index.py"
    )


def test_corpus_index_rendering_is_deterministic() -> None:
    """Two renderings of the same corpus produce the same bytes (no timestamps or set ordering)."""
    assert corpus_index.render_index(CORPUS_ROOT) == corpus_index.render_index(CORPUS_ROOT)


def test_corpus_index_check_mode_exits_zero_against_the_committed_index() -> None:
    assert corpus_index.main(["--check"]) == 0


def test_corpus_index_check_mode_reports_a_missing_index(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """A corpus without an INDEX.md fails the freshness check (the soft local gate)."""
    empty_root = tmp_path / "negotiation-cases"
    empty_root.mkdir()

    assert corpus_index.main(["--corpus-root", str(empty_root), "--check"]) == 1
    assert "INDEX.md is missing" in capsys.readouterr().err


def test_corpus_index_write_mode_rewrites_the_same_content(tmp_path: Path) -> None:
    """Writing into a copy of the corpus reproduces the committed index byte for byte."""
    root = tmp_path / "negotiation-cases"
    root.mkdir()
    (root / "from-text").mkdir()
    (root / "from-text" / "cases.json").write_text(
        json.dumps(
            [
                {
                    "id": "FT-X-01",
                    "api": "generateProposeFromText",
                    "languages": ["zh-CN"],
                    "expect": {"outcome": "success"},
                }
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    assert corpus_index.main(["--corpus-root", str(root)]) == 0
    written = (root / "INDEX.md").read_text(encoding="utf-8")
    assert corpus_index.main(["--corpus-root", str(root), "--check"]) == 0
    assert "FT-X-01" in written
    assert b"\r" not in (root / "INDEX.md").read_bytes()


# --------------------------------------------------------------------- generate_golden


def test_generate_golden_check_mode_exits_zero_against_the_committed_fixtures() -> None:
    generate_golden = _load_tool("generate_golden")

    assert generate_golden.main(["--check"]) == 0


# --------------------------------------------------------------------- live_transcript_export


def _sample_run(tmp_path: Path) -> Path:
    """One synthetic transcript run carrying a passed generate case and a failed validate case."""
    run = LiveTranscript.create_run(tmp_path)
    run.append_case(
        LiveCaseResult(
            case_id="LIVE-GEN-01/zh-CN",
            outcome=Outcome.PASS,
            assertion_summary="expect.success=true scenarioCode=private-line-complaint llmCalls=2",
            input_summary="深圳访问广州的政企专线时延骤升。",
            scenario_code="private-line-complaint",
            params={"accessPort": "P533-01"},
            llm_calls=[
                LiveLlmCall(
                    messages=[{"role": "user", "content": "投诉文本"}],
                    json_schema={"type": "object"},
                    temperature=0.0,
                    max_tokens=None,
                    content='{"slots":{}}',
                    model="qwen3-27b",
                    usage={"prompt_tokens": 10, "completion_tokens": 4},
                    duration_ms=8,
                    error=None,
                )
            ],
            duration_ms=1234,
            failure_diff=None,
        )
    )
    run.append_case(
        LiveCaseResult(
            case_id="LIVE-VAL-01/zh-CN",
            outcome=Outcome.FAIL,
            assertion_summary="expect.success=true paramsAbsent llmCalls=1",
            input_summary=None,
            scenario_code=None,
            params=None,
            llm_calls=[],
            duration_ms=900,
            failure_diff="faultTime was filled but expected absent: 2026-05-11",
        )
    )
    return run.write().parent


def test_live_transcript_export_renders_the_report(tmp_path: Path) -> None:
    run_dir = _sample_run(tmp_path)

    assert live_transcript_export.main([str(run_dir)]) == 0

    report = (run_dir / "export" / "report.md").read_text(encoding="utf-8")
    assert "# Live corpus run transcript" in report
    assert "## Run summary" in report, "the summary section renders when summary.json exists"
    assert "cases: 2 (pass 1, fail 1, error 0, skip 0)" in report
    assert "## ✅ LIVE-GEN-01/zh-CN — PASS" in report
    assert "## ❌ LIVE-VAL-01/zh-CN — FAIL" in report
    assert "### Failure diff" in report and "faultTime" in report
    assert "### Extracted params" in report and "P533-01" in report
    assert "### LLM call 1" in report
    assert "Replay request (copy into Postman)" in report


def test_live_transcript_export_replay_request_mirrors_the_openai_client_payload(tmp_path: Path) -> None:
    """The replay body carries exactly the JSON-mode instruction and schema message the
    production ``OpenAIClient`` prepends (the sync guarantee the Java copy keeps by hand)."""
    run_dir = _sample_run(tmp_path)
    transcript = json.loads((run_dir / "transcript.json").read_text(encoding="utf-8"))
    call = transcript[0]["llmCalls"][0]

    replay = live_transcript_export.build_replay_request(call)

    from a2a_t.llm.providers.openai import _JSON_MODE_INSTRUCTION_DEFAULT

    assert [message["role"] for message in replay["messages"]] == ["system", "user"]
    system_content = replay["messages"][0]["content"]
    assert system_content.startswith(_JSON_MODE_INSTRUCTION_DEFAULT)
    assert "Return JSON that conforms to this JSON schema: " in system_content
    assert replay["messages"][1] == {"role": "user", "content": "投诉文本"}
    assert replay["response_format"] == {"type": "json_object"}
    assert replay["temperature"] == 0.0
    assert replay["model"] == "qwen3-27b"


def test_live_transcript_export_merges_a_recorded_system_message_into_the_single_system_message() -> None:
    """A recorded leading system prompt joins the same merged system message the client sends."""
    replay = live_transcript_export.build_replay_request(
        {
            "model": "qwen3-27b",
            "messages": [
                {"role": "system", "content": "SYS"},
                {"role": "user", "content": "USER"},
            ],
            "jsonSchema": {"type": "object"},
        }
    )

    assert [message["role"] for message in replay["messages"]] == ["system", "user"]
    assert "SYS" in replay["messages"][0]["content"]
    assert "Return JSON that conforms to this JSON schema: " in replay["messages"][0]["content"]
    assert replay["messages"][1] == {"role": "user", "content": "USER"}


def test_live_transcript_export_accepts_a_transcript_file(tmp_path: Path) -> None:
    run_dir = _sample_run(tmp_path)
    out_dir = tmp_path / "exported"

    assert live_transcript_export.main([str(run_dir / "transcript.json"), "--out", str(out_dir)]) == 0

    assert (out_dir / "report.md").is_file()


def test_live_transcript_export_fails_on_a_missing_transcript(tmp_path: Path) -> None:
    with pytest.raises(SystemExit) as aborted:
        live_transcript_export.main([str(tmp_path)])

    assert "transcript not found" in str(aborted.value)
