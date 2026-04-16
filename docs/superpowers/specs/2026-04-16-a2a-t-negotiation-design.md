# A2A-T 协商下发设计

## 1. 目标

本设计面向如下链路：

- 上层调用方把原始自然语言任务描述交给客户端 SDK
- 客户端 SDK 生成任务 Prompt，并通过 A2A 发送给服务端
- 服务端在调用下游 Agent 之前，对任务 Prompt 做校验
- 如果信息缺失或存在可修正的明显非法信息，服务端发起协商
- 客户端根据配置决定自动补参继续协商，或将协商结果返回给上层

第一版目标是打通以下主链路：

1. 客户端通过 A2A 发送任务 Prompt
2. 服务端通过 A2A 接收任务 Prompt 并校验
3. 服务端在需要补参时返回协商要求
4. 客户端在同一任务内继续协商
5. 服务端管理协商状态、轮次和终止条件

## 2. 非目标

第一版不负责：

- 在下游 Agent 执行阶段再次触发协商
- 定义独立的 negotiation HTTP API
- 定义 A2A-T 自定义 negotiation 事件体系
- 自动恢复客户端或服务端重启前未完成的协商
- 客户端智能补全的具体实现
- 以结构化增量字段替代完整 Prompt 续轮提交

## 3. 范围

第一版协商范围包括：

- 缺失信息
- 明显非法但可以由客户端补充或修正的信息

第一版协商只发生在：

- 客户端 Prompt 生成之后
- 服务端下游 Agent 执行之前

一旦服务端 Prompt 校验通过并进入下游 Agent 执行，第一版不再重新触发协商。

## 4. 总体方案

采用以下总体方案：

- 客户端闭环驱动，服务端状态主控

含义如下：

- 客户端负责驱动 Prompt 生成、A2A 发送、接收协商响应、补参、重生成 Prompt、继续发送
- 服务端负责 Prompt 校验、协商状态管理、轮次控制、终止判断、最终执行决策
- 协议层尽量复用 A2A 原生语义，通过同一个 `task_id/context_id` 和 `TASK_STATE_INPUT_REQUIRED + message` 表达协商

### 4.1 上下文视图

下面的上下文视图描述第一版协商下发方案在整体系统中的位置。

```plantuml
@startuml
skinparam componentStyle rectangle
skinparam shadowing false
skinparam packageStyle rectangle

actor "上层调用方" as Caller

rectangle "客户端进程" {
  [Task Submission Facade] as ClientFacade
  [Prompt Generation Module] as PromptGen
  [A2A Client] as A2AClient
  [Supplement Provider\n(optional)] as SupplementProvider
  [Progress Callback\n(optional)] as ProgressCallback
  collections "Client Negotiation Context\n(in-memory, persistence extension)" as ClientContext
}

rectangle "服务端进程" {
  [A2A Server Handler] as ServerHandler
  [Prompt Validator] as PromptValidator
  [Negotiation Manager] as NegotiationManager
  collections "Negotiation State Store\n(in-memory, persistence extension)" as NegotiationStore
  [Downstream Agent] as DownstreamAgent
}

Caller --> ClientFacade : submit(natural_language_description)
ClientFacade --> PromptGen : 生成完整 Prompt
ClientFacade --> A2AClient : 发送任务 / 续轮任务
ClientFacade --> ClientContext : 维护原始描述\n与补充历史
ClientFacade ..> SupplementProvider : 自动协商时获取补充描述
ClientFacade ..> ProgressCallback : 报告当前阶段

A2AClient --> ServerHandler : A2A task message
ServerHandler --> PromptValidator : 校验 Prompt
ServerHandler --> NegotiationManager : 管理协商状态/轮次/终止
NegotiationManager --> NegotiationStore
ServerHandler --> DownstreamAgent : 校验通过后下发执行

note bottom of A2AClient
协商续轮保持同一
task_id / context_id
end note

note bottom of ServerHandler
协商通过
TASK_STATE_INPUT_REQUIRED
+ message 表达
end note
@enduml
```

## 5. 角色职责

### 5.1 上层调用方

上层调用方负责：

- 发起一次任务下发请求
- 按需配置补参 provider
- 按需配置进度回调
- 在自动协商关闭或无法继续时，自行决定如何与最终用户交互

### 5.2 客户端 SDK

客户端 SDK 负责：

- 接收原始自然语言任务描述
- 维护客户端侧协商上下文
- 调用现有 Prompt 生成链路生成完整任务 Prompt
- 通过 A2A 向服务端发送 Prompt
- 接收服务端的 `INPUT_REQUIRED` 协商响应
- 在自动协商模式下，通过补参 provider 获取增量自然语言补充描述
- 基于原始描述和补充历史重生成新的完整 Prompt
- 在同一 `task_id/context_id` 上继续协商
- 向调用方返回最终结果或 `negotiation_required`

客户端不负责：

- 主控协商轮次和终止条件
- 在服务端完成校验后替代服务端做执行决策

### 5.3 补参 Provider

补参 provider 是客户端的一个可配置接口，只负责：

- 在服务端要求补充或修正信息时，向调用方获取增量自然语言补充描述

它不负责：

- A2A 通信
- 协商状态管理
- 直接生成任务 Prompt

### 5.4 进度回调

进度回调是客户端的一个可选接口，只负责：

- 向调用方报告当前执行阶段

它不参与协商控制逻辑。是否配置进度回调，不改变任务下发与协商的语义。

### 5.5 服务端 SDK

服务端负责：

- 接收客户端提交的任务 Prompt
- 对任务 Prompt 执行校验
- 判断是否进入协商
- 维护协商状态、轮次和当前任务最新输入
- 在需要补参时通过 A2A 返回 `TASK_STATE_INPUT_REQUIRED`
- 在校验通过后，把最后通过校验的 Prompt 传给下游 Agent
- 在达到终止条件时结束协商

## 6. 协议与任务语义

第一版协议层采用以下原则：

- 协商始终属于同一个任务
- 协商期间保持同一个 `task_id`
- 协商期间保持同一个 `context_id`
- 不新增 A2A-T 自定义 negotiation 事件

服务端在需要补参时：

- 将任务状态置为 `TASK_STATE_INPUT_REQUIRED`
- 在返回消息中说明当前缺失/错误信息和补充要求

客户端后续补参时：

- 继续使用原来的 `task_id/context_id`
- 再次发送新的完整任务 Prompt

## 7. 协商状态模型

服务端第一版最小协商状态集为：

- `negotiating`
- `satisfied`
- `rejected`

含义如下：

- `negotiating`：仍在协商中，等待客户端提交新的完整 Prompt
- `satisfied`：最新 Prompt 已通过校验，可以进入下游 Agent 执行
- `rejected`：协商终止，不再继续

第一版不扩展更多中间状态。

## 8. 客户端协商上下文

客户端需要维护的协商上下文至少包括：

- 原始自然语言任务描述
- 每轮补充得到的自然语言描述历史
- 当前关联的 `task_id/context_id`

客户端每轮重生成 Prompt 时，只使用：

- 原始自然语言任务描述
- 每轮补充描述

服务端追问文本本身不纳入下一轮 Prompt 生成输入。

## 9. 服务端协商状态

服务端需要持有的协商状态至少包括：

- 当前协商状态
- 当前轮次
- 当前任务最新输入 Prompt
- 最后通过校验的 Prompt

服务端每轮收到新的完整 Prompt 后：

- 将其视为当前任务最新输入
- 对其重新执行完整校验
- 如通过，则将最后通过校验的 Prompt 交给下游 Agent

## 10. 自动协商策略

客户端协商行为需要做成可配置策略。第一版至少支持两种策略：

### 10.1 `manual`

- 客户端不自动调用补参 provider
- 服务端一旦返回协商要求，客户端直接返回 `negotiation_required`

### 10.2 `auto`

- 客户端通过补参 provider 自动获取补充描述并继续协商
- 如果未配置补参 provider，则自动退化为 `manual`

## 11. 返回结果

第一版顶层返回结果需要覆盖三类结果：

- `success`
- `failure`
- `negotiation_required`

其中：

- `success`：任务已通过校验并成功下发给服务端下游执行链路
- `failure`：任务无法继续，且不属于“等待上层继续补参”的中间状态
- `negotiation_required`：当前需要补参，但客户端未继续自动协商

`negotiation_required` 必须是结构化结果，而不是纯文本。它至少包含：

- 结构化缺失/错误信息
- 服务端追问文本
- 当前任务关联标识
- 当前协商轮次和上限信息

服务端追问文本作为结构化结果中的一个字段存在。

## 12. 公开接口策略

长期上客户端会支持三种调用方式：

- 同步调用
- 流式调用
- 异步调用

第一版公开接口策略为：

- 三种接口都可以对外暴露
- 第一版只实现同步调用
- 流式调用和异步调用显式抛出 `NotImplementedError`

## 13. 第一版同步调用语义

第一版同步调用仍然是：

- 一次调用跑到成功、失败或 `negotiation_required`

同步调用支持可选的进度回调：

- 如果提供进度回调，客户端在关键阶段通知调用方
- 如果未提供进度回调，客户端静默执行，只返回最终结果

同步调用不会因为缺少进度回调而改变控制语义。

## 14. 端到端流程

### 14.1 流程时序图

下面的时序图描述第一版同步调用下的主流程，以及进入协商后的手动/自动分支。

```plantuml
@startuml
skinparam shadowing false
autonumber

actor "上层调用方" as Caller
participant "客户端 SDK" as ClientSDK
participant "Prompt Generation Module" as PromptGen
participant "Supplement Provider" as SupplementProvider
participant "A2A Client" as A2AClient
participant "服务端 SDK" as ServerSDK
participant "Prompt Validator" as Validator
participant "Negotiation Manager" as NegotiationManager
participant "Downstream Agent" as DownstreamAgent

Caller -> ClientSDK : submit(natural_language_description)
ClientSDK -> ClientSDK : 创建协商上下文\n记录原始描述
ClientSDK -> PromptGen : generate(原始描述 + 补充历史=[])
PromptGen --> ClientSDK : 完整 Prompt
ClientSDK -> A2AClient : send(prompt)
A2AClient -> ServerSDK : sendTask(prompt, new task_id/context_id)
ServerSDK -> NegotiationManager : 创建/加载协商状态
ServerSDK -> Validator : validate(prompt)

alt 校验通过
  Validator --> ServerSDK : valid
  ServerSDK -> NegotiationManager : 标记 satisfied\n记录 validated prompt
  ServerSDK -> DownstreamAgent : execute(validated prompt)
  ServerSDK --> A2AClient : success
  A2AClient --> ClientSDK : success
  ClientSDK --> Caller : success
else 需要补参或修正
  Validator --> ServerSDK : issues + follow_up_message
  ServerSDK -> NegotiationManager : 记录 latest prompt / issues\n检查轮次上限
  alt 已达上限或不可继续
    NegotiationManager --> ServerSDK : rejected
    ServerSDK --> A2AClient : failure(rejected)
    A2AClient --> ClientSDK : failure
    ClientSDK --> Caller : failure
  else 允许继续协商
    NegotiationManager --> ServerSDK : negotiating
    ServerSDK --> A2AClient : TASK_STATE_INPUT_REQUIRED\n+ issues + follow_up_message
    A2AClient --> ClientSDK : negotiation request
    alt `manual` 或未配置 provider
      ClientSDK --> Caller : negotiation_required
    else `auto` 且已配置 provider
      loop 直到 satisfied 或 rejected
        ClientSDK -> SupplementProvider : provide(issues, follow_up_message)
        SupplementProvider --> ClientSDK : 增量补充描述
        ClientSDK -> ClientSDK : 追加到补充历史
        ClientSDK -> PromptGen : regenerate(原始描述 + 补充历史)
        PromptGen --> ClientSDK : 新完整 Prompt
        ClientSDK -> A2AClient : resend(同一 task_id/context_id)
        A2AClient -> ServerSDK : sendTask(new prompt)
        ServerSDK -> NegotiationManager : 加载当前协商状态
        ServerSDK -> Validator : validate(new prompt)
        alt 校验通过
          Validator --> ServerSDK : valid
          ServerSDK -> NegotiationManager : 标记 satisfied\n记录 validated prompt
          ServerSDK -> DownstreamAgent : execute(validated prompt)
          ServerSDK --> A2AClient : success
          A2AClient --> ClientSDK : success
          ClientSDK --> Caller : success
        else 继续要求补参
          Validator --> ServerSDK : issues + follow_up_message
          ServerSDK -> NegotiationManager : 更新状态并检查上限
          alt 达到上限或不可继续
            NegotiationManager --> ServerSDK : rejected
            ServerSDK --> A2AClient : failure(rejected)
            A2AClient --> ClientSDK : failure
            ClientSDK --> Caller : failure
          else 返回下一轮协商
            NegotiationManager --> ServerSDK : negotiating
            ServerSDK --> A2AClient : TASK_STATE_INPUT_REQUIRED\n+ issues + follow_up_message
            A2AClient --> ClientSDK : negotiation request
          end
        end
      end
    end
  end
end
@enduml
```

### 14.2 首次下发

1. 调用方调用客户端封装对象，输入原始自然语言任务描述
2. 客户端创建协商上下文
3. 客户端生成第一版完整任务 Prompt
4. 客户端通过 A2A 发送给服务端
5. 服务端接收后执行 Prompt 校验

### 14.3 校验通过

如果服务端校验通过：

1. 服务端将协商状态置为 `satisfied`
2. 服务端记录最后通过校验的 Prompt
3. 服务端把该 Prompt 传给下游 Agent 执行
4. 客户端返回 `success`

### 14.4 进入协商

如果服务端发现缺失信息或可修正的明显非法信息：

1. 服务端将协商状态置为 `negotiating`
2. 服务端一次性返回当前已知的全部缺失/错误信息
3. 服务端通过 `TASK_STATE_INPUT_REQUIRED + message` 返回协商要求

### 14.5 客户端处理协商要求

客户端收到协商要求后，按策略处理：

- `manual`
  - 客户端直接返回 `negotiation_required`
- `auto`
  - 如果配置了补参 provider，则向其获取增量自然语言补充描述
  - 如果未配置补参 provider，则退化为 `manual`

### 14.6 自动协商续轮

在 `auto` 且成功获取补充描述的情况下：

1. 客户端把本轮补充描述追加到协商上下文
2. 客户端基于“原始描述 + 补充历史”重新生成新的完整 Prompt
3. 客户端继续使用原来的 `task_id/context_id` 发送新 Prompt
4. 服务端把该 Prompt 视为当前任务最新输入，并重新完整校验

### 14.7 协商终止

协商在以下情况下结束：

- 校验通过，进入执行
- 达到协商轮次上限
- 客户端明确放弃
- 其他无法继续协商的情况

除“校验通过，进入执行”外，其余情况统一落到 `rejected`。

## 15. 持久化与恢复扩展点

### 15.1 客户端

第一版客户端：

- 默认只在内存中保存协商上下文
- 不支持进程重启后的自动恢复

但必须预留接口，支持未来：

- 持久化协商上下文
- 恢复未完成协商并继续任务

### 15.2 服务端

第一版服务端：

- 默认只在内存中保存协商状态和当前有效 Prompt
- 不支持重启后的自动恢复

但必须预留接口，支持未来：

- 持久化协商状态
- 恢复未完成协商
- 恢复最后通过校验的 Prompt

## 16. 第一版设计原则

第一版坚持以下原则：

- 优先复用现有 Prompt 生成和 Prompt 校验能力
- 优先复用 A2A 原生状态语义
- 同一个任务内完成协商，不把续轮伪装成新任务
- 自动协商与状态感知解耦
- 自动协商与手动上层承接兼容
- 默认只做内存态，但不堵死未来恢复能力
