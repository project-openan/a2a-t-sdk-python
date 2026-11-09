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
3. `Energy saving mode`
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
  id 34 同一段里先 Target 后 Feasibility，记为 `[target, feasibility]`。

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
| 20 / 21 / 28 / 34 | A2A 与 A2A-T 的预期行为差异，见 `known_issue` | 只加说明，不关断言 |

这些用例都标了 `tags: [needs-confirmation]`，业务确认后把对应 `assert` 打开即可。

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

- `--model-mode live` 用 sample `.env` 里的真实模型；`offline` 用 demo 的脚本模型。脚本模型不支持 11-key schema，提取结果会为空，只适合链路自检。
- `--out` 写 JSON 报告；默认写到 `runs/osp-eval/<timestamp>.json`。

### 8.1 判定细节

- `assert.intent: false` 的用例（40/46）和 `assert.a2a-t.task_t: false` 的用例只记录，不参与 pass/fail。
- `assert.a2a-t.validate: false` 的用例（49–51）只记录。
- 6 条协商用例（32/34/35/36/37/38）的 `expect.intent` 是协商后的最终意图；离线 runner 只看到初始请求，所以 `intent` 阶段标 `recorded`，不判失败。
- `slot.*` 生成失败（缺区域、缺速率、非法制式等）发生在 Task-T 之前；如果该用例预期 `validate: blocked`，runner 把 validate 判为通过，intent/task_t 标 `recorded`。
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
- 临时请求 tab：手输文本直接跑一次，不做预期断言。
- 报告导出 JSON / XLSX；Excel 只作为导出格式，不再是输入。

### 9.1 Modify 的限制

Modify 用例会先跑一个基线 Create，拿到 `taskId` / `contextId` 后再以 modify 模式续传。但**本地 RAN Agent 会立即把任务置为终态**，随后服务端拒绝 Modify（`Task ... is in terminal state`）。

要在本地验证 19 条 Modify 用例，请用支持长任务的 NMS 模拟器（`--nms --nms-sim` + `A2AT_SAMPLE_NMS_SIM_HOLD_WORKING=1`），或直接连真实 NMS。控制台会把基线 run id、Task id 和失败原因写进结果，便于判断是 seed 问题还是服务端拒绝。

## 10. 已知限制

- **A2A 最终状态不判定**：原表只给意图与协商答复。
- **术语上下文是软断言**：没有独立可观测点。
- **SDK 校验与样例业务门分开**：样例 OSS 还有一层必填字段 gate（`_missing_required_fields`）。
  要判定"预期SDK校验结果"，`intent` / `task_t` / `validate` 三个阶段应只跑 SDK 管线，
  不要把样例 gate 的 `blocked` 当成 SDK 的 `blocked`。
- **控制台显式开启校验**：`/tester` 启动运行项时传 `validate_before_send=True`，不依赖
  `A2AT_SAMPLE_PRE_SEND_VALIDATION=on` 的 `presentation` 限定。
- **`scenario: night` 只是运行时配置**：它不提供任何预期值，预期全部来自本文件。
