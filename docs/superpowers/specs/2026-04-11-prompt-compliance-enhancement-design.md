# Prompt 遵从性检查模块能力增强设计文档

## 1. 背景

当前 `prompt_compliance` 模块已经具备以下基础能力：

- 可插拔的安全护栏集成
- 基于 LLM 的结构化槽位提取
- 镜像式槽位配置存储与加载
- 槽位校验与错误处理

但当前实现仍存在以下问题：

- `errors.py` 中只有 `PromptComplianceError` 形成了完整实现，其他异常语义不完整
- `PromptComplianceProviderConfig` 的命名与实际职责不一致
- `slot_config.py` 的命名无法准确表达其职责
- 配置还没有像 prompt 模块那样统一从 `.env` 读取并带默认值
- 缺少默认 slot 存储目录，不利于开发人员预制默认配置
- 模块虽然基本可用，但整体仍不够开箱即用

本次需求目标是：在不考虑兼容性负担的前提下，整体增强 prompt 遵从性检查模块的命名、配置、目录、异常和装配体验。

## 2. 设计目标

### 2.1 目标

本次增强目标如下：

- 所有 prompt compliance 配置统一支持从 `.env` 读取
- 提供完整默认值，做到最少配置即可使用
- 创建默认 slot 存储目录，便于预制 `slot.json`
- 修正命名不合理的配置模型、模块和异常名称
- 补齐所有有明确场景的异常类型
- 将 slot 文件格式从 `slot.yaml` 统一切换为 `slot.json`
- 保持模块职责清晰，避免引入额外默认 factory

### 2.2 非目标

本次不处理：

- 兼容旧命名、旧文件名、旧接口
- `slot.yaml` 与 `slot.json` 并存
- 新增默认 factory 风格的快捷装配入口
- 重构 LLM 抽象层
- 重构 Prompt 模块

## 3. 设计结论

本次增强做出以下明确设计决策：

1. 配置统一入口为 `PromptComplianceConfig.from_env()`
2. 允许直接重命名现有模型、模块和导出，不保留兼容别名
3. slot 文件从 `slot.yaml` 改为 `slot.json`
4. 默认 slot 根目录为 `./slots`
5. 默认在仓库中创建 `slots/.gitkeep`
6. 调用方自行组装 service 与依赖，不新增 `factory.py`
7. 子组件直接接收各自子配置对象，避免“配置能读取但组件未使用”

## 4. 模块结构调整

建议保留模块主路径：

```text
src/a2a_t/server/prompt_compliance/
```

但调整内部职责如下：

```text
prompt_compliance/
├── __init__.py
├── config.py
├── errors.py
├── extractors.py / extractor.py
├── guardrails.py
├── models.py
├── origin_resolver.py
├── parser.py
├── schema_builder.py
├── service.py
├── slot_schema.py
└── validator.py
```

### 4.1 `config.py`

职责：

- 定义 prompt compliance 配置模型
- 提供 `.env` 读取能力
- 定义默认值

### 4.2 `models.py`

职责：

- 定义运行时数据模型
- 不放配置模型

### 4.3 `guardrails.py`

职责：

- 定义护栏协议
- 提供默认 `noop` 实现
- 提供护栏 provider 注册与创建

### 4.4 `slot_schema.py`

职责：

- 定义 `slot.json` 文件结构模型
- 负责镜像路径定位
- 负责 JSON 加载与 Pydantic 校验

### 4.5 `schema_builder.py`

职责：

- 将 `SlotSchema` 构造成运行时 JSON Schema

### 4.6 `validator.py`

职责：

- 使用 `jsonschema` 对 extracted slots 执行运行时校验

### 4.7 `service.py`

职责：

- 串起 guardrail、prompt origin、slot extraction、slot schema 加载、slot validation
- 做分阶段错误映射

## 5. 命名调整设计

本次允许直接重命名，不保留兼容别名。

### 5.1 配置模型

- `PromptComplianceProviderConfig`
  -> `GuardrailProviderConfig`

- `SlotSchemaConfig`
  -> `SlotStorageConfig`

### 5.2 slot 文件模型与解析器

- `SlotConfig`
  -> `SlotSchema`

- `SlotConfigResolver`
  -> `SlotSchemaResolver`

### 5.3 文件名

- `slot_config.py`
  -> `slot_schema.py`

### 5.4 异常名

- `SlotConfigLoadError`
  -> `SlotSchemaLoadError`

- `SlotConfigValidationError`
  -> `SlotSchemaValidationError`

## 6. 配置设计

## 6.1 总配置入口

建议新增：

```python
PromptComplianceConfig.from_env(env: EnvConfig) -> PromptComplianceConfig
```

并让它成为 prompt compliance 模块唯一默认配置入口。

### 6.2 配置结构

```python
PromptComplianceConfig
├── guardrail: GuardrailProviderConfig
├── slot_extraction: SlotExtractionConfig
├── slot_storage: SlotStorageConfig
└── slot_not_found_policy: str
```

### 6.3 配置分发原则

- `PromptComplianceService` 接收总配置 `PromptComplianceConfig`
- 具体子组件只接收各自子配置：
  - `config.guardrail`
  - `config.slot_extraction`
  - `config.slot_storage`

这样既统一配置来源，又避免子组件依赖整个大配置对象。

## 7. `.env` 配置项设计

### 7.1 安全护栏

```text
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER=noop
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_API_BASE=
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_API_KEY_ENV=
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID=
```

说明：

- 默认 provider 为 `noop`
- 当前阶段优先保证模块开箱即用

### 7.2 槽位提取

```text
A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_PROVIDER=
A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MODEL=
A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TIMEOUT_SECONDS=30
```

### 7.3 槽位存储

```text
A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR=./slots
A2AT_PROMPT_COMPLIANCE_SLOT_FILE_NAME=slot.json
```

### 7.4 运行策略

```text
A2AT_PROMPT_COMPLIANCE_SLOT_NOT_FOUND_POLICY=strict
```

允许值：

- `strict`
- `skip`

默认值：

- `strict`

## 8. slot 文件格式与目录布局

## 8.1 文件格式切换

本次直接将槽位定义文件从：

- `slot.yaml`

切换为：

- `slot.json`

不再保留 YAML 兼容处理。

### 原因

- 存储格式与运行时 JSON Schema 思维一致
- 更适合机器校验、自动生成、格式化、diff
- 避免 YAML 歧义
- 当前版本无人使用，无兼容包袱

## 8.2 默认目录

默认 slot 根目录：

```text
./slots
```

仓库内新增：

```text
slots/.gitkeep
```

### 运行时策略

`SlotSchemaResolver` 初始化时，如果根目录不存在，应自动创建目录。

## 8.3 布局规则

统一布局为：

```text
<slot_root>/<name>/<version>/<language>/slot.json
```

示例：

```text
./slots/network diagnosis/1.0.0/zh-CN/slot.json
```

## 9. 数据模型设计

## 9.1 配置模型

### `GuardrailProviderConfig`

描述安全护栏 provider 配置。

### `SlotExtractionConfig`

描述 LLM 槽位提取配置。

### `SlotStorageConfig`

描述 slot 文件的根目录与文件名配置。

### `PromptComplianceConfig`

描述模块总配置，并负责 `.env` 读取。

## 9.2 运行时模型

继续保留并整理：

- `PromptIdentity`
- `GuardrailResult`
- `SlotExtractionResult`
- `PromptComplianceResult`
- `SlotValidationResult`

这些模型仍放在 `models.py`，与配置模型分离。

## 10. 错误模型设计

建议补齐所有已有明确场景的异常。

### 10.1 基类

```python
class PromptComplianceError(Exception):
    ...
```

建议保留 `context: dict[str, object]`。

### 10.2 护栏阶段

- `GuardrailExecutionError`
- `GuardrailRejectedError`

区别：

- `ExecutionError`：执行失败、超时、网络错误
- `RejectedError`：护栏明确拒绝

### 10.3 processed prompt 与原始 prompt 阶段

- `ProcessedPromptParseError`
- `PromptOriginResolveError`

### 10.4 槽位提取阶段

- `SlotExtractionError`

### 10.5 slot 文件加载与校验阶段

- `SlotSchemaLoadError`
- `SlotSchemaValidationError`

### 10.6 槽位运行时校验阶段

- `SlotValidationError`

## 11. 组件职责设计

## 11.1 `guardrails.py`

保留并调整：

- `SafetyGuardrail`
- `NoopSafetyGuardrail`
- `TransportSafetyGuardrail`
- `SafetyGuardrailFactory`

其中：

```python
SafetyGuardrailFactory.create(config: GuardrailProviderConfig)
```

## 11.2 `slot_schema.py`

建议包含：

- `SlotDefinition`
- `SlotDependencyRule`
- `SlotSchema`
- `SlotSchemaResolver`

职责：

- 读取 `slot.json`
- 校验 JSON 结构
- 映射为稳定的 Python 模型

## 11.3 `schema_builder.py`

接口建议：

```python
def build(self, slot_schema: SlotSchema) -> dict[str, Any]:
    ...
```

职责：

- 把 `SlotSchema` 转为运行时 JSON Schema

## 11.4 `validator.py`

接口建议：

```python
def validate(self, *, extracted_slots: dict[str, object], slot_schema: SlotSchema) -> SlotValidationResult:
    ...
```

职责：

- 用 `jsonschema` 执行 extracted slots 的运行时校验

## 11.5 `extractor.py`

职责：

- 调用 LLM 做结构化槽位提取
- 强约束 JSON 输出结构
- 输出 `SlotExtractionResult`

## 11.6 `service.py`

职责：

- 串联所有子模块
- 做流程编排与错误映射
- 输出 `PromptComplianceResult`

不负责：

- 直接读取 `.env`
- 直接解析 slot 文件
- 直接执行 JSON Schema 构建

## 12. Service 流程设计

建议 `PromptComplianceService.check(...)` 固定按以下顺序执行：

1. 执行输入护栏检查
2. 解析 processed prompt
3. 定位原始 prompt
4. 执行槽位提取
5. 加载 `slot.json`
6. 执行槽位校验
7. 返回结果

### 12.1 输入护栏检查

调用：

```python
guardrail.check(processed_prompt_text, request_metadata)
```

### 12.2 解析 processed prompt

使用 `ProcessedPromptParser` 提取 `PromptIdentity`

### 12.3 定位原始 prompt

使用 `PromptOriginResolver`

### 12.4 槽位提取

将以下两份内容一起提供给 `PromptSlotExtractor`：

- processed prompt
- original prompt

### 12.5 加载 `slot.json`

根据 identity 定位：

```text
<slot_root>/<name>/<version>/<language>/slot.json
```

### 12.6 槽位校验

调用：

```python
SlotValidator.validate(extracted_slots, slot_schema)
```

## 13. slot 文件缺失策略

继续保留：

- `strict`
- `skip`

默认建议：

- `strict`

原因：

- 模块核心职责就是遵从性检查
- 默认跳过会削弱模块价值

## 14. 开箱即用设计

本次增强的核心不是增加新功能，而是让默认链路自然可用。

开箱即用的关键措施：

- 所有配置统一从 `.env` 获取
- 所有配置提供默认值
- 默认 slot 根目录存在
- 默认文件名固定为 `slot.json`
- 命名清晰
- 错误完整
- service 流程稳定

建议默认调用链路：

```python
env = EnvConfig.load(env_path=Path(".env"))
config = PromptComplianceConfig.from_env(env)

guardrail = SafetyGuardrailFactory.create(config.guardrail)
slot_schema_resolver = SlotSchemaResolver(config.slot_storage)
slot_extractor = PromptSlotExtractor(config.slot_extraction)

service = PromptComplianceService(
    config=config,
    guardrail=guardrail,
    processed_prompt_parser=ProcessedPromptParser(),
    prompt_origin_resolver=PromptOriginResolver(...),
    slot_extractor=slot_extractor,
    slot_schema_resolver=slot_schema_resolver,
    slot_validator=SlotValidator(),
)
```

## 15. README / 导出同步项

本次设计落地后，README 和对外导出需要同步更新：

- 所有 `slot.yaml` 改为 `slot.json`
- 所有旧命名替换为新命名
- 增加 `.env` 示例
- 增加默认 `./slots` 目录说明
- 说明模块推荐装配方式

不再导出旧名字：

- `PromptComplianceProviderConfig`
- `SlotConfig`
- `SlotConfigResolver`

## 16. 测试建议

建议新增或调整测试覆盖：

- `.env` 配置读取
- 默认 slot 根目录存在
- `slot.json` 文件加载
- `slot.json` Pydantic 结构校验
- 各异常类型的使用场景
- service 各阶段错误映射
- `strict/skip` 行为分支
- README / 示例同步断言

## 17. 风险与注意事项

- 本次直接放弃兼容性，落地时必须同步更新所有测试、文档和导出
- `slot.json` 切换后，所有示例和说明必须同步替换
- `strict/skip` 行为需在 service 层保持语义稳定
- 异常补齐后，测试断言应尽量转向结构化错误语义，而不是模糊字符串匹配

## 18. 最终结论

本次增强建议整体收敛为：

- **配置统一入口**：`PromptComplianceConfig.from_env()`
- **slot 文件统一切换**：`slot.json`
- **默认目录可预制**：`./slots`
- **命名整体纠偏**：guardrail / slot schema / slot storage
- **异常完整落地**
- **service 保持编排职责**
- **不新增默认 factory**

这样可以在不引入兼容负担的前提下，让 prompt 遵从性检查模块在命名、配置、目录、异常和使用体验上整体提升到“更开箱即用”的状态。
