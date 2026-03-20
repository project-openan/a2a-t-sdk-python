# a2a-python 参考实现文档

> 基于 `reference/a2a-python/` 官方 Python SDK 源码整理，结合 A2A v1.0 规格文档。

---

## 1. 概述

`a2a-python` 是 A2A（Agent2Agent）协议的官方 Python SDK，提供构建 A2A 兼容 Agent 和客户端所需的全部基础设施。

**三层架构：**
- **数据模型层**：基于 Protobuf 生成的类型（`src/a2a/types/`）
- **抽象操作层**：抽象基类定义可扩展接口（`AgentExecutor`、`TaskStore` 等）
- **协议绑定层**：JSON-RPC、HTTP+REST、gRPC 三种协议实现

---

## 2. 项目结构

```
src/a2a/
├── client/          # 客户端：连接远程 Agent
├── server/          # 服务端：构建 Agent
├── types/           # Protobuf 生成的数据类型
├── auth/            # 认证：用户模型、上下文
├── utils/           # 工具函数：错误、任务、消息、常量
├── compat/v0_3/     # v0.3 协议向后兼容适配器
├── contrib/         # 扩展贡献（Vertex AI 集成）
├── extensions/      # 扩展支持框架
└── migrations/      # 数据库迁移支持
```

---

## 3. 核心数据模型（Protobuf 生成）

### 主要类型

| 类型 | 说明 |
|------|------|
| `Task` | 工作单元，有 id、contextId、status、history、artifacts |
| `Message` | 通信单元，有 role、parts、messageId |
| `Part` | 内容最小单元：text \| raw（字节）\| url \| data（结构化） |
| `Artifact` | 任务产出，有 artifactId、name、parts |
| `AgentCard` | Agent 元数据：name、description、supportedInterfaces、capabilities、skills |
| `TaskStatus` | 包含 state + timestamp + message |
| `StreamResponse` | 流式包装：task \| message \| statusUpdate \| artifactUpdate |

### TaskState 状态机

```
SUBMITTED → WORKING → COMPLETED
                    → FAILED
                    → CANCELED
                    → REJECTED
                    → INPUT_REQUIRED  （中断，等待用户输入）
                    → AUTH_REQUIRED   （中断，等待授权）
```

### JSON 字段命名规范

- 字段名：camelCase（如 `contextId`、`taskId`）
- 枚举值：SCREAMING_SNAKE_CASE（如 `TASK_STATE_COMPLETED`、`ROLE_USER`）
- 时间戳：ISO 8601 UTC（`YYYY-MM-DDTHH:mm:ss.sssZ`）

---

## 4. 客户端架构

### 核心类

| 类 | 文件 | 说明 |
|----|------|------|
| `Client`（ABC） | `client/client.py` | 定义所有客户端方法的抽象接口 |
| `BaseClient` | `client/base_client.py` | 与传输层无关的核心实现 |
| `ClientFactory` | `client/client_factory.py` | 根据 AgentCard 能力选择合适的传输层 |
| `A2ACardResolver` | `client/card_resolver.py` | 从 `/.well-known/agent-card.json` 获取 AgentCard |

### 客户端方法

| 方法 | 说明 |
|------|------|
| `send_message()` | 发送消息，自动选择流式/轮询 |
| `get_task()` | 获取任务状态和历史 |
| `list_tasks()` | 列出任务（支持过滤和分页） |
| `cancel_task()` | 取消任务 |
| `subscribe()` | 订阅任务事件流 |
| `get_extended_agent_card()` | 获取认证后的扩展 AgentCard |

### 传输层

| 传输层 | 文件 | 协议 | 流式方式 |
|--------|------|------|----------|
| `JsonRpcTransport` | `client/transports/jsonrpc.py` | JSON-RPC 2.0 over HTTP | SSE |
| `RestTransport` | `client/transports/rest.py` | HTTP+JSON REST | SSE |
| `GrpcTransport` | `client/transports/grpc.py` | Protocol Buffers over gRPC | 原生 gRPC 流 |

### 扩展点

- `Consumer`：事件回调，类型为 `Callable[[ClientEvent, AgentCard], Coroutine[None, Any, Any]]`
- `ClientCallInterceptor`（`client/interceptors.py`）：请求/响应前后钩子，用于注入认证等
- `TenantTransportDecorator`（`client/transports/tenant_decorator.py`）：装饰器，注入租户信息

---

## 5. 服务端架构

### 核心组件

| 组件 | 文件 | 说明 |
|------|------|------|
| `AgentExecutor`（ABC） | `server/agent_execution/agent_executor.py` | Agent 核心逻辑接口，实现 `execute()` 和 `cancel()` |
| `RequestHandler`（ABC） | `server/request_handlers/request_handler.py` | 协调所有组件，处理所有 A2A 方法 |
| `DefaultRequestHandler` | `server/request_handlers/default_request_handler.py` | RequestHandler 的默认实现 |
| `RequestContext` | `server/context.py` | 请求上下文：message、task_id、context_id、related_tasks |
| `TaskManager` | `server/tasks/task_manager.py` | 管理任务生命周期，从 TaskStore 读写任务 |
| `TaskUpdater` | `server/tasks/task_updater.py` | Agent 发布更新的辅助类 |

### TaskUpdater 方法

| 方法 | 说明 |
|------|------|
| `start_work()` | 将任务状态设为 WORKING |
| `update_status()` | 更新任务状态（含可选消息） |
| `add_artifact()` | 添加产出物 |
| `cancel()` | 取消任务 |

### DefaultRequestHandler 依赖关系

```
DefaultRequestHandler
  ├── AgentExecutor          # 业务逻辑（开发者实现）
  ├── TaskStore              # 任务持久化
  ├── QueueManager           # 事件队列管理
  └── PushNotificationSender # 推送通知发送
```

### 流式机制

| 组件 | 说明 |
|------|------|
| `EventQueue` | 有界异步队列（默认 1024），支持 `tap()` 扇出 |
| `EventConsumer` | `consume_all()` 异步生成器，持续产出事件直到终止状态 |

事件类型：`Task`、`Message`、`TaskStatusUpdateEvent`、`TaskArtifactUpdateEvent`

---

## 6. 服务端协议绑定

### JSON-RPC 绑定（`A2AStarletteApplication`）

- 文件：`server/apps/starlette_app.py`
- 基于 Starlette 框架
- 所有请求 POST 到单一端点 `/rpc`
- 方法名：PascalCase（`SendMessage`、`GetTask` 等）
- 流式响应：SSE（`text/event-stream`），每条事件为 JSON-RPC 响应格式
- 包含 v0.3 向后兼容适配器 `JSONRPC03Adapter`
- 支持 `card_modifier` / `extended_card_modifier` 回调动态修改 AgentCard

### HTTP+REST 绑定（`A2ARESTFastAPIApplication`）

- 文件：`server/apps/fastapi_app.py`
- 基于 FastAPI 框架

| 端点 | HTTP 方法 | A2A 方法 | 说明 |
|------|-----------|----------|------|
| `/message:send` | POST | SendMessage | 发送消息 |
| `/message:stream` | POST | SendStreamingMessage | 流式发送 |
| `/tasks/{id}` | GET | GetTask | 获取任务 |
| `/tasks` | GET | ListTasks | 列出任务 |
| `/tasks/{id}:cancel` | POST | CancelTask | 取消任务 |
| `/tasks/{id}:subscribe` | POST | SubscribeToTask | 订阅任务 |
| `/tasks/{id}/pushNotificationConfigs` | POST | CreatePushConfig | 创建推送配置 |
| `/extendedAgentCard` | GET | GetExtendedAgentCard | 获取扩展 AgentCard |
| `/.well-known/agent-card.json` | GET | — | 公开 AgentCard |

### gRPC 绑定（`GrpcHandler`）

- 文件：`server/request_handlers/grpc_handler.py`
- 实现 `A2AService` gRPC 服务
- 原生服务端流式 RPC
- 错误通过 `google.rpc.Status` + `google.rpc.ErrorInfo` 传递

---

## 7. 持久化层

### TaskStore（ABC）

文件：`server/tasks/task_store.py`

| 方法 | 说明 |
|------|------|
| `save(task)` | 保存任务 |
| `get(task_id)` | 按 ID 查询 |
| `list(...)` | 过滤+分页查询 |
| `delete(task_id)` | 删除任务 |

实现类：
- `InMemoryTaskStore`（`server/tasks/inmemory_task_store.py`）：内存存储，进程重启后丢失
- `DatabaseTaskStore`（`server/tasks/database_task_store.py`）：SQLAlchemy，支持过滤、分页、按更新时间排序

分页方式：**游标分页**（base64 编码的 cursor token），非 offset 分页。

### PushNotificationConfigStore（ABC）

文件：`server/tasks/push_notification_config_store.py`

| 方法 | 说明 |
|------|------|
| `set_info(task_id, config, context)` | 设置或更新推送配置 |
| `get_info(task_id, context)` | 获取推送配置列表 |
| `delete_info(task_id, context, config_id=None)` | 删除推送配置 |

实现类：
- `InMemoryPushNotificationConfigStore`（`server/tasks/inmemory_push_notification_config_store.py`）
- `DatabasePushNotificationConfigStore`（`server/tasks/database_push_notification_config_store.py`）

### 数据库模型（SQLAlchemy Mixin）

- `TaskMixin` / `TaskModel`：任务持久化，表名可配置
- `PushNotificationConfigMixin` / `PushNotificationConfigModel`：推送配置持久化

---

## 8. 错误类型体系

### 错误层级

```
A2AError（基类）
  ├── TaskNotFoundError                    # -32001 / 404
  ├── TaskNotCancelableError               # -32002 / 409
  ├── PushNotificationNotSupportedError    # -32003 / 400
  ├── UnsupportedOperationError            # -32004 / 400
  ├── ContentTypeNotSupportedError         # -32005 / 415
  ├── InvalidAgentResponseError            # -32006 / 502
  ├── ExtendedAgentCardNotConfiguredError  # -32007 / 400
  ├── ExtensionSupportRequiredError        # -32008 / 400
  └── VersionNotSupportedError             # -32009 / 400
```

### 错误映射字典（`utils/errors.py`）

| 字典 | 说明 |
|------|------|
| `JSON_RPC_ERROR_CODE_MAP` | 异常类型 → JSON-RPC 错误码 |
| `A2A_REST_ERROR_MAPPING` | 异常类型 → HTTP 状态码 + gRPC 状态 + reason 字符串 |
| `A2A_REASON_TO_ERROR` | reason 字符串 → 异常类型（反向映射） |

### JSON-RPC 错误响应格式

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "error": {
    "code": -32001,
    "message": "Task not found",
    "data": [{
      "@type": "type.googleapis.com/google.rpc.ErrorInfo",
      "reason": "TASK_NOT_FOUND",
      "domain": "a2a-protocol.org"
    }]
  }
}
```

---

## 9. 认证与授权

### 用户模型

- `User`（ABC）：基础用户接口
- `UnauthenticatedUser`：默认未认证用户
- `ServerCallContext.user`：服务端当前用户

### 客户端认证

- `ClientCallInterceptor`：注入认证凭据的钩子
- 凭据通过 HTTP Header 传递（Bearer Token、API Key 等）
- 支持自定义凭据提供者

### 服务端认证

- `CallContextBuilder`：从请求构建上下文
- 认证方案在 AgentCard 的 `securitySchemes` 中声明
- 支持：API Key、HTTP Auth、OAuth 2.0、OpenID Connect、Mutual TLS

---

## 10. 工具函数

### 任务工具（`utils/task.py`）

| 函数 | 说明 |
|------|------|
| `new_task()` | 从 Message 创建新任务 |
| `completed_task()` | 创建已完成的任务 |
| `apply_history_length()` | 限制历史记录长度 |
| `encode_page_token()` | 分页游标编码（→ base64） |
| `decode_page_token()` | 分页游标解码（base64 →） |

### 内容工具

| 函数 | 文件 | 说明 |
|------|------|------|
| `get_text_parts()` | `utils/parts.py` | 提取文本 Part |
| `get_data_parts()` | `utils/parts.py` | 提取结构化数据 Part |
| `get_file_parts()` | `utils/parts.py` | 提取文件 Part |
| `get_message_text()` | `utils/message.py` | 从 Message 提取文本 |
| `append_artifact_to_task()` | `utils/artifact.py` | 向任务添加产出物 |

### 常量（`utils/constants.py`）

- 协议版本：`1.0`、`0.3`
- 传输协议：`JSONRPC`、`HTTP+JSON`、`GRPC`
- Well-known 路径：`/.well-known/agent-card.json`
- 分页大小限制

### 遥测（`utils/telemetry.py`）

- `@trace_class()` 装饰器：OpenTelemetry span 追踪
- `SpanKind` 枚举：CLIENT、SERVER

---

## 11. 关键设计模式

| 模式 | 应用场景 |
|------|----------|
| 抽象基类（ABC） | TaskStore、AgentExecutor、Client、PushNotificationSender 等所有可插拔组件 |
| 工厂模式 | ClientFactory 根据 AgentCard 选择传输层 |
| 策略模式 | AgentExecutor、TaskStore、PushNotificationSender 可替换实现 |
| 观察者模式 | EventQueue + Consumer 回调，支持 tap() 扇出 |
| 装饰器模式 | TenantTransportDecorator 包装传输层注入租户信息 |
| 适配器模式 | JSONRPC03Adapter、REST03Adapter 兼容 v0.3 协议 |
| 模板方法 | RequestHandler 定义流程，子类实现细节 |

---

## 12. v0.3 向后兼容

### 主要破坏性变更（v0.3 → v1.0）

| 变更点 | v0.3 | v1.0 |
|--------|------|------|
| Part 类型判别符 | `"kind": "text"` 字段 | 成员名本身（`{"text": "..."}`) |
| 扩展 AgentCard 字段位置 | 顶层 `supportsExtendedAgentCard` | `capabilities.extendedAgentCard` |
| 流式事件包装 | `kind` 字段 | `statusUpdate`/`artifactUpdate` 包装键 |

### 兼容层（`compat/v0_3/`）

- `JSONRPC03Adapter`（`compat/v0_3/jsonrpc_adapter.py`）：JSON-RPC v0.3 请求/响应转换
- `REST03Adapter`（`compat/v0_3/rest_adapter.py`）：REST v0.3 兼容

