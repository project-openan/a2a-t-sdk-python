# Trace span 与指标收集说明（phase 级）

> 适用版本：`feat/ran-energy-saving-adk`（`6a82a12` 之后）。
> 本文解释默认 `A2AT_SAMPLE_TRACE_SPAN_LEVEL=phase` 时进入 Langfuse 的每类 span 的作用、
> 核心内容，以及它们如何与 run metrics、Prometheus、SQLite 一起构成端到端统计。
> 配套阅读：`TECHNICAL-zh.md` 第 8 节（观测）、`docs-local/osp-eval-cases/README.md`（OSP 指标）。

## 1. 数据流

```
RunState / ClientFlow / ServerExecutor / ADK / LangGraph
        │  每个事件 → 一个 Span（kind + phase + startedAt/endedAt + detail/tokens）
        ▼
  run.traces（内存，全量） ──► Web UI 时间线 / transcript.json（全量，排障用）
        │
        ├──► LangfuseSpanExporter（按 trace_span_level 过滤）
        │        └── 每个 span = 一个 Langfuse observation
        │
        └──► build_run_metrics（run 结束后 2s grace）
                 ├── runs/client-state.sqlite3::run_metrics
                 └── runs/metrics/run-metrics.jsonl
                          ├── /api/metrics/runs|summary|export.csv、/comparison
                          └── summarize_metrics() 聚合
```

身份键（端到端不要混）：

| 键 | 含义 | 去向 |
|---|---|---|
| `runId` | 一次运行的唯一 id | span、SQLite、Langfuse trace metadata |
| `traceId` | 网管/下游可见的 32 位 hex；默认等于 runId | Langfuse trace id（每次 attempt 一条） |
| `conversationId` | 会话；纠错重试会新建 | 客户端会话/历史 |
| `chainId` | 一次纠错链（被拦截的 attempt + 其重试）；无重试时等于 conversationId | Langfuse `session.id`、trace metadata |
| `parentRunId` | 本 attempt 重试的前一条 run | trace metadata |
| `caseId` | OSP 用例 1–51（tester 启动时） | Langfuse trace metadata |
| `taskId` / `contextId` | A2A 任务身份 | span 字段、SQLite、webhook 路由 |

## 2. phase 级会进 Langfuse 的 span

`phase`（默认）是**显式白名单**，不是"每个 phase 取第一条"。每类 span 都是 OSP 时间点或
token/时延统计的载体。

| kind | observation 名 | phase | 生产者 | 核心内容 | 用途 |
|---|---|---|---|---|---|
| `llm` | `LLM call · <stage>` / `Planner turn` | 随 stage | `shared/traced_llm.py`、`shared/adk_tracing.py`、`server_executor` | request/response、model、`tokens{prompt,cached,completion,total}`、duration | AX/AY/AZ/BA、modelMs |
| `tool` | 工具名 / `Tool call · <name>` | negotiation / execution | ADK plugin、`server_executor` | 工具名、args、result、duration | toolMs、执行证据 |
| `message_sent` | `Task-T sent` / `Accept sent` / `Counter proposal sent · Rn` / `Pure A2A message sent` | task_prompt / task_validation / negotiation / resolution | `client_flow`、`pure_client_flow`、`plain_client_flow` | performative、templateUri、`taskT`（≤4000 字）、`negotiationT`、messageId | AM、AS；X 列 Task-T 原文 |
| `task_prompt_rendered` | `Task-T prompt rendered` / `Modify …` | task_prompt / task_modify | `client_flow` | Task-T 全文、templateUri、**SDK 调用起止/duration** | AL、X 列 |
| `task_validation` | `Task-T validation <verdict>` | task_validation / task_modify | `client_flow` | verdict、errors、negotiationKind、reason | Y 列（拦截结论） |
| `intent_processed` | `Intent processed` | task_prompt | `client_runtime`（A2A-T）/ `run_state`（pure 提升） | rawRequest、normalizedText、结构化 intent、missingFields | Z 列（A2A 侧） |
| `task_created` | `Task created` / `Task modified` | execution | `run_state._maybe_emit_task_created` | operation、state=`TASK_STATE_SUBMITTED`、taskId/contextId | **AN** |
| `propose` | `Propose · <kind> · Rn` | negotiation | 客户端收到服务端 PROPOSE（`record_event`） | round、templateUri、协商正文、negotiationContext | **AO/AP**、AW |
| `negotiation_started` | `Negotiation started · <kind> · Rn` | negotiation | `pre_task_negotiation`（本地 RAN Agent） | round、kind、templateUri、服务端理解起止 | **AT** |
| `accept` / `counter` / `reject` / `abort` | `Decision · <action> · Rn` | negotiation / resolution | `run_state.record_decision` | action、rationale、filledParams/counterParams、confirmedBy | **AQ**、AW、BB |
| `approval` | `Approval requested` / `Approval resolved` | execution | `server_executor`（本地审批 / MIE） | approvalState、cellId、window、rate、timeout | 人工等待时延 |
| `finished` | `Run finished` | done | `run_state.emit_finished` | finalState、report、error | 结束时间、终态 |
| root | `run · <scenario> · <protocol>` | — | exporter 创建 | trace 级 metadata（见 §5） | 运行级时间窗、结果 |
| event | `trace opened` | — | exporter 创建 | 与 root 相同的 trace 属性 | 让 Langfuse 尽早就绪 |

**只在 `full` 导出**（排障证据，默认不进 dashboard）：`run_started`、`runtime_ready`、
`discovery`、`intent_review_requested`、`task_tracking`、`decision_requested` /
`decision_made` / `decision_ungrounded`、`execution`、`artifact`（execution/report）、
非终态与终态 `status`、`transport`（NMS token/stream/poll、错误帧等原始报文）。

**`minimal`**：只保留 `llm`、`tool`、`finished`（以及无 `finished` 时的最后一条 status）。
它不能支撑 OSP 时间点统计，只适合压量看模型/工具开销。

过滤实现：`shared/langfuse_tracing.py::LangfuseSpanExporter._should_export`。
UI 时间线和 transcript 永远保留全部 span，过滤只影响 Langfuse。

### 2.1 OSS 侧 LLM 调用详解

OSS 进程里有两条 LLM 链路，观测名就能区分：

| 链路 | 生产者 | span 标题 | 是否有 stage |
|---|---|---|---|
| ADK agent（意图 / 决策 / 人工答复结构化） | `shared/adk_tracing.py` | `LLM call · <stage 或 adk_agent>` | 由 `trace_scope(stage=…)` 指定 |
| A2A-T SDK 注入的 traced provider | `shared/traced_llm.py` | `LLM call · <stage 或 structured>` | 由 `trace_stage(…)` 指定 |

当前 OSS 侧最常看到的四个名字：

| 观测名 | 触发点 | 作用 | 核心内容 | phase | 指标关联 |
|---|---|---|---|---|---|
| `LLM call · adk_agent` | ADK 模型轮次没有显式 stage；OSS 侧主要是**业务意图理解**（`AdkIntentExtractor`，审批 1 的意图卡） | 把 operator 原文读成 `BusinessIntent`（operationType / region / sites / windows / goal / backgroundEvent …），**只用于展示与审批，不生成 Task-T** | 请求=operator 原文；响应=结构化意图 JSON | `execution`（ADK 默认） | token 计入 AX/AZ；`run_metrics` 归到 `byComponent["client:execution"]` |
| `LLM call · task_t_slot_extraction` | `client_flow` 在 `trace_stage("task_t_slot_extraction")` 下调用 SDK `generate_task_prompt_from_text` | 初次 **Create** 的 Task-T 生成：从 operator 原文抽取 slot 并渲染 Task-T 模板；SDK 内部可能多次调用模型，每次一条 generation，全部同名 | 请求=原文+会话上下文；响应=Task-T Markdown（含 Task Context / Terminology / Expected Output） | `task_prompt` | 决定 X 列；token 是 A2A-T 的主要固定开销；`client:task_t_slot_extraction` |
| `LLM call · task_t_modify_extraction` | 同上，但 `run_mode=modify`（`client_flow` 的 modify 分支） | **Modify** run 的 Task-T 生成：把修改要求渲染成续传目标 Task 的新 Task-T；不新建 Task | 请求=修改原文+上下文；响应=新 Task-T | `task_modify` | 同 X 列；`client:task_t_modify_extraction` |
| `LLM call · structured` | 没有 stage 的 SDK 结构化调用兜底；OSS 侧当前主要是**发送前内容校验** `validate_task_prompt_and_data_filling`（`_validate_task_prompt`，未包 `trace_stage`） | 对已生成的 Task-T 做语义校验，返回拦截/可协商结论、缺失项与结构化参数；不生成 Task-T | 请求=Task-T+schema；响应=校验 JSON（verdict / errors / filled） | `negotiation`（SDK 默认） | 决定 Y 列；token 计入 AX/AZ，但 `byComponent` 会落到 `client:negotiation`（因为没有 stage） |

同链路还会出现的名字（不在上面四个里，但统计时要认得）：

| 观测名 | 作用 |
|---|---|
| `client_decision_agent` | ADK 协商决策：读 propose，产出 accept / counter / reject / abort |
| `client_manual_answer` | 人工答复的结构化预览（manual/confirm 模式） |
| `task_t_slot_extraction_refill` | accept 后按协商结果重新填充 Task-T |
| `negotiation_accept_extraction` / `negotiation_propose_extraction` / `negotiation_reject_extraction` / `negotiation_abort_extraction` | 用 SDK 把决策构造成 Negotiation-T 报文（AR/AS 前的模型调用） |

要点：

- `adk_agent` 与 `structured` 都是**兜底名**：前者表示"ADK 调用没被 stage 标注"，后者表示
  "SDK structured 调用没被 stage 标注"，并不代表一种业务动作。看阶段和请求内容才能判断。
- `structured` 可能一次 run 出现多条（SDK 校验内部的多步调用），且目前没有独立 stage，
  所以 `run_metrics.tokens.byComponent` 里校验开销和协商阶段混在一起。若要把校验 token
  单独统计，应给 `_validate_task_prompt` 包一个显式 stage（例如 `task_t_validation`）。
- 这四类调用都属于 **client / OSS 侧**；`Planner turn`、`LLM call · task_t_server_validation`
  等属于 RAN Agent 侧，比较 A2A 与 A2A-T 时要按 `bySide` 分开。

## 3. 与 OSP 指标列的对应

| 列 | 指标 | 数据来源（phase 级） | 计算 |
|---|---|---|---|
| AK | 原始意图起始 | root span 起点；精确值在 `source.receivedAt`（tester metadata） | 起点 |
| AL | OSS 调 SDK | `task_prompt_rendered.startedAt` | 起点 |
| AM | OSS 下发请求 | 首条 `message_sent`（对应 hop）的 `startedAt` | 起点 |
| AN | 任务创建/修改成功 | `task_created.startedAt` | 起点 |
| AO/AP | 协商发起/通知用户 | 每轮 `propose.startedAt` | 起点 |
| AQ | OSS 用户答复 | 每轮 `Decision · …` 的 `startedAt`（人工模式已回溯到提交时刻） | 起点 |
| AR | 调 SDK 构造协商回复 | `negotiation_*_extraction` 的 `llm.startedAt` | 起点 |
| AS | 下发协商回复 | 对应 `message_sent` 的 `startedAt` | 起点 |
| AT | 模型发起协商 | `negotiation_started.startedAt`（本地服务端） | 起点 |
| AU | 端到端时延 | AN − AK | 由上面两列相减 |
| AV | 协商时延 | AQ − AT（多轮取和） | 相减 |
| AW | 协商轮次 | trace metadata `rounds`；或 `max(round)` | 最大轮次 |
| AX/AY/AZ | 输入/缓存/输出 token | 所有 `generation` 的 `usage_details.input/input_cached/output` | 求和 |
| BA | 总 token | AX + AZ 或 `usage_details.total` | 求和 |
| BB | 是否协商 | trace metadata `negotiated`（或 `rounds > 0`） | 布尔 |
| U | traceId | Langfuse trace id = `run.traceId` | 直接取 |

token 求和时按 metadata `side` 区分 `client` / `server`：A2A 与 A2A-T 的对比应分别给出
OSS 侧与 RAN 侧，避免把 Task-T 固定开销和 planner 开销混在一起。

## 4. 与其他指标层的结合

### 4.1 单 run 指标（`protocol/run_metrics.py`）

run 结束后（`metrics_grace_seconds` 等待服务端 trace 批次）生成一份 JSON：

- `tokens`：`prompt/completion/total/cached`、`cacheHitRate`、`llmCalls`、
  `bySide`/`byComponent`（client/server + stage）。
- `latency`：`totalMs`（startedAt→finishedAt）、`modelMs`（llm duration 求和）、
  `toolMs`、`approvalWaitMs`（approval requested/resolved 配对）、`otherMs`。
- `negotiation`：`rounds`、`messages`、`observedSequence`、`outcome`。
- `accuracy`：终态、轮次、协商目标、报告存在性；A2A-T 还比对 performative 序列。

落库：`runs/client-state.sqlite3::run_metrics` + `runs/metrics/run-metrics.jsonl`。
查询：`GET /api/runs/{runId}/metrics`、`/api/metrics/runs`、`/api/metrics/summary`、
`/api/metrics/export.csv`，以及 `/comparison` 页面。

**与 span 的关系**：run_metrics 完全由 span 推导，因此它和 Langfuse 应该同源一致；差异通常
意味着某个 span 没进内存（例如服务端 trace 批次晚于 grace 窗口）。

### 4.2 聚合指标（`protocol/metrics_summary.py`）

按 `protocol` / `scenario` 分组：`runs`、`passed`、`successRate`、`accuracy`、token 均值/
总和、`cacheHitRate`、时延均值/p95、`avgRounds`。OSP 回填时按 `caseId` 另行分组即可。

### 4.3 Prometheus（`shared/metrics.py`，各进程 `/metrics`）

| 指标 | 含义 |
|---|---|
| `a2at_client_runs_total{status,final_state}` | run 终态计数 |
| `a2at_client_runs_in_flight` | 当前运行数 |
| `a2at_client_run_duration_seconds` | run 总时长 summary |
| `a2at_negotiation_rounds_total{role,round}` | 协商消息计数 |
| `a2at_server_tasks_total{state}` | 服务端 Task 终态计数 |

Prometheus 适合做实时告警和趋势；OSP 的逐用例回填仍以 run_metrics / Langfuse 为准。

### 4.4 Langfuse trace 级 metadata

root span 上写入（`shared/langfuse_tracing.py::_root_attributes`）：

`runId`、`scenario`、`chainId`、`parentRunId`、`language`、`mode`、`protocol`、`transport`、
`discovery`、`intentMode`、`modelMode`、`modelLabel`、`runMode`、`sourceKind`、`caseId`、
`caseName`、`batchId`、`receivedAt`、`userRequest`；`phase`/`minimal` 在 `finish()` 时再写
`rounds`、`negotiated`。`session.id = chainId`（无纠错链时等于 conversationId），
`user.id = userId`。

因此 Langfuse 里可以：按 `protocol` / `caseId` / `modelMode` 过滤 → 对 generation 的
`usage_details` 求和得到 AX/AY/AZ/BA；按 `rounds`/`negotiated` 出 AW/BB；用
`Propose`/`Decision`/`task_created` 的 observation 时间戳出 AO/AQ/AN。

## 5. 端到端统计的三种用法

### 5.1 单次 OSP 回填（推荐）

1. tester 启动 run，写入 `caseId/batchId/receivedAt`；
2. run 结束读 `run_metrics`（本地，完整 token/accuracy）或 Langfuse trace；
3. 按 §3 填 AK–BB；U 列填本次 attempt 的 `run.traceId`；
4. A2A 与 A2A-T 用同一 `caseId` 对齐比较；
5. 被拦截后的修正重试每次 attempt 单独一行、单独 U 列，用 `chainId` 归组、`parentRunId`
   表示先后，不要把多次 attempt 合并成一个 run 的时延/token。

### 5.2 Langfuse 批量看板

- 过滤：`metadata.protocol`、`metadata.caseId`、`metadata.modelMode`、`metadata.sourceKind`；
- 指标：generation 用量求和、observation 时延、`rounds`/`negotiated`；
- 局限：offline（脚本）run 不导出；`minimal` 级别没有时间点载体。

### 5.3 本地批量统计

```bash
# 每次 run 结束已追加
runs/metrics/run-metrics.jsonl
# 或在服务运行时
curl localhost:8080/api/metrics/summary
curl localhost:8080/api/metrics/export.csv
```

`summarize_metrics()`/`summarize_protocols()` 可直接对 JSONL 复算，不依赖 UI。

## 6. 已知近似与边界

- **AL/AR** 是 SDK/LLM 调用起点；SDK 内部的预处理不计入。
- **AQ** 只在 manual/confirm 的 UI 提交时有精确值；tester 的脚本化 decision 没有人工时延。
- **AT** 只有本地 RAN Agent 会发 `negotiation_started`；真机 a2a-t-server 需要按
  `docs-local/osp-metrics-a2at-server-handover.md` 提供等价信号。
- **AN** 取首个 `TASK_STATE_SUBMITTED`；若厂商用别的成功信号，需要真机侧给出机器可读字段。
- **pure A2A** 没有逐轮 `propose`/`decision` span（标准 A2A 的 INPUT_REQUIRED 轮次），
  轮次以 `run_metrics.negotiation.rounds` 和 `message_sent` 为准。
- **token 双计**：`run_metrics.tokens` 同时包含 client 与 server，比较时必须按 `bySide`
  拆分；Langfuse 中按 observation metadata `side` 过滤。
- **offline** 运行 token 标记为 `scripted`，不导出 Langfuse；只用于确定性回归。
- **`transport`** 只在 `full` 出现，真机长任务的长轮询不会进入 dashboard。
- **纠错重试**：被拦截、未发出 Task-T 的 attempt 没有 AM/AN/协商列，AU/AX 等应按“有效
  attempt”统计；多次 attempt 用 `chainId` 归组、各自保留 traceId。
