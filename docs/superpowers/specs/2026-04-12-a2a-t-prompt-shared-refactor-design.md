# A2A-T Prompt 共享能力重构设计文档

## 1. 背景

当前仓库中存在两条相互接近但分散实现的能力链路：

1. client 侧 A2A-T Prompt Generation
   - 从自然语言或 JSON 输入生成 A2A-T 任务请求
   - 依赖场景识别、slot 提取、slot 校验、资源加载

2. server 侧 Prompt Compliance
   - 对 client 传来的 A2A-T 任务请求执行 guardrail 校验和 slot 校验
   - 依赖资源加载、slot 提取、slot 校验、front matter 解析

这两条链路在以下方面已经出现明显重复：

- 资源文件读取
- 场景与 slot 相关模型
- 基于 LLM 的结构化分析
- slot 校验
- 统一错误模型与结果映射

当前设计目标是在不破坏现有 client / server 对外边界的前提下，将公共能力上提到 `a2a_t.prompt` 包内，形成统一的共享实现。

## 2. 设计目标

1. 消除 client/server 间重复实现，建立共享 prompt 公共能力层。
2. 将共享能力统一收敛到 `src/a2a_t/prompt/` 下，避免 client/server 各自维护同类逻辑。
3. 统一资源模型、资源路径规则、slot schema 模型、slot 校验结果模型。
4. 统一 client/server 的 LLM 调用基线，全部收敛到 `LLMClient`。
5. 保持 client/server 各自的业务编排与对外返回模型仍独立。

## 3. 重构范围

### 3.1 本次范围

- 重构 `a2a_t.prompt` 包，拆分为共享子包
- 上提共享资源加载能力
- 上提共享分析能力
- 上提共享校验能力
- 调整 client prompt generation 对共享层的依赖
- 调整 server prompt compliance 对共享层的依赖
- 统一 slot schema 资源模型
- 统一 front matter 身份字段

### 3.2 不在本次范围

- 改造服务端对外业务语义本身
- 改造 client 对外 API 名称
- 支持在线资源发布平台
- 支持更多非本地资源来源的真实接入实现
- 启用 `allowed_values / range / pattern` 的运行时校验主路径

## 4. 新的代码架构

### 4.1 总体结构

重构后代码结构分为三层：

1. 共享层：`src/a2a_t/prompt/`
2. client 编排层：`src/a2a_t/client/prompt/`
3. server 编排层：`src/a2a_t/server/prompt_compliance/`

其中：

- 共享层只放公共能力，不放 client/server 业务编排
- client/server 只保留各自的入口、流程编排、结果映射

### 4.2 共享层包结构

共享层统一拆为以下子包：

```text
src/a2a_t/prompt/
  common/
  resources/
  analysis/
  validation/
```

职责如下：

- `common`
  - 公共配置
  - 公共常量
  - 公共错误基类
  - 少量共享模型
  - 少量通用工具函数
- `resources`
  - 资源来源管理
  - catalog / registry / provider
  - 本地落盘
  - 场景、模板、guidance prompt、slot schema 的读取与解析
- `analysis`
  - 场景识别
  - slot 提取
  - 与分析动作直接相关的 messages 组装
  - 与分析动作直接相关的 structured `json_schema` 构建
- `validation`
  - slot 校验
  - guardrail 校验
  - 校验结果模型

### 4.3 client 与 server 的边界

#### client 编排层

client 层保留：

- `PromptClient.generate_a2a_t_prompt(...)`
- client 侧编排 orchestrator
- client 侧返回对象 `PromptGenerationResult`
- client 侧失败码、阶段码、结果映射
- 最终 markdown prompt 渲染

#### server 编排层

server 层保留：

- `PromptComplianceService`
- server 侧编排顺序
- server 侧结果对象 `PromptComplianceResult`
- server 侧 stage / error_code 映射
- server 对 guardrail / slot validation 的通过与拒绝语义

## 5. 共享资源设计

### 5.1 统一资源根目录

共享资源统一放在：

```text
package_data/prompt_resources/
```

### 5.2 统一资源路径

```text
package_data/prompt_resources/
  scenarios/
    <version>/<language>/scenarios.json
  templates/
    <scenario_code>/<version>/<language>/template.md
  prompts/
    scenario_recognition/<version>/<language>/system.md
    scenario_recognition/<version>/<language>/user.md
    slot_extraction/<version>/<language>/system.md
    slot_extraction/<version>/<language>/user.md
  slots/
    <scenario_code>/<version>/<language>/slot.json
```

规则如下：

- 模板与 slot schema 统一按 `scenario_code + version + language` 定位
- guidance prompt 统一按 `分析动作 + version + language` 定位
- 场景表统一按 `version + language` 定位

### 5.3 资源版本与语言来源

为保证 `scenario_code + version + language` 这套资源定位规则可以直接落地，本次设计约定 `A2ATConfig` 顶层新增以下配置字段：

- `language`
- `prompt_resource_version`

建议语义如下：

- `language`
  - 表示 client 生成 prompt 时使用的目标语言
  - 缺失时回退到 `en-US`
- `prompt_resource_version`
  - 表示本次生成或校验链路统一使用的 prompt 资源版本
  - 缺失时回退到默认版本 `0.0.1`

补充规则如下：

- client 侧生成链路从 `A2ATConfig` 顶层读取：
  - `language`
  - `prompt_resource_version`
- client 最终生成的 front matter 中，`version` 固定写入 `prompt_resource_version`
- server 侧遵从校验链路从 front matter 中读取：
  - `scenario_code`
  - `language`
  - `version`
- server 不再自行推导版本，也不再从其他配置源覆盖 front matter 中的 `version`
- 本期不做版本 fallback
  - 若目标 `version` 下资源不存在，直接返回资源缺失错误
- 本期只做语言 fallback，且 fallback 目标固定为 `en-US`

说明：

- 当前仓库中的 `A2ATConfig` 代码尚未包含上述两个顶层字段
- 本文档将其视为本次重构需要补齐的前置配置契约

### 5.4 `resources` 包内部结构

`resources` 包内不采用一个“大而全 resolver”，而采用：

- 多个专用 loader
- 一个轻量 registry

建议结构：

```text
src/a2a_t/prompt/resources/
  __init__.py
  cache.py
  catalog.py
  catalog_registry.py
  providers.py
  parser.py
  registry.py
  scenario_loader.py
  template_loader.py
  prompt_resource_loader.py
  slot_schema_loader.py
```

职责如下：

- `registry.py`
  - 根据 `scenario_code / version / language / stage` 做路由
  - 负责 fallback 决策
  - 不负责具体文件解析
- `cache.py`
  - 负责资源内容缓存
- `catalog.py`
  - 负责描述资源从哪里来
- `catalog_registry.py`
  - 负责 catalog 注册与选择
- `providers.py`
  - 负责本地、URL 等来源的具体拉取
- `parser.py`
  - 负责将资源内容解析成不同资源对象
- `scenario_loader.py`
  - 加载 `scenarios/<version>/<language>/scenarios.json`
- `template_loader.py`
  - 加载 `templates/<scenario_code>/<version>/<language>/template.md`
- `prompt_resource_loader.py`
  - 加载 `prompts/<analysis_action>/<version>/<language>/*`
- `slot_schema_loader.py`
  - 加载 `slots/<scenario_code>/<version>/<language>/slot.json`

### 5.5 与现有 `PromptLoader` 的边界

当前 `src/a2a_t/prompt/` 中已有的 `PromptLoader` / `MarkdownPromptParser` 继续保留，但职责边界需要明确收紧：

- `PromptLoader`
  - 继续只服务“带 front matter 的已发布 prompt 资产”
- `MarkdownPromptParser`
  - 继续只解析“带 front matter 的 markdown prompt”
- 新增的共享资源加载链路
  - 不经过 `MarkdownPromptParser`
  - 不要求 `template.md`、`system.md`、`user.md` 自带 front matter
  - 由 `resources` 子包下的专用 loader 直接读取和解析

具体边界如下：

- `template.md`
  - 视为纯模板正文资源
- `prompts/*/*.md`
  - 视为纯指导 prompt 文本资源
- `slot.json`
  - 视为结构化 JSON 资源
- client 生成后的最终 markdown prompt
  - 仍然是带 front matter 的正式 prompt 文本
  - server 的 `ProcessedPromptParser` 负责解析它

实现约束如下：

- 不复用现有 `PromptLoader + MarkdownPromptParser` 直接加载上述三类无 front matter 资源
- `catalog.py / catalog_registry.py / providers.py / cache.py` 可以继续作为资源来源管理底座复用
- 但是否使用这些底座能力，由 `resources` 子包内的专用 loader 决定

### 5.6 `resources` 层输入输出模型

为保证共享 `resources` 子包可直接指导编码，约定以下最小模型：

```python
@dataclass(slots=True)
class ResourceIdentity:
    scenario_code: str | None
    version: str
    language: str
```

```python
@dataclass(slots=True)
class PromptMessages:
    system_prompt: str
    user_prompt: str
```

```python
@dataclass(slots=True)
class ResolvedLanguage:
    requested_language: str
    resolved_language: str
    fallback_applied: bool
```

专用 loader 接口约定如下：

```python
class ScenarioLoader:
    def load(self, *, version: str, language: str) -> list[ScenarioDefinition]: ...
```

```python
class TemplateLoader:
    def load(self, *, scenario_code: str, version: str, language: str) -> str: ...
```

```python
class PromptResourceLoader:
    def load(self, *, analysis_action: str, version: str, language: str) -> PromptMessages: ...
```

```python
class SlotSchemaLoader:
    def load(self, *, scenario_code: str, version: str, language: str) -> SlotSchema: ...
```

`registry.py` 只负责：

- 根据 client/server 当前场景，决定是否执行语言 fallback
- 输出最终使用语言
- 驱动上述专用 loader

`registry.py` 不负责：

- 解析 markdown front matter
- 拼装 LLM messages
- 构造运行时业务结果对象

## 6. 统一 slot schema 设计

### 6.1 顶层模型

统一后的 `slot.json` 顶层字段为：

- `scenario_code`
- `version`
- `slots`

### 6.2 单 slot 模型

统一后的单 slot 字段如下：

- `name`
- `required`
- `description`
- `example`
- `value_constraint`
- `type`
- `allowed_values`
- `range`
- `pattern`

### 6.3 统一模型定义

```python
@dataclass(slots=True)
class SlotRange:
    min: float | int | None
    max: float | int | None
```

```python
@dataclass(slots=True)
class SlotDefinition:
    name: str
    required: bool
    description: str
    example: str
    value_constraint: str
    type: str | None
    allowed_values: list[object] | None
    range: SlotRange | None
    pattern: str | None
```

```python
@dataclass(slots=True)
class SlotSchema:
    scenario_code: str
    version: str
    slots: list[SlotDefinition]
```

### 6.4 使用规则

client 本期使用字段：

- `name`
- `required`
- `description`
- `example`
- `value_constraint`

server 本期使用字段：

- `name`
- `required`
- `description`
- `example`
- `value_constraint`

保留但本期暂不启用的字段：

- `type`
- `allowed_values`
- `range`
- `pattern`

## 7. 统一 front matter 设计

client 最终输出必须是带 front matter 的 markdown prompt。

统一后的 front matter 字段至少包括：

- `scenario_code`
- `language`
- `version`
- `description`

示例：

```markdown
---
scenario_code: energy_saving
language: zh-CN
version: 0.0.1
description: 用于能耗分析与节能建议生成的 A2A-T 任务请求
---

请为深圳南山A区机房创建节能分析任务，分析时间范围为 2026-04-01 至 2026-04-07，重点关注供配电系统和制冷系统，并输出优化建议。
```

补充规则：

- front matter 中不再使用 `name`
- server 后续统一按 `scenario_code + language + version` 解析身份

## 8. `prompt` 包下需要迁移 / 新增的代码

### 8.1 需要迁移的代码

从 `src/a2a_t/server/prompt_compliance/` 中迁移或重写到共享层的能力：

- `slot_schema.py`
  - 不直接原样迁移
  - 抽取统一 `SlotSchema` 模型与 loader 职责
- `schema_builder.py`
  - 不直接原样迁移
  - 拆成 analysis 侧的 structured schema builder
- `validator.py`
  - 不直接原样迁移
  - 重写为统一 `SlotValidator`
- `guardrails.py`
  - 上提到 `prompt.validation`
- `guardrail_providers.py`
  - 上提到 `prompt.validation`

### 8.2 需要新增的代码

#### `a2a_t.prompt.common`

- `config.py`
- `constants.py`
- `errors.py`
- `models.py`

#### `a2a_t.prompt.resources`

- `models.py`
- `errors.py`
- `registry.py`
- `scenario_loader.py`
- `template_loader.py`
- `prompt_resource_loader.py`
- `slot_schema_loader.py`

#### `a2a_t.prompt.analysis`

- `models.py`
- `errors.py`
- `scenario_recognizer.py`
- `slot_extractor.py`
- `message_builder.py`
- `json_schema_builder.py`

#### `a2a_t.prompt.validation`

- `models.py`
- `errors.py`
- `slot_validator.py`
- `guardrails.py`
- `guardrail_providers.py`

## 9. server 端需要适配修改的代码

### 9.1 需要修改的文件

- `src/a2a_t/server/prompt_compliance/parser.py`
- `src/a2a_t/server/prompt_compliance/models.py`
- `src/a2a_t/server/prompt_compliance/extractor.py`
- `src/a2a_t/server/prompt_compliance/service.py`
- `src/a2a_t/server/prompt_compliance/errors.py`
- `src/a2a_t/server/prompt_compliance/__init__.py`
- `src/a2a_t/server/prompt_compliance/config.py`
- `src/a2a_t/server/prompt_compliance/origin_resolver.py`
- `src/a2a_t/server/prompt_compliance/slot_schema.py`
- `src/a2a_t/server/prompt_compliance/schema_builder.py`
- `src/a2a_t/server/prompt_compliance/validator.py`

### 9.2 具体适配项

1. `ProcessedPromptParser`
   - 输入仍为 markdown prompt
   - front matter 身份字段改为：
     - `scenario_code`
     - `language`
     - `version`
   - 不再解析 `name`

2. `PromptIdentity`
   - 字段从 `name/language/version` 改为：
     - `scenario_code`
     - `language`
     - `version`

3. `PromptOriginResolver`
   - 从 server 主链路中移除
   - `PromptComplianceService` 不再依赖 original prompt 回取
   - `origin_resolver.py` 不再作为 prompt compliance 主路径必需文件
   - 若仓库中仍保留该文件，仅允许作为过渡兼容代码存在，不得再被主链路调用

4. `PromptSlotExtractor`
   - 不再依赖 `LLMAdapter`
   - 改为依赖共享 `LLMClient`
   - 不再接收 `original_prompt`
   - 改为接收：
     - `processed_prompt_text`
     - `template_text`
     - `slot_schema`
     - `system_prompt`
     - `user_prompt`
   - 复用 `a2a_t.prompt.analysis.slot_extractor`

5. `SlotSchemaResolver / SlotValidator`
   - 不再使用 server 侧本地实现
   - 改为复用 `a2a_t.prompt.resources.slot_schema_loader`
   - 改为复用 `a2a_t.prompt.validation.slot_validator`

6. `PromptComplianceService`
   - 保留 server 编排职责
   - 内部依赖切换到共享层
   - 构造依赖调整为：
     - 保留 `guardrail`
     - 保留 `parser`
     - 移除 `origin_resolver`
     - 使用共享 `slot_extractor`
     - 使用共享 `template_loader`
     - 使用共享 `prompt_resource_loader`
     - 使用共享 `slot_schema_loader`
     - 使用共享 `slot_validator`

7. server 结果模型
   - `SlotExtractionResult`
     - 不再保留 `notes`
     - 不再保留 `confidence`
     - 统一收敛为共享 `slots + slot_errors`
   - `PromptComplianceResult`
     - 对外主字段保持：
       - `passed`
       - `stage`
       - `extracted_slots`
       - `error_code`
       - `error_message`
     - 不再要求保留 `notes`
     - 不再要求保留 `confidence`
   - server 若仍需记录原始 LLM 诊断信息，统一走 debug 日志，不进入主结果模型

## 10. client 端需要新增的代码

### 10.1 保留的 client 编排层

建议保留：

```text
src/a2a_t/client/
  prompt_client.py
  prompt/
    __init__.py
    prompt_generation_orchestrator.py
    models.py
    constants.py
    input_normalizer.py
    renderer.py
```

### 10.2 需要删除或取消的 client 专属实现

以下能力不再保留在 client 私有目录：

- `scenario_registry.py`
- `scenario_recognizer.py`
- `slot_extractor.py`
- `validator.py`

这些职责统一改为依赖：

- `a2a_t.prompt.resources`
- `a2a_t.prompt.analysis`
- `a2a_t.prompt.validation`

### 10.3 client 新增职责

client 端还需要新增：

1. 版本参与资源定位
   - `scenario_code + version + language`
   - `version` 取自 `A2ATConfig.prompt_resource_version`

2. front matter 渲染
   - 将最终输出从“纯 prompt_text”升级为“带 front matter 的 markdown prompt”

3. `PromptGenerationResult` 适配
   - 继续保留 client 对外结构
   - 若需要 `missing_required_fields`，由 client 从共享 `slot_errors` 派生

## 11. client 端业务流程

client 端最终流程如下：

1. 接收 `user_input`
2. 归一化输入，识别 `input_kind`
3. 从 `A2ATConfig` 顶层获取：
   - `language`
   - `prompt_resource_version`
4. 对配置执行归一化：
   - `language` 缺失时回退 `en-US`
   - `prompt_resource_version` 缺失时回退 `0.0.1`
5. 通过共享 `resources` 加载：
   - `scenarios.json`
   - `scenario_recognition` prompt
   - 若目标语言资源缺失，则在同一 `version` 下回退到 `en-US`
6. 调用共享 `analysis.scenario_recognizer`
7. 得到 `scenario_code`
8. 根据 `scenario_code + version + language` 通过共享 `resources` 加载：
   - `template.md`
   - `slot.json`
   - `slot_extraction` prompt
   - 若目标语言资源缺失，则在同一 `version` 下回退到 `en-US`
9. 调用共享 `analysis.slot_extractor`
10. 调用共享 `validation.slot_validator`
11. 若校验失败：
    - 组装 client 失败结果
12. 若校验成功：
    - 用 client `renderer` 填充模板正文
    - 生成 front matter
      - `scenario_code = 第一阶段识别结果`
      - `language = 最终生效语言`
      - `version = prompt_resource_version`
    - 拼装最终 markdown prompt
13. 返回 `PromptGenerationResult`

## 12. server 端业务流程

server 端最终流程如下：

1. 接收 client 传来的 markdown prompt
2. 调用共享 `validation.guardrail`
3. 解析 front matter，得到：
   - `scenario_code`
   - `language`
   - `version`
4. 通过共享 `resources` 按 `scenario_code + version + language` 加载：
   - `template.md`
   - `slot.json`
   - `slot_extraction` prompt
   - server 不做版本 fallback
   - server 不再做语言二次 fallback，直接按 front matter 中的 `language` 精确加载
5. 调用共享 `analysis.slot_extractor`
   - 将请求 prompt、`template.md`、`slot.json`、`slot_extraction` prompt 一并传入
   - 由 `slot_extractor` 基于共享 structured 调用链路提取 slot
6. 调用共享 `validation.slot_validator`
7. 若校验失败：
   - 组装 `PromptComplianceResult`
8. 若校验成功：
   - 返回通过结果

补充说明：

- server 本期 slot 校验主路径不再使用 `allowed_values / range / pattern`
- server 本期也采用：
  - LLM 提取 slot
  - 基于 `value_constraint` 判断合法性

## 13. 共享分析能力规格

### 13.1 场景识别器

场景定义模型：

```python
@dataclass(slots=True)
class ScenarioDefinition:
    scenario_code: str
    scenario_name: str
    description: str
    example: str
```

建议模型：

```python
@dataclass(slots=True)
class ScenarioRecognitionResult:
    matched: bool
    scenario_code: str | None
    error_message: str | None
```

建议接口：

```python
class ScenarioRecognizer:
    def recognize(
        self,
        *,
        normalized_input: str,
        scenarios: list[ScenarioDefinition],
        language: str,
    ) -> ScenarioRecognitionResult: ...
```

### 13.2 slot 提取器

建议模型：

```python
@dataclass(slots=True)
class SlotExtractionResult:
    slots: dict[str, str | None]
    slot_errors: list[SlotValidationError]
```

建议接口：

```python
class SlotExtractor:
    def extract(
        self,
        *,
        normalized_input: str,
        scenario_code: str,
        version: str,
        language: str,
        template_text: str,
        slot_schema: SlotSchema,
        system_prompt: str,
        user_prompt: str,
    ) -> SlotExtractionResult: ...
```

## 14. 共享校验能力规格

### 14.1 Slot 校验结果模型

```python
@dataclass(slots=True)
class SlotValidationError:
    slot_name: str
    code: str
    message: str
```

```python
@dataclass(slots=True)
class SlotValidationResult:
    passed: bool
    slot_errors: list[SlotValidationError]
```

规则如下：

- 不返回 `missing_required_fields`
- 必填缺失通过 `code = "missing_input"` 表达
- 值不合法通过 `code = "invalid_value"` 表达
- 上层若需要 `missing_required_fields`，自行从 `slot_errors` 派生

### 14.2 SlotValidator 接口

```python
class SlotValidator:
    def validate(
        self,
        *,
        slots: dict[str, str | None],
        slot_errors: list[SlotValidationError],
        slot_schema: SlotSchema,
    ) -> SlotValidationResult: ...
```

### 14.3 Guardrail 接口

```python
class SafetyGuardrail:
    def check(
        self,
        prompt_text: str,
        context: dict[str, object] | None = None,
    ) -> GuardrailResult: ...
```

说明：

- guardrail 与 slot validation 同处 `validation` 包
- 但两者是独立能力，不共用返回模型

## 15. client 结果模型规格

### 15.1 PromptGenerationResult

```python
@dataclass(slots=True)
class PromptGenerationResult:
    success: bool
    prompt_text: str | None
    scenario: ScenarioResolution | None
    language: str
    input_kind: str
    slots: dict[str, str | None]
    validation: ValidationResult
    failure: PromptGenerationFailure | None
```

### 15.2 ValidationResult

```python
@dataclass(slots=True)
class ValidationResult:
    passed: bool
    missing_required_fields: list[str]
    slot_errors: list[SlotError]
```

说明：

- 这是 client 对外模型
- `missing_required_fields` 不由共享层直接返回
- 由 client 从共享 `SlotValidationResult.slot_errors` 派生

## 16. server 结果模型规格

### 16.1 PromptComplianceResult

server 继续保留 `PromptComplianceResult` 作为对外编排结果。

建议保持：

- `passed`
- `stage`
- `extracted_slots`
- `error_code`
- `error_message`

映射规则：

- 共享 `SlotValidationResult.passed = false`
  - server 映射为 `slot_validation_error`
- 共享 `SlotValidationResult.slot_errors`
  - server 可聚合为 `error_message`
  - 也可在后续扩展为结构化透出

## 17. 文件迁移清单

### 17.1 从 server 侧迁移或重写

- `server/prompt_compliance/slot_schema.py`
- `server/prompt_compliance/schema_builder.py`
- `server/prompt_compliance/validator.py`
- `server/prompt_compliance/guardrails.py`
- `server/prompt_compliance/guardrail_providers.py`
- `server/prompt_compliance/extractor.py`

### 17.2 从 server 主链路移除或废弃

- `server/prompt_compliance/origin_resolver.py`
  - 不再进入主链路
- `server/prompt_compliance/service.py` 中对 `origin_resolver` 的构造和调用
  - 必须移除

### 17.3 client 侧删除或收缩

- 删除：
  - `client/prompt/scenario_registry.py`
  - `client/prompt/scenario_recognizer.py`
  - `client/prompt/slot_extractor.py`
  - `client/prompt/validator.py`
- 保留并改造：
  - `client/prompt/prompt_generation_orchestrator.py`
  - `client/prompt/models.py`
  - `client/prompt/renderer.py`
  - `client/prompt/input_normalizer.py`

## 18. 风险与约束

1. 这是一次跨 client/server 的共享层重构，改动面较大。
2. 统一 schema 后，旧服务端 `slot.json` 需要迁移。
3. server front matter 解析身份字段会发生变化，需要同步调整测试数据。
4. client 最终输出从纯文本升级为 markdown prompt，会影响联调输入假设。

## 19. 测试要求

### 19.1 共享层

- 资源定位与 fallback
- `A2ATConfig.language` 与 `A2ATConfig.prompt_resource_version` 读取
- 统一 slot schema 加载
- 场景识别
- slot 提取
- slot 校验
- guardrail

### 19.2 client

- prompt generation 两阶段成功链路
- 失败链路
- front matter 渲染
- markdown 最终输出格式

### 19.3 server

- front matter 解析
- 新身份字段链路
- guardrail + slot validation 编排
- 不再依赖 `origin_resolver` 的主链路回归
- 与共享层对接后的回归

## 20. 结论

本次重构的核心是：

- 将 prompt 公共能力统一上提到 `a2a_t.prompt`
- 统一资源模型、slot schema、LLM 调用基线、slot 校验结果模型
- client/server 仅保留各自编排与对外返回模型

最终目标不是简单搬运现有代码，而是建立一套新的共享实现，使 client prompt generation 与 server prompt compliance 在同一资源体系、同一分析体系、同一校验体系上运行。
