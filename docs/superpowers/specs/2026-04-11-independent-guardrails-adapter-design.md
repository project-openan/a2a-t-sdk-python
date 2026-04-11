# 独立护栏适配层设计文档

## 1. 背景

当前 `prompt_compliance` 模块已经具备服务端请求校验链路，并且在请求进入时会先执行一次安全护栏检查：

```text
客户端发送请求
-> 服务端收到 processed prompt
-> guardrail.check(processed_prompt_text, request_metadata)
-> 通过后继续 prompt compliance 后续流程
```

结合 `docs/superpowers/specs/2026-04-11-independent-guardrails-provider-comparison.md` 的调研结果，可以明确当前最值得支持的独立护栏服务型供应商包括：

1. AWS — Amazon Bedrock Guardrails
2. Microsoft — Azure AI Content Safety
3. Google Cloud — Model Armor

这三家都可以承担“服务端请求进入时的输入侧安全检查”职责，但当前代码里的护栏抽象仍偏轻量：

- 只有 `check(prompt_text, context) -> GuardrailResult`
- 结果语义主要是 `passed: bool`
- 没有统一的 `policy_id / template_id` 承载位
- 没有显式的厂商适配层

当前实现可以“勉强接入”三家，但还不够优雅，也不利于后续逐步扩展。

本次设计的目标是：**让护栏适配层在设计上同时兼容 AWS / Azure / Google，但本轮实现只落地 Google Model Armor，AWS / Azure 只保留扩展点，不实现真实接入。**

## 2. 设计目标

### 2.1 本轮目标

- 保持当前服务端“请求进入时输入侧校验”的使用方式不变
- 为 AWS / Azure / Google 三家建立统一的护栏适配抽象
- 将厂商差异隔离在 provider adapter 内部
- 增强护栏配置模型与结果模型，使其能承载三家共同需求
- 本轮仅实现 `google_model_armor` provider
- AWS / Azure 仅设计兼容，不注册、不实现真实调用

### 2.2 非目标

本轮不处理：

- 输出侧护栏检查
- 护栏命中后的 prompt 改写继续执行
- 多 provider 并行/串行仲裁
- AWS Bedrock Guardrails 真实适配器
- Azure AI Content Safety 真实适配器
- 引入云厂商官方 SDK 作为强依赖

## 3. 范围结论

本轮护栏适配层仅服务于如下链路：

```text
PromptComplianceService.check(...)
-> guardrail.check(processed_prompt_text, request_metadata)
-> 根据结果决定是否继续执行 prompt compliance
```

也就是说：

- 只检查 **processed prompt 输入**
- 不检查模型输出
- 不做 response masking
- 不做多阶段策略编排

## 4. 设计结论

本次采用以下设计决策：

1. 不新建独立的顶层 `guardrails` 模块，继续复用 `src/a2a_t/server/prompt_compliance/guardrails.py`
2. 保留 `SafetyGuardrail.check(prompt_text, context)` 这一对外调用方式
3. 在内部新增“adapter 层”，将各厂商结果归一化
4. 在设计上支持 `aws_bedrock` / `azure_content_safety` / `google_model_armor`
5. 本轮只实现并注册 `google_model_armor`
6. 结果模型不再只依赖 `passed: bool`，新增统一决策语义
7. 当前输入侧场景下，`mask` 与 `review` 一律视为“不放行”

## 5. 当前实现评估

## 5.1 当前代码优点

当前 `src/a2a_t/server/prompt_compliance/guardrails.py` 已具备：

- `SafetyGuardrail` 统一协议
- `NoopSafetyGuardrail`
- `TransportSafetyGuardrail`
- `SafetyGuardrailFactory`
- `GuardrailProviderConfig`
- `GuardrailResult`

这些能力已经提供了最小可插拔基础。

## 5.2 当前代码不足

若要“优雅支持”三家独立护栏产品，当前实现主要不足是：

- `GuardrailResult` 只有布尔通过/拒绝语义，不足以表达 `allow / block / mask / review`
- `GuardrailProviderConfig` 缺少 `policy_id / endpoint / region / credentials_ref`
- 没有统一的 `GuardrailRequest`
- 没有 provider adapter 协议，导致厂商差异只能塞进 `transport`
- 没有区分“当前已实现 provider”和“未来预留 provider”

## 6. 模块结构设计

继续保留主路径：

```text
src/a2a_t/server/prompt_compliance/
```

建议调整为：

```text
prompt_compliance/
├── __init__.py
├── config.py
├── errors.py
├── guardrails.py
├── models.py
├── service.py
└── ...
```

本轮不新增过多文件，而是在 `guardrails.py` 中引入 adapter 相关抽象；若后续 provider 数量增加，再拆分为：

```text
guardrail_adapters.py
providers/google_model_armor.py
providers/aws_bedrock.py
providers/azure_content_safety.py
```

## 7. 数据模型设计

## 7.1 `GuardrailDecision`

新增统一决策枚举：

```python
class GuardrailDecision(str, Enum):
    ALLOW = "allow"
    BLOCK = "block"
    MASK = "mask"
    REVIEW = "review"
```

说明：

- `ALLOW`：通过，继续执行
- `BLOCK`：阻断，返回拒绝
- `MASK`：理论上可改写后继续，但当前输入侧场景先视为拒绝
- `REVIEW`：需要人工确认，当前输入侧场景先视为拒绝

## 7.2 `GuardrailRequest`

新增统一请求模型：

```python
@dataclass
class GuardrailRequest:
    text: str
    metadata: dict[str, object] | None = None
    policy_id: str | None = None
```

职责：

- 承载 processed prompt 输入
- 承载请求元数据
- 承载统一策略标识

## 7.3 `GuardrailResult`

增强当前结果模型：

```python
@dataclass
class GuardrailResult:
    decision: GuardrailDecision = GuardrailDecision.ALLOW
    passed: bool = True
    category: str | None = None
    reason: str | None = None
    raw_response: dict[str, Any] | None = None
    provider: str | None = None
    policy_id: str | None = None
```

说明：

- `passed` 为兼容当前 service 逻辑暂时保留
- 长期以 `decision` 为主语义
- `provider` 用于记录来自哪个厂商
- `policy_id` 用于记录本次命中的护栏策略/模板

## 8. 配置模型设计

## 8.1 `GuardrailProviderConfig`

建议增强为：

```python
@dataclass
class GuardrailProviderConfig:
    provider: str = "noop"
    timeout: float = 10.0
    policy_id: str = ""
    endpoint: str = ""
    region: str = ""
    credentials_ref: str = ""
    config: dict[str, Any] = field(default_factory=dict)
```

## 8.2 字段语义

| 字段 | Google Model Armor | AWS Bedrock Guardrails | Azure AI Content Safety |
|---|---|---|---|
| `provider` | `google_model_armor` | `aws_bedrock` | `azure_content_safety` |
| `policy_id` | template / template resource name | guardrail id | policy / project / config id |
| `endpoint` | 可选 endpoint | 可选 | Azure service endpoint |
| `region` | location | AWS region | 可选 |
| `credentials_ref` | 凭据 env key / ADC 标识 | 凭据 env key / profile | 凭据 env key |
| `config` | floor settings 等额外项 | version 等 | features / thresholds 等 |

这样本轮虽然只实现 Google，但 AWS / Azure 后续无需再修改配置结构。

## 8.3 `.env` 设计

建议在当前 Prompt Compliance 配置基础上补充：

```text
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER=noop
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS=10
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID=
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_ENDPOINT=
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_REGION=
A2AT_PROMPT_COMPLIANCE_GUARDRAIL_CREDENTIALS_REF=
```

说明：

- 默认仍然使用 `noop`
- 用户显式配置后才启用 `google_model_armor`
- AWS / Azure 后续也复用同一套字段

## 9. Adapter 抽象设计

## 9.1 `GuardrailAdapter`

新增内部协议：

```python
class GuardrailAdapter(Protocol):
    provider_name: str

    def check_input(self, request: GuardrailRequest) -> GuardrailResult:
        ...
```

说明：

- 当前只定义 `check_input()`
- 不定义 `check_output()`，因为本轮范围不包含输出侧护栏

## 9.2 `AdapterSafetyGuardrail`

新增桥接实现：

```python
class AdapterSafetyGuardrail:
    def __init__(self, config: GuardrailProviderConfig, adapter: GuardrailAdapter) -> None:
        ...

    def check(self, prompt_text: str, context: dict[str, object] | None = None) -> GuardrailResult:
        request = GuardrailRequest(
            text=prompt_text,
            metadata=context,
            policy_id=self._config.policy_id or None,
        )
        return self._adapter.check_input(request)
```

职责：

- 保持当前 `SafetyGuardrail` 接口不变
- 将 `processed_prompt_text + context` 转换为统一请求模型

## 10. Provider 设计

## 10.1 本轮实现的 provider

本轮只实现：

- `noop`
- `google_model_armor`

## 10.2 预留但不实现的 provider

本轮只在设计上兼容，不注册、不实现：

- `aws_bedrock`
- `azure_content_safety`

原因：

- 当前业务范围只要求建立优雅扩展底座
- Google 是本轮优先落地对象
- 提前注册未实现 provider 容易误导用户“已经可用”

## 11. Google Model Armor 适配设计

## 11.1 provider 名

建议固定为：

```text
google_model_armor
```

## 11.2 适配器职责

`GoogleModelArmorGuardrailAdapter` 负责：

1. 从 `GuardrailProviderConfig` 读取 Google 相关配置
2. 构造 Model Armor 的输入检查请求
3. 调用 transport
4. 将 Google 原始响应映射为统一 `GuardrailResult`

## 11.3 transport 设计

本轮建议继续采用“可注入 transport”的方式，而不是直接引入真实 Google SDK：

```python
GoogleModelArmorGuardrailAdapter(config, transport=...)
```

原因：

- 当前仓库尚未建立 Google Cloud SDK 依赖管理
- 更适合单元测试
- 避免先把 SDK 绑定死

## 11.4 Google 结果映射

统一映射规则建议如下：

- 明确安全通过 -> `ALLOW`
- 明确 prompt injection / jailbreak / 高风险命中 -> `BLOCK`
- 返回可替换文本或脱敏建议 -> `MASK`
- 厂商返回中间态或人工复核态 -> `REVIEW`

在当前输入侧场景中，service 处理建议为：

- `ALLOW` -> 继续执行
- `BLOCK` / `MASK` / `REVIEW` -> 统一作为拒绝

## 12. AWS / Azure 预留设计

虽然本轮不实现，但抽象必须确保后续可自然扩展。

## 12.1 AWS Bedrock Guardrails

后续可扩展为：

- provider 名：`aws_bedrock`
- 使用 `policy_id` 承载 `guardrail id`
- 使用 `region` 承载部署区域
- 使用 `config` 承载 guardrail version 等额外字段

## 12.2 Azure AI Content Safety

后续可扩展为：

- provider 名：`azure_content_safety`
- 使用 `endpoint` 承载服务地址
- 使用 `credentials_ref` 承载密钥配置
- 使用 `config` 承载 features / thresholds / shields 等配置

## 13. Factory 设计

当前 `SafetyGuardrailFactory` 建议继续保留，但内部注册对象改为 adapter builder。

建议调用方式保持不变：

```python
guardrail = SafetyGuardrailFactory.create(config.guardrail)
```

内部流程调整为：

```text
GuardrailProviderConfig
-> factory 根据 provider 创建 adapter
-> adapter 包装为 AdapterSafetyGuardrail
-> 返回统一 SafetyGuardrail
```

本轮默认注册：

- `noop`
- `google_model_armor`

不注册：

- `aws_bedrock`
- `azure_content_safety`

## 14. Service 行为设计

`PromptComplianceService` 的对外调用方式不变：

```python
guardrail.check(processed_prompt_text, request_metadata)
```

建议只调整内部决策处理逻辑：

- `decision == ALLOW` -> `passed=True`
- `decision in {BLOCK, MASK, REVIEW}` -> 返回 guardrail rejected

这样不会破坏当前服务层编排顺序，也不会扩大当前业务范围。

## 15. 错误处理设计

继续保留：

- `GuardrailExecutionError`
- `GuardrailRejectedError`

并明确规则：

- 调用厂商失败、超时、认证失败、网络异常 -> `GuardrailExecutionError`
- 厂商明确判定风险命中 -> 作为正常 `GuardrailResult` 返回，不抛异常

也就是说：

- **护栏命中不是异常**
- **护栏调用失败才是异常**

## 16. 测试设计

建议覆盖以下测试：

### 16.1 模型与配置

- `GuardrailDecision` 枚举值
- `GuardrailResult` 对 `provider / policy_id / decision` 的承载
- `.env` 读取 `policy_id / endpoint / region / credentials_ref`

### 16.2 Factory

- `noop` 可创建
- `google_model_armor` 可创建
- `aws_bedrock` 未注册时报清晰错误
- `azure_content_safety` 未注册时报清晰错误

### 16.3 Google Adapter

- allow 响应映射为 `ALLOW`
- block 响应映射为 `BLOCK`
- timeout / connection error 映射为 `GuardrailExecutionError`
- 原始返回保留在 `raw_response`

### 16.4 Service

- `ALLOW` 时继续执行
- `BLOCK / MASK / REVIEW` 时统一拒绝

## 17. 实施顺序建议

建议按以下顺序落地：

### 阶段 A

- 增强 `GuardrailProviderConfig`
- 增强 `GuardrailResult`
- 新增 `GuardrailDecision`
- 新增 `GuardrailRequest`

### 阶段 B

- 引入 `GuardrailAdapter`
- 引入 `AdapterSafetyGuardrail`
- 重构 `SafetyGuardrailFactory`

### 阶段 C

- 实现 `google_model_armor` adapter
- 补充对应单测

### 阶段 D

- 调整 service 的决策处理逻辑
- 更新 `.env` / `env.example` / README

### 阶段 E

- 补充 AWS / Azure 预留说明与回归测试

## 18. 风险与注意事项

- 本轮不应注册未实现 provider，否则会给用户造成“已支持”的误解
- `MASK` 在输入侧场景下先按拒绝处理，避免未设计的 prompt 改写副作用
- 当前仅做输入侧护栏，不应在代码中提前引入输出侧复杂逻辑
- 配置字段需通用化，不能只为 Google 临时命名
- 后续若 provider 数量增加，建议把 adapter 从 `guardrails.py` 中拆出独立文件

## 19. 最终结论

本次设计建议在 `prompt_compliance` 模块内引入一层轻量但标准化的 Guardrail Adapter 抽象：

- **设计上兼容三家**：AWS / Azure / Google
- **本轮只落地 Google**：`google_model_armor`
- **保留当前服务调用方式**：`guardrail.check(prompt_text, context)`
- **统一结果语义**：`allow / block / mask / review`
- **保留 AWS / Azure 后续扩展空间**

这样可以在不推翻当前 `PromptComplianceService` 编排方式的前提下，把当前“最低可接入”的护栏能力提升为“可优雅扩展”的适配底座。
