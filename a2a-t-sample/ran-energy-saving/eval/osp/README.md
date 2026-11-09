# OSP 无线节能评测用例

来源：`OSP-Unified Evaluation Set_v.01.xlsx` 的前两个 sheet（`A2A_原始数据`、`A2A-T_原始数据`）。
一条用例一个文件，共 **51** 条；A2A 与 A2A-T 的预期放在同一个文件里。

本目录是**运行用例集**，不是原表导出：原表里只用于跑测后回填的观测列（实际转换、实际校验、
时间点、token 等）不在这里，跑测结果另行记录。

## 1. 目录

```
eval/osp/
├── README.md        # 本文件
├── index.yaml       # 51 条用例的目录索引（选题、速查用）
└── cases/
    ├── 01-速率保障目标-视频码率720p-[不拦截]-[Create].yaml
    └── ...          # 共 51 个
```

## 2. 文件名

```
<id>-<用例名>-[<预期校验>]-[<操作>].yaml
```

- `id`：原表编号，1–51，两位补零，保证目录排序。
- `<用例名>`：原表用例名，保留中文；`： / \ : * ? " < > |`、换行、连续空白等替换为 `-`，
  折叠重复的 `-`，最长 50 字。
- `<预期校验>`：`拦截` / `不拦截` / `不拦截+术语` / `未填写`。
- `<操作>`：`Create` / `Modify`。

## 3. Schema

```yaml
schema: osp-eval-case/v1
id: 1
name: 速率保障目标：视频码率720p
dimension: D1 意图完备无歧义
operation: Create
tags: [rate-guarantee, negotiation-a2a]
user_input: |
  ...
expect:
  intent:
    "Operation type": Create
    "Energy saving cell RAT": ALL
    # ... 共 11 个键，见第 5 节
  protocols:
    a2a-t:
      validate: pass                 # blocked | pass | pass_with_terminology | null
      validate_label: 不拦截
      send: true                     # 是否预期把请求发出去
      task_t: |                      # 原表 H 列：预期 Task-T 报文全文
        ## Operation Type
        ...
      negotiation:
        expected: false
        expected_rounds: 0
        kinds: []
        rounds: []
      final_state: task_completed    # 由 validate 派生
      final_state_derived: true
    a2a:
      negotiation:
        expected: true
        expected_rounds: 1
        kinds: null                  # 原表未给类型，不臆造
        rounds:
          - "Set the guaranteed throughput targets: 00:00-22:00→2Mbps."
      final_state: null              # 原表未给，不判 pass/fail
assert:
  intent: true                       # 11 键意图是否参与判定
  a2a-t:
    validate: true
    task_t: true
    send: true
    negotiation: true
    final_state: true
  a2a:
    negotiation: true
    final_state: false
blocked_followup:                    # 仅 a2a-t 且 validate: blocked 的用例
  input: |-                          # A2A-T 拦截后替换/补充的请求原文
    Implement energy-saving in A2ATestRegion and set the guaranteed throughput targets: ...
  expect: {}                         # 可选：局部覆盖补充后阶段的期望
  assert: {}                         # 可选：局部覆盖补充后阶段的断言开关
runner:
  scenario: night                    # 仅作运行时配置，不提供预期
  language: en-US
  approval: approve
  timeout_seconds: 300
  pre_send_validation: always
source:
  file: OSP-Unified Evaluation Set_v.01.xlsx
  sheet: A2A-T_原始数据
  row: 2
known_issue: null
```

### 3.1 `expect.intent`

A2A 与 A2A-T 的预期结构化意图在这 51 条里完全相同，因此只存一份。11 个键：

1. `Operation type`
2. `Energy saving cell RAT`
3. `Energy saving aggressiveness`
4. `Energy saving region`
5. `Whether Experience-Preserving Energy Saving is Required`
6. `Special Requirements for Carrier Shutdown Frequencies`
7. `Energy saving time`
8. `Frequencies of carriers that can be shut down`
9. `Gain target`
10. `Guaranteed throughput target by zone`
11. `Scenario-based energy saving`

`Guaranteed throughput target by zone` 与 `Scenario-based energy saving` 是嵌套数组，比较时要
**展开后按时段逐条比**，不能整串比。

### 3.2 `task_t`

原表 H 列全文，固定 8 段：`Operation Type` / `Task Type` / `Task Description` / `Task Object` /
`Task Target` / `Task Context` / `Terminology Explanation` / `Expected Output`。

### 3.3 `assert`

决定某个字段只记录还是参与 pass/fail。默认全开；只有原表本身矛盾的用例才关掉对应项，
见第 6 节。

### 3.4 `blocked_followup`（A2A-T 拦截后补充信息）

只有 `expect.protocols.a2a-t.validate: blocked` 的用例可以带这个字段。`input` 是拦截后由操作员
补充/替换的请求原文；用例现有的 `expect.intent`、`task_t`、`negotiation` 描述的就是这条请求，
不需要再写一份完整期望。

`/tester` 对这类用例跑两段：

| 段 | 输入 | 判定 |
|---|---|---|
| 拦截段 | 原 `user_input` | 只跑 SDK 三阶段；`intent` / `task_t` 记为 `recorded`（期望值属于补充请求），`validate: blocked` 参与判定；**不启动 runtime run，不下发请求** |
| 补充段 | `blocked_followup.input` | 跑完整管线；`validate: pass`、`send: true`、`final_state: task_completed` 由拦截态机械派生，其余期望继承用例 |

补充段的 `expect`/`assert` 可用同名子块局部覆盖（例如补充请求触发一轮可行性协商）。拦截段结果
写在 item 的 `interception`（持久化 JSON 同名键）；结果表增加「拦截校验」「拦截后补充信息」两列，
阶段差异表用 `拦截/validate` 等前缀区分两段。

数据来源是重构版工作簿 K 列（`无线节能场景用例_重构版_A2ATestRegion`）。用例文件仍是生成产物，
生成器尚未支持该列：重跑 `generate_osp_cases.py` 会丢掉字段，`--check` 会报漂移。合并脚本见
`docs-local/merge-osp-blocked-followup.py`（内部，不入库）。

### 3.5 `baseline`（Modify 的配对创建）

每条 `operation: Modify` 的用例都带一个 `baseline.request`：一条 Create 请求，用来建出被修改的 Task。
它是运行期 fixture，不产生额外的 OSP 期望；`request` 只负责建出一个可续传的 Task。

```yaml
baseline:
  # Paired Create: the manual baseline the testers use (A2ATestRegion; 00:00-07:00 2Mbps,
  # 07:00-18:00 10Mbps, 18:00-24:00 20Mbps). ...
  request: |-
    Create an energy saving task for A2ATestRegion: guarantee 2Mbps from 00:00 to 07:00, 10Mbps
    from 07:00 to 18:00 and 20Mbps from 18:00 to 24:00.
```

基线是测试人员手工验收时使用的 Create Task-T（`A2ATestRegion` + 三段日计划），已确认不拦截、
不协商；不是按用例自由发挥的“前一天计划”。配对规则：

1. 地点必须显式写成 `A2ATestRegion`。写成 `Region A` 或其他区域会信息协商或校验不通过。
2. 计划保持手工基线 `00:00-07:00 2Mbps / 07:00-18:00 10Mbps / 18:00-24:00 20Mbps`；**不要**
   按用例站点或停电目标另造“10Mbps 整天”之类的计划：高费率 × 长窗口会被网管在基线 Create
   阶段判为不可行并要求降到 2Mbps，基线任务根本建不出来，Modify 也就跑不到。
3. **不含**停电/背景事件——停电是 Modify 引入的新事件。
4. 时间窗用散文写（`from 00:00 to 07:00`），不要写 `{HH:MM~HH:MM,NMbps}`：scripted provider 会把
   显式窗口改写成 Task Context，随后语义校验失败，基线判 blocked。
5. offline/scripted 模式下基线的 Task-T 由 `night` 场景的固定计划渲染，请求文本里的站点/速率只在
   live 模式生效；这不影响建 Task 和续传，但看 wire 时不要按请求文本逐字核对。
6. nms-sim 回放仍建议 `A2AT_SAMPLE_NMS_SIM_WINDOW_POLICY=any`：OSP 的 Modify 窗口和手工基线
   的第一段都不是 demo 的 `00:00~06:00` 窗口。

`/tester` 优先用用例自己的 `baseline.request`；没有该字段时回退到共享 fixture（`_baseline_request`）。
生成器尚未支持该字段，手工加入后 `--check` 会报漂移（与 `blocked_followup` 相同）。

## 4. 阶段模型与 `stop_after`

固定阶段：

| 阶段 | 内容 | 是否发送到网管 |
|---|---|---|
| `intent` | 从 `user_input` 解析结构化意图 | 否 |
| `task_t` | 生成预期 Task-T 报文（仅 A2A-T） | 否 |
| `validate` | SDK 内容校验（仅 A2A-T） | 否 |
| `send` | 发送请求 | 是 |
| `negotiate` | 协商轮次 | 是（多轮） |
| `final` | 最终状态 | — |

runner 用 `stop_after: intent | task_t | validate | send | negotiate | final` 截断。
前三个阶段可以只跑 SDK 管线、完全不碰 NMS，适合快速回归；后三个阶段才需要完整运行时。

**"不协商"也是断言**：`negotiation.expected: false` + `expected_rounds: 0`，不是跳过阶段。
A2A-T 只有 6 条需要协商（32/34/35/36/37/38），其余 45 条断言 0 轮；A2A 的 51 条全部有协商答复。

## 5. 判定规则

### 5.1 意图

11 个键逐字段比；数组按元素展开后比。

### 5.2 Task-T

| 段 | 判定 |
|---|---|
| `Operation Type` / `Task Type` / `Task Object` / `Task Target` | 精确比 |
| `Task Context` | 规范化后比（空白、全/半角标点、时间与速率写法） |
| `Task Description` / `Terminology Explanation` / `Expected Output` | 仅记录，不逐字比（固定模板） |

### 5.3 校验

| `validate` | 含义 | `send` | `final_state` |
|---|---|---|---|
| `blocked` | 拦截，不发送 | `false` | `input_invalid` |
| `pass` | 不拦截 | `true` | `task_completed`（派生） |
| `pass_with_terminology` | 不拦截，携带术语上下文 | `true` | `task_completed`（派生） |
| `null` | 原表未填写 | `null` | `null` |

`pass_with_terminology` 目前只能硬断言"未拦截"；"术语上下文"没有独立的离散可观测点，
先作为软预期记录。等有探针后再收紧。

### 5.4 协商

- **A2A**：51 条全部有 P 列协商答复，`expected_rounds = len(rounds)`（1–3）；协商类型原表未给，
  `kinds: null`，不判定类型。
- **A2A-T**：仅 6 条有 Q 列协商答复，各 1 轮；`kinds` 由答复里的标题派生：
  `Feasibility Negotiation Result` → `feasibility`，`Target Negotiation Result` → `target`。
  6 条当前都只有 `Feasibility Negotiation Result`，所以 `kinds: [feasibility]`。解析器仍支持一条
  答复里同时出现 Target 与 Feasibility，并按出现顺序生成决策；id 34 的早期版本就是这种形态，已刷新，
  不要再按 `[target, feasibility]` 理解。

### 5.5 最终状态

只有 A2A-T 侧由 `validate` 机械派生（`blocked` → `input_invalid`，其余 → `task_completed`），
并标 `final_state_derived: true`。A2A 侧原表没有给最终状态，不臆造，`final_state: null`，
`assert.a2a.final_state: false`。

## 6. 原表冲突与 `assert` 开关

| id | 情况 | 处理 |
|---|---|---|
| 40 | 输入无区域，I 列预期拦截；J/H 却填了 `A2ATestRegion` | `assert.intent: false`、`assert.a2a-t.task_t: false`；validate 仍判拦截 |
| 46 | 输入 `green energy-saving mode`，I 列预期拦截；J/H 写成 `Aggressive` | 同上 |
| 49–51 | I 列"预期SDK校验结果"为空 | `assert.a2a-t.validate/send/final_state: false`；意图与 Task-T 仍判定 |
| 7 | H 列把 gain target 放在 Task Context、Task Target 留空；slot 约束要求定量 gain target 放在 Task Target | 保留断言，`known_issue` 说明差异，待业务确认 |
| 20 / 21 / 28 / 34 | A2A 与 A2A-T 的预期行为差异，见 `known_issue`；34 另有 H/J 时段不一致 | 只加说明，不关断言 |

`tags: [needs-confirmation]` 目前只在 34（H/J 时段不一致）和 40/46（原表预期与输入矛盾）上。业务
确认后去掉标签；40/46 再把对应 `assert` 打开。7 与 20/21/28 只保留 `known_issue` 说明。

## 7. 重新生成

源 JSON 在 `docs-local/osp-eval-cases/`（内部目录，不入库）。修改源数据后：

```bash
python docs-local/osp-eval-cases/generate_osp_cases.py
python docs-local/osp-eval-cases/generate_osp_cases.py --check
```

`--check` 会逐文件比较生成的 YAML 与已提交文件，有漂移时返回非零。

## 8. 离线 runner

`eval/osp/runner.py` 只跑 SDK 拥有的三个阶段，不启动 Web UI、不发送到网管：

```bash
PYTHONPATH=. ../../.venv/Scripts/python.exe -m eval.osp.runner --all
PYTHONPATH=. ../../.venv/Scripts/python.exe -m eval.osp.runner --ids 1,40,46,49 --verbose
PYTHONPATH=. ../../.venv/Scripts/python.exe -m eval.osp.runner --ids 1 --stop-after intent --model-mode live
```

| 阶段 | runner 做什么 |
|---|---|
| `intent` | 生成 Task-T，再用 `OSP_INTENT_SCHEMA` 提取 11 键意图，与 `expect.intent` 逐字段比 |
| `task_t` | 分段比实际 Task-T 与 `expect.protocols.a2a-t.task_t`；Operation Type / Task Type / Task Object / Task Target 精确比，Task Context 按速率/时段/站点/RAT/模式做结构化比较，其余仅记录 |
| `validate` | 调用样例的 Task-T 内容校验，映射为 `blocked` / `pass`；`slot.*` 生成失败视为拦截 |

`send / negotiate / final` 需要完整运行时，不在这个 runner 内。

**带 `blocked_followup` 的拦截用例跑两段**（与 `/tester` 共用 `eval/osp/followup.py` 的派生规则）：

| 段 | 输入 | 判定 | 报告位置 |
|---|---|---|---|
| 拦截段 | 原 `user_input` | 只判 `validate: blocked`；intent/task_t 标 `recorded` | `interception.stages` |
| 补充段 | `blocked_followup.input` | 判 intent / task_t / validate（`validate: pass` 机械派生），`blocked_followup.expect/assert` 可局部覆盖 | `stages` |

用例 pass = 拦截段与补充段都通过。**不要把原请求的 intent/task_t 与补充请求的期望比较**：期望描述的是补充请求，原请求本来就缺这些值。

- `--model-mode live` 用 sample `.env` 里的真实模型；`offline` 用 demo 的脚本模型。脚本模型不支持 11-key schema，提取结果会为空，只适合链路自检。
- `--out` 写 JSON 报告；默认写到 `runs/osp-eval/<timestamp>.json`。

### 8.1 判定细节

- `assert.intent: false` 的用例（40/46）和 `assert.a2a-t.task_t: false` 的用例只记录，不参与 pass/fail；如果 `blocked_followup.assert` 把开关打开，则补充段判定。
- `assert.a2a-t.validate: false` 的用例（49–51）只记录。
- 6 条协商用例（32/34/35/36/37/38）的 `expect.intent` 是协商后的最终意图；离线 runner 只看到初始请求，所以 `intent` 阶段标 `recorded`，不判失败。这些用例的 `task_t` / `validate` 期望也可能包含协商后才产生的值（例如 37 的 `10Mbps`），离线跑出现差异时先对照 `known_issue` 判断是初始转换问题还是协商终态差异。
- `slot.*` 生成失败（缺区域、缺速率、非法制式等）发生在 Task-T 之前；如果该用例预期 `validate: blocked`，runner 把 validate 判为通过，intent/task_t 标 `recorded`。补充段若仍发生 `slot.*`，说明补充请求仍不完整，按 error/failed 记录。
- Task Context 是自由文本，runner 先抽取速率、时段、站点、RAT、模式再比较；两边都只有时段时才退回分词比较。有疑问时看报告里的 `expected` / `actual` 原文。

## 9. 评测控制台 `/tester`

`/tester` 已改为 **catalog 驱动**，不再导入 Excel：

- 左侧列出 `eval/osp/cases` 的 51 条用例，可按 id/名称、维度、操作、校验结果、标签筛选，勾选后批量执行。
- 一条用例 × 一个协议 = 一个运行项；协议可选 A2A-T / A2A。
- 运行配置：`执行到`（intent / task_t / validate / send / negotiate / final）、审批、超时。
- 前三阶段走 SDK-only runner；后三阶段走 `RunManager` 完整运行时。
- 协商脚本化：A2A-T 用 Q 列答复生成 `NegotiationDecision`，A2A 用 P 列答复作为 `INPUT_REQUIRED` 答案。
- 结果表按阶段显示 `passed / failed / recorded / error`，点“差异”看逐字段/逐段 expected/actual。
- 带 `blocked_followup` 的拦截用例先记录拦截段，再用补充请求继续跑；拦截段证据在抽屉里以
  `拦截/...` 展示，拦截请求不会被下发。
- 用例详情抽屉展示 `baseline.request`（Modify 的前置基线 Create；从结果行打开时同时显示本次实际
  基线的 state 与 wire 链接）和 `blocked_followup.input`（拦截后补充请求）。有补充请求时，
  `expect.intent` / `task_t` 标注为「（补充请求）」。
- 执行上下文不再需要导出才能看到：结果表有独立的 `RunId` 列；Modify 运行项分两行显示
  `基线` 和 `修改` 两个 run（各自链到 wire），普通用例只有一行。结果行在「结果 / 错误」下显示
  `task <短 id>`（悬停看完整 runId / traceId / taskId / contextId）；结果抽屉的「运行上下文」
  给出 `runId`（wire / trace 链接）、`traceId`、`taskId`、`contextId`。
- 基线进度在结果抽屉的「基线 Create（Modify 前置）」块实时显示（创建中 / 已受理 / 失败 +
  当前 Task state + runId / taskId / contextId），结果表的阶段列也会出现「基线」chip。
- 报告导出 JSON / XLSX；JSON 带完整 `baseline` 对象，XLSX「用例结果」新增「基线状态 / 基线 Run ID /
  基线 taskId / 基线 state / 基线错误」列，「仲裁格式」的备注会写明基线失败原因。Excel 只作为导出
  格式，不再是输入。
- 临时请求 tab：手输文本直接跑一次，不做预期断言。

### 9.1 Modify 的配对基线与限制

每条 Modify 用例都会先跑一条**配对的基线 Create**，拿到 `taskId` / `contextId` 后再续传：

- **配对规则**：每条 Modify 用例在 YAML 里带自己的 `baseline.request`——测试人员手工验收用的
  Create Task-T（`A2ATestRegion` + 三段日计划，不拦截、不协商），不含停电事件。不要按用例另造
  计划：高费率 × 长窗口会让网管在基线 Create 阶段就发起可行性协商，基线任务建不出来。规则和示例
  见第 3.5 节。没有 `baseline.request` 的用例回退到共享 fixture（`_baseline_request`），它同样
  使用 `A2ATestRegion` 和手工基线计划。
- **决策模式**：基线跟随批次的决策模式（`_mode_for`）。live 批次用在线决策代理（`MODE_AUTO`），
  网管对基线 Create 追问时由 ADK 回答；scripted 批次的基线仍是空 script，一旦触发协商就会以
  `The scripted decision sequence is exhausted` 明确失败，而不是静默继续。
- **受理门槛**：基线必须到达可续传状态才会发送 Modify。A2A-T 是 `SUBMITTED`（或已进入 `WORKING`）；纯
  A2A 是外层 Task 的 `WORKING`。`COMPLETED` 是终态，A2A 层会拒绝续传；`INPUT_REQUIRED` 会把 Modify 文本
  当成待答问题的答案，同样不算可续传。基线失败时结果里记录 `baseline`（run id、taskId、state、原请求、
  失败原因），不会退化成“随便挑一个 Task”。
- **协议差异**：A2A-T 的 Modify 直接续传厂商 Task；纯 A2A 的 Modify 是标准 A2A 续传，续的是 OSS Agent 的
  外层 Task（`message.taskId` / `contextId`），由 OSS 把原文转发给下游。纯 A2A 没有 `Task-T` 门禁，所有
  Modify 行都会真实发送，因此更依赖基线 Task 保持非终态。另外，OSS 只对**自己的 NMS 下游**持久化
  outer↔inner binding：纯 A2A Modify 需要 `--nms`（真实 NMS 或 `--nms-sim`），本地 RAN hop 没有可续传的
  下游 Task。

**本地 RAN Agent 会立即把任务置为终态**，随后服务端拒绝 Modify（`Task ... is in terminal state`）。要在本地
验证 20 条 Modify 用例，请用支持长任务的 NMS 模拟器（`--nms --nms-sim` +
`A2AT_SAMPLE_NMS_SIM_HOLD_WORKING=1` + `A2AT_SAMPLE_NMS_SIM_WINDOW_POLICY=any`），或直接连真实 NMS。

## 10. 已知限制

- **A2A 最终状态不判定**：原表只给意图与协商答复。
- **术语上下文是软断言**：没有独立可观测点。
- **SDK 校验与样例业务门分开**：样例 OSS 还有一层必填字段 gate（`_missing_required_fields`）。
  要判定"预期SDK校验结果"，`intent` / `task_t` / `validate` 三个阶段应只跑 SDK 管线，
  不要把样例 gate 的 `blocked` 当成 SDK 的 `blocked`。
- **控制台显式开启校验**：`/tester` 启动运行项时传 `validate_before_send=True`，不依赖
  `A2AT_SAMPLE_PRE_SEND_VALIDATION=on` 的 `presentation` 限定。
- **`scenario: night` 只是运行时配置**：它不提供任何预期值，预期全部来自本文件。
