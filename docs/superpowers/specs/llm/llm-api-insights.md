# `a2a_t.llm` 厂商 API 洞察报告

## 1. 文档目的

本文基于 OpenAI、Google Gemini、Anthropic Claude、DeepSeek 的官方 API 文档整理，目标不是罗列接口细节，而是为 `a2a_t.llm` 的后续重构提供一份统一的认知底座。重点关注以下问题：

- 四家 LLM 厂商的 API 设计哲学分别是什么
- 在结构化输出、工具调用、文件处理、检索增强、实时/异步能力上各自如何建模
- 如果要在 `a2a_t.llm` 中做统一适配，抽象边界应该落在哪里
- 当前项目里 OpenAI / Google / Anthropic 三个 adapter 的实现思路，分别对应厂商原生能力中的哪一部分
- 如果后续引入 DeepSeek 适配，应该把它视为哪一种能力映射路径

## 2. 执行摘要

当前 `a2a_t.llm` 更像一个“面向结构化抽取的小型适配层”，而不是完整的通用 LLM 网关。它已经覆盖了三家厂商在“按 schema 返回结构化结果”这一场景下的最小公共能力，但还没有覆盖更完整的聊天、多模态、工具调用、文件输入、流式输出、异步任务等能力。把 DeepSeek 一并纳入视野后，可以更清楚地看到：即便接口表面兼容，厂商在“结构化结果的保证方式”上依然存在显著差异。

四家厂商的核心差异可以概括为：

- OpenAI：接口收敛度高，正在向统一响应对象靠拢，适合做“通用能力入口”。
- Google Gemini：多模态与上下文增强能力很强，文件、缓存、搜索、URL 上下文等能力组合度高。
- Anthropic：消息与工具语义非常清晰，尤其强调 tool use、stop reason、代理式交互与可控性。
- DeepSeek：接口形态与 OpenAI 高度兼容，上手与迁移成本低，推理模型与缓存成本优化突出，但结构化输出更接近 JSON mode / strict tool schema，而不是 OpenAI 那种更强的原生响应 schema 语义。

如果 `a2a_t.llm` 后续要从“结构化抽取层”扩展为“通用模型适配层”，建议按 capability 而不是按单一 `LLMAdapter` 大接口去设计。否则 OpenAI、Gemini、Claude、DeepSeek 在语义上本就不同的能力，会被强行压扁成一组过于抽象、但又不够真实的统一方法。

## 3. 当前项目上下文

从现有代码看，`src/a2a_t/llm` 的设计意图是给上层业务提供一个统一的模型访问面：

- [`src/a2a_t/llm/base.py`](C:\Users\y00642297\MyWork\a2a-t-sdk\src\a2a_t\llm\base.py) 定义了 `complete()`、`chat()`、`structured()` 三类能力。
- [`src/a2a_t/llm/factory.py`](C:\Users\y00642297\MyWork\a2a-t-sdk\src\a2a_t\llm\factory.py) 负责根据配置实例化 adapter。
- OpenAI、Google、Anthropic 三个 adapter 目前真正实现的是 `structured()`。
- 当前最明确的业务落点在 [`src/a2a_t/server/prompt_compliance/extractor.py`](C:\Users\y00642297\MyWork\a2a-t-sdk\src\a2a_t\server\prompt_compliance\extractor.py)，用于做 prompt slot extraction。

这意味着当前代码已经验证了一个很实用的方向：把“结构化抽取”作为独立能力做供应商适配。但如果继续沿着同一套抽象直接扩展到更广泛的 LLM 场景，会逐步暴露出能力模型不对齐的问题。

## 4. 四家厂商的 API 设计心智模型

### 4.1 OpenAI

OpenAI 的整体方向是把多种交互能力收敛到统一响应体系中。它的官方文档强调：

- 统一的响应对象与输出项组织方式
- 结构化输出的原生支持
- 工具/函数调用能力
- 文件输入、会话状态、后台任务、批处理、实时交互等扩展能力

对适配层而言，OpenAI 的优势是“能力面比较全，而且 API 组织越来越统一”。这意味着：

- 它适合成为统一抽象设计时的一个重要参考样本
- 但也不能把 OpenAI 的接口形态直接等同于行业标准

在当前项目中，OpenAI adapter 的实现本质上是利用官方提供的 JSON Schema 约束能力，把上层的 schema 要求直接下传给模型侧，然后将返回结果作为结构化 JSON 使用。

### 4.2 Google Gemini

Gemini API 的组织方式更强调“内容块”和“多模态输入部件”的组合。它的官方能力版图里，除了模型调用本身，还比较突出：

- 原生结构化输出
- function calling
- Files API
- context caching
- Live API
- URL Context
- Google Search
- File Search
- Batch API

Gemini 的一个鲜明特点是：模型能力、上下文增强能力、文件处理能力之间结合得很紧。这对适配层设计的启发是：

- Gemini 不只是“一个生成模型接口”
- 它更像“模型 + 上下文基础设施 + 检索增强能力”的组合系统

在当前项目中，Google adapter 的实现主要使用了 Gemini 的 schema 约束输出能力，把 `structured()` 映射为带 `response_json_schema` 的调用。

### 4.3 Anthropic Claude

Anthropic 的 API 风格相对克制，但语义边界很清晰。官方文档里最重要的几个关键词是：

- Messages API
- content blocks
- tool use
- stop reasons
- prompt caching
- PDF / files

Anthropic 在结构化输出上的典型思路，并不是单纯强调“让模型直接吐一个严格 JSON”，而是更偏向通过工具调用机制，让模型把结构化内容作为 tool input 提交出来。这种方式在 agent 型交互里非常自然，因为：

- 结构化结果和工具调用本来就是同一种语义层级
- 模型何时停下、为何停下，会通过 stop reason 清楚表达
- 对“先思考、后调用工具、再继续”的多轮过程更友好

在当前项目中，Anthropic adapter 正是基于这一路径：将 schema 封装为 tool 的 `input_schema`，然后从返回结果中提取 tool input，再序列化为 JSON 交回上层。

### 4.4 DeepSeek

DeepSeek 的官方 API 文档显示，它在接口层首先强调的是“OpenAI 兼容”。官方 Quick Start 明确说明可以直接使用 OpenAI SDK，只需把 `base_url` 指向 `https://api.deepseek.com`。这使它在工程接入上非常直接，尤其适合作为：

- 已有 OpenAI 调用链的低成本兼容接入对象
- 需要推理模型与普通聊天模型并存的接入对象
- 对缓存成本敏感、且有长前缀复用场景的接入对象

从官方文档可以归纳出 DeepSeek 当前公开 API 的几个关键点：

- 主要模型入口是 `deepseek-chat` 与 `deepseek-reasoner`
- 官方文档当前说明二者对应 DeepSeek-V3.2，且上下文窗口为 128K
- 支持流式输出
- 支持 JSON Output
- 支持 Function Calling
- 支持 `strict` mode 的工具 schema 校验，但需使用 `https://api.deepseek.com/beta`
- Context Caching on Disk 默认开启
- 额外提供 Anthropic API 兼容入口，便于接入 Anthropic/Claude 生态工具链

需要特别注意的是，DeepSeek 在“结构化输出”上的公开能力语义，与 OpenAI / Gemini 的原生 response schema 并不完全相同。它当前公开文档里最直接的结构化输出路径是：

- `response_format = {"type": "json_object"}` 的 JSON Output
- 在 prompt 中显式要求输出 JSON，并给出示例
- 当结构约束更严格时，借助 Function Calling 的 `strict` mode

这意味着如果未来引入 DeepSeek adapter，更合理的适配判断应当是：

- 在聊天接口层，可优先视为 OpenAI-compatible provider
- 在结构化抽取层，应把它视为“JSON mode + strict tool schema”能力组合
- 不宜直接假设它等价于 OpenAI 的强 schema constrained response 模式

另外，DeepSeek 官方文档中还体现出一个很有工程价值的特征：缓存机制默认开启，并在 `usage` 中返回 `prompt_cache_hit_tokens` 与 `prompt_cache_miss_tokens`。这对长前缀、多轮对话、few-shot 模板型调用场景非常有意义。

## 5. 关键能力对比

| 维度 | OpenAI | Google Gemini | Anthropic Claude | DeepSeek |
| --- | --- | --- | --- | --- |
| 核心接口风格 | 倾向统一响应模型 | 以 contents / parts 组织多模态内容 | 以 messages / content blocks / tool use 组织 | OpenAI-compatible `chat/completions` 为主，并提供 Anthropic 兼容入口 |
| 结构化输出 | 原生 schema 约束输出 | 原生 schema 约束输出 | 更自然地通过 tool use + input schema 实现 | JSON Output 保证合法 JSON 字符串；更强约束依赖 strict tool schema |
| 工具调用 | 明确支持 function/tool calling | 明确支持 function calling | 工具语义最强，tool use 是核心交互模型之一 | 支持 function calling，`strict` mode 可做服务端 schema 校验 |
| 多模态能力 | 能力完整，接口逐步统一 | 非常强，天然多模态导向 | 支持文档和多类型输入，但设计重心更偏 agent/tool | 公开 API 文档当前更突出文本、推理、工具与补全能力 |
| 文件处理 | 支持文件输入与相关工作流 | Files API 较完整，适合长上下文素材管理 | PDF / files 支持清晰，适合文档理解 | 公开 API 文档中未形成与 Gemini/OpenAI 对等的独立文件能力版图 |
| 检索增强 | 能配合内建工具与外部能力扩展 | URL Context、Google Search、File Search 较突出 | 倾向与 tool use / 外部系统协作 | 更偏向通过外部工具链集成，公开文档未突出内建检索能力 |
| 实时/流式/异步 | 支持流式、后台任务、批处理、实时 | 支持 Live API、Batch API、缓存 | 支持流式与工具驱动交互，强调 stop reason | 支持流式；另有 Chat Prefix Completion、FIM Completion、reasoning 模型 |
| 缓存机制 | 支持 prompt caching | 支持 context caching | 支持 prompt caching | Context Caching on Disk 默认开启，并返回 cache hit/miss usage 字段 |
| 统一抽象难度 | 中等 | 偏高，因能力组合面广 | 偏高，因 tool 语义非常核心 | 中等偏高，表面兼容 OpenAI，但结构化与能力版图不能直接等价 |

## 6. 对“结构化输出”能力的特别观察

如果只看当前 `a2a_t.llm` 已经覆盖的能力，也就是 `structured()`，四家的差异非常关键：

### 6.1 OpenAI 与 Google 的共同点

二者都更接近“原生受约束 JSON 输出”模式：

- 上层提供 schema
- 模型在生成阶段尽量受 schema 约束
- 返回值可被直接视为结构化对象或结构化字符串

这类模式适合：

- 信息抽取
- 分类
- 固定字段归一化
- 表单填充
- 低交互成本的结构化返回

### 6.2 DeepSeek 的位置

DeepSeek 更适合被放在 OpenAI / Google 与 Anthropic 之间理解：

- 它在接口形态上接近 OpenAI-compatible chat completions
- 它支持 JSON Output，可满足一部分结构化返回需求
- 但它公开文档中的结构化保障，更接近“合法 JSON 字符串输出”
- 当需要更严格字段约束时，实际更应依赖 strict function calling schema

因此，DeepSeek 的 `structured()` 设计不宜简单复用 OpenAI adapter 的语义预期。更稳妥的做法是区分两类实现策略：

- `JSON_MODE`：用于尽量返回可解析 JSON
- `STRICT_TOOL_SCHEMA`：用于需要更强字段约束的场景

### 6.3 Anthropic 的不同点

Anthropic 在实践上更适合被视为“通过工具调用返回结构化结果”：

- schema 不只是约束输出格式
- schema 更像一个工具入参契约
- 返回结构化结果时，模型是在“调用一个定义好的工具”

这类模式的优点是语义更真实：

- 结构化结果天然可进入 tool-call 生命周期
- 更容易扩展到 agent 工作流
- 上层可以更明确地区分“普通文本回答”与“结构化动作产出”

### 6.4 对统一适配层的直接影响

因此，未来不要把 `structured()` 简单理解成“返回一个 JSON 字符串”。更合理的理解应该是：

- 对外暴露统一的“结构化结果能力”
- 对内允许存在不同的供应商实现策略
- 原生 schema 输出是一种策略
- JSON mode 输出是一种策略
- 基于 tool use 的结构化产出是另一种策略

也就是说，统一的是能力契约，不应该强迫统一底层机制。

## 7. 对“消息模型”的特别观察

当前 `a2a_t.llm` 把 `messages` 近似建模为 `list[dict[str, str]]`。这个抽象在纯文本问答时够用，但对三家厂商的真实 API 来说偏窄。

更真实的消息模型应该至少支持：

- `role`
- 多段内容块，而不是单一字符串
- 文本块
- 图片块 / 文件块 / PDF 引用
- 工具调用块
- 工具结果块
- 厂商返回的 finish / stop / usage 元数据

原因很直接：

- Gemini 的 `parts` 天然不是单字符串
- Anthropic 的 `content blocks` 与 `tool_use` 本来就是分块结构
- OpenAI 也在向更丰富的输入/输出项结构靠拢
- DeepSeek 虽然在主接口上兼容 OpenAI，但其 Anthropic 兼容入口与 tool calling 也说明未来消息层不能只按纯文本字符串建模

因此，后续如果要扩展聊天、多模态、工具调用，消息层一定要先升级，否则 adapter 会被迫在边界处做大量不透明的字符串拼装。

## 8. 对 `a2a_t.llm` 重构的主要启发

### 8.1 按 capability 拆分，而不是继续扩张单一 adapter 接口

建议把未来能力拆成若干明确接口，例如：

- Text / Chat generation
- Structured output
- Tool calling
- File-aware input
- Streaming
- Async / batch execution

这样做的价值是：

- 能避免某些厂商“名义支持、实际语义不同”的能力被硬塞进统一方法
- 让每个 adapter 只实现自己真正具备的 capability
- 让上层按需依赖，而不是默认拿到一个过大的万能接口

### 8.2 定义强类型请求/响应模型

当前 `transport` 更像“隐式的 dict in / dict out callable”。这在实验期可接受，但在扩展阶段会成为维护成本来源。

建议补齐：

- `LLMMessage`
- `ContentBlock`
- `StructuredRequest`
- `StructuredResult[T]`
- `ToolDefinition`
- `ToolCall`
- `ToolResult`
- `Usage`
- `FinishReason`

这样可以显著降低不同 adapter 在边界处各自做私有协议转换的风险。

### 8.3 明确“供应商特定策略”是设计的一部分

统一抽象不应追求“所有厂商都长得一样”，而应明确保留供应商差异：

- OpenAI / Google 可走 native schema strategy
- DeepSeek 可走 json-mode 或 strict-tool strategy
- Anthropic 可走 tool-backed structured strategy

未来甚至可以显式建模为：

- `StructuredMode.NATIVE_JSON`
- `StructuredMode.JSON_MODE`
- `StructuredMode.TOOL_CALL`
- `StructuredMode.BEST_EFFORT_JSON`

这会比“所有厂商都返回 `response.content` 的 JSON 字符串”更清晰。

### 8.4 把工具调用从“特殊情况”升级为“一等能力”

Anthropic 的设计已经说明：工具调用不是附属功能，而是 LLM 系统设计中的核心语义之一。Gemini 和 OpenAI 也都支持这一方向。

因此，如果未来 `a2a_t.llm` 要继续演进：

- tool definition 不应只是某个 adapter 内部的私有参数
- tool call / tool result 应该进入统一模型层
- stop / finish reason 也应进入统一响应对象

### 8.5 文件、检索、缓存能力应被视为外围 capability

这几类能力不一定要在第一阶段就统一，但至少要在架构上预留扩展点：

- 文件上传与文件引用
- PDF / 长文档理解
- URL / Web / Search 增强
- prompt/context caching
- background / batch / live session

这些能力在 Gemini 和 OpenAI 上已经比较成体系，在 Anthropic 上也逐步清晰。DeepSeek 则提醒我们，缓存、推理模式、补全模式也可能成为供应商差异的一部分。如果未来完全不预留，会导致统一层只能停留在“文本调用代理”。

## 9. 对当前三家 adapter 的定位判断，以及对 DeepSeek 接入的启发

结合现有代码，可以把当前三个 adapter 理解为：

- OpenAI adapter：原生 schema 结构化抽取适配器
- Google adapter：Gemini schema 结构化抽取适配器
- Anthropic adapter：基于 tool use 的结构化抽取适配器

这个定位本身是合理的，而且对于 `PromptSlotExtractor` 这类场景非常实用。

但也要清楚看到边界：

- 它们还不是完整聊天适配器
- 还不是完整工具代理适配器
- 还不是多模态/文件统一层
- 还不是异步/流式/批处理调度层

如果未来引入 DeepSeek，一个比较务实的初始判断是：

- chat 能力层先复用 OpenAI-compatible transport 思路
- structured 能力层不要直接复用 OpenAI 的强 schema 假设
- 优先支持 `JSON_MODE` 版本
- 对高可靠结构化抽取场景，再补 `STRICT_TOOL_SCHEMA` 版本

换句话说，当前设计已经找到了一个正确切口，但尚不足以直接承载更广义的 LLM 平台能力。

## 10. 建议的演进路径

为了降低重构风险，建议按增量方式推进：

### 第一阶段

- 保留现有 `structured()` 能力作为兼容入口
- 把返回值从“字符串化 JSON”逐步收敛为“已解析结构化结果对象”
- 为 usage、finish reason、raw provider response 预留字段

### 第二阶段

- 引入统一的消息与内容块模型
- 把 `complete()` / `chat()` 从抽象占位改成真正可落地的能力接口
- 明确哪些 adapter 支持 text/chat，哪些只支持 structured

### 第三阶段

- 引入 tool calling capability
- 让 Anthropic 的当前实现回归其原生语义，而不是只把 tool result 当作 JSON 搬运手段
- 同时为 OpenAI / Gemini 增加同类能力映射

### 第四阶段

- 视业务需要再扩展 files、retrieval、streaming、batch、cache
- 避免一次性做成“全能网关”，优先围绕真实使用场景演进

## 11. 结论

从官方 API 文档出发，可以得出一个比较明确的结论：OpenAI、Google Gemini、Anthropic、DeepSeek 都已经具备支撑企业级 LLM 适配层的核心能力，但它们在“结构化输出如何实现、工具调用处于什么语义层、文件与检索如何进入调用链、缓存与推理模式如何暴露”这些关键问题上并不完全一致。

因此，`a2a_t.llm` 后续最重要的设计原则不是“做一个表面统一的大接口”，而是：

- 在上层暴露稳定、清晰、可组合的 capability
- 在下层允许供应商差异以策略形式真实存在
- 对公共概念做强类型建模
- 对结构化输出、工具调用、消息块、多模态输入做一等抽象
- 不把“OpenAI-compatible”误判为“能力语义完全等价”

如果沿这个方向演进，当前已经实现的 OpenAI / Google / Anthropic 三个结构化 adapter 可以直接成为新架构的第一批 capability 实现，而不是被推倒重来；而 DeepSeek 也可以在后续以“OpenAI-compatible chat + 独立 structured strategy”方式平滑接入。

## 12. 官方参考文档

### OpenAI

- https://developers.openai.com/api/docs
- https://developers.openai.com/api/docs/guides/structured-outputs
- https://developers.openai.com/api/docs/guides/function-calling
- https://developers.openai.com/api/docs/guides/conversation-state
- https://developers.openai.com/api/docs/guides/background
- https://developers.openai.com/api/docs/guides/file-inputs
- https://developers.openai.com/api/docs/guides/prompt-caching
- https://developers.openai.com/api/docs/guides/batch
- https://developers.openai.com/api/docs/guides/realtime
- https://developers.openai.com/api/docs/models
- https://developers.openai.com/api/docs/models/compare

### Google Gemini

- https://ai.google.dev/gemini-api/docs/models
- https://ai.google.dev/gemini-api/docs/structured-output
- https://ai.google.dev/gemini-api/docs/function-calling
- https://ai.google.dev/gemini-api/docs/live-api
- https://ai.google.dev/gemini-api/docs/caching
- https://ai.google.dev/gemini-api/docs/files
- https://ai.google.dev/api/files
- https://ai.google.dev/gemini-api/docs/batch-api
- https://ai.google.dev/gemini-api/docs/url-context
- https://ai.google.dev/gemini-api/docs/file-search
- https://ai.google.dev/gemini-api/docs/google-search
- https://ai.google.dev/gemini-api/docs/tokens
- https://ai.google.dev/pricing

### Anthropic Claude

- https://platform.claude.com/docs/en/api/overview
- https://platform.claude.com/docs/en/api/messages
- https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/overview
- https://platform.claude.com/docs/en/docs/agents-and-tools/tool-use/implement-tool-use
- https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
- https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- https://platform.claude.com/docs/en/build-with-claude/pdf-support
- https://platform.claude.com/docs/en/api/beta/files/list
- https://docs.anthropic.com/en/docs/models-overview
- https://docs.anthropic.com/en/docs/build-with-claude/overview
- https://docs.anthropic.com/en/docs/about-claude/pricing

### DeepSeek

- https://api-docs.deepseek.com/
- https://api-docs.deepseek.com/guides/json_mode/
- https://api-docs.deepseek.com/guides/function_calling/
- https://api-docs.deepseek.com/guides/kv_cache/
- https://api-docs.deepseek.com/guides/anthropic_api
- https://api-docs.deepseek.com/quick_start/pricing
