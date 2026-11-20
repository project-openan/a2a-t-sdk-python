"""Command line of the AI evaluation support layer.

Run from the sample root::

    python -m eval.osp.ai profile init
    python -m eval.osp.ai instance up
    python -m eval.osp.ai batch run --cases 1 --protocols a2a-t --stop-after working
    python -m eval.osp.ai triage <batch-id>
    python -m eval.osp.ai report <batch-id>
    python -m eval.osp.ai instance down
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Mapping, Sequence

from eval.osp.ai import batch as batch_module
from eval.osp.ai import evidence, report, triage
from eval.osp.ai.http import HttpError
from eval.osp.ai.instance import InstanceError, down, status, up
from eval.osp.ai.nms import active_tasks, cancel_and_wait
from eval.osp.ai.paths import (
    batch_json_path,
    failures_path,
    manifest_path,
    report_path,
)
from eval.osp.ai.profile import (
    DEFAULT_PROFILE_PATH,
    launcher_argv,
    load_profile,
    manifest,
    port_conflicts,
    sanitized_env,
    validate_profile,
)
from eval.osp.ai.types import FailureRecord, Profile

#: Sample root used by the CLI and by the launcher.
SAMPLE_ROOT = Path(__file__).resolve().parents[3]
#: Repository root that owns ``docs-local/``.
REPO_ROOT = SAMPLE_ROOT.parent.parent
#: Default report directory.
DOCS_LOCAL = REPO_ROOT / "docs-local"

#: Stages whose failure is explained by the wire exchange rather than by a field diff.
_RUNTIME_STAGES = frozenset({"send", "negotiate", "accepted", "working", "final", "item"})

_PROFILE_TEMPLATE = """\
# OSP AI evaluation instance profile. Keep this file out of git.
# Bind ports are passed to run_demo.py as arguments; the rest becomes the child environment.
A2AT_OSP_AI_NAME=osp-ai
A2AT_OSP_AI_RUN_DIR={run_dir}
A2AT_OSP_AI_RUNS_DIR={runs_dir}
A2AT_OSP_AI_UI_HOST=127.0.0.1
A2AT_OSP_AI_UI_PORT=18080
A2AT_OSP_AI_OSS_PORT=18081
A2AT_OSP_AI_MIE_PORT=18082
A2AT_OSP_AI_WEBHOOK_HOST=127.0.0.1
A2AT_OSP_AI_WEBHOOK_PORT=19000
A2AT_OSP_AI_OSS_WEBHOOK_PORT=19001
# Both receivers must be reachable from the vendor NMS.
A2AT_OSP_AI_WEBHOOK_PUBLIC_URL=https://<yellow-host>:19000/webhook
A2AT_OSP_AI_OSS_WEBHOOK_PUBLIC_URL=https://<yellow-host>:19001/webhook
A2AT_OSP_AI_PROTOCOL=both
A2AT_OSP_AI_MODEL_MODE=live
A2AT_OSP_AI_DECISION_MODE=live
A2AT_OSP_AI_APPROVAL=auto
A2AT_OSP_AI_LANGUAGE=en-US
A2AT_OSP_AI_NMS=1
A2AT_OSP_AI_NMS_SIM=0
A2AT_OSP_AI_TRANSPORT=webhook
A2AT_OSP_AI_OWNER=osp-ai

# Model endpoint (the local proxy in this workspace).
A2AT_LLM_API_BASE=http://127.0.0.1:31943/v1
A2AT_LLM_MODEL=mass/volcengine/deepseek-v4.1-flash

# Vendor NMS credentials. Never commit this file.
A2AT_SAMPLE_NMS_A2A_BASE=
A2AT_SAMPLE_NMS_TOKEN_URL=
A2AT_SAMPLE_NMS_TOKEN_USER=
A2AT_SAMPLE_NMS_TOKEN_VALUE=
A2AT_SAMPLE_NMS_REGION=A2ATestRegion
"""


def main(argv: Sequence[str] | None = None) -> int:
    """Run one CLI command and return its exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return _dispatch(args)
    except (InstanceError, batch_module.BatchError, HttpError, FileNotFoundError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def build_parser() -> argparse.ArgumentParser:
    """Return the argument parser."""
    parser = argparse.ArgumentParser(prog="python -m eval.osp.ai", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--profile", type=Path, default=None, help="profile file (default: ~/.config/a2at/osp-ai.env)")
    common.add_argument("--json", action="store_true", help="print the machine-readable result")

    profile_parser = sub.add_parser("profile", parents=[common], help="inspect or create the instance profile")
    profile_sub = profile_parser.add_subparsers(dest="profile_command", required=True)
    init_parser = profile_sub.add_parser("init", parents=[common], help="write a profile template")
    init_parser.add_argument("--path", type=Path, default=None)
    init_parser.add_argument("--force", action="store_true")
    profile_sub.add_parser("show", parents=[common], help="show the resolved profile")

    instance_parser = sub.add_parser("instance", parents=[common], help="start, inspect or stop the instance")
    instance_sub = instance_parser.add_subparsers(dest="instance_command", required=True)
    up_parser = instance_sub.add_parser("up", parents=[common], help="start the isolated instance")
    up_parser.add_argument("--wait", type=float, default=120.0)
    instance_sub.add_parser("status", parents=[common], help="show the instance state")
    down_parser = instance_sub.add_parser("down", parents=[common], help="stop the instance and clean its tasks")
    down_parser.add_argument("--wait", type=float, default=30.0)
    down_parser.add_argument("--keep-tasks", action="store_true")

    batch_parser = sub.add_parser("batch", parents=[common], help="drive a tester batch headlessly")
    batch_sub = batch_parser.add_subparsers(dest="batch_command", required=True)
    run_parser = batch_sub.add_parser("run", parents=[common], help="create, start and wait for one batch")
    _add_selection_args(run_parser)
    run_parser.add_argument("--timeout", type=float, default=21600.0, help="batch wall-clock budget (seconds)")
    run_parser.add_argument("--case-timeout", type=float, default=None, help="per-case run timeout (seconds)")
    run_parser.add_argument("--poll", type=float, default=1.0)
    run_parser.add_argument("--no-wait", action="store_true", help="create and start, then return")
    run_parser.add_argument("--no-report", action="store_true", help="skip triage and report at the end")
    wait_parser = batch_sub.add_parser("wait", parents=[common], help="wait for an existing batch")
    wait_parser.add_argument("batch_id")
    wait_parser.add_argument("--timeout", type=float, default=21600.0)
    wait_parser.add_argument("--poll", type=float, default=1.0)

    triage_parser = sub.add_parser("triage", parents=[common], help="flatten one batch into failures.jsonl")
    triage_parser.add_argument("batch_id")
    triage_parser.add_argument("--batch-file", type=Path, default=None)
    triage_parser.add_argument("--runs-dir", type=Path, default=None)
    triage_parser.add_argument("--out", type=Path, default=None)

    report_parser = sub.add_parser("report", parents=[common], help="render the self-contained report")
    report_parser.add_argument("batch_id")
    report_parser.add_argument("--batch-file", type=Path, default=None)
    report_parser.add_argument("--runs-dir", type=Path, default=None)
    report_parser.add_argument("--out", type=Path, default=None)
    report_parser.add_argument("--detail-lines", type=int, default=20)

    evidence_parser = sub.add_parser("evidence", parents=[common], help="read run artifacts")
    evidence_sub = evidence_parser.add_subparsers(dest="evidence_command", required=True)
    wire_parser = evidence_sub.add_parser("wire", parents=[common], help="read a wire log in full")
    wire_parser.add_argument("run_id")
    wire_parser.add_argument("--path", type=Path, default=None)
    wire_parser.add_argument("--direction", choices=("in", "out"), default=None)
    wire_parser.add_argument("--kind", default=None)
    wire_parser.add_argument("--task", default=None)
    wire_parser.add_argument("--grep", default=None)
    wire_parser.add_argument("--limit", type=int, default=None)
    wire_parser.add_argument("--raw", action="store_true", help="do not redact")
    trace_parser = evidence_sub.add_parser("trace", parents=[common], help="read the run transcript/trace")
    trace_parser.add_argument("run_id")
    evidence_sub.add_parser("tasks", parents=[common], help="list active vendor Tasks")

    tasks_parser = sub.add_parser("tasks", parents=[common], help="cancel vendor Tasks")
    tasks_sub = tasks_parser.add_subparsers(dest="tasks_command", required=True)
    cancel_parser = tasks_sub.add_parser("cancel", parents=[common], help="cancel one or more Tasks")
    cancel_parser.add_argument("task_ids", nargs="+")
    cancel_parser.add_argument("--no-recheck", action="store_true")

    skill_parser = sub.add_parser("install-skill", parents=[common], help="install the RUNBOOK as a project skill")
    skill_parser.add_argument("--target", type=Path, default=None)
    return parser


def _add_selection_args(parser: argparse.ArgumentParser) -> None:
    """Add the case/protocol selection arguments of ``batch run``."""
    parser.add_argument("--cases", default="", help="comma separated case ids")
    parser.add_argument("--ids-file", type=Path, default=None, help="one case id per line")
    parser.add_argument("--all", action="store_true", help="select all 51 cases")
    parser.add_argument("--protocols", default="a2a-t,a2a")
    parser.add_argument("--stop-after", default="working")
    parser.add_argument("--approval", default=None)
    parser.add_argument("--decision-mode", default=None)
    parser.add_argument("--working-timeout", type=float, default=None)
    parser.add_argument("--keep-tasks", action="store_true")


def _dispatch(args: argparse.Namespace) -> int:
    """Dispatch one parsed command."""
    if args.command == "profile":
        return _profile_command(args)
    if args.command == "install-skill":
        return _install_skill(args)
    profile = load_profile(args.profile)
    if args.command == "instance":
        return _instance_command(args, profile)
    if args.command == "batch":
        return _batch_command(args, profile)
    if args.command == "triage":
        return _triage_command(args, profile)
    if args.command == "report":
        return _report_command(args, profile)
    if args.command == "evidence":
        return _evidence_command(args, profile)
    if args.command == "tasks":
        return _tasks_command(args, profile)
    raise ValueError(f"unknown command {args.command!r}")


def _profile_command(args: argparse.Namespace) -> int:
    """Create or show the instance profile."""
    if args.profile_command == "init":
        target = (args.path or args.profile or DEFAULT_PROFILE_PATH).expanduser()
        if target.exists() and not args.force:
            raise ValueError(f"profile already exists: {target} (use --force)")
        target.parent.mkdir(parents=True, exist_ok=True)
        name = target.stem
        text = _PROFILE_TEMPLATE.format(
            run_dir=Path.home() / ".config" / "a2at" / name / "launcher",
            runs_dir=SAMPLE_ROOT / "runs-ai",
        )
        target.write_text(text, encoding="utf-8", newline="\n")
        _chmod_600(target)
        print(f"wrote {target}")
        return 0
    profile = load_profile(args.profile)
    document = _profile_document(profile)
    _print_document(document, json_output=args.json)
    return 0 if not document["errors"] else 1


def _instance_command(args: argparse.Namespace, profile: Profile) -> int:
    """Start, inspect or stop the instance."""
    if args.instance_command == "up":
        state = up(profile, wait_seconds=args.wait)
        _print_document(state.to_dict(), json_output=args.json)
        return 0
    if args.instance_command == "status":
        _print_document(status(profile).to_dict(), json_output=args.json)
        return 0
    result = down(profile, cancel_tasks=not args.keep_tasks, wait_seconds=args.wait)
    _print_document(result, json_output=args.json)
    return 0 if not result.get("errors") else 1


def _batch_command(args: argparse.Namespace, profile: Profile) -> int:
    """Create and wait for a batch."""
    if args.batch_command == "wait":
        snapshot = batch_module.wait(profile, args.batch_id, timeout_seconds=args.timeout, poll_seconds=args.poll)
        _print_batch(snapshot, json_output=args.json)
        return 0
    case_ids = _case_ids(args)
    protocols = _csv(args.protocols)
    batch_module.validate_selection(protocols, args.stop_after)
    snapshot = batch_module.create(
        profile,
        case_ids=case_ids,
        protocols=protocols,
        stop_after=args.stop_after,
        approval=args.approval,
        timeout_seconds=args.case_timeout,
        working_timeout_seconds=args.working_timeout,
        decision_mode=args.decision_mode,
        keep_tasks=args.keep_tasks,
    )
    snapshot = batch_module.start(profile, snapshot.batch_id)
    if args.no_wait:
        _print_batch(snapshot, json_output=args.json)
        return 0
    last = {"status": "", "summary": {}}

    def _tick(current: batch_module.BatchSnapshot) -> None:
        marker = (current.status, json.dumps(current.summary, sort_keys=True))
        if marker == (last["status"], json.dumps(last["summary"], sort_keys=True)):
            return
        last.update(status=current.status, summary=dict(current.summary))
        print(f"[batch] {current.batch_id} {current.status} {current.summary}", file=sys.stderr)

    finished = batch_module.wait(
        profile,
        snapshot.batch_id,
        timeout_seconds=args.timeout,
        poll_seconds=args.poll,
        on_tick=_tick,
    )
    _print_batch(finished, json_output=args.json)
    if batch_module.ui_restart_cancelled(finished):
        print("warning: the UI restarted during the batch; rerun it instead of triaging it", file=sys.stderr)
    if args.no_report:
        return 0
    return _triage_and_report(profile, finished.batch_id, detail_lines=20)


def _triage_and_report(profile: Profile, batch_id: str, *, detail_lines: int) -> int:
    """Write ``failures.jsonl`` and the report of one finished batch."""
    batch_path = batch_json_path(profile.runs_dir, batch_id)
    records = _records(Path(profile.runs_dir), batch_path=batch_path)
    out_path = failures_path(profile.runs_dir, batch_id)
    triage.write_failures(records, out_path)
    batch_manifest = _batch_manifest(profile, batch_path, batch_id)
    manifest_path(profile.runs_dir, batch_id).write_text(
        json.dumps(batch_manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    text = report.render_report(
        manifest=batch_manifest,
        batch=triage.load_batch(batch_path),
        records=records,
        excerpts=_excerpts(records),
        detail_limit=detail_lines,
    )
    target = report_path(DOCS_LOCAL, batch_id)
    report.write_report(target, text)
    print(f"failures: {out_path}")
    print(f"report: {target}")
    return 0


def _triage_command(args: argparse.Namespace, profile: Profile) -> int:
    """Flatten one batch into ``failures.jsonl``."""
    runs_dir = Path(args.runs_dir) if args.runs_dir else Path(profile.runs_dir)
    batch_path = args.batch_file or batch_json_path(runs_dir, args.batch_id)
    records = _records(runs_dir, batch_path=batch_path)
    out_path = args.out or failures_path(runs_dir, args.batch_id)
    triage.write_failures(records, out_path)
    summary = triage.summarize(records)
    _print_document(
        {"batchId": args.batch_id, "failures": str(out_path), "summary": summary},
        json_output=args.json,
    )
    return 0


def _report_command(args: argparse.Namespace, profile: Profile) -> int:
    """Render one self-contained report."""
    runs_dir = Path(args.runs_dir) if args.runs_dir else Path(profile.runs_dir)
    batch_path = args.batch_file or batch_json_path(runs_dir, args.batch_id)
    records = _records(runs_dir, batch_path=batch_path)
    text = report.render_report(
        manifest=_batch_manifest(profile, batch_path, args.batch_id),
        batch=triage.load_batch(batch_path),
        records=records,
        excerpts=_excerpts(records),
        detail_limit=args.detail_lines,
    )
    target = args.out or report_path(DOCS_LOCAL, args.batch_id)
    report.write_report(target, text)
    print(f"report: {target}")
    return 0


def _evidence_command(args: argparse.Namespace, profile: Profile) -> int:
    """Read wire logs, transcripts or active tasks."""
    if args.evidence_command == "tasks":
        rows = active_tasks(profile)
        _print_document({"tasks": rows, "count": len(rows)}, json_output=args.json)
        return 0
    run_id = args.run_id
    if args.evidence_command == "trace":
        row = evidence.read_run_row(profile.runs_dir, run_id)
        document = {
            "runId": run_id,
            "row": row,
            "transcript": evidence.read_transcript(profile.runs_dir, run_id),
            "trace": evidence.read_trace(profile.runs_dir, run_id),
            "taskSnapshot": evidence.read_task_snapshot(profile.runs_dir, run_id),
        }
        _print_document(document, json_output=True)
        return 0
    path = args.path or (Path(profile.runs_dir) / run_id / "wire.jsonl")
    rows = evidence.read_wire(
        path,
        direction=args.direction,
        kind=args.kind,
        task_id=args.task,
        grep=args.grep,
        limit=args.limit,
    )
    if not args.raw:
        rows = [evidence.redact(row) for row in rows]
    _print_document({"runId": run_id, "path": str(path), "count": len(rows), "frames": rows}, json_output=True)
    return 0


def _tasks_command(args: argparse.Namespace, profile: Profile) -> int:
    """Cancel one or more vendor Tasks."""
    results = [
        cancel_and_wait(profile, task_id)
        if not args.no_recheck
        else {"taskId": task_id, "accepted": _cancel_once(profile, task_id)}
        for task_id in args.task_ids
    ]
    _print_document({"tasks": results}, json_output=args.json)
    return 0


def _cancel_once(profile: Profile, task_id: str) -> bool:
    """Cancel one Task without the re-check loop."""
    from eval.osp.ai.nms import cancel_task

    return cancel_task(profile, task_id)


def _install_skill(args: argparse.Namespace) -> int:
    """Copy the RUNBOOK into the project's skill directory."""
    source = SAMPLE_ROOT / "eval" / "osp" / "ai" / "RUNBOOK.md"
    if not source.is_file():
        raise FileNotFoundError(f"runbook not found: {source}")
    target = args.target or (REPO_ROOT / ".claude" / "skills" / "osp-ai-eval" / "SKILL.md")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(source.read_text(encoding="utf-8"), encoding="utf-8", newline="\n")
    print(f"installed {target}")
    return 0


def _batch_manifest(profile: Profile, batch_path: Path, batch_id: str) -> dict[str, object]:
    """Return the manifest of one batch, deriving its selection from the batch document."""
    document = triage.load_batch(batch_path)
    raw_case_ids = document.get("caseIds")
    case_ids = [value for value in raw_case_ids if isinstance(value, int)] if isinstance(raw_case_ids, list) else []
    raw_protocols = document.get("protocols")
    protocols = [str(value) for value in raw_protocols] if isinstance(raw_protocols, list) else []
    raw_stop_after = document.get("stopAfter")
    stop_after = raw_stop_after if isinstance(raw_stop_after, str) else None
    extra = {
        "batchStatus": document.get("status"),
        "decisionMode": document.get("decisionMode") or profile.decision_mode,
        "approval": document.get("approval") or profile.approval,
        "batchCreatedAt": document.get("createdAt"),
        "batchStartedAt": document.get("startedAt"),
        "batchFinishedAt": document.get("finishedAt"),
    }
    return manifest(
        profile,
        batch_id=batch_id,
        case_ids=case_ids,
        protocols=protocols,
        stop_after=stop_after,
        extra=extra,
    )


def _records(runs_dir: Path, *, batch_path: Path) -> list[FailureRecord]:
    """Return the failure records of one batch."""
    document = triage.load_batch(batch_path)
    return triage.failure_records(document, batch_path=batch_path, runs_dir=Path(runs_dir))


def _excerpts(records: Sequence[FailureRecord]) -> dict[str, str]:
    """Return a bounded, self-contained evidence excerpt per failure record.

    A pre-send failure (intent / task_t / validate) is explained by its first divergent field, not
    by the outbound frame; a runtime failure is explained by the wire exchange. Each excerpt is
    capped and redacted by ``evidence.excerpt`` so the blue zone never needs the yellow files.
    """
    extracts: dict[str, str] = {}
    for record in records:
        key = f"{record.case_id}:{record.protocol}:{record.stage}"
        parts: list[str] = []
        if record.first_diff is not None:
            parts.append("firstDiff:\n" + evidence.excerpt(record.first_diff, max_lines=8))
            parts.append("expected:\n" + evidence.excerpt(record.expected, max_lines=6))
            parts.append("actual:\n" + evidence.excerpt(record.actual, max_lines=6))
        if record.stage in _RUNTIME_STAGES:
            wire = record.artifacts.wire
            if wire is None:
                parts.append("wire: (no wire.jsonl for this run)")
            else:
                rows = evidence.read_wire(wire, task_id=record.run.task_id, limit=20)
                parts.append(
                    "wire:\n"
                    + evidence.excerpt(rows or {"path": str(wire), "note": "no matching frame"}, max_lines=20)
                )
        extracts[key] = "\n\n".join(parts) if parts else "(no inline evidence; see artifacts)"
    return extracts


def _case_ids(args: argparse.Namespace) -> list[int]:
    """Return the selected case ids from ``--cases`` / ``--ids-file`` / ``--all``."""
    if args.all:
        from eval.osp.catalog import CaseCatalog

        return sorted(summary.id for summary in CaseCatalog().summaries())
    ids = [int(value) for value in _csv(args.cases)]
    if args.ids_file is not None:
        ids.extend(
            int(line.strip())
            for line in args.ids_file.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        )
    if not ids:
        raise ValueError("select at least one case with --cases, --ids-file or --all")
    return sorted(set(ids))


def _profile_document(profile: Profile) -> dict[str, object]:
    """Return the serialisable profile plus its validation state."""
    return {
        "profile": {
            "name": profile.name,
            "sourcePath": str(profile.source_path) if profile.source_path is not None else None,
            "sampleRoot": str(profile.sample_root),
            "runDir": str(profile.run_dir),
            "runsDir": str(profile.runs_dir),
            "uiUrl": profile.ui_url,
            "ports": {label: port for label, port in profile.bind_ports},
            "protocol": profile.protocol,
            "modelMode": profile.model_mode,
            "decisionMode": profile.decision_mode,
            "approval": profile.approval,
            "language": profile.language,
            "owner": profile.owner,
            "nms": profile.nms,
            "nmsSim": profile.nms_sim,
            "webhookPublicUrl": profile.webhook_public_url,
            "ossWebhookPublicUrl": profile.oss_webhook_public_url,
        },
        "launcher": launcher_argv(profile, action="up"),
        "envKeys": sorted(sanitized_env(profile)),
        "errors": validate_profile(profile),
        "portConflicts": port_conflicts(profile),
    }


def _print_batch(snapshot: batch_module.BatchSnapshot, *, json_output: bool) -> None:
    """Print a batch compactly, or its full document with ``--json``."""
    if json_output:
        _print_document(snapshot.to_dict(), json_output=True)
        return
    items = []
    for item in snapshot.items:
        stages = item.get("stages")
        items.append(
            {
                "caseId": item.get("caseId"),
                "protocol": item.get("protocol"),
                "status": item.get("status"),
                "stages": {
                    str(name): stage.get("status")
                    for name, stage in (stages.items() if isinstance(stages, Mapping) else ())
                    if isinstance(stage, Mapping)
                },
            }
        )
    _print_document(
        {
            "batchId": snapshot.batch_id,
            "status": snapshot.status,
            "summary": dict(snapshot.summary),
            "items": items,
        },
        json_output=False,
    )


def _print_document(document: object, *, json_output: bool) -> None:
    """Print a document as JSON or as a short human summary."""
    if json_output:
        print(json.dumps(document, ensure_ascii=False, indent=2, default=str))
        return
    if isinstance(document, Mapping):
        for key, value in document.items():
            print(f"{key}: {json.dumps(value, ensure_ascii=False, default=str)}")
        return
    print(json.dumps(document, ensure_ascii=False, default=str))


def _csv(value: str) -> list[str]:
    """Split a comma separated CLI value."""
    return [item.strip() for item in value.split(",") if item.strip()]


def _chmod_600(path: Path) -> None:
    """Best-effort owner-only permission on the profile (it carries a vendor token)."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


if __name__ == "__main__":
    raise SystemExit(main())
