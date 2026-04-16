# `a2a_t.llm` SessionStore Provider 隔离与容量保护设计

## 1. 背景

当前 `a2a_t.llm` 已经具备以下能力：

- `LLMClient` 作为统一入口
- `LLMAdapter` 基类负责 `chat()` 会话编排
- `SessionStore` / `InMemorySessionStore` 负责会话状态保存

但现阶段 `SessionStore` 设计仍有两个明显问题：

### 1.1 不同 provider 的 session 没有隔离

当前 `session_id` 使用纯 UUID，不包含 provider 信息。  
`ChatSession` 也不记录 provider。  
`LLMClient` 会把同一个共享 `SessionStore` 注入不同 provider 的 adapter。

这意味着存在如下风险：

1. 用户先通过 `openai` 创建 session
2. 后续切换到 `deepseek`
3. 若继续传入旧 `session_id`，当前实现仍可能读到旧 session
4. 旧的 `openai` 历史会在 `deepseek` adapter 中继续被使用

这不符合预期，也会让会话语义与 provider 边界失真。

### 1.2 SessionStore 当前没有容量保护

当前 `InMemorySessionStore` 使用进程内字典保存所有 session，没有以下保护：

- session 总量上限
- 单 provider session 数量上限
- 单个 session 历史长度上限
- 配置误设过大时的硬保护

在极端情况下，这些问题都可能推高内存占用并触发 OOM 风险。

## 2. 目标

本次设计目标如下：

- 杜绝跨 provider 复用 session
- 保持 `SessionStore` 抽象尽量稳定，不扩大改动面
- 为内存中的 session 数量增加上限保护
- 为单个 session 历史长度增加上限保护
- 为关键配置增加代码内置硬上限，防止误配置导致过度占用内存
- 保持 `.env` 配置数量尽量少

## 3. 非目标

本次不解决以下问题：

- 持久化 `SessionStore`
- 精确按真实内存字节数限制 session 占用
- session TTL / 过期机制
- 会话查询接口，如 `list_sessions()` / `get_session_history()`
- 跨 provider session 迁移
- 切换 provider 时自动清理旧 provider 的全部 session

## 4. 设计原则

### 4.1 provider 隔离必须是硬约束

不能仅依赖调用方“正确传入 provider”。  
session 本身必须携带 provider 归属，并在加载时做强校验。

### 4.2 保护优先于便利

当检测到 provider 不匹配或配置越界时，应直接报错，而不是静默兼容或自动修正。

### 4.3 控制复杂度

不修改 `SessionStore` 协议签名，不把 provider 语义扩散到所有 store 接口中。  
优先通过包装层和元数据增强实现隔离。

### 4.4 容量控制采用近似而稳定的代理指标

本期不做字节级精确内存预算。  
先通过以下代理指标建立保护：

- session 数量
- 单 session 历史轮数

## 5. 总体方案

### 5.1 推荐方案

采用以下组合设计：

- `session_id` 带 provider 前缀
- `ChatSession` 显式记录 `provider`
- 新增 `ProviderScopedSessionStore`
- `LLMClient` 为当前 provider 创建并注入 scoped store
- 读取 session 时校验 `session.provider == current_adapter.adapter_type`
- 超出容量上限时按最近最少访问语义淘汰旧 session
- 复用 `A2AT_LLM_HISTORY_WINDOW` 控制单 session 最大保留历史轮数

### 5.2 不采用的方案

#### 方案 A：修改 `SessionStore` 协议，所有接口显式带 provider

不采用。  
这会把 provider 语义污染到整个 store 抽象层，也会扩大未来持久化 store 的实现负担。

#### 方案 B：按 adapter 实例维护独立 SessionStore

不采用。  
当前 adapter 是按调用临时创建的，若把 store 绑定到 adapter 实例，会直接破坏多轮对话能力。

#### 方案 C：切换 provider 时自动清除旧 provider session

不采用。  
这会引入强副作用，也不适合共享 store 或多调用方并存的场景。

## 6. provider 隔离设计

### 6.1 `session_id` 编码规则

`session_id` 从纯 UUID 调整为：

```text
<provider>-<uuid>
```

例如：

- `openai-550e8400-e29b-41d4-a716-446655440000`
- `deepseek-550e8400-e29b-41d4-a716-446655440000`

作用：

- 提高排障可读性
- 为 store 过滤与快速校验提供辅助信息

### 6.2 `ChatSession` 元数据

`ChatSession` 新增：

- `provider: str`
- `last_accessed_time`

同时将当前 `updated_at` 重命名为 `last_accessed_time`。

原因：

- 该时间不仅会在写入时更新
- 在读取已有 session 时也会更新
- 它表达的是“最近被访问/使用的时间”，而不只是“被修改的时间”

### 6.3 `ProviderScopedSessionStore`

保持 `SessionStore` 协议不变，在其外层新增一个 provider 作用域包装：

```python
class ProviderScopedSessionStore:
    def __init__(self, provider: str, root_store: SessionStore) -> None:
        ...
```

职责：

- 仅允许访问当前 provider 对应的 session
- 对外继续暴露：
  - `get(session_id)`
  - `save(session)`
  - `reset(session_id)`
  - `delete(session_id)`

核心行为：

- `save()` 时校验：
  - `session.provider` 必须与当前 scoped provider 一致
  - `session_id` 前缀必须与当前 provider 一致
- `get()/reset()/delete()` 时：
  - 先校验 `session_id` 前缀是否属于当前 provider
  - 不属于当前 provider 时直接返回 `None` 或按当前接口语义视为不存在

这样可以让不同 provider 在正常路径下天然看不到彼此的 session。

### 6.4 最终一致性校验

除了 scoped store 隔离外，在 adapter 层加载已有 session 后，仍需额外校验：

```python
session.provider == self.adapter_type
```

若不匹配，直接抛 `LLMRuntimeError`。

这层校验是防御性保护，用于避免未来出现：

- store wrapper 被绕过
- session 被错误注入
- 其它实现缺陷造成的 provider 串用

### 6.5 provider 切换时的行为

当用户切换默认 provider 或方法级覆盖 provider 后：

- 不自动清理旧 provider session
- 不自动迁移旧 session
- 旧 provider 的 session 在新 provider 下不可继续使用

如果继续传入旧 session id：

- 正常路径下应查不到该 session
- 或在最终校验时触发 provider mismatch 错误

## 7. SessionStore 容量控制

### 7.1 `.env` 配置项

新增：

```dotenv
A2AT_LLM_SESSION_MAX_TOTAL=300
A2AT_LLM_SESSION_MAX_PER_PROVIDER=100
```

### 7.2 配置语义

#### `A2AT_LLM_SESSION_MAX_TOTAL`

表示整个进程内、所有 provider 加总后，最多允许保留多少个 session。

这个值的单位是：

- session 数量

不是：

- 消息条数
- token 数量
- 字节数

#### `A2AT_LLM_SESSION_MAX_PER_PROVIDER`

表示单个 provider 最多允许保留多少个 session。

例如：

- `openai` 最多 100 个 session
- `deepseek` 最多 100 个 session
- 各 provider 分别独立计算

### 7.3 两类上限同时生效

这两个配置不是二选一，而是同时生效：

- 不能超过单 provider 上限
- 也不能超过全局总上限

例如：

```dotenv
A2AT_LLM_SESSION_MAX_TOTAL=300
A2AT_LLM_SESSION_MAX_PER_PROVIDER=100
```

则：

- `openai=100, deepseek=100, google=50, anthropic=50` 是允许的
- 若再创建一个 `anthropic` session，虽然 anthropic 自身仅为 51、未超 100，但全局会超 300，因此仍需触发淘汰

### 7.4 淘汰策略

采用基于 `last_accessed_time` 的最近最少访问淘汰语义。

规则如下：

1. 当当前 provider session 数量超过 `A2AT_LLM_SESSION_MAX_PER_PROVIDER` 时
   - 优先在当前 provider 内淘汰最久未访问的 session
2. 当总 session 数量超过 `A2AT_LLM_SESSION_MAX_TOTAL` 时
   - 在全局范围淘汰最久未访问的 session

本期默认行为是：

- 自动淘汰旧 session
- 不因达到上限而拒绝创建新 session

## 8. 单 session 历史长度控制

### 8.1 复用 `A2AT_LLM_HISTORY_WINDOW`

不新增新的 turns 配置，直接复用现有：

```dotenv
A2AT_LLM_HISTORY_WINDOW=10
```

### 8.2 语义升级

当前 `A2AT_LLM_HISTORY_WINDOW` 仅用于控制“发送给模型的最近 N 轮上下文”。  
本次设计将其语义升级为：

- 每次发送给模型时，只使用最近 N 轮完整历史
- `SessionStore` 中也只保留最近 N 轮完整历史

这里的 N 表示：

- 最近 N 轮 user/assistant 往返

`system_prompt` 不计入该窗口。

### 8.3 裁剪规则

每轮对话完成后：

1. 追加 assistant 响应到 session
2. 将 `session.messages` 裁剪到最近 N 轮
3. 再保存回 store

这样可以避免以下问题：

- 虽然请求只发最近 N 轮，但 store 中的历史仍然无限增长

## 9. 时间字段设计

### 9.1 字段命名

`ChatSession.updated_at` 调整为：

- `last_accessed_time`

### 9.2 更新时间

以下场景都要更新 `last_accessed_time`：

- 创建新 session
- 读取已有 session
- 重置 session
- 追加新消息并保存 session

这样该字段才能真实反映 session 活跃度，并服务于淘汰策略。

## 10. 配置安全保护

### 10.1 需要保护的配置项

以下配置都必须增加代码内置硬上限：

- `A2AT_LLM_HISTORY_WINDOW`
- `A2AT_LLM_SESSION_MAX_TOTAL`
- `A2AT_LLM_SESSION_MAX_PER_PROVIDER`

### 10.2 保护策略

对以上配置执行以下校验：

- 必须是正整数
- 不得超过代码内置硬上限
- 若超过，直接抛 `LLMConfigError`

不采用自动截断。  
原因是静默修正会让系统行为不透明，且不利于定位配置错误。

### 10.3 配置间关系校验

额外约束：

- `A2AT_LLM_SESSION_MAX_TOTAL >= A2AT_LLM_SESSION_MAX_PER_PROVIDER`

否则配置无意义，应在初始化阶段直接报错。

### 10.4 硬上限的位置

这些最大值应写死在代码里，作为框架级保护值，而不是继续开放给 `.env` 配置。  
这样可以防止“保护参数本身也被误配置得过大”。

## 11. 运行时行为示例

### 11.1 同 provider 正常续聊

1. 用户通过 `openai` 创建 session
2. 获得 `session_id = openai-...`
3. 再次使用 `openai` 调用 `chat(session_id=...)`
4. 正常加载并继续会话

### 11.2 切换 provider 后继续使用旧 session

1. 用户先通过 `openai` 创建 session
2. 后续切换默认 provider 为 `deepseek`
3. 继续传入旧的 `openai-...` session id

预期结果：

- `deepseek` 的 scoped store 不应找到该 session
- 若异常情况下找到了，也必须因为 `session.provider != "deepseek"` 而直接报错

### 11.3 容量淘汰

假设：

```dotenv
A2AT_LLM_SESSION_MAX_TOTAL=300
A2AT_LLM_SESSION_MAX_PER_PROVIDER=100
```

若当前：

- `openai=100`
- `deepseek=100`
- `google=50`
- `anthropic=50`

此时再创建一个 `anthropic` session：

- anthropic provider 范围内仍未超 100
- 但全局已达到 301
- 系统应淘汰全局范围内 `last_accessed_time` 最老的 session

## 12. 需要修改的对象

建议修改或新增以下文件：

- `src/a2a_t/llm/base.py`
- `src/a2a_t/llm/client.py`
- `src/a2a_t/llm/session_store.py`
- `tests/test_llm/test_base_chat_flow.py`
- `tests/test_llm/test_client.py`

视实现方式可选修改：

- `package_data/.env`
- `package_data/env.example`

## 13. 测试策略

### 13.1 provider 隔离测试

- 新建 session 时 `session_id` 带 provider 前缀
- `ChatSession.provider` 被正确保存
- `openai` session 不能被 `deepseek` 读取
- provider mismatch 会抛 `LLMRuntimeError`

### 13.2 容量控制测试

- 超过 `A2AT_LLM_SESSION_MAX_PER_PROVIDER` 时，会淘汰该 provider 下最旧 session
- 超过 `A2AT_LLM_SESSION_MAX_TOTAL` 时，会淘汰全局最旧 session
- 淘汰依据为 `last_accessed_time`

### 13.3 历史裁剪测试

- `A2AT_LLM_HISTORY_WINDOW` 同时作用于：
  - 发给模型的消息窗口
  - store 中保留的历史窗口
- store 中不会无限积累旧消息

### 13.4 配置保护测试

- 三个配置为非正整数时报错
- 三个配置超过代码硬上限时报错
- `A2AT_LLM_SESSION_MAX_TOTAL < A2AT_LLM_SESSION_MAX_PER_PROVIDER` 时报错

## 14. 风险与取舍

### 14.1 不做精确内存限制

本设计控制的是：

- session 数量
- 单 session 历史轮数

而不是 Python 进程中的真实字节占用。  
这是有意接受的取舍，用更简单、更稳定的规则换取足够强的风险收敛。

### 14.2 `last_accessed_time` 需要显式维护

由于访问 session 也会影响淘汰顺序，读取路径必须能更新访问时间。  
实现时需要仔细处理“读取即活跃”的语义，避免出现只在写入时更新时间的偏差。

### 14.3 配置语义更强

将 `A2AT_LLM_HISTORY_WINDOW` 同时用于“请求窗口”和“存储窗口”，会把两者绑定。  
这是为了减少配置项数量的有意取舍。  
若未来业务需要两者解耦，可再单独拆出存储窗口配置。

## 15. 结论

本轮 `SessionStore` 的最佳演进方向是：

- 通过 `ProviderScopedSessionStore` 实现 provider 级作用域隔离
- 用 `session_id` 前缀和 `ChatSession.provider` 建立双重防护
- 用 `last_accessed_time` 驱动基于活跃度的淘汰
- 用 `.env` 中的 total/per-provider 配置控制 session 数量
- 复用 `A2AT_LLM_HISTORY_WINDOW` 控制单 session 历史长度
- 用代码内置硬上限防止误配置导致 OOM 风险

这样可以在保持当前抽象相对稳定的前提下，把 `a2a_t.llm` 的会话管理从“功能可用”推进到“具备隔离性与安全边界”的状态。

## 16. Implementation Follow-Up

后续实现已进一步收敛默认行为：

- 默认 root session store 的作用域从 `LLMClient` 实例级提升为进程级共享
- `session_store` 不再作为 `LLMClient` 对外公开注入点
- `reset_session()` / `delete_session()` 收敛为纯内部 session 管理接口
