# `a2a_t.llm` 当前使用规格限制

## 1. 文档目的

本文档总结截至 2026-04-16 当前代码实际生效的 `a2a_t.llm` 使用规格限制，重点面向：

- `LLMClient`
- `LLMAdapter`
- SessionStore 行为
- 四类 provider adapter 的能力边界

本文档描述的是 **当前代码事实**，不是未来可能的设计目标。

建议配套阅读：

- `2026-04-16-a2a-t-llm-design.md`
- `2026-04-16-a2a-t-llm-evolution.md`

## 2. 总体原则

当前 `a2a_t.llm` 更适合以下场景：

- 受控的多 provider 推理调用
- JSON object 输出
- 结构化输出或半结构化输出
- 需要有限 chat session 记忆的场景

当前模块不应被假设为：

- 通用自由文本聊天平台
- 流式 LLM 平台
- 持久化会话平台
- 多模态统一平台

## 3. Provider 支持范围

当前支持的 provider 固定为：

- `openai`
- `deepseek`
- `google`
- `anthropic`

传入其他 provider 时，会在运行时配置构建阶段直接报错。

## 4. 配置文件限制

### 4.1 `.env` 加载

- `LLMClient` 默认从 `package_data/.env` 读取配置
- 若传入 `env_path`，则读取指定路径
- 配置文件不存在时，`LLMClient` 初始化直接报 `LLMConfigError`

### 4.2 必填配置

以下配置在初始化阶段必须可解析出有效值：

- `A2AT_LLM_PROVIDER`
- `A2AT_LLM_MODEL`

否则 `LLMClient` 初始化会失败。

### 4.3 `api_key` 语义

- `A2AT_LLM_API_KEY` 会在调用前做 `strip()`
- 空字符串或纯空白字符串被视为未配置
- 该错误通常在 `chat()` / `complete()` / `structured()` 调用时抛出，而不是在 `LLMClient` 初始化时抛出

### 4.4 默认值

当前实现中的默认值为：

```dotenv
A2AT_LLM_HISTORY_WINDOW=10
A2AT_LLM_SESSION_MAX_TOTAL=300
A2AT_LLM_SESSION_MAX_PER_PROVIDER=100
```

### 4.5 数值配置限制

#### `A2AT_LLM_HISTORY_WINDOW`

- 必须是正整数
- 最大值为 `100`

#### `A2AT_LLM_SESSION_MAX_TOTAL`

- 必须是正整数
- 最大值为 `3000`
- 语义是“当前进程默认共享 root session store 可保留的 session 总数上限”

#### `A2AT_LLM_SESSION_MAX_PER_PROVIDER`

- 必须是正整数
- 最大值为 `1000`
- 语义是“单个 provider 在 root store 中可保留的 session 数量上限”

#### 组合约束

必须满足：

```text
A2AT_LLM_SESSION_MAX_TOTAL >= A2AT_LLM_SESSION_MAX_PER_PROVIDER
```

否则 `LLMClient` 初始化直接失败。

## 5. `LLMClient` 公共接口限制

### 5.1 初始化参数

当前 `LLMClient.__init__()` 只公开：

- `env_path: Path | None = None`

当前不再公开：

- `session_store`

因此调用方不能再向 `LLMClient` 注入自定义 session store。

### 5.2 显式 runtime override 参数

当前方法级 runtime override 参数已经收敛为显式签名：

- `chat()`: `provider`, `model`, `api_key`, `base_url`, `temperature`, `max_tokens`, `timeout_seconds`, `history_window`
- `complete()`: `provider`, `model`, `api_key`, `base_url`, `temperature`, `max_tokens`, `timeout_seconds`
- `structured()`: `provider`, `model`, `api_key`, `base_url`, `temperature`, `max_tokens`, `timeout_seconds`

上述接口不再接受未声明的公开 `**kwargs`。

### 5.3 `history_window`

- `history_window` 只属于 `chat()`
- `complete()` 不接受 `history_window`
- `structured()` 不接受 `history_window`

这类调用会直接表现为 Python `TypeError`。

### 5.4 `provider` / `model`

- 方法级 `provider` / `model` 可以覆盖 `.env` 默认值
- 覆盖值仍必须是合法 provider 和非空 model

### 5.5 `api_key` / `base_url` / `timeout_seconds`

- 这些值都支持方法级显式覆盖
- 若未覆盖，则使用 `.env` 默认值
- 是否被底层 provider 完整消费，取决于 adapter 实现

### 5.6 `reset_session()` / `delete_session()`

当前 `LLMClient` 的 session 管理接口为：

- `reset_session(session_id)`
- `delete_session(session_id)`

它们不接受：

- `provider`
- `model`
- `api_key`
- `base_url`
- `timeout_seconds`
- `history_window`
- 其他未声明参数

## 6. Session 使用限制

### 6.1 Session 仅存在于 `chat()`

- `chat()` 会创建和复用 session
- `complete()` 不使用 session
- `structured()` 不使用 session

因此：

- `complete()` 返回的 `session_id` 永远是 `None`
- `structured()` 不会创建会话状态

### 6.2 Session ID 格式固定

当前 session id 固定格式为：

```text
<provider>-<uuid>
```

例如：

- `openai-xxxx`
- `deepseek-xxxx`

### 6.3 Session 与 provider 强绑定

同一个 session 只能被其所属 provider 继续使用。

例如：

- `openai-...` 的 session 不能交给 `deepseek`
- `deepseek-...` 的 session 不能交给 `openai`

当前通过两层机制保证这一点：

- `session_id` 前缀过滤
- `ChatSession.provider` 元数据校验

### 6.4 默认 Session 作用域是进程级共享

默认情况下，不同 `LLMClient()` 实例会共享同一个进程级默认 root session store。

这意味着：

- 新建 `LLMClient` 不会自动拥有一份独立 session 空间
- 同一进程中旧实例创建的 session，可被新实例继续访问

但同时：

- provider 隔离仍然存在
- root store 的容量配置采用首次初始化锁定

因此：

- 第一个 `LLMClient` 会锁定 `session_max_total` / `session_max_per_provider`
- 后续若新建 `LLMClient` 使用不同容量配置，初始化会直接失败

### 6.5 `reset_session()` 语义

`reset_session(session_id)` 会：

- 清空消息历史
- 清空 `system_prompt`
- 保留原 `session_id`

若 session 不存在，则抛：

- `LLMRuntimeError("unknown session_id: ...")`

### 6.6 `delete_session()` 语义

`delete_session(session_id)` 会删除指定 session。

若该 session 不存在：

- 静默成功
- 不报错

当前明确保留 delete 的幂等语义。

## 7. `system_prompt` 限制

在同一个 session 内：

- 第一次 `chat()` 传入的 `system_prompt` 会写入 session
- 后续继续使用同一 session 时，再传新的 `system_prompt`，当前实现会忽略

因此：

- `system_prompt` 不是每轮都可动态覆盖
- 若需要替换 `system_prompt`，应先 `reset_session()` 或创建新 session

## 8. 历史窗口与存储限制

### 8.1 `history_window` 一值两用

当前 `history_window` 同时控制：

1. 发送给模型的上下文窗口
2. session store 中实际保留的最近历史长度

### 8.2 持久化历史裁剪

每次 `chat()` 成功后，session 中只保留最近 `2 * history_window` 条消息。

这意味着当前实现不会无限保留旧消息。

## 9. Session 数量淘汰限制

### 9.1 限制单位

当前限制的是：

- session 条数
- 不是 token 总量
- 不是字节数
- 不是 Python 对象真实内存占用

### 9.2 淘汰策略

当前淘汰依据为：

- `last_accessed_time`

也就是近似的最近最少访问语义。

### 9.3 哪些行为会刷新访问时间

以下操作会刷新 `last_accessed_time`：

- 读取已有 session
- 保存 session
- reset session

### 9.4 淘汰顺序

保存 session 后，当前实现会依次检查：

1. 当前 provider 是否超过 `A2AT_LLM_SESSION_MAX_PER_PROVIDER`
2. 全局是否超过 `A2AT_LLM_SESSION_MAX_TOTAL`

超限时，会淘汰对应范围中 `last_accessed_time` 最老的 session。

## 10. Provider 能力矩阵限制

### 10.1 `openai`

- 支持 `complete()`
- 支持 `chat()`
- 支持 `structured()`

限制：

- `complete()` / `chat()` 强制使用 JSON object 输出模式
- `structured()` 使用 `json_schema` 响应格式

### 10.2 `deepseek`

- 支持 `complete()`
- 支持 `chat()`
- 支持 `structured()`

限制：

- `complete()` / `chat()` 强制 JSON object 输出
- `structured()` 也强制 JSON object 输出
- `json_schema` 当前仅被转换为 prompt 级 system 指令

因此，DeepSeek 的 `structured()` 不是严格协议级 schema 约束。

### 10.3 `google`

- 支持 `complete()`
- 支持 `chat()`
- 支持 `structured()`

限制：

- 当前统一走 `generate_content`
- 输出 MIME 类型统一走 `application/json`
- `base_url` 当前不生效

### 10.4 `anthropic`

- 不支持 `complete()`
- 不支持 `chat()`
- 仅支持 `structured()`

限制：

- `structured()` 依赖 tool-use 返回
- 若响应中没有 `tool_use` block，会直接报错
- 若运行环境缺少 `anthropic` 包，adapter 初始化会失败

## 11. `structured()` 输入限制

当前 `structured()` 要求：

- `messages` 必须是 `list[dict[str, str]]`
- 每个元素应包含 `role` 和 `content`
- `json_schema` 必须是字典对象

当前没有额外的宽松兼容层，因此调用方应按预期结构提供数据。

## 12. SDK 依赖限制

不同 provider 依赖不同第三方 SDK：

- `openai` / `deepseek` 依赖 `openai`
- `google` 依赖 `google-genai`
- `anthropic` 依赖 `anthropic`

若运行环境缺少对应依赖，adapter 创建或导入会失败。
更精确地说，失败时机取决于 provider 实现，通常发生在导入对应 adapter 模块或创建对应 adapter 实例时。

## 13. 当前不承诺的能力

当前实现不承诺以下能力：

- 流式输出
- 工具调用统一抽象
- 多模态输入统一抽象
- 持久化 SessionStore
- OpenAI Responses API
- Anthropic 的完整 chat/complete 能力

## 14. 使用建议

当前推荐的使用方式是：

- 优先用 `LLMClient`，不要直接拼装 provider adapter，除非你明确需要底层测试或扩展
- 若你会在同一进程内创建多个 `LLMClient`，确保它们的 session 容量配置一致
- 若你依赖严格 schema 约束，优先使用 `openai` / `google` / `anthropic` 的 `structured()`
- 若你在一个 session 中需要更换 `system_prompt`，先 `reset_session()` 或新建 session
- 若你频繁切换 provider，不要尝试复用旧 provider 的 session id

## 15. 三条最重要的隐形规格

如果只记住三条，应记住：

1. 默认 session store 是进程级共享的，不是 `LLMClient` 实例私有的
2. Session 是 provider 绑定的，不能跨 provider 复用
3. 当前 `a2a_t.llm` 更偏向 JSON / structured 输出客户端，而不是通用自由文本聊天客户端

## 16. 相关文档

若需要继续深入：

- 当前架构与实现说明：`2026-04-16-a2a-t-llm-design.md`
- 历史演进与决策来源：`2026-04-16-a2a-t-llm-evolution.md`
