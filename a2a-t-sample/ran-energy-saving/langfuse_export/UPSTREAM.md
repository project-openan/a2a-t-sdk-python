# Vendored upstream: LangfuseObserveTools

This directory is a vendored copy of the `langfuse_export` package from
[LangfuseObserveTools](git@gitcode.com:xflyer/LangfuseObserveTools.git).

| Item | Value |
|---|---|
| Source repo | `git@gitcode.com:xflyer/LangfuseObserveTools.git` |
| Version | `0.5.1` |
| Commit | `69bd7e6` |
| Vendored into | `a2a-t-sample/ran-energy-saving/langfuse_export/` |
| Tests vendored into | `a2a-t-sample/ran-energy-saving/tests/langfuse_export/` |

The vendored copy was produced by rebasing the previous `0.4.3` snapshot (`dc0d872`)
onto `69bd7e6` in a scratch mirror, not by replacing the directory. The local patches
below were replayed during that rebase.

## Upstream changes since 0.4.3

- **0.5.0** — negotiation round model switch: `--round-mode {legacy,decision}`,
  default `legacy`. `decision` pairs each PROPOSE with the DECISION that answers it
  (intended for pure A2A, where proposals carry no round metadata). `build_metrics`
  takes `round_mode`, and `negotiation.roundMode` reports the mode actually used.
- **0.5.1** — `metrics.xlsx` gains a `协议` column at index 1, immediately after
  `trace ID`; every later column shifts by one. Version/docs updated.

## Sync procedure: per-file replay

Do **not** replace the whole directory. A directory replace drops vendored-only
adaptations that the rest of the sample depends on:

- `tests/conftest.py` — `ROOT = Path(__file__).resolve().parents[2]`, plus `HERE` and
  `FIXTURES`. Upstream assumes the tests live at the repo root; here they are nested
  under `tests/langfuse_export/`, so the import path and fixture directory must be
  recomputed from `HERE`.
- `tests/test_render.py` — `ROOT = Path(__file__).resolve().parent`; the golden
  transcript is resolved next to the test, not at the upstream repo root.
- `tests/langfuse_export/samples/transcript.md` — the golden file lives beside the
  vendored tests, not in the upstream root-level `samples/` directory.
- `client.close()` (P2) — upstream `client.py` has a zero delta from 0.4.3 to 0.5.1,
  so the patch is easy to miss; a directory replace restores the unpatched file and
  leaks the authenticated `requests.Session`.

Replay order: copy the files that actually changed upstream (`cli.py`, `metrics.py`,
`table.py`, `__init__.py`, and the tests), re-apply P1–P8 and P9a/P9b below per file,
then re-apply the four vendored-only adaptations above. Keep the `test_metrics.py` →
`test_export_metrics.py` rename and the sample-only assertions listed under "Tests".

## Local patches

| Patch | Location | Change |
|---|---|---|
| P1 | `cli.run_export` | Add `config=None`, `emit=True` and `print_result=True` (the latter is upstream). A supplied `Config` skips `load_config`; `emit=False` suppresses every stdout/stderr write made by the function (the `--insecure` notice, the `--verbose` network line, the per-request log callback, and the final result block even when `print_result` is True). The CLI path and `main()` keep the default `emit=True`. |
| P2 | `client.LangfuseClient` / `cli.run_export` | Add `close()` for the authenticated `requests.Session`; `run_export` closes its client in a `finally`, including when `_collect` raises. `_close_client` is defensive for fake clients without `close`. |
| P3 | `cli.export_metrics` / `cli.export_full` | Output-free functions for the web observer: `export_metrics(config, trace_id, *, run_id=None, round_mode="legacy")` returns `{"metrics": ..., "warnings": [...]}` without disk writes or prints; `export_full(config, trace_id, out_root, *, zip_file=True, run_id=None, round_mode="legacy")` builds the argparse Namespace (`[trace_id, "--out", out_root, "--metrics", "--no-media"]` plus `--run-id` when supplied, `--round-mode` when not `legacy`, and the zip flag) and calls `run_export(..., config=config, emit=False)`. |
| P4 | `cli._collect` | Extract the shared fetch/derive steps from `run_export` into an internal `_collect(...)` used by both `run_export` and `export_metrics`, so the same trace yields identical `metrics`, `endToEndMs`, `notFound` behaviour and warnings on both paths. |
| P5 | `metrics._negotiation` | After scanning rows and before the server fallback: for every round with no `negotiationStartedAt` but a `proposeAt`, set `negotiationStartedAt = proposeAt`, `negotiationStartedAtSource = "client:propose"`, `clock = "client"`. A real client `negotiation_started` still wins; the a2a-t-server fallback only fills rounds that are still unset. Pure A2A rounds (propose + Decision, no `negotiation_started`) therefore get a non-null `latencyMs` from propose→decision and are not marked `clock=server`. |
| P6 | `metrics._negotiation` | Each `roundsDetail[]` entry gains `negotiationType` and `hop`, read from the propose row's flattened Langfuse metadata `detail.negotiationType` / `detail.hop` (a nested `metadata["detail"]` dict is also accepted for unit fixtures; both default to `None`). A2A-T propose rows may carry `negotiationType`; `hop` is usually `None` there. |
| P7 | `metrics` / `cli` | Run-attempt selection for shared correction traces: `--run-id`, `select_run_id`, `_distinct_run_ids`, `_merged_run_meta`; `run_rows` (timeline, tokens, metadata) vs `audit_rows` (token/negotiation audit; a shared trace must not mix the other attempt's rows); `metrics.attemptCount` / `metrics.attemptRunIds`. Without an explicit id, `select_run_id` picks the attempt that actually sent Task-T (`message_sent` / `task_created`, newest wins) and warns when the trace has several runs. |
| P8 | vendored files | Ruff formatting churn (line wrapping, quote style) with no behaviour change. Re-run the formatter after replay instead of hand-merging. |
| P9a | `metrics._negotiation_decision` | Decision-mode `roundsDetail[]` carries `negotiationType` / `hop` through the same flattened-or-nested lookup as P6, so `--round-mode decision` does not lose the A2A-T detail fields. |
| P9b | `metrics._negotiation_decision` | Decision-mode sorting uses a float total-order key (`float("-inf")` for rows without `startTime`, UTC-normalized naive timestamps), so a row with no start time never compares against a `datetime` and naive/aware timestamps do not mix. The `decisions` list and `next_decision_round` / `previous_decision_round` helpers are annotated `datetime \| None`. |
| P9c | `cli.export_metrics` / `cli.export_full` | Validate `round_mode` against `ROUND_MODES` before building the argparse `Namespace`. `argparse` answers an invalid `--round-mode` with `SystemExit`, a `BaseException` that escapes `except Exception`; the wrappers now raise `ValueError` instead, and the observe job manager maps a `SystemExit` from one trace to that trace's error row rather than letting it kill the server. |

## Tests

The vendored tests keep the upstream layout with one rename: `test_metrics.py` becomes
`test_export_metrics.py` because the sample already has a top-level `tests/test_metrics.py`
and pytest imports these modules without packages. Replay the rename during a sync.

Sample-only additions that upstream does not have:

- the three upstream `test_decision_round_mode_*` tests (PROPOSE-DECISION pairing, two
  decision rounds, client `negotiation_started` attachment);
- two sample-only decision tests: P9a carries `negotiationType`/`hop`, P9b tolerates a
  row without `startTime`, plus a flattened-`detail.*` variant of the P9a assertion;
- the legacy `roundMode` assertion (the default path must report `"legacy"`);
- the `__version__ == "0.5.1"` assertion;
- the CLI `--round-mode` forwarding test (`run_export` passes the parsed mode through to
  `build_metrics`) and the P9c rejection test (`ValueError`, never `SystemExit`);
- the `test_table` protocol assertion (`headers[:5]` and the shifted column indices after
  the new `协议` column).

## Historical version strings

`demo/web/observe/table.py` and `tests/test_observe_table.py` contain
`上游 0.4.3 起，缺失侧输出 0` / `0.4.3` strings. They describe upstream's 0.4.3
token-bucket semantics and are pinned by tests; they are historical notes, not version
carriers. Do **not** bump them when syncing the vendored package to a newer release.
