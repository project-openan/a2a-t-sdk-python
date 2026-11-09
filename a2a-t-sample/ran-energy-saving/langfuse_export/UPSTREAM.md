# Vendored upstream: LangfuseObserveTools

This directory is a vendored copy of the `langfuse_export` package from
[LangfuseObserveTools](git@gitcode.com:xflyer/LangfuseObserveTools.git).

| Item | Value |
|---|---|
| Source repo | `git@gitcode.com:xflyer/LangfuseObserveTools.git` |
| Version | 0.2.6 |
| Commit | `7c088a2` |
| Vendored into | `a2a-t-sample/ran-energy-saving/langfuse_export/` |
| Tests vendored into | `a2a-t-sample/ran-energy-saving/tests/langfuse_export/` |

## Local patches

The sample applies four small patches to the vendored copy. They are deliberately
minimal so a future upstream refresh is "replace the directory, replay P1-P4".

| Patch | Location | Change |
|---|---|---|
| P1 | `cli.run_export` | Add `config=None` and `emit=True`. A supplied `Config` skips `load_config`; `emit=False` suppresses every stdout/stderr write (success lines, `--insecure` notice, verbose network line, per-request log callback). The CLI path and `main()` are unchanged. |
| P2 | `client.LangfuseClient` / `cli.run_export` | Add `close()` for the authenticated `requests.Session`; `run_export` closes its client in a `finally`, including when an exception is raised. |
| P3 | `cli.export_metrics` / `cli.export_full` | Add two output-free functions for the web observer: `export_metrics(config, trace_id)` returns `{"metrics": ..., "warnings": [...]}` without disk writes; `export_full(config, trace_id, out_root, *, zip_file=True)` builds the argparse Namespace (`--out`, `--metrics`, `--no-media`, zip flag) and calls `run_export(..., config=config, emit=False)`. |
| P4 | `cli._collect` | Extract the shared fetch/derive steps from `run_export` into an internal `_collect(...)` used by both `run_export` and `export_metrics`, so the same trace yields identical `metrics`, `endToEndMs`, `notFound` behaviour and warnings on both paths. |

The vendored tests keep the upstream layout with one rename: `test_metrics.py` becomes
`test_export_metrics.py` because the sample already has a top-level `tests/test_metrics.py` and
pytest imports these modules without packages. Replay the rename during a sync.
