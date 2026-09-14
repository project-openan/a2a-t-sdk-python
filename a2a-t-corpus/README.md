# a2a-t-corpus — A2A-T accuracy verification corpus

The data-driven accuracy verification of the A2A-T SDK: JSON workflow cases are executed through
the production SDK assembly against a **real LLM**, producing a complete per-step API/LLM
transcript, latency and token metrics, and a 95%+ accuracy target line that drives prompt tuning.

Extension coverage: **Task-T** is the reference extension; **Negotiation-T** follows the same
structure (target / information / feasibility negotiation for RAN energy saving). Notification-T
and Authorization-T extend the same way once their reference scenarios stabilize.

- **Test-only module**: lives under `a2a-t-corpus/`, is not part of the `a2a_t` wheel, and is
  deselected from CI with `-m 'not live'`.
- **A real LLM is required**: a missing or invalid `A2AT_LLM_*` configuration fails fast with an
  actionable error. The corpus `.env` keys reuse the project-root `env.example` naming, so the
  corpus template is a tuned subset of the root one. The structural self-guards run offline and
  stay inside CI.
- **Case JSON is the shared Java/Python contract**: the corpus case files and the two schemas
  under `schemas/` are byte-identical to the Java repository (`a2a-t-corpus`), and the tools under
  `tools/` are the same Python scripts.

## Quick start

```bash
cp a2a-t-corpus/env.example a2a-t-corpus/.env   # fill A2AT_LLM_BASE_URL / API_KEY / MODEL
uv run pytest a2a-t-corpus/suites/task/test_task_t_from_text_workflow.py -m live
uv run pytest a2a-t-corpus/suites/task -m live --corpus-scenario=private-line-complaint
uv run pytest a2a-t-corpus/suites/negotiation -m live --corpus-scenario='ran-energy-saving-*'
```

- `--corpus-scenario`: scenario name glob (comma-separated); `--case-filter`: case id glob.
  Test ids are `<scenario>/<case id>`, so `-k TC00000001` also reaches a single case.
- Every executed case prints to the console as it completes; failed/crashed cases additionally
  print their full interaction trace. Transcript files reflect only the current run.
- `--corpus-output-dir=<dir>`: redirect the `output_result_<flow>.json` files and the summary
  into the given directory (default: transcripts write back into the scenario directories, the
  summary into `a2a-t-corpus/.corpus/`).
- Without an LLM, run the structural gates first (these run in CI):
  `uv run pytest a2a-t-corpus/suites/task/test_task_self_guard.py a2a-t-corpus/suites/negotiation/test_negotiation_self_guard.py`.

## Directory

```
a2a-t-corpus/
├── env.example / .env (secrets, gitignored) / README.md / README_zh.md
├── schemas/              input-case.schema.json, output-result.schema.json (shared contract)
├── tools/                inputCsvToJson, outputJsonToCsv, csv-templates/ (same as Java)
├── conftest.py           pytest options, runtime fixture, case parametrization
├── engine/               the workflow framework (config/constants/loader/discover/registry/
│                         recorder/from_step/errors_serializer/assertion/engine/assembler/
│                         client_apis/server_apis/negotiation_apis/report/suite)
└── suites/               the per-extension workflow suites
    ├── task/             test_task_t_from_text_workflow, test_task_t_from_data_workflow,
    │   │                 test_self_guard
    │   └── resources/<scenario>/   input_case_from_text.json, input_case_from_data.json
    │                               (+ output_result_*.json written by each run)
    └── negotiation/      test_negotiation_t_from_text_workflow,
        │                 test_negotiation_t_from_data_workflow, test_negotiation_self_guard
        └── resources/<scenario>/   ran-energy-saving-target-negotiation,
                                    ran-energy-saving-information-negotiation,
                                    ran-energy-saving-feasibility-negotiation
```

## Case JSON contract (v1, first-hand definition in `schemas/`)

```json
{
  "id": "TC00000001", "caseDimension": "01-意图清晰参数完整", "caseDesc": "标准专线业务中断投诉",
  "caseCategory": "正常",
  "input": [
    { "api": "generateTaskPromptFromText",
      "args": { "text": "发生专线业务中断，接入端口名称为P781-……",
                "templateUri": "Task-T/network-layer/private-line-complaint/v1" } },
    { "api": "validateTaskPromptAndDataFilling",
      "args": { "schema": { "…": "JSON Schema" }, "templateUri": "…",
                "promptText": { "$fromStep": 1, "$field": "promptText" } } }
  ],
  "expected": [
    { "result": "success", "data": {}, "error": { "errCode": "", "errMessage": "" } },
    { "result": "success", "data": { "accessPort": "P781-……" }, "error": { "errCode": "", "errMessage": "" } }
  ]
}
```

- `id` uses a `TC` (TestCase) prefix and is unique within one case file.
- `input` / `expected` are equal-length arrays; order is the execution order; the same API may
  appear multiple times.
- `expected.result`: `success` / `error`; `data` is a subset assertion (optional `dataExact`
  tightens it to whole-map equality); `error.errCode` must be in the SDK error catalog.
- `{"$fromStep": N, "$field": "promptText"}` copies the response field of an earlier step
  (1-based); literal values win.
- `caseCategory` is an open enumeration: `正常` / `异常` / `边界` / `模糊语义` ...
- Loading is strict: unknown keys, missing expectations, length mismatches, unregistered APIs and
  out-of-catalog error codes fail at load time.

## Output and metrics

- Per scenario: `output_result_from_text.json` / `output_result_from_data.json`, one record per
  case; its `interactions` interleave **API steps and their underlying LLM calls** with complete
  requests/responses (error steps dump every property plus the cause chain), latency and tokens —
  never truncated.
- `a2a-t-corpus/.corpus/summary_report_<flow>.json` + console table: accuracy overall / by
  scenario / by dimension / by category / by API step (95% target line marked), latency p50/p95,
  token totals.

## Tools loop

```bash
python a2a-t-corpus/tools/inputCsvToJson.py --template --out my-cases.csv   # case design table
# fill the table, then convert (structural validation runs by default)
python a2a-t-corpus/tools/inputCsvToJson.py --csv my-cases.csv \
    --out a2a-t-corpus/suites/task/resources/<scenario>/input_case_from_text.json
# after a run, review (one row per case with complete per-step request/response)
python a2a-t-corpus/tools/outputJsonToCsv.py \
    --json a2a-t-corpus/suites/task/resources/<scenario>/output_result_from_text.json --out review.csv
# optional: fill an input JSON back into the design table
python a2a-t-corpus/tools/inputCsvToJson.py --reverse --csv <input_case_from_text.json> --out back.csv
```

## Adding a scenario (zero code)

Create `suites/<extension>/resources/<scenario>/` with the two input JSONs — the suites discover
it at collection time. Run the matching `test_*_self_guard.py` (offline) as the structural gate
first.

## Configuration name parity

The corpus configuration reuses the `A2AT_LLM_*` key family of the project-root `env.example`
(provider/base_url/api_key/model/temperature/timeout_seconds/max_attempts/max_tokens); the corpus
`env.example` is a tuned subset and extra keys are ignored. Resolution order: environment
variables override the `a2a-t-corpus/.env` file.
