# Vendored upstream: LangfuseObserveTools

This directory is a vendored copy of the `langfuse_export` package from
[LangfuseObserveTools](git@gitcode.com:xflyer/LangfuseObserveTools.git).

| Item | Value |
|---|---|
| Source repo | `git@gitcode.com:xflyer/LangfuseObserveTools.git` |
| Version | 0.4.3 |
| Commit | `dc0d872` |
| Vendored into | `a2a-t-sample/ran-energy-saving/langfuse_export/` |
| Tests vendored into | `a2a-t-sample/ran-energy-saving/tests/langfuse_export/` |

## Upstream changes since 0.2.6

- **0.3.0** — `--table`: derive the Issue AF–AW `metrics.xlsx` from `metrics.json`
  (new `table.py`; `writer.write_xlsx`).
- **0.4.0** — multi-trace batch export: `trace_id` is now `nargs="+"` (space or comma
  separated); `run_batch_export` writes one `batch_<ts>/` directory, one combined
  `table.xlsx`, and `batch.json`; a failed trace does not abort the batch.
- **0.4.1–0.4.2** — table column order/Chinese headers; token columns split per
  client/server side.
- **0.4.3** — `tokens.bySide` always contains `client` and `server` zero buckets, so
  pure A2A runs (no LLM usage) print `0` instead of blank cells.

## Local patches

The sample applies six small patches to the vendored copy. They are deliberately
minimal so a future upstream refresh is "replace the directory, replay P1-P6".

| Patch | Location | Change |
|---|---|---|
| P1 | `cli.run_export` | Add `config=None`, `emit=True` and `print_result=True` (the latter is upstream). A supplied `Config` skips `load_config`; `emit=False` suppresses every stdout/stderr write made by the function (the `--insecure` notice, the `--verbose` network line, the per-request log callback, and the final result block even when `print_result` is True). The CLI path and `main()` keep the default `emit=True`. |
| P2 | `client.LangfuseClient` / `cli.run_export` | Add `close()` for the authenticated `requests.Session`; `run_export` closes its client in a `finally`, including when `_collect` raises. `_close_client` is defensive for fake clients without `close`. |
| P3 | `cli.export_metrics` / `cli.export_full` | Add two output-free functions for the web observer: `export_metrics(config, trace_id)` returns `{"metrics": ..., "warnings": [...]}` without disk writes or prints; `export_full(config, trace_id, out_root, *, zip_file=True)` builds the argparse Namespace (`[trace_id, "--out", out_root, "--metrics", "--no-media"]` + zip flag) and calls `run_export(..., config=config, emit=False)`. |
| P4 | `cli._collect` | Extract the shared fetch/derive steps from `run_export` into an internal `_collect(...)` used by both `run_export` and `export_metrics`, so the same trace yields identical `metrics`, `endToEndMs`, `notFound` behaviour and warnings on both paths. |
| P5 | `metrics._negotiation` | After scanning rows and before the server fallback: for every round with no `negotiationStartedAt` but a `proposeAt`, set `negotiationStartedAt = proposeAt`, `negotiationStartedAtSource = "client:propose"`, `clock = "client"`. A real client `negotiation_started` still wins; the a2a-t-server fallback only fills rounds that are still unset. Pure A2A rounds (propose + Decision, no `negotiation_started`) therefore get a non-null `latencyMs` from propose→decision and are not marked `clock=server`. |
| P6 | `metrics._negotiation` | Each `roundsDetail[]` entry gains `negotiationType` and `hop`, read from the propose row's flattened Langfuse metadata `detail.negotiationType` / `detail.hop` (a nested `metadata["detail"]` dict is also accepted for unit fixtures; both default to `None`). A2A-T propose rows may carry `negotiationType`; `hop` is usually `None` there. |

The vendored tests keep the upstream layout with one rename: `test_metrics.py` becomes
`test_export_metrics.py` because the sample already has a top-level `tests/test_metrics.py` and
pytest imports these modules without packages. Replay the rename during a sync.
