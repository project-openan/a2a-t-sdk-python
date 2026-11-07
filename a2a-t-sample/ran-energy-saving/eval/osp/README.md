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
| 20 / 21 / 28 / 34 | A2A 与 A2A-T 的预期行为差异，见 `known_issue` | 只加说明，不关断言 |

这些用例都标了 `tags: [needs-confirmation]`，业务确认后把对应 `assert` 打开即可。

## 7. 重新生成

源 JSON 在 `docs-local/osp-eval-cases/`（内部目录，不入库）。修改源数据后：

```bash
python docs-local/osp-eval-cases/generate_osp_cases.py
python docs-local/osp-eval-cases/generate_osp_cases.py --check
```

`--check` 会逐文件比较生成的 YAML 与已提交文件，有漂移时返回非零。

## 8. 已知限制

- **A2A 最终状态不判定**：原表只给意图与协商答复。
- **术语上下文是软断言**：没有独立可观测点。
- **SDK 校验与样例业务门分开**：样例 OSS 还有一层必填字段 gate（`_missing_required_fields`）。
  要判定"预期SDK校验结果"，`intent` / `task_t` / `validate` 三个阶段应只跑 SDK 管线，
  不要把样例 gate 的 `blocked` 当成 SDK 的 `blocked`。
- **tester 入口默认不做校验**：`A2AT_SAMPLE_PRE_SEND_VALIDATION=on` 只覆盖
  `source.kind=presentation`；用 `/tester` 跑这些用例时必须显式设 `always`（见 `runner`）。
- **`scenario: night` 只是运行时配置**：它不提供任何预期值，预期全部来自本文件。
