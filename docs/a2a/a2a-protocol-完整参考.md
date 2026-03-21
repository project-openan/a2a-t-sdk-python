# A2A 协议完整参考

> 基于 A2A Protocol Specification v1.0
> 本文档仅包含 JSON-RPC 和 HTTP+REST 绑定，不包含 gRPC

---

## 目录

1. [数据模型](#1-数据模型)
2. [JSON-RPC 协议绑定](#2-json-rpc-协议绑定)
3. [HTTP+REST 协议绑定](#3-httprest-协议绑定)
4. [操作详解](#4-操作详解)
5. [错误处理](#5-错误处理)
6. [示例](#6-示例)

---

## 1. 数据模型

### 1.1 Task

任务的基本工作单元。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 任务唯一标识（Server 生成） |
| `contextId` | string | 上下文标识，用于关联相关任务 |
| `status` | TaskStatus | 任务状态 |
| `artifacts` | Artifact[] | 产出物列表 |
| `history` | Message[] | 消息历史 |
| `metadata` | Map<string, any> | 自定义元数据 |

### 1.2 TaskStatus

任务状态对象。

| 字段 | 类型 | 说明 |
|------|------|------|
| `state` | TaskState | 任务状态枚举 |
| `message` | Message | 状态相关消息（可选） |
| `timestamp` | string | 状态更新时间（ISO 8601） |

### 1.3 TaskState

任务状态枚举。

| 枚举值 | 说明 |
|--------|------|
| `TASK_STATE_SUBMITTED` | 已提交 |
| `TASK_STATE_WORKING` | 处理中 |
| `TASK_STATE_COMPLETED` | 已完成 |
| `TASK_STATE_FAILED` | 失败 |
| `TASK_STATE_CANCELED` | 已取消 |
| `TASK_STATE_REJECTED` | 已拒绝 |
| `TASK_STATE_INPUT_REQUIRED` | 需要用户输入（中断） |
| `TASK_STATE_AUTH_REQUIRED` | 需要认证（中断） |

### 1.4 Message

通信单元。

| 字段 | 类型 | 说明 |
|------|------|------|
| `messageId` | string | 消息唯一标识 |
| `role` | Role | 角色：USER 或 AGENT |
| `parts` | Part[] | 内容部件列表 |
| `contextId` | string | 上下文标识 |
| `taskId` | string | 关联的任务 ID |
| `referenceTaskIds` | string[] | 引用的相关任务 ID |
| `metadata` | Map<string, any> | 自定义元数据 |

### 1.5 Role

消息角色枚举。

| 枚举值 | 说明 |
|--------|------|
| `ROLE_USER` | 用户 |
| `ROLE_AGENT` | Agent |

### 1.6 Part

内容的最小单元。一个 Message 或 Artifact 包含一个或多个 Part。

| 字段 | 类型 | 说明 |
|------|------|------|
| `text` | TextPart | 文本内容 |
| `file` | FilePart | 文件引用 |
| `data` | DataPart | 结构化数据 |

#### 1.6.1 TextPart

```json
{
  "text": "这是文本内容"
}
```

#### 1.6.2 FilePart

```json
{
  "file": {
    "uri": "https://example.com/files/doc.pdf",
    "mimeType": "application/pdf",
    "name": "document.pdf"  // 可选
  }
}
```

#### 1.6.3 DataPart

```json
{
  "data": {
    "key": "value",
    "nested": {
      "field": 123
    }
  }
}
```

### 1.7 Artifact

任务产出物。

| 字段 | 类型 | 说明 |
|------|------|------|
| `artifactId` | string | 产出物 ID |
| `name` | string | 名称（可选） |
| `mimeType` | string | MIME 类型 |
| `parts` | Part[] | 内容部件列表 |
| `metadata` | Map<string, any> | 自定义元数据 |

### 1.8 AgentCard

Agent 元数据文档。

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | Agent 名称 |
| `description` | string | Agent 描述 |
| `url` | string | 服务端点 URL |
| `version` | string | A2A 协议版本（如 "1.0"） |
| `capabilities` | AgentCapabilities | Agent 能力 |
| `provider` | AgentProvider | 提供者信息（可选） |
| `skills` | AgentSkill[] | Agent 技能列表 |
| `supportedInterfaces` | AgentInterface[] | 支持的接口列表 |
| `securitySchemes` | SecurityScheme[] | 安全方案定义 |
| `security` | string[] | 默认启用的安全方案 |
| `authentication` | AuthenticationInfo | 认证信息（可选） |

### 1.9 AgentCapabilities

Agent 能力。

| 字段 | 类型 | 说明 |
|------|------|------|
| `streaming` | boolean | 是否支持流式推送 |
| `pushNotifications` | boolean | 是否支持推送通知 |
| `extendedAgentCard` | boolean | 是否支持扩展 AgentCard |

### 1.10 AgentSkill

Agent 技能。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 技能 ID |
| `name` | string | 技能名称 |
| `description` | string | 技能描述 |
| `tags` | string[] | 标签列表 |
| `examples` | string[] | 使用示例 |
| `inputModes` | string[] | 支持的输入模式 |
| `outputModes` | string[] | 支持的输出模式 |

### 1.11 SendMessageRequest

发送消息请求。

| 字段 | 类型 | 说明 |
|------|------|------|
| `message` | Message | 要发送的消息 |
| `taskId` | string | 关联的任务 ID（可选） |
| `contextId` | string | 上下文 ID（可选） |
| `configuration` | SendMessageConfiguration | 配置（可选） |
| `metadata` | Map<string, any> | 自定义元数据（可选） |

### 1.12 SendMessageConfiguration

消息发送配置。

| 字段 | 类型 | 说明 |
|------|------|------|
| `acceptedOutputModes` | string[] | 接受的输出模式 |
| `returnImmediately` | boolean | 是否立即返回（不等待任务完成） |
| `sessionId` | string | 会话 ID（可选） |

### 1.13 StreamResponse

流式响应包装对象。

```json
{
  "task": { /* Task 对象 */ },
  "message": { /* Message 对象 */ },
  "statusUpdate": { /* TaskStatusUpdateEvent 对象 */ },
  "artifactUpdate": { /* TaskArtifactUpdateEvent 对象 */ }
}
```

### 1.14 TaskStatusUpdateEvent

任务状态更新事件。

| 字段 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务 ID |
| `status` | TaskStatus | 新状态 |
| `final` | boolean | 是否为最终状态 |

### 1.15 TaskArtifactUpdateEvent

任务产出物更新事件。

| 字段 | 类型 | 说明 |
|------|------|------|
| `taskId` | string | 任务 ID |
| `artifact` | Artifact | 更新的产出物 |
| `append` | boolean | 是否追加（true）或替换（false） |

### 1.16 PushNotificationConfig

推送通知配置。

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | string | 配置 ID |
| `taskId` | string | 任务 ID |
| `webhookUrl` | string | Webhook URL |
| `authentication` | AuthenticationInfo | 认证信息 |

---

## 2. JSON-RPC 协议绑定

### 2.1 协议要求

- **协议**：JSON-RPC 2.0 over HTTP(S)
- **Content-Type**：`application/json`
- **方法命名**：PascalCase（如 `SendMessage`、`GetTask`）
- **流式**：Server-Sent Events（`text/event-stream`）

### 2.2 服务参数传输

通过 HTTP Header 传输：

| Header | 说明 | 示例 |
|--------|------|------|
| `A2A-Version` | 协议版本 | `1.0` |
| `A2A-Extensions` | 扩展 URI 列表（逗号分隔） | `https://example.com/ext/v1` |

### 2.3 请求结构

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": { /* 方法特定参数 */ }
}
```

### 2.4 方法映射

| A2A 操作 | JSON-RPC 方法 |
|----------|---------------|
| SendMessage | `SendMessage` |
| SendStreamingMessage | `SendStreamingMessage` |
| GetTask | `GetTask` |
| ListTasks | `ListTasks` |
| CancelTask | `CancelTask` |
| SubscribeToTask | `SubscribeToTask` |
| CreatePushNotificationConfig | `CreateTaskPushNotificationConfig` |
| GetPushNotificationConfig | `GetTaskPushNotificationConfig` |
| ListPushNotificationConfigs | `ListTaskPushNotificationConfigs` |
| DeletePushNotificationConfig | `DeleteTaskPushNotificationConfig` |
| GetExtendedAgentCard | `GetExtendedAgentCard` |

---

## 3. HTTP+REST 协议绑定

### 3.1 协议要求

- **协议**：HTTP(S) + JSON
- **Content-Type**：`application/json`
- **方法**：标准 HTTP 动词（GET、POST、PUT、DELETE）
- **流式**：Server-Sent Events

### 3.2 端点映射

| A2A 操作 | HTTP 端点 | HTTP 方法 |
|----------|-----------|-----------|
| SendMessage | `/message:send` | POST |
| SendStreamingMessage | `/message:stream` | POST |
| GetTask | `/tasks/{id}` | GET |
| ListTasks | `/tasks` | GET |
| CancelTask | `/tasks/{id}:cancel` | POST |
| SubscribeToTask | `/tasks/{id}:subscribe` | POST |
| CreatePushNotificationConfig | `/tasks/{id}/pushNotificationConfigs` | POST |
| GetPushNotificationConfig | `/tasks/{id}/pushNotificationConfigs/{configId}` | GET |
| ListPushNotificationConfigs | `/tasks/{id}/pushNotificationConfigs` | GET |
| DeletePushNotificationConfig | `/tasks/{id}/pushNotificationConfigs/{configId}` | DELETE |
| GetExtendedAgentCard | `/extendedAgentCard` | GET |
| AgentCard（公开） | `/.well-known/agent-card.json` | GET |

### 3.3 公开 AgentCard

Agent 必须公开其 AgentCard 于 `/.well-known/agent-card.json`。

```json
{
  "name": "My Agent",
  "description": "A helpful assistant agent",
  "url": "https://agent.example.com",
  "version": "1.0",
  "capabilities": {
    "streaming": true,
    "pushNotifications": false,
    "extendedAgentCard": false
  },
  "skills": []
}
```

---

## 4. 操作详解

### 4.1 SendMessage

发送消息以启动或继续任务。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `message` | Message | 是 | 要发送的消息 |
| `taskId` | string | 否 | 现有任务 ID（继续对话） |
| `contextId` | string | 否 | 上下文 ID |
| `configuration` | SendMessageConfiguration | 否 | 配置 |

**返回：**

- `Task` 对象（异步处理）
- `Message` 对象（直接响应）

**行为：**

- Agent 可以创建新 Task 异步处理，也可以直接返回 Message
- `return_immediately: false`（默认）：阻塞直到任务达到终态
- `return_immediately: true`：立即返回，客户端通过轮询/订阅/推送获取更新

### 4.2 SendStreamingMessage

发送消息并实时推送更新。

**请求参数：** 同 SendMessage

**返回：** SSE 流

```
data: {"task": {...}}
data: {"statusUpdate": {...}}
data: {"artifactUpdate": {...}}
```

**流内容：**

1. 初始：Task 或 Message
2. 后续：零个或多个 TaskStatusUpdateEvent / TaskArtifactUpdateEvent
3. 结束：任务达到终态时关闭

### 4.3 GetTask

获取任务当前状态。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 任务 ID |
| `historyLength` | int | 否 | 返回的历史消息数量 |

**返回：** Task 对象

### 4.4 ListTasks

列出任务（支持过滤和分页）。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `contextId` | string | 否 | 按上下文过滤 |
| `status` | TaskState | 否 | 按状态过滤 |
| `pageSize` | int | 否 | 每页数量（默认 50） |
| `pageToken` | string | 否 | 分页游标 |

**返回：**

```json
{
  "tasks": [/* Task 数组 */],
  "nextPageToken": ""  // 空字符串表示最后一页
}
```

### 4.5 CancelTask

取消任务。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 任务 ID |

**返回：** 更新后的 Task 对象

**错误：**

- TaskNotCancelableError：任务不在可取消状态

### 4.6 SubscribeToTask

订阅任务事件流。

**请求参数：**

| 参数 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `id` | string | 是 | 任务 ID |

**返回：** SSE 流

**错误：**

- UnsupportedOperationError：任务处于终态
- TaskNotFoundError：任务不存在

### 4.7-4.10 推送通知配置

**CRUD 操作：**

- CreateTaskPushNotificationConfig
- GetTaskPushNotificationConfig
- ListTaskPushNotificationConfigs
- DeleteTaskPushNotificationConfig

### 4.11 GetExtendedAgentCard

获取认证后的扩展 AgentCard。

**前提：** AgentCard.capabilities.extendedAgentCard 必须为 true

**要求：** 必须认证

---

## 5. 错误处理

### 5.1 错误类型

| 错误名称 | 说明 |
|----------|------|
| `TaskNotFoundError` | 任务不存在或无法访问 |
| `TaskNotCancelableError` | 任务不在可取消状态 |
| `PushNotificationNotSupportedError` | Agent 不支持推送通知 |
| `UnsupportedOperationError` | 操作不支持 |
| `ContentTypeNotSupportedError` | 不支持的媒体类型 |
| `InvalidAgentResponseError` | Agent 响应无效 |
| `ExtendedAgentCardNotConfiguredError` | 未配置扩展 AgentCard |
| `ExtensionSupportRequiredError` | 需要支持的扩展 |
| `VersionNotSupportedError` | 不支持的协议版本 |

### 5.2 JSON-RPC 错误码

| JSON-RPC 错误码 | 错误名称 | 说明 |
|-----------------|----------|------|
| `-32700` | JSONParseError | 无效 JSON |
| `-32600` | InvalidRequestError | 无效请求 |
| `-32601` | MethodNotFoundError | 方法不存在 |
| `-32602` | InvalidParamsError | 无效参数 |
| `-32603` | InternalError | 内部错误 |
| `-32001` | TaskNotFoundError | 任务未找到 |
| `-32002` | TaskNotCancelableError | 任务不可取消 |
| `-32003` | PushNotificationNotSupportedError | 推送通知不支持 |
| `-32004` | UnsupportedOperationError | 不支持的操作 |
| `-32005` | ContentTypeNotSupportedError | 内容类型不支持 |
| `-32006` | InvalidAgentResponseError | Agent 响应无效 |
| `-32007` | ExtendedAgentCardNotConfiguredError | 扩展 AgentCard 未配置 |
| `-32008` | ExtensionSupportRequiredError | 需要扩展支持 |
| `-32009` | VersionNotSupportedError | 版本不支持 |

### 5.3 HTTP 状态码映射

| 错误名称 | HTTP 状态码 |
|----------|-------------|
| TaskNotFoundError | 404 |
| TaskNotCancelableError | 409 |
| PushNotificationNotSupportedError | 400 |
| UnsupportedOperationError | 400 |
| ContentTypeNotSupportedError | 415 |
| InvalidAgentResponseError | 502 |
| ExtendedAgentCardNotConfiguredError | 400 |
| ExtensionSupportRequiredError | 400 |
| VersionNotSupportedError | 400 |

---

## 6. 示例

### 6.1 JSON-RPC 请求示例

#### SendMessage 请求

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "method": "SendMessage",
  "params": {
    "message": {
      "messageId": "msg-001",
      "role": "ROLE_USER",
      "parts": [
        {
          "text": {
            "text": "帮我查找附近的餐厅"
          }
        }
      ]
    },
    "configuration": {
      "acceptedOutputModes": ["text/plain"],
      "returnImmediately": false
    }
  }
}
```

#### SendMessage 响应（返回 Task）

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "task": {
      "id": "task-123",
      "contextId": "ctx-456",
      "status": {
        "state": "TASK_STATE_WORKING",
        "timestamp": "2025-01-15T10:30:00.000Z"
      },
      "artifacts": [],
      "history": []
    }
  }
}
```

#### SendMessage 响应（返回 Message）

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "message": {
      "messageId": "msg-002",
      "role": "ROLE_AGENT",
      "parts": [
        {
          "text": {
            "text": "您好！我是助手。"
          }
        }
      ]
    }
  }
}
```

#### GetTask 请求

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "GetTask",
  "params": {
    "id": "task-123",
    "historyLength": 10
  }
}
```

#### GetTask 响应

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "id": "task-123",
    "contextId": "ctx-456",
    "status": {
      "state": "TASK_STATE_COMPLETED",
      "timestamp": "2025-01-15T10:35:00.000Z"
    },
    "artifacts": [
      {
        "artifactId": "artifact-001",
        "mimeType": "text/plain",
        "parts": [
          {
            "text": {
              "text": "以下是附近的餐厅..."
            }
          }
        ]
      }
    ],
    "history": [
      {
        "messageId": "msg-001",
        "role": "ROLE_USER",
        "parts": [{"text": {"text": "帮我查找附近的餐厅"}}]
      },
      {
        "messageId": "msg-002",
        "role": "ROLE_AGENT",
        "parts": [{"text": {"text": "以下是附近的餐厅..."}}]
      }
    ]
  }
}
```

#### ListTasks 请求

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "method": "ListTasks",
  "params": {
    "contextId": "ctx-456",
    "status": "TASK_STATE_COMPLETED",
    "pageSize": 10,
    "pageToken": ""
  }
}
```

#### ListTasks 响应

```json
{
  "jsonrpc": "2.0",
  "id": 3,
  "result": {
    "tasks": [
      {
        "id": "task-123",
        "contextId": "ctx-456",
        "status": {
          "state": "TASK_STATE_COMPLETED",
          "timestamp": "2025-01-15T10:35:00.000Z"
        }
      }
    ],
    "nextPageToken": ""
  }
}
```

#### CancelTask 请求

```json
{
  "jsonrpc": "2.0",
  "id": 4,
  "method": "CancelTask",
  "params": {
    "id": "task-123"
  }
}
```

#### 错误响应示例

```json
{
  "jsonrpc": "2.0",
  "id": 5,
  "error": {
    "code": -32001,
    "message": "Task not found",
    "data": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "TASK_NOT_FOUND",
        "domain": "a2a-protocol.org",
        "metadata": {
          "taskId": "nonexistent-task-id",
          "timestamp": "2025-01-15T10:30:00.000Z"
        }
      }
    ]
  }
}
```

### 6.2 HTTP+REST 请求示例

#### SendMessage (POST /message:send)

```http
POST /message:send HTTP/1.1
Host: agent.example.com
Content-Type: application/json
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
A2A-Version: 1.0

{
  "message": {
    "messageId": "msg-001",
    "role": "ROLE_USER",
    "parts": [
      {
        "text": {
          "text": "帮我查找附近的餐厅"
        }
      }
    ]
  },
  "configuration": {
    "acceptedOutputModes": ["text/plain"],
    "returnImmediately": false
  }
}
```

#### 响应 (HTTP/JSON)

```http
HTTP/1.1 200 OK
Content-Type: application/json

{
  "task": {
    "id": "task-123",
    "contextId": "ctx-456",
    "status": {
      "state": "TASK_STATE_COMPLETED",
      "timestamp": "2025-01-15T10:35:00.000Z"
    },
    "artifacts": [
      {
        "artifactId": "artifact-001",
        "mimeType": "text/plain",
        "parts": [
          {
            "text": {
              "text": "以下是附近的餐厅..."
            }
          }
        ]
      }
    ],
    "history": []
  }
}
```

#### GetTask (GET /tasks/{id})

```http
GET /tasks/task-123?historyLength=10 HTTP/1.1
Host: agent.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
A2A-Version: 1.0
```

#### ListTasks (GET /tasks)

```http
GET /tasks?contextId=ctx-456&status=completed&pageSize=10 HTTP/1.1
Host: agent.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
A2A-Version: 1.0
```

#### CancelTask (POST /tasks/{id}:cancel)

```http
POST /tasks/task-123:cancel HTTP/1.1
Host: agent.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
A2A-Version: 1.0
```

#### SubscribeToTask (POST /tasks/{id}:subscribe)

```http
POST /tasks/task-123:subscribe HTTP/1.1
Host: agent.example.com
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
A2A-Version: 1.0

HTTP/1.1 200 OK
Content-Type: text/event-stream

data: {"task":{"id":"task-123","status":{"state":"TASK_STATE_WORKING"}}}

data: {"statusUpdate":{"taskId":"task-123","status":{"state":"TASK_STATE_WORKING","message":{"role":"ROLE_AGENT","parts":[{"text":{"text":"正在搜索..."}}]}}}}

data: {"artifactUpdate":{"taskId":"task-123","artifact":{"artifactId":"a1","parts":[{"text":{"text":"找到3家餐厅"}}]}}}

data: {"task":{"id":"task-123","status":{"state":"TASK_STATE_COMPLETED"}}}
```

#### 错误响应 (HTTP)

```http
HTTP/1.1 404 Not Found
Content-Type: application/json

{
  "error": {
    "code": 404,
    "status": "NOT_FOUND",
    "message": "The specified task ID does not exist or is not accessible",
    "details": [
      {
        "@type": "type.googleapis.com/google.rpc.ErrorInfo",
        "reason": "TASK_NOT_FOUND",
        "domain": "a2a-protocol.org",
        "metadata": {
          "taskId": "task-123",
          "timestamp": "2025-01-15T10:30:00.000Z"
        }
      }
    ]
  }
}
```

### 6.3 流式推送示例

#### SendStreamingMessage (POST /message:stream)

```http
POST /message:stream HTTP/1.1
Host: agent.example.com
Content-Type: application/json
Authorization: Bearer eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

{
  "message": {
    "role": "ROLE_USER",
    "parts": [{"text": {"text": "写一首关于春天的诗"}}]
  }
}
```

#### SSE 响应

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream

data: {"task":{"id":"task-789","status":{"state":"TASK_STATE_WORKING"}}}

data: {"statusUpdate":{"taskId":"task-789","status":{"state":"TASK_STATE_WORKING","message":{"role":"ROLE_AGENT","parts":[{"text":{"text":"让我为您创作..."}}]}}}}

data: {"artifactUpdate":{"taskId":"task-789","artifact":{"artifactId":"poem-001","mimeType":"text/plain","parts":[{"text":{"text":"春风拂面..."}}]}}}

data: {"task":{"id":"task-789","status":{"state":"TASK_STATE_COMPLETED","message":{"role":"ROLE_AGENT","parts":[{"text":{"text":"诗作已完成！"}}}}}}
```

---

## 附录：字段命名规范

- **JSON 字段名**：camelCase（如 `contextId`、`taskId`、`messageId`）
- **枚举值**：SCREAMING_SNAKE_CASE（如 `TASK_STATE_COMPLETED`、`ROLE_USER`）
- **时间戳**：ISO 8601 UTC（如 `2025-01-15T10:30:00.000Z`）
