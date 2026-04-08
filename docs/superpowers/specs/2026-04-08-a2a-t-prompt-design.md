# A2A-T Prompt 获取组件设计文档

## 1. 背景

当前需要在 SDK 中提供一个用 Python 实现的 Prompt 获取组件。

这个组件的目标不是编辑、发布或管理 Prompt 资产，而是面向调用方提供一套稳定、可扩展的获取能力：

1. 列出可用 Prompt
2. 选择目标 Prompt
3. 加载 Prompt 正文
4. 对远端 Prompt 做解析、校验与缓存

为保持职责清晰，这个组件计划拆分为多个子组件，每个子组件只负责一个明确问题。

## 2. 设计目标

### 2.1 本期目标

1. 提供稳定的 Python 库 API，不提供 CLI。
2. 建立完整的 Prompt 获取链路：
   - `PromptCatalogRegistry`
   - `PromptCatalog`
   - `PromptReference`
   - `PromptLoader`
3. 为调用方提供“列出 Prompt”与“加载 Prompt 正文”两段能力。
4. 默认提供三类 catalog：
   - `LocalPromptCatalog`
   - `UrlPromptCatalog`
   - `AgentPromptCatalog`
5. 保留可扩展的主协议与默认实现：
   - `PromptProvider`
   - `PromptParser`
   - `PromptStore`
6. 让调用方自己决定如何选择 catalog 和 Prompt。

### 2.2 非目标

1. 不提供内置 Prompt 选择策略，如 `search()`、`select()`、语言回退、版本优选。
2. 不提供 Prompt 编辑、发布、审核与治理能力。
3. 不提供 CLI。
4. 不公开更细粒度的二级/三级协议族。

## 3. 子组件划分

这个 Prompt 获取组件包含以下子组件：

1. `PromptCatalogRegistry`
   - 暴露所有可用 catalog
2. `PromptCatalog`
   - 从单一来源列出可用 Prompt
3. `PromptReference`
   - 作为 catalog 输出，也是 loader 输入
4. `PromptLoader`
   - 基于已选中的 reference 加载 Prompt 正文
5. `PromptProvider`
   - 获取原始 Prompt 内容
6. `PromptParser`
   - 解析 Prompt 内容与 front matter
7. `PromptStore`
   - 处理远端 Prompt 缓存

完整链路可以理解为：

`PromptCatalogRegistry -> PromptCatalog -> PromptReference -> PromptLoader -> PromptProvider -> PromptParser -> Prompt`

对于远端来源，还会额外经过：

`PromptLoader <-> PromptStore`

## 4. 核心设计原则

1. 单一职责：列举、选择、加载、解析、缓存分层清晰。
2. 强契约优先：Prompt front matter 决定发布契约，缺字段直接报错。
3. 默认可用，按需替换：SDK 提供默认实现，也允许调用方替换主组件或内部扩展点。
4. 调用方负责选择：SDK 负责列出和加载，不负责业务选择策略。
5. 协议优先：顶层主组件以 `Protocol` 暴露，便于扩展。

## 5. 顶层职责划分

### 5.1 `PromptCatalogRegistry`

职责：

1. 向调用方暴露所有可用 catalog。
2. 让调用方先选 catalog，再选 Prompt。
3. 不负责 Prompt 选择。
4. 不负责 Prompt 正文加载。

建议接口：

```python
class PromptCatalogRegistry(Protocol):
    def list_catalogs(self) -> dict[str, PromptCatalog]: ...
```

### 5.2 `PromptCatalog`

职责：

1. 面向某个来源列出所有可用 Prompt。
2. 返回统一的 `PromptReference` 列表。
3. 不负责 Prompt 正文加载。
4. 不负责选择、过滤、搜索、排序决策。

建议接口：

```python
class PromptCatalog(Protocol):
    def list(self) -> list[PromptReference]: ...
```

### 5.3 `PromptLoader`

职责：

1. 在调用方已选定 `PromptReference` 后加载正文。
2. 协调 provider、parser、validation、cache。
3. 对远端来源处理缓存、刷新与 stale fallback。
4. 不负责 catalog 列举或 Prompt 选择。

### 5.4 调用方职责

调用方负责：

1. 获取 `PromptCatalogRegistry`
2. 选择某个 catalog
3. 调用 `catalog.list()` 获取 `PromptReference` 列表
4. 自行决定如何筛选与选择 `PromptReference`
5. 将选中的 `PromptReference` 交给 `PromptLoader.load()`

## 6. Prompt 发布契约

Prompt 文件必须包含 Markdown front matter。

### 6.1 发布字段

必填字段：

- `name`
- `version`
- `title`
- `description`

可选字段：

- `language`

`language` 缺失时补为 `default`。

### 6.2 唯一身份

Prompt 的发布身份由以下字段唯一确定：

- `name`
- `language`
- `version`

### 6.3 约束

1. 调用方不能覆盖 Prompt 中的真实元数据。
2. `title` 与 `description` 用于展示，不参与身份判定。
3. 本期不引入 `namespace`。
4. 本期不要求公开 `prompt_id`。

## 7. 对外模型

### 7.1 `PromptSource`

```python
@dataclass(slots=True)
class PromptSource:
    source_type: Literal["local_file", "url", "agent"]
    locator: str
```

说明：

1. `local_file` 时，`locator` 为本地文件路径。
2. `url` 时，`locator` 为 Prompt 正文 URL。
3. `agent` 时，`locator` 为已知 Agent Prompt URL。

### 7.2 `PromptReference`

```python
@dataclass(slots=True)
class PromptReference:
    name: str
    language: str
    version: str
    title: str
    description: str
    source: PromptSource
    metadata: dict[str, Any] | None = None
```

说明：

1. `PromptReference` 是 catalog 输出，也是 loader 输入。
2. `source` 保存后续加载正文所需的来源定位信息。
3. `metadata` 允许附加来源特定信息。

### 7.3 `Prompt`

字段：

- `name`
- `language`
- `version`
- `title`
- `description`
- `format`
- `body`
- `raw_content`
- `source`
- `cache_status`

### 7.4 `FetchResult`

字段：

- `content`
- `content_type`
- `source`
- `fetched_at`

### 7.5 `CachedPromptRecord`

字段：

- `cache_key`
- `source_type`
- `name`
- `language`
- `version`
- `format`
- `fetched_at`
- `expires_at`
- `checksum`

## 8. `PromptLoader` 输入契约

`PromptLoader.load()` 的主入口应为：

```python
load(
    *,
    reference: PromptReference,
    refresh: bool = False,
) -> Prompt
```

说明：

1. `reference.source` 用于定位正文来源。
2. `reference.name`、`reference.language`、`reference.version` 用于最终校验。
3. 调用方不需要把这三个字段拆出来单独传入 loader。

## 9. Provider / Parser / Store 设计

### 9.1 `PromptProvider`

职责：

1. 根据 `locator` 获取原始内容。
2. 返回统一的 `FetchResult`。
3. 不负责解析。
4. 不负责业务元数据校验。
5. 不负责缓存。

默认 provider：

- `LocalFileProvider`
- `UrlProvider`
- `AgentProvider`

#### Fetcher 扩展点

默认 provider 的一级内部扩展点为：

- `LocalFileFetcher`
- `UrlFetcher`
- `AgentFetcher`

### 9.2 `PromptParser`

职责：

1. 解析 Prompt 原始内容。
2. 提取元数据与正文。
3. 校验格式层面的必填字段。
4. 返回标准化 `Prompt`。

默认 parser：

- `MarkdownPromptParser`

### 9.3 `PromptStore`

职责：

1. 读写缓存。
2. 输出明确的缓存语义。
3. 处理过期判断与 stale fallback 需要的旧缓存保留。
4. 处理写入冲突策略。

默认 store：

- `LocalFilePromptStore`

### 9.4 Store 内部策略

支持两类内部策略：

- `ExpirationPolicy`
- `ConflictResolutionPolicy`

默认实现：

- `TTLExpirationPolicy`
- `OverwriteOnConflictPolicy`

`PromptStore.resolve()` 返回：

- `miss`
- `hit`
- `expired`
- `stale_fallback`

## 10. 默认缓存布局

默认本地缓存目录结构：

```text
<cache_root>/
  prompts/
    <source_type>/
      <cache_key>/
        content.md
        metadata.json
```

`cache_key` 由以下维度生成：

1. `source_type`
2. `locator`
3. `name`
4. `language`
5. `version`
6. `format`

## 11. 默认 Catalog 实现

### 11.1 `LocalPromptCatalog`

职责：

1. 扫描本地目录下的 Markdown 文件。
2. 解析 front matter。
3. 输出 `source_type="local_file"` 的 `PromptReference`。

配置约束：

1. 可显式传入目录。
2. 缺省目录由 `env.yaml` 配置。
3. 未自定义时，默认扫描该目录下所有 Markdown。

### 11.2 `UrlPromptCatalog`

职责：

1. 从索引 URL 获取 Prompt 列表。
2. 将索引项展开为 `PromptReference`。
3. 输出 `source_type="url"` 的 `PromptReference`。

配置约束：

1. 没有默认索引 URL。
2. 必须通过 `env.yaml` 配置。
3. 未配置时，表示没有 URL catalog 源。

索引文档约束：

1. 配置的是索引 URL，不是 Prompt 正文 URL。
2. 后续正文加载时，真正使用的是索引项中的 `url` 字段。

### 11.3 `AgentPromptCatalog`

职责：

1. 输入一组 `a2a-python` 定义的 `AgentCard`。
2. 从 `AgentCard.extensions` 中定位 Prompt 扩展。
3. 从扩展参数中读取索引 URL。
4. 拉取索引并展开为 `PromptReference`。

输出约束：

1. 直接输出落到 Prompt 正文层级的引用。
2. 默认输出 `source_type="url"` 的 `PromptReference`。
3. Provider 只需要读取正文，不承担 discovery。
4. `AgentPromptCatalog.__init__` 中的 `agent_cards` 类型直接使用 `a2a-python` 定义的 `AgentCard`。

## 12. Agent Catalog 配置模型

`PromptLoaderConfig` 增加以下 Agent catalog 相关配置：

- `default_prompt_extension_uri: str | None`
- `prompt_extension_uri_overrides: dict[str, str]`
- `default_prompt_index_url_param_key: str`
- `prompt_index_url_param_key_overrides: dict[str, str]`

### 12.1 覆盖规则

覆盖键使用 `AgentCard.name`：

1. 先查 `prompt_extension_uri_overrides[agent_card.name]`
2. 未命中则使用 `default_prompt_extension_uri`
3. 先查 `prompt_index_url_param_key_overrides[agent_card.name]`
4. 未命中则使用 `default_prompt_index_url_param_key`

### 12.2 Agent 扩展定位逻辑

对每个 `AgentCard`：

1. 根据配置确定目标扩展 `uri`
2. 在 `AgentCard.extensions` 中找到 `extension.uri == 目标 uri`
3. 根据配置确定索引参数 key
4. 从 `extension.params[configured_key]` 读取索引 URL
5. 拉取索引并展开为 Prompt references

### 12.3 已知限制

由于当前 `a2a-python` 的 `AgentCard` 没有 `id` 字段，本期接受 `AgentCard.name` 可能重名的风险。

## 13. 索引格式约束

`UrlPromptCatalog` 与 `AgentPromptCatalog` 使用的索引至少要表达：

- `name`
- `language`
- `version`
- `title`
- `description`
- `url`

说明：

1. `url` 必须指向 Prompt 正文地址。
2. catalog 展开时会转换为 `PromptSource(source_type="url", locator=url)`。

## 14. 加载流程

### 14.1 完整主流程

1. 调用方获取 `PromptCatalogRegistry`
2. 调用方选择一个 `PromptCatalog`
3. 调用方调用 `catalog.list()`
4. 调用方从列表中选出一个 `PromptReference`
5. 调用方将该 `PromptReference` 交给 `PromptLoader.load(reference=...)`
6. `PromptLoader` 根据 `source_type` 选择对应 `PromptProvider`
7. `PromptProvider` 返回 `FetchResult`
8. `PromptLoader` 调用 `PromptParser`
9. `PromptLoader` 校验 `name + language + version`
10. 对远端来源协调 `PromptStore`
11. 返回 `Prompt`

### 14.2 本地文件来源

1. 不走远端缓存刷新流程。
2. 直接读取并解析。

### 14.3 远端来源

1. 先读缓存语义结果。
2. 命中且未过期时直接返回。
3. 缺失、过期或 `refresh=True` 时拉取远端。
4. 解析与校验成功后写入缓存。
5. 刷新失败且允许回退时返回旧缓存。

## 15. 错误模型

建议保留以下错误类型：

- `PromptSourceError`
- `PromptFetchError`
- `PromptParseError`
- `PromptMetadataError`
- `PromptCacheError`

## 16. 测试策略

### 16.1 Registry / Catalog

覆盖：

1. `PromptCatalogRegistry` 能暴露全部 catalog。
2. 三类默认 catalog 能统一输出 `PromptReference`。
3. Agent catalog 能按 `AgentCard.name` 命中 override。

### 16.2 Provider / Parser / Store

覆盖：

1. 默认 provider 成功路径与错误路径。
2. fetcher 注入替换能力。
3. Markdown parser 成功解析与缺字段失败。
4. store 的命中、缺失、过期、stale fallback 与冲突策略。

### 16.3 Loader

覆盖：

1. `reference=...` 主调用路径。
2. 本地来源直读。
3. 远端刷新与 stale fallback。
4. 元数据校验失败时不写缓存。

## 17. 包结构

组件建议组织为：

- `a2a_t.prompt.catalog_registry`
- `a2a_t.prompt.catalog`
- `a2a_t.prompt.loader`
- `a2a_t.prompt.providers`
- `a2a_t.prompt.parser`
- `a2a_t.prompt.cache`
- `a2a_t.prompt.models`
- `a2a_t.prompt.config`

## 18. 结论

本设计将 Prompt 获取组件拆分为一条清晰链路：

1. `PromptCatalogRegistry`
2. `PromptCatalog`
3. `PromptReference`
4. `PromptLoader`
5. `PromptProvider`
6. `PromptParser`
7. `PromptStore`

这样可以把“列出 Prompt”和“加载 Prompt 正文”两段能力明确分开，同时为本地、URL、Agent 三类来源提供统一的扩展模型。
