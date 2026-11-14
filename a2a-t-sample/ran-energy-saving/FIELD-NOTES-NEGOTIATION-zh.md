# 现场联调：未触发协商的取证与复测指引

> 适用场景：A2A-T + `--nms`（真机网管），`message:send` 已被受理（`TASK_STATE_SUBMITTED`），
> 但始终没有 `Negotiation-T` / `decision_requested`，run 停在 `incomplete` 或长期 `running`。
>
> 本文随代码入库，蓝区通过 git 下发给黄区。**A 组现在就能做，不需要蓝区新代码；
> B 组是蓝区修复后的验收，当前分支尚未实现，等蓝区通知后再跑。**
>
> 详细内部分析与原始数据不随本文入库。

---

## 0. 复测前先确认

| 项 | 记录 |
| --- | --- |
| 黄区 build | `git rev-parse HEAD` 的完整 hash |
| 是否包含以下提交 | `0f6cb82`（等待态 UI）、`7eb9414`（校验失败透出/重试）、`210a117`（`json_schema`） |
| 实例 / 端口 | 例：`A2AT_MODEL=qwen38`，UI `8084`，webhook `9004` |
| 场景 / 模式 / 协议 | 例：`outage`，`confirm`，A2A-T，`--nms` |
| 启动方式 | 必须 `./run_demo.py --stop` 后干净启动，不复用旧进程 |
| 时间基准 | 注明本机时区；所有时间戳说明是本地时间还是 NMS 的 `Z` 时间 |

> 如果 “任务创建时间” 与 `GET /tasks/{id}` 返回的 `timestamp` 不一致，先确认是 build、时区，
> 还是该 Task 实际由更早的 run 创建。

---

## 1. 先读：三条已知行为，避免误判

1. **客户端不会主动发起协商。** 在 `--nms` 模式下，客户端只发送 Task-T；`PROPOSE` 只能由
   网管在 `message:send` 的响应或后续推送里回。网管不回，就没有 `decision_requested`。
2. **本地判定 “negotiable” 不等于网管一定会协商。** 本地语义校验只决定 “请求仍然发送”，
   不向网管发出协商请求；是否协商由网管自己的校验决定。
3. **任务停在 `SUBMITTED` 不推进是已知的网管侧现象（A12）。** Create 和 Modify 都可能出现；
   它和 “没有协商” 是两个现象，不要合并成一个结论。

因此，一份可归因的报告必须同时回答：

- Task-T 原文是什么？
- 本地校验到底看到了什么？
- 网管 Task 的真实状态/时间戳是什么？
- 网管在 **Modify** 上到底会不会协商？（需要对照实验）

---

## 2. A 组：不改代码，现在就能做

### A1（最高优先）回传 outbound Task-T 原文

从 `runs/<runId>/wire.jsonl` 取 outbound `message:send` 的 metadata Task-T：

- 失败 run（例：`354d814b…`）
- 同一 Task 的上一条 run（例：`a4ad4189…`）
- 拿到过 `PROPOSE` 的对照 Task 的所属 run（例：`451ee200…`）

每条至少回传 `Task Context` 段落，最好整段 Task-T。重点看：

- 是否包含 `Time-segmented rate guarantee target: {08:00-18:00,10Mbps}`（或等价写法）；
- 是否只有 `Cell RAT: NR, LNR`；
- `Operation Type` 是 `Create` 还是 `Modify`。

> 生成 Task-T 和校验 Task-T 是两次独立的模型调用。校验错误里的 `actual` 是**校验模型**的读数，
> 不是 Task-T 原文。只有拿到原文才能区分 “槽位抽取丢了速率” 和 “校验模型误读”。

### A2 回传 `task_validation` 完整 detail

从事件流/trace 导出 `task_validation` span 的 `detail`：

- `verdict`、`reason`、`negotiationKind`、`attempts`
- `errors[]`：`slotName`、`code`、`facts`
- 校验模型抽取出的 `params`

重点记录 `params.guaranteedThroughputTarget` 的值：`null` / `"NR, LNR"` /
`"{08:00-18:00,10Mbps}"`。这决定业务门禁为什么放行。

### A3 回传网管 Task 文档的完整 JSON

```text
GET /a2a/json/tasks/<stuck-task-id>
GET /a2a/json/tasks/<healthy-task-id>     # 对照
```

不要只贴 `state` / `timestamp` 两个字段。需要确认：

- `state` 在顶层还是 `status.state`；
- `timestamp` / `updatedAt` 在顶层还是 `status` 下；
- `contextId`、`history`、`artifacts` 是否完整；
- `history` 里能否看到两次 Modify 的消息。

> 蓝区要把 “停滞判断” 做进发送前门禁，必须先知道真实文档里有没有可用的时间戳字段。

### A4 确认对照 Task 的来源

- 拿到过 `PROPOSE` 的 Task 是 **Create** 还是 **Modify**？
- 对应哪个 runId / 哪个 `contextId`？
- 它的 outbound Task-T 与失败 run 是否一致？

### A5（关键对照）Modify 一个健康的 `WORKING` Task

**步骤**

1. 准备一个确实处于 `TASK_STATE_WORKING` 的 Task：
   - 真机：用 Running-tasks picker 选 `WORKING`；或
   - 本地模拟器：`A2AT_SAMPLE_NMS_SIM_HOLD_WORKING=1` 创建一个会保持 `WORKING` 的 Task。
2. 记录 `targetTaskId` / `contextId` 和选中时间。
3. 发送与失败 run **完全相同**的不可行请求（例：10 Mbps + 08:00-18:00 停电）。
4. 记录：
   - `message:send` 的响应状态；
   - 是否出现 `Negotiation-T` / `negotiationContext` / `PROPOSE`；
   - Task 状态是否推进；
   - 网管 Task 的 `timestamp` 是否变化。

**判定**

| 结果 | 含义 |
| --- | --- |
| 回 `PROPOSE` / `INPUT_REQUIRED` | 网管支持 Modify 协商；问题在目标 Task 或门禁 |
| 只回 `SUBMITTED`、无 `PROPOSE`、状态不动 | 网管可能不在 Modify 上协商；需要先确认产品预期，不能只修客户端门禁 |

### A6（关键对照）Modify 一个“刚创建、还在 SUBMITTED”的 Task

1. 新建一个 Task，记录 `taskId` / `contextId` / 创建时间。
2. 在 **60 秒内**用同一条不可行请求做 Modify。
3. 记录：NMS 是否推进、是否协商、`timestamp` 是否变化。

> 用来区分 “SUBMITTED 天生不可 Modify” 和 “只有停滞很久的 SUBMITTED 有问题”。

### A7 停滞观测（只记录，不判断对错）

对一个长时间停在 `SUBMITTED` 的 Task，记录：

- `activity` 帧：`state`、`stateSince`、`pollCount`、`pollIntervalSeconds`、`detached`；
- 页面等待条文案与 elapsed；
- 2 分钟后是否出现 “任务已在 SUBMITTED 停留” 类提示；
- run 是否一直 `running`、是否永远不会结束；
- 时间线里 `task_poll` / `task_created` 的行文案。

---

## 3. B 组：蓝区修复后的验收（当前分支尚未实现）

> 以下为目标行为，不是当前行为。蓝区实现后会在本文更新状态；在此之前不要把它们当成缺陷上报。

### B1 Modify 目标状态门禁

- 卡在 `SUBMITTED` 超过阈值的 Task：**发送前拒绝**，文案包含停滞时长和下一步建议（取消/改选）。
- `WORKING` Task：允许 Modify。
- 终态 Task：拒绝。
- **所有入口一致**：展示页 `/api/runs`、tester console、直接 API、CLI 都经过同一道门，
  不能只在展示页路由里检查。

### B2 时间戳与停滞判断

- Task 列表 / 单 Task 读取返回可用的 `updatedAt` / `timestamp`；
- 模拟器返回该字段，离线用例可覆盖；
- 阈值可配置，且只对 `SUBMITTED` 生效，不按 `WORKING` 年龄误杀合法长任务。

### B3 “未回协商”的显式观测

- flow 以 `incomplete` 结束时，结果里带明确原因
  （例：`no Negotiation-T received; remote state TASK_STATE_SUBMITTED`）；
- 时间线 / UI 区分 “已请求网管协商，等待 PROPOSE” 和 “网管执行中”；
- 长时间无变化时给出可操作提示，而不是只显示 “NMS is executing”。

### B4 受理文案

- “Task modified / Task created” 改为反映真实语义（受理 / 状态推进）；
- 收到 `SUBMITTED` 快照时不暗示任务已成功修改。

### B5 回归

- 正常 Create → WORKING → Modify → COMPLETED 两阶段链路不退化；
- 离线模拟器 `--nms-sim` 的 Modify 用例通过；
- 全量样例测试通过（数量以 `pytest` 输出为准，不要照抄文档）。

---

## 4. 统一回传格式

每条结论按此格式，缺项写 “未采集”：

```text
- runId:
- 时间（本地 / NMS）:
- build commit:
- 实例 / 端口 / 模型:
- 场景 / 模式 / 协议:
- 操作步骤:
- 现象:
- 原始证据:
  - wire.jsonl 关键帧（out / in）
  - task_validation detail
  - GET /tasks/{id} 完整 JSON
  - activity 帧 / 页面截图
- 初判归属: 我方 / 网管 / 模型 / 网络
```

**不要只给结论句**（如 “网管不协商”“不是模型问题”）。没有原始证据的结论无法归因。

---

## 5. 归属初判（填表参考）

| 现象 | 初判归属 | 备注 |
| --- | --- | --- |
| 任务受理后停在 `SUBMITTED` 不推进 | 网管侧（已知 A12） | 仍需回传 Task JSON 证明时间戳未变 |
| 健康 `WORKING` Task 的 Modify 也不回 `PROPOSE` | 网管契约 / 产品预期 | 先做 A5，再决定是否改客户端 |
| Task-T 的 `Task Context` 缺速率 / 只有 RAT | 我方（槽位抽取） | 由 A1 证明 |
| 校验 `params` 非空但值明显错误，门禁仍放行 | 我方（校验 / 门禁） | 由 A2 证明 |
| 页面对 `SUBMITTED` 停滞显示 “NMS 执行中” | 我方（观测文案） | 不影响协议结果 |
| Modify 打在停滞 `SUBMITTED` 上仍被允许 | 我方（入口策略） | 待 B1 |

---

## 6. 本文的复用结构

后续蓝区给黄区下发复测任务，统一按以下结构写：

1. **复测前提**：build、实例、端口、模型、时区、干净启动；
2. **先读的已知行为**：避免把不同现象合并；
3. **A 组**：不改代码的取证 / 对照实验，每条写 “步骤 / 期望 / 必须回传的证据”；
4. **B 组**：修复后验收，每条写 “目标行为 / 入口 / 回归项”，并明确当前是否已实现；
5. **统一回传格式**：runId + 原始帧 + JSON + 截图；
6. **归属初判表**：把 “我方观感问题” 和 “网管不推进” 分开。
