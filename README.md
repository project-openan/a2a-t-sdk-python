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
from datetime import timedelta

from a2a_t.prompt import (
    DefaultPromptCatalogRegistry,
    LocalFilePromptStore,
    LocalFileProvider,
    LocalPromptCatalog,
    MarkdownPromptParser,
    PromptLoader,
    PromptLoaderConfig,
)

config = PromptLoaderConfig(
    local_prompt_dir="./prompts",
    allowed_extensions=[".md"],
    default_ttl=timedelta(hours=6),
)

catalog_registry = DefaultPromptCatalogRegistry()
catalog_registry.register("local", LocalPromptCatalog(config=config))

loader = PromptLoader(
    config=config,
    parser=MarkdownPromptParser(),
    cache_store=LocalFilePromptStore(config.local_prompt_dir),
    providers={"local_file": LocalFileProvider()},
)

catalog = catalog_registry.get("local")
reference = catalog.list()[0]
prompt = loader.load(reference=reference)
```

### 约束说明

- Prompt 发布身份由 `name + language + version` 唯一确定
- 本地镜像目录结构为 `<local_root>/<name>/<version>/<language>/prompt.<ext>`
- 通过 `A2AT_PROMPT_LOCAL_DIR` 配置 Prompt 本地根目录
- 通过 `A2AT_PROMPT_ALLOWED_EXTENSIONS` 配置允许扫描的 Prompt 扩展名
- 组件直接接收 `PromptLoaderConfig`，调用方按需组装 `PromptCatalogRegistry` 与 `PromptLoader`
- `ExpirationPolicy` 负责判断缓存是否过期
- `ConflictResolutionPolicy` 负责决定缓存冲突时是否覆盖

### 环境变量

Prompt 模块当前使用以下环境变量：

- `A2AT_PROMPT_LOCAL_DIR`
- `A2AT_PROMPT_ALLOWED_EXTENSIONS`

更完整的 Prompt 设计说明见：

- `docs/superpowers/specs/2026-04-08-a2a-t-prompt-design.md`

## Prompt Compliance

Prompt Compliance 模块用于在服务端对加工后的 Prompt 做遵从性检查。典型流程为：

1. 使用安全护栏检查加工后的 Prompt
2. 从加工后 Prompt 的 front matter 解析 `name + language + version`
3. 通过 Prompt catalog 和 loader 找回原始 Prompt
4. 调用 LLM 提取结构化槽位
5. 加载镜像路径下的 `slot.json`
6. 使用运行时 JSON Schema 校验提取出的槽位

### 核心对象

- `PromptComplianceConfig`
- `GuardrailProviderConfig`
- `SlotExtractionConfig`
- `SlotSchemaConfig`
- `SlotSchema`
- `SlotSchemaResolver`
- `SlotSchemaBuilder`
- `SlotValidator`
- `PromptComplianceService`

### 槽位目录

槽位文件默认存放在 `./slots`，路径布局与 Prompt 身份镜像：

```text
slots/
└── <name>/
    └── <version>/
        └── <language>/
            └── slot.json
```

示例：

```text
slots/network diagnosis/1.0.0/zh-CN/slot.json
```

### 环境变量

Prompt Compliance 模块使用以下环境变量：

- `A2AT_PROMPT_COMPLIANCE_ENABLED`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_PROVIDER`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MODEL`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TIMEOUT_SECONDS`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TEMPERATURE`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MAX_RETRIES`
- `A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR`
- `A2AT_PROMPT_COMPLIANCE_SLOT_FILE_NAME`
- `A2AT_PROMPT_COMPLIANCE_SLOT_NOT_FOUND_POLICY`

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
