# 1 API参考

## 1.1 简介

A2A-T SDK 对外仅提供两个入口：客户端入口 `A2ATClient`（提示词生成、协商消息生成与校验）与服务端入口 `A2ATServer`（提示词校验、协商消息生成与校验），API 分类总览见下。

- **API 总览**

| 分类 | API定义 | Client/Server | API含义 | 是否涉及LLM |
| ---- | ------- | -------- | ------- |---------|
| Negotiation-T | `generate_negotiation_propose_prompt_from_text` | A2A-T Client / A2A-T Server | 从自然语言文本生成协商发起（propose）报文 | 是 |
| Negotiation-T | `generate_negotiation_accept_prompt_from_text` | A2A-T Client / A2A-T Server | 从自然语言文本生成协商接受（accept）报文 | 是 |
| Negotiation-T | `generate_negotiation_reject_prompt_from_text` | A2A-T Client / A2A-T Server | 从自然语言文本生成协商拒绝（reject）报文 | 是 |
| Negotiation-T | `generate_negotiation_abort_prompt_from_text` | A2A-T Client / A2A-T Server | 从自然语言文本生成协商中止（abort）报文 | 是 |
| Negotiation-T | `generate_negotiation_propose_prompt_from_data` | A2A-T Client / A2A-T Server | 从结构化数据确定性生成协商发起报文 | 否 |
| Negotiation-T | `generate_negotiation_accept_prompt_from_data` | A2A-T Client / A2A-T Server | 从结构化数据确定性生成协商接受报文 | 否 |
| Negotiation-T | `generate_negotiation_reject_prompt_from_data` | A2A-T Client / A2A-T Server | 从结构化数据确定性生成协商拒绝报文 | 否 |
| Negotiation-T | `generate_negotiation_abort_prompt_from_data` | A2A-T Client / A2A-T Server | 从结构化数据确定性生成协商中止报文 | 否 |
| Negotiation-T | `validate_propose_prompt_and_data_filling` | A2A-T Client / A2A-T Server | 校验协商发起报文合规性并按Schema提取参数 | 是 |
| Negotiation-T | `validate_accept_prompt_and_data_filling` | A2A-T Client / A2A-T Server | 校验协商接受报文合规性并按Schema提取参数 | 是 |
| Negotiation-T | `validate_reject_prompt_and_data_filling` | A2A-T Client / A2A-T Server | 校验协商拒绝报文合规性并按Schema提取参数 | 是 |
| Negotiation-T | `validate_abort_prompt_and_data_filling` | A2A-T Client / A2A-T Server | 校验协商中止报文合规性并按Schema提取参数 | 是 |
| Task-T | `generate_task_prompt_from_text` | A2A-T Client | 从自然语言文本按指定Task-T模板生成任务提示词（跳过场景识别） | 是 |
| Task-T | `generate_task_prompt_from_data_with_schema` | A2A-T Client | 从结构化数据+语义Schema按指定Task-T模板生成任务提示词 | 是 |
| Task-T | `validate_task_prompt_and_data_filling` | A2A-T Server | 校验Task-T任务提示词合规性并按Schema提取参数 | 是 |
| Notification-T | `generate_notification_prompt_from_text` | A2A-T Client | 从自然语言文本按指定Notification-T模板生成通知订阅提示词 | 是 |
| Notification-T | `generate_notification_prompt_from_data_with_schema` | A2A-T Client | 从结构化数据+语义Schema按指定Notification-T模板生成通知订阅提示词 | 是 |
| Notification-T | `validate_notification_prompt_and_data_filling` | A2A-T Server | 校验Notification-T提示词合规性并按Schema提取参数 | 是 |
| Authorization-T | `generate_auth_prompt_from_text` | A2A-T Client | 从自然语言文本按指定Authorization-T模板生成授权提示词 | 是 |
| Authorization-T | `generate_auth_prompt_from_data_with_schema` | A2A-T Client | 从结构化数据+语义Schema按指定Authorization-T模板生成授权提示词 | 是 |
| Authorization-T | `validate_auth_prompt_and_data_filling` | A2A-T Server | 校验Authorization-T提示词合规性并按Schema提取参数 | 是 |
| 通用接口 | `generate_task_prompt` | A2A-T Client | 从自然语言或结构化输入经场景识别生成任务提示词 | 是 |
| 通用接口 | `check_task_prompt` | A2A-T Server | 校验任务提示词的场景、模板与槽位合规性 | 是 |

**公共数据类型与约定**

- **协商会话上下文：**`NegotiationContext(id, round, max_rounds, performative)`（frozen dataclass，id 为 UUID 形态、round 从 1 起、默认轮次 `DEFAULT_MAX_ROUNDS = 5`），随消息 metadata 传输、不经 LLM；`NegotiationContext.of(id, round, performative)` 使用默认轮次，`next_round()` 推进轮次，`with_performative()` 派生携带其它言语意图的上下文，`is_exhausted()` 判断轮次超限。`NegotiationPerformative` 取值 `PROPOSE` / `ACCEPT` / `REJECT` / `ABORT`。
- **异常体系：** 所有 SDK 处理失败均为 `A2ATError` 子类，捕获 `A2ATError` 即可覆盖全部处理失败，`code_str` 获取机器可读错误码。所有消息均由 SDK 按错误码模板渲染，语言跟随 `A2AT_LANGUAGE`。如下业务异常均继承 `A2ATBusinessError`，额外提供 `facts` 返回渲染消息所依据的结构化事实值；
  - 模板生成失败抛 `PromptGenerationError`（Task-T / Notification-T / Authorization-T）或 `NegotiationGenerationError`（Negotiation-T）。
  - 模板校验+提参失败抛 `ContentValidationError`（Task-T / Notification-T / Authorization-T）或 `NegotiationParamExtractionError`（Negotiation-T）。
  - 基础设施失败为直接继承 `A2ATError` 的 `ResourceNotFoundError`、`ConfigFileNotFoundError`；编程错误（None、空白或畸形入参）刻意留在异常树之外，以 `TypeError` / `ValueError` 抛出。
- **SlotValidationError：** 逐槽位校验错误明细，随校验失败异常返回（各接口输出说明中的 `errors` / `failed_parameters` 均引用此定义）：

   | 字段 | 类型 | 说明                                                                                                                       |
   | ---- | ---- |--------------------------------------------------------------------------------------------------------------------------|
   | slot_name | str | 出错槽位名                                                                                                                    |
   | code | str | 槽位级错误码，取值来自 1.4 错误码列表，如 `slot.not_provided`、`content.param_missing`、`content.entry_field_missing`、`content.format_error` |
   | message | str | 人类可读的错误说明，由 SDK 按错误码的消息模板渲染，语言跟随 `A2AT_LANGUAGE`                                                                         |
   | facts | dict[str, str] \| None | 渲染消息所依据的结构化事实值（如 `section_label`、`index`、`field_label`），可为 None                                                          |

- **以返回值报告结果的接口：** `generate_task_prompt` 与 `check_task_prompt` 不抛业务异常，失败信息随返回对象给出（结构见 1.3.22 / 1.3.23）。
- **模板 URI：** `A2ATClient` 与 `A2ATServer` 对外提供的所有指定模板的方法均以 `template_uri` 声明模板选择，接受裸字符串，推荐直接传 `a2a_t.core.standard_templates` 的 `*_URI` 字符串常量（每个都有去掉 `_URI` 后缀的同名 `TemplateUri` 类型化常量）；来自外部的字符串直接传入即可。类型 `TemplateUri` 仍用于底层服务层，可用 `TemplateUri.parse(str)` 解析，返回 `TemplateUri | None` 且不抛异常。当前支持的模板 URI 常量（字符串形态）如下：

   | 常量名称                                                        | 含义 | TemplateUri |
   |-------------------------------------------------------------| ---- | ---- |
   | standard_templates.ENERGY_SAVING_URI                         | Task-T 节能任务模板 | Task-T/network-layer/ran-energy-saving/v1 |
   | standard_templates.PRIVATE_LINE_COMPLAINT_URI                | Task-T 专线投诉任务模板 | Task-T/network-layer/private-line-complaint/v1 |
   | standard_templates.SUBSCRIBE_INCIDENT_URI                    | Notification-T 事件订阅通知模板 | Notification-T/network-layer/subscribe-incident/v1 |
   | standard_templates.SERVICE_RECOVERY_URI                      | Notification-T 业务抢通通知模板 | Notification-T/network-layer/service-recovery/v1 |
   | standard_templates.AUTHORIZATION_POLICY_MANAGEMENT_URI       | Authorization-T 授权策略管理模板 | Authorization-T/authorization-policy-management/v1 |
   | standard_templates.INFORMATION_NEGOTIATION_PROPOSE_URI       | Negotiation-T 信息协商意图发起模板 | Negotiation-T/information-negotiation/propose/v1 |
   | standard_templates.INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI | Negotiation-T 信息协商接受/拒绝模板 | Negotiation-T/information-negotiation/accept-reject/v1 |
   | standard_templates.TARGET_NEGOTIATION_PROPOSE_URI            | Negotiation-T 目标协商意图发起模板 | Negotiation-T/target-negotiation/propose/v1 |
   | standard_templates.TARGET_NEGOTIATION_ACCEPT_REJECT_URI      | Negotiation-T 目标协商接受/拒绝模板 | Negotiation-T/target-negotiation/accept-reject/v1 |
   | standard_templates.FEASIBILITY_NEGOTIATION_PROPOSE_URI       | Negotiation-T 可行性协商意图发起模板 | Negotiation-T/feasibility-negotiation/propose/v1 |
   | standard_templates.FEASIBILITY_NEGOTIATION_ACCEPT_REJECT_URI | Negotiation-T 可行性协商接受/拒绝模板 | Negotiation-T/feasibility-negotiation/accept-reject/v1 |
   | standard_templates.NEGOTIATION_ABORT_URI                     | Negotiation-T 协商中止通用模板 | Negotiation-T/common/abort/v1 |

- **模板 URI 校验策略：** 所有带 `template_uri` 参数的方法均接收原始 URI 字符串。template_uri 为 `None` 抛 `TypeError`；为空白或非法 URI（少于三段，或某一段不是简单段）抛 `ValueError`，消息为 `Unparseable template URI: <输入>`。

## 1.2 约束和限制

- 部分接口涉及到LLM调用，使用时需根据对所接模型服务可提供的并发能力，结合业务时限要求，控制使用API时的调用频率和并发。
- 涉及到LLM调用的API针对文本`text`输入会做长度防护，在输入超过`.env`（`package_data/.env` 或 `env_path` 指向的文件）中配置的 `A2AT_INPUT_TEXT_MAX_CHARS` 字符时，会报错误码 `input.text_too_long` ，该配置项默认配置为 16384（16×1024）；不涉及LLM调用的结构化数据不受此限制。
- SDK 无状态：协商会话状态随每条消息的 A2A-T metadata（`negotiationContext`）往返，不保存在 SDK 内部；旧的 `start_negotiation` / `receive_negotiation` / `continue_negotiation` 状态机协商 API 自 1.1.0 起废弃，调用时发出 `DeprecationWarning`，将在下一版本删除。

## 1.3 API说明

### 1.3.1 generate_negotiation_propose_prompt_from_text

**API定义**

```python
def generate_negotiation_propose_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：服务端Agent收到参数不全的Task-T任务报文后，用自然语言向客户端Agent发起"补充缺失信息"的协商请求；也适用于客户端Agent向服务端发起目标澄清或可行性评估请求。

**功能说明**：从自然语言文本生成协商发起（propose）阶段的结构化协商报文。执行流程：先加载模板，再执行一次 LLM 内容抽取，最后确定性渲染模板。适用于信息/目标/可行性协商的发起方。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| text | str | 是 | 描述协商发起内容的自然语言文本（如请求补充的缺失信息清单）；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| context | NegotiationContext | 是 | 协商会话上下文，不经 LLM 直接注入生成消息的 `negotiationContext` metadata |
| template_uri | str | 是 | propose 模板，如 `INFORMATION_NEGOTIATION_PROPOSE_URI` |

**请求样例**

```python
from pathlib import Path
from a2a_t.client.a2at_client import A2ATClient
from a2a_t.core.metadata import MetadataContent, NegotiationContext, NegotiationPerformative
from a2a_t.core.standard_templates import INFORMATION_NEGOTIATION_PROPOSE_URI

client = A2ATClient(env_path=Path("package_data/.env"))

ctx = NegotiationContext.of(
    "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3", 1, NegotiationPerformative.PROPOSE)

propose = client.generate_negotiation_propose_prompt_from_text(
    "请提供以下缺失信息：投诉分类：专线中断或专线质差。两个参数均为必选，缺少无法启动诊断。",
    ctx,
    INFORMATION_NEGOTIATION_PROPOSE_URI,
)

# 生成的metadata随A2A消息传输
metadata = propose.build_metadata_content()
```

**输出说明**

成功时返回 `MetadataContent`：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| template_uri | str | 生成报文所用模板 URI，如 `Negotiation-T/information-negotiation/propose/v1` |
| prompt_text | str | 渲染后的协商报文文本，作为 A2A 消息 metadata 中扩展 URI 对应的值传输 |
| extension_uri | str | TMF 扩展 URI（`https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1`），即报文在 metadata 中的 key |
| negotiation_context | NegotiationContext | 协商会话上下文（id / round / maxRounds / performative），不经 LLM 随报文携带 |
| build_metadata_content() | dict[str, object] | 构建可直接放入 `Message.metadata` 的映射：扩展 URI → 报文文本、`templateUri` → 模板 URI、`negotiationContext` → 嵌套上下文对象 |

失败时抛 `NegotiationGenerationError`（`A2ATError` 子类）：

| 成员 | 类型 | 说明 |
| ---- | ---- | ---- |
| code_str | str | 机器可读错误码，取值见下 |
| message | str | 人类可读的失败描述 |
| facts | dict[str, str] | 渲染消息所依据的结构化事实值 |

错误码：

- `template.not_found`（模板或提示词资源缺失）
- `template.render_failed`（模板渲染失败）

- `negotiation.content_extract_failed`（无法从文本抽取结构化内容，可重试）

- `llm.not_configured`（未配置 LLM 客户端，检查 `A2AT_LLM_*` 配置）

- `llm.invocation_failed`（LLM 传输失败，可重试）

- `llm.response_invalid`（LLM 返回内容不符合步骤要求，可重试）

- `negotiation.invalid_input`（文本为空、抽取内容与阶段不符、确认请求与其它板块组合矛盾）

- `negotiation.field_missing`（缺少必填字段）

- `input.text_too_long`（输入超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

context 或 template_uri 为 None 抛 `TypeError`，template_uri 为空白或非法、或 performative 段不是 `propose` 抛 `ValueError`；text 为 None 或空白不属编程错误，以 `negotiation.invalid_input` 抛 `NegotiationGenerationError`（见上错误码）。

**响应样例**

```text
template_uri : Negotiation-T/information-negotiation/propose/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 信息协商
请根据<所需信息项>补充相关内容。

## 所需信息项
1. 投诉分类：举例：专线质差
```

### 1.3.2 generate_negotiation_accept_prompt_from_text

**API定义**

```python
def generate_negotiation_accept_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：协商响应方（通常是客户端Agent）收到对端的信息协商请求后，用自然语言补充/交付所请求的信息并生成接受报文回传，如补齐接入端口名称与投诉分类后确认启动诊断。

**功能说明**：从自然语言文本生成协商接受（accept）报文。一次 LLM 内容抽取（抽取出的结论必须为 `ACCEPT`，否则以 `negotiation.conclusion_mismatch` 拒绝）+ 确定性渲染。适用于协商响应方补充/交付信息。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| text | str | 是 | 描述接受内容的自然语言文本（如补充交付的信息清单）；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| context | NegotiationContext | 是 | 协商会话上下文 |
| template_uri | str | 是 | accept-reject 模板，如 `INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI` |

**请求样例**

```python
accept = client.generate_negotiation_accept_prompt_from_text(
    "同意补充以下信息：1. 接入端口名称：P533-珠江旧城-PTN3900-23-TPA1EG24-1；"
    "2. 投诉分类：专线质差。信息已完整，可以启动诊断。",
    ctx.with_performative(NegotiationPerformative.ACCEPT),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.1](#131-generate_negotiation_propose_prompt_from_text)）。

失败时抛 `NegotiationGenerationError`（结构同 1.3.1）。错误码：

- `template.not_found`（模板或提示词资源缺失）
- `template.render_failed`（模板渲染失败）

- `negotiation.content_extract_failed`（无法从文本抽取结构化内容，可重试）

- `llm.not_configured`（未配置 LLM 客户端，检查 `A2AT_LLM_*` 配置）

- `llm.invocation_failed`（LLM 传输失败，可重试）

- `llm.response_invalid`（LLM 返回内容不符合步骤要求，可重试）

- `negotiation.invalid_input`（文本为空）

- `negotiation.conclusion_mismatch`（抽取结论不是 `ACCEPT`）

- `negotiation.field_missing`（缺少必填字段）

- `input.text_too_long`（输入超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

**响应样例**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 信息协商结果
Accept

## 信息协商结果内容
1. 接入端口名称：P533-珠江旧城-PTN3900-23-TPA1EG24-1
2. 投诉分类：专线质差
```

### 1.3.3 generate_negotiation_reject_prompt_from_text

**API定义**

```python
def generate_negotiation_reject_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：协商响应方（通常是客户端Agent）无法满足对端的协商请求时，用自然语言生成拒绝报文回传并结束本轮协商，如因站点清单不可用无法提供接入端口名称。

**功能说明**：从自然语言文本生成协商拒绝（reject）报文。一次 LLM 内容抽取（抽取出的结论必须为 `REJECT`，否则以 `negotiation.conclusion_mismatch` 拒绝）+ 确定性渲染。

**输入说明**：同 1.3.2，text 为描述拒绝原因的自然语言文本。

**请求样例**

```python
reject = client.generate_negotiation_reject_prompt_from_text(
    "拒绝补充信息：接入端口名称因站点清单不可用而无法提供，本次协商结束。",
    ctx.with_performative(NegotiationPerformative.REJECT),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.1](#131-generate_negotiation_propose_prompt_from_text)）。

失败时抛 `NegotiationGenerationError`（结构同 1.3.1）。错误码：

- `template.not_found`（模板或提示词资源缺失）
- `template.render_failed`（模板渲染失败）

- `negotiation.content_extract_failed`（无法从文本抽取结构化内容，可重试）

- `llm.not_configured`（未配置 LLM 客户端，检查 `A2AT_LLM_*` 配置）

- `llm.invocation_failed`（LLM 传输失败，可重试）

- `llm.response_invalid`（LLM 返回内容不符合步骤要求，可重试）

- `negotiation.invalid_input`（文本为空）

- `negotiation.conclusion_mismatch`（抽取结论不是 `REJECT`）

- `negotiation.field_missing`（缺少必填字段）

- `input.text_too_long`（输入超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

**响应样例**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 信息协商结果
Reject

## 信息协商结果内容
1. 接入端口名称：无法提供，工作台侧端口资源台账暂不可查
```

### 1.3.4 generate_negotiation_abort_prompt_from_text

**API定义**

```python
def generate_negotiation_abort_prompt_from_text(
    self, text: str | None, context: NegotiationContext,
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：任一协商参与方在协商无法继续（轮次耗尽、超时、token 预算耗尽等）时，用自然语言生成中止报文回传并终止整个协商会话。

**功能说明**：从自然语言文本生成协商中止（abort）报文。中止消息与协商类型无关：寻址模板必须是公共 abort 模板（`Negotiation-T/common/abort/v1`），内容仅携带终止原因。流程为一次 LLM 内容抽取 + 确定性渲染。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| text | str | 是 | 描述终止原因的自然语言文本；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| context | NegotiationContext | 是 | 协商会话上下文 |
| template_uri | str | 是 | 公共 abort 模板：`NEGOTIATION_ABORT_URI`（`Negotiation-T/common/abort/v1`） |

**请求样例**

```python
abort = client.generate_negotiation_abort_prompt_from_text(
    "协商轮次已超过上限，无法继续提供缺失参数，本次协商终止。",
    ctx.with_performative(NegotiationPerformative.ABORT),
    NEGOTIATION_ABORT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.1](#131-generate_negotiation_propose_prompt_from_text)）。

失败时抛 `NegotiationGenerationError`（结构同 1.3.1）。错误码：

- `template.not_found`（模板或提示词资源缺失）
- `template.render_failed`（模板渲染失败）

- `negotiation.content_extract_failed`（无法从文本抽取结构化内容，可重试）

- `llm.not_configured`（未配置 LLM 客户端，检查 `A2AT_LLM_*` 配置）

- `llm.invocation_failed`（LLM 传输失败，可重试）

- `llm.response_invalid`（LLM 返回内容不符合步骤要求，可重试）

- `negotiation.invalid_input`（文本为空）

- `negotiation.field_missing`（缺少必填字段）

- `input.text_too_long`（输入超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

编程错误：context 或 template_uri 为 None 抛 `TypeError`；template_uri 为空白或非法、或不指向公共 abort 模板抛 `ValueError`。

**响应样例**

```text
template_uri : Negotiation-T/common/abort/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 协商结果
Abort

## 协商终止原因
协商轮次已超过上限，无法继续提供缺失参数
```

### 1.3.5 generate_negotiation_propose_prompt_from_data

**API定义**

```python
def generate_negotiation_propose_prompt_from_data(
    self, data: NegotiationProposeData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：与 fromText 相同的发起场景，但输入为业务系统构造的结构化数据（如服务端Agent根据 `validate_task_prompt_and_data_filling` 检出的缺失槽位清单自动生成协商请求条目），适合对报文内容有确定性要求、不希望引入 LLM 抽取不确定性的场景。

**功能说明**：从结构化数据输入确定性生成协商发起报文，**不调用 LLM**。类型化内容经校验后分派到模板 URI 寻址的协商类型生成器，从模板确定性渲染。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| data | `NegotiationProposeData(context, content)` | 是 | 协商上下文 + 类型化发起内容；内容类型按协商类型选择 |
| template_uri | str | 是 | propose 模板 |

三种协商类型的发起内容（`a2a_t.negotiation.content.models`）：

| 协商类型 | Propose 内容类型 | 字段 |
| -------- | ---------------- | ---- |
| 信息协商 | `InformationProposeContent` | `items`（缺失项清单）、`relationship`（缺失项间关系，可空） |
| 目标协商 | `TargetProposeContent` | `target_negotiation_description`（必填）、`intent_understanding`、`alignment_and_clarification`、`request_for_clarification`（三个条目列表均可空/NULL，空则省略对应章节）、`target_confirm_request`（可空 str，非空表示本轮消息类别为"目标已澄清并请求对方确认"；非空时 `intent_understanding` / `alignment_and_clarification` / `request_for_clarification` 必须全为空） |
| 可行性协商 | `FeasibilityProposeContent` | `feasibility_negotiation_description`（必填）、`action`（`NegotiationAction.REQUEST_FEASIBILITY_EVALUATION` / `PROPOSE_ALTERNATIVE_ON_FAILURE`，两值不变）、`contents_to_evaluate`、`infeasibility_details_and_proposal`、`feasibility_confirm_request`（可空 str，非空表示本轮消息类别为"评估可行并请求确认"：`action` 须取 `REQUEST_FEASIBILITY_EVALUATION` 且 `contents_to_evaluate` / `infeasibility_details_and_proposal` 皆为空） |

**请求样例**

```python
from a2a_t.negotiation.content.models import (
    InformationProposeContent, NegotiationItem, NegotiationProposeData,
)

ctx = NegotiationContext.of(
    "3dbc13b5-bd57-4c2b-b503-24e381b6c8d3", 2, NegotiationPerformative.PROPOSE)

propose = client.generate_negotiation_propose_prompt_from_data(
    NegotiationProposeData(
        context=ctx,
        content=InformationProposeContent(
            items=[
                NegotiationItem("接入端口名称", "举例：P533-珠江旧城-PTN3900-23-TPA1EG24-1"),
                NegotiationItem("投诉分类", "举例：专线质差"),
                NegotiationItem("专线业务标识", None),
            ],
            relationship="OR",
        ),
    ),
    INFORMATION_NEGOTIATION_PROPOSE_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.1](#131-generate_negotiation_propose_prompt_from_text)，协商报文均携带 `negotiation_context`）。

失败时抛 `NegotiationGenerationError`（结构同 1.3.1）。错误码：

- `template.not_found`（模板缺失）

- `negotiation.content_invalid`（类型化内容字段无效，如必填 items 为空、必填描述为空白）

- `negotiation.invalid_input`（确认请求与其它条件板块组合矛盾等输入无效场景）

- `template.render_failed`（模板渲染失败）

另有两类编程错误（`A2ATError` 树外，标准 Python 异常）：入参或其 context 为 None 抛 `TypeError`；template_uri 为空白或非法、内容类型与模板协商类型不符、performative 段不是 `propose` 抛 `ValueError`。

**响应样例**

```text
template_uri : Negotiation-T/information-negotiation/propose/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 信息协商
请根据<所需信息项>补充相关内容。

## 所需信息项
1. 接入端口名称：举例：P533-珠江旧城-PTN3900-23-TPA1EG24-1
2. 投诉分类：举例：专线质差
3. 专线业务标识
缺失项之间的关系：OR
```

### 1.3.6 generate_negotiation_accept_prompt_from_data

**API定义**

```python
def generate_negotiation_accept_prompt_from_data(
    self, data: NegotiationEndingData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：协商响应方（通常是客户端Agent）按对端请求的槽位清单程序化补参后，以结构化条目生成接受报文回传。

**功能说明**：从结构化数据输入确定性生成协商接受报文，**不调用 LLM**。`content.conclusion` 必须为 `Accept`，其他结论（含 `Abort`）以 `negotiation.conclusion_mismatch` 业务错误拒绝。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| data | `NegotiationEndingData(context, content)` | 是 | 协商上下文 + 类型化接受内容（结论为 `Accept`） |
| template_uri | str | 是 | accept-reject 模板 |

三种协商类型的接受内容：`InformationEndingContent(ACCEPT, items)`（交付的信息项清单）、`TargetEndingContent(ACCEPT, confirmed_intent, None)`（最终确认的意图）、`FeasibilityEndingContent(ACCEPT, feasibility_summary)`（可行性评估结论摘要）。

**请求样例**

```python
from a2a_t.negotiation.content.enums import NegotiationConclusion
from a2a_t.negotiation.content.models import InformationEndingContent, NegotiationEndingData

accept = client.generate_negotiation_accept_prompt_from_data(
    NegotiationEndingData(
        context=ctx,
        content=InformationEndingContent(
            conclusion=NegotiationConclusion.ACCEPT,
            items=[
                NegotiationItem("接入端口名称", "P533-珠江旧城-PTN3900-23-TPA1EG24-1"),
                NegotiationItem("投诉分类", "专线质差"),
            ],
        ),
    ),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.1](#131-generate_negotiation_propose_prompt_from_text)）。

失败时抛 `NegotiationGenerationError`（结构同 1.3.1）。错误码：

- `template.not_found`（模板缺失）

- `negotiation.content_invalid`（类型化内容字段无效，如必填 items 为空、必填描述为空白）

- `template.render_failed`（模板渲染失败）

编程错误：入参或其 context 为 None 抛 `TypeError`；template_uri 为空白或非法、内容类型不符或 performative 段不是 `accept-reject` 抛 `ValueError`；`conclusion` 不是 `ACCEPT` 以 `negotiation.conclusion_mismatch` 业务错误拒绝。

**响应样例**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 信息协商结果
Accept

## 信息协商结果内容
1. 接入端口名称：P533-珠江旧城-PTN3900-23-TPA1EG24-1
2. 投诉分类：专线质差
```

### 1.3.7 generate_negotiation_reject_prompt_from_data

**API定义**

```python
def generate_negotiation_reject_prompt_from_data(
    self, data: NegotiationEndingData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：协商响应方程序化判定无法满足对端请求后，以结构化条目（无法提供的项及原因）生成拒绝报文回传。

**功能说明**：从结构化数据输入确定性生成协商拒绝报文，**不调用 LLM**。`content.conclusion` 必须为 `Reject`，其他结论以 `negotiation.conclusion_mismatch` 业务错误拒绝。

**输入说明**：同 [1.3.6](#136-generate_negotiation_accept_prompt_from_data)，但结论为 `REJECT`。拒绝内容：`InformationEndingContent(REJECT, items)`（无法提供的项及原因）、`TargetEndingContent(REJECT, None, failure_reason)`（拒绝原因）、`FeasibilityEndingContent(REJECT, feasibility_summary)`（不可行结论摘要）。

**请求样例**

```python
reject = client.generate_negotiation_reject_prompt_from_data(
    NegotiationEndingData(
        context=ctx,
        content=InformationEndingContent(
            conclusion=NegotiationConclusion.REJECT,
            items=[
                NegotiationItem("接入端口名称", "无法提供，工作台侧端口资源台账暂不可查"),
            ],
        ),
    ),
    INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.1](#131-generate_negotiation_propose_prompt_from_text)）。

失败时抛 `NegotiationGenerationError`（结构同 1.3.1）。错误码：

- `template.not_found`（模板缺失）

- `negotiation.content_invalid`（类型化内容字段无效，如必填 items 为空、必填描述为空白）

- `template.render_failed`（模板渲染失败）

编程错误：入参或其 context 为 None 抛 `TypeError`；template_uri 为空白或非法、内容类型不符或 performative 段不是 `accept-reject` 抛 `ValueError`；`conclusion` 不是 `REJECT` 以 `negotiation.conclusion_mismatch` 业务错误拒绝。

**响应样例**

```text
template_uri : Negotiation-T/information-negotiation/accept-reject/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 信息协商结果
Reject

## 信息协商结果内容
1. 接入端口名称：无法提供，工作台侧端口资源台账暂不可查
```

### 1.3.8 generate_negotiation_abort_prompt_from_data

**API定义**

```python
def generate_negotiation_abort_prompt_from_data(
    self, data: NegotiationAbortData, template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：任一协商参与方以结构化方式生成中止报文，终止无法继续的协商会话。

**功能说明**：从结构化数据输入确定性生成协商中止报文，**不调用 LLM**。中止消息与类型无关：寻址模板必须是公共 abort 模板，`NegotiationAbortContent` 仅携带终止原因（`Abort` 结论为模板固定文本）。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| data | `NegotiationAbortData(context, content)` | 是 | 协商上下文 + `NegotiationAbortContent(termination_reason)` |
| template_uri | str | 是 | 公共 abort 模板：`NEGOTIATION_ABORT_URI` |

**请求样例**

```python
from a2a_t.negotiation.content.models import NegotiationAbortContent, NegotiationAbortData

abort = client.generate_negotiation_abort_prompt_from_data(
    NegotiationAbortData(
        context=ctx,
        content=NegotiationAbortContent(termination_reason="协商轮次已超过上限，无法继续提供缺失参数"),
    ),
    NEGOTIATION_ABORT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.1](#131-generate_negotiation_propose_prompt_from_text)）。

失败时抛 `NegotiationGenerationError`（结构同 1.3.1）。错误码：

- `template.not_found`（模板缺失）

- `negotiation.content_invalid`（类型化内容字段无效，如终止原因为空白）

- `template.render_failed`（模板渲染失败）

编程错误：入参或其 context 为 None 抛 `TypeError`；template_uri 为空白或非法、或不指向公共 abort 模板抛 `ValueError`。

**响应样例**

```text
template_uri : Negotiation-T/common/abort/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Negotiation-T/v1
prompt_text  :
## 协商结果
Abort

## 协商终止原因
协商轮次已超过上限，无法继续提供缺失参数
```

### 1.3.9 validate_propose_prompt_and_data_filling

**API定义**

```python
def validate_propose_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**典型场景**：发起方（服务端Agent）发送协商请求前的出站自检；或接收方（客户端Agent）校验收到的协商请求并提取需补充的槽位清单，驱动后续补参。

**功能说明**：校验一条协商发起（propose）报文是否为格式正确的协商消息，并按调用方提供的 JSON Schema 从中提取参数。管线顺序：模板 URI 校验 → 确定性规则门（协商上下文）→ 模板加载 → 一次可重试 LLM 语义校验（同时提取参数）→ 确定性参数合并。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| prompt | str | 是 | 待校验的协商发起报文文本（`MetadataContent.prompt_text`）；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| context | NegotiationContext | 是 | 随报文传输的协商上下文；为 None 时报 `negotiation.invalid_input` |
| schema | Mapping[str, object] | 是 | 调用方提供的参数 JSON Schema，声明要提取的参数 |
| template_uri | str | 是 | propose 模板 |

**请求样例**

```python
schema = {
    "type": "object",
    "properties": {
        "接入端口名称": {"type": "string"},
        "投诉分类": {"type": "string"},
    },
    "required": ["接入端口名称"],
}

# propose_prompt 为对端发来的协商发起报文文本
requested = client.validate_propose_prompt_and_data_filling(
    propose_prompt, ctx, schema, INFORMATION_NEGOTIATION_PROPOSE_URI,
)

# 过滤掉上下文参数（id/round/maxRounds）后即为需补充的槽位清单
needed = {k: v for k, v in requested.data.items() if k not in ("id", "round", "maxRounds")}
```

**输出说明**

成功时返回 `FilledParamData`：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| data | dict[str, object] \| list[object] | 合并后的参数：协商上下文参数（`id` / `round` / `maxRounds`，键冲突时**优先**）+ 按调用方 Schema 从报文中提取的参数；调用方 Schema 为数组形态时，`data` 为有序数组 |
| context | dict[str, object] \| None | 仅数组形态的 `data` 下携带的上下文参数（`id` / `round` / `maxRounds`）；对象形态时上下文已合并进 `data`，该字段为 `None` |

失败时抛 `NegotiationParamExtractionError`（`A2ATError` 子类）：

| 成员 | 类型 | 说明 |
| ---- | ---- | ---- |
| code_str | str | 机器可读错误码，取值见下 |
| message | str | 人类可读的失败描述 |
| errors | list[SlotValidationError] | 逐槽位错误明细，结构见公共约定 |

错误码：

- `negotiation.invalid_input`（prompt 为 None 或空白、报文不是协商消息，或 context 为 None）

- `negotiation.rule_violation`（协商上下文违反规则；`errors` 中的嵌套槽位错误码标识具体规则，如 `negotiation.invalid_context_id`、`negotiation.round_exceeded`）

- `negotiation.semantic_rejected`（语义校验拒绝；`errors` 中的逐槽位明细使用封闭的 `negotiation.*` 码集，如 `negotiation.conclusion_content_mismatch`、`negotiation.missing_result_content`、`negotiation.field_inconsistency`）

- `llm.invocation_failed` / `llm.response_invalid`（LLM 失败，可重试）

- `template.not_found`（校验提示词资源缺失）

编程错误：schema / template_uri 为 None 抛 `TypeError`，template_uri 为空白或非法、performative 段不匹配抛 `ValueError`；prompt 为 None 或空白不属编程错误，以 `negotiation.invalid_input` 抛 `NegotiationParamExtractionError`（见上错误码）。

**响应样例**

```text
requested.data =
{
  '接入端口名称': '举例：P533-珠江旧城-PTN3900-23-TPA1EG24-1',
  '投诉分类': '举例：专线质差',
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.10 validate_accept_prompt_and_data_filling

**API定义**

```python
def validate_accept_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**典型场景**：发起方（服务端Agent）校验对端回传的接受报文，提取交付的参数值并与期望补齐值核对，确认无误后继续任务执行。

**功能说明**：校验一条协商接受（accept）报文并按 Schema 提取参数（管线同 1.3.9，期望的 performative 固定为 `accept-reject`）。

**输入说明**：同 [1.3.9](#139-validate_propose_prompt_and_data_filling)，prompt 为 accept 报文文本，template_uri 为 accept-reject 模板。

**请求样例**

```python
# accept_prompt 为客户端回传的接受报文文本，accept_context 为其协商上下文
accept_params = server.validate_accept_prompt_and_data_filling(
    accept_prompt, accept_context, schema, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**输出说明**

成功时返回 `FilledParamData`（结构同 [1.3.9](#139-validate_propose_prompt_and_data_filling)），`data` 携带交付内容中按 Schema 提取的参数与上下文参数。

失败时抛 `NegotiationParamExtractionError`（结构同 1.3.9）。错误码：

- `negotiation.invalid_input`（prompt 为 None 或空白、报文不是 accept 协商消息，或 context 为 None）

- `negotiation.rule_violation`（协商上下文违反规则；`errors` 中的嵌套槽位错误码标识具体规则，如 `negotiation.invalid_context_id`、`negotiation.round_exceeded`）

- `negotiation.semantic_rejected`（结论不是 Accept 或内容不满足 accept 阶段约束）

- `llm.invocation_failed` / `llm.response_invalid`（LLM 失败，可重试）

- `template.not_found`（校验提示词资源缺失）

编程错误：schema / template_uri 为 None 抛 `TypeError`，template_uri 为空白或非法、performative 段不是 `accept-reject` 抛 `ValueError`；prompt 为 None 或空白不属编程错误，以 `negotiation.invalid_input` 抛 `NegotiationParamExtractionError`（见上错误码）。

**响应样例**

```text
accept_params.data =
{
  '接入端口名称': 'P533-珠江旧城-PTN3900-23-TPA1EG24-1',
  '投诉分类': '专线质差',
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.11 validate_reject_prompt_and_data_filling

**API定义**

```python
def validate_reject_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**典型场景**：发起方（服务端Agent）校验对端回传的拒绝报文，提取拒绝原因，据此终止任务或转入人工处理。

**功能说明**：校验一条协商拒绝（reject）报文并按 Schema 提取参数（管线同 1.3.9，期望的 performative 固定为 `accept-reject`）。

**输入说明**：同 [1.3.9](#139-validate_propose_prompt_and_data_filling)，prompt 为 reject 报文文本，template_uri 为 accept-reject 模板。

**请求样例**

```python
reject_params = server.validate_reject_prompt_and_data_filling(
    reject_prompt, ctx, schema, INFORMATION_NEGOTIATION_ACCEPT_REJECT_URI,
)
```

**输出说明**

成功时返回 `FilledParamData`（结构同 [1.3.9](#139-validate_propose_prompt_and_data_filling)），`data` 携带拒绝原因中按 Schema 提取的参数与上下文参数。

失败时抛 `NegotiationParamExtractionError`（结构同 1.3.9）。错误码：

- `negotiation.invalid_input`（prompt 为 None 或空白、报文不是 reject 协商消息，或 context 为 None）

- `negotiation.rule_violation`（协商上下文违反规则；`errors` 中的嵌套槽位错误码标识具体规则，如 `negotiation.invalid_context_id`、`negotiation.round_exceeded`）

- `negotiation.semantic_rejected`（结论不是 Reject 或内容不满足 reject 阶段约束）

- `llm.invocation_failed` / `llm.response_invalid`（LLM 失败，可重试）

- `template.not_found`（校验提示词资源缺失）

编程错误：schema / template_uri 为 None 抛 `TypeError`，template_uri 为空白或非法、performative 段不是 `accept-reject` 抛 `ValueError`；prompt 为 None 或空白不属编程错误，以 `negotiation.invalid_input` 抛 `NegotiationParamExtractionError`（见上错误码）。

**响应样例**

```text
reject_params.data =
{
  '接入端口名称': '无法提供，工作台侧端口资源台账暂不可查',
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.12 validate_abort_prompt_and_data_filling

**API定义**

```python
def validate_abort_prompt_and_data_filling(
    self,
    prompt: str | None,
    context: NegotiationContext | None,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**典型场景**：协商参与方校验对端发来的中止报文，提取终止原因，据此释放会话资源并结束协商。

**功能说明**：校验一条协商中止（abort）报文并按 Schema 提取参数（管线同 1.3.9，template_uri 必须寻址公共 abort 模板）。

**输入说明**：同 [1.3.9](#139-validate_propose_prompt_and_data_filling)，prompt 为 abort 报文文本，template_uri 为公共 abort 模板（`NEGOTIATION_ABORT_URI`）。

**请求样例**

```python
abort_params = server.validate_abort_prompt_and_data_filling(
    abort_prompt, ctx, schema, NEGOTIATION_ABORT_URI,
)
```

**输出说明**

成功时返回 `FilledParamData`（结构同 [1.3.9](#139-validate_propose_prompt_and_data_filling)），`data` 携带终止原因中按 Schema 提取的参数与上下文参数。

失败时抛 `NegotiationParamExtractionError`（结构同 1.3.9）。错误码：

- `negotiation.invalid_input`（prompt 为 None 或空白、报文不是 abort 协商消息，或 context 为 None）

- `negotiation.rule_violation`（协商上下文违反规则；`errors` 中的嵌套槽位错误码标识具体规则，如 `negotiation.invalid_context_id`、`negotiation.round_exceeded`）

- `negotiation.semantic_rejected`（报文不满足 abort 阶段约束）

- `llm.invocation_failed` / `llm.response_invalid`（LLM 失败，可重试）

- `template.not_found`（校验提示词资源缺失）

编程错误：schema / template_uri 为 None 抛 `TypeError`，template_uri 为空白或非法、或不指向公共 abort 模板抛 `ValueError`；prompt 为 None 或空白不属编程错误，以 `negotiation.invalid_input` 抛 `NegotiationParamExtractionError`（见上错误码）。

**响应样例**

```text
abort_params.data =
{
  'id': '3dbc13b5-bd57-4c2b-b503-24e381b6c8d3',
  'round': 1,
  'maxRounds': 5
}
```

### 1.3.13 generate_task_prompt_from_text

**API定义**

```python
def generate_task_prompt_from_text(
    self, text: str, template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：客户端Agent将用户的自然语言任务描述（如专线投诉诊断诉求）转换为指定场景的Task-T协议报文，适合已确定目标模板、需跳过场景识别的场景。

**功能说明**：从自然语言文本按指定 Task-T 模板生成任务提示词报文，**跳过场景识别**（模板由调用方显式指定）。执行一次 LLM 槽位提取后确定性渲染模板。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| text | str | 是 | 自然语言任务描述；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| template_uri | str | 是 | Task-T 模板，如 `PRIVATE_LINE_COMPLAINT_URI`（`Task-T/network-layer/private-line-complaint/v1`） |

**请求样例**

```python
from a2a_t.core.standard_templates import PRIVATE_LINE_COMPLAINT_URI

metadata = client.generate_task_prompt_from_text(
    "帮我创建专线投诉诊断任务，P781-珠江新城-PTN7900-23-TPA1EG24-17 这个端口，"
    "客户报的是专线质差，从2026年5月11号早上8点半开始，深圳访问广州的核心系统非常慢，"
    "时延从12ms飙到320ms，柜面和手机银行老是报连接超时，OSS流水号是event-id-20260511-09013。",
    PRIVATE_LINE_COMPLAINT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| template_uri | str | 生成报文所用模板 URI，如 `Task-T/network-layer/private-line-complaint/v1` |
| prompt_text | str | 渲染后的任务提示词报文文本，作为 A2A 消息 metadata 中扩展 URI 对应的值传输 |
| extension_uri | str | TMF 扩展 URI（`https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1`），即报文在 metadata 中的 key |
| negotiation_context | NegotiationContext | 始终为 None（非协商报文） |
| build_metadata_content() | dict[str, object] | 构建可直接放入 `Message.metadata` 的两键映射：扩展 URI → 报文文本、`templateUri` → 模板 URI |

失败时抛 `PromptGenerationError`（`A2ATError` 子类）：

| 成员 | 类型 | 说明 |
| ---- | ---- | ---- |
| code_str | str | 机器可读错误码，取值见下 |
| message | str | 人类可读的失败描述 |
| failed_parameters | list[SlotValidationError] | 槽位校验失败的明细（`slot.not_provided` 等槽位域失败码时非空），结构见公共约定 |

错误码：

- `template.not_found`（模板缺失）

- `template.load_failed`（提示词资源加载失败）

- `slot.schema_not_found`（槽位 Schema 缺失）

- `llm.not_configured`（未配置 LLM 客户端，检查 `A2AT_LLM_*` 配置）

- `llm.invocation_failed` / `llm.response_invalid`（LLM 调用失败，可重试）

- `slot.not_provided`（必填槽在输入中未提供）

- `slot.constraint_violated`（槽位取值不在允许范围内）

- `slot.rule_violation`（其它槽位校验规则违规的兜底码）

- `template.render_failed`（模板渲染失败）

- `input.text_too_long`（输入超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

编程错误：text 或 template_uri 为 None 抛 `TypeError`；template_uri 为空白或非法抛 `ValueError`。

**响应样例**

```text
template_uri : Task-T/network-layer/private-line-complaint/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1
prompt_text  :
## 任务类型(Task Type)
传输专线业务投诉诊断

## 任务描述(Task Description)
基于<任务对象>、<任务上下文> 进行投诉场景的网络侧故障根因诊断, 达成<任务目标>中定义的投诉诊断目标，按照<预期输出>中定义的结构返回任务处理结果。

## 任务目标(Task Target)
对网络侧故障进行诊断，返回故障根因和修复建议等诊断结果信息。

## 任务对象(Task Object)
接入端口名称：P781-珠江新城-PTN7900-23-TPA1EG24-17

## 任务上下文(Task Context)
1. 投诉分类：专线质差
2. 问题发生时间：2026-05-11T08:21:46Z
3. OSS侧事件流水号：event-id-20260511-09013
4. 投诉详情：从5月11号早上8点半开始，深圳访问广州的响应时延从平均12ms骤升至320ms

## 预期输出(Expected Output)
要求投诉诊断任务的结果包含如下信息：
1. 诊断结果；参数的取值范围包括：成功、失败；(必选)
2. 诊断结果详细信息； (必选)
3. 修复建议； (可选)
4. 故障根因列表，每个故障根因包含故障根因名称、详细描述、修复建议、故障根因点位置等信息； (可选)

## 术语解释(Terminology Explanation)
1. 专线中断
   - 同义词术语：private line interruption，业务中断，网络无法连通，业务不通，业务无法访问
2. 专线质差
   - 同义词术语：private line poor quality，业务卡顿，业务访问超时，业务丢包，业务时延大，业务抖动，业务拥塞，业务体验下滑，业务误码率高，业务光功率异常
```

### 1.3.14 generate_task_prompt_from_data_with_schema

**API定义**

```python
def generate_task_prompt_from_data_with_schema(
    self,
    data: Mapping[str, object],
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：客户端Agent将上游系统的结构化任务参数（字段名可与模板槽位不同，由Schema描述字段语义）转换为Task-T协议报文，适合任务参数已结构化持有的场景。

**功能说明**：从结构化数据 + 语义 Schema 按指定 Task-T 模板生成任务提示词，**跳过场景识别**。`schema` 描述每个输入字段的业务含义（description / examples / enum 等），指导槽位填充与取值约束；每个 data 的 key 对应一个槽位值。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| data | Mapping[str, object] | 是 | 结构化业务字段输入，key 为业务字段名，value 为字段值 |
| schema | Mapping[str, object] | 是（非空） | 字段语义 JSON Schema，描述各字段含义与约束 |
| template_uri | str | 是 | Task-T 模板 |

**请求样例**

```python
data = {
    "portName": "P781-福田中心-PTN7900-2-TPA1EG24-03",
    "complaintScenario": "专线质差",
    "faultStartTime": "2026-05-11T08:21:46Z",
    "ticketNo": "event-id-20260511-09013",
    "faultDetailText": "从5月11号早上8点半开始，深圳访问广州的响应时延从平均12ms骤升至320ms",
}

semantics_schema = {
    "type": "object",
    "properties": {
        "portName": {"type": "string", "description": "业务字段：接入端口名称，唯一标识被投诉的专线对象"},
        "complaintScenario": {
            "type": "string",
            "description": "业务字段：投诉分类场景，专线中断或专线质差二者必选其一",
            "enum": ["专线中断", "专线质差"],
        },
        "faultStartTime": {"type": "string", "description": "业务字段：问题发生时间"},
        "ticketNo": {"type": "string", "description": "业务字段：OSS 侧受理的投诉工单或事件流水号"},
        "faultDetailText": {"type": "string", "description": "业务字段：用户对故障现象的自由描述"},
    },
    "required": ["portName", "complaintScenario"],
}

metadata = client.generate_task_prompt_from_data_with_schema(
    data, semantics_schema, PRIVATE_LINE_COMPLAINT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.13](#1313-generate_task_prompt_from_text)）。

失败时抛 `PromptGenerationError`（结构同 1.3.13）。编程错误：入参为 None 抛 `TypeError`，template_uri 为空白或非法或 schema 为空 dict 抛 `ValueError`。

**响应样例**（与 1.3.13 同模板，槽位值来自结构化输入，`投诉详情` 为示例截断值）

```text
template_uri : Task-T/network-layer/private-line-complaint/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Task-T/v1
prompt_text  :
## 任务类型(Task Type)
传输专线业务投诉诊断

## 任务描述(Task Description)
基于<任务对象>、<任务上下文> 进行投诉场景的网络侧故障根因诊断, 达成<任务目标>中定义的投诉诊断目标，按照<预期输出>中定义的结构返回任务处理结果。

## 任务目标(Task Target)
对网络侧故障进行诊断，返回故障根因和修复建议等诊断结果信息。

## 任务对象(Task Object)
接入端口名称：P781-福田中心-PTN7900-2-TPA1EG24-03

## 任务上下文(Task Context)
1. 投诉分类：专线质差
2. 问题发生时间：2026-05-11T08:21:46Z
3. OSS侧事件流水号：event-id-20260511-09013
4. 投诉详情：从5月11号早上8点半开始，深圳访问广州的响应时延从平均12ms骤升至320ms

## 预期输出(Expected Output)
要求投诉诊断任务的结果包含如下信息：
1. 诊断结果；参数的取值范围包括：成功、失败；(必选)
2. 诊断结果详细信息； (必选)
3. 修复建议； (可选)
4. 故障根因列表，每个故障根因包含故障根因名称、详细描述、修复建议、故障根因点位置等信息； (可选)

## 术语解释(Terminology Explanation)
1. 专线中断
   - 同义词术语：private line interruption，业务中断，网络无法连通，业务不通，业务无法访问
2. 专线质差
   - 同义词术语：private line poor quality，业务卡顿，业务访问超时，业务丢包，业务时延大，业务抖动，业务拥塞，业务体验下滑，业务误码率高，业务光功率异常
```

### 1.3.15 validate_task_prompt_and_data_filling

**API定义**

```python
def validate_task_prompt_and_data_filling(
    self,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**典型场景**：服务端Agent收到Task-T报文后、进入业务执行前的参数校验与提取入口；也是协商流程中"缺槽检测"的判定点——required参数缺失时由调用方决定是否发起协商补参。

**功能说明**：校验一条 Task-T 任务提示词报文是否匹配模板与槽位约束，并按调用方提供的 JSON Schema 提取参数。管线顺序：输入门 → 规则门 → 模板加载 → 一次可重试 LLM 语义校验（同时提取参数）→ 确定性参数合并。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| prompt | str | 是（非空） | 待校验的任务提示词报文文本（`MetadataContent.prompt_text`）；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| schema | Mapping[str, object] | 是 | 调用方提供的参数 JSON Schema，声明要提取/校验的参数及 required 约束 |
| template_uri | str | 是 | Task-T 模板（前缀段必须为 `Task-T`） |

**请求样例**

```python
# 服务端参数Schema（key为服务端业务字段名，与客户端字段名可不同，SDK完成跨字段适配）
validation_schema = {
    "type": "object",
    "properties": {
        "accessPort": {"type": "string", "description": "接入端口名称，唯一标识被投诉的专线对象"},
        "bizScenario": {
            "type": "string",
            "description": "投诉分类场景，必填，仅允许取值：专线中断、专线质差",
            "enum": ["专线中断", "专线质差"],
        },
        "faultTime": {"type": "string", "description": "问题发生时间"},
        "eventSerialNo": {"type": "string", "description": "OSS 侧受理的投诉工单或事件流水号"},
        "faultDetail": {"type": "string", "description": "投诉/故障现象详情描述"},
    },
    "required": ["accessPort", "bizScenario"],
}

extracted = server.validate_task_prompt_and_data_filling(
    metadata.prompt_text, validation_schema, PRIVATE_LINE_COMPLAINT_URI,
).data
```

**输出说明**

成功时返回 `FilledParamData`：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| data | dict[str, object] \| list[object] | 按 Schema 提取的参数，key 为 Schema 中声明的参数名；调用方 Schema 为数组形态时，`data` 为有序数组 |
| context | dict[str, object] \| None | 仅数组形态的 `data` 下携带的上下文参数；对象形态时为 `None` |

失败时抛 `ContentValidationError`（`A2ATError` 子类）：

| 成员 | 类型 | 说明 |
| ---- | ---- | ---- |
| code_str | str | 机器可读错误码，取值见下 |
| message | str | 人类可读的失败描述 |
| errors | list[SlotValidationError] | 逐槽位错误明细（槽位级错误码如 `content.param_missing`、`content.entry_field_missing`、`content.format_error`），结构见公共约定 |
| params | dict[str, object] | 被拒前的部分提取参数（无法提取的槽位值为 None 或缺失） |

错误码：

- `negotiation.invalid_input`（prompt 为 None 或空白、schema 为 None，或 templateUri 前缀段/版本与该接口不符）

- `negotiation.semantic_rejected`（语义校验拒绝，含 required 参数缺失或取值非法；`errors` 中的逐槽位明细使用 `content.*` 码集，如 `content.param_missing`、`content.entry_field_missing`、`content.format_error`、`content.value_not_allowed`）

- `llm.invocation_failed` / `llm.response_invalid`（LLM 失败，可重试）

- `template.not_found`（校验提示词资源缺失）

- `input.text_too_long`（提示词超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

编程错误：template_uri 为 None 抛 `TypeError`，为空白/非法抛 `ValueError`；prompt 为 None 或空白、schema 为 None 不属编程错误，以 `negotiation.invalid_input` 抛 `ContentValidationError`（见上错误码）。

**响应样例**（`faultTime`、`faultDetail` 为示例截断值）

```text
extracted =
{
  'accessPort': 'P781-珠江新城-PTN7900-23-TPA1EG24-17',
  'bizScenario': '专线质差',
  'faultTime': '2026-05-11',
  'eventSerialNo': 'event-id-20260511-09013',
  'faultDetail': '320ms'
}
```

校验拒绝时（缺少关键槽位的负例）：

```text
ContentValidationError: [negotiation.semantic_rejected] ...
    slot=任务对象 code=content.param_missing message=... facts={'section_label': '任务对象'}
```

### 1.3.16 generate_notification_prompt_from_text

**API定义**

```python
def generate_notification_prompt_from_text(
    self, text: str, template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：客户端Agent将自然语言订阅需求（如业务抢通事件订阅）转换为指定场景的Notification-T订阅报文。

**功能说明**：从自然语言文本按指定 Notification-T 模板生成通知订阅提示词报文。执行一次 LLM 槽位提取后确定性渲染模板，生成阶段执行内置槽位 Schema 校验。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| text | str | 是 | 自然语言订阅描述（通知主题、订阅条件、上报通知数据格式等）；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| template_uri | str | 是 | Notification-T 模板，如 `SUBSCRIBE_INCIDENT_URI`、`SERVICE_RECOVERY_URI` |

**请求样例**

```python
from a2a_t.core.standard_templates import SERVICE_RECOVERY_URI

result = client.generate_notification_prompt_from_text(
    "我想订阅一下业务抢通事件。上报通知数据格式如下："
    "1. 业务抢通方案执行状态，取值范围：未启动、已结束；"
    "2. 投诉诊断任务流水号；3. OSS侧事件流水号；4. 接入端口名称；"
    "5. 是否已授权OMC自动抢通，取值范围：是、否；6. 业务抢通方案名称；7. 业务抢通方案详情",
    SERVICE_RECOVERY_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.13](#1313-generate_task_prompt_from_text)，`extension_uri` 为 `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1`）。

失败时抛 `PromptGenerationError`（结构同 1.3.13）。编程错误：text 或 template_uri 为 None 抛 `TypeError`；template_uri 为空白或非法抛 `ValueError`。

**响应样例**（按模板渲染，实际文本随 LLM 槽位提取结果变化；本例输入未指定订阅条件，对应槽位留空）

```text
template_uri : Notification-T/network-layer/service-recovery/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1
prompt_text  :
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式> 及 <预期输出> 信息，完成网络侧业务抢通事件的订阅与上报任务。

## 通知主题
业务抢通事件

## 订阅条件

## 上报通知数据格式
1. 业务抢通方案执行状态，取值范围：未启动、已结束
2. 投诉诊断任务流水号
3. OSS侧事件流水号
4. 接入端口名称
5. 是否已授权OMC自动抢通，取值范围：是、否
6. 业务抢通方案名称
7. 业务抢通方案详情

## 预期输出
1. 订阅结果，取值范围：成功
2. 订阅成功后，按照<上报通知数据格式>上报消息
```

### 1.3.17 generate_notification_prompt_from_data_with_schema

**API定义**

```python
def generate_notification_prompt_from_data_with_schema(
    self,
    data: Mapping[str, object],
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：客户端Agent将上游系统/界面提交的结构化订阅参数转换为Notification-T订阅报文，适合订阅参数已结构化持有的场景。

**功能说明**：从结构化数据 + 语义 Schema 按指定 Notification-T 模板生成通知订阅提示词。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| data | Mapping[str, object] | 是 | 结构化订阅输入（如订阅条件、上报数据格式字段列表） |
| schema | Mapping[str, object] | 是（非空） | 字段语义 JSON Schema |
| template_uri | str | 是 | Notification-T 模板 |

**请求样例**

```python
data = {
    "condition": "子网名称：xx子网",
    "reportFormat": [
        {"name": "业务抢通方案执行状态", "values": ["未启动", "已结束"], "required": True},
        {"name": "投诉诊断任务流水号", "required": True},
        {"name": "OSS侧事件流水号", "required": True},
        {"name": "接入端口名称", "required": True},
        {"name": "是否已授权OMC自动抢通", "values": ["是", "否"], "required": True},
        {"name": "业务抢通方案名称", "required": True},
        {"name": "业务抢通方案详情", "required": True},
        {"name": "业务抢通方案执行结束时间", "required": False},
    ],
}

data_schema = {
    "type": "object",
    "properties": {
        "condition": {"type": "string", "description": "订阅条件，可选。待订阅的条件描述。"},
        "reportFormat": {
            "type": "array",
            "description": "上报通知数据格式，必选。描述所需上报内容的字段列表。",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "values": {"type": "array"},
                    "required": {"type": "boolean"},
                },
                "required": ["name"],
            },
        },
    },
    "required": ["reportFormat"],
}

result = client.generate_notification_prompt_from_data_with_schema(
    data, data_schema, SERVICE_RECOVERY_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.13](#1313-generate_task_prompt_from_text)，`extension_uri` 为 `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1`）。

失败时抛 `PromptGenerationError`（结构同 1.3.13）。编程错误：入参为 None 抛 `TypeError`，template_uri 为空白或非法或 schema 为空 dict 抛 `ValueError`。

**响应样例**（与 1.3.16 同模板，槽位值来自结构化输入：`订阅条件` 填充 `condition`，`上报通知数据格式` 按 `reportFormat` 列表渲染）

```text
template_uri : Notification-T/network-layer/service-recovery/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1
prompt_text  :
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式> 及 <预期输出> 信息，完成网络侧业务抢通事件的订阅与上报任务。

## 通知主题
业务抢通事件

## 订阅条件
子网名称：xx子网

## 上报通知数据格式
1. 业务抢通方案执行状态，取值范围：未启动、已结束（必选）
2. 投诉诊断任务流水号（必选）
3. OSS侧事件流水号（必选）
4. 接入端口名称（必选）
5. 是否已授权OMC自动抢通，取值范围：是、否（必选）
6. 业务抢通方案名称（必选）
7. 业务抢通方案详情（必选）
8. 业务抢通方案执行结束时间（可选）

## 预期输出
1. 订阅结果，取值范围：成功
2. 订阅成功后，按照<上报通知数据格式>上报消息
```

### 1.3.18 validate_notification_prompt_and_data_filling

**API定义**

```python
def validate_notification_prompt_and_data_filling(
    self,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**典型场景**：服务端Agent收到Notification-T订阅报文后校验合规性并提取订阅参数（主题/条件/上报格式），据此建立订阅关系。

**功能说明**：校验一条 Notification-T 通知订阅提示词报文是否匹配模板与槽位约束，并按调用方 Schema 提取参数（订阅主题、订阅条件、上报通知数据格式等）。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| prompt | str | 是（非空） | 待校验的通知订阅提示词报文文本；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| schema | Mapping[str, object] | 是 | 调用方提供的参数 JSON Schema |
| template_uri | str | 是 | Notification-T 模板（前缀段必须为 `Notification-T`） |

**请求样例**

```python
validation_schema = {
    "type": "object",
    "properties": {
        "topic": {"type": "string", "description": "订阅主题（必选）。订阅的事件主题名称。"},
        "subscriptionCondition": {
            "type": "string", "description": "订阅条件（可选）。待订阅的条件描述。",
        },
        "notificationDataFormat": {
            "type": "string", "description": "上报通知数据格式（必选）。待上报的通知数据格式描述。",
        },
    },
    "required": ["topic", "notificationDataFormat"],
}

# prompt_text 为客户端生成的通知订阅提示词报文文本
result = server.validate_notification_prompt_and_data_filling(
    prompt_text, validation_schema, SERVICE_RECOVERY_URI,
)
```

**输出说明**

成功时返回 `FilledParamData`（结构同 [1.3.15](#1315-validate_task_prompt_and_data_filling)），`data` 为按 Schema 提取的参数（订阅主题、订阅条件、上报通知数据格式等）。

失败时抛 `ContentValidationError`（结构同 1.3.15）。错误码：

- `negotiation.invalid_input`（prompt 为 None 或空白、schema 为 None，或 templateUri 前缀段/版本与该接口不符）

- `negotiation.semantic_rejected`（必选参数缺失或取值非法；`errors` 中的逐槽位明细使用 `content.*` 码集）

- `llm.invocation_failed` / `llm.response_invalid`（LLM 失败，可重试）

- `template.not_found`（校验提示词资源缺失）

- `input.text_too_long`（提示词超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

编程错误：template_uri 为 None 抛 `TypeError`，为空白/非法抛 `ValueError`；prompt 为 None 或空白、schema 为 None 不属编程错误，以 `negotiation.invalid_input` 抛 `ContentValidationError`（见上错误码）。

**响应样例**

```text
result.data =
{
  'topic': '业务抢通事件',
  'subscriptionCondition': '子网名称：xx子网',
  'notificationDataFormat': '业务抢通事件数据包含：业务抢通方案执行状态（未启动、已结束）、投诉诊断任务流水号、OSS侧事件流水号、接入端口名称、是否已授权OMC自动抢通（是、否）、业务抢通方案名称、业务抢通方案详情。'
}
```

### 1.3.19 generate_auth_prompt_from_text

**API定义**

```python
def generate_auth_prompt_from_text(
    self, text: str, template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：客户端Agent将自然语言授权诉求（新增/修改/删除/查询动网操作授权策略）转换为Authorization-T报文。

**功能说明**：从自然语言文本按指定 Authorization-T 模板生成授权策略操作提示词报文。执行一次 LLM 槽位提取后确定性渲染模板。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| text | str | 是 | 自然语言授权描述（操作类型 + 动网操作的授权策略内容）；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| template_uri | str | 是 | Authorization-T 模板：`AUTHORIZATION_POLICY_MANAGEMENT_URI`（`Authorization-T/authorization-policy-management/v1`） |

**请求样例**

```python
from a2a_t.core.standard_templates import AUTHORIZATION_POLICY_MANAGEMENT_URI

result = client.generate_auth_prompt_from_text(
    "加个校园专网的授权，处置用业务抢通，做个隧道调优，有效期先不填后面补",
    AUTHORIZATION_POLICY_MANAGEMENT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.13](#1313-generate_task_prompt_from_text)，`extension_uri` 为 `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1`）。

失败时抛 `PromptGenerationError`（结构同 1.3.13），如操作类型不在"新增/修改/删除/查询授权策略"范围内时以 `slot.constraint_violated` 拒绝。编程错误：text 或 template_uri 为 None 抛 `TypeError`；template_uri 为空白或非法抛 `ValueError`。

**响应样例**

```text
template_uri : Authorization-T/authorization-policy-management/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1
prompt_text  :
## 授权策略的操作类型
新增授权策略

## 授权策略的操作描述
请根据<授权策略的操作类型>和<动网操作的授权策略列表>完成相应的授权操作，按照<预期输出>中定义的结构返回授权策略的操作执行结果。<预期输出>表示预期返回内容。

## 动网操作的授权策略列表
校园专网，业务抢通，隧道调优

## 预期输出
1. 授权操作执行结果，取值范围： 成功、失败、部分成功；
2. 授权操作执行成功时，返回执行成功的<动网操作的授权策略列表>；
3. 授权操作执行失败或部分成功时，返回失败列表，包含授权策略和失败原因；
```

### 1.3.20 generate_auth_prompt_from_data_with_schema

**API定义**

```python
def generate_auth_prompt_from_data_with_schema(
    self,
    data: Mapping[str, object],
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> MetadataContent
```

**典型场景**：客户端Agent将授权管理界面/系统按字段提交的结构化授权策略数据转换为Authorization-T报文。

**功能说明**：从结构化数据 + 语义 Schema 按指定 Authorization-T 模板生成授权策略操作提示词，跳过场景识别。语义约束同 1.3.14。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| data | Mapping[str, object] | 是 | 结构化授权输入（操作类型、策略数量、策略详情列表等） |
| schema | Mapping[str, object] | 是（非空） | 字段语义 JSON Schema |
| template_uri | str | 是 | Authorization-T 模板 |

**请求样例**

```python
data = {
    "操作类型": "新增授权策略",
    "策略数量": 2,
    "详情": [
        {"业务场景": "校园专网", "处置类型": "业务抢通",
         "操作名称": "隧道调优", "有效期": "永久生效"},
        {"业务场景": "医疗专线", "处置类型": "业务恢复",
         "操作名称": "频段调整", "有效期": "2026-06-01~2030-06-18"},
    ],
}

schema = {
    "type": "object",
    "properties": {
        "操作类型": {
            "type": "string",
            "enum": ["新增授权策略", "修改授权策略", "删除授权策略", "查询授权策略"],
        },
        "策略数量": {"type": "integer", "description": "要新增的策略数量"},
        "详情": {
            "type": "array",
            "description": "策略详情列表",
            "items": {
                "type": "object",
                "properties": {
                    "业务场景": {"type": "string"},
                    "处置类型": {"type": "string"},
                    "操作名称": {"type": "string"},
                    "有效期": {"type": "string"},
                },
            },
        },
    },
}

result = client.generate_auth_prompt_from_data_with_schema(
    data, schema, AUTHORIZATION_POLICY_MANAGEMENT_URI,
)
```

**输出说明**

成功时返回 `MetadataContent`（结构同 [1.3.13](#1313-generate_task_prompt_from_text)，`extension_uri` 为 `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1`）。

失败时抛 `PromptGenerationError`（结构同 1.3.13）。编程错误：入参为 None 抛 `TypeError`，template_uri 为空白或非法或 schema 为空 dict 抛 `ValueError`。

**响应样例**

```text
template_uri : Authorization-T/authorization-policy-management/v1
extension_uri: https://projects.tmforum.org/a2aproject/telecommunication/extensions/Authorization-T/v1
prompt_text  :
## 授权策略的操作类型
新增授权策略

## 授权策略的操作描述
请根据<授权策略的操作类型>和<动网操作的授权策略列表>完成相应的授权操作，按照<预期输出>中定义的结构返回授权策略的操作执行结果。<预期输出>表示预期返回内容。

## 动网操作的授权策略列表
校园专网，业务抢通，隧道调优，永久生效；医疗专线，业务恢复，频段调整，2026-06-01~2030-06-18

## 预期输出
1. 授权操作执行结果，取值范围： 成功、失败、部分成功；
2. 授权操作执行成功时，返回执行成功的<动网操作的授权策略列表>；
3. 授权操作执行失败或部分成功时，返回失败列表，包含授权策略和失败原因；
```

### 1.3.21 validate_auth_prompt_and_data_filling

**API定义**

```python
def validate_auth_prompt_and_data_filling(
    self,
    prompt: str,
    schema: Mapping[str, object],
    template_uri: str | TemplateUri,
) -> FilledParamData
```

**典型场景**：服务端Agent收到Authorization-T报文后校验合规性并提取操作类型与策略列表，据此执行授权策略管理动作。

**功能说明**：校验一条 Authorization-T 授权提示词报文是否匹配模板与槽位约束，并按调用方 Schema 提取参数（操作类型、策略列表等）。按各操作类型的字段要求做差异化校验：新增条目须含业务场景/处置类型/操作名称/有效期，修改条目须含策略标识与新有效期，删除条目可为策略标识或条件字段。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| prompt | str | 是（非空） | 待校验的授权提示词报文文本；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |
| schema | Mapping[str, object] | 是 | 调用方提供的参数 JSON Schema（含 `操作类型`、`策略列表` 等） |
| template_uri | str | 是 | Authorization-T 模板（前缀段必须为 `Authorization-T`） |

**请求样例**

```python
import json

# paramSchema 声明：操作类型（enum：新增/修改/删除/查询授权策略）、策略列表
#（数组，条目含 策略标识/业务场景/处置类型/操作名称/有效期）
with open("param-schema.json", encoding="utf-8") as f:
    param_schema = json.load(f)

result = server.validate_auth_prompt_and_data_filling(
    metadata.prompt_text, param_schema, AUTHORIZATION_POLICY_MANAGEMENT_URI,
)
```

**输出说明**

成功时返回 `FilledParamData`（结构同 [1.3.15](#1315-validate_task_prompt_and_data_filling)），`data` 为按 Schema 提取的参数（操作类型、策略列表等）。

失败时抛 `ContentValidationError`（结构同 1.3.15）。错误码：

- `negotiation.invalid_input`（prompt 为 None 或空白、schema 为 None，或 templateUri 前缀段/版本与该接口不符）

- `negotiation.semantic_rejected`（必选参数缺失或取值非法，如新增条目缺少必填字段（`content.entry_field_missing`）、有效期格式错误（`content.format_error`））

- `llm.invocation_failed` / `llm.response_invalid`（LLM 失败，可重试）

- `template.not_found`（校验提示词资源缺失）

- `input.text_too_long`（提示词超过 `A2AT_INPUT_TEXT_MAX_CHARS`）

编程错误：template_uri 为 None 抛 `TypeError`，为空白/非法抛 `ValueError`；prompt 为 None 或空白、schema 为 None 不属编程错误，以 `negotiation.invalid_input` 抛 `ContentValidationError`（见上错误码）。

**响应样例**

```text
result.data =
{
  '操作类型': '新增授权策略',
  '策略列表': [
    {'策略标识': None, '业务场景': '校园专网', '处置类型': '业务抢通', '操作名称': '隧道调优', '有效期': '永久生效'},
    {'策略标识': None, '业务场景': '医疗专线', '处置类型': '业务恢复', '操作名称': '频段调整', '有效期': '2026-06-01~2030-06-18'}
  ]
}
```

### 1.3.22 generate_task_prompt

**API定义**

```python
def generate_task_prompt(self, user_input: str | dict[str, object]) -> PromptGenerationResult
```

**典型场景**：客户端Agent的场景自动路由入口：不指定模板，由SDK识别用户输入所属业务场景并生成对应报文，适合场景集合已知、希望简化接入的场景。

**功能说明**：通用任务提示词生成入口，经场景识别自动定位模板：自然语言或结构化输入先由 LLM 识别业务场景，再按场景对应的内置模板完成槽位提取与渲染。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| user_input | str \| dict[str, object] | 是 | 任务描述：`str`（自然语言）或 `dict`（结构化输入），统一经 LLM 抽取；入参为 `str` 时输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |

**请求样例**

```python
result = client.generate_task_prompt(
    "请生成一个Incident事件订阅任务：通知主题为Incident，"
    "订阅级别为critical、medium、high、low，上报通知数据格式为DataPart"
)

if result.success:
    processed_prompt = result.prompt_text  # 作为A2A消息metadata发送
else:
    print(result.failure.code, ":", result.failure.message)
```

**输出说明**

成功时（`success` 为 `True`，不抛异常，结果随返回值返回）：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| success | bool | 始终为 True |
| prompt_text | str | 渲染后的任务提示词报文文本，作为 A2A 消息 metadata 发送 |
| failure | PromptGenerationFailure | 始终为 None |

失败时（`success` 为 `False`，不抛异常，失败负载随结果返回）：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| success | bool | 始终为 False |
| prompt_text | str | 始终为 None |
| failure | PromptGenerationFailure | 标准化失败负载，结构见下表 |

`PromptGenerationFailure` 结构：

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| code | str | 机器可读错误码，取值来自错误码列表，如 `scenario.not_matched`（场景识别未命中）、`input.text_too_long`（输入长度防护）、`template.load_failed`（提示词资源加载失败）、`template.not_found`（模板缺失）、`slot.schema_not_found`（槽位 Schema 缺失）、`slot.not_provided`（必填槽缺失）、`template.render_failed`（渲染失败）、`llm.invocation_failed` / `llm.response_invalid`（LLM 失败） |
| message | str | 人类可读的失败描述 |
| stage | str \| None | 失败发生的阶段：`input`（输入长度防护）、`scenario`（场景识别）、`preparation`（模板/槽位/提示词资源加载）、`generation`（LLM 槽位提取等生成过程）、`validation`（槽位校验）、`render`（模板渲染） |

`PromptGenerationResult` 与 `PromptGenerationFailure` 均提供 `to_dict()`，可直接 JSON 序列化。

编程错误：`user_input` 既不是 `str` 也不是 `dict` 时抛 `TypeError`；为空白字符串或空 `dict` 时抛 `ValueError`。

**响应样例**

成功时（实际文本随 LLM 槽位提取结果变化）：

```text
result.success = True
result.prompt_text =
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
该主题的名称是"incident"

## 订阅条件
故障级别为"critical"、"medium"、"high"、"low"

## 上报通知数据格式
通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

失败时（输入无法命中任何内置场景）：

```text
result.success = False
result.failure =
PromptGenerationFailure(code='scenario.not_matched', message='输入内容无法匹配任何已知场景：<原因>', stage='scenario')
```

### 1.3.23 check_task_prompt

**API定义**

```python
def check_task_prompt(self, *, processed_prompt_text: str) -> PromptComplianceResult
```

**典型场景**：服务端Agent对收到的任务报文做协议完备性校验（场景/模板/槽位合规），只需通过与失败结论、无需提取参数的场景。

**功能说明**：通用任务提示词合规校验入口（服务端）：对客户端下发的 processed task prompt 执行场景匹配、模板遵从性校验与槽位校验。与 `validate_task_prompt_and_data_filling` 的区别：本接口不提取参数、不接收调用方 Schema，仅给出通过与失败的标准化结论。

**输入说明**

| 参数 | 类型 | 必填 | 说明 |
| ---- | ---- | ---- | ---- |
| processed_prompt_text | str | 是 | 客户端下发的 A2A-T 协议报文文本（`Message.metadata` 中以扩展 URI 为 key 的值）；输入长度受配置项 `A2AT_INPUT_TEXT_MAX_CHARS` 限制，默认值为 16384 |

**请求样例**

```python
result = server.check_task_prompt(processed_prompt_text=processed_prompt)

if result.success:
    print("prompt check passed")
else:
    print(result.failure.message)
```

**输出说明**

成功时（`success` 为 `True`，不抛异常，结果随返回值返回）：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| success | bool | 始终为 True |
| failure | PromptComplianceFailure | 始终为 None |

失败时（`success` 为 `False`，不抛异常，失败负载随结果返回）：

| 字段/方法 | 类型 | 说明 |
| --------- | ---- | ---- |
| success | bool | 始终为 False |
| failure | PromptComplianceFailure | 标准化失败负载，结构见下表 |

`PromptComplianceFailure` 结构：

| 字段 | 类型 | 说明 |
| ---- | ---- | ---- |
| code | str | 机器可读错误码，取值来自错误码列表：`scenario.not_matched`（报文解析/场景识别失败）、`template.not_found`（模板缺失）、槽位域码如 `slot.not_provided`（必填缺失）、`slot.constraint_violated`（取值越界）、`slot.rule_violation`（其它槽位规则违规）、`input.text_too_long`（输入长度防护） |
| message | str | 人类可读的失败描述 |
| stage | str | 失败发生的阶段：`input_gate`（输入长度防护）、`prompt_parse`（场景解析失败）、`preparation`（模板/槽位/提示词资源加载）、`slot_extraction`（LLM 槽位提取）、`slot_validation`（规则/语义槽位校验） |

`PromptComplianceResult` 与 `PromptComplianceFailure` 均提供 `to_dict()`，可直接 JSON 序列化。

**响应样例**

成功时：

```text
result.success = True
```

失败时：

```text
result.success = False
result.failure =
PromptComplianceFailure(code='slot.not_provided', message='输入中未提供「任务对象」。', stage='slot_validation')
```

## 1.4 错误码列表

**错误码分类**：BUSINESS = 调用方可行动的预期业务失败，由 `A2ATBusinessError` 子类携带；INFRA = 基础设施失败，由普通 `A2ATError` 携带。

| 错误码                                    | 类别     | 消息（zh-CN）                                                | 消息（en-US）                                                |
| ----------------------------------------- | -------- | ------------------------------------------------------------ | ------------------------------------------------------------ |
| `template.not_found`                      | BUSINESS | 模板「{template_uri}」不存在 | Template '{template_uri}' does not exist |
| `template.render_failed`                  | BUSINESS | 模板「{template_uri}」渲染失败:{reason}                      | Failed to render template '{template_uri}': {reason}         |
| `template.load_failed`                    | INFRA    | 模板资源「{resource_path}」读取失败                          | Failed to read template resource '{resource_path}'           |
| `slot.schema_not_found`                   | BUSINESS | 模板「{template_uri}」缺少参数定义文件(语言「{language}」)   | Template '{template_uri}' is missing its slot schema (language '{language}') |
| `slot.not_provided`                       | BUSINESS | 输入中未提供「{slot_label}」。                               | '{slot_label}' is not provided in the input.                 |
| `slot.constraint_violated`                | BUSINESS | 「{slot_label}」的取值「{actual}」不在允许范围内             | The value of '{slot_label}' ({actual}) is not within the allowed range |
| `slot.semantic_conflict`                  | BUSINESS | 「{slot_label}」的取值与参数定义冲突:{reason}                | The value of '{slot_label}' conflicts with the slot definition: {reason} |
| `slot.fabricated_value`                   | BUSINESS | 「{slot_label}」的取值「{actual}」是占位内容,不是有效值      | The value of '{slot_label}' ({actual}) is placeholder content, not a valid value |
| `slot.cross_scenario_pollution`           | BUSINESS | 「{slot_label}」的取值混入了其他场景的内容                   | The value of '{slot_label}' contains content from a different scenario |
| `slot.insufficient_grounding`             | BUSINESS | 「{slot_label}」的取值缺少充分依据                           | The value of '{slot_label}' lacks sufficient grounding       |
| `slot.rule_violation`                     | BUSINESS | 「{slot_label}」的取值不符合校验规则。                       | The value of '{slot_label}' violates the validation rules.   |
| `input.text_too_long`                     | BUSINESS | 输入文本长度 {actual_length} 超过上限 {max_chars}(A2AT_INPUT_TEXT_MAX_CHARS) | Input text length {actual_length} exceeds the maximum of {max_chars} (A2AT_INPUT_TEXT_MAX_CHARS) |
| `content.param_missing`                   | BUSINESS | 「{section_label}」未填写,请补充该参数的取值                 | '{section_label}' is empty; please provide a value           |
| `content.entry_field_missing`             | BUSINESS | 「{section_label}」第 {index} 条缺少必填字段「{field_label}」 | Entry {index} of '{section_label}' is missing required field '{field_label}' |
| `content.format_error`                    | BUSINESS | 「{section_label}」的取值格式不符合要求:{reason}             | The format of '{section_label}' is invalid: {reason}         |
| `content.value_not_allowed`               | BUSINESS | 「{section_label}」的取值「{actual}」不在允许范围内          | The value of '{section_label}' ({actual}) is not allowed     |
| `content.semantic_conflict`               | BUSINESS | 「{section_label}」存在语义冲突:{reason}                     | '{section_label}' has a semantic conflict: {reason}          |
| `content.rule_violation`                  | BUSINESS | 「{section_label}」的取值不符合校验规则。                    | The value of '{section_label}' violates the validation rules. |
| `scenario.not_matched`                    | BUSINESS | 输入内容无法匹配任何已知场景:{reason}                        | The input does not match any known scenario: {reason}        |
| `llm.not_configured`                      | BUSINESS | 未配置 LLM 客户端,无法执行该操作(请检查 A2AT_LLM_* 配置)     | No LLM client is configured; check the A2AT_LLM_* settings   |
| `llm.invocation_failed`                   | BUSINESS | LLM 调用失败(提供方 {provider}):{reason}                     | LLM invocation failed (provider {provider}): {reason}        |
| `llm.response_invalid`                    | BUSINESS | LLM 返回内容不符合要求({step} 步骤),请重试                   | The LLM response is invalid (step: {step}); please retry     |
| `negotiation.invalid_input`               | BUSINESS | 输入的协商内容无效:{reason}                                  | The negotiation input is invalid: {reason}                   |
| `negotiation.invalid_context_id`          | BUSINESS | 协商上下文标识「{actual}」不是合法的 UUID                    | The negotiation context id '{actual}' is not a valid UUID    |
| `negotiation.round_exceeded`              | BUSINESS | 协商轮次 {round} 已超过上限 {max_rounds}                     | Negotiation round {round} exceeds the maximum of {max_rounds} |
| `negotiation.type_mismatch`               | BUSINESS | 报文内容属于「{implied}」协商,与声明的模板类型「{declared}」不符 | The message implies '{implied}' negotiation but the declared template type is '{declared}' |
| `negotiation.phase_mismatch`              | BUSINESS | 报文阶段与声明的模板阶段不符({implied} vs {declared})        | The message phase does not match the declared template phase ({implied} vs {declared}) |
| `negotiation.conclusion_mismatch`         | BUSINESS | 报文结论为「{actual}」,与该方法的预期「{expected}」不符      | The message conclusion is '{actual}' but '{expected}' is expected for this method |
| `negotiation.content_invalid`             | BUSINESS | 协商内容字段「{field}」无效:{reason}                         | The negotiation content field '{field}' is invalid: {reason} |
| `negotiation.field_missing`               | BUSINESS | 协商报文缺少必填字段「{field}」                              | The negotiation message is missing required field '{field}'  |
| `negotiation.content_extract_failed`      | BUSINESS | 无法从文本提取协商内容({field}):{reason}                     | Failed to extract negotiation content from text ({field}): {reason} |
| `negotiation.conclusion_content_mismatch` | BUSINESS | 结论为「{conclusion}」,但「{section_label}」未表达该结论应携带的内容 | The conclusion is '{conclusion}' but '{section_label}' does not state the content the conclusion requires |
| `negotiation.missing_result_content`      | BUSINESS | 「{section_label}」板块缺少结论应携带的内容                  | The '{section_label}' section is missing the content required by its conclusion |
| `negotiation.mutually_exclusive_sections` | BUSINESS | 互斥板块同时出现:{sections}                                  | Mutually exclusive sections appear together: {sections}      |
| `negotiation.constraint_conflict`         | BUSINESS | 「{section_label}」与既有约束冲突:{reason}                   | '{section_label}' conflicts with existing constraints: {reason} |
| `negotiation.field_inconsistency`         | BUSINESS | 「{section_label}」内字段取值前后不一致:{reason}             | Fields within '{section_label}' are inconsistent: {reason}   |
| `negotiation.invalid_time_interval`       | BUSINESS | 「{section_label}」的时间区间不合法(开始时间不得晚于结束时间) | The time interval of '{section_label}' is invalid (start must not be later than end) |
| `negotiation.semantic_rejected`           | BUSINESS | 协商报文语义校验未通过                                       | The negotiation message failed semantic validation           |
| `negotiation.rule_violation`              | BUSINESS | 「{section_label}」不符合协商报文的校验规则。                | '{section_label}' violates the negotiation message validation rules. |
| `infra.config_invalid`                    | INFRA    | 配置项「{key}」无效:{reason}                                  | Invalid configuration '{key}': {reason}                      |
| `infra.resource_read_failed`              | INFRA    | 资源「{resource_path}」读取失败                              | Failed to read resource '{resource_path}'                    |
| `infra.internal_error`                    | INFRA    | SDK 内部错误,请联系维护方并提供上下文                        | SDK internal error; contact the maintainer with context      |