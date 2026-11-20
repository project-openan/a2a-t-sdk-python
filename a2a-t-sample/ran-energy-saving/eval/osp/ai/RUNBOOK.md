---
name: osp-ai-eval
description: Run OSP energy-saving cases against a real NMS from an isolated AI instance, triage failures from artifacts, and produce a blue-zone-ready problem report. Use in the yellow zone for AI-driven OSP testing, or when asked to run/analyse eval/osp cases on a real machine.
---

# OSP AI 跑批 Runbook

你是黄区的"AI 测试执行者"。你的工作不是改代码，而是：**在独立实例上跑 OSP 用例、读证据、出报告**。
默认只读仓库；只有明确使用独立 worktree 且操作员同意时才改代码。

## 0. 硬规则

1. **并发 1。** 一个实例一次只跑一个批次；不要同时开两个 AI 会话。
2. **只用独立实例。** 不碰测试人员那套 `RUN_DIR` / `RUNS_DIR` / 端口；本 runbook 的 CLI 已强制隔离。
3. **不猜期望值。** 期望只来自 `eval/osp/cases/*.yaml`；不要用上一轮结果或模型输出当 oracle。
4. **不自动重跑。** 基础设施失败先报告，由操作员决定；避免在网管上重复建 Task。
5. **清理是交付的一部分。** 每轮结束必须确认：0 个"本实例创建的"非终态 Task；进程停止；端口释放。
6. **不改 `/tester`、不改 `eval/osp/cases`。** 临时探针走 `POST /api/tester/scratch`，结果留在 `runs-ai/`。
7. **报告必须自包含。** 蓝区读不到黄区 `runs-ai/`；关键证据要内联到报告里，并脱敏 token。

## 1. 前置检查

```bash
cd a2a-t-sample/ran-energy-saving
python -m eval.osp.ai profile show --json
```

确认：

- `errors` 为空，`portConflicts` 为空；
- `ports` 不是测试人员实例的端口（8080/8000/8001/8002/9000/9001）；
- `runsDir` 以 `runs-ai` 结尾，而不是 `runs`；
- 双协议时 `webhookPublicUrl` 与 `ossWebhookPublicUrl` 都已填写，且网管可达；
- `nms` 为 true，`A2AT_SAMPLE_NMS_*` 四项齐全（profile 里，不要写进报告）。

首次使用：

```bash
python -m eval.osp.ai profile init
# 编辑 ~/.config/a2at/osp-ai.env：填网管 token、模型端点、两个 public URL
```

## 2. 起实例

```bash
python -m eval.osp.ai instance up --json
```

- 启动前会做 env 清洗与端口预检；失败时看 `RUN_DIR/ai-launcher.log` 的尾部。
- 就绪标志：`/api/meta` 返回 200。
- 起实例后先跑一条 Create 冒烟，再放大：

```bash
python -m eval.osp.ai batch run --cases 1 --protocols a2a-t --stop-after working
```

## 3. 跑批

```bash
# 单条 Modify（会先跑配对的基线 Create）
python -m eval.osp.ai batch run --cases 34 --protocols a2a-t --stop-after working

# 双协议、按维度分片
python -m eval.osp.ai batch run --cases 1,20,22,23,24,28,31,40,43,44 --protocols a2a-t,a2a --stop-after working
```

- 默认 `decision_mode=live`、`approval=auto`；真机必须 live。
- 纯 A2A 不能跑 `intent/task_t/validate`；CLI 会在提交前拒绝。
- 看到 `UI 进程已重启，批次未执行完`：批次被取消，**重跑该批次**，不要当作用例失败。
- `batch run` 结束会自动写 `failures.jsonl` 和报告；`--no-report` 可跳过。

进度只看摘要变化，不要刷屏；一条用例 2–5 分钟是正常的。

## 4. 读证据

```bash
# 全文 wire（UI 只保留最后 200 条，这里没有截断）
python -m eval.osp.ai evidence wire <runId> --kind message:send --limit 3
python -m eval.osp.ai evidence wire <runId> --task <taskId> --grep COMPLETED

# transcript / trace / task snapshot（UI 停了也能读）
python -m eval.osp.ai evidence trace <runId>

# 网管上仍然活跃的 Task（权威来源）
python -m eval.osp.ai evidence tasks --json
```

顺序：**先看 `failures.jsonl` 的 `signals` 和 `firstDiff`，再决定要不要翻 wire。**
不要一上来读几千行报文。

## 5. 失败分类与归属

CLI 的 `signals` 只回答"哪一层先出问题"；你负责解释和归属。

| 类别 | 典型证据 | 归属倾向 |
|---|---|---|
| `sdk.slot` | `slot.*`，Task-T 未生成 | 我方 / 语料 |
| `sdk.validation` | `content.*`、`semantic_conflict`、`rule_violation` | 我方 |
| `client.gate` | 样例必填字段 gate（`_missing_required_fields`） | 我方 |
| `runtime.send` | "run 在发送前结束" | 我方 |
| `runtime.negotiation` | 轮次不符、脚本决策耗尽 | 我方 / 网管 |
| `runtime.baseline` | Modify 基线未到可续传状态 | 我方 |
| `runtime.task` | 已发送但 Task 不推进 / 超时 | 网管 / 我方 |
| `nms.protocol` | 4xx/5xx、报文形状被拒 | 网管 |
| `infra` | 连接、超时、进程、端口 | 网络 / 环境 |

规则：

- 归"网管/网络"必须附 wire 证据位置和帧内容；没有证据就写"待确认"。
- 区分"本地观察窗结束"和"网管任务真的结束"：`waiting` 不是 `failed`。
- `validationCode` 同时可能是 SDK 校验和样例 gate；分不清时写 `origin: unknown`，不要臆造。
- 同一问题在 A2A-T 与纯 A2A 表现不同时，分开写：A2A-T 直连网管；纯 A2A 是 UI → OSS → 网管，
  `wire.jsonl` 与 `wire-oss.jsonl` 是两跳。

## 6. 报告

`batch run` 或 `report` 会生成 `docs-local/osp-ai/<date>-<batchId>.md`：

- 第 0/3/4/5 节标了 `<!-- AI 填 -->`，只填这些；
- 第 1/2/6 节是机器事实，**不要改**；
- 每条失败必须写：现象 → 根因 → 归属 → 置信度 → 下一步探针；
- "需要蓝区跟改"给到 `文件:行` 和验收用例；
- 不确定的写"待现场确认"，不要用"应该""可能已修复"。

报告是给蓝区的**唯一交付物**。写完后自检：一个没进过黄区的人，只看报告能否复现判断？
不能就补证据。

## 7. 收尾

```bash
python -m eval.osp.ai instance down --json
```

确认返回：

- `tasks`: 本实例创建的 Task 已 cancel，且复查到终态；
- `unknownActive`: 不是本实例创建的活跃 Task，**列出但不取消**（那是测试人员的）；
- `stopped`: launcher 已停；
- `portsClosed`: 五个端口都释放；
- `errors`: 为空。非空就把原文贴进报告第 6 节。

不要重发 cancel：第一次已成功，重发会拿到空 500，而网管列表有 60s+ 延迟。

## 8. 什么情况下必须中止并报告

- 网管连续 5xx 或 token 获取失败；
- 当前活跃 Task 无法清理，且可能影响测试人员；
- 模型端点不可用（live 决策无法进行）；
- 端口已被非本实例进程占用；
- 报告所需的 wire/transcript 缺失，且无法从 `RUNS_DIR` 恢复。

中止时保留 `runs-ai/`、`RUN_DIR`、报告草稿，写清楚停在哪一步。

## 9. 命令速查

```bash
python -m eval.osp.ai profile init|show
python -m eval.osp.ai instance up|status|down
python -m eval.osp.ai batch run --cases ... --protocols ... --stop-after ...
python -m eval.osp.ai batch wait <batchId>
python -m eval.osp.ai triage <batchId> [--batch-file ...]
python -m eval.osp.ai report <batchId> [--out ...]
python -m eval.osp.ai evidence wire|trace|tasks
python -m eval.osp.ai tasks cancel <taskId>...
python -m eval.osp.ai install-skill
```
