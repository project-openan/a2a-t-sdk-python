# a2a-t-sdk

Python A2A SDK for Telecom Scenarios.

## Overview

This SDK extends the official [a2a-python](https://github.com/a2a/a2a-python) SDK with features tailored for telecom operator environments.

## Features

- **A2A Client SDK**: O域客户端，支持发送自然语言请求
- **A2A Server SDK**: OMC域服务端，接收并处理请求
- **Prompt场景化**: 提供基于 catalog 和 loader 的 Prompt 获取能力
- **上下文压缩**: 多种压缩策略可配置
- **连接池管理**: 客户端和服务端双侧连接池
- **限流保护**: 服务端限流，保护领域大模型
- **LLM集成**: 支持 HTTP/gRPC/MQ/插件四种集成方式

## Installation

```bash
pip install a2a-t-sdk
```

## Quick Start

```python
from a2a_t import ExtendedClient

# Create client
client = ExtendedClient(url="http://localhost:8080")

# Send request
response = client.send(task_id="task-001", params={"prompt": "查询基站状态"})
```

## Prompt

当前仓库中的 Prompt 组件提供两段能力：

1. `PromptCatalogRegistry` / `PromptCatalog`
   用于列出可用 Prompt
2. `PromptLoader`
   用于加载调用方已经选中的 Prompt 正文

完整链路可以理解为：

`registry -> catalog -> PromptReference -> PromptLoader -> Prompt`

### 核心对象

- `PromptCatalogRegistry`
- `PromptCatalog`
- `PromptReference`
- `PromptLoader`
- `PromptLoaderConfig`
- `PromptSource`
- `Prompt`

### 调用方式

```python
from datetime import datetime, timedelta, timezone

from a2a_t.prompt import (
    LocalFilePromptStore,
    LocalPromptCatalog,
    MarkdownPromptParser,
    PromptLoader,
    PromptLoaderConfig,
    UrlProvider,
)

config = PromptLoaderConfig(
    cache_dir=".cache/prompts",
    default_ttl=timedelta(hours=6),
)

loader = PromptLoader(
    config=config,
    parser=MarkdownPromptParser(),
    cache_store=LocalFilePromptStore(config.cache_dir),
    providers={"url": UrlProvider()},
    now_provider=lambda: datetime.now(timezone.utc),
)

catalog = LocalPromptCatalog(prompt_dir="./prompts")
reference = catalog.list()[0]
prompt = loader.load(reference=reference)
```

### 约束说明

- Prompt 发布身份由 `name + language + version` 唯一确定
- 远端缓存目录结构为 `prompts/<source_type>/<cache_key>/`
- `local_file` 没有缓存过期的概念
- `ExpirationPolicy` 负责判断缓存是否过期
- `ConflictResolutionPolicy` 负责决定缓存冲突时是否覆盖

更完整的 Prompt 设计说明见：

- `docs/superpowers/specs/2026-04-08-a2a-t-prompt-design.md`

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/

# Type check
mypy src/
```

## Project Structure

```text
a2a-t-sdk/
├── src/a2a_t/         # Main package
│   ├── common/        # Common utilities
│   ├── client/        # Client extensions
│   ├── server/        # Server extensions
│   ├── prompt/        # Prompt loading components
│   ├── compression/   # Compression strategies
│   ├── llm/           # LLM adapters
│   └── config/        # Configuration
├── templates/         # Built-in templates
├── tests/             # Test suite
├── docs/              # Documentation
└── examples/          # Usage examples
```

## License

Apache License 2.0
