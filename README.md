# a2a-t-sdk

基于 A2A-T 协议的 Python SDK，方便开发者基于 A2A-T 协议开发 Agent。

## Quick Start

```python
from a2a_t import ExtendedClient

client = ExtendedClient(url="http://localhost:8080")
response = client.send(task_id="task-001", params={"prompt": "查询基站状态"})
```

## Project Structure

```text
a2a-t-sdk/
├── src/a2a_t/
│   ├── common/
│   ├── client/
│   ├── server/
│   ├── prompt/
│   ├── compression/
│   ├── llm/
│   └── config/
├── tests/
└── docs/
```

## Prompt

当前仓库提供一套完整的 Prompt 获取链路，分为两段：

1. `PromptCatalogRegistry` / `PromptCatalog`
   - 列出可用 Prompt
2. `PromptLoader`
   - 加载调用方已经选中的 Prompt 正文

可以把它理解为：

`registry -> catalog -> prompt references -> loader -> prompt`

## 核心概念

### `PromptCatalogRegistry`

负责暴露所有可用 catalog。

典型接口：

```python
catalogs = registry.list_catalogs()
```

返回值是 `dict[str, PromptCatalog]`，调用方先选 catalog，再从该 catalog 中选择 Prompt。

### `PromptCatalog`

只负责列出可用 Prompt 引用。

典型接口：

```python
references = catalog.list()
```

返回值是 `list[PromptReference]`。

### `PromptReference`

`PromptReference` 是 catalog 输出，也是 loader 输入。它至少包含：

- `name`
- `language`
- `version`
- `title`
- `description`
- `source`

### `PromptLoader`

`PromptLoader` 只负责加载正文，不负责 catalog 选择逻辑。

主入口：

```python
prompt = loader.load(reference=reference)
```

## Prompt 发布契约

Prompt 文件必须包含 Markdown front matter。

必填字段：

- `name`
- `version`
- `title`
- `description`

可选字段：

- `language`

`language` 缺失时会补为 `default`。

Prompt 的唯一身份由 `name + language + version` 确定。

## 完整调用流程

一个完整的调用链通常是：

1. 创建 `PromptLoader`
2. 获取某个 `PromptCatalog` 或 `PromptCatalogRegistry`
3. 调用 `catalog.list()` 获取 `PromptReference` 列表
4. 由调用方选择一个 `PromptReference`
5. 将该 reference 交给 `PromptLoader.load(reference=...)`
6. 获取最终的 `Prompt`

## 公开 API

主要公开对象位于 `a2a_t.prompt`：

- `PromptCatalog`
- `PromptCatalogRegistry`
- `PromptReference`
- `PromptLoader`
- `PromptLoaderConfig`
- `PromptSource`
- `Prompt`
- `PromptProvider`
- `PromptParser`
- `PromptStore`
- `LocalPromptCatalog`
- `UrlPromptCatalog`
- `AgentPromptCatalog`
- `LocalFileProvider`
- `UrlProvider`
- `AgentProvider`
- `MarkdownPromptParser`
- `LocalFilePromptStore`
- `TTLExpirationPolicy`
- `OverwriteOnConflictPolicy`
- `LocalFileFetcher`
- `UrlFetcher`
- `AgentFetcher`

## 组装 `PromptLoader`

最小可用 loader 一般包含：

- `PromptLoaderConfig`
- `PromptParser`
- `PromptStore`
- `providers`
- `now_provider`

示例：

```python
from datetime import datetime, timedelta, timezone

from a2a_t.prompt import (
    AgentProvider,
    LocalFilePromptStore,
    LocalFileProvider,
    MarkdownPromptParser,
    PromptLoader,
    PromptLoaderConfig,
    UrlProvider,
)

config = PromptLoaderConfig(
    cache_dir=".cache/prompts",
    default_ttl=timedelta(hours=6),
    allow_stale_fallback=True,
)

loader = PromptLoader(
    config=config,
    parser=MarkdownPromptParser(),
    cache_store=LocalFilePromptStore(config.cache_dir),
    providers={
        "local_file": LocalFileProvider(),
        "url": UrlProvider(),
        "agent": AgentProvider(),
    },
    now_provider=lambda: datetime.now(timezone.utc),
)
```

## 场景 1：从本地目录列出并加载 Prompt

适用于本地仓库中已经存在一批 Markdown Prompt 文件。

```python
from datetime import datetime, timedelta, timezone

from a2a_t.prompt import (
    LocalFilePromptStore,
    LocalFileProvider,
    LocalPromptCatalog,
    MarkdownPromptParser,
    PromptLoader,
    PromptLoaderConfig,
)

config = PromptLoaderConfig(
    cache_dir=".cache/prompts",
    default_ttl=timedelta(hours=6),
)

loader = PromptLoader(
    config=config,
    parser=MarkdownPromptParser(),
    cache_store=LocalFilePromptStore(config.cache_dir),
    providers={"local_file": LocalFileProvider()},
    now_provider=lambda: datetime.now(timezone.utc),
)

catalog = LocalPromptCatalog(prompt_dir="./prompts")
references = catalog.list()

reference = next(
    item
    for item in references
    if item.name == "diagnosis"
    and item.language == "default"
    and item.version == "1.0.0"
)

prompt = loader.load(reference=reference)

print(prompt.title)
print(prompt.body)
```

说明：

1. `LocalPromptCatalog` 返回的 `source_type` 是 `local_file`
2. 本地文件默认不走远端缓存刷新流程

## 场景 2：从 URL 索引列出并加载 Prompt

适用于远端维护一份 Prompt 索引文件，索引里再指向具体正文 URL。

假设索引内容类似：

```json
{
  "prompts": [
    {
      "name": "diagnosis",
      "language": "default",
      "version": "1.0.0",
      "title": "Alarm Diagnosis",
      "description": "Diagnose alarm events.",
      "url": "https://example.com/prompts/diagnosis.md"
    }
  ]
}
```

调用示例：

```python
from datetime import datetime, timedelta, timezone

from a2a_t.prompt import (
    LocalFilePromptStore,
    MarkdownPromptParser,
    PromptLoader,
    PromptLoaderConfig,
    UrlPromptCatalog,
    UrlProvider,
)

config = PromptLoaderConfig(
    cache_dir=".cache/prompts",
    default_ttl=timedelta(hours=6),
    allow_stale_fallback=True,
)

loader = PromptLoader(
    config=config,
    parser=MarkdownPromptParser(),
    cache_store=LocalFilePromptStore(config.cache_dir),
    providers={"url": UrlProvider()},
    now_provider=lambda: datetime.now(timezone.utc),
)

catalog = UrlPromptCatalog(index_url="https://example.com/prompt-index.json")
references = catalog.list()

reference = references[0]
prompt = loader.load(reference=reference)

print(prompt.source.locator)
print(prompt.body)
```

说明：

1. 这里配置的是索引 URL，不是 Prompt 正文 URL
2. `UrlPromptCatalog` 会把索引项里的 `url` 展开成最终的 `PromptReference.source.locator`
3. `PromptLoader` 后续真正读取的是这个最终正文 URL

## 场景 3：从 AgentCard 扩展列出并加载 Prompt

适用于调用方已经拿到一组 `AgentCard`，并希望从它们的扩展里定位 Prompt 索引。

`AgentPromptCatalog` 的逻辑是：

1. 遍历 `AgentCard.extensions`
2. 根据配置的扩展 `uri` 找到目标扩展
3. 从 `params[configured_key]` 中取出索引 URL
4. 拉取索引并展开成 `PromptReference`
5. 最终交给 loader 加载正文

示例：

```python
from datetime import datetime, timedelta, timezone

from a2a.types import AgentCard

from a2a_t.prompt import (
    AgentPromptCatalog,
    LocalFilePromptStore,
    MarkdownPromptParser,
    PromptLoader,
    PromptLoaderConfig,
    UrlProvider,
)


# 这些 AgentCard 通常由上游 discovery / resolver 提供。
agent_cards: list[AgentCard] = get_agent_cards_from_upstream()

config = PromptLoaderConfig(
    cache_dir=".cache/prompts",
    default_ttl=timedelta(hours=6),
    default_prompt_extension_uri="a2a-t.prompts",
    default_prompt_index_url_param_key="promptIndexUrl",
)

loader = PromptLoader(
    config=config,
    parser=MarkdownPromptParser(),
    cache_store=LocalFilePromptStore(config.cache_dir),
    providers={"url": UrlProvider()},
    now_provider=lambda: datetime.now(timezone.utc),
)

catalog = AgentPromptCatalog(
    agent_cards=agent_cards,
    default_prompt_extension_uri=config.default_prompt_extension_uri,
    prompt_extension_uri_overrides=config.prompt_extension_uri_overrides,
    default_prompt_index_url_param_key=config.default_prompt_index_url_param_key,
    prompt_index_url_param_key_overrides=config.prompt_index_url_param_key_overrides,
)

references = catalog.list()
reference = references[0]
prompt = loader.load(reference=reference)

print(prompt.title)
print(prompt.body)
```

说明：

1. `AgentPromptCatalog` 接收的是 `AgentCard` 列表，不负责 discovery
2. `AgentProvider` 不再负责读取 `AgentCard`
3. 默认情况下，Agent catalog 展开后的 reference 会直接落到 Prompt 正文 URL
4. 因此加载正文时一般走 `UrlProvider`
5. `a2a-python` 当前版本的 `AgentCard` 是生成模型，通常不建议在业务代码里手工构造

## 场景 4：使用 `PromptCatalogRegistry`

如果调用方希望统一持有多个 catalog，可以自己提供一个 registry。

```python
from a2a_t.prompt import LocalPromptCatalog, PromptCatalog, PromptCatalogRegistry, UrlPromptCatalog


class StaticPromptCatalogRegistry:
    def __init__(self, catalogs: dict[str, PromptCatalog]) -> None:
        self.catalogs = catalogs

    def list_catalogs(self) -> dict[str, PromptCatalog]:
        return self.catalogs


registry = StaticPromptCatalogRegistry(
    catalogs={
        "local": LocalPromptCatalog(prompt_dir="./prompts"),
        "remote": UrlPromptCatalog(index_url="https://example.com/prompt-index.json"),
    }
)

catalogs = registry.list_catalogs()
catalog = catalogs["local"]
references = catalog.list()
```

SDK 当前定义的是 registry 协议，选择哪个 catalog 仍由调用方自己实现。

## Catalog 设计约束

### `LocalPromptCatalog`

- 扫描本地目录下所有 Markdown 文件
- 返回 `source_type="local_file"` 的 references
- 缺省目录可以由 `env.yaml` 配置

### `UrlPromptCatalog`

- 需要显式提供索引 URL
- 如果 `env.yaml` 未配置索引 URL，则表示没有 URL catalog 源
- 返回 `source_type="url"` 的 references

### `AgentPromptCatalog`

- 输入是一组 `AgentCard`
- 根据扩展 `uri` 和参数 key 定位索引 URL
- 支持按 `AgentCard.name` 做覆盖配置
- 返回最终可直接加载正文的 references

## 加载行为

### 本地来源

- `local_file`：直接读取并解析
- 默认不走远端缓存刷新流程
- `local_file` 没有缓存过期的概念

### 远端来源

- `url` / `agent`：优先读缓存
- 缓存过期后自动刷新
- 刷新失败时，如果开启 `allow_stale_fallback`，返回旧缓存
- 可通过 `refresh=True` 强制刷新

## 缓存布局

- 远端 Prompt 缓存目录结构为：`prompts/<source_type>/<cache_key>/`
- `cache_key` 由 `source_type + locator + name + language + version + format` 生成
- 缓存写入使用解析后的真实 Prompt 元数据

## 扩展点

### Provider 扩展点

- `LocalFileProvider(fetcher=...)`
- `UrlProvider(fetcher=...)`
- `AgentProvider(fetcher=...)`

### Store 扩展点

- `LocalFilePromptStore` 默认组合 `TTLExpirationPolicy` 与 `OverwriteOnConflictPolicy`
- 可以替换 `ExpirationPolicy`
- 可以替换 `ConflictResolutionPolicy`
- `ExpirationPolicy` 负责判断缓存是否过期
- `ConflictResolutionPolicy` 负责决定缓存冲突时是否覆盖已有记录

### 主组件替换

`PromptLoader` 依赖的是协议：

- `PromptProvider`
- `PromptParser`
- `PromptStore`

调用方可以直接替换这些主组件，而不是必须使用默认实现。

## 组件调用关系

1. 调用方获取某个 catalog 或 registry
2. 调用方拿到 `PromptReference`
3. 调用方选择一个 `PromptReference`
4. `PromptLoader` 根据 `reference.source.source_type` 选择 provider
5. provider 返回原始内容
6. parser 输出标准化 `Prompt`
7. loader 做最终身份校验
8. 对远端来源协调 store
9. 返回最终 `Prompt`

简化后可以理解为：

`caller -> catalog -> PromptReference -> PromptLoader -> PromptProvider -> PromptParser -> Prompt`
