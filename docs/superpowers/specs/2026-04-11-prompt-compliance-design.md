# Prompt 遵从校验最终设计文档

## 1. 设计目标

Prompt Compliance 模块用于在服务端接收到加工后 Prompt 时，执行输入侧遵从性检查，并在请求继续执行前完成以下工作：
1. 安全护栏检查
2. processed prompt front matter 解析
3. original prompt 回取
4. 结构化槽位提取
5. 槽位规则校验

当前实现同时满足以下目标：
- 支持服务端输入侧安全护栏接入
- 支持基于 original prompt + processed prompt 的槽位提取
- 支持镜像路径下的 `slot.json` 加载
- 支持 Pydantic 校验 slot 文件结构
- 支持 JSON Schema 执行运行时槽位规则校验
- 设计上兼容 AWS / Azure / Google 三类独立护栏产品
- 当前仅正式实现 `google_model_armor`

## 3. 非目标

当前版本不负责：
- 客户端 Prompt 加工逻辑
- 输出侧安全护栏
- 多 provider 级联/投票
- 通用规则引擎平台化
- AWS / Azure 护栏的真实接入实现

## 4. 总体处理链路

当前服务端遵从校验链路为：

```text
processed prompt
-> SafetyGuardrail.check()
-> ProcessedPromptParser.parse()
-> PromptOriginResolver.resolve()
-> PromptSlotExtractor.extract()
-> SlotSchemaResolver.load()
-> SlotValidator.validate()
-> PromptComplianceResult
```

其中：
- 安全护栏永远先于槽位提取执行
- original prompt 通过 prompt catalog + prompt loader 回取
- 槽位规则以 `slot.json` 为准
- 仅校验通过的请求才继续后续任务执行

## 5. 当前代码结构

当前相关代码位于：
- `src/a2a_t/server/prompt_compliance/config.py`
- `src/a2a_t/server/prompt_compliance/models.py`
- `src/a2a_t/server/prompt_compliance/errors.py`
- `src/a2a_t/server/prompt_compliance/parser.py`
- `src/a2a_t/server/prompt_compliance/origin_resolver.py`
- `src/a2a_t/server/prompt_compliance/extractor.py`
- `src/a2a_t/server/prompt_compliance/slot_schema.py`
- `src/a2a_t/server/prompt_compliance/schema_builder.py`
- `src/a2a_t/server/prompt_compliance/validator.py`
- `src/a2a_t/server/prompt_compliance/guardrails.py`
- `src/a2a_t/server/prompt_compliance/guardrail_providers.py`
- `src/a2a_t/server/prompt_compliance/service.py`

## 6. 配置设计

### 6.1 总配置入口

当前统一配置入口为：
- `PromptComplianceConfig.from_env(env)`

### 6.2 当前配置模型

当前配置模型为：
- `PromptComplianceConfig`
- `GuardrailProviderConfig`
- `SlotExtractionConfig`
- `SlotSchemaConfig`

其中：
- `PromptComplianceConfig.guardrail` 用于护栏配置
- `PromptComplianceConfig.slot_extraction` 用于 LLM 槽位提取配置
- `PromptComplianceConfig.slot_schema` 用于 `slot.json` 定位配置

### 6.3 当前环境变量

当前 Prompt Compliance 使用以下环境变量：
- `A2AT_PROMPT_COMPLIANCE_ENABLED`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_ENDPOINT`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_REGION`
- `A2AT_PROMPT_COMPLIANCE_GUARDRAIL_CREDENTIALS_REF`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_PROVIDER`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MODEL`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TIMEOUT_SECONDS`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TEMPERATURE`
- `A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MAX_RETRIES`
- `A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR`
- `A2AT_PROMPT_COMPLIANCE_SLOT_FILE_NAME`
- `A2AT_PROMPT_COMPLIANCE_SLOT_NOT_FOUND_POLICY`

### 6.4 当前默认值

当前关键默认值为：
- 护栏 provider：`noop`
- 护栏超时：`10s`
- 槽位提取超时：`30s`
- 槽位目录：`./slots`
- 槽位文件名：`slot.json`
- 缺失 slot 文件策略：`strict`

## 7. Prompt 身份与原始 Prompt 回取

### 7.1 PromptIdentity

当前身份模型：
- `name`
- `language`
- `version`

### 7.2 原始 Prompt 回取原则

服务端收到的只有加工后的 Prompt。
当前设计要求：
- 先从 processed prompt 中解析 front matter
- 再基于 front matter 的 `name/language/version`
- 通过既有 prompt catalog + prompt loader 回取 original prompt

这是当前遵从校验链路中的权威身份来源。

## 8. 槽位提取设计

### 8.1 输入

槽位提取器的输入包括：
- processed prompt
- original prompt

### 8.2 输出模型

当前输出强约束为结构化 JSON，对应运行时模型：
- `SlotExtractionResult.slots: dict[str, object]`
- `SlotExtractionResult.notes: list[str]`
- `SlotExtractionResult.confidence: float | None`
- `SlotExtractionResult.raw_response: dict[str, Any] | None`

### 8.3 LLM 提供商范围

设计目标上支持：
- `openai`
- `anthropic`
- `google`

当前代码重点已落在结构化输出契约与校验边界，厂商适配细节仍主要由后续 extractor 实现完善。

### 8.4 结构化输出原则

最终设计要求各厂商优先走原生结构化输出能力：
- OpenAI：Structured Outputs / `json_schema`
- Google：Gemini structured output / JSON Schema
- Anthropic：tool use + `input_schema`

无论厂商能力如何，最终都必须落到统一的结构化输出模型上。

## 9. 槽位文件设计

### 9.1 文件格式

当前最终格式为：
- `slot.json`

不再使用：
- `slot.yaml`

### 9.2 路径布局

当前布局规则为：

```text
<slot_root>/slots/<name>/<version>/<language>/slot.json
```

默认根目录来自：
- `A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR`

默认值：
- `./slots`

示例：

```text
./slots/slots/network diagnosis/1.0.0/zh-CN/slot.json
```

说明：
- 当前 `SlotSchemaResolver` 会将 `root_dir` 与 `slot_root_name` 共同拼接
- 默认 `slot_root_name` 为 `slots`
- 因此默认实际路径是 `./slots/slots/...`
- 这是当前代码真实实现，最终设计文档以此为准

### 9.3 默认目录准备

当前仓库已包含：
- `slots/.gitkeep`

同时 `SlotSchemaResolver` 初始化时会确保根目录存在。

## 10. slot.json 模型设计

### 10.1 顶层模型

当前 `slot.json` 通过 `SlotSchema` 表达，包含：
- `prompt_identity`
- `slots`
- `rules`

### 10.2 单槽位定义

当前 `SlotDefinition` 支持：
- `name`
- `required`
- `type`
- `allowed_values`
- `range`
- `pattern`

当前支持的 `type`：
- `string`
- `number`
- `integer`
- `boolean`
- `enum`
- `list`

### 10.3 跨槽位规则

当前 `SlotRule` 已支持：
- `dependency`

规则语义为：
- 当某槽位满足指定条件时，要求另一组槽位必须同时存在

这已覆盖“出现 A 时必须同时有 B”这一需求。

## 11. 槽位校验分层

### 11.1 文件格式校验

当前先使用 Pydantic 校验 `slot.json` 文件格式本身：
- 文件是否为合法 JSON
- 结构字段是否完整
- `slots` 定义是否合法
- `rules` 定义是否合法
- enum / pattern / range 等语义是否匹配

失败时抛出：
- `SlotSchemaLoadError`
- `SlotSchemaValidationError`

### 11.2 运行时规则校验

当前再将 `SlotSchema` 转换为运行时 JSON Schema，并通过 `jsonschema` 执行 extracted slots 校验。

该层负责：
- required
- type
- enum
- range
- pattern
- dependency

失败时返回：
- `SlotValidationResult(valid=False, errors=[...])`
- 并由 service 映射为 `SlotValidationError`

### 11.3 技术选型结论

当前最终方案为：
- 用 **Pydantic** 校验 `slot.json` 文件格式本身
- 用 **jsonschema** 执行槽位规则校验

## 12. 护栏抽象设计

### 12.1 统一接口

当前对外统一接口为：
- `SafetyGuardrail.check(prompt_text, context) -> GuardrailResult`

### 12.2 统一模型

当前已引入：
- `GuardrailDecision`
- `GuardrailRequest`
- `GuardrailResult`

其中 `GuardrailDecision` 支持：
- `ALLOW`
- `BLOCK`
- `MASK`
- `REVIEW`

### 12.3 adapter 层

当前已引入：
- `GuardrailAdapter`
- `AdapterSafetyGuardrail`

设计结论：
- 对外仍保留 `SafetyGuardrail` 的简单调用形式
- 对内通过 adapter 隔离厂商差异

## 13. 独立护栏供应商设计

### 13.1 设计兼容范围

当前设计上兼容：
- `google_model_armor`
- `aws_bedrock`
- `azure_content_safety`

### 13.2 当前已实现 provider

当前仅已实现：
- `google_model_armor`

### 13.3 当前未实现 provider

当前仅预留名称，不注册为可用 provider：
- `aws_bedrock`
- `azure_content_safety`

当用户选择它们时，当前工厂会返回“已预留、未实现”的清晰错误。

## 14. Google Model Armor 设计

### 14.1 代码组织

当前 Google provider 不再单独拆文件，而是统一收敛在：
- `src/a2a_t/server/prompt_compliance/guardrail_providers.py`

这是当前最终代码结构。

### 14.2 当前实现组件

当前已实现：
- `GoogleModelArmorGateway`
- `GoogleModelArmorGuardrailAdapter`

### 14.3 SDK 依赖

当前正式使用官方 SDK：
- `google-cloud-modelarmor`

SDK 依赖被限制在 provider 实现文件中，不向 service / config / models 泄漏。

### 14.4 当前结果映射

Google Model Armor 结果当前统一映射为：
- `NO_MATCH_FOUND` -> `ALLOW`
- `MATCH_FOUND` 且有 filter results -> `BLOCK`
- 其他不确定情况 -> `REVIEW`

### 14.5 当前 service 处理规则

在当前输入侧场景下：
- `ALLOW`：继续执行
- `BLOCK`：拒绝
- `MASK`：拒绝
- `REVIEW`：拒绝

也就是说，当前 `MASK` 和 `REVIEW` 不做改写后继续执行，而是统一视为不放行。

## 15. 错误模型设计

当前核心错误类型包括：
- `GuardrailExecutionError`
- `GuardrailRejectedError`
- `ProcessedPromptParseError`
- `PromptOriginResolveError`
- `SlotExtractionError`
- `SlotSchemaLoadError`
- `SlotSchemaValidationError`
- `SlotValidationError`

设计结论：
- 护栏命中属于正常业务结果，不一定抛异常
- 护栏调用失败才属于执行异常
- service 负责将阶段性错误统一映射为 `PromptComplianceResult`

## 16. Service 设计

### 16.1 当前构造方式

当前 `PromptComplianceService` 通过依赖注入接收：
- `guardrail`
- `parser`
- `origin_resolver`
- `extractor`
- `slot_config_resolver`
- `validator`
- `slot_not_found_policy`

当前并不是直接接收整个 `PromptComplianceConfig`。

### 16.2 当前执行顺序

当前执行顺序固定为：
1. 护栏检查
2. processed prompt 解析
3. original prompt 回取
4. 槽位提取
5. slot schema 加载
6. slot 校验
7. 返回统一结果

### 16.3 slot 文件缺失策略

当前支持：
- `strict`
- `skip`

默认值：
- `strict`

行为：
- `strict`：缺失 `slot.json` 直接失败
- `skip`：跳过槽位规则校验，返回 `skipped_slot_validation`

## 17. 对外导出

当前 `src/a2a_t/server/prompt_compliance/__init__.py` 已导出：
- `PromptComplianceConfig`
- `GuardrailProviderConfig`
- `SlotExtractionConfig`
- `SlotSchemaConfig`
- `GuardrailDecision`
- `GuardrailRequest`
- `GuardrailResult`
- `GoogleModelArmorGateway`
- `GoogleModelArmorGuardrailAdapter`
- `SlotSchema`
- `SlotSchemaResolver`
- `SlotSchemaBuilder`
- `SlotValidator`
- `PromptComplianceService`
- 各类 error

## 18. 测试结论

当前实现已覆盖以下测试方向：
- prompt compliance 配置读取
- guardrail 抽象与 factory
- Google Model Armor provider
- slot schema 加载与校验
- service 阶段编排
- 文档/README/env 一致性
- 全量回归

## 19. 最终结论

Prompt Compliance 模块的最终设计可总结为：
- 服务端以 processed prompt 为入口执行输入侧遵从校验
- original prompt 必须通过 prompt catalog + prompt loader 回取
- 槽位文件统一为 `slot.json`
- 用 Pydantic 校验 `slot.json` 文件格式本身
- 用 JSON Schema 执行运行时槽位规则校验
- 护栏通过 adapter 层统一抽象，兼容 AWS / Azure / Google
- 当前仅正式实现 `google_model_armor`
- Google provider 统一收敛在 `guardrail_providers.py`
- `BLOCK / MASK / REVIEW` 在当前服务端输入侧场景下统一拒绝

