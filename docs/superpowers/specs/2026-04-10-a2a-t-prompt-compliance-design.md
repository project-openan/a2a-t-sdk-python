# A2A-T Prompt 模板遵从性检查设计文档

## 1. 背景

本文档定义 A2A-T 服务端的 Prompt 模板遵从性检查流程。

一次完整请求的处理链路如下：

1. 客户端通过 `prompt_catalog + prompt_loader` 获取一份原始 prompt。
2. 客户端基于这份原始 prompt 进行加工，并向服务端发送仅包含加工后 prompt 的请求。
3. 服务端首先对加工后 prompt 执行安全护栏检查。
4. 安全护栏通过后，服务端从加工后 prompt 中解析 front matter。
5. 服务端基于 front matter，再次通过 `prompt_catalog + prompt_loader` 回取原始 prompt。
6. 服务端将加工后 prompt 与原始 prompt 一并提交给 LLM 做槽位提取。
7. 服务端使用同一份 front matter 身份信息定位镜像的槽位配置文件。
8. 服务端根据槽位配置校验提取结果。
9. 只有校验通过的请求才继续后续任务执行。

本设计覆盖以下内容：

- 可插拔的安全护栏集成
- 基于 LLM 的结构化槽位提取
- 镜像式槽位配置存储与加载
- 槽位校验与错误处理

本设计不在当前任务中修改现有 prompt 缓存布局实现，只记录后续兼容性影响。

## 2. 范围

### 2.1 本次范围

- 在服务端增加独立的 prompt 遵从性检查子系统
- 支持可插拔的安全护栏适配器
- 支持通过 `openai`、`anthropic`、`google` 三类 LLM 提供方执行槽位提取
- 基于加工后 prompt 的 front matter 回取原始 prompt
- 定义镜像式槽位配置路径规则
- 支持必填、类型、枚举、范围、正则、依赖关系校验
- 返回统一的遵从性检查结果与错误信息

### 2.2 不在本次范围

- 本任务内不修改现有 prompt 缓存实现
- 不修改客户端的 prompt 加工逻辑
- 不设计超出本需求范围的通用规则引擎
- 不在本文档中展开实现计划或代码改动

## 3. 现有上下文

当前代码库已经具备以下相关能力：

- `src/a2a_t/prompt` 下的 prompt catalog 与加载抽象
- `src/a2a_t/llm` 下的 LLM 抽象
- `src/a2a_t/server` 下的服务端入口

相关文件包括：

- `src/a2a_t/prompt/catalog.py`
- `src/a2a_t/prompt/loader.py`
- `src/a2a_t/prompt/models.py`
- `src/a2a_t/llm/base.py`
- `src/a2a_t/llm/factory.py`
- `src/a2a_t/server/prompt_handler.py`

新设计应建立在这些现有边界之上扩展，而不是绕开现有组件另起一套实现。

## 4. 设计目标

1. 在可确定的环节尽量保持流程的确定性。
2. 使用 front matter 作为原始 prompt 回取和槽位配置定位的权威身份信息。
3. 将 LLM 的职责限制在槽位提取，而不是规则选择。
4. 允许不同安全护栏产品通过统一契约接入。
5. 复用现有 LLM 抽象层，并在此基础上扩展三类目标提供方。
6. 使大量任务的槽位配置具备可管理性。
7. 保持服务端入口轻量，将复杂逻辑拆入职责单一的组件。

## 5. 总体架构

本设计采用分层的遵从性检查管线。

### 5.1 组件划分

1. `ProcessedPromptParser`
   - 负责从服务端收到的加工后 prompt 中解析 front matter
   - 输出后续流程需要的 prompt 身份字段

2. `PromptOriginResolver`
   - 负责基于 front matter，通过 `prompt_catalog + prompt_loader` 回取原始 prompt

3. `SafetyGuardrail`
   - 负责对加工后 prompt 执行第一层内容风险检查

4. `PromptSlotExtractor`
   - 负责将加工后 prompt 与原始 prompt 一起提交给 LLM
   - 输出结构化槽位提取结果

5. `SlotConfigResolver`
   - 负责根据 prompt 身份信息定位槽位配置文件

6. `SlotValidator`
   - 负责根据槽位配置规则校验提取结果

7. `PromptComplianceService`
   - 负责串联整体流程并输出统一结果

### 5.2 请求处理流程

1. 接收客户端传入的加工后 prompt
2. 执行 `SafetyGuardrail.check(processed_prompt)`
3. 解析加工后 prompt 中的 front matter
4. 根据 front matter 回取原始 prompt
5. 使用两份 prompt 执行槽位提取
6. 定位镜像式槽位配置
7. 校验提取出的槽位
8. 返回通过结果或结构化错误

## 6. 运行时契约

### 6.1 遵从性输入

```python
@dataclass(slots=True)
class ComplianceInput:
    processed_prompt_text: str
    request_metadata: dict[str, object] | None = None
```

其中 `processed_prompt_text` 是该子系统的核心输入。

### 6.2 加工后 Prompt 解析

```python
class ProcessedPromptParser(Protocol):
    def parse(self, processed_prompt_text: str) -> PromptIdentity: ...
```

```python
@dataclass(slots=True)
class PromptIdentity:
    name: str
    language: str
    version: str
```

### 6.3 原始 Prompt 回取

```python
class PromptOriginResolver(Protocol):
    def resolve(self, identity: PromptIdentity) -> Prompt: ...
```

该解析器必须通过现有 prompt catalog 与 loader 抽象工作，而不是直接做文件级读取。

### 6.4 安全护栏

```python
class SafetyGuardrail(Protocol):
    async def check(self, prompt_text: str, context: GuardrailContext) -> GuardrailResult: ...
```

```python
@dataclass(slots=True)
class GuardrailResult:
    passed: bool
    category: str | None = None
    reason: str | None = None
    raw_response: dict[str, object] | None = None
```

### 6.5 槽位提取

```python
class PromptSlotExtractor(Protocol):
    async def extract(
        self,
        *,
        original_prompt: Prompt,
        processed_prompt_text: str,
        context: ExtractionContext,
    ) -> SlotExtractionResult: ...
```

```python
@dataclass(slots=True)
class SlotExtractionResult:
    slots: dict[str, object]
    notes: list[str]
    confidence: float | None = None
    raw_response: dict[str, object] | None = None
```

提取器只负责结构化槽位提取，不负责选择规则，也不负责决定是否跳过槽位校验。

### 6.6 槽位配置定位

```python
class SlotConfigResolver(Protocol):
    def resolve(self, identity: PromptIdentity) -> SlotConfig | None: ...
```

### 6.7 槽位校验

```python
class SlotValidator(Protocol):
    def validate(
        self,
        *,
        extracted_slots: dict[str, object],
        slot_config: SlotConfig,
        context: ValidationContext,
    ) -> SlotValidationResult: ...
```

## 7. 槽位配置路径规划

槽位配置的存储路径必须与 prompt 身份镜像对应。

### 7.1 目标目录结构

```text
<cache_root>/
  prompts/
    <prompt_name>/
      <version>/
        <language>/
          prompt.md
          metadata.json
  slots/
    <prompt_name>/
      <version>/
        <language>/
          slot.yaml
```

示例：

```text
<cache_root>/prompts/network diagnosis/1.0.0/zh-CN/prompt.md
<cache_root>/prompts/network diagnosis/1.0.0/zh-CN/metadata.json
<cache_root>/slots/network diagnosis/1.0.0/zh-CN/slot.yaml
```

### 7.2 设计决策

上述槽位路径规则在本特性设计中为强制规范。

原因如下：

1. prompt 身份信息可以确定性地定位对应槽位配置。
2. prompt 资产与校验规则保持物理镜像关系，便于运维与排障。
3. 后续 prompt 缓存布局调整时，可以向同一套身份结构对齐。

### 7.3 兼容性说明

当前 prompt 缓存布局可能尚未完全匹配该目标结构。本次任务先将 `slots` 的目标路径规则固化进设计文档，prompt 缓存实现是否对齐，留待后续任务处理。

## 8. 槽位配置文件格式

槽位配置文件使用 YAML。

### 8.0 校验分层策略

槽位配置相关校验分为两层：

1. 使用 `Pydantic` 校验 `slot.yaml` 文件格式本身
2. 使用 `jsonschema` 执行运行时槽位规则校验

这样分层的原因如下：

1. `slot.yaml` 本身属于配置文件，适合先映射为稳定的 Python 配置模型，并通过 `Pydantic` 校验字段是否完整、类型是否正确、结构是否符合预期。
2. 运行时槽位校验面对的是动态提取结果，`jsonschema` 更适合表达类型、枚举、范围、正则、条件依赖等规则。
3. 将“配置文件格式是否合法”和“提取结果是否满足规则”拆开后，错误边界更清晰，调试与测试都更直接。

### 8.1 示例

```yaml
prompt_identity:
  name: "network_device_query"
  language: "zh-CN"
  version: "1.0.0"

slots:
  - name: "device_type"
    required: true
    type: "string"

  - name: "location"
    required: false
    type: "string"

  - name: "operation"
    required: true
    type: "enum"
    allowed_values:
      - "query"
      - "restart"
      - "diagnose"

rules:
  - type: "dependency"
    when:
      slot: "operation"
      equals: "restart"
    requires:
      - "device_type"
      - "location"
```

### 8.2 支持的单槽位约束

- `required`
- `type`
- `allowed_values`
- `range`
- `pattern`

### 8.3 支持的跨槽位规则

首期支持：

- `dependency`

语义示例：

- 当槽位 `A` 出现且满足指定值时，槽位 `B` 必须同时存在。

后续新增规则类型时，不需要修改顶层文件结构。

### 8.4 `slot.yaml` 配置模型校验

`slot.yaml` 加载后，首先应映射到 `Pydantic` 模型，并在此阶段完成配置结构校验。

建议覆盖：

- `prompt_identity.name/language/version` 是否存在且为合法字符串
- `slots` 是否为列表
- 每个槽位项是否包含合法的 `name`
- `type` 是否属于支持集合
- `allowed_values`、`range`、`pattern` 等字段是否与 `type` 和规则语义一致
- `rules` 是否为合法规则列表
- `dependency` 规则中的 `when`、`requires` 结构是否完整

这一层失败时，应视为配置文件错误，而不是请求校验失败。

### 8.5 运行时槽位规则校验

配置文件通过 `Pydantic` 校验后，系统应将槽位规则转换为运行时 `JSON Schema`，并使用 `jsonschema` 执行最终校验。

该层负责校验：

- 槽位是否存在
- 槽位值类型是否匹配
- 枚举值是否在允许集合内
- 数值或长度范围是否满足要求
- 文本是否符合正则
- 条件依赖是否满足

对于依赖关系，推荐优先映射到 JSON Schema 的条件能力，例如：

- `dependentRequired`
- `if / then`

这样可以尽量减少手写规则执行逻辑，把规则执行交给成熟库处理。

## 9. LLM 集成设计

### 9.1 提供方支持范围

遵从性检查子系统必须支持：

- `openai`
- `anthropic`
- `google`

实现方式应建立在现有 `a2a_t.llm` 抽象之上扩展，而不是再实现一套独立的 LLM 调用栈。

### 9.2 结构化输出契约

LLM 输出必须强约束为结构化 JSON，最小形态如下：

```json
{
  "slots": {},
  "notes": [],
  "confidence": 0.95
}
```

含义如下：

- `slots`：按槽位名组织的提取结果
- `notes`：模型对不确定项、假设项的补充说明
- `confidence`：可选置信度，取值范围为 `0..1`

### 9.3 JSON Schema

SDK 应定义统一的 JSON Schema，并对所有提供方返回结果执行 schema 校验。

```json
{
  "type": "object",
  "required": ["slots", "notes"],
  "properties": {
    "slots": {
      "type": "object",
      "additionalProperties": true
    },
    "notes": {
      "type": "array",
      "items": { "type": "string" }
    },
    "confidence": {
      "type": ["number", "null"],
      "minimum": 0,
      "maximum": 1
    }
  },
  "additionalProperties": false
}
```

### 9.4 各厂商适配策略

每个提供方适配器都应优先使用厂商官方支持的原生结构化输出能力。

- `OpenAI`
  - 优先使用 Structured Outputs 与 `json_schema`
  - 官方文档说明该能力可约束模型输出遵循指定 JSON Schema
  - 参考：<https://platform.openai.com/docs/guides/structured-outputs>

- `Google`
  - 优先使用 Gemini 的结构化输出与 JSON schema 支持
  - 官方文档说明 Gemini 可生成符合指定 JSON Schema 的响应
  - 参考：<https://ai.google.dev/gemini-api/docs/structured-output>

- `Anthropic`
  - 优先使用 tool use 配合 `input_schema`
  - 官方文档说明 tool 可以携带 `input_schema`，可用于获得符合给定 schema 的结构化 JSON 结果
  - 参考：<https://docs.anthropic.com/en/docs/agents-and-tools/tool-use/implement-tool-use>
  - 参考：<https://docs.anthropic.com/en/docs/build-with-claude/tool-use>

### 9.5 回退策略

如果目标提供方或目标模型在当前场景下无法使用原生结构化输出能力，则适配器可以退回到以下路径：

1. 普通文本生成
2. SDK 侧提取 JSON 文本
3. SDK 侧执行 schema 校验
4. 在可配置范围内有限重试

此类回退应在日志中明确标识，并视为可靠性低于原生结构化输出路径。

## 10. 安全护栏集成设计

安全护栏层必须是可插拔的。

### 10.1 设计原则

1. SDK 不硬编码业务侧的安全策略内容。
2. SDK 只统一调用契约与返回结果归一化格式。
3. 不同安全护栏产品必须可通过配置替换。

### 10.2 工厂模型

```python
class SafetyGuardrailFactory(Protocol):
    def create(self, config: GuardrailConfig) -> SafetyGuardrail: ...
```

遵从性检查主流程只依赖工厂产出的统一接口，不依赖任何厂商私有类型。

## 11. 配置模型

```yaml
prompt_compliance:
  enabled: true

  guardrail:
    provider: "custom_guardrail"
    timeout: 10
    config: {}

  slot_extraction:
    provider: "openai"
    model: "gpt-4.1"
    timeout: 20
    temperature: 0
    max_retries: 2

  slot_schema:
    root_dir: "./cache"
    slot_root_name: "slots"
    file_name: "slot.yaml"
    not_found_policy: "strict"

  providers:
    openai:
      api_key: "${OPENAI_API_KEY}"
    anthropic:
      api_key: "${ANTHROPIC_API_KEY}"
    google:
      api_key: "${GOOGLE_API_KEY}"
```

### 11.1 槽位配置缺失策略

支持取值：

- `strict`
  - 缺少 `slot.yaml` 时直接失败
- `skip`
  - 缺少 `slot.yaml` 时跳过槽位校验并继续流程

该策略与前面确认过的可配置行为一致。

## 12. 错误模型

遵从性检查子系统需要区分确定性失败与外部依赖瞬时失败。

### 12.1 错误类型

- `GuardrailRejectedError`
- `ProcessedPromptParseError`
- `PromptOriginResolveError`
- `SlotExtractionError`
- `SlotValidationError`

### 12.2 统一结果模型

```python
@dataclass(slots=True)
class PromptComplianceResult:
    passed: bool
    stage: str
    extracted_slots: dict[str, object] | None = None
    notes: list[str] | None = None
    confidence: float | None = None
    error_code: str | None = None
    error_message: str | None = None
```

## 13. 重试与降级规则

### 13.1 可重试失败

仅对瞬时失败执行重试：

- 安全护栏调用超时或临时网络失败
- LLM 超时或临时提供方失败
- LLM 结构化输出解析失败或 schema 校验失败

建议默认值：

- `max_retries: 2`
- 指数退避

### 13.2 不可重试失败

以下情况应直接失败：

- 加工后 prompt 的 front matter 解析失败
- 原始 prompt 回取失败
- 在 `strict` 策略下未找到槽位配置
- 槽位规则校验不通过

## 14. 测试策略

### 14.1 单元测试

为以下能力增加单元测试：

- 加工后 prompt 解析
- 原始 prompt 回取
- 槽位配置路径定位
- `slot.yaml` 的 Pydantic 配置模型校验
- 从槽位配置到 JSON Schema 的转换
- 槽位规则校验
- 结构化输出 schema 校验

### 14.2 提供方适配器测试

为以下适配器增加测试：

- `openai`
- `anthropic`
- `google`

覆盖重点包括：

- 请求构造
- 结构化输出解析
- schema 校验失败处理
- 可重试错误归一化

### 14.3 集成测试

增加端到端遵从性检查流测试，覆盖：

- 安全护栏拒绝
- front matter 缺失
- 原始 prompt 回取成功
- 缺失 `slot.yaml`
- 依赖关系规则失败
- 成功校验通过
- LLM 输出非法 JSON

## 15. 包结构建议

建议模块组织如下：

- `a2a_t.server.prompt_compliance`
- `a2a_t.server.prompt_compliance.parser`
- `a2a_t.server.prompt_compliance.guardrails`
- `a2a_t.server.prompt_compliance.extractor`
- `a2a_t.server.prompt_compliance.slot_config`
- `a2a_t.server.prompt_compliance.validator`
- `a2a_t.server.prompt_compliance.errors`
- `a2a_t.server.prompt_compliance.models`

这样可以保持服务端入口轻量，同时将遵从性检查相关逻辑隔离到独立模块。

## 16. 关键决策总结

1. 服务端先对加工后 prompt 执行安全护栏检查。
2. 服务端从加工后 prompt 中解析 front matter，并据此回取原始 prompt。
3. 原始 prompt 与加工后 prompt 一起提交给 LLM 做槽位提取。
4. LLM 只负责槽位提取，不负责规则选择。
5. 槽位配置按镜像身份存放在 `slots/<name>/<version>/<language>/slot.yaml`。
6. `openai`、`google`、`anthropic` 都应优先采用官方支持的原生结构化输出能力。
7. prompt 缓存实现调整不属于本任务，但目标镜像身份模型已在设计中明确。
