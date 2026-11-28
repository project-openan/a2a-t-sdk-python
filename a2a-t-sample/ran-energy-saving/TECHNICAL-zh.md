# RAN 节能 —— 技术说明（交付剖面）

本文描述交付剖面：面向真机网管的 live、agent 驱动 A2A-T 客户端。此前描述 offline/scripted、
OSS 代理、MIE 审批以及内部观测/审核控制台的说明已不再适用。

## 1. 进程拓扑

| 进程 | 模块 | 默认端口 | 职责 |
|---|---|---:|---|
| Web UI | `demo.web.serve` | 8080 | presentation + `/tester` + `/status` |
| 入站 gateway | `protocol.client_gateway` | 8001 | A2A 调用方（Gemini Enterprise）→ 客户端 run |
| 本地 RAN 服务端 | `protocol.server_app` | 8000 | 进场前自测的参考服务端 |
| 回调接收 | UI/gateway 内部 | 9000 / 9001 | 网管推送 |

真机部署为 `UI（可选 gateway）→ 厂商网管`。gateway 是独立入站面，不代理 Web UI。

## 2. 协议链路

- **A2A-T**：Task-T + Negotiation-T 扩展。SDK 从运维原文抽取 slot 并生成 Task-T；`slot.*`
  失败在 Task-T 生成前拦截，客户端 agent 应答
  information / target / feasibility 提案。
- **标准 A2A**：普通消息 + `TASK_STATE_INPUT_REQUIRED` 续传。两条链路使用同一个决策 agent 和
  同一套 run/task 存储。
- pure hop 只支持**直连**：`UI → 网管` 或 `UI → 本地服务端`。已废弃的
  `UI → OSS Agent → downstream` 代理不在交付范围内。

## 3. 决策职责

1. **intent 层**（`demo/agents/intent_agent`）：仅用于展示与 approval 1，不重写请求、不生成
   Task-T 业务值。
2. **SDK slot 抽取**：Task-T 的唯一来源；抽取失败在 Task-T 生成前拦截，生成成功只记录为本地
   通过证据，不再做第二次内容校验。
3. **客户端决策 agent**（`demo/agents/client_agent`）：依据服务端提案、运维原文与冻结的协商
   原则，产生协商答复及 accept/reject/abort 决策。
4. **确定性规则**决定协议正确性：performative、轮次、Task 生命周期、终态与前置条件。模型不拥有
   这些判断。

产品中不存在 offline/scripted 决策 provider。单元测试使用 `tests/support/` 下的 test-only
double；验收使用 live 端点。

## 4. 身份与关联

- `runId`、`conversationId`、task/context id、`traceparent` 端到端传递。
- webhook 推送按 task/context id 归属；带未知身份的推送不会投给“唯一的 run”。
- 入站 gateway 一消息一 Task；调用方身份来自 A2A 认证上下文。
- gateway AgentCard 使用配置的公网 URL；配置 token 时声明 bearer 安全方案。

## 5. 长任务

`A2AT_SAMPLE_LONG_TASK_TRACKING=1` 使客户端在同步观察窗口结束后继续跟踪 Task。OSS hop 及其
外层 Task binding 已删除，客户端直接跟踪厂商 Task。真机 run 不再按演示录制序列评分，只比较
终态与报告是否存在。

## 6. 持久化

| 文件 | 内容 |
|---|---|
| `runs/client-state.sqlite3` | conversations、turns、runs、negotiation_events、task_snapshots、metrics、ADK sessions |
| `runs/task-status.sqlite3` | 本地服务端 Task |
| `runs/gateway-task-status.sqlite3` | 入站 gateway Task |
| `runs/<runId>/wire.jsonl` | 网管原始报文 |
| `runs/<runId>/server-trace.jsonl` | 本地服务端 span |

## 7. 可观测性

- `/status` 是只读运行状态页（批次、run、共享 SDK 池）。
- `/tester` 与 `/status` 跟随进程语言（`--language`，默认 `en-US`，修改需重启）。tester 批次
  记录创建语言，消息与导出保持该语言；不提供运行时切换。证据与协议错误保持原文。
- Langfuse OTLP 导出可选且 best-effort；未配置时为空操作。
- trace / Langfuse / metrics 失败不影响协议结果。

## 8. gateway 契约

- 输入：普通 A2A 消息，至少一个非空 text part；空文本直接拒绝。
- 一消息 = 一个入站 Task = 一个客户端 run；不支持同一 Task 续传。
- 下游协议：`a2a-t`（默认）或 `a2a`，由 `A2AT_SAMPLE_GATEWAY_DOWNSTREAM_PROTOCOL` 配置。
- 回调：`A2AT_SAMPLE_GATEWAY_WEBHOOK_PORT`，与 UI 接收端口分离。
- 公网 URL：`A2AT_SAMPLE_GATEWAY_PUBLIC_URL`；TLS 由客户反代终结。
- 认证：`A2AT_SAMPLE_AUTH_TOKEN`；设置后卡片声明 bearer 方案。

## 9. 测试

- 单元测试：`pytest tests -q`，使用 test-only 模型/SDK double。
- 验收：`/tester` 对真机网管执行，并覆盖八个业务场景。
- 静态检查：`ruff check shared protocol demo tests run_demo.py`。
- 样例不在 CI 中运行，本地全量套件是唯一自动化门禁。
