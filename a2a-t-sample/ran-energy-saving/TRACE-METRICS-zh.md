# Trace 与指标（交付剖面）

## 1. Span 来源

| 产生方 | 模块 | 范围 |
|---|---|---|
| 客户端流程 | `protocol/client_flow.py` | 发送、协商、决策、Task 生命周期 |
| 客户端 agent | `shared/adk_tracing.py` | ADK 决策与意图轮次 |
| SDK 结构化调用 | `shared/traced_llm.py` | Task-T slot 抽取与内容校验 |
| 本地服务端 | `protocol/server_executor.py` | planner 轮次、工具调用、Task 状态 |
| gateway | `protocol/client_gateway.py` | 入站 Task 映射 |

每个 run 写入 `runs/<runId>/wire.jsonl`（A2A/NMS 原始报文）；本地服务端另写
`runs/<runId>/server-trace.jsonl`。服务端与 UI 分进程时，UI 通过 HTTP 接收 span。

## 2. 身份

四种身份不可混用：

- `runId`：客户端 run；
- 厂商 `traceId` / `taskId` / `contextId`：网管 Task；
- Langfuse trace id：导出的 trace；
- OTel span / parent span id：trace 树。

`traceparent` 逐跳传递。UI 按 run id 关联 span；厂商链路同时把 run id 放进关联头。

## 3. 协商阶段

A2A-T run 发布 `propose` / `accept` / `reject` / `abort` 决策及轮次。标准 A2A run 发布
`INPUT_REQUIRED` 问题与客户端答复。两者都由决策 agent 作答；performative 与轮次由确定性流程
控制。

## 4. 指标

`protocol/run_metrics.py` 为每个 run 生成一份文档：

- token 与调用次数，按 prompt / completion / cache hit 拆分；
- 时延拆分为模型、工具与其余编排时间；
- `negotiation`：轮次、报文数、观察到的序列、结果；
- `accuracy`：终态与报告是否存在。真机 run **不**比较演示录制序列与目标值，只保留终态与报告
  存在性作为 ground truth。

文档写入 `runs/client-state.sqlite3`，由 `/api/runs/<id>/metrics` 与 `/api/metrics/*` 暴露，
并由测试台导出 CSV/xlsx。协议对比页与 observe 控制台不在交付范围内。

## 5. Langfuse

OTLP 导出可选：配置 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` 时导出 run 的 span；未配置时
为空操作。导出失败只记日志，不影响协议结果。

## 6. 保留期

`runs/<runId>/` 文件是持久证据。SQLite 保存可查询状态与协商审计，不保存完整 prompt。终态 Task
的保留期由 `A2AT_SAMPLE_TASK_RETENTION_DAYS` 控制。
