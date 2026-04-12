# a2a-t-sdk

面向电信场景的 Python A2A SDK。

## 概述

本 SDK 在官方 [a2a-python](https://github.com/a2a/a2a-python) SDK 基础上进行扩展，提供更适合电信运营商场景的能力增强。

## 功能特性

- **A2A Client SDK**: O域客户端，支持发送自然语言请求
- **A2A Server SDK**: OMC域服务端，接收并处理请求
- **Prompt场景化**: 提供基于 catalog 和 loader 的 Prompt 获取能力
- **上下文压缩**: 多种压缩策略可配置
- **连接池管理**: 客户端和服务端双侧连接池
- **限流保护**: 服务端限流，保护领域大模型
- **LLM集成**: 支持 HTTP/gRPC/MQ/插件四种集成方式

## 安装

```bash
pip install a2a-t-sdk
```

## 快速开始

```python
from a2a_t import ExtendedClient

# Create client
client = ExtendedClient(url="http://localhost:8080")

# Send request
response = client.send(task_id="task-001", params={"prompt": "查询基站状态"})
```

## Prompt Management

Prompt 模块提供两类能力：列出可用 Prompt，以及加载选中的 Prompt 正文。

调用链路如下：

`registry -> catalog -> PromptReference -> PromptLoader -> Prompt`

### 快速使用

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

### 关键配置

- `A2AT_PROMPT_DEFAULT_TTL_SECONDS`：远端 Prompt 本地镜像的默认过期时间，单位为秒
- `A2AT_PROMPT_LOCAL_DIR`：Prompt 本地根目录，既用于本地 Prompt 扫描，也用于远端 Prompt 镜像落盘
- `A2AT_PROMPT_ALLOWED_EXTENSIONS`：本地 Catalog 允许扫描的 Prompt 文件扩展名列表，逗号分隔
- `A2AT_DEFAULT_PROMPT_EXTENSION_URI`：Agent Prompt Catalog 默认读取的 Prompt 扩展 URI
- `A2AT_PROMPT_EXTENSION_URI_OVERRIDES`：按 Agent 名称覆盖 Prompt 扩展 URI 的 JSON 映射
- `A2AT_DEFAULT_PROMPT_INDEX_URL_PARAM_KEY`：Agent Prompt Catalog 默认读取索引 URL 的参数名
- `A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES`：按 Agent 名称覆盖索引 URL 参数名的 JSON 映射

更完整的 Prompt 设计说明见：

- `docs/superpowers/specs/2026-04-11-prompt-management-design.md`

## Prompt Compliance

Prompt Compliance 用于在服务端校验“加工后的 Prompt”是否仍满足原始模板约束。

完整流程为：安全护栏检查 → 解析 Prompt 身份 → 回取原始 Prompt → LLM 提取槽位 → 加载 `slot.json` → 执行规则校验。

### 快速使用

```python
from datetime import datetime, timedelta, timezone

from a2a_t.prompt import (
    DefaultPromptCatalogRegistry,
    LocalFilePromptStore,
    LocalFileProvider,
    LocalPromptCatalog,
    MarkdownPromptParser,
    PromptLoader,
    PromptLoaderConfig,
)
from a2a_t.server.prompt_compliance import (
    GuardrailProviderConfig,
    ProcessedPromptParser,
    PromptComplianceService,
    PromptOriginResolver,
    PromptSlotExtractor,
    SafetyGuardrailFactory,
    SlotSchemaConfig,
    SlotSchemaResolver,
    SlotValidator,
)


class DemoStructuredAdapter:
    def structured(
        self,
        *,
        messages: list[dict[str, str]],
        json_schema: dict[str, object],
        **kwargs: object,
    ) -> object:
        return type(
            "LLMResponseLike",
            (),
            {
                "content": '{"slots": {"cityName": "广州"}, "notes": ["ok"], "confidence": 0.95}',
                "model": "demo",
                "usage": {},
                "metadata": {},
            },
        )()


prompt_root = "./prompts"
slot_root = "./slots"

loader_config = PromptLoaderConfig(
    local_prompt_dir=prompt_root,
    allowed_extensions=[".md"],
    default_ttl=timedelta(hours=1),
)

catalog_registry = DefaultPromptCatalogRegistry()
catalog_registry.register("local", LocalPromptCatalog(config=loader_config))

loader = PromptLoader(
    config=loader_config,
    parser=MarkdownPromptParser(),
    cache_store=LocalFilePromptStore(prompt_root),
    providers={"local_file": LocalFileProvider()},
    now_provider=lambda: datetime.now(timezone.utc),
)

service = PromptComplianceService(
    guardrail=SafetyGuardrailFactory.create(GuardrailProviderConfig(provider="noop")),
    parser=ProcessedPromptParser(),
    origin_resolver=PromptOriginResolver(
        catalog_registry=catalog_registry,
        prompt_loader=loader,
    ),
    extractor=PromptSlotExtractor(adapter=DemoStructuredAdapter()),
    slot_config_resolver=SlotSchemaResolver(SlotSchemaConfig(root_dir=slot_root, file_name="slot.json")),
    validator=SlotValidator(),
    slot_not_found_policy="strict",
)

result = service.check(
    processed_prompt_text="---\nname: weather query\nlanguage: zh-CN\nversion: 0.0.1\n---\n请帮我查询广州今天天气。",
    request_metadata={"request_id": "demo-request"},
)

print(result.passed)
print(result.extracted_slots)
```

`service.check(...)` 会返回 `PromptComplianceResult`。

### 槽位目录

槽位文件默认放在 `./slots`，路径与 Prompt 身份镜像：

```text
slots/
└── <name>/
    └── <version>/
        └── <language>/
            └── slot.json
```

示例：

```text
slots/weather query/0.0.1/zh-CN/slot.json
```

### 关键配置

- `A2AT_PROMPT_COMPLIANCE_ENABLED`：是否启用服务端校验
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER`：安全护栏 provider，默认 `noop`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID`：护栏策略或模板标识
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_PROVIDER`：槽位提取使用的 LLM provider
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MODEL`：槽位提取使用的模型名称
- `A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR`：槽位配置根目录
- `A2AT_PROMPT_COMPLIANCE_SLOT_FILE_NAME`：槽位配置文件名，默认 `slot.json`
- `A2AT_PROMPT_COMPLIANCE_SLOT_NOT_FOUND_POLICY`：槽位配置缺失时的处理策略，支持 `strict` 和 `skip`

### 安全护栏 Provider

- 当前已实现的独立护栏 provider 为 `google_model_armor`
- Google provider 使用官方 SDK `google-cloud-modelarmor` 接入
- 设计上已预留 `AWS / Azure` 扩展位，但当前版本尚未实现

## 开发

```bash
# 安装依赖
pip install -e ".[dev]"

# 运行测试
pytest

# 代码检查
ruff check src/

# 类型检查
mypy src/
```

## 项目结构

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

## 许可证

Apache License 2.0
