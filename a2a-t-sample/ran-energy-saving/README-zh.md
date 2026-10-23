# RAN 节能样例

一个 A2A-T 样例：包含一个客户端 Agent、一个 RAN 域服务端 Agent，以及用于发现 AgentCard 的 mock
注册中心。

**读者定位。** 本文档面向实现 **client agent** 的团队；**server agent** 是可运行的参考实现。
生产中两端通常归属不同厂商，因此下文统一按角色说明各自负责什么，不使用含混的第二人称。

## 目录

- [1. 本样例演示了什么](#1-本样例演示了什么)
  - [1.1 目录结构](#11-目录结构)
- [2. 快速开始](#2-快速开始)
- [3. 使用 A2A-T SDK 实现 Task-T 交互](#3-使用-a2a-t-sdk-实现-task-t-交互)
  - [3.1 四个步骤](#31-四个步骤)
  - [3.2 客户端侧](#32-客户端侧)
  - [3.3 服务端侧](#33-服务端侧)
- [4. 使用 A2A-T SDK 实现 Negotiation-T 交互](#4-使用-a2a-t-sdk-实现-negotiation-t-交互)
  - [4.1 协商场景](#41-协商场景)
  - [4.2 样例如何选择协商类型](#42-样例如何选择协商类型)
  - [4.3 通用机制](#43-通用机制)
  - [4.4 信息协商（information）](#44-信息协商information)
  - [4.5 目标协商（target）](#45-目标协商target)
  - [4.6 可行性协商（feasibility）](#46-可行性协商feasibility)
  - [4.7 reject 与 abort](#47-reject-与-abort)
- [5. 自定义 Agent 开发说明](#5-自定义-agent-开发说明)
- [6. 附录](#6-附录)
  - [6.1 Webhook 传输](#61-webhook-传输)
  - [6.2 配置（a2a-t-sample/.env）](#62-配置a2a-t-sampleenv)
  - [6.3 分别启动两端](#63-分别启动两端)
  - [6.4 mock LLM 机制](#64-mock-llm-机制)
  - [6.5 支持的参数（`run_demo.py`）](#65-支持的参数run_demopy)

## 1. 本样例演示了什么

本样例演示 A2A-T Agent 协作：客户端 Agent 与 RAN（Radio Access Network，无线接入网）节能 Agent
交互，执行节能任务。样例同时使用 Task-T 与 Negotiation-T。

关键工作流：

- **Task-T（不含协商）：**

  ```text
  自然语言 ──> 任务提示词 ──> 校验 ──> 任务执行
  ```

- **Task-T + Negotiation-T（含协商）：**

  ```text
  自然语言（不完整 / 存在歧义 / 不可行）
    ──> 任务提示词 ──> 校验 ──> 协商 ──> 达成一致 ──> 任务执行
  ```

### 1.1 目录结构

| 路径 | 核心职责 |
| --- | --- |
| [`src/client/client_main.py`](src/client/client_main.py) | 装配根：读取 `.env`、构建运行时（`build_client_runtime`）、从注册中心发现服务端 AgentCard、启动/停止 webhook 接收器、装配 demo 的 `ScriptedNegotiationResponseProvider`。客户端侧 `INTEGRATION POINT` 标注位于 `main()`。 |
| [`src/client/client_flow.py`](src/client/client_flow.py) | 编排：消息序列（生成 → 发送 → 消费）、报文机制（metadata 键、`A2A-Extensions` 头）、事件管线，以及两种传输驱动（SSE 流式 / webhook 队列）。它调用协商决策接入点，但不持有任何决策。 |
| [`src/client/negotiation_response.py`](src/client/negotiation_response.py) | 协商决策接入点：`NegotiationProposeView`、`NegotiationDecision`、`NegotiationResponseProvider` Protocol，以及 demo 实现 `ScriptedNegotiationResponseProvider`。 |
| [`src/client/scenario_data.py`](src/client/scenario_data.py) | 样例输入与脚本化应答 —— 它**同时喂两侧**（见 [§5](#5-自定义-agent-开发说明)）。 |
| [`src/server/server_main.py`](src/server/server_main.py) | A2A SDK 应用装配默认包含推送通知模块。服务端集成时请查看 `build_server_runtime` 中的 `INTEGRATION POINT` 注释。 |
| [`src/server/server_flow.py`](src/server/server_flow.py) | 服务端流程：校验 → 协商或执行 → 流式推送进度与报告。 |
| [`src/server/task_execution.py`](src/server/task_execution.py) | 集成 `TaskExecutionService` 作为执行入口，并包含一个模拟任务进度、生成意图报告的 demo 执行服务。 |
| [`src/common/`](src/common) | 共享适配器、按 schema 路由的 mock LLM、`WebhookReceiver`，以及双端共享的 `TASK_PARAM_SCHEMA`。 |
| [`src/registry/`](src/registry) | mock 注册中心（AgentCard 注册/查询，端口 5001）。 |
| [`resources/mock_llm/`](resources/mock_llm) | 脚本化的槽位抽取与内容校验应答。 |
| [`run_demo.py`](run_demo.py) | 非阻塞启动器：后台启动注册中心与服务端，前台运行客户端。 |

## 2. 快速开始

前置条件：Python 3.12 或更高版本与 [`uv`](https://docs.astral.sh/uv/)。

**Step 1 —— 创建仓库 `.venv` 并安装 SDK。** 在仓库根目录执行。

```bash
uv sync --dev
```

**Step 2 —— 配置样例。** `a2a-t-sample/` 存放样例读取的 `.env`。

```bash
cd a2a-t-sample
cp env.example .env
uv pip install -r requirements.txt
```

**Step 3 —— 运行样例。** 启动器在后台启动注册中心与服务端，并在前台运行客户端。加
`--language zh-CN` 运行中文。

```bash
cd ran-energy-saving
python run_demo.py
```

请使用仓库 `.venv` 中的解释器（激活该环境，或直接调用）。

> **Windows 提示：** 直接指定解释器，例如
> `..\..\.venv\Scripts\python.exe run_demo.py`。

客户端与服务端的日志会实时交错输出到终端；客户端退出后，后台进程自动终止。

加 `--scenario negotiation` 运行信息协商（缺失 region），或加 `--negotiation target` /
`--negotiation feasibility` 运行另外两类。若客户端无法保持流式连接，加 `--transport webhook` ——
见 [§6.1](#61-webhook-传输)。

## 3. 使用 A2A-T SDK 实现 Task-T 交互

### 3.1 四个步骤

1. **生成** —— 客户端用 A2A-T 客户端 SDK 的 `generate_task_prompt_from_text`，把自然语言输入转换为
   Task-T 任务请求（渲染好的提示词）。
2. **发送** —— 提示词放在消息 `metadata` 中发给服务端。
3. **校验** —— 服务端从 metadata 中取出提示词，调用 A2A-T 服务端 SDK 的
   `validate_task_prompt_and_data_filling` 校验并抽取参数（`operationType`、`region`、
   `energySavingGoal`、`guaranteedThroughputTarget`，以及可选的站点级 `siteList` 与
   `backgroundEvent`）。双端共享的调用方 schema `TASK_PARAM_SCHEMA` 位于
   [`src/common/task_schema.py`](src/common/task_schema.py)。
4. **执行并报告** —— 抽取出的参数交给业务执行；进度以状态更新返回、结果以报告 artifact 返回，
   由客户端呈现。

### 3.2 客户端侧

交互的核心步骤如下（摘自 [`client_flow.py`](src/client/client_flow.py)，有简化）：

```python
prompt_content = prompt_client.generate_task_prompt_from_text(input_text, ENERGY_SAVING_TEMPLATE_URI)
prompt_text, extension_uri = _require_prompt_text(prompt_content)
request = SendMessageRequest()
...  # 此处省略 message_id / role
request.message.parts.add().text = _build_request_text(initial_input)   # 承载场景文本的 text 部分
request.message.metadata[extension_uri] = prompt_text   # 提示词放在 metadata 中传输
request.message.metadata["templateUri"] = getattr(prompt_content, "template_uri", None) or ENERGY_SAVING_TEMPLATE_URI
call_context = ClientCallContext(service_parameters={"A2A-Extensions": extension_uri})
async for stream_response in a2a_client.send_message(request, context=call_context):
    ...  # 消费状态更新与报告 artifact
```

### 3.3 服务端侧

`_extract_prompt_text` 从 metadata 读取提示词，`execute_server_flow` 调用
`validate_task_prompt_and_data_filling` 完成校验与抽取（[`server_flow.py`](src/server/server_flow.py)）。
被拒绝的提示词映射为 `TASK_STATE_REJECTED`，而不是让服务崩溃；校验通过的参数会传给执行接入点
（`TaskExecutionService`）。

模板、参数 schema 与自然语言输入都是样例特有的；各端需要替换的内容见
[§5](#5-自定义-agent-开发说明)。

## 4. 使用 A2A-T SDK 实现 Negotiation-T 交互

两个 SDK 负责协议细节，业务决策由两端 Agent 各自负责。

- **A2A-T SDK** 负责生成与校验协商消息（propose、accept、reject、abort）：渲染提示词文本，并把
  调用方传入的 `negotiationContext` 带入生成的消息。它不保存会话状态，也不含传输层。
- **A2A SDK** 负责传输这些消息，因此部署时总是两者配合使用。
- 在本样例中，server agent 决定请求何时无法按收到的内容执行、且值得通过协商解决；client agent
  决定如何应答收到的 propose。协议本身允许任何一方发起 propose（见
  [§4.3](#43-通用机制)）。

### 4.1 协商场景

在本样例中，服务端无法按收到的请求执行、且该问题可以通过协商解决而不必直接拒绝时，由服务端开启
协商。样例覆盖三种情况：

**信息不完整。** 客户端要求执行节能，给了目标、时段和吞吐目标，却没说在哪个区域。服务端缺这一项
无法开工，于是告诉客户端缺什么；客户端补上，任务继续。

**目标存在歧义。** 客户端给出的分时段保障速率目标里，两个时段重叠：
`{17:30~23:00,20Mbps}` 与 `{20:00~00:00,10Mbps}`。请求本身格式没问题，但重叠时段的保障速率无法
唯一确定。服务端复述它理解的意图，请客户端调整时间；客户端带着修正后的时段回应。

**目标在当前条件下无法满足。** 客户端要求两个站点在 00:00~18:00 停电期间保障
`{00:00~06:00,2Mbps}、{06:00~18:00,10Mbps}`，而站点既有的备电要求是至少支撑 18 小时。按这个
速率执行，电池撑不到停电结束。服务端给出不可行的结论和提案（把 06:00~18:00 的保障速率从 10Mbps 下调
到 2Mbps）；客户端接受调整后的目标。

### 4.2 样例如何选择协商类型

是否需要协商、以及属于哪种类型，是**server agent 的业务决策，两个 SDK 都不作规定** —— client
只对收到的 propose 作出反应。本样例是数据驱动的：首个内容校验错误的错误码通过
`_NEGOTIATION_TYPE_BY_ERROR_CODE` 表（[`server_flow.py`](src/server/server_flow.py)）选出类型。
这张表是**样例约定，不是 SDK 契约**。

报文并不依赖这张表。每个 propose 都记录自己的 `templateUri` —— 即 metadata 中这段报文由哪个
Prompt 模板渲染而来，例如 `Negotiation-T/information-negotiation/propose/v1`。这个 URI 同时告诉
客户端收到的是哪一类协商，因此客户端按收到的消息决定应答 —— accept、reject 或 abort —— 而不是按
错误码。

### 4.3 通用机制

以下机制对三类协商均适用，各类型小节不再重复。

**轮次。** 一轮由一次 propose 和针对该 propose 的应答（accept、reject 或 abort）构成。会话上下文
携带从 1 开始的 `round`：应答沿用其所回应的 propose 的轮次，只有**新的** propose 才推进轮次。
客户端最初的请求不计入协商轮次 —— 第一轮从服务端的 propose 开始。`maxRounds`（默认 5）限制会话
轮数。

**双方都可以发起 propose。** 协商是对称的：A2A-T 客户端 SDK 与 A2A-T 服务端 SDK 都提供同样的四个
生成方法（propose / accept / reject / abort）和同样的校验方法，任何一方都能发起协商，任何一方也都
能应答。区别只在于谁发送 propose；报文形态、会话处理与传输完全一致。本样例由**服务端**发起，因为
发现"请求无法按收到的内容执行"的是服务端。客户端同样可以发起协商，例如它无法满足服务端公布的某项
约束时，由服务端来 accept / reject / abort。本样例只在客户端侧实现了决策（provider 接入点）；如果
由客户端发起协商，对等的决策逻辑需要放在服务端侧。

**四条报文的闭环：**

1. 客户端 → 服务端：节能请求。样例中它故意在以下三处之一存在缺陷：缺少区域、目标中时段重叠、或
   保障速率无法支撑停电时长。
2. 服务端 → 客户端：propose —— 缺什么、需要澄清什么、或哪个目标不可行以及提供了什么替代方案。
3. 客户端 → 服务端：一条消息，同时承载 accept 和补全/修正后重新发送的请求。
4. 服务端 → 客户端：结果 —— 先进度更新，后报告。

会话（`id` / `round` / `maxRounds` / performative）随每条协商消息放在 `negotiationContext` metadata
中，因此 A2A-T SDK 本身无状态。

**协商发生在任务创建之前。** 第 2 条报文中的 propose 是一条独立的 A2A `Message`（角色为
`ROLE_AGENT`，context id 与请求相同），不是 Task 状态事件。只有 accept 携带的 Task-T 提示词校验
通过后，服务端才创建任务（先 `SUBMITTED`，再 `WORKING`），此后按普通 Task-T 交互继续。节能任务是
值守类长时任务：报告发出后仍保持 `WORKING`，没有 `COMPLETED` 状态 —— 只有任务被取消才会结束。

**client 侧要接的地方。** 收到第 2 条报文时，客户端 flow 构建一个轻量的 `NegotiationProposeView`（kind、
`templateUri`、渲染文本）并调用（[`negotiation_response.py`](src/client/negotiation_response.py)）：

```python
@runtime_checkable
class NegotiationResponseProvider(Protocol):
    def respond(
        self, propose: NegotiationProposeView, session: NegotiationContext
    ) -> NegotiationDecision: ...
```

`NegotiationDecision` 携带 `conclusion`（`accept` / `reject` / `abort`）以及对应的载荷：`accept`
时是 `filled_params`（信息协商，name → value）、`confirmed_intent`（目标协商）或
`feasibility_summary`（可行性协商）；`reject` / `abort` 时是 `reason`。**`accept` 已端到端实现**：
flow 根据 client 侧的决策重新生成 Task-T 提示词、构建 accept 消息（同轮、performative 为
`ACCEPT`），并经所选传输发送。`respond` 是同步的，会阻塞 flow 直到返回；需要等待的实现（人工确认、
上游系统调用）在 `respond` 内部自行处理。

> Provider 只替换对 propose 的脚本化**应答** —— 信息协商中就是 `filled_params` 返回的取值。
> Task-T 补全参数（`negotiation_params`）仍由调用方传入：flow 用它们重新生成 accept 消息中的
> Task-T 提示词，缺少它们时 flow 不会应答该 propose。样例中两者都来自 `scenario_data`，因此保持
> 一致。

### 4.4 信息协商（information）

**① 触发条件。** 请求不完整 —— 缺少服务端必需的参数（样例中为区域）。样例的触发条件是
`content.param_missing` 校验错误，但这个错误码是样例的判断方式，不属于协议本身。

**② 参考实现。**

| 步骤 | SDK 调用 | 参考代码 |
| --- | --- | --- |
| 服务端 propose | `generate_negotiation_propose_prompt_from_text` | [`server_flow.py`](src/server/server_flow.py)（`_emit_negotiation_propose`） |
| 客户端决策 | `NegotiationResponseProvider.respond` | [`negotiation_response.py`](src/client/negotiation_response.py)、[`client_flow.py`](src/client/client_flow.py)（`_plan_negotiation_accept`） |
| 客户端 accept | `generate_negotiation_accept_prompt_from_text` | [`client_flow.py`](src/client/client_flow.py)（`_build_negotiation_accept_request`） |

```python
# 服务端 propose：把业务报文写成文本，再经 from-text API 渲染
propose = server.generate_negotiation_propose_prompt_from_text(
    propose_text,   # 缺失的参数，写成编号列表
    context,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
)

# 客户端决策：询问 provider 如何应答 propose
decision = provider.respond(propose_view, session)   # filled_params = 要补的取值

# 客户端 accept：把应答写成业务报文，再经 from-text API 渲染
accept_text = f"Accept. {_numbered_items(decision.filled_params.items())}"
accept = client.generate_negotiation_accept_prompt_from_text(
    accept_text,
    context,
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

propose 列出缺失的参数；accept 文本列出补全后的取值。

**③ 可复用的客户端代码。** 提示词渲染、`templateUri`/`negotiationContext` 的 metadata 组装、
会话处理与传输 —— `client_flow.py` 中除决策本身以外的全部内容。

**④ 客户端自行实现的代码。** provider 接入点背后的应答来源。

```python
class MyProvider:
    def respond(self, propose: NegotiationProposeView, session: NegotiationContext) -> NegotiationDecision:
        values = my_system.look_up(propose.kind, propose.prompt_text)   # client 侧的查询
        return NegotiationDecision(conclusion="accept", filled_params=values)
```

### 4.5 目标协商（target）

**① 触发条件。** 目标存在歧义：分时段保障速率目标里有两个时段重叠，重叠时段的保障速率无法
唯一确定。样例的触发条件是 `content.value_not_allowed` 校验错误。

**② 参考实现。**

| 步骤 | SDK 调用 | 参考代码 |
| --- | --- | --- |
| 服务端 propose | `generate_negotiation_propose_prompt_from_text` | [`server_flow.py`](src/server/server_flow.py)（`_emit_negotiation_propose`） |
| 客户端决策 | `NegotiationResponseProvider.respond` | [`negotiation_response.py`](src/client/negotiation_response.py)、[`client_flow.py`](src/client/client_flow.py)（`_plan_negotiation_accept`） |
| 客户端 accept | `generate_negotiation_accept_prompt_from_text` | [`client_flow.py`](src/client/client_flow.py)（`_build_negotiation_accept_request`） |

```python
# 服务端 propose：把业务报文写成文本，再经 from-text API 渲染
propose = server.generate_negotiation_propose_prompt_from_text(
    propose_text,   # 意图复述 + 待澄清请求
    context,
    TARGET_NEGOTIATION_PROPOSE_URI,
)

# 客户端决策：询问 provider 如何应答 propose
decision = provider.respond(propose_view, session)   # confirmed_intent = 修正后的目标

# 客户端 accept：把应答写成业务报文，再经 from-text API 渲染
accept_text = f"Accept. {decision.confirmed_intent}"
accept = client.generate_negotiation_accept_prompt_from_text(
    accept_text,
    context,
    TARGET_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

样例的 propose 请求澄清：服务端复述它理解的意图（`intent_understanding`），并询问需要调整哪个
时段（`request_for_clarification`）。accept 把修正后的目标放进 `confirmed_intent`。

**③ 可复用的客户端代码。** 与信息协商相同：渲染、metadata 组装、会话处理、传输。

**④ 客户端自行实现的代码。** 如何得出 `confirmed_intent`：样例从 `scenario_data` 读取，客户端
实现则从消解歧义的组件获取。

### 4.6 可行性协商（feasibility）

**① 触发条件。** 请求在当前条件下无法满足：保障速率无法支撑请求中所列站点的停电时长。样例的
触发条件是 `content.rule_violation` 校验错误。

**② 参考实现。**

| 步骤 | SDK 调用 | 参考代码 |
| --- | --- | --- |
| 服务端 propose | `generate_negotiation_propose_prompt_from_text` | [`server_flow.py`](src/server/server_flow.py)（`_emit_negotiation_propose`） |
| 客户端决策 | `NegotiationResponseProvider.respond` | [`negotiation_response.py`](src/client/negotiation_response.py)、[`client_flow.py`](src/client/client_flow.py)（`_plan_negotiation_accept`） |
| 客户端 accept | `generate_negotiation_accept_prompt_from_text` | [`client_flow.py`](src/client/client_flow.py)（`_build_negotiation_accept_request`） |

```python
# 服务端 propose：把业务报文写成文本，再经 from-text API 渲染
propose = server.generate_negotiation_propose_prompt_from_text(
    propose_text,   # 不可行结论 + 替代提案
    context,
    FEASIBILITY_NEGOTIATION_PROPOSE_URI,
)

# 客户端决策：询问 provider 如何应答 propose
decision = provider.respond(propose_view, session)   # feasibility_summary = 应答内容

# 客户端 accept：把应答写成业务报文，再经 from-text API 渲染
accept_text = f"Accept. {decision.feasibility_summary}"
accept = client.generate_negotiation_accept_prompt_from_text(
    accept_text,
    context,
    FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

样例的 propose 说明评估结论为该目标不可行，并附上替代方案
（`action=PROPOSE_ALTERNATIVE_ON_FAILURE` 加 `infeasibility_details_and_proposal`）；accept 说明
客户端同意的调整。

**③ 可复用的客户端代码。** 与信息协商相同：渲染、metadata 组装、会话处理、传输。

**④ 客户端自行实现的代码。** `feasibility_summary` 中的应答内容，即客户端对服务端提案的答复。

### 4.7 reject 与 abort

客户端可以用 `reject`（拒绝该 propose）或 `abort`（终止协商）回应 propose，而不只是 `accept`；
两者在协议中都是终态。本样例只实现了 accept 路径：reject/abort 决策只记录日志并结束客户端流程，
不发送报文，因此服务端不会收到通知。生产环境的客户端应发送对应的报文；A2A-T SDK 提供相应的生成
方法（`generate_negotiation_reject_prompt_from_data` / `..._from_text`，以及
`generate_negotiation_abort_prompt_from_data` / `..._from_text`）。

## 5. 自定义 Agent 开发说明

**归属**列标明各组件由哪一端负责。client 团队负责标注为 client 与 both 的行；server agent 负责
标注为 server 的行。

| demo 组成 | 归属 | demo 中的作用 | 保留还是替换 |
| --- | --- | --- | --- |
| 任务模板与参数 schema —— `ENERGY_SAVING_TEMPLATE_URI`（[`scenario_data.py`](src/client/scenario_data.py)）、[`src/common/task_schema.py`](src/common/task_schema.py) | 两端 | 客户端据此渲染请求、服务端据此校验的契约 | **替换**为双方约定的模板与 schema |
| mock LLM —— [`resources/mock_llm/`](resources/mock_llm) + `src/common/` 中按 schema 路由的 mock | 两端 | 脚本化的槽位抽取与内容校验应答，`A2AT_LLM_API_KEY` 为空时启用 | CI/离线运行**保留**；真实部署中两端各自替换为真实的 OpenAI 兼容端点 |
| 脚本化执行数据 —— [`src/server/constants_data.py`](src/server/constants_data.py) | server | demo 执行流式推送的脚本化进度步骤与意图报告 | server agent 经 `TaskExecutionService` 接入点**替换**（[`src/server/task_execution.py`](src/server/task_execution.py)）；它的服务装配后，demo 服务与数据即可删除 |
| 样例输入与脚本化应答 —— [`src/client/scenario_data.py`](src/client/scenario_data.py) | 两端（见下） | Task-T 自然语言输入、协商应答与预制 propose 文案 | 两侧都要**替换**：client 提供自己的任务意图，并经自己的 `NegotiationResponseProvider` 应答；server 把 `server_flow._negotiation_propose_text` 中的预制 propose 文案改为自己的业务措辞 |
| mock 注册中心 —— [`src/registry/`](src/registry)（端口 5001） | 两端 | AgentCard 注册（server）与查询发现（client） | **替换**为部署环境的注册中心，或绕过发现、直接从已知 AgentCard 构造 `a2a-sdk` 客户端 |
| 静态 webhook token —— `A2AT_SAMPLE_WEBHOOK_TOKEN`（默认 `demo-webhook-token`） | 两端 | `X-A2A-Notification-Token` 头中的共享静态 token | **替换**为生产鉴权（逐客户端密钥、轮换、TLS）；把接收器当作暴露在外的 HTTP 端点对待 |

> **`scenario_data` 同时喂两侧** —— 客户端的 Task-T 输入与脚本化协商应答（补全参数、确认意图、
> 可行性摘要）经 provider 接入点使用；服务端的 propose 文本也由同一模块构建：`_negotiation_propose_text`
> ([`server_flow.py`](src/server/server_flow.py)) 用其访问器填充 target/feasibility 的 propose
> 文案。只替换 provider 与执行服务，服务端仍会用 demo 的预制文案发起 propose —— 因此两侧都完成
> 替换前，`scenario_data.py` 不能删除。

## 6. 附录

### 6.1 Webhook 传输

两种传输投递相同的任务事件，区别在于客户端如何接收。默认是 SSE 流式：客户端保持流式
`message:stream` 调用，事件到达即消费。Webhook 推送通知面向无法保持流式连接的客户端（长时任务、间歇
在线的客户端），通过 `--transport webhook` 或 `A2AT_SAMPLE_TRANSPORT=webhook` 启用。

webhook 模式下的变化：客户端非阻塞发送（`return_immediately`），每个任务事件以 webhook POST 形式
送达客户端进程内嵌的接收器（[`src/common/webhook_receiver.py`](src/common/webhook_receiver.py) 的
`WebhookReceiver`），它额外绑定一个监听端口（`--webhook-port`，默认 9000）。服务端推送在
`X-A2A-Notification-Token` 头中携带 `A2AT_SAMPLE_WEBHOOK_TOKEN`（默认 `demo-webhook-token`）。
流程以报告 artifact 结束，另有 30 秒空闲兜底。协商往返在两种传输上的行为一致；任务创建前的
propose 是 Message 而非任务事件，因此客户端从非阻塞 RPC 应答中读取它。

- **两条注册路径。** 初始请求在请求上内联注册推送配置
  （`SendMessageConfiguration.task_push_notification_config`，即扁平的
  `TaskPushNotificationConfig(url, token)`），因为此时 task id 尚不存在。发送 accept 前，客户端流程
  经 `POST /tasks/{id}/pushNotificationConfigs`（a2a-sdk
  `BaseClient.create_task_push_notification_config`）重注册同一 URL/token —— 该端点路径在 task id
  已知后才可用。
- **没有服务端模式开关。** 服务端无条件装配推送机制（a2a-sdk `DefaultRequestHandler` 携带
  `InMemoryPushNotificationConfigStore` + `BasePushNotificationSender`，
  [`server_main.py`](src/server/server_main.py)）；SSE 模式下它只是闲置。
- **非阻塞路径。** a2a-sdk 客户端以 `ClientConfig(streaming=False)` 构建 —— 这是选择非阻塞
  `message:send` 路径的必要条件（仅设置 `return_immediately` 不会切换路径）。推送报文是与 SSE 事件
  同构的 StreamResponse JSON —— 因此协商检测、artifact 计数与展示逻辑与 SSE 共享。
- **Token 鉴权。** 接收器对每个 POST 校验 `X-A2A-Notification-Token`：不匹配回 `401`（事件被丢弃），
  畸形或无法识别的报文回 `400`。demo 的静态共享 token 是 demo 级的 —— 生产请使用 TLS 之上可轮换的
  逐客户端密钥。
- **结束信号。** 所有场景都以报告 artifact 结束：节能任务是值守类长时任务，报告发出后保持
  `WORKING`，服务端不会再发终态。校验失败（`REJECTED` 任务）或流异常（`FAILED` / `CANCELED`）仍会
  立即结束流程，summary 行以 `end=<state>` 记录。任务创建前的 propose 是 Message，不是推送的任务
  事件：webhook 模式下客户端从非阻塞 RPC 应答中读取它，并在 accept 上注册自己的内联推送配置。空闲
  兜底（`A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT`，默认 30 秒，每收到一个事件重置）打印
  `webhook-idle-timeout` 行后带着已收集的事件正常结束。`A2AT_SAMPLE_MAX_ARTIFACTS` 在 webhook
  模式下不生效。每个阶段的唯一 RPC 应答以 `webhook-rpc-reply` 记录一次，不重复计数。
  `stream-completed` 汇总统计经 webhook 收到的事件并追加 `transport=webhook`。

### 6.2 配置（a2a-t-sample/.env）

| 键 | 含义 |
| --- | --- |
| `A2AT_LANGUAGE` | `zh-CN` / `en-US`；决定自然语言输入与 mock/模板语言树 |
| `A2AT_SAMPLE_SCENARIO` | `default` / `negotiation`；样例场景变体（与 `--scenario` 语义相同） |
| `A2AT_SAMPLE_NEGOTIATION_KIND` | `information` / `target` / `feasibility`（默认 `information`）；协商场景输入，与 `--negotiation` 语义相同 |
| `A2AT_SAMPLE_TRANSPORT` | `sse` / `webhook`（默认 `sse`）；客户端事件传输方式，与 `--transport` 语义相同 |
| `A2AT_SAMPLE_WEBHOOK_PORT` | 内嵌 webhook 接收器的绑定端口（默认 `9000`；`0` = 操作系统分配临时端口） |
| `A2AT_SAMPLE_WEBHOOK_TOKEN` | 服务端在 `X-A2A-Notification-Token` 头中必须携带的推送 token（默认 `demo-webhook-token`）；不匹配回 `401` |
| `A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT` | 空闲兜底超时（秒）：webhook 流程超过该时长未收到推送即结束（默认 `30`） |
| `A2AT_LLM_API_KEY` | 留空 ⇒ mock LLM；否则为 OpenAI 兼容的 key |
| `A2AT_LLM_PROVIDER` / `A2AT_LLM_MODEL` / `A2AT_LLM_BASE_URL` | LLM 端点配置 |
| `A2AT_LLM_TIMEOUT_SECONDS` | HTTP 客户端超时（秒，默认 60） |
| `A2AT_SAMPLE_HOST` / `A2AT_SAMPLE_PORT` | 手动 `python -m` 路径的服务端绑定（默认 `127.0.0.1:8000`） |
| `REGISTRY_CENTER_HOST` / `REGISTRY_CENTER_PORT` | 手动 `python -m` 路径的注册中心绑定（默认 `127.0.0.1:5001`） |
| `A2AT_SAMPLE_DEBUG` | `true` 时打印完整的 LLM 请求/响应报文 |
| `A2AT_SAMPLE_MAX_ARTIFACTS`（进程环境变量，非 `.env`） | 收到 N 个 artifact 后停止（默认 0 = 不限制） |

说明：`run_demo.py` 的就绪检查固定使用 `5001` / `8000`，不读取 `A2AT_SAMPLE_PORT` /
`REGISTRY_CENTER_PORT` —— 这两对仅在手动 `python -m` 路径下设置。服务端的进度与报告文案始终是
英文，与 `--language` 无关。该开关切换客户端输入、模板、mock 应答与协商消息文本。模板、参数
schema、AgentCard 与自然语言输入都是本样例特有的；替换后的模板必须属于 `Task-T` 扩展且使用 `v1`
版本。SDK 不负责加载远程 prompt 资源；本处的注册中心是样例专用的 mock，用于 AgentCard 发现，
不是 SDK 能力。

### 6.3 分别启动两端

把 `PYTHONPATH` 指向样例的 `src/` 目录，在各自终端分别启动（`.env` 从当前目录读取；缺失 `.env` 对
客户端与服务端而言会**直接报错**）。启动顺序为注册中心 → 服务端 → 客户端（客户端要从注册中心发现
服务端的 AgentCard）。

以下命令在仓库根目录执行：

```bash
# bash / zsh
cd a2a-t-sample
export PYTHONPATH="$PWD/ran-energy-saving/src"
uv run --no-sync python -m registry.registry_main    # 终端一：注册中心（5001）
uv run --no-sync python -m server.server_main         # 终端二：服务端（8000）
uv run --no-sync python -m client.client_main         # 终端三：客户端
```

```powershell
# PowerShell
cd a2a-t-sample
$env:PYTHONPATH = "$pwd\ran-energy-saving\src"
uv run --no-sync python -m registry.registry_main
uv run --no-sync python -m server.server_main
uv run --no-sync python -m client.client_main
```

运行语言取自 `a2a-t-sample/.env` 的 `A2AT_LANGUAGE`（`env.example` 默认是 `zh-CN`）；`run_demo.py`
每次运行用 `--language` 覆盖（默认 `en-US`）。

### 6.4 mock LLM 机制

`A2AT_LLM_API_KEY` 为空时，样例安装随包的 mock LLM
([`src/common/mock_llm.py`](src/common/mock_llm.py))：它按**输出 schema 签名**
（`route_structured_call`）路由每个结构化调用，回放
[`resources/mock_llm/{zh-CN,en-US}/`](resources/mock_llm) 中脚本化的槽位抽取与内容校验应答。

### 6.5 支持的参数（`run_demo.py`）

| 参数 | 作用 |
| --- | --- |
| `--language {en-US,zh-CN}` | 运行语言（默认 `en-US`） |
| `--scenario {default,negotiation}` | 场景变体（默认：`default`；`negotiation` 运行缺失 region 的信息协商） |
| `--negotiation {information,target,feasibility}` | 协商类型：`information`（默认；缺失 region）、`target`（分时段保障速率目标中两个时段重叠）或 `feasibility`（保障速率无法支撑 18 小时停电）。`target` 与 `feasibility` 可单独触发协商；`information` 还需配合 `--scenario negotiation`。该参数只选择客户端输入；服务端依据内容校验错误码决定类型 |
| `--transport {sse,webhook}` | 客户端的事件传输方式（默认 `sse`）；见 [§6.1](#61-webhook-传输) |
| `--webhook-port N` | `--transport webhook` 时内嵌 webhook 接收器的绑定端口（默认 9000；`0` 表示由操作系统分配临时端口） |
| `--keep-alive` | 保留注册中心/服务端不停止；之后用 `--stop` 关闭 |
| `--keep-logs` | 保留临时运行目录（日志 / `.env`），位于 `<temp>/a2at-ran-energy-saving/` |
| `--stop` | 停止之前 `--keep-alive` 留下的后台进程并退出 |
| `--timeout N` | 等待每个端口的秒数（默认 30） |
