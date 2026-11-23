# OSP 无线节能评测用例

来源：`OSP-Unified Evaluation Set_v.01.xlsx` 的前两个 sheet（`A2A_原始数据`、`A2A-T_原始数据`）。
一条用例一个文件，共 **51** 条；A2A 与 A2A-T 的预期放在同一个文件里。

本目录是**运行用例集**，不是原表导出：原表里只用于跑测后回填的观测列（实际转换、实际校验、
时间点、token 等）不在这里，跑测结果另行记录。

## 1. 目录

```
eval/osp/
├── README.md        # 本文件
├── index.yaml       # 100 条用例的目录索引（选题、速查用）
└── cases/
    ├── 01-速率保障目标-视频码率720p-[不拦截+术语]-[Create].yaml
    └── ...          # 共 100 个
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
client_conditions: |                 # 可选：本用例的客户端决策条件（不进 expect / assert）
  停电期间可以接受到 2Mbps；低于 2Mbps 不能接受。
expect:
  intent:
    "Operation type": Create
    "Energy saving cell RAT": ALL
    # ... 共 11 个键，见第 5 节
  protocols:
    a2a-t:
      validate: pass                 # blocked | pass | pass_with_terminology | null
      validate_label: 不拦截
      slot_errors:                   # 可选：拦截时报出的槽位错误（仅 blocked 用例，见 5.3）
        mode: any                    # any（默认，任一命中）| cover（观测到的每条都要被覆盖）
        expect:
          - code: slot.constraint_violated
            contains: [gain, 增益]    # 可选：命中任一关键词
      send: true                     # 是否预期把请求发出去
      task_t: |                      # 原表 H 列：预期 Task-T 报文全文
        ## Operation Type
        ...
      negotiation:
        expected: false
        expected_rounds: 0
        kinds: []
        rounds: []                   # 客户端答复的期望文本（Q 列）；agent 模式下不再驱动运行
        messages: []                 # 可选：服务端协商报文，按轮次给备选（见 5.4）
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

**`client_conditions`（可选）**：本用例特有的客户端接受边界或客户端已知事实，自然语言，与
`user_input` 同级。它只作为 client agent 的决策输入和 client 决策质量评委的期望来源，不进入
`expect`，不新增断言维度。不写时只用批次级通用协商原则（`negotiation-principles.yaml`）。
必须是字符串；加载用例时校验，写错类型直接报错。

### 3.1 `expect.intent`

A2A 与 A2A-T 的预期结构化意图在这 100 条里完全相同，因此只存一份。11 个键：

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

**全流程批次：一次 run，两段 gate。** A2A-T 的发送前校验属于每次 run 的必经阶段，补充请求是
同一次 run 内的修正输入，不是第二次请求：

| 段 | 输入 | 判定 |
|---|---|---|
| 拦截段（run 内） | 原 `user_input` | run 用原请求生成 Task-T 并校验；被拦截时不发送。`intent` / `task_t` 记为 `recorded`（期望值属于补充请求），`validate: blocked` 参与判定 |
| 补充段（同一次 run） | `blocked_followup.input` | gate 阻塞后由 `correction_provider` 注入；同一 run 重新抽取、再校验、继续发送。`validate: pass`、`send: true`、`final_state: task_completed` 由拦截态机械派生，其余期望继承用例 |

- run 的 `userRequest` 始终是**原请求**；实际下发的补充文本记 `dispatchedRequest`，同一条 trace 中
  以 `correction_applied` span 分隔两次尝试（两次 `task_prompt_rendered` / `task_validation`）。
- 补充段的 `expect`/`assert` 可用同名子块局部覆盖（例如补充请求触发一轮可行性协商）。拦截段结果
  写在 item 的 `interception`（持久化 JSON 同名键）；结果表增加「拦截校验」「拦截后补充信息」两列，
  阶段差异表用 `拦截/validate` 等前缀区分两段。
- 只有一次修正：补充请求再次被拦截即终止（`followupBlocked`），不会循环。

**补充段只在原请求被真正拦截后执行。** 判定是 run 自己 gate 的结论：

- 结论是 `blocked` → 同一 run 内注入修正并继续；
- 结论是 `pass` → **实际未拦截**：不注入修正，原请求直接下发。下游阶段只记录、不判定
  （`intent` / `task_t` / `send` / `negotiation` / `final_state` 的断言全部关闭，因为用例期望描述
  的是补充请求）；`item.interception.followupSkipped = true`、全流程批次另加
  `originalDispatched = true`，拦截记录 `validate` 记 `recorded`，`item.error` 保持为空；
- 结论是 `unavailable` 或没有结论（校验调不出来 / `stop_after` 停在 `intent`/`task_t`）→ **既不能
  证明拦截、也不能证明放过**，按现有两段行为记录：拦截段因与用例期望不符判红。

**SDK-only 批次**（`stop_after ∈ intent/task_t/validate`）不启动 run，仍由离线 runner 执行两段；
它的客户端由 `/tester` 的 `--env-file`（`env_path`）构建，与全流程 run 使用的是同一份 SDK 配置。
全流程批次不再有独立预检，gate 与下发天然同配置，2026-11-17 那种“预检与 run 模型不一致”的偏差
不再可能发生。

`/review` 与 tester 一致：未拦截时 `validate` 记 `recorded`，已按原请求下发时其余维度记
`recorded`，未启动 run 时记 `not_applicable`（用 `missing` 会读成证据缺失），整个用例判 `pass`。

`/annotate` 只展示补充请求原文，不执行它。

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
4. 时间窗用散文写（`from 00:00 to 07:00`），不要写 `{HH:MM-HH:MM,NMbps}`：scripted provider 会把
   显式窗口改写成 Task Context，随后语义校验失败，基线判 blocked。
5. offline/scripted 模式下基线的 Task-T 由 `night` 场景的固定计划渲染，请求文本里的站点/速率只在
   live 模式生效；这不影响建 Task 和续传，但看 wire 时不要按请求文本逐字核对。
6. nms-sim 回放仍建议 `A2AT_SAMPLE_NMS_SIM_WINDOW_POLICY=any`：OSP 的 Modify 窗口和手工基线
   的第一段都不是 demo 的 `00:00-06:00` 窗口。

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
A2A-T 只有 10 条需要协商（26/28/29/30/31/32/50/51/88/89），其余 90 条断言 0 轮；A2A 有 89 条协商答复，11 条（45–49/53–57/69）断言 0 轮。

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

#### `slot_errors`（拦截时预期报出的槽位错误）

```yaml
      validate: blocked
      slot_errors:
        mode: any
        expect:
          - code: slot.constraint_violated
            slotName: 增益目标             # 可选：精确
            message: 增益目标不允许为 20%   # 可选：精确（仅归一化空白/全半角）
            contains: [gain, 增益]
            facts: { actual: "20" }
          - slot.not_provided            # 只写 code 的简写
```

- 只在**该段** `validate: blocked` 且 `assert.a2a-t.validate` 打开时判定；带 `blocked_followup`
  的用例因此只判**拦截段**（补充段的 `validate` 由派生逻辑改成 `pass`，不判）。
- 观测源两处都收：Task-T 之前 `PromptGenerationError.failed_parameters`、Task-T 之后
  `ContentValidationError.errors`。
- 匹配键：`code`（精确）、`slotName`（精确，可选）、`message`（精确，仅做全半角与空白归一化）、
  `contains`（任一关键词出现在 slot 名 / message / facts 值里）、`facts`（子集精确匹配）。
- `/annotate` 采纳一条候选时导出的就是**整个观测到的错误**（`code` + `slotName` + `message` +
  `facts`），这样 oracle 记录的是当时真正报出的内容；如果不想把模型渲染的 `slotName`/`message`
  固化进预期，在用例（或 overlay）里删掉对应键即可（只留 `code` / `facts` 同样合法）。
- 这两个块既可以写在用例 YAML 的 `expect.protocols` 下，也可以写在 `eval/osp/expectations-overlay.yaml`
  （`CaseCatalog` 加载时合并）；现场推荐 overlay，见 §12.5。
- `mode: any`：观测到的错误里任意一条命中任意一个期望即通过。`mode: cover`：观测到的每条错误都必须
  被某条期望覆盖（且至少观测到一条）。
- 没有该块 = 不判定（保持"只看 verdict"的历史行为）。

#### `negotiation.messages`（服务端协商报文）

```yaml
      negotiation:
        expected: true
        expected_rounds: 1
        kinds: [feasibility]
        rounds: [...]                 # 不变：客户端答复脚本
        messages:                     # 外层下标 = 轮次（1 基），内层 = 该轮可接受的渲染
          - - |-
              ## Feasibility Negotiation
              ...
            - |-
              ## Feasibility Negotiation
              ...
```

- 观测源：`run.proposals[i].content`（A2A-T 是渲染后的 Negotiation-T；A2A 是下游 `INPUT_REQUIRED`
  原文），不是 `wire.jsonl`。
- 判定：期望小节必须按序出现（观测方允许**多出**可选小节）；逐小节比业务事实——速率、时段与小时、
  站点 id、百分比、结论词。期望事实必须是观测事实的子集，措辞可自由改写。没有小节的纯文本
  （A2A 常见）按整段比事实，没有事实时退化为关键词包含。
- 每轮至少命中一个备选；轮数必须等于 `expected_rounds`（多一轮、少一轮都红）。
- `expected_rounds > 1` 时必须嵌套成"每轮一组"；单轮用例可以写成扁平字符串列表（即该轮的备选）。
- `--nms-sim` 的 pure A2A 仍是 `recorded`：模拟器发的是固定文案，无法复现厂商对话。

### 5.4 协商

- **A2A**：89 条有 P 列协商答复，`expected_rounds = len(rounds)`（1–3）；11 条没有，断言 0 轮。
  协商类型原表未给，`kinds: null`，不判定类型。
- **A2A-T**：仅 10 条有 Q 列协商答复，各 1 轮；`kinds` 由答复里的标题派生：
  `Feasibility Negotiation Result` → `feasibility`，`Target Negotiation Result` → `target`。
  其中 9 条只有 `Feasibility Negotiation Result`，id 51 只有 `Information Negotiation Result`。
  解析器仍支持一条答复里同时出现多种协商，并按出现顺序生成决策。
- **报文内容**：`negotiation.rounds` 是**客户端答复的期望文本**（Q/P 列）。批次
  `negotiationSource=script` 时它仍作为脚本驱动运行；`negotiationSource=agent` 时不再进入运行，
  只作为 client 决策质量评委的期望。服务端报文用 `negotiation.messages` 另写，判定规则见 5.3
  之后的 `negotiation.messages` 一节。没有该块时只判轮数与类型。

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
- 6 条协商用例（32/34/35/36/37/38）的 `expect.intent` 是协商后的最终意图；其中 37/38 现在是拦截用例，期望描述的是 `blocked_followup` 的补充请求（补充后仍触发一轮可行性协商），runner 按两段式执行。离线 runner 只看到初始请求，所以拦截段的 `intent` 标 `recorded`，不判失败。这些用例的 `task_t` / `validate` 期望也可能包含协商后才产生的值（例如 32/35 的 `10Mbps`），离线跑出现差异时先对照 `known_issue` 判断是初始转换问题还是协商终态差异。
- `slot.*` 生成失败（缺区域、缺速率、非法制式等）发生在 Task-T 之前；如果该用例预期 `validate: blocked`，runner 把 validate 判为通过，intent/task_t 标 `recorded`。补充段若仍发生 `slot.*`，说明补充请求仍不完整，按 error/failed 记录。
- Task Context 是自由文本，runner 先抽取速率、时段、站点、RAT、模式再比较；两边都只有时段时才退回分词比较。有疑问时看报告里的 `expected` / `actual` 原文。

## 9. 评测控制台 `/tester`

`/tester` 已改为 **catalog 驱动**，不再导入 Excel：

- 左侧列出 `eval/osp/cases` 的 100 条用例，可按 id/名称、维度、操作、校验结果、标签筛选，勾选后批量执行。
- 一条用例 × 一个协议 = 一个运行项；协议可选 A2A-T / A2A。
- 运行配置：`执行到`（intent / task_t / validate / send / negotiate / final）、审批、超时。
- 前三阶段走 SDK-only runner；后三阶段走 `RunManager` 完整运行时。
- 协商答复来源（批次级）：`script` 时 A2A-T 用 Q 列答复生成 `NegotiationDecision`，A2A 用 P 列
  答复作为 `INPUT_REQUIRED` 答案；`agent` 时 A2A-T 由 client agent 现场决策，Q 列只作期望文本。
  离线进程只能 `script`；`agent` 需要 live 决策模式。
- 结果表按阶段显示 `passed / failed / recorded / error`，点“差异”看逐字段/逐段 expected/actual。
- 带 `blocked_followup` 的拦截用例：全流程批次由 run 自己的 gate 拦截后在同一 run 内注入补充请求
  （`correction_applied`），run 的 `userRequest` 仍是原请求；SDK-only 批次由 runner 先跑拦截段、
  再跑补充段。拦截段证据在抽屉里以 `拦截/...` 展示，被拦截的原请求不会被下发。
- 用例详情抽屉展示 `baseline.request`（Modify 的前置基线 Create；从结果行打开时同时显示本次实际
  基线的 state 与 wire 链接）和 `blocked_followup.input`（拦截后补充请求）。有补充请求时，
  `expect.intent` / `task_t` 标注为「（补充请求）」。
- 执行上下文不再需要导出才能看到：结果表有独立的 `RunId` 列；Modify 运行项分两行显示
  `基线` 和 `修改` 两个 run（各自链到 wire），普通用例只有一行。结果行在「结果 / 错误」下显示
  `task <短 id>`（悬停看完整 runId / traceId / taskId / contextId）；结果抽屉的「运行上下文」
  给出 `runId`（wire / trace 链接）、`traceId`、`taskId`、`contextId`。
- 基线进度在结果抽屉的「基线 Create（Modify 前置）」块实时显示（创建中 / 已受理 / 失败 +
  当前 Task state + runId / taskId / contextId），结果表的阶段列也会出现「基线」chip。
- 结果抽屉新增「轨迹」tab：读取 `/api/runs/{runId}/trace.json` 的 transcript timeline，按 seq 展示
  事件/状态/耗时，展开可看原始 JSON；Modify 可在「基线 / 修改」两个 run 之间切换。抽屉打开时随批次
  轮询刷新（只有事件数或 run 状态变化才重绘，保留滚动位置）。
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

## 11. 实时模型评测（Task-T / 拦截）

离线 runner 用确定性 provider；要用真实模型（黄区验收、模型对比）评测 Task-T 与拦截结论时，有两个入口，
预期值都来自本目录的用例：

```bash
# 1) pytest：每个用例一个断言，失败信息带逐阶段状态与 diff
A2AT_OSP_LIVE=1 \
A2AT_LLM_PROVIDER=openai A2AT_LLM_MODEL=mass/qwen3.8-flash \
A2AT_LLM_BASE_URL=http://127.0.0.1:31943/v1 A2AT_LLM_API_KEY=not-needed \
A2AT_LLM_MAX_TOKENS=16384 \
pytest tests/test_osp_live_eval.py -q

# 2) runner：写 JSON 报告，summary.stages 给出 intent / task_t / validate 的分阶段计数
python -m eval.osp.runner --all --stop-after validate --model-mode live \
  --out runs/osp-eval/qwen3.8-flash.json

# 3) 同一次运行再写一份失败用例的 Markdown 详情报告（原始请求、逐阶段状态、期望/实际 diff）
python -m eval.osp.runner --all --stop-after validate --model-mode live \
  --out runs/osp-eval/qwen3.8-flash.json --detail runs/osp-eval/qwen3.8-flash-failures.md
```

要点：

- **默认跳过**。`A2AT_OSP_LIVE` 未设置时 `tests/test_osp_live_eval.py` 整个模块跳过，普通套件与 CI 不发任何
  模型请求。
- **配置来源**：`A2AT_OSP_LIVE_ENV` 指向一个 live `.env`（推荐黄区使用，内含 `A2AT_SAMPLE_MODEL_MODE=live`
  与 `A2AT_LLM_*` / `ADK_MODEL_*`）；不设时使用导入时捕获的 shell 导出（样例 `conftest` 会为每个测试清理
  `A2AT_*`，模块在构建客户端前恢复）。
- **子集**：`A2AT_OSP_LIVE_IDS=1,11,42`，或 runner 的 `--ids`。
- **阶段口径**：`intent` / `task_t` / `validate` 只跑 SDK 管线，不经过样例 OSS 的业务 gate；`blocked`
  的判定见第 10 节。
- **耗时**：单个用例（3 次模型调用）在远端模型上约 1–3 分钟，但个别行（站点/时间段很多）单次调用可能
  超过默认 180 秒，表现为全阶段 `error`。此时把 `A2AT_SAMPLE_LLM_TIMEOUT_SECONDS` 调到 600 再跑，
  不要直接判定为模型缺陷；100 条串行需要数小时，按需用 `--ids` 分批，或把 `--out` 指向不同文件后汇总
  `summary.stages`。

## 12. 预期标注控制台 `/annotate`

新增的两类期望（`slot_errors`、`negotiation.messages`）不靠手写：`/annotate` 把已经跑过的 tester
批次里的观测值整理成候选，人工挑选并确认，再**导出**给用例维护者合并。它是独立页面，不是 `/tester`
的一个 tab——`/tester` 只服务"本浏览器发起的那一个批次"，而标注要跨批次、跨轮次、跨操作员查看同一批
用例的语料。

> 现场（黄区）启动与操作手册见 **`eval/osp/ANNOTATE-GUIDE.md`**：只读实例的启动命令、健康检查、页面流程、
> HTTP API、导出合并与故障对照都在那里；本节的 12.7 只给最小示例。

### 12.1 目标模型：用例 → 协议 → 子任务

页面映射的就是 `eval/osp/cases` 这 100 条用例，每个用例都有两个协议任务，任务下挂它实际拥有的子任务：

```
用例 #16 设置 gain target为20%至30%
├── A2A-T 任务
│   ├── 子任务：拦截 slot_errors      （仅 validate: blocked 的 52 条）
│   └── 子任务：协商报文 messages     （仅 expected_rounds > 0 的 10 条）
└── A2A 任务
    └── 子任务：协商报文 messages     （89 条）
```

- 语料分布：10 条双协议都协商（26/28/29/30/31/32/50/51/88/89），79 条只有 A2A 协商，0 条只有
  A2A-T 协商，11 条都不协商（45–49/53–57/69）。
- 没有子任务的协议任务显示为「无需标注」，列表可勾选隐藏；它不是错误，而是"该协议不预期协商"这一事实。
- 子任务总数 151（52 + 10 + 89），页面顶部的进度按子任务统计 `未采集 / 草稿 / 已确认`。

### 12.2 数据来源

| 产物 | 位置 | 提供 |
|---|---|---|
| tester 批次快照 | `runs/tester/<batchId>.json` | caseId / protocol / 批次轮次 / 是否真机 / 决策模式 / 模型 / 每段 `stages`（含 `validate.detail.slotErrors` 与拦截段）、runId |
| run 存储 | `runs/client-state.sqlite3` → `negotiation_events` | 每轮服务端报文 `content`（A2A-T 的 Negotiation-T 原文；A2A 的下游 `INPUT_REQUIRED` 原文），按 `runId` 关联 |

- 扫描**不按浏览器 owner 过滤**，多人多轮的批次合在一起看；默认只读最近 1 天（可调 6 小时–7 天）。
- 真机判定读批次的 `discovery: nms`（兼容旧的 `nms` / `nmsSim` 键）；`decisionMode`、模型名与
  `nms` 都会入库。重新「采集候选」会刷新这些元数据，修复前采集的旧记录无需删库。
- 批次超过上限时会显式提示"已达批次上限"，不做静默截断。
- 右侧「运行轮次」下拉只列**当前用例、当前协议**的观测，按批次完成时间倒序；默认选中**最新一条真机
  + live** 的观测，另有「全部轮次」把多轮候选并列（并按内容聚合 `出现 k/n 次`）。
- 三个勾选框：只看真机、只看 live、只看一致候选（默认全开）。

### 12.3 两个子任务

- **拦截 `slot_errors`**：52 条 `validate: blocked` 用例各一个。候选来自拦截段的 `slotErrors`；
  采纳后导出**整个观测错误**（`code` / `slotName` / `message` / `facts`），可再编辑 `contains`
  关键词（多语言标签用逗号分隔），并选择 `mode: any | cover`。
- **协商报文 `messages`**：10 条 A2A-T + 89 条 A2A 用例各一个。候选按协商轮次分组，同一轮可采纳
  多个备选（"任意一个都 OK"）。

### 12.4 一致性守门

只有**与用例现有预期一致**的观测可以采纳：拦截用例必须实测也是 `blocked`，协商用例的观测轮数必须
等于 `expected_rounds` 且 `kinds` 命中。不一致的候选仍然展示（红底）作为证据，但不能变成 oracle；
需要记录时写进备注或 `known_issue`。这样可以避免"把当前缺陷固化成期望"。

### 12.5 存储与使用（默认直接读库，不需要导出）

- 标注库：`runs/annotations/annotations.sqlite3`（append-only：`observation` / `target` / `choice` /
  `event`）。来源 runId、确认人、时间只存在这里，**不写进用例 YAML**。
- 交互是**选择 → 确认保存**两步：勾选、关键词、`mode`、备注都只改页面本地状态，不发请求也不刷新；
  底部「确认保存」用一次 `POST /api/annotate/case/{id}/{protocol}/apply` 提交当前用例 + 协议下的
  **全部**子任务（accepted 集合 = 提交的 `observationIds`）并标记为已确认。保存成功后「已保存的
  导出值」和左侧状态徽标才更新；未保存就切换用例会提示。
- 状态机：`pending → draft → confirmed`；确认后再改候选会自动退回 `draft`。
- **页面不写任何用例文件**：黄区的用例会被热更新替换，直接写会被覆盖或与正在测的副本分叉。
- **默认不需要导出**：`CaseCatalog` 通过 `annotation_overlay` 回调**按用例 id 直接读标注库**的
  已确认值，在加载时叠加到用例文档；标注页保存后立即 reload。tester / review / annotate 看到的是
  同一份合并结果，没有"忘记导出"的中间态。审核实例用 `--annotations` 指向标注库（默认
  `<runs>/annotations/annotations.sqlite3`，与标注实例默认 `--store` 相同）。离线
  `python -m eval.osp.runner` 直接读用例 YAML，不读标注库；需要离线复判时以 tester 的离线批次为准。
  - 读库使用 SQLite `mode=ro`，不创建 `-wal`、不修改写入方；库不存在时视为无 overlay。
  - 标注库中个别条目失效（例如用例 `expected_rounds` 改过）时只跳过该用例并告警，不影响整个语料。
- **可选导出**：顶部「导出 overlay YAML / JSON」产出 `osp-expectations-overlay/v1`，用于跨环境、
  入库或蓝区固化；可直接放到 `eval/osp/expectations-overlay.yaml`（或 `--overlay` 指定的路径）。
  - overlay 只接受两类键：`a2a-t.slot_errors`（仅 blocked 用例）与
    `<protocol>.negotiation.messages`；`validate` / `task_t` / 轮次数仍以生成的用例为准。
  - YAML overlay 是严格模式（写错即失败）；标注库是宽松模式（坏条目告警后跳过）。
  - 叠加顺序：生成用例 < YAML overlay < 标注库（最新的人工确认优先）。
- 右侧「生成期望片段」：单用例的片段，已按 `expect.protocols` 的层级（6 空格）缩进；没有已确认值时
  会明确说明。它和 overlay 的单个 case 条目同形。
- `eval/osp/index.yaml` 只记录 id/文件名/标签/轮次数，不含这两类期望，无需更新它。

### 12.6 注意

- 采纳的是**观测到的原文**，但判定仍按第 5 节的归一化规则（小节 + 业务事实）：一次措辞不同的
  渲染不会让用例变红。
- 标注库不改动 run 存储，也不删除任何候选；重复扫描同一批次只会累加 `seen_count`。
- 该页面只读批次与 run 存储、写标注库，不启动 run、不向网管下发任何东西。

### 12.7 指向另一个实例的只读标注实例

现场实例不方便重启时，**不要**在它上面再起一个 Web UI：完整应用启动时会执行 `recover_runs()`，
可能把线上尚未结束的 run 标成 interrupted。改为启动只读标注服务：

```bash
# 读取现场实例的 runs/（只读），标注库放到别处，另开一个端口
python -m demo.web.annotate \
  --runs-dir /data/live/runs \
  --store /data/annotate/annotations.sqlite3 \
  --port 8090
# 打开 http://127.0.0.1:8090/annotate
```

| 参数 | 说明 |
|---|---|
| `--runs-dir` | 源 runs 目录；批次 JSON 在 `<runs-dir>/tester` |
| `--client-db` | 服务端报文来源，默认 `<runs-dir>/client-state.sqlite3`；缺失时只采集 slot 候选并告警 |
| `--store` | 标注库路径，**唯一被写入**的文件；不传则写 `<runs-dir>/annotations/annotations.sqlite3` |
| `--cases-dir` | 要标注的用例目录，默认打包含的 `eval/osp/cases` |
| `--host/--port` | 默认 `127.0.0.1:8090`（与 Web UI 的 8080 不冲突） |
| `--check` | 只打印解析后的路径并退出，开端口前先确认指向 |

- 该进程不启动 run、不恢复 run 状态、不向源目录写其它文件；源实例正在跑时也可以同时使用（SQLite 读连接兼容 WAL）。
- 需要挂到 LAN 时自己加 `--host 0.0.0.0`，并注意该页面会展示请求原文与报文。

### 12.8 按 traceId / runId 批量采纳

第一批期望值不需要逐个子任务点选：把已经确认过的运行标识（traceId，或 run 控制台里的 runId）
填进页面顶部的「按 traceId / runId 批量采纳」，一次把它们的观测采纳为标注值（默认同时确认）。

- **标识即选择**：填了就只读携带这些标识的批次条目，**不受时间窗限制**；不会因为一条旧 trace
  不在默认 1 天窗口里就被报成"未找到"。
- **仍然过一致性守门**：与用例现有预期不一致的观测（该拦未拦、轮数/类型不符）永远不采纳，只在
  结果里报 `无一致候选`。
- **不覆盖已审阅的工作**：目标已有采纳值或用例已有该期望时跳过（`已有采纳` / `用例已有期望`）；
  勾选「覆盖已有采纳/期望」才重写。同一目标下同一内容只保留一份（按轮次去重）。
- **可限定范围**：协议（A2A-T / A2A）与子任务（`slot_errors` / `messages`）两个下拉可以缩小批次。
- **结果可解释**：返回匹配/未找到/无候选的标识数、扫描批次数、逐目标采纳结果与跳过原因；
  批次超上限会显式提示。
- 自动采纳与手工勾选等价：`messages` 落原文作为该轮备选，`slot_errors` 落**整个观测错误**
  （`code` / `slotName` / `message` / `facts`），导出格式见 §5.3。API 见 `ANNOTATE-GUIDE.md` 第 7 节。

## 13. 验收审核控制台 `/review`

tester 回答"这一轮跑得怎么样"，`/review` 回答"**用今天的语料看，这批证据能不能验收**"。它是跨
批次的只读验收层：证据仍来自 `runs/tester/*.json`，判定在读取时用当前用例重新计算，结论由人给出并
落库，最后导出验收报告。

### 13.1 判定模型

| 维度 | oracle | 证据 | 协议 |
|---|---|---|---|
| 最终意图（硬门槛） | `expect.intent` vs `serverIntent`（`flatten_intent` 语义判定） | 下游 SUBMITTED 结构化读数 | A2A-T；A2A 仅记录 |
| SDK Task-T | `expect.protocols.a2a-t.task_t` | `stages.task_t.actual`（分节判定） | A2A-T |
| 拦截 | `validate` + `slot_errors` | 拦截段 `stages.validate.detail` | A2A-T |
| 协商 | 轮数/类型 + `messages` | 阶段 actual + 按 runId 关联的 `negotiation_events` | 双协议 |
| 终态 | `final_state` | `stages.final.actual` | 按 `assert` |

- **以当前语料重判**：`eval/osp/review.py` 用 `compare.py` / `expectations.py` 对已存证据重新判，
  所以新写的 `slot_errors` / `messages` 立即对旧批次生效，不需要重跑。期望值的来源是标注库
  （按用例 id 直接读，见 §12.5），可选的 YAML overlay 叠加在其前面。
- **意图三方对比**：页面并列「用例预期 / 服务端最终 / SDK 抽取」，前者衡量交付正确性，后两者衡量
  SDK 抽取与 Task-T 传递损耗。
- **blocked 用例**：拦截段对原始用例判 validate + slot_errors；后续段对 `followup_case` 判
  intent / Task-T / 终态。
- **不可判定就标出来**：模拟器/代理/无直连 payload 的意图是 `recorded`，缺报文证据是 `missing`，
  两者都让协议任务成为 `partial`，不能被当成通过。
- 证据按用例 id 归属：用例被就地修改（黄区常见）时，旧轮次仍按该用例重新判定；该轮冻结的原文
  （`refs.userInput`）只在轮次详情里展示为溯源信息，不再作为有效性门槛。

### 13.2 结论与新鲜度

- 自动判定：`pass / fail / partial / recorded / missing / not_expected / not_applicable / not_run`。
- 人工结论（按用例 × 协议，共 200 个）：`pending / accepted / defect / known_issue / recheck`；
  缺陷必须填归属（SDK / 网管 / 用例 / 环境 / 其他）和备注。
- 人工覆盖自动判定时服务端要求备注；页面提示"自动判定不是通过"。
- 结论记录证据 runId、用例指纹和保存时的自动判定（整体 + 各维度）：用例或期望变更 → `stale`；
  之后的新轮次若自动判定与保存时一致，结论静默沿用（`newEvidence` 只作信息）；整体状态或任一维度
  变化 → `verdictChanged`，保留原结论但要求复核，并列出变化点。

### 13.3 页面与导出

- 左栏用例列表，右栏当前用例 × 协议：结论卡、运行轮次（默认最新真机 + live，可看全部轮次稳定性）、
  最终意图、Task-T、拦截、协商、运行终态、审核结论、证据。
- 「保存结论」一次写入该协议任务的结论；不再有逐项提交。
- 批次视图的「本轮用例状态」和「关键数据」默认只显示本轮实际有 run 的协议任务（勾选「只看本轮已运行」），
  取消勾选可看完整 100 个用例；证据覆盖数仍在批次摘要里。
- 从批次表点开的用例详情是右侧抽屉；保存结论后点「关闭」（Esc / 遮罩同样可关）回到批次表，
  筛选、滚动位置和当前批次保持不变，适合连续复核。
- 导出：`osp-review/v1` YAML/JSON + Markdown 验收报告（结论汇总、用例矩阵、缺陷清单按归属分组、
  已认可偏差、待审核、已知问题/待复核）。与 `/annotate` 一样不写用例文件。
- 入口：主 Web UI 的 `/review`；黄区用只读进程
  `python -m demo.web.review --runs-dir … --store … [--annotations <标注库>] --port 8091`。
  详细运行手册见 **`eval/osp/REVIEW-GUIDE.md`**。
