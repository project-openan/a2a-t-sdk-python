# 现场联调注意：LLM 延迟、意图默认、token 统计

本文给黄区现场联调用，随代码入库。设计细节与实验数据在未入库的
`docs-local/qwen3.8-请求耗时定位.md`，这里只留可执行的结论和开关。

## 1. qwen3.8 慢的结论

慢在模型默认思考 + 前缀缓存未命中，不是报文错误：

- 同一份 6k token 的请求，冷前缀 78–229s；刚发过一次后 3–6s。
- 每次 run 的用户请求文字不同，排在 system 之后，所以几乎每次都是冷路径。
- 顶层 `enable_thinking: false` / `reasoning_effort: low` 在该网关上**已实测无效**。

因此代码侧能做的只有：让网关走 no-think 路由（或认 `chat_template_kwargs`），以及可选的
失败重试。**不要只靠调大超时。**

## 2. 新增 / 变更的配置键

| 键 | 默认 | 行为 |
| --- | --- | --- |
| `A2AT_LLM_EXTRA_BODY` | 空 | JSON 对象，原样作为 OpenAI SDK 的 `extra_body` 合并进请求体。例：`{"chat_template_kwargs":{"enable_thinking":false}}` |
| `A2AT_LLM_TIMEOUT_RETRIES` | `0` | 传输超时后的额外尝试次数（0–5）。只覆盖 **SDK structured 调用**，只重试 `APITimeoutError`；不覆盖 ADK/LiteLlm 那一轮 |
| `A2AT_LLM_TEMPERATURE` | 源 `.env` 值 | 现在**真正生效**：调用点没传 temperature 时使用它 |
| `A2AT_LLM_MAX_TOKENS` | 源 `.env` 值 | **仍不作用于 structured 调用**：低上限会把思考输出截成非法 JSON。需要硬上限的调用点显式传 `max_tokens` |
| `A2AT_SAMPLE_INTENT_MODE` | **`raw`** | A2A-T 默认不再跑 ADK 意图解析；见第 4 节 |

## 3. 超时预算怎么算

- `A2AT_SAMPLE_LLM_TIMEOUT_SECONDS`（默认 **120s**）：单次 LLM 调用预算，总是覆盖源 `.env`
  里的 `A2AT_LLM_TIMEOUT_SECONDS`。
- `A2AT_SAMPLE_HTTP_TIMEOUT_SECONDS`（默认 **300s**）：整条 A2A 请求预算。
- 开启重试时必须满足：

  ```text
  A2AT_SAMPLE_LLM_TIMEOUT_SECONDS × (A2AT_LLM_TIMEOUT_RETRIES + 1) < A2AT_SAMPLE_HTTP_TIMEOUT_SECONDS
  ```

  否则 HTTP 会先超时。例如 120s × (1+1) = 240s < 300s 可用；300s × 2 = 600s 不可用。
  注意这是**单次调用**的上界；一条 A2A 请求里还可能串行发生槽位抽取、校验等多次 structured
  调用，总和也要留在 HTTP 预算内。
- 重试是**临时兜底**，不是根治。第二次请求可能命中服务端前缀缓存（实测 156s → 5.9s），
  但客户端超时后网关是否仍写入缓存没有保证。

## 4. A2A-T 默认 `raw` 意图

- A2A-T 的 Task-T 由 SDK 从 operator 原文生成，ADK 意图层只用于展示和审批 1。
- 默认 `raw` 后：不再产生 `LLM call · adk_agent`，审批 1 只显示原文/渲染后的 Task-T。
- 需要结构化意图卡时，在源 `.env` 设置 `A2AT_SAMPLE_INTENT_MODE=processed`，或用
  `--intent-mode processed` / `POST /api/runs {"intentMode":"processed"}`。
- 旧 `.env` 里如果还留着 `A2AT_SAMPLE_INTENT_MODE=processed`，会继续跑意图解析；要吃到新默认
  请删除该行或改成 `raw`。
- **pure A2A 不受影响**：OSS Agent 始终执行自己的意图层（`demo/intent.py`）。

## 5. token 统计怎么读

- `tokens.prompt` 只汇总**上报了 usage** 的调用；超时/无响应的调用不在里面。
- `tokens.missingCalls`：没有 usage 的调用数；UI 会显示“N 次无用量”。
- `tokens.estimatedCalls` / `tokens.promptEstimated`：对没有 usage 但抓到了 request 的调用，
  按请求文本估算的 prompt 大小。它**不会**混进 `tokens.prompt`。
- 所以看到 `prompt=689, missingCalls=1, promptEstimated≈6000` 时，真实请求约 6k token，
  689 只是那一次成功调用的用量。

## 6. 现场复测步骤

1. 干净重启：`./run_demo.py --stop`，再按目标模式启动；不要复用旧 OSS 进程。
2. 确认意图默认：源 `.env` 中没有 `A2AT_SAMPLE_INTENT_MODE=processed`；跑一个 A2A-T run，
   trace 里应**没有** `LLM call · adk_agent`。
3. 试网关 no-think：

   ```bash
   A2AT_LLM_EXTRA_BODY='{"chat_template_kwargs":{"enable_thinking":false}}'
   ```

   如果延迟不变，说明 oneapi 不认这个字段，需要管理员提供 no-think 路由/别名。
4. 需要临时兜底时再开重试：

   ```bash
   A2AT_SAMPLE_LLM_TIMEOUT_SECONDS=120
   A2AT_LLM_TIMEOUT_RETRIES=1
   ```

   并按第 3 节检查预算。
5. 仍然失败时，请带：run id、实例/端口/模型、请求原文、`wire*.jsonl` 关键帧、
   `task_t_*_extraction` span 的 `durationMs` 与 `usage`、是否离线可复现。

## 7. 回滚

删除 `A2AT_LLM_EXTRA_BODY` / `A2AT_LLM_TIMEOUT_RETRIES`，把
`A2AT_SAMPLE_INTENT_MODE=processed` 加回来，`A2AT_SAMPLE_LLM_TIMEOUT_SECONDS` 恢复 120 即可。
