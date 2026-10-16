# RAN 节能（Task-T）样例

## 目录

- [概览](#概览)
  - [目录结构](#目录结构)
- [如何运行](#如何运行)
  - [分别启动两端](#分别启动两端)
  - [配置](#配置)
- [如何使用 A2A-T SDK](#如何使用-a2a-t-sdk)
  - [客户端：生成并发送 Task-T prompt](#客户端生成并发送-task-t-prompt)
  - [服务端：校验、抽取并流式推送](#服务端校验抽取并流式推送)
- [协商场景（Negotiation-T）](#协商场景negotiation-t)
- [Webhook 传输（A2A 推送通知）](#webhook-传输a2a-推送通知)

## 概览

本样例是 A2A-T Python SDK 的一个完整 **Task-T** 示例。它包含：面向 RAN 节能任务的 A2A-T
**客户端**与**服务端**，以及用于发现 AgentCard 的**注册中心**。

样例按下列步骤交互：

1. **构造请求**——客户端调用 A2A-T 客户端 SDK 的 `generate_task_prompt_from_text`，把原始自然语言输入
   转换为 Task-T 任务请求（渲染好的 prompt）。
2. **发送请求**——客户端把任务请求发给服务端；渲染好的 prompt 放在 `message.metadata` 中。
3. **校验**——服务端从 `message.metadata` 取出 prompt，调用 A2A-T 服务端 SDK 的
   `validate_task_prompt_and_data_filling` 做校验并抽取参数。
4. **执行并上报**——服务端把抽取出的参数交给业务逻辑，执行节能任务，生成状态更新与节能报告并发送给
   客户端。
5. **接收与呈现**——客户端消费流式事件：进度取自状态更新消息的文本，报告正文取自
   `artifact.metadata`，并呈现两者。

### 目录结构

| 路径 | 作用 |
| --- | --- |
| `src/client/` | 客户端：注册中心发现 → `generate_task_prompt_from_text` → 消费流 |
| `src/server/` | 服务端：`validate_task_prompt_and_data_filling` → 流式推送进度与意图报告 |
| `src/registry/` | mock 注册中心（AgentCard 注册/查询，端口 5001） |
| `src/common/` | 共享适配器 + 按 schema 路由的 mock LLM |
| `resources/mock_llm/{zh-CN,en-US}/` | 脚本化的槽位抽取与内容校验应答 |
| `run_demo.py` | 跨平台、非阻塞启动器（后台注册中心/服务端，自动清理） |

## 如何运行

> **前置条件：** Python（使用仓库 `.venv`）与 [`uv`](https://docs.astral.sh/uv/)。

先在仓库根目录和 `a2a-t-sample/` 各执行一次：

```bash
# 仓库根目录：创建 .venv 并安装 SDK
uv sync --dev

# a2a-t-sample/（存放 .env 的目录）
cd a2a-t-sample
cp env.example .env
uv pip install -r requirements.txt
```

然后用一条跨平台命令运行样例：

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py --language zh-CN   # 中文（默认是 en-US）
```

`run_demo.py` 会把注册中心和服务端作为**后台子进程**启动，并在前台运行客户端。两端输出会**实时交错**
打印到终端（日志同时写入运行目录），随后停止后台进程。请使用装有样例依赖的解释器（仓库 `.venv`），
例如 Windows 下 `..\..\.venv\Scripts\python.exe run_demo.py`。

| 参数 | 作用 |
| --- | --- |
| `--language {en-US,zh-CN}` | 运行语言（默认 `en-US`） |
| `--scenario {default,negotiation}` | 场景变体（默认 `default`；`negotiation` 运行缺失 region 的信息协商，见[协商场景](#协商场景negotiation-t)） |
| `--negotiation {information,target,feasibility}` | 协商类型，选择客户端场景输入（默认 `information` = 缺失 region；`target` = 100Mbps 速率超出小区能力；`feasibility` = 30% 节能目标与 50Mbps 速率目标冲突）。`information` 仍受 `--scenario` 开关控制（只有 `--scenario negotiation` 运行缺失 region 流程）；`target`/`feasibility` 无需该开关即可触发。该参数只选择输入——服务端依据内容校验错误码数据驱动地分派，见[协商场景](#协商场景negotiation-t) |
| `--channel {fromData,fromText}` | 协商 propose/accept 消息的生成通道（默认 `fromData`；`fromText` 每条生成的消息多一次抽取 LLM 调用，见[协商场景](#协商场景negotiation-t)） |
| `--transport {sse,webhook}` | 客户端的事件传输方式（默认 `sse`）：`sse` 消费流式响应；`webhook` 在客户端内嵌推送接收器，以 webhook POST 接收全部任务事件，见[Webhook 传输](#webhook-传输a2a-推送通知) |
| `--webhook-port N` | `--transport webhook` 时内嵌 webhook 接收器的绑定端口（默认 9000；`0` 表示由操作系统分配临时端口），见[Webhook 传输](#webhook-传输a2a-推送通知) |
| `--keep-alive` | 保留注册中心/服务端不停止；之后用 `--stop` 关闭 |
| `--keep-logs` | 保留临时运行目录（日志 / `.env`），位于 `<temp>/a2at-ran-energy-saving/` |
| `--stop` | 停止之前 `--keep-alive` 留下的后台进程并退出 |
| `--timeout N` | 等待每个端口的秒数（默认 30） |

> 服务端的进度与报告文案是内置的英文，与 `--language` 无关；该开关只切换客户端输入、模板与 mock 应答。

**预期输出**（已裁剪）：

```text
[client] a2a-event: {"task": {"id": "...", "status": {"state": "TASK_STATE_SUBMITTED", ...}}}
[client] a2a-event: {"statusUpdate": {"status": {"state": "TASK_STATE_WORKING", ...}}}
...
[client] a2a-event: {"artifactUpdate": {"artifact": {"name": "energy saving intent report", ...}}}
[client] stream-completed: events=12 artifacts=1
```

### 分别启动两端

需要把服务端和客户端放在不同终端运行时（例如托管你自己的服务端，或分别查看两端日志），把
`PYTHONPATH` 指向用例的 `src/` 后分别启动即可。`.env` 从当前目录读取，缺失 `.env` 对客户端/服务端
而言会**直接报错**。

> [!IMPORTANT]
> 先启动注册中心与服务端；客户端要从注册中心发现服务端的 AgentCard，因此它们必须已在运行。

运行语言取自 `a2a-t-sample/.env` 的 `A2AT_LANGUAGE`（`env.example` 默认是 `zh-CN`）；`run_demo.py`
每次运行用 `--language` 覆盖（默认 `en-US`）。

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

### 配置

本样例用到的配置（来自 `a2a-t-sample/.env`）：

| 键 | 含义 |
| --- | --- |
| `A2AT_LANGUAGE` | `zh-CN` / `en-US`；决定自然语言输入与 mock/模板语言树 |
| `A2AT_SAMPLE_SCENARIO` | `default` / `negotiation`；选择样例场景变体（`negotiation` = 缺失 region 的信息协商，见[协商场景](#协商场景negotiation-t)） |
| `A2AT_SAMPLE_NEGOTIATION_KIND` | `information` / `target` / `feasibility`（默认 `information`）；选择协商场景输入，与 `--negotiation` 语义相同 |
| `A2AT_SAMPLE_CHANNEL` | `data` / `text`（默认 `data`）；协商 propose/accept 消息的生成通道，与 `--channel` 语义相同 |
| `A2AT_SAMPLE_TRANSPORT` | `sse` / `webhook`（默认 `sse`）；客户端的事件传输方式，与 `--transport` 语义相同 |
| `A2AT_SAMPLE_WEBHOOK_PORT` | 内嵌 webhook 接收器的绑定端口（默认 `9000`；`0` = 操作系统分配临时端口），`A2AT_SAMPLE_TRANSPORT=webhook` 时使用 |
| `A2AT_SAMPLE_WEBHOOK_TOKEN` | 服务端在 `X-A2A-Notification-Token` 头中必须携带的推送 token（默认 `demo-webhook-token`）；不匹配回 `401` |
| `A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT` | 空闲兜底超时（秒）：webhook 流程超过该时长未收到推送即结束（默认 `30`） |
| `A2AT_LLM_API_KEY` | 留空 ⇒ mock LLM；否则为 OpenAI 兼容的 key |
| `A2AT_LLM_PROVIDER` / `A2AT_LLM_MODEL` / `A2AT_LLM_BASE_URL` | LLM 端点配置 |
| `A2AT_LLM_TIMEOUT_SECONDS` | HTTP 客户端超时（秒，默认 60） |
| `A2AT_SAMPLE_HOST` / `A2AT_SAMPLE_PORT` | 手动 `python -m` 路径的服务端绑定（默认 `127.0.0.1:8000`） |
| `REGISTRY_CENTER_HOST` / `REGISTRY_CENTER_PORT` | 手动 `python -m` 路径的注册中心绑定（默认 `127.0.0.1:5001`） |
| `A2AT_SAMPLE_DEBUG` | `true` 时打印完整的 LLM 请求/响应报文 |

环境变量（从进程环境读取，非 `.env`）：

| 键 | 含义 |
| --- | --- |
| `A2AT_SAMPLE_MAX_ARTIFACTS` | 收到 N 个 artifact 后停止（默认 0 = 不限制） |

说明：

- `run_demo.py` 的就绪检查固定使用 `5001` / `8000`，不读取 `A2AT_SAMPLE_PORT` /
  `REGISTRY_CENTER_PORT`；这两对仅在手动 `python -m` 路径下设置。
- 上面这些值（`template_uri`、参数 schema、AgentCard、自然语言输入）都是**本样例特有**的，请替换为
  你自己的。模板必须属于 `Task-T` 扩展且使用 `v1` 版本。
- 内置资源与语言覆盖有限；远程 prompt 资源加载不属于 SDK；本处的注册中心是样例侧的 mock，用于
  AgentCard 发现，不是 SDK 能力。

## 如何使用 A2A-T SDK

### 客户端：生成并发送 Task-T prompt

用 A2A-T SDK 把你的自然语言输入渲染成 Task-T prompt ——
[`client_flow.py:891`](src/client/client_flow.py#L891)：

```python
prompt_content = prompt_client.generate_task_prompt_from_text(input_text, ENERGY_SAVING_TEMPLATE_URI)
prompt_text, extension_uri = _require_prompt_text(prompt_content)
```

它返回 `MetadataContent`（`prompt_text`、`extension_uri`、`template_uri`、`build_metadata_content()`），
并会抛出 `PromptGenerationError`（错误码如 `template.not_found`、`slot.not_provided`、
`llm.not_configured`、`input.text_too_long`）。

把 prompt 放进消息 metadata，用 `a2a-sdk` 发送（流式）——
[`client_flow.py:905`](src/client/client_flow.py#L905)：

```python
request = SendMessageRequest()
request.message.message_id = str(uuid.uuid4())
request.message.role = Role.ROLE_USER
request.message.parts.add().text = _build_request_metadata(initial_input)
request.message.metadata[extension_uri] = prompt_text
request.message.metadata["templateUri"] = (
    getattr(prompt_content, "template_uri", None) or ENERGY_SAVING_TEMPLATE_URI
)

call_context = ClientCallContext(
    service_parameters={"A2A-Extensions": extension_uri},
)

async for stream_response in a2a_client.send_message(request, context=call_context):
    ...
```

这段之前的目标 AgentCard 发现与 `a2a-sdk` 客户端构建见
[`client_main.py`](src/client/client_main.py)。

### 服务端：校验、抽取并流式推送

从客户端发来的消息 metadata 中读取 A2A-T 报文 ——
[`server_flow.py:75`](src/server/server_flow.py#L75)：

```python
def _extract_prompt_text(request_context: RequestContext) -> str:
    """Extract the prompt text from metadata under the Task-T extension URI."""
    if request_context.message is None or request_context.message.metadata is None:
        raise ValueError("Expected message metadata for Task-T prompt")
    metadata = MessageToDict(request_context.message.metadata)
    return str(metadata.get(TASK_T_EXTENSION_URI, ""))
```

用 A2A-T SDK 校验 prompt 并抽取参数 ——
[`server_flow.py:455`](src/server/server_flow.py#L455)：

```python
filled_params = prompt_server.validate_task_prompt_and_data_filling(
    prompt=prompt_text,
    schema=TASK_PARAM_SCHEMA,
    template_uri=ENERGY_SAVING_TEMPLATE_URI,
)
```

`schema` 由**调用方**提供：它声明要抽取哪些参数；`filled_params.data` 是合并后的参数 map（数组型
schema 则为 list）。prompt 被拒绝时会抛出 `ContentValidationError`（错误码如
`negotiation.semantic_rejected`、`llm.invocation_failed`、`template.not_found`），并携带
`exc.errors`，含每槽位的 `slot_name` / `code` / `message`；样例将其映射为 `TASK_STATE_REJECTED`
而不是让服务崩溃 ——
[`server_flow.py:482`](src/server/server_flow.py#L482)：

```python
if validation_failure is not None:
    await emit_status_update(
        request_context=request_context,
        event_queue=event_queue,
        context_id=resolved_context_id,
        task_id=resolved_task_id,
        state=TaskState.TASK_STATE_REJECTED,
        text=f"Prompt validation failed: {validation_failure}",
    )
    ...
    return
```

整条流程运行在 `a2a-sdk` 的 `AgentExecutor` 中，它把执行委托给 `execute_server_flow` ——
[`server_main.py:59`](src/server/server_main.py#L59)：

```python
async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
    kwargs = {
        "request_context": context,
        "event_queue": event_queue,
        "prompt_server": self._prompt_server,
        "log_sink": self._log_sink,
    }
    await self._execute_flow(**kwargs)
```

`a2a-sdk` 应用装配（routes、task store）见
[`src/server/server_main.py`](src/server/server_main.py)。

## 协商场景（Negotiation-T）

除上述默认流程外，本样例还提供多个协商场景，在同一条流式链路上演示 **Negotiation-T** 协商。支持三类
协商类型，由服务端产生的内容校验错误码数据驱动地选择：

> 要把协商集成到您自己的系统？参见 [A2A-T 协商集成指南](../../docs/zh/协商集成指南.md)，了解
> SDK/客户职责分界、七步集成路径与 demo 到生产的替换清单。

| 内容校验错误码 | 协商类型 | 第 1 轮 propose → 第 2 轮 accept |
| --- | --- | --- |
| `content.param_missing` | 信息协商（information） | 服务端索要缺失参数（`region`）；客户端补全后应答 |
| `content.value_not_allowed` | 目标协商（target） | 服务端提出澄清后的目标请求确认（确认请求轮）；客户端以确认后的意图应答 |
| `content.semantic_conflict` | 可行性协商（feasibility） | 服务端请求对冲突内容做可行性评估（`action=REQUEST_FEASIBILITY_EVALUATION`，携带 `contents_to_evaluate`）；客户端以可行性摘要应答 |

> 该映射是样例层的约定。报文是自描述的：每个 propose 携带各自的 `templateUri`
> （`Negotiation-T/{information,target,feasibility}-negotiation/propose/v1`），客户端基于该 URI
> 分派 accept。

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py --scenario negotiation                         # 缺失 region 的输入；信息协商
python run_demo.py --negotiation target                           # 100Mbps 速率超出小区能力；目标协商
python run_demo.py --negotiation feasibility --channel fromText   # 30% 节能目标与 50Mbps 速率冲突；可行性协商
```

各参数的默认值（`default` 场景、`information` 类型、`fromData` 通道）保持上文流程不变；协商类型与
通道也可以通过 `.env` 中的 `A2AT_SAMPLE_NEGOTIATION_KIND` / `A2AT_SAMPLE_CHANNEL` 选择
（见[配置](#配置)）。

**第一轮——服务端发起 propose。** 客户端用 `generate_task_prompt_from_text`
（[`client_flow.py:891`](src/client/client_flow.py#L891)）从自然语言输入生成 Task-T prompt。服务端用
`validate_task_prompt_and_data_filling`（[`server_flow.py:455`](src/server/server_flow.py#L455)）校验后
得到按槽位命名的拒绝；错误码选择协商类型（见上表），服务端用
`generate_negotiation_propose_prompt_from_data`
（[`server_flow.py:285`](src/server/server_flow.py#L285)）从类型化内容（`a2a_t.negotiation.content.models`
中的 `InformationProposeContent` / `TargetProposeContent` / `FeasibilityProposeContent`）渲染对应的
Negotiation-T propose——fromData 通道下渲染不涉及 LLM 调用——并发出 `TASK_STATE_INPUT_REQUIRED`，
propose 放在该状态消息的 metadata 中（`build_metadata_content()`）。

**第二轮——客户端 accept。** 客户端从 INPUT_REQUIRED 的 metadata 中读取 propose 并解析
`negotiationContext` 会话，然后基于 propose 的 `templateUri` 分派应答的 ending 内容——信息协商：
`InformationEndingContent`（补全后的应答项）；目标协商：`TargetEndingContent`（`ACCEPT` +
`confirmed_intent`）；可行性协商：`FeasibilityEndingContent`（`ACCEPT` + `feasibility_summary`）。
客户端用 `generate_task_prompt_from_data_with_schema`
（[`client_flow.py:686`](src/client/client_flow.py#L686)）从补全后的参数重新生成 Task-T prompt，用
`generate_negotiation_accept_prompt_from_data`
（[`client_flow.py:701`](src/client/client_flow.py#L701)）渲染 accept，然后把两个 prompt 一并放进
消息 metadata 重新发送同一任务。服务端检测到 Negotiation-T URI 即视为协商续轮，校验补全后的
prompt，流式推送 UC1 进度与报告 artifact，最后以 `TASK_STATE_COMPLETED` 结束任务——协商运行到此
结束，而默认流程保持长连接运行。

**fromText 通道。** 选择 `--channel fromText`（`A2AT_SAMPLE_CHANNEL=text`）时，每条协商消息先渲染为
自然语言文本，再由 SDK 从文本中抽取结构化内容：服务端调用
`generate_negotiation_propose_prompt_from_text`
（[`server_flow.py:282`](src/server/server_flow.py#L282)），客户端调用
`generate_negotiation_accept_prompt_from_text`
（[`client_flow.py:695`](src/client/client_flow.py#L695)）——每条生成的协商消息多一次抽取 LLM 调用
（使用内置 mock LLM 时表现为额外的 `llm-mock` 日志行）。报文格式与 `fromData` 完全一致。已知的 mock 伪影：`--scenario negotiation --channel fromText` 时 information propose 携带预置的 2 条抽取项列表（`fromData` 携带 1 条缺失项列表）；accept 与报文约定保持等价。各类型的
消息形态：目标协商的 propose 携带协商摘要与确认请求，accept 携带确认后的意图；可行性协商的 propose
携带评估请求与 `contents_to_evaluate` 条目，accept 携带可行性摘要。

> **语言行为：** 协商消息文本（propose 摘要、确认请求、可行性条目、accept 摘要）跟随运行语言
> （`--language`）；UC1 进度与报告文案不随语言变化，保持内置英文。

报文约定：

| 内容 | 位置 |
| --- | --- |
| propose（第 1 轮）/ accept（第 2 轮）文本 | 消息 `metadata` 中 Negotiation-T 扩展 URI 之下（`https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1`） |
| 协商会话 | 嵌套的 `negotiationContext` metadata map（`id` / `round` / `maxRounds` / `performative`） |
| 第 2 轮续轮信号 | 第 2 轮消息 `metadata` 中出现 Negotiation-T URI；`task_id` / `context_id` 沿用第 1 轮 |
| 第 2 轮 `A2A-Extensions` 头 | 同时携带两个扩展 URI（Task-T **与** Negotiation-T），逗号拼接 |

**预期输出**（已裁剪）：

```text
[client] a2a-event: {"task": {"id": "...", "status": {"state": "TASK_STATE_SUBMITTED", ...}}}
[client] a2a-event: {"statusUpdate": {"status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"metadata": {"negotiationContext": {"round": 1.0, ..., "performative": "PROPOSE"}, ".../extensions/Negotiation-T/v1": "## Information Negotiation ...", "templateUri": "Negotiation-T/information-negotiation/propose/v1"}}}}}
[client] negotiation-accept: generated (round=2) template_uri=Negotiation-T/information-negotiation/accept-reject/v1 items=[('region', 'Songshanhu Administration Committee')]
[client] a2a-send-message: round=2 streaming text_part=create ran energy saving task metadata_keys=[<Task-T/v1>, <Negotiation-T/v1>, templateUri, negotiationContext] header[A2A-Extensions]=<Negotiation-T/v1>,<Task-T/v1>
[client] a2a-event: {"artifactUpdate": {"artifact": {"name": "energy saving intent report", ...}}}
[client] a2a-event: {"statusUpdate": {"status": {"state": "TASK_STATE_COMPLETED", ...}}}
[client] stream-completed: events=14 artifacts=1
```

## Webhook 传输（A2A 推送通知）

除上述 SSE 流式消费外，样例还支持 **webhook 传输**（`--transport webhook`、`A2AT_SAMPLE_TRANSPORT=webhook`）——
即 A2A **推送通知（Push Notification）**模式，面向不保持流式长连接的客户端：

- **纯回调模式。** 客户端不消费 SSE：发送非阻塞 `message:send`（`return_immediately=True`），**全部**任务事件以
  webhook POST 的形式送达客户端进程内嵌的接收器：`client_main` 在流程开始前于客户端自己的 asyncio loop 上启动
  Starlette/uvicorn [`WebhookReceiver`](src/common/webhook_receiver.py)，流程结束后停止
  （[`client_main.py`](src/client/client_main.py)）。推送报文是与 SSE 事件同构的 StreamResponse JSON（a2a-sdk
  发送 `to_stream_response(event)`），因此协商检测、artifact 计数与展示逻辑与 SSE 路径完全共享。
- **两条注册路径。** 第 1 轮在请求内联注册 push 配置（`SendMessageConfiguration.task_push_notification_config`，
  即扁平的 `TaskPushNotificationConfig(url, token)`——此时 task id 尚未知）；第 2 轮在发送协商 accept 前，经
  `POST /tasks/{id}/pushNotificationConfigs` REST 端点（a2a-sdk `BaseClient.create_task_push_notification_config`）
  重注册同一 URL/token。服务端**无条件**装配推送机制——a2a-sdk `DefaultRequestHandler` 携带
  `InMemoryPushNotificationConfigStore` + `BasePushNotificationSender`
  （[`server_main.py`](src/server/server_main.py)）——SSE 模式下它只是闲置，不存在服务端模式开关。
- **Token 鉴权。** 服务端推送发送方在每个 POST 的 `X-A2A-Notification-Token` 头中携带
  `A2AT_SAMPLE_WEBHOOK_TOKEN`（默认 `demo-webhook-token`）；接收器校验该头，不匹配回 `401`（事件被丢弃）；
  格式错误或无法识别的报文回 `400`（两条拒绝路径都属于接收器契约）。
- **结束信号。** 任一 SDK 终态（`TASK_STATE_COMPLETED` / `FAILED` / `CANCELED` / `REJECTED`）都会在两个阶段
  立即结束流程——协商场景正常以 `COMPLETED` 结束，校验失败或流异常则以 `REJECTED` / `FAILED` 直接结束，
  而不是一直挂到空闲兜底——summary 行以 `end=<state>` 记录终态结束。默认场景仍以报告 artifact 结束；
  `INPUT_REQUIRED`（协商 propose）只是第 1 轮的中断点，不是流程结束。空闲超时
  （`A2AT_SAMPLE_WEBHOOK_IDLE_TIMEOUT`，默认 30 秒，每收到一个事件重置）是兜底：打印
  `webhook-idle-timeout` 诊断行后，带着已收集的事件正常结束流程。`A2AT_SAMPLE_MAX_ARTIFACTS` 在 webhook
  模式下不生效——artifact 结束信号本身就关闭默认流程（没有流截断上限）。

webhook 模式下客户端内嵌接收器，因此除注册中心/服务端端口外**多绑定一个监听端口**——
`A2AT_SAMPLE_WEBHOOK_PORT`（默认 9000；`--webhook-port N`，`0` = 操作系统分配临时端口）。a2a 客户端以
`ClientConfig(streaming=False)` 构建——这是选择非阻塞 `message:send` 路径的必要条件（仅设置
`return_immediately` 不会切换路径）。语言行为沿用上文协商章节的说明。每一轮的唯一 RPC 应答（首个 Task）以
`webhook-rpc-reply` 记录一次、不重复计数：`stream-completed` 汇总统计经 webhook 收到的事件（与 SSE 口径
一致），并追加 `transport=webhook`（终态结束再追加 `end=<state>`，空闲兜底结束则追加 `idle-timeout=<N>s`）。

```bash
cd a2a-t-sample/ran-energy-saving
python run_demo.py --transport webhook                          # 默认场景；以报告 artifact 结束
python run_demo.py --transport webhook --scenario negotiation   # 信息协商；以 COMPLETED 结束
python run_demo.py --transport webhook --negotiation target     # webhook 上的目标协商
```

**预期输出**（已裁剪）：

```text
[webhook-receiver] listening: url=http://127.0.0.1:9000/webhook
[client] webhook-mode: push_url=http://127.0.0.1:9000/webhook idle_timeout=30.0s (client streaming=false, events via push notifications)
[client] a2a-send-message: round=1 webhook text_part=create ran energy saving task metadata_keys=[<Task-T/v1>, templateUri] return_immediately=true push_registration=inline
[client] webhook-event: {"statusUpdate": {..., "status": {"state": "TASK_STATE_INPUT_REQUIRED", "message": {"metadata": {"negotiationContext": {"round": 1.0, ..., "performative": "PROPOSE"}, ".../extensions/Negotiation-T/v1": "## Information Negotiation ...", "templateUri": "Negotiation-T/information-negotiation/propose/v1"}}}}}
[client] negotiation-accept: generated (round=2) template_uri=Negotiation-T/information-negotiation/accept-reject/v1 items=[('region', 'Songshanhu Administration Committee')]
[client] webhook-push-reregister: task_id=... url=http://127.0.0.1:9000/webhook method=create_task_push_notification_config token=***
[client] a2a-send-message: round=2 webhook text_part=create ran energy saving task metadata_keys=[<Task-T/v1>, <Negotiation-T/v1>, templateUri, negotiationContext] header[A2A-Extensions]=<Negotiation-T/v1>,<Task-T/v1> return_immediately=true push_registration=endpoint
[client] webhook-event: {"statusUpdate": {..., "status": {"state": "TASK_STATE_COMPLETED", ...}}}
[client] stream-completed: events=14 artifacts=1 transport=webhook end=TASK_STATE_COMPLETED
[webhook-receiver] stopped: url=http://127.0.0.1:9000/webhook
```
