# `a2a_t.llm` 演进文档

## 1. 文档目的

本文档以时间线为主线、以特性为副线，总结 `a2a_t.llm` 模块截至 2026-04-16 的已知演进历史。

本文档强调三类信息：

1. 哪些设计已经进入当前实现
2. 哪些文档只代表分析或讨论，并未成为当前代码事实
3. 每次演进实际改变了什么能力边界

建议配套阅读：

- `2026-04-16-a2a-t-llm-design.md`
- `2026-04-16-a2a-t-llm-usage-constraints.md`

## 2. 演进总览

`a2a_t.llm` 的演进可分为四个主阶段：

1. 2026-04-11：建立通用 LLM 网关一期基线
2. 2026-04-12：引入 `LLMClient` 并从 transport 占位实现切换到官方 SDK
3. 2026-04-14：补齐 SessionStore 的 provider 隔离、容量保护与历史裁剪
4. 2026-04-15：将默认 session store 收敛为进程级共享，并收紧 `LLMClient` 的公开接口

2026-04-16 的工作主要是文档归纳，不再新增新的实现能力。

## 3. 时间线

### 3.1 2026-04-11：LLM 网关一期基线

相关文档：

- `docs/superpowers/specs/llm/2026-04-11-llm-gateway-phase1-design.md`
- `docs/superpowers/plans/2026-04-11-llm-gateway-phase1.md`

这一天确立了最早的统一抽象边界：

- 引入 `LLMAdapter` 基类
- 定义 `ChatMessage` / `ChatSession` / `LLMResponse`
- 定义 `SessionStore` 协议和默认内存实现
- 确立 `complete()` / `chat()` / `structured()` 的统一外观
- 建立 provider adapter 的工厂化装配路径
- 确立 `system_prompt`、`history_window`、session 生命周期的基础语义

这一阶段的核心价值是：把 LLM 能力从“散落的 provider 细节”变成“统一抽象 + provider 适配”的架构。

### 3.2 2026-04-12：`LLMClient` 与官方 SDK 化

相关文档：

- `docs/superpowers/specs/llm/2026-04-12-llm-client-design.md`
- `docs/superpowers/plans/2026-04-12-llm-client-implementation.md`

这一阶段完成了两件关键事情：

1. 引入 `LLMClient` 作为易用入口
2. 去掉 `transport` 占位设计，改为 provider adapter 直接对接官方 SDK

落地变化包括：

- `LLMClient` 从 `.env` 读取默认 provider/model/api_key 等配置
- `LLMClient` 统一创建 adapter 并分发调用
- `OpenAIAdapter` 改为使用官方 OpenAI SDK
- `DeepSeekAdapter` 改为使用 OpenAI 兼容路径访问 DeepSeek
- `GoogleAdapter` 改为使用 `google-genai`
- `AnthropicAdapter` 改为使用 `anthropic`，但只保留 `structured()`

这一步使 `a2a_t.llm` 从“抽象设计成立”推进到“具备真实 SDK 调用能力”。

### 3.3 2026-04-12：DeepSeek JSON Mode 收敛

相关文档：

- `docs/superpowers/specs/llm/2026-04-12-llm-deepseek-json-mode-refactor-design.md`

这份文档对应的核心思想已经部分进入当前实现：

- DeepSeek 独立 adapter 化
- `complete()` / `chat()` / `structured()` 全部强制 JSON object 输出
- `structured()` 通过 prompt 注入 schema，而不是协议级 schema

它解决的是 DeepSeek 与 OpenAI 虽然同属兼容接口，但结构化语义强度不同的问题。

当前状态可以认为是：

- 文档中的主设计思想已落地
- 其结论已经被吸收入当前 `DeepSeekAdapter` 的事实行为

### 3.4 2026-04-12：OpenAI Responses API 迁移分析

相关文档：

- `docs/superpowers/specs/llm/2026-04-12-llm-openai-responses-api-migration-discuss.md`

这不是当前实现文档，而是一个讨论文档。

它分析了是否要把 `OpenAIAdapter` 从 chat completions 迁移到 Responses API，但当前结论是：

- 没有迁移
- 当前仍以 `chat.completions.create` 为实现基础

因此这份文档在历史中的地位是：

- 提供过技术分析
- 没有改变当前代码事实

### 3.5 2026-04-14：SessionStore 安全收敛

相关文档：

- `docs/superpowers/specs/llm/2026-04-14-llm-session-store-safety-design.md`
- `docs/superpowers/plans/2026-04-14-llm-session-store-safety-implementation.md`

这是 LLM 模块第二次重要收敛。

落地变化包括：

- session id 统一收敛为 `<provider>-<uuid>`
- 引入 `ProviderScopedSessionStore`
- 增加 `ChatSession.provider` 约束
- 增加 provider 前缀与 provider 元数据双重校验
- 引入 `A2AT_LLM_SESSION_MAX_TOTAL`
- 引入 `A2AT_LLM_SESSION_MAX_PER_PROVIDER`
- 引入按 `last_accessed_time` 的近似 LRU 淘汰
- 将 `history_window` 从“仅请求窗口”升级为“请求窗口 + 存储窗口”
- 移除 `updated_at`，统一使用 `last_accessed_time`

这一阶段的关键贡献是：让 session 从“能用”变成“有边界、有隔离、有容量保护”。

### 3.6 2026-04-15：全局共享 SessionStore 与 `LLMClient` API 收紧

相关文档：

- `docs/superpowers/specs/llm/2026-04-15-llm-client-global-session-store-design.md`
- `docs/superpowers/plans/2026-04-15-llm-client-global-session-store-implementation.md`
- `docs/superpowers/specs/llm/2026-04-15-llm-client-usage-constraints.md`

这是 `LLMClient` 层面的第三次关键收敛。

落地变化包括：

- 默认 root session store 从 `LLMClient` 实例级提升为进程级共享
- 增加“首次初始化锁定”规则
- 当 session store 容量配置冲突时，后续 `LLMClient` 初始化直接失败
- 取消 `LLMClient.__init__()` 的公开 `session_store` 注入能力
- `chat()` / `complete()` / `structured()` 改为显式 runtime override 参数
- 公开 `**kwargs` 被收回
- `history_window` 只保留在 `chat()`
- `reset_session()` / `delete_session()` 不再走 adapter 创建路径，而是直接对 root store 操作

这一阶段的关键结果是：

- session 管理被定义为内部能力，而不是外部自由拼装能力
- `LLMClient` 的公开接口与真实行为终于对齐

### 3.7 2026-04-16：文档归纳

相关文档：

- `docs/superpowers/specs/2026-04-16-a2a-t-llm-design.md`
- `docs/superpowers/specs/2026-04-16-a2a-t-llm-evolution.md`
- `docs/superpowers/specs/2026-04-16-a2a-t-llm-usage-constraints.md`

这一步不新增能力，主要目的有两个：

1. 把多份中间设计和计划文档收敛成更适合当前维护的稳定文档
2. 将“历史讨论”和“当前实现事实”明确分离

## 4. 按特性视角看演进

### 4.1 架构抽象

演进路径：

- 2026-04-11：建立 `LLMAdapter` / `SessionStore` / factory 基线
- 2026-04-12：加入 `LLMClient` 作为推荐公开入口

当前状态：

- 统一公开入口是 `LLMClient`
- 真正 provider 差异仍封装在 adapter 中

### 4.2 Provider SDK 接入

演进路径：

- 2026-04-11：通用适配层设计成立
- 2026-04-12：切换到官方 SDK 实现

当前状态：

- OpenAI：官方 SDK
- DeepSeek：OpenAI 兼容 SDK 路径
- Google：`google-genai`
- Anthropic：`anthropic`，仅 `structured()`

### 4.3 Session 管理

演进路径：

- 2026-04-11：建立基础 session 语义
- 2026-04-14：增加 provider 隔离、容量控制、历史裁剪
- 2026-04-15：默认 root store 提升为进程级共享

当前状态：

- root store 是进程级共享
- provider 作用域通过 wrapper 强约束
- session 只存在于 `chat()`

### 4.4 `LLMClient` 外部接口

演进路径：

- 2026-04-12：先建立统一 client 入口
- 2026-04-15：收紧签名和内部化 session 管理

当前状态：

- 推理方法使用显式参数
- session 管理方法只接收 `session_id`
- 不再接受公开 `**kwargs`

### 4.5 结构化输出

演进路径：

- 2026-04-11：统一 `structured()` 抽象
- 2026-04-12：各 provider 按自身能力走不同实现

当前状态：

- OpenAI：协议级 `json_schema`
- Google：`response_json_schema`
- Anthropic：tool-use
- DeepSeek：prompt 级 schema 注入

这也是当前 provider 能力差异最明显的一条主线。

## 5. 历史文档的当前定位

### 5.1 已被当前实现吸收的文档

以下文档的主体设计方向已经被当前实现吸收，但细节仍应以当前代码和 2026-04-16 的汇总文档为准：

- `2026-04-11-llm-gateway-phase1-design.md`
- `2026-04-12-llm-client-design.md`
- `2026-04-12-llm-deepseek-json-mode-refactor-design.md`
- `2026-04-14-llm-session-store-safety-design.md`
- `2026-04-15-llm-client-global-session-store-design.md`

对应实施计划文档也大体已经完成，并在当前代码中留下了可验证痕迹。

### 5.2 讨论或背景材料

以下文档不应被当作“当前实现规格”：

- `2026-04-12-llm-openai-responses-api-migration-discuss.md`
  - 属于讨论，当前未采纳
- `llm-api-insights.md`
  - 属于背景分析和未来启发，不是当前实现承诺

### 5.3 现状规格文档

截至 2026-04-16，推荐优先阅读的稳定文档应是：

- `2026-04-16-a2a-t-llm-design.md`
- `2026-04-16-a2a-t-llm-evolution.md`
- `2026-04-16-a2a-t-llm-usage-constraints.md`

## 6. 当前演进的总体结论

`a2a_t.llm` 的演进不是简单功能堆叠，而是三次连续收敛：

1. 先建立统一抽象
2. 再换成真实 SDK 和统一 client
3. 再收紧 session 与接口边界

所以当前模块的真实定位不是“无限扩展的通用 LLM 平台”，而是：

- 有明确 provider 差异边界
- 偏向 JSON / structured 输出
- 会话能力受控
- 对外公开接口已经趋于稳定

这是截至当前最严谨、最贴近代码事实的历史结论。

## 7. 相关文档

若需要继续深入：

- 当前架构、对象模型、4+1 视图：`2026-04-16-a2a-t-llm-design.md`
- 当前 `LLMClient` 使用边界与限制：`2026-04-16-a2a-t-llm-usage-constraints.md`
- 历史设计原稿与实施计划：`docs/superpowers/specs/llm/*`、`docs/superpowers/plans/*`
