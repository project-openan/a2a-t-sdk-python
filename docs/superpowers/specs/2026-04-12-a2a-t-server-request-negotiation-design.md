# A2A-T 服务端请求协商设计文档

## 1. 设计目标

A2A-T 服务端请求协商模块用于处理这样一种场景：

- 服务端收到客户端发起的请求
- 在请求正式下发给下层 Agent 之前，发现当前请求缺少必要参数
- 服务端向客户端发起协商，要求客户端补齐参数
- 如果协商完成后参数被成功补齐，则**当前请求继续执行**
- 如果协商失败、超时或超过次数限制，则**当前请求被拒绝**

本模块的核心目标是：

1. 让“参数不完整”的请求在**同一次请求生命周期内**完成协商与恢复
2. 复用现有 `prompt_compliance` 的槽位提取与槽位校验能力
3. 基于现有 `slot.json` 识别缺失槽位、依赖槽位和非法槽位
4. 同时返回：
   - 协议层可消费的结构化问题信息
   - 客户端可直接展示的协商问题列表
5. 为后续“基于原始 prompt 模板渲染完整 prompt”的能力预留清晰接口

## 2. 非目标

本轮设计不负责：

- 在服务端内部借助 LLM 自动补齐参数
- 将客户端补参后的请求重新视为一次全新请求
- 定义 prompt 模板占位符语法
- 实现原始 prompt 的真实渲染逻辑
- 实现分布式共享的协商状态存储
- 实现持久化会话存储
- 引入 negotiation 专用配置文件
- 基于 LLM 动态生成协商问题文案

## 3. 需求约束

根据已确认的需求，本模块必须满足以下约束：

1. 协商对象是客户端，不是服务端内部的 LLM 自动补全
2. 服务端协商响应需要同时提供：
   - 缺失/非法槽位等底层问题信息
   - 可直接渲染给用户的问题列表
3. 槽位完整性判断依据直接复用现有 `slot.json`
4. 当前请求的参数来源直接复用 `prompt_compliance` 的槽位提取结果
5. 协商能力与 `prompt_compliance` 解耦，但由 `prompt_compliance` 提供问题发现结果
6. 协商成功后必须让**当前请求继续执行**
7. 第一版需要引入协商会话状态，但只要求默认内存实现，同时预留持久化扩展点
8. 最终下发给下层 Agent 的仍然必须是一份**完整 prompt**
9. 完整 prompt 基于 original prompt 重新生成，但具体渲染逻辑本轮只做接口预留

## 4. 总体架构

本需求采用三层职责拆分：

```text
客户端请求
-> PromptComplianceService
   -> 安全护栏
   -> processed prompt 解析
   -> original prompt 回取
   -> 槽位提取
   -> slot.json 校验
-> RequestNegotiationService
   -> 判断是否可协商
   -> 创建/恢复 NegotiationSession
   -> 生成协商问题
   -> 处理客户端补参
   -> 协商成功后恢复当前请求
-> PromptRenderService（预留接口）
   -> 基于 original prompt + effective_slots 生成完整 prompt
-> 下层 Agent
```

职责边界如下：

- `prompt_compliance`
  - 负责识别请求是否缺少必要参数
  - 不负责管理协商会话
  - 不负责生成客户端协商协议

- `request_negotiation`
  - 负责把结构化槽位问题转换为协商结果
  - 负责管理一次请求内的协商挂起/恢复
  - 负责协调恢复执行

- `prompt renderer`
  - 负责将最终有效槽位回填到 original prompt
  - 本轮只定义接口，不落地具体模板语法和渲染实现

## 5. 与现有模块的关系

### 5.1 与 `prompt_compliance` 的关系

本需求不重复实现以下能力：

- processed prompt front matter 解析
- original prompt 回取
- 槽位提取
- `slot.json` 加载
- 槽位规则校验

请求协商模块直接复用 `prompt_compliance` 的输出。

因此需要对 `prompt_compliance` 做最小增强：

1. 当槽位校验失败时，不只返回 `error_code` 和 `error_message`
2. 还要提供结构化的槽位问题明细，供 negotiation 模块消费

### 5.2 与历史 `info_negotiate.py` 的关系

`src/a2a_t/server/negotiation/info_negotiate.py` 只作为历史参考，不直接复用。

原因是：

- 它与当前 `prompt_compliance` 架构不对齐
- 它不是围绕 `slot.json` 规则驱动设计
- 它不符合当前项目的模块边界和双语风格要求

## 6. 模块划分

建议新增目录：

```text
src/a2a_t/server/request_negotiation/
  __init__.py
  models.py
  store.py
  translator.py
  service.py
  renderer.py
  errors.py
```

各文件职责如下：

- `models.py`
  - 定义 negotiation 相关数据模型
- `store.py`
  - 定义协商会话存储接口
  - 提供默认内存实现
- `translator.py`
  - 负责把 `prompt_compliance` 的槽位问题转换为 negotiation 问题与问题文案
- `service.py`
  - negotiation 主编排层
  - 负责开启协商、继续协商、恢复执行
- `renderer.py`
  - 定义完整 prompt 渲染接口
  - 本轮不提供真实模板渲染逻辑
- `errors.py`
  - 仅保留 negotiation 域内真正需要的错误类型

同时最小化调整：

- `src/a2a_t/server/prompt_compliance/models.py`
- `src/a2a_t/server/prompt_compliance/service.py`
- `src/a2a_t/server/prompt_handler.py`

## 7. 核心数据模型

### 7.1 Prompt Compliance 输出增强

为支撑请求协商，`prompt_compliance` 需要新增结构化槽位问题模型。

建议新增：

```python
SlotIssue
```

字段建议为：

- `slot_name: str`
- `issue_type: Literal["missing_required", "dependency_missing", "value_invalid"]`
- `message: str`
- `depends_on: str | None`
- `expected: object | None`
- `actual: object | None`

同时在 `PromptComplianceResult` 中补充：

- `slot_issues: list[SlotIssue] | None`
- `extracted_slots: dict[str, object] | None`
- `prompt_identity: PromptIdentity | None`
- `original_prompt: Prompt | None`

说明：

- `slot_issues` 用于交给 negotiation 模块消费
- `extracted_slots` 用于生成协商会话初始槽位状态
- `prompt_identity` 用于定位当前 prompt
- `original_prompt` 用于后续完整 prompt 渲染

### 7.2 NegotiationSession

`NegotiationSession` 表示一次仍在进行中的请求协商会话。

建议字段：

- `session_id: str`
- `request_id: str`
- `original_request: dict[str, object]`
- `processed_prompt_text: str`
- `prompt_identity: PromptIdentity`
- `original_prompt: Prompt`
- `base_slots: dict[str, object]`
- `negotiated_slots: dict[str, object]`
- `issues: list[SlotIssue]`
- `status: Literal["pending", "completed", "rejected", "expired"]`
- `attempt_count: int`
- `max_attempts: int`
- `created_at: datetime`
- `expires_at: datetime`

说明：

- `base_slots` 表示首次由 `prompt_compliance` 提取出的槽位
- `negotiated_slots` 表示客户端在协商过程中补充的槽位
- 恢复执行时使用：

```text
effective_slots = {**base_slots, **negotiated_slots}
```

### 7.3 NegotiationIssue

`NegotiationIssue` 是对外协议层的问题模型，来源于 `SlotIssue` 的翻译结果。

建议字段：

- `slot_name: str`
- `issue_type: str`
- `message: str`

### 7.4 NegotiationQuestion

`NegotiationQuestion` 用于客户端直接渲染交互问题。

建议字段：

- `slot_name: str`
- `prompt: str`
- `required: bool`
- `reason: str`
- `expected_type: str | None`
- `allowed_values: list[object] | None`

### 7.5 NegotiationRequiredResponse

当服务端要求客户端继续协商时返回该结构。

建议字段：

- `status: Literal["negotiation_required"]`
- `session_id: str`
- `request_id: str`
- `issues: list[NegotiationIssue]`
- `questions: list[NegotiationQuestion]`
- `attempt_count: int`
- `max_attempts: int`
- `expires_at: str`

### 7.6 NegotiationContinueRequest

客户端继续协商时，不回传缺失槽位名，而是回传**补充后的槽位值**。

建议字段：

- `session_id: str`
- `request_id: str`
- `provided_slots: dict[str, object]`

这表示客户端明确告诉服务端：

- 本轮补了哪些槽位
- 每个槽位的具体值是什么

### 7.7 NegotiationOutcome

协商继续请求的处理结果建议建模为统一结果对象。

建议支持三种状态：

- `completed`
  - 协商成功，当前请求恢复执行
- `needs_negotiation`
  - 仍需继续协商
- `rejected`
  - 协商失败，当前请求终止

## 8. 协商状态存储设计

### 8.1 存储接口

建议定义：

```python
NegotiationSessionStore
```

接口方法：

- `create(session: NegotiationSession) -> None`
- `get(session_id: str) -> NegotiationSession | None`
- `save(session: NegotiationSession) -> None`
- `delete(session_id: str) -> None`
- `cleanup_expired(now: datetime) -> None`

### 8.2 默认实现

第一版提供：

- `InMemoryNegotiationSessionStore`

特点：

- 进程内存态
- 实现简单
- 便于单测和本地调试

限制：

- 进程重启后会话丢失
- 不支持多进程/多实例共享

### 8.3 扩展点

设计上预留后续实现：

- `FileNegotiationSessionStore`
- `SqliteNegotiationSessionStore`
- 第三方 KV / Redis 风格实现

当前业务逻辑只能依赖 `NegotiationSessionStore` 接口，不能绑定具体存储实现。

## 9. 执行流程设计

### 9.1 首次请求流程

```text
客户端请求
-> PromptComplianceService.check()
-> 若通过：继续原请求
-> 若失败且属于可协商槽位问题：
   -> RequestNegotiationService.start()
   -> 创建 NegotiationSession
   -> 返回 NegotiationRequiredResponse
-> 若失败且不可协商：
   -> 直接返回错误
```

### 9.2 协商继续流程

```text
客户端提交 NegotiationContinueRequest
-> RequestNegotiationService.continue_()
-> 加载 NegotiationSession
-> 合并 base_slots + negotiated_slots + provided_slots
-> 基于 effective_slots 重新执行 slot 校验
-> 若通过：
   -> 调用 PromptRenderService.render()
   -> 生成完整 prompt
   -> 恢复原请求执行
   -> 返回原请求正常结果
-> 若仍缺少参数且可继续协商：
   -> 更新会话
   -> 返回新的 NegotiationRequiredResponse
-> 若超时/超次数/明确放弃：
   -> 标记 rejected 或 expired
   -> 返回拒绝结果
```

### 9.3 为什么恢复流程不重新跑完整 Prompt Compliance

协商恢复时不建议重新执行以下步骤：

- 安全护栏检查
- original prompt 回取
- LLM 槽位提取

原因：

1. 首次请求已经完成这些步骤
2. 客户端在协商阶段补的是结构化槽位值，而不是重新提交一份新的 processed prompt
3. 恢复时更合理的做法是：
   - 复用已有 `base_slots`
   - 合并 `provided_slots`
   - 重新执行 slot 校验
   - 校验通过后渲染完整 prompt

因此恢复阶段只需要重新执行：

- 槽位合并
- 槽位规则校验
- prompt 渲染
- 原请求恢复执行

## 10. 可协商与不可协商边界

### 10.1 可协商问题

第一版定义以下问题为可协商：

- 必填槽位缺失
- 依赖槽位缺失
- 槽位值不满足约束，但允许客户端重新提供

对应 `issue_type`：

- `missing_required`
- `dependency_missing`
- `value_invalid`

### 10.2 不可协商问题

以下问题直接终止当前请求，不进入协商：

- 安全护栏拒绝
- processed prompt 解析失败
- original prompt 无法回取
- LLM 槽位提取失败
- `slot.json` 文件不存在
- `slot.json` 文件格式损坏
- 协商会话不存在
- 协商会话已过期
- 协商次数超过限制
- 客户端明确拒绝继续协商

这些问题都属于系统级错误或流程级终止条件，不应被建模为“可继续补参”的业务问题。

## 11. 协商问题翻译设计

`translator.py` 负责将 `SlotIssue` 转换为：

- `NegotiationIssue`
- `NegotiationQuestion`

第一版采用**规则驱动模板生成**，不依赖 LLM。

示例：

- 必填缺失：
  - `请补充参数：location`
- 依赖缺失：
  - `当 operation 为 restart 时，需要同时提供 location`
- 枚举值非法：
  - `参数 operation 取值无效，请从 query、restart 中选择`

文案原则：

1. 可预测
2. 可测试
3. 不依赖额外外部服务
4. 后续可替换为配置化或 LLM 生成

## 12. 完整 Prompt 渲染预留

### 12.1 设计原则

协商成功后，服务端最终必须向下层 Agent 传递一份**完整 prompt**。

该完整 prompt 必须基于：

- original prompt
- effective_slots

重新生成。

### 12.2 当前范围

本轮只预留接口，不定义模板占位符语法，也不实现真实渲染逻辑。

建议新增接口：

```python
PromptRenderService
```

接口方法：

- `render(original_prompt: Prompt, effective_slots: dict[str, object]) -> str`

### 12.3 当前约束

后续真实渲染实现应直接复用另一项需求中“客户端根据原始 prompt 填充槽位”的逻辑成果。

因此本轮 negotiation 模块只能：

- 依赖渲染接口
- 不固化模板语法
- 不写死占位符格式

## 13. 配置设计

建议新增 negotiation 配置模型：

- `NegotiationConfig`

建议字段：

- `enabled: bool = True`
- `session_ttl_seconds: int = 300`
- `max_negotiation_attempts: int = 3`
- `store_backend: str = "memory"`

说明：

- 第一版只实现 `memory`
- 但配置字段仍应显式存在，便于后续扩展

建议最终由全局配置入口统一分发给 negotiation 模块。

## 14. 与服务端入口的集成

建议由服务端入口层统一编排 negotiation 流程。

若继续沿用当前 `PromptHandler` 风格，则建议职责为：

- 接收初始请求
- 调用 `PromptComplianceService`
- 判断：
  - 通过
  - 需协商
  - 直接拒绝
- 调用 `RequestNegotiationService.start()`
- 接收协商继续请求并调用 `RequestNegotiationService.continue_()`

`PromptHandler` 自身不应管理：

- 协商会话存储
- 问题翻译
- 完整 prompt 渲染

它只做流程编排与结果路由。

## 15. 错误模型设计

第一版 negotiation 域内建议仅保留少量异常：

- `NegotiationError`
- `NegotiationSessionNotFoundError`
- `NegotiationSessionExpiredError`
- `NegotiationSessionRejectedError`

说明：

- negotiation 模块应优先使用结果对象表达业务分支
- 只有确实属于异常流程的情况才使用异常
- 不应重复引入大量与结果模型重叠的 Error

## 16. 测试设计

### 16.1 单元测试

新增模块的单元测试至少覆盖：

- `translator`
  - `SlotIssue -> NegotiationIssue/Question` 转换
- `store`
  - create/get/save/delete/cleanup_expired
- `service.start`
  - 能创建会话并返回协商结果
- `service.continue_`
  - 补参成功后恢复执行
  - 补参不足时继续协商
  - 超时/超次数时拒绝

### 16.2 集成测试

至少覆盖：

1. 首次请求缺少必填槽位，返回 `NegotiationRequiredResponse`
2. 客户端补齐槽位后，当前请求继续执行成功
3. 依赖槽位缺失触发协商
4. 会话超时导致请求被拒绝
5. 超过最大协商次数导致请求被拒绝

### 16.3 渲染接口测试

本轮不测真实模板渲染，只测试：

- negotiation service 在协商成功后会调用 `PromptRenderService`
- 渲染结果会作为恢复执行输入传给下层执行器

## 17. 分阶段实施建议

建议实现顺序为：

1. 扩展 `prompt_compliance` 输出结构化 `SlotIssue`
2. 新增 negotiation 数据模型与 store 接口
3. 实现 translator
4. 实现 negotiation service 的 `start`
5. 实现 negotiation service 的 `continue_`
6. 预留 `PromptRenderService` 接口并接入恢复流程
7. 接入服务端入口层编排
8. 补齐单测与集成测试

## 18. 最终结论

本需求的正确落地方向不是“请求失败后让客户端重新发起一次全新请求”，而是：

- 在当前请求生命周期内识别缺失参数
- 创建协商会话
- 让客户端补充结构化槽位值
- 服务端基于 original prompt 和最终槽位恢复出完整 prompt
- 继续执行当前请求

最终推荐方案为：

- 复用 `prompt_compliance` 发现槽位问题
- 新增独立 `request_negotiation` 模块管理协商过程
- 引入可扩展的 `NegotiationSessionStore`
- 第一版默认内存实现
- 完整 prompt 渲染能力本轮只预留接口，不固化模板语法

