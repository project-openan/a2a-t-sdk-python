# A2A 协议精简知识

> 基于 A2A Protocol Specification v1.0 精简摘要

---

## 1. 概述

A2A（Agent2Agent）协议是一个开放标准，旨在实现独立 AI Agent 系统之间的通信和互操作。

**核心目标：**
- **互操作性**：桥接不同 Agent 系统之间的通信
- **协作**：支持 Agent 之间委托任务、交换上下文
- **发现**：动态发现和理解其他 Agent 的能力
- **灵活性**：支持同步请求/响应、流式推送、异步推送通知
- **安全**：基于企业级安全实践
- **异步优先**：原生支持长时运行任务和人机交互

---

## 2. 核心概念

| 概念 | 说明 |
|------|------|
| **A2A Client** | 代表用户或系统向 A2A Server 发起请求的应用或 Agent |
| **A2A Server** | 暴露 A2A 兼容端点，处理任务并提供响应的 Agent 或 Agent 系统 |
| **Agent Card** | Server 发布的 JSON 元数据文档，描述其身份、能力、Skills、端点和认证要求 |
| **Message** | Client 与 Agent 之间的一次通信，有 `role`（user/agent）和多个 `Parts` |
| **Task** | A2A 管理的基本工作单元，有唯一 ID，有状态生命周期 |
| **Part** | Message 或 Artifact 中的最小内容单元，可以是 text、file、data |
| **Artifact** | Agent 任务产生的输出（文档、图片、结构化数据），由多个 `Parts` 组成 |
| **Context** | 可选标识符，用于逻辑上分组相关的 Tasks 和 Messages |
| **Extension** | Agent 提供核心功能之外附加功能或数据的机制 |

---

## 3. 三层架构

```
┌─────────────────────────────────────────────────────┐
│ Layer 1: 数据模型层 (Canonical Data Model)           │
│ Task | Message | Part | Artifact | AgentCard        │
├─────────────────────────────────────────────────────┤
│ Layer 2: 操作层 (Abstract Operations)              │
│ SendMessage | GetTask | ListTasks | Streaming...    │
├─────────────────────────────────────────────────────┤
│ Layer 3: 协议绑定层 (Protocol Bindings)             │
│ JSON-RPC 2.0 | HTTP+REST | gRPC                    │
└─────────────────────────────────────────────────────┘
```

---

## 4. 核心操作（11 个）

| 操作 | 说明 |
|------|------|
| **SendMessage** | 发送消息，启动或继续任务 |
| **SendStreamingMessage** | 发送消息并实时流式推送更新 |
| **GetTask** | 获取任务当前状态（含历史） |
| **ListTasks** | 列出任务（支持过滤和分页） |
| **CancelTask** | 取消运行中的任务 |
| **SubscribeToTask** | 订阅任务事件流 |
| **CreatePushNotificationConfig** | 创建推送通知配置 |
| **GetPushNotificationConfig** | 获取推送通知配置 |
| **ListPushNotificationConfigs** | 列出推送通知配置 |
| **DeletePushNotificationConfig** | 删除推送通知配置 |
| **GetExtendedAgentCard** | 获取认证后的扩展 AgentCard |

---

## 5. Task 状态机

```
         ┌─────────────┐
         │  SUBMITTED  │
         └──────┬──────┘
                │
         ┌──────▼──────┐
         │   WORKING   │
         └──────┬──────┘
                │
    ┌───────────┼───────────┐
    │           │           │
    ▼           ▼           ▼
COMPLETED    FAILED      CANCELED
                            │
                      REJECTED
                            │
                 ┌──────────┴──────────┐
                 │                     │
            INPUT_REQUIRED        AUTH_REQUIRED
                 │ (用户输入后继续)      │ (授权后继续)
                 └──────────┬──────────┘
                            │
                     回到 WORKING
```

---

## 6. 数据模型概览

### Task
- `id`：任务唯一标识（Server 生成）
- `contextId`：上下文标识（可选）
- `status`：TaskStatus（state + timestamp + message）
- `artifacts`：产出物列表
- `history`：消息历史

### Message
- `messageId`：消息唯一标识
- `role`：角色（USER / AGENT）
- `parts`：内容列表（text/raw/url/data）
- `contextId`：上下文标识
- `taskId`：关联的任务 ID

### Part（内容最小单元）
- `textPart`：文本内容
- `filePart`：文件引用（fileUri + mimeType）
- `dataPart`：结构化数据（JSON 对象）

### Artifact
- `artifactId`：产出物 ID
- `name`：名称（可选）
- `mimeType`：MIME 类型
- `parts`：内容列表

### AgentCard
- `name`、`description`
- `url`：服务地址
- `version`：协议版本
- `capabilities`：流式推送、推送通知、扩展 AgentCard 支持
- `skills`：Agent 技能列表
- `securitySchemes`、`security`：认证方案

---

## 7. 协议绑定（JSON-RPC & HTTP+REST）

### JSON-RPC 2.0
- **端点**：`POST /rpc`
- **Content-Type**：`application/json`
- **方法命名**：PascalCase（如 `SendMessage`、`GetTask`）
- **流式**：Server-Sent Events（`text/event-stream`）

### HTTP+REST
| 端点 | HTTP 方法 | A2A 操作 |
|------|-----------|----------|
| `/message:send` | POST | SendMessage |
| `/message:stream` | POST | SendStreamingMessage |
| `/tasks/{id}` | GET | GetTask |
| `/tasks` | GET | ListTasks |
| `/tasks/{id}:cancel` | POST | CancelTask |
| `/tasks/{id}:subscribe` | POST | SubscribeToTask |
| `/.well-known/agent-card.json` | GET | AgentCard（公开） |

---

## 8. 任务更新机制

| 机制 | 说明 | 适用场景 |
|------|------|----------|
| **轮询（Polling）** | 定期调用 GetTask | 简单集成 |
| **流式（Streaming）** | 实时推送更新 | 交互式应用 |
| **推送通知（Push）** | Webhook 回调 | 长时任务、Server-to-Server |

---

## 9. 认证基础

AgentCard 中声明安全方案：
- **API Key**：`api_key`
- **HTTP Auth**：`basic` / `bearer`
- **OAuth 2.0**：`oauth2`
- **OpenID Connect**：`openid`
- **Mutual TLS**：`mutual_tls`

---

## 10. 关键设计原则

1. **简单**：复用现有标准（HTTP、JSON-RPC 2.0、SSE）
2. **企业就绪**：支持认证、授权、安全、隐私
3. **异步优先**：原生支持长时任务
4. **模态无关**：支持 text、file、structured data
5. **不透明执行**：基于声明的能力协作，无需共享内部实现
