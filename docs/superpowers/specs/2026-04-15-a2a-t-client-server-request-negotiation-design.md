# A2A-T Client/Server 请求协商设计

## 1. 设计目标

本设计面向如下场景：

- A2A-T Client 基于原生 `a2a-python` client 发起请求
- A2A-T Server 基于原生 `a2a-python` server 接收请求
- 服务端在执行当前请求前，发现当前请求缺少必要参数、缺少依赖参数，或者存在可修正的非法参数
- 服务端不直接拒绝，而是在当前请求生命周期内发起“请求协商”
- 服务端返回一段自然语言协商 prompt，说明当前请求要继续执行还需要补齐或修改哪些参数
- 客户端收到协商 prompt 后，识别本轮需要补齐的槽位
- 客户端 provider 为这些槽位提供值
- 客户端复用当前 `PromptClient.generate_a2a_t_prompt()` 生成携带补参槽位的新 prompt
- 客户端将补参 prompt 和 `delta_slots` 回传给服务端
- 协商成功后，服务端继续执行当前请求，而不是要求业务方重新发起一轮全新请求

本设计目标是：

1. 在 `a2a-python` 原生 client/server 基础上，为 A2A-T 提供双端协商能力
2. 复用现有 client 侧 `prompt_generation` 和 server 侧 `prompt_compliance`
3. 让 client SDK 对业务方提供“自动协商”体验
4. 让 server 成为协商状态主控方，统一控制轮数、超时和恢复执行
5. 在协议层通过事件流扩展承载协商，而不是另起独立 API

## 2. 非目标

本轮设计不负责：

- 在服务端自动替客户端生成最终补参内容
- 用 LLM 自动改写业务原始意图
- 定义新的独立 HTTP negotiation API
- 实现分布式共享 session store
- 实现真正的 prompt 模板渲染引擎
- 让所有 `prompt_compliance` 失败都进入协商

## 3. 范围约束

第一版仅支持以下可协商问题：

- 必填槽位缺失
- 依赖槽位缺失
- 可由客户端重新提供值的非法槽位

第一版不进入协商的情况包括：

- guardrail 拒绝
- processed prompt 解析失败
- 模板/资源加载失败
- 首次请求的槽位提取失败
- session 不存在、已过期或已拒绝

## 4. 核心原则

### 4.1 自动协商优先

对接入方暴露的默认体验应为自动协商。业务方通过 A2A-T client 发起一次请求，SDK 在底层自动处理协商事件、调用 provider、提交补参、继续执行。

### 4.2 事件流承载

协商流程应基于原生 `a2a-python` 的任务状态/事件流扩展承载，而不是定义独立 negotiation API。

### 4.3 服务端主控

服务端负责：

- 判断是否进入协商
- 生成协商问题和自然语言提示
- 持有协商 session
- 控制最大轮数和超时
- 决定继续执行还是终止当前请求

客户端负责：

- 识别 negotiation 事件
- 识别本轮需要补齐的槽位
- 调用 provider 获取槽位值
- 复用当前 prompt generation 生成补参 prompt
- 将补参 prompt 和 `delta_slots` 回传给服务端

### 4.4 自然语言协商提示

服务端必须向客户端返回一段自然语言 prompt，明确说明：

- 当前请求还缺少哪些参数
- 哪些参数需要修改
- 哪些参数存在依赖关系
- 客户端应补充什么信息才能继续完成当前请求

客户端默认根据该提示识别本轮需要补齐的槽位，调用 provider 获取槽位值，然后复用当前 `PromptClient.generate_a2a_t_prompt()` 生成携带补参槽位的新 prompt，再提交给服务端。

## 5. 总体架构

```text
业务方
-> A2A-T Client
   -> prompt_generation
   -> negotiation driver
   -> requested-slot extractor
   -> negotiation provider
-> a2a-python 原生 client
-> 事件流
-> a2a-python 原生 server
-> A2A-T Server
   -> PromptHandler
   -> prompt_compliance
   -> negotiation service
   -> negotiation prompt generator
   -> session store
-> 下游任务执行
```

### 5.1 Client 侧职责

- `prompt_generation` 负责将输入生成 `processed_prompt_text`
- `negotiation driver` 负责消费协商事件并驱动后续动作
- `requested-slot extractor` 负责识别本轮待补槽位
- `negotiation provider` 负责为本轮待补槽位提供具体值
- `prompt_generation` 负责把 provider 返回的槽位值重新生成携带补参信息的 A2A-T prompt
- `extended_client` 负责将自动协商接入现有调用入口

### 5.2 Server 侧职责

- `PromptHandler` 负责入口编排和结果路由
- `prompt_compliance` 负责首次请求的安全检查、prompt 解析、槽位提取、槽位校验
- `negotiation service` 负责协商 session 和多轮流转
- `negotiation prompt generator` 负责将结构化问题转成自然语言提示
- `negotiation service` 负责消费 client 回传的 `delta_slots`、执行合并策略和重新校验

## 6. 协议与交互流程

### 6.1 首次请求

```text
client 发起任务请求
-> server 执行 prompt_compliance.check()
-> 若通过：继续原任务
-> 若失败且属于可协商问题：
   -> server 创建 negotiation session
   -> server 生成 negotiation_prompt
   -> server 发出 a2at.negotiation.required 事件
-> 若失败且不可协商：
   -> server 直接拒绝当前请求
```

### 6.2 协商继续

```text
client 收到 a2at.negotiation.required
-> negotiation driver 从 negotiation_prompt / issues / questions 中识别 requested_slots
-> negotiation driver 调用 provider 获取 slot_values
-> client 复用 prompt_generation 生成 response_prompt
-> client 发出 a2at.negotiation.continue
-> server 加载 session
-> server 读取 client 回传的 delta_slots
-> server 合并 effective_slots
-> server 重新执行 slot 校验
-> 若通过：
   -> 恢复当前请求执行
-> 若仍缺失但未超限：
   -> 生成下一轮 negotiation_prompt
   -> 再次发出 a2at.negotiation.required
-> 若超轮数/超时/放弃：
   -> 发出 a2at.negotiation.rejected
```

### 6.3 事件类型

建议定义以下 A2A-T 扩展事件：

- `a2at.negotiation.required`
- `a2at.negotiation.continue`
- `a2at.negotiation.rejected`

## 7. 数据模型

### 7.1 PromptComplianceResult 增强

当前 `PromptComplianceResult` 只包含：

- `passed`
- `stage`
- `extracted_slots`
- `error_code`
- `error_message`

为了支持 negotiation，需要新增结构化问题输出：

```python
SlotIssue
```

建议字段：

- `slot_name: str`
- `issue_type: Literal["missing_required", "dependency_missing", "value_invalid"]`
- `message: str`
- `depends_on: str | None`
- `expected: object | None`
- `actual: object | None`

并增强 `PromptComplianceResult`：

- `slot_issues: list[SlotIssue] | None`
- `extracted_slots: dict[str, object] | None`
- `prompt_identity: PromptReference | None`

### 7.2 NegotiationSession

```python
NegotiationSession
```

建议字段：

- `session_id: str`
- `task_id: str`
- `prompt_identity: PromptReference`
- `processed_prompt_text: str`
- `base_slots: dict[str, object]`
- `negotiated_slots: dict[str, object]`
- `issues: list[SlotIssue]`
- `status: Literal["pending", "completed", "rejected", "expired"]`
- `attempt_count: int`
- `max_attempts: int`
- `created_at: datetime`
- `expires_at: datetime`

说明：

- `base_slots` 表示首次 compliance 提取出的槽位
- `negotiated_slots` 表示多轮协商中累计补齐的槽位
- 最终有效槽位按如下方式合并：

```text
effective_slots = {**base_slots, **negotiated_slots}
```

### 7.3 NegotiationRequiredEventPayload

建议字段：

- `session_id: str`
- `task_id: str`
- `attempt_count: int`
- `max_attempts: int`
- `expires_at: str`
- `issues: list[NegotiationIssue]`
- `questions: list[NegotiationQuestion]`
- `negotiation_prompt: str`
- `language: str`
- `expected_response_format: Literal["prompt"]`

### 7.4 NegotiationContinuePayload

建议字段：

- `session_id: str`
- `task_id: str`
- `response_prompt: str`
- `delta_slots: dict[str, object]`
- `response_format: Literal["prompt"]`

### 7.5 NegotiationRejectedEventPayload

建议字段：

- `session_id: str`
- `task_id: str`
- `reason: str`
- `message: str`

## 8. 服务端自然语言协商提示

### 8.1 设计要求

服务端必须新增 prompt 生成 API，用于根据缺失/非法槽位问题生成自然语言提示。

建议接口：

```python
NegotiationPromptGenerator
```

建议方法：

- `generate(issues, slot_schema, current_slots, language, attempt_count) -> str`

输出目标：

- 明确说明哪些参数缺失
- 明确说明哪些参数需要修改
- 在依赖场景下明确说明条件关系
- 语言与当前 prompt 语言保持一致

### 8.2 第一版实现建议

第一版默认采用规则生成：

- `RuleBasedNegotiationPromptGenerator`

原因：

- 文案可预测
- 便于测试
- 不额外引入服务端 LLM 依赖

同时预留可选实现：

- `LLMNegotiationPromptGenerator`

## 9. 客户端协商响应模型

### 9.1 Provider 设计

客户端收到服务端 `negotiation_prompt` 后，应先识别本轮需要补齐的槽位，再由 provider 为这些槽位提供值。

建议接口：

```python
NegotiationProvider
```

建议方法：

- `provide_values(requested_slots, context) -> NegotiationProviderResult`

建议 `NegotiationProviderResult` 至少包含：

- `slot_values: dict[str, object]`
- `cancelled: bool`
- `message: str | None`

### 9.2 第一版 provider 策略

第一版采用“内置交互式 provider + 可替换 provider”：

- 默认 `InteractiveNegotiationProvider`
- 允许业务方替换为自定义 provider

默认 provider 行为：

- 展示服务端返回的自然语言提示
- 展示本轮需要补齐或修改的槽位
- 收集每个槽位对应的值
- 将 `slot_values` 返回给 driver

### 9.3 requested_slots 提取

服务端返回的 `negotiation_prompt` 是自然语言 prompt。客户端需要复用当前 prompt 能力，从该 prompt 中识别本轮需要补齐的槽位。

建议新增 client 侧组件：

```python
RequestedSlotExtractor
```

建议方法：

- `extract(negotiation_prompt, issues, questions) -> list[RequestedSlot]`

实现策略：

- 优先使用服务端事件中的结构化 `issues/questions`
- 当结构化字段不足时，再复用现有 prompt analysis/runtime 能力从 `negotiation_prompt` 中提取
- 不重新发明一套独立提槽逻辑，尽量复用现有 shared prompt runtime 能力

### 9.4 response_prompt 生成

provider 返回 `slot_values` 后，client 不手写 response prompt，而是复用当前 `PromptClient.generate_a2a_t_prompt()`。

当前 prompt generation 已支持：

- 自然语言字符串输入
- JSON 字符串输入
- `dict[str, object]` 输入

因此 negotiation driver 可以将 provider 结果组织为：

```python
slot_values: dict[str, object]
```

并调用现有 prompt generation 链路生成 `response_prompt`。

## 10. delta_slots 合并策略

客户端回传 `a2at.negotiation.continue` 时携带 `delta_slots`。服务端不能无条件将其并入当前请求上下文，否则可能导致多传槽位污染、越权修改和状态漂移。

因此需要定义可配置的槽位合并策略接口：

```python
NegotiationSlotMergePolicy
```

建议方法：

- `filter(delta_slots, issues, slot_schema, current_slots) -> SlotMergeDecision`

建议输出：

- `accepted_slots: dict[str, object]`
- `ignored_slots: dict[str, object]`
- `rejected_slots: dict[str, object]`
- `should_abort: bool`
- `abort_reason: str | None`

推荐默认实现：

- `IgnoreUnknownNegotiationSlotMergePolicy`

默认规则：

- 仅允许以下槽位进入 `accepted_slots`：
  - 当前 `issues` 直接涉及的槽位
  - 这些槽位的显式依赖槽位
  - schema 中存在且被服务端判定为本轮可补充的槽位
- schema 中不存在、但又不属于危险字段的多传槽位：
  - 默认忽略
  - 记录日志
  - 不进入 `effective_slots`
- 命中受保护槽位、保留字段或明显越权字段时：
  - 标记为 `rejected_slots`
  - 可直接终止本轮协商

建议合并流程：

```text
client slot_values
-> client prompt_generation.generate()
-> response_prompt + delta_slots
-> slot_merge_policy.filter()
-> accepted_delta_slots
-> effective_slots = {**base_slots, **negotiated_slots, **accepted_delta_slots}
```

### 10.1 扩展性要求

第一版默认采用“忽略并记录日志”的策略，但必须允许用户注入自定义实现，例如：

- 多传即失败
- 多传但回告客户端被丢弃的字段
- 允许某些扩展槽位在特定场景下透传

因此 `NegotiationService` 不应把过滤逻辑写死在内部，而应依赖 `NegotiationSlotMergePolicy` 接口。

## 11. 多轮、超时与状态控制

### 11.1 配置模型

建议在 `A2ATConfig` 中新增：

```python
NegotiationConfig
```

建议字段：

- `enabled: bool = True`
- `max_attempts: int = 3`
- `session_timeout_seconds: int = 300`
- `provider_timeout_seconds: int = 300`
- `store_backend: str = "memory"`
- `provider_type: str = "interactive"`
- `auto_continue: bool = True`

### 11.2 环境变量建议

- `A2AT_NEGOTIATION_ENABLED=true`
- `A2AT_NEGOTIATION_MAX_ATTEMPTS=3`
- `A2AT_NEGOTIATION_SESSION_TIMEOUT_SECONDS=300`
- `A2AT_NEGOTIATION_PROVIDER_TIMEOUT_SECONDS=300`
- `A2AT_NEGOTIATION_PROVIDER_TYPE=interactive`
- `A2AT_NEGOTIATION_AUTO_CONTINUE=true`

### 11.3 规则

- 最大轮数由 server 控制
- session 超时由 server 时间判定
- provider 超时、取消、空回复视为客户端放弃
- 每轮 `a2at.negotiation.required` 计一次协商轮次
- 第一版使用内存 store

## 12. 模块划分

### 12.1 Server

建议在现有 `src/a2a_t/server/negotiation/` 下承接正式实现：

```text
src/a2a_t/server/negotiation/
  __init__.py
  models.py
  store.py
  translator.py
  prompt_generator.py
  slot_merge_policy.py
  service.py
  errors.py
```

各文件职责：

- `models.py`
  - negotiation 相关数据模型
- `store.py`
  - session store 接口和默认内存实现
- `translator.py`
  - `SlotIssue -> NegotiationIssue / NegotiationQuestion`
- `prompt_generator.py`
  - 自然语言协商提示生成
- `slot_merge_policy.py`
  - 负责筛选和处理 `delta_slots`
- `service.py`
  - negotiation 主编排
- `errors.py`
  - negotiation 域内异常

### 12.2 Client

建议新增：

```text
src/a2a_t/client/negotiation/
  __init__.py
  models.py
  provider.py
  requested_slot_extractor.py
  interactive_provider.py
  driver.py
```

各文件职责：

- `models.py`
  - provider 输入输出模型
- `provider.py`
  - provider 接口
- `requested_slot_extractor.py`
  - 从服务端协商 prompt / issues / questions 中识别本轮待补槽位
- `interactive_provider.py`
  - 默认交互式实现
- `driver.py`
  - 自动协商驱动

## 13. 与现有模块的集成

### 13.1 Server 集成点

- `PromptHandler`
  - 负责 normal / negotiation_required / rejected 路由
- `prompt_compliance`
  - 负责首次请求的完整校验
- `negotiation.service`
  - 负责 start / continue / reject

### 13.2 Client 集成点

- `PromptClient`
  - 继续负责 prompt 生成
- `ExtendedClient`
  - 负责自动 negotiation 事件处理
- `negotiation.driver`
  - 作为 `ExtendedClient` 的内部能力

## 14. 测试设计

### 14.1 Server 单元测试

- `SlotIssue` 构造与翻译
- `NegotiationPromptGenerator` 文案生成
- `NegotiationSlotMergePolicy` 默认策略
- `NegotiationSessionStore` 的 create/get/save/delete/cleanup
- `service.start()`
- `service.continue_()`
- 超时、超轮数、取消、非法值修正失败

### 14.2 Client 单元测试

- requested-slot extractor 正确识别本轮待补槽位
- provider 输出 `slot_values`
- driver 复用 prompt generation 生成 `response_prompt`
- driver 正确发送携带 `response_prompt` 和 `delta_slots` 的 `a2at.negotiation.continue`
- provider 超时/取消分支

### 14.3 集成测试

- 首次请求缺参 -> 进入 negotiation
- client 自动补参 -> server 校验成功 -> 请求继续执行
- 多轮协商成功
- 超时失败
- 超轮数失败

## 15. 分阶段实施建议

建议实施顺序：

1. 扩展 `prompt_compliance` 输出结构化 `SlotIssue`
2. 新增 server negotiation 数据模型和 store
3. 实现 `translator.py`
4. 实现 `prompt_generator.py`
5. 实现 `slot_merge_policy.py`
6. 实现 `service.start()`
7. 实现 `service.continue_()`
8. 接入 `PromptHandler`
9. 新增 client requested-slot extractor、provider 和 driver
10. 让 driver 复用 `PromptClient.generate_a2a_t_prompt()`
11. 接入 `ExtendedClient`
12. 补齐双端集成测试

## 16. 最终建议

最终推荐方案为：

- 协商能力以 `a2a-python` 原生事件流扩展承载
- 服务端主控协商状态
- 服务端向客户端返回自然语言 `negotiation_prompt`
- 客户端基于该提示识别待补槽位
- provider 为对应槽位提供值
- 客户端复用现有 prompt generation 生成携带补参槽位的 `response_prompt`
- 服务端使用 client 回传的 `delta_slots` 执行合并策略和校验
- 协商成功后继续执行当前请求
- 第一版支持多轮协商，并支持配置最大轮数和超时时间
