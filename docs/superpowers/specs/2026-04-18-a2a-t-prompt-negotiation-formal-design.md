# A2A-T Prompt / Validation / Negotiation 正式设计

## 1. 文档定位

本文是当前 A2A-T 第一版正式设计基线，覆盖三块能力：

- task prompt 生成
- task prompt 校验
- negotiation

本文以当前讨论结论为唯一基线，并结合现有代码给出可落地的重构方向。除明确标记为“重构项”的内容外，本文应与当前代码保持一致。

本文替代旧的 `2026-04-17-a2a-t-negotiation-code-design.md`，后续开发应优先以本文为准。

## 2. 固定约束

### 2.1 顶层交互边界

- A：客户端 Agent
- B：Client SDK
- C：Server SDK
- D：服务端 Agent
- A 和 D 直接通过 A2A 原生 SDK 交互
- B 和 C 不直接通信
- A 收到/准备发送 A2A 消息时调用 B
- D 收到/准备发送 A2A 消息时调用 C

### 2.2 SDK 边界

- SDK 不感知 A2A 对象模型
- SDK 对外输入只使用普通数据：
  - `message: str`
  - `context_json: dict[str, object] | None`
- A/D 负责在 A2A message/task metadata 与 SDK `context_json` 之间做适配
- 非 negotiation 场景下，`context_json` 可以为 `None`
- negotiation 之外的运行时补充参数，放在各自方法的独立输入对象中，不并入 `context_json`
- 第一版不新增统一的 `A2ATClient` / `A2ATServer` facade

### 2.3 第一版范围

- 只实现 `sendMessage`
- 预留 `sendMessageStream`
- 只做同步设计
- store 只实现 `InMemory`
- 预留文件/数据库等 store 扩展点，但当前不实现

### 2.4 negotiation 类型

- `information`
- `clarification`
- `feasibility`
- `fulfillment`

其中：

- `information` 是 type 1 task prompt 下发失败后的内部补参协商
- `clarification` / `feasibility` / `fulfillment` 不进入 type 1 的 prompt 校验主链路
- 四类 negotiation 共享统一主流程，但各自 facts、prompt 资源、处理规则必须隔离

## 3. 能力边界

### 3.1 对外公开能力

第一版公开能力固定为：

```python
generate(input) -> PromptGenerationResult
check_task_prompt(task_id, processed_prompt_text) -> dict[str, object]
start_negotiation(input) -> dict[str, object]
receive_negotiation(message, context_json) -> dict[str, object]
continue_negotiation(input) -> dict[str, object]
```

### 3.2 返回契约

- `generate(input)`
  - 返回 `PromptGenerationResult`
  - 成功时由调用方读取 `prompt_text`
  - 失败时由调用方读取 `failure`

- `check_task_prompt(task_id, processed_prompt_text)`
  - 返回：

```json
{
  "passed": true,
  "need_negotiation": false,
  "negotiation_input": null,
  "stage": "passed",
  "extracted_slots": null,
  "error_code": null,
  "error_message": null
}
```

- `start_negotiation(input)`
  - 返回 fixed-key map

- `receive_negotiation(message, context_json)`
  - 返回本地结构化结果：

```json
{
  "context": {},
  "needResponse": true,
  "facts": {}
}
```

- `continue_negotiation(input)`
  - 返回 fixed-key map
  - 唯一允许的额外 key：
    - C 侧 `information + status=agreed`
    - `https://github.com/a2aproject/telecommunication/extensions/Task-T/v1`

### 3.3 fixed-key map

协商报文统一返回以下 key：

- `https://github.com/a2aproject/telecommunication/extensions/NEGOTIATION-T`
  - 值为 negotiation prompt 字符串
- `https://github.com/a2aproject/telecommunication/extensions/DATA-NEGOTIATION-T/v1`
  - 值为固定 negotiation context json

`information + agreed` 的 server 响应允许额外带：

- `https://github.com/a2aproject/telecommunication/extensions/Task-T/v1`
  - 值为最终 task prompt
  - 只供 D 本地执行，不跨端透传给 A/B

## 4. 协议与数据契约

### 4.1 固定 negotiation context

A/D 在 A2A metadata 中传递的数据结构固定为：

```json
{
  "negotiationType": "information",
  "negotiationId": "xxxxx-xxxx-xxxx",
  "role": "client",
  "round": 3,
  "status": "in-progress",
  "extra": {}
}
```

固定约束：

- `negotiationType` 只能是：
  - `information`
  - `clarification`
  - `feasibility`
  - `fulfillment`
- `role` 只能是：
  - `client`
  - `server`
- `status` 只能是：
  - `in-progress`
  - `agreed`
  - `rejected`
- `role` 表示本次协商的发起方角色，不随轮次变化
- B 发起首轮协商时，`role=client`
- C 发起首轮协商时，`role=server`
- `round` 从 `1` 开始递增
- `extra` 必须是 object，但第一版不解释其内部语义
- `round` 和 `status` 由 B/C 维护，A/D 不负责推进
- `extra` 中即使出现与核心字段同名的 key，也一律忽略，不参与协议语义

### 4.2 最小格式校验

negotiation 报文的最小格式校验只做两层：

- `message` 必须是非空字符串
- `context_json` 必须包含固定根字段，且枚举值/类型合法

第一版不做：

- `extra` 内部字段校验
- facts 核心字段强校验
- 跨 type 的通用 slot 抽取
- negotiation message 的理解不走 LLM；`receive_negotiation(...)` 依赖固定模板格式做确定性解析
- task prompt 之外的 front matter 渲染或校验；front matter 只作用于 task prompt

### 4.3 非法输入处理

- 报文结构非法：抛异常
- 报文结构合法，但本轮协商内容不接受：
  - `receive_negotiation(...)` 返回本地处理结果
  - 上层再调用 `continue_negotiation(status="rejected")`
- 终态 negotiation 再续轮：抛异常

## 5. 业务流程

### 5.1 task prompt 生成

1. A 调用 B 的 `generate(input)`
2. B 基于现有 prompt generation 链路生成 `PromptGenerationResult`
3. A 从 `result.prompt_text` 取出完整 task prompt
4. A 通过 A2A 将该 prompt 发给 D

说明：

- 初始 task prompt 下发不是 negotiation 报文，不携带 negotiation `context_json`

### 5.2 task prompt 校验通过

1. D 收到 task prompt 后调用 C 的 `check_task_prompt(task_id, processed_prompt_text)`
2. C 复用现有 prompt compliance 链路校验
3. 若 `passed=true`：
   - `need_negotiation=false`
   - D 直接执行任务

### 5.3 `information` 协商闭环

1. D 调用 `check_task_prompt(task_id, processed_prompt_text)`
2. 若 `passed=false` 且 `need_negotiation=true`：
   - C 返回：

```json
{
  "passed": false,
  "need_negotiation": true,
  "negotiation_input": {
    "type": "information",
    "contentText": "...",
    "facts": {}
  },
  "stage": "slot_validation",
  "extracted_slots": {},
  "error_code": "slot_validation_error",
  "error_message": "..."
}
```

3. D 调用 `start_negotiation(negotiation_input)` 生成首轮协商报文
4. D 通过 A2A 发给 A
5. A 调用 `receive_negotiation(message, context_json)` 获取：
   - `context`
   - `needResponse`
   - `facts`
6. A 基于 `facts` 与本地 Agent 交互，补充缺失/修正信息
7. A 重新调用 `generate(input_with_new_info)` 生成最新完整 task prompt，并从 `result.prompt_text` 取值
8. A 调用：

```json
{
  "context": {},
  "status": "in-progress",
  "contentText": "latest task prompt"
}
```

9. B 通过 `continue_negotiation(...)` 生成下一轮协商报文
10. D 收到后，C 再次 `receive_negotiation(...)`
11. C 在 `information` 处理分支内部再次校验最新 task prompt
12. 若仍不满足：
    - 返回新的 `facts`
    - D 再次发起下一轮 `in-progress`
13. 若已满足：
    - D 调用 `continue_negotiation(status="agreed", contentText=final_task_prompt)`
    - C 返回：
      - negotiation fixed-key map
      - `Task-T/v1`
    - D 先将 `agreed` 报文发给 A
    - D 再执行 `Task-T/v1`

关键约束：

- `information` 不新开 negotiationId
- B 每轮都发送“最新完整 task prompt”，不是补字段 delta
- C 给出 `agreed` 时，已经持有最终可执行 task prompt
- A/B 通过收到 `status=agreed` 得知协商已结束

### 5.4 `clarification` / `feasibility` / `fulfillment`

这三类统一采用三段式：

1. 接收方调用 `receive_negotiation(message, context_json)`
2. Agent 本地补充信息或做业务判断
3. 调用 `continue_negotiation({context, status, contentText})`

它们与 `information` 的差异仅在于：

- facts 结构不同
- prompt 资源不同
- type-specific 处理规则不同

它们不额外进入 task prompt 校验链路。

## 6. 包设计

### 6.1 保留的现有包

以下包继续保留，作为当前可复用的底层能力：

```text
src/a2a_t/prompt/common
src/a2a_t/prompt/resources
src/a2a_t/prompt/analysis
src/a2a_t/prompt/validation
src/a2a_t/client/prompt_generation
src/a2a_t/server/prompt_compliance
```

职责不变：

- prompt 元数据解析与渲染
- prompt 资源加载
- scenario recognition / slot extraction
- slot validation / guardrail
- client 端 prompt 生成编排
- server 端 prompt 校验编排

### 6.2 目标包结构

第一版目标结构为：

```text
src/a2a_t/
├─ prompt/
│  ├─ builders/
│  ├─ common/
│  ├─ resources/
│  ├─ analysis/
│  ├─ validation/
│  └─ task_rendering/
├─ negotiation/
│  ├─ common/
│  ├─ rendering/
│  ├─ handling/
│  ├─ store/
│  ├─ types/
│  └─ runtime/
├─ client/
│  ├─ prompt_generation/
│  └─ negotiation/
└─ server/
   ├─ prompt_compliance/
   └─ negotiation/
```

说明：

- `prompt/builders` 继续保留，负责复用当前 runtime components 装配
- `prompt/analysis`、`prompt/validation` 继续保留为底层 runtime
- `prompt/common/task_prompt_format.py` 负责 task prompt front matter 的格式化与解析
- `prompt/task_rendering` 是 task prompt 渲染收口层
- `common/prompt_resources`、`common/prompt_runtime` 承接 prompt 与 negotiation 共用的资源加载与 runtime 装配
- `negotiation/` 是新的共享核心层
- `client/*`、`server/*` 只做端侧公开入口与依赖装配
- `negotiation/handling` 是内部流程层，不作为 SDK 对外入口
- 不单独新增 `negotiation/parsing` 一级包；解析能力下沉为 `handling` 内部组件

### 6.3 `negotiation/types`

第一版不把每个 type 继续拆成多个文件，结构固定为：

```text
src/a2a_t/negotiation/types/
├─ base.py
├─ information.py
├─ clarification.py
├─ feasibility.py
└─ fulfillment.py
```

## 7. 类设计

### 7.1 task prompt 相关

#### `client.prompt_generation.PromptGenerationOrchestrator`

角色：

- B 侧 task prompt 生成入口
- 对外稳定方法为 `generate(input)`
- 内部复用现有 `generate(...)` 链路

处理范围：

- 输入归一化
- scenario recognition
- slot extraction
- slot validation
- task prompt 渲染

兼容策略：

- 当前代码中的 `generate(...)` 直接作为公开生成入口
- 不再额外保留 `render_task_prompt(...)` 薄封装
- `PromptClient` 继续作为 compatibility shim，不承载 negotiation

#### `prompt.task_rendering.TaskPromptRenderer`

角色：

- 仅负责 task prompt 文本生成
- 内部通过 `task_prompt_format` 写入 front matter
- 不负责 negotiation context 生成
- 不负责 fixed-key map 组装

#### `server.prompt_compliance.PromptComplianceOrchestrator`

角色：

- C 侧 task prompt 校验核心执行器
- 核心方法为 `check(processed_prompt_text, request_metadata)`
- 直接输出包含 `need_negotiation` / `negotiation_input` 的语义结果

兼容策略：

- 当前 `check(processed_prompt_text=...)` 保留并直接承担语义结果输出
- `PromptHandler` 收敛为单一 `check_task_prompt(...)` 薄 facade

#### `server.prompt_handler.PromptHandler`

角色：

- C 侧 task prompt 校验兼容 facade
- 对外公开 `check_task_prompt(task_id, processed_prompt_text)`
- 内部只做 `PromptComplianceOrchestrator.check(...)` 的薄代理

### 7.2 negotiation 公开入口

#### `client.negotiation.NegotiationOrchestrator`

角色：

- B 侧协商公开入口
- 暴露：
  - `start_negotiation(...)`
  - `receive_negotiation(...)`
  - `continue_negotiation(...)`

#### `server.negotiation.NegotiationOrchestrator`

角色：

- C 侧协商公开入口
- 暴露同样三种能力

#### `BaseNegotiationOrchestrator`

角色：

- 提供三种公开能力的公共流程模板
- 只做参数校验、流程编排与依赖调用
- 不承载 type-specific 业务规则
- 不直接操作 store
- 直接依赖收敛为 `NegotiationHandler` 和 `NegotiationParser`

### 7.3 negotiation 核心组件

#### `NegotiationHandler`

角色：

- 负责：
  - `start(...)`
  - `receive(...)`
  - `continue_(...)`
  - 首轮 `NegotiationContext` 生成
  - 首轮协商 prompt 文本生成
  - 首轮 fixed-key map 组装
  - 首轮 `NegotiationRecord` 建档
  - 续轮合法性校验、状态推进、type 路由和 store 更新
- 是内部用例执行器，不直接对外暴露

#### `NegotiationParser`

角色：

- 作为公开入口和 `NegotiationHandler` 共享的解析组件
- 统一解析 negotiation context 和固定 `negotiation-json` 报文
- 不作为独立公开入口

### 7.4 negotiation type

#### `BaseNegotiationType`

固定三个钩子：

- `render_start_prompt(...)`
- `process_received_message(...)`
- `render_continue_prompt(...)`

不负责：

- `negotiationId` 分配
- `round` 递增
- 统一 store 读写
- fixed-key map 组装

#### 四个子类

- `InformationNegotiationType`
- `ClarificationNegotiationType`
- `FeasibilityNegotiationType`
- `FulfillmentNegotiationType`

职责：

- facts 解释
- type prompt 生成差异
- 接收结果构造差异
- continue 规则差异

其中 `InformationNegotiationType` 额外负责：

- `missingFields` / `invalidFields` 的解释
- C 侧 `information + agreed` 的 `final_task_prompt` 产出

## 8. 数据模型

### 8.1 公开输入输出

#### `check_task_prompt(task_id, processed_prompt_text)` 结果

```json
{
  "passed": false,
  "need_negotiation": true,
  "negotiation_input": {
    "type": "information",
    "contentText": "...",
    "facts": {}
  },
  "stage": "slot_validation",
  "extracted_slots": {},
  "error_code": "slot_validation_error",
  "error_message": "Slot validation failed."
}
```

约束：

- `passed=true` -> `need_negotiation=false` 且 `negotiation_input=null`
- `need_negotiation=true` -> `negotiation_input` 必须非空
- `error_message` 只保留字符串

#### `start_negotiation(input)` 输入

```json
{
  "type": "clarification",
  "contentText": "...",
  "facts": {}
}
```

#### `continue_negotiation(input)` 输入

```json
{
  "context": {},
  "status": "in-progress",
  "contentText": "..."
}
```

### 8.2 negotiation 核心模型

#### `NegotiationContext`

字段：

- `negotiation_type`
- `negotiation_id`
- `role`
- `round`
- `status`
- `extra`

#### `NegotiationRecord`

字段：

- `context`
- `last_message`
- `last_receive_result`
- `last_continue_result`
- `last_task_prompt`
- `created_at`
- `updated_at`

#### type 内部结果

- `ReceiveResult`
  - `need_response`
  - `facts`
- `ContinueResult`
  - `prompt_text`
  - `final_task_prompt`

约束：

- `final_task_prompt` 仅允许在 C 侧 `information + agreed` 非空

### 8.3 facts 最小结构

`receive_negotiation(...).facts` 第一版按 type 隔离：

#### `information`

```json
{
  "missingFields": ["startTime", "location"],
  "invalidFields": [
    {
      "name": "deviceType",
      "reason": "unsupported value"
    }
  ]
}
```

#### `clarification`

```json
{
  "clarificationItems": [
    {
      "name": "intent",
      "reason": "target is ambiguous",
      "question": "Do you want to reduce energy cost or improve occupant comfort?"
    }
  ]
}
```

#### `feasibility`

```json
{
  "feasibilityItems": [
    {
      "name": "task",
      "reason": "execution capability is uncertain",
      "question": "Can you complete this task under the current constraints?"
    }
  ]
}
```

#### `fulfillment`

```json
{
  "fulfillmentItems": [
    {
      "name": "result",
      "reason": "result acceptance is uncertain",
      "question": "Does this result satisfy your expected outcome?"
    }
  ]
}
```

说明：

- `facts` 必须存在，但允许为空对象
- 泛型层不校验 `facts` 内部业务字段
- 各 type 可独立演进，互不影响

## 9. Store 设计

### 9.1 抽象接口

第一版只保留接口：

```python
get(negotiation_id)
save(record)
delete(negotiation_id)
cleanup_expired()
```

当前约束：

- `cleanup_expired()` 第一版只需保留接口
- 默认实现可以直接返回 `True`

### 9.2 默认实现

- 默认实现：`InMemoryNegotiationStateStore`
- 当前不实现文件/数据库 store
- 后续通过 `.env` 配置切换 store 类型
- 如果配置的 store 不可用：
  - 打日志
  - 回退到 `InMemory`

### 9.3 当前存储范围

store 至少保存：

- negotiation 基本状态
- 最近一次接收结果
- 最近一次续轮结果
- 最近一次 task prompt
- 创建/更新时间

终态记录：

- 不立即删除
- 短期保留
- 通过 `cleanup_expired()` 老化清理

## 10. 边界与异常处理

### 10.1 task prompt 侧

- `generate(...)`
  - 对外契约统一为抛异常
  - 若底层仍复用当前 `generate(...)` 失败结果对象，由公开入口负责转换

- `check_task_prompt(...)`
  - 请求本身无法处理：抛异常
  - 业务校验失败：正常返回 `passed/need_negotiation/negotiation_input/error_message`
  - 若底层仍复用当前 `check(...)` 返回的 `PromptComplianceResult`，由公开入口负责翻译

### 10.2 negotiation 侧

- `start_negotiation(...)`
  - 输入不合法、无法生成首轮协商：抛异常

- `receive_negotiation(...)`
  - `message` 为空或 `context_json` 缺根字段：抛异常
  - `context_json` 合法但本轮协商被业务拒绝：
    - 返回本地处理结果
    - 由上层决定是否调用 `continue_negotiation(status="rejected")`

- `continue_negotiation(...)`
  - `context` 与 store 不一致、终态后续轮、无法恢复状态：抛异常
  - 合法拒绝：返回 `status=rejected` 的 fixed-key map

### 10.3 `information` 特殊边界

- B 不直接发送补字段答案，而是每轮重新生成完整 task prompt
- C 在 `receive_negotiation(...)` 内部不直接回包
- C 只有在重新校验通过后，才允许生成 `status=agreed`
- `status=agreed` 说明：
  - 协商结束
  - prompt 已确定

## 11. Prompt 资源设计

目标目录：

```text
package_data/prompt_resources/
├─ prompts/
│  ├─ scenario_recognition/
│  ├─ slot_extraction/
│  ├─ task_submission/
│  ├─ information_negotiation/
│  ├─ clarification_negotiation/
│  ├─ feasibility_negotiation/
│  └─ fulfillment_negotiation/
├─ scenarios/
├─ templates/
└─ slots/
```

统一规则：

- 延续现有 `version/language/system.md/user.md`
- 四类 negotiation 使用独立 prompt 目录
- task prompt 与 negotiation prompt 共用同一资源体系，但不共用同一资源分类

## 12. 与当前代码的映射

### 12.1 直接复用

- `src/a2a_t/client/prompt_generation/prompt_generation_orchestrator.py`
- `src/a2a_t/client/prompt_generation/prompt_generation_orchestrator_builder.py`
- `src/a2a_t/server/prompt_compliance/prompt_compliance_orchestrator.py`
- `src/a2a_t/server/prompt_compliance/prompt_compliance_orchestrator_builder.py`
- `src/a2a_t/prompt/builders/prompt_runtime_components_builder.py`
- `src/a2a_t/prompt/common/*`
- `src/a2a_t/prompt/resources/*`
- `src/a2a_t/prompt/analysis/*`
- `src/a2a_t/prompt/validation/*`

### 12.2 保持兼容，不作为新核心入口

- `PromptClient`
- `PromptHandler`
- `ExtendedClient`
- `ExtendedServer`

其中：

- `PromptClient` / `PromptHandler` 保留为兼容壳
- `ExtendedClient` / `ExtendedServer` 不承载本轮语义层设计

### 12.3 需要新增或重构

- 新增共享 `negotiation/` 包
- 新增 `client/negotiation/`
- 新增 `server/negotiation/`
- 删除 `PromptGenerationOrchestrator.render_task_prompt(...)` 薄封装，统一保留 `generate(...)`
- 由 `PromptComplianceOrchestrator.check(...)` 直接输出语义化校验结果
- `PromptHandler` 收敛为单一 `check_task_prompt(...)` 薄 facade

### 12.4 当前代码的明确差异

当前代码中：

- `PromptGenerationOrchestrator` 公开方法保留为 `generate(...)`
- `PromptComplianceOrchestrator` 公开方法保留为 `check(...)`
- `PromptComplianceResult` 已直接包含：
  - `passed`
  - `stage`
  - `extracted_slots`
  - `error_code`
  - `error_message`
  - `need_negotiation`
  - `negotiation_input`

因此当前代码的关键收敛点是：

- 统一让 B 侧调用方使用 `generate(...)` 返回的 `PromptGenerationResult`
- 统一让 C 侧 facade 使用 `PromptHandler.check_task_prompt(...)`
- 让 `check(...)` 的结果直接服务 `information` 场景的协商发起
- 在共享层维护 negotiation 状态机、type 路由和 store

## 13. 结论

第一版最稳妥的落地方式是：

- 保留现有 prompt generation / prompt compliance 作为底层能力层
- 在其上新增 task prompt 领域入口与 negotiation 共享核心层
- 由 A/D 负责 A2A 适配，B/C 只处理普通数据对象
- 用同一套主流程承接四类 negotiation，用 type 子类隔离差异

这套设计的直接收益是：

- 与当前代码兼容面最大
- 与已确认讨论结论一致
- `information` 闭环可以直接指导后续开发
- 其他三类 negotiation 可在相同主流程上平滑扩展
