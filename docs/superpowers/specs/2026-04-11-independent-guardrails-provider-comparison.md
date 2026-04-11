# 独立护栏服务接入对比

## 1. 结论摘要

当前比较明确可归入“独立护栏服务型”的主要供应商有：

1. **AWS — Amazon Bedrock Guardrails**
2. **Microsoft — Azure AI Content Safety**
3. **Google Cloud — Model Armor**

其中：

- **AWS Bedrock Guardrails**：最像“完整护栏编排产品”
- **Azure AI Content Safety**：能力面最广，既有内容审核，也有 Prompt Shields、Groundedness、Task Adherence
- **Google Cloud Model Armor**：更偏 AI 安全防护，特别强调 prompt injection / jailbreak / 敏感信息 / 有害内容

## 2. 判定标准

本次“独立护栏服务型”采用以下标准：

- 有**独立产品/服务入口**
- 可配置**护栏策略/模板/规则**
- 可在**用户输入**和/或**模型输出**两侧执行检查
- 不依赖某个单一模型的内建安全机制才能工作
- 能作为应用架构中的**独立安全层**

**不纳入本次主表**的通常包括：

- 单纯 `Moderation API`
- 仅模型内建 safety refusal
- 仅 safety mode / harmlessness tuning，而无独立护栏编排层

## 3. 厂商逐项调研

## 3.1 AWS — Amazon Bedrock Guardrails

**产品定位**
- AWS 提供独立护栏产品：`Amazon Bedrock Guardrails`

**核心能力**
- 内容过滤：有害文本/图像
- 拒绝主题：`Denied topics`
- 敏感信息过滤：PII 检测、屏蔽/脱敏
- 词表过滤：`Word filters`
- 上下文校验：`Contextual grounding checks`
- 提示攻击检测：`Prompt Attack`

**集成方式**
- 可在模型推理时引用 guardrail
- 可用于：
  - 模型推理
  - Bedrock Agents
  - Knowledge Bases
  - Flows

**独立护栏特征**
- 护栏本身是独立配置对象
- 输入、输出都可评估
- 命中后可直接阻断或替换响应
- 文档明确说明可用于不同 FM 调用链路

**适用场景**
- 企业内统一护栏平台
- RAG / Agent 场景
- 需要主题封禁、PII 遮罩、grounding 检查的业务

**调研判断**
- 这是目前最典型、最明确的“独立护栏服务型”产品之一

**官方资料**
- https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-how.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails.html
- https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-use.html

## 3.2 Microsoft — Azure AI Content Safety

**产品定位**
- Azure 提供独立安全服务：`Azure AI Content Safety`

**核心能力**
- 文本/图像内容审核
- `Prompt Shields`
  - 检测用户 prompt 攻击
  - 检测文档攻击
- `Groundedness detection`
- `Protected material detection`
- `Task adherence API`
- 自定义分类能力

**集成方式**
- 作为独立 API/服务使用
- 也可在 Azure AI Foundry 中使用

**独立护栏特征**
- 不是单一模型内建能力
- 有独立资源、独立 API、独立 Studio
- 可作为应用的前置/后置安全层
- 除内容审核外，还覆盖 prompt protection、grounding、agent/tool 行为偏移检测

**适用场景**
- 既要内容审核，又要 prompt injection 防护
- Agent / tool-use 系统
- 需要统一安全工作台和可视化治理的企业

**调研判断**
- Azure AI Content Safety 属于独立护栏服务，且能力覆盖面很全
- 如果后续要做“护栏抽象层”，Azure 很适合作为一类完整 provider

**官方资料**
- https://learn.microsoft.com/en-us/azure/ai-services/content-safety/overview
- https://learn.microsoft.com/en-us/azure/ai-services/content-safety/
- https://learn.microsoft.com/en-us/shows/responsible-ai/azure-ai-content-safety-prompt-shields

## 3.3 Google Cloud — Model Armor

**产品定位**
- Google Cloud 提供独立 AI 安全服务：`Model Armor`

**核心能力**
- Prompt / response 安全筛查
- Prompt injection / jailbreak 检测
- 有害内容过滤
- 敏感数据保护
- 恶意 URL 检测
- 文档筛查
- 模板机制与 floor settings

**集成方式**
- 可作为独立服务使用
- 也可与以下能力集成：
  - Vertex AI
  - GKE / Service Extensions
  - Gemini Enterprise
  - Google Cloud MCP servers

**独立护栏特征**
- 文档直接称其为 Google Cloud service
- 有模板、端点、floor settings
- 支持先筛 prompt，再筛 response
- 强调运行时 AI security，而非单纯模型 safety setting

**适用场景**
- 云上 AI 网关/运行时保护
- 重点关注 jailbreak / prompt injection / 数据泄露
- 需要组织级最低护栏要求（floor settings）

**调研判断**
- Google Cloud 现在也具备明确的“独立护栏服务型”产品
- 它比 Vertex 单纯 safety filters 更符合“可独立编排的护栏层”定义

**官方资料**
- https://docs.cloud.google.com/model-armor
- https://docs.cloud.google.com/security-command-center/docs/model-armor
- https://cloud.google.com/security/products/model-armor
- https://docs.cloud.google.com/security-command-center/docs/model-armor-integrations

## 4. 对比结论

| 供应商 | 独立产品形态 | 输入/输出双向检查 | Prompt Injection/Jailbreak | PII/敏感信息 | Grounding/事实性 | Agent/工具链支持 |
|---|---|---:|---:|---:|---:|---:|
| AWS Bedrock Guardrails | 强 | 是 | 是 | 是 | 是 | 是 |
| Azure AI Content Safety | 强 | 是 | 是 | 是 | 是 | 是 |
| Google Cloud Model Armor | 强 | 是 | 是 | 是 | 部分偏安全/筛查导向 | 有 |

## 5. 对系统设计的建议

如果要设计“安全护栏适配层”，建议抽象成统一接口：

- `scan_input(prompt, context) -> GuardrailResult`
- `scan_output(prompt, response, context) -> GuardrailResult`
- `provider_name`
- `policy_id / template_id`
- `raw_result`
- `decision`：allow / block / mask / review

建议优先支持顺序：

1. **AWS Bedrock Guardrails**
   - 产品边界最清晰
   - 护栏概念最成熟
2. **Azure AI Content Safety**
   - 能力丰富，适合企业化
3. **Google Cloud Model Armor**
   - 适合做 AI 安全运行时保护扩展

## 6. 本次未纳入主表的厂商

以下不归类为“独立护栏服务型”主选项，至少不如上面三家明确：

- **OpenAI**
  - 主要是 `Moderation API`
  - 更像内容审核接口，不是完整独立护栏编排服务
- **Anthropic**
  - 官方有 guardrails 实践与 jailbreak mitigation 指南
  - 但没有查到类似 Bedrock Guardrails / Azure Content Safety / Model Armor 的独立护栏产品
- **Cohere / Mistral**
  - 更偏 moderation / safety mode，不属于本轮主结论
