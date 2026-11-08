# Field notes: LLM latency, intent default, token accounting

This file is checked in for the yellow-zone field integration. The design details and raw
measurements live in the untracked `docs-local/qwen3.8-请求耗时定位.md`; this page keeps only the
actionable conclusions and switches.

## 1. Why qwen3.8 is slow

The model reasons for a long time on a cold prefix. The request itself is valid:

- The same ~6k-token request takes 78-229 s cold and 3-6 s immediately after the prefix is warm.
- Every run carries different operator text after the system prompt, so the prefix is almost always
  cold.
- On this gateway the top-level `enable_thinking: false` and `reasoning_effort: low` do **not**
  work (measured). Only a gateway-side no-think route or `chat_template_kwargs` can change it.

Raising the timeout alone does not fix it.

## 2. New and changed keys

| Key | Default | Behavior |
| --- | --- | --- |
| `A2AT_LLM_EXTRA_BODY` | empty | JSON object forwarded as the OpenAI SDK `extra_body` (merged into the request body). Example: `{"chat_template_kwargs":{"enable_thinking":false}}` |
| `A2AT_LLM_TIMEOUT_RETRIES` | `0` | Extra attempts after a transport timeout (0-5). Covers **SDK structured calls only**, retries only `APITimeoutError`; the ADK/LiteLlm turn is not covered |
| `A2AT_LLM_TEMPERATURE` | source `.env` value | Now applied when a structured call site passes no temperature |
| `A2AT_LLM_MAX_TOKENS` | source `.env` value | Still **not applied** to structured calls: a low cap truncates a reasoning model into invalid JSON. A call site that needs a cap passes `max_tokens` explicitly |
| `A2AT_SAMPLE_INTENT_MODE` | **`raw`** | A2A-T no longer runs the ADK intent layer by default; see section 4 |

## 3. Timeout budget

- `A2AT_SAMPLE_LLM_TIMEOUT_SECONDS` (default **120 s**): one LLM call. It always overrides the
  source `A2AT_LLM_TIMEOUT_SECONDS`.
- `A2AT_SAMPLE_HTTP_TIMEOUT_SECONDS` (default **300 s**): the whole A2A request.
- With retries enabled:

  ```text
  A2AT_SAMPLE_LLM_TIMEOUT_SECONDS x (A2AT_LLM_TIMEOUT_RETRIES + 1) < A2AT_SAMPLE_HTTP_TIMEOUT_SECONDS
  ```

  Otherwise the HTTP request times out first. 120 s x 2 = 240 s fits; 300 s x 2 = 600 s does not.
  This bounds **one call**; one A2A request may still run slot extraction, validation and other
  structured calls in series, so their sum must also fit the HTTP budget.
- Retry is a **temporary mitigation**, not the fix. The second request may hit the server-side
  prefix cache (156 s -> 5.9 s measured), but a client-side timeout does not guarantee the gateway
  keeps and caches the computation.

## 4. A2A-T now defaults to `raw` intent

- A2A-T Task-T is extracted by the SDK from the operator's own text; the ADK intent layer only feeds
  the intent panel and approval 1.
- With the new default there is no `LLM call · adk_agent` span, and approval 1 shows the request /
  rendered Task-T without the structured intent card.
- To restore the card, set `A2AT_SAMPLE_INTENT_MODE=processed`, or pass `--intent-mode processed` /
  `POST /api/runs {"intentMode":"processed"}` for one run.
- An existing `.env` that still sets `A2AT_SAMPLE_INTENT_MODE=processed` keeps the old behavior;
  delete the line or set `raw` to get the new default.
- **Pure A2A is unaffected**: the OSS Agent always runs its own intent layer (`demo/intent.py`).

## 5. Reading token metrics

- `tokens.prompt` sums **only the calls that reported usage**; a timed-out call contributes nothing.
- `tokens.missingCalls`: calls without usage; the UI labels it as "N no usage".
- `tokens.estimatedCalls` / `tokens.promptEstimated`: a request-text estimate for missing calls that
  still carry their request payload. It is **not** added to `tokens.prompt`.
- Example: `prompt=689, missingCalls=1, promptEstimated~6000` means the real request was about 6k
  tokens and 689 is only the one successful call.

## 6. Field retest

1. Clean restart: `./run_demo.py --stop`, then start the target mode; do not reuse an old OSS
   process.
2. Confirm the intent default: the source `.env` has no `A2AT_SAMPLE_INTENT_MODE=processed`. An
   A2A-T run must have **no** `LLM call · adk_agent` span.
3. Try the gateway no-think switch:

   ```bash
   A2AT_LLM_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}'
   ```

   If latency does not change, oneapi does not honor the field; ask the administrator for a no-think
   route/alias.
4. Enable retry only as a temporary fallback:

   ```bash
   A2AT_SAMPLE_LLM_TIMEOUT_SECONDS=120
   A2AT_LLM_TIMEOUT_RETRIES=1
   ```

   Check the budget in section 3.
5. If it still fails, report the run id, instance/port/model, request text, the key `wire*.jsonl`
   frames, the `task_t_*_extraction` span `durationMs` and `usage`, and whether it reproduces
   offline.

## 7. Rollback

Remove `A2AT_LLM_EXTRA_BODY` / `A2AT_LLM_TIMEOUT_RETRIES`, set
`A2AT_SAMPLE_INTENT_MODE=processed`, and restore `A2AT_SAMPLE_LLM_TIMEOUT_SECONDS=120`.
