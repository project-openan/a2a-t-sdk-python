# Prompt Runtime 配置与资源加载重构设计

## 1. 背景

当前仓库中，A2A-T prompt 相关能力分散在 `config`、`prompt`、`client`、`server` 四个区域：

- `config` 负责顶层配置读取
- `prompt` 负责资源读取、分析、校验等公共能力
- `client` 负责 A2A-T prompt 生成编排
- `server` 负责 A2A-T prompt 合规校验编排

当前实现已经具备以下能力：

- 从 `.env` 读取 `language`、`prompt_resource_version`、prompt 相关配置、prompt compliance 配置
- 读取本地 prompt 资源文件
- 基于 LLM 做场景识别、slot 提取
- 基于 guardrail 和 slot 校验做服务端校验

但配置模型所有权、资源来源抽象、client/server 装配方式仍然分散，已经出现明显的依赖方向问题和重复装配问题。

## 2. 设计目标

本次重构目标如下：

1. 将所有运行时配置模型统一收口到 `src/a2a_t/config`
2. 将 prompt 公共运行时能力统一收口到 `src/a2a_t/prompt`
3. 移除旧的 `PromptLoader` 主链路，改为“统一资源来源抽象 + 上层资源 loader”架构
4. 保留 `catalog / provider / cache` 抽象和扩展槽位，但当前仅实现本地来源
5. 为 client/server 分别提供应用层 orchestrator builder，统一装配入口
6. 让 client 与 server 基于同一套配置模型、同一套公共运行时依赖工作

## 3. 非目标

本次不负责：

- 实现远端 URL / Agent 来源
- 修改 client/server 对外业务语义
- 重新设计 LLMClient 本身
- 修改现有资源文件内容格式之外的业务协议

说明：

- `.env` 中远端相关配置项允许保留，但本次重构后默认不参与运行时装配
- 远端扩展只保留抽象和扩展槽位，不保留具体实现代码
- `SDKConfig`、`PromptConfig`、`LLMConfig` 等现有通用配置模型不纳入本次重构范围

## 4. 当前架构与主要问题

### 4.1 当前架构

当前链路可以概括为两套并存的体系：

1. 通用 Prompt 资产链

```text
PromptLoader
-> catalog
-> provider
-> parser
-> cache
```

对应代码主要位于：

- `src/a2a_t/prompt/loader.py`
- `src/a2a_t/prompt/resources/catalog.py`
- `src/a2a_t/prompt/resources/providers.py`
- `src/a2a_t/prompt/resources/cache.py`

2. A2A-T 业务资源链

```text
ScenarioLoader
TemplateLoader
SlotSchemaLoader
PromptResourceLoader
PromptResourceRegistry
```

对应代码主要位于：

- `src/a2a_t/prompt/resources/scenario_loader.py`
- `src/a2a_t/prompt/resources/template_loader.py`
- `src/a2a_t/prompt/resources/slot_schema_loader.py`
- `src/a2a_t/prompt/resources/prompt_resource_loader.py`
- `src/a2a_t/prompt/resources/registry.py`

client 和 server 又分别在各自入口手工拼装这些依赖。

### 4.2 当前主要问题

#### 4.2.1 配置定义权分散

`A2ATConfig` 定义在 `src/a2a_t/config/models.py`，但它直接引用：

- `a2a_t.prompt.common.config.PromptLoaderConfig`
- `a2a_t.prompt.validation.config.GuardrailProviderConfig`
- `a2a_t.server.prompt_compliance.config.PromptComplianceConfig`

这导致：

- `config` 依赖 `prompt`
- `config` 依赖 `server`
- `prompt`、`client`、`server` 又反过来消费 `A2ATConfig`

这在架构上已经形成“配置域反向依赖运行时域”的问题。

#### 4.2.2 通用 Prompt 资产链与 A2A-T 资源链边界混乱

当前同时存在：

- 旧的 `PromptLoader` 资产链
- 新的 A2A-T 资源 loader 链

但二者没有统一的资源来源抽象，也没有统一装配方式，造成：

- 代码重复
- 配置语义混杂
- 调用方很难判断应该依赖哪一套链路

#### 4.2.3 资源来源能力与当前需求不匹配

当前实际需求只有本地资源，但代码里仍保留了：

- `UrlPromptCatalog`
- `AgentPromptCatalog`
- `UrlProvider`
- `AgentProvider`
- 远端缓存刷新语义

这会带来额外复杂度，但当前运行时并不需要。

#### 4.2.4 client/server 装配方式不统一

client 当前在 `PromptClient` 中手工装配：

- `LLMClient`
- 各类资源 loader
- `ScenarioRecognizer`
- `SlotExtractor`
- `SlotValidator`

server 当前也没有统一 builder 对 `guardrail + resources + analysis + validation` 进行集中装配。

结果是：

- 依赖构造逻辑散落
- client/server 对共享能力的装配规则容易漂移
- 单测难以围绕“配置驱动的装配”建立稳定覆盖

## 5. 目标架构

### 5.1 总体分层

重构后，代码按三层组织：

1. `config`
2. `prompt`
3. `client / server`

职责如下：

- `config`
  只负责配置模型和配置加载
- `prompt`
  只负责公共运行时能力和公共 builder
- `client / server`
  只负责应用层 orchestrator builder、入口 API 和结果映射

### 5.2 目标包结构

```text
src/a2a_t/
  config/
    __init__.py
    models.py
    loader.py
    source.py

  prompt/
    __init__.py
    common/
    resources/
    analysis/
    validation/
    builders/

  client/
    prompt_client.py
    prompt/
      prompt_generation_orchestrator.py
      prompt_generation_orchestrator_builder.py
      ...

  server/
    prompt_handler.py
    prompt_compliance/
      prompt_compliance_orchestrator.py
      prompt_compliance_orchestrator_builder.py
      result.py
      constants.py
```

### 5.3 关键原则

1. `config` 不再依赖 `prompt`、`client`、`server`
2. `prompt` 不再定义配置 dataclass
3. `PromptLoader` 从主架构中移除
4. 所有上层资源读取都从统一的 `PromptResourceSource` 读取
5. 当前仅有本地来源实现

## 6. 配置设计

### 6.1 配置模型所有权

以下配置模型统一迁移或收口到 `src/a2a_t/config`：

- `PromptRuntimeConfig`
- `GuardrailProviderConfig`
- `PromptComplianceConfig`
- `SlotSchemaConfig`

说明：

- 迁移后，这些模型不再由 `prompt` 或 `server` 包定义
- `prompt` / `client` / `server` 统一从 `config` 包导入

### 6.2 A2ATConfig

重构后，`A2ATConfig` 继续作为顶层配置入口，至少包含：

```python
@dataclass(slots=True)
class A2ATConfig:
    prompt: PromptRuntimeConfig
    prompt_compliance: PromptComplianceConfig
```

说明：

- `language`
- `prompt_resource_version`

不再挂在 `A2ATConfig` 顶层，而是收口到 `PromptRuntimeConfig`

### 6.3 PromptRuntimeConfig

为替代旧的 `PromptLoaderConfig`，定义新的 prompt 运行时配置模型：

```python
@dataclass(slots=True)
class PromptRuntimeConfig:
    language: str = "en-US"
    prompt_resource_version: str = "0.0.1"
    source_type: str = "local_file"
    local_root_dir: str = "./package_data/prompt_resources"
```

说明：

- `language` 表示当前 prompt 运行时默认语言
- `prompt_resource_version` 表示当前 prompt 运行时默认资源版本
- `source_type` 当前仅支持 `local_file`
- 为未来扩展保留 `url`、`agent` 等来源类型空间
- 当前不再保留 `default_ttl`

### 6.4 PromptComplianceConfig

重构后，`PromptComplianceConfig` 仅保留服务端合规校验真正需要的配置：

```python
@dataclass(slots=True)
class PromptComplianceConfig:
    enabled: bool = False
    guardrail: GuardrailProviderConfig = field(default_factory=GuardrailProviderConfig)
    providers: dict[str, dict[str, Any]] = field(default_factory=dict)
```

说明：

- 原先 `slot_schema.root_dir` 这类路径配置应移出 `PromptComplianceConfig`
- 统一资源根目录由 `PromptRuntimeConfig` 管理

### 6.5 GuardrailProviderConfig

`GuardrailProviderConfig` 迁移到 `config` 后，字段语义保持当前实现：

```python
@dataclass(slots=True)
class GuardrailProviderConfig:
    provider: str = "noop"
    timeout: float = 10.0
    policy_id: str = ""
    endpoint: str = ""
    region: str = ""
    credentials_ref: str = ""
    config: dict[str, Any] = field(default_factory=dict)
```

### 6.6 环境变量约束

保留当前 `.env` 中与远端相关的字段，但重构后：

- 可以继续存在
- 默认不参与本地模式装配
- 文档中应标记为“预留扩展字段”

## 7. 统一资源来源抽象

### 7.1 抽象命名

统一资源来源抽象命名为：

- `PromptResourceSource`

### 7.2 抽象职责

`PromptResourceSource` 的职责是：

- 描述资源树来自哪里
- 提供文件级读取能力
- 屏蔽本地/远端来源差异
- 统一纳入 `catalog / provider / cache` 这组来源控制能力

它不负责：

- 理解 `scenario_code / version / language`
- 决定模板、slot schema、prompt 文件的路径规则
- 解析业务语义

### 7.3 对上层暴露的粒度

`PromptResourceSource` 对上层只暴露文件级能力：

```python
class PromptResourceSource(Protocol):
    source_type: str

    def read_text(self, *, relative_path: str) -> str: ...

    def read_json(self, *, relative_path: str) -> dict[str, Any]: ...

    def exists(self, *, relative_path: str) -> bool: ...
```

说明：

- 上层 loader 自己拼相对路径
- source 只负责“这个相对路径如何从当前来源读取出来”
- 上层 loader、orchestrator、builder 不直接依赖 `catalog / provider / cache`

### 7.4 下层组成

`PromptResourceSource` 由以下底层抽象支撑：

- `catalog`
- `provider`
- `cache`

它们的职责调整如下：

- `catalog`
  负责确定资源来源入口或资源根信息
- `provider`
  负责按 locator 获取原始内容
- `cache`
  作为未来扩展能力保留，以可选依赖方式挂入 `PromptResourceSource`

说明：

- 这三者不是仅作为未来扩展的旁路抽象保留
- 它们要被纳入当前 `PromptResourceSource` 主流程
- 区别仅在于当前主流程只装配本地实现
- 当前本地模式下，`cache` 默认传 `None`

### 7.5 当前本地实现

当前唯一落地实现为：

- `LocalPromptResourceSource`

其行为：

- 通过本地 `catalog` 确定资源根
- 通过本地 `provider` 执行实际文件读取
- `cache` 接口纳入主流程边界，但当前本地模式默认不注入缓存实现
- 根据 `PromptRuntimeConfig.local_root_dir` 作为资源根目录
- 直接读取本地文本和 JSON 文件
- 不经过远端拉取
- 不经过默认缓存

## 8. 资源加载设计

### 8.1 上层 loader 保留

以下 loader 继续保留：

- `ScenarioLoader`
- `TemplateLoader`
- `SlotSchemaLoader`
- `PromptResourceLoader`
- `PromptResourceRegistry`

### 8.2 loader 与 source 的关系

所有上层 loader 改为依赖 `PromptResourceSource`，不再直接依赖本地文件路径。

示意：

```python
class TemplateLoader:
    def __init__(self, *, source: PromptResourceSource) -> None: ...

    def load(self, *, reference: PromptReference) -> str: ...
```

### 8.3 路径规则保持在 loader 层

各 loader 自己维护路径规则：

- `ScenarioLoader`
  - `scenarios/<version>/<language>/scenarios.json`
- `TemplateLoader`
  - `templates/<scenario_code>/<version>/<language>/template.md`
- `SlotSchemaLoader`
  - `slots/<scenario_code>/<version>/<language>/slot.json`
- `PromptResourceLoader`
  - `prompts/<analysis_action>/<version>/<language>/system.md`
  - `prompts/<analysis_action>/<version>/<language>/user.md`

### 8.4 PromptLoader 处理结论

`PromptLoader` 从本次目标架构中移除。

原因：

- 当前 A2A-T 主链路并不依赖“通用 prompt 资产加载器”
- 真正被使用的是四类 A2A-T 业务资源 loader
- 继续保留 `PromptLoader` 会加剧“两套资源链并存”的问题

### 8.5 catalog / provider / cache 的处理结论

这些概念继续保留，但不再围绕 `PromptLoader` 组织，而是围绕 `PromptResourceSource` 组织。

结论如下：

- 保留抽象
- 保留本地实现
- 删除 URL / Agent 的具体实现
- 删除 `LocalFilePromptStore` 默认实现
- 当前本地模式直接读文件，`cache` 默认传 `None`

补充说明：

- `catalog / provider / cache` 必须进入当前主流程装配
- 当前并不是“source 直接绕开 catalog/provider”
- 而是“source 在当前本地模式下，使用本地 catalog 与本地 provider 完成资源定位与读取”

## 9. 公共 builder 设计

### 9.1 放置位置

公共 builder 放在：

- `src/a2a_t/prompt/builders`

### 9.2 设计原则

公共 builder：

- 负责根据 `A2ATConfig` 装配 prompt 公共运行时依赖
- 不负责直接构建 client/server orchestrator

client/server 的 orchestrator 仍由各自包内 builder 构建。

### 9.3 返回形式

公共 builder 返回聚合对象：

- `PromptRuntimeComponents`

建议模型：

```python
@dataclass(slots=True)
class PromptRuntimeComponents:
    resource_source: PromptResourceSource
    resource_registry: PromptResourceRegistry
    scenario_loader: ScenarioLoader
    template_loader: TemplateLoader
    slot_schema_loader: SlotSchemaLoader
    prompt_resource_loader: PromptResourceLoader
    slot_validator: SlotValidator
    guardrail: SafetyGuardrail
```

说明：

- `guardrail` 虽然当前只有 server 使用，但属于 prompt 公共能力，放在聚合对象中是合理的

### 9.4 公共 builder 本体

建议新增：

- `PromptRuntimeComponentsBuilder`

职责：

1. 根据 `A2ATConfig.prompt` 构建 `PromptResourceSource`
2. 在构建 `PromptResourceSource` 时，显式装配 `catalog / provider / cache`
3. 基于 source 构建各类资源 loader
4. 构建 `PromptResourceRegistry`
5. 构建 `SlotValidator`
6. 根据 `A2ATConfig.prompt_compliance.guardrail` 构建 `SafetyGuardrail`

## 10. client / server 应用层 builder

### 10.1 client builder

新增：

- `src/a2a_t/client/prompt/prompt_generation_orchestrator_builder.py`

职责：

- 接收 `A2ATConfig`
- 直接创建或接收 `LLMClient`
- 调用 `PromptRuntimeComponentsBuilder`
- 基于 `PromptRuntimeComponents`、`LLMClient` 组装 `PromptGenerationOrchestrator`
- 在 builder 内创建 `ScenarioRecognizer` 与 `SlotExtractor`

### 10.2 server builder

新增：

- `src/a2a_t/server/prompt_compliance/prompt_compliance_orchestrator_builder.py`

职责：

- 接收 `A2ATConfig`
- 直接创建或接收 `LLMClient`
- 调用 `PromptRuntimeComponentsBuilder`
- 基于 `PromptRuntimeComponents`、`LLMClient` 组装 `PromptComplianceOrchestrator`
- 在 builder 内创建共享 `SlotExtractor`

### 10.3 设计边界

client/server builder 必须留在各自包内，不能放到 `config` 或公共 builder 中。

原因：

- orchestrator 是应用层对象
- 其输入输出模型、失败码、日志语义都带有明显 client/server 语义

## 11. client 集成方案

### 11.1 PromptClient 改造方向

`PromptClient` 改造后不再直接手工 new：

- `ScenarioLoader`
- `TemplateLoader`
- `SlotSchemaLoader`
- `PromptResourceLoader`
- `SlotValidator`

而是改为：

1. 读取 `A2ATConfig`
2. 创建或接收 `LLMClient`
3. 调用 `PromptGenerationOrchestratorBuilder`
4. 保存生成好的 orchestrator

### 11.2 client 运行流程

```text
PromptClient
-> PromptGenerationOrchestratorBuilder
-> PromptRuntimeComponentsBuilder
-> PromptRuntimeComponents
-> PromptGenerationOrchestrator
```

### 11.3 client 对配置的使用

client 主链路至少显式消费：

- `A2ATConfig.prompt`

其中包括：

- `A2ATConfig.prompt.language`
- `A2ATConfig.prompt.prompt_resource_version`

不再依赖散落的默认值和隐式 `getattr()` 风格配置访问。

## 12. server 集成方案

### 12.1 server 运行流程

```text
PromptHandler
-> PromptComplianceOrchestratorBuilder
-> PromptRuntimeComponentsBuilder
-> PromptRuntimeComponents
-> PromptComplianceOrchestrator
```

### 12.2 server 对配置的使用

server 主链路至少显式消费：

- `A2ATConfig.prompt`
- `A2ATConfig.prompt_compliance`

### 12.3 guardrail 装配

server 不再自己手工决定 guardrail 依赖构造逻辑，而是统一通过公共 builder 输出的 `guardrail` 使用。

### 12.4 server 对 LLM 的装配

server 与 client 保持一致，不通过公共 `PromptRuntimeComponentsBuilder` 构建 `LLMClient`。

约束如下：

- `PromptComplianceOrchestratorBuilder` 直接创建或接收 `LLMClient`
- builder 基于 `LLMClient` 创建共享 `SlotExtractor`
- 公共 builder 不感知 LLM 生命周期

## 13. 删除与迁移清单

### 13.1 删除的主链路能力

删除或退出主方案的对象：

- `PromptLoader`
- `UrlPromptCatalog`
- `AgentPromptCatalog`
- `UrlProvider`
- `AgentProvider`
- `LocalFilePromptStore`

### 13.2 保留的抽象与扩展槽位

继续保留：

- `PromptResourceSource`
- `catalog` 抽象
- `provider` 抽象
- `cache` 抽象
- `source_type`
- registry 抽象

### 13.3 配置迁移

从运行时包迁移到 `config`：

- `PromptRuntimeConfig`
- `GuardrailProviderConfig`
- `PromptComplianceConfig`
- `SlotSchemaConfig`

### 13.4 client/server 新增

新增：

- `prompt/builders/prompt_runtime_components_builder.py`
- `prompt/builders/prompt_runtime_components.py`
- `client/prompt/prompt_generation_orchestrator_builder.py`
- `server/prompt_compliance/prompt_compliance_orchestrator_builder.py`

## 14. 测试要求

### 14.1 config

补充或调整测试：

- `A2ATConfig` 不再依赖 `prompt` / `server` 包中的配置模型
- `.env` 到配置对象的映射正确
- 远端预留字段保留但不参与本地模式装配

### 14.2 prompt/resources

补充或调整测试：

- `PromptResourceSource` 本地实现的 `read_text / read_json / exists`
- 各类 loader 基于 source 的路径拼接正确
- `PromptResourceRegistry` 语言 fallback 正确
- 删除 URL / Agent 后顶层导出正确收口

### 14.3 prompt/builders

补充测试：

- `PromptRuntimeComponentsBuilder` 根据 `A2ATConfig` 能正确产出聚合依赖
- 当前本地模式下不会创建远端 provider，且 `cache` 默认传 `None`
- guardrail 装配正确

### 14.4 client

补充或调整测试：

- `PromptClient` 通过 builder 构建 orchestrator
- `PromptGenerationOrchestratorBuilder` 能正确组装公共依赖
- `PromptGenerationOrchestratorBuilder` 负责创建或接收 `LLMClient`，并注入 `ScenarioRecognizer` / `SlotExtractor`

### 14.5 server

补充或调整测试：

- `PromptComplianceOrchestratorBuilder` 能正确组装公共依赖
- `PromptComplianceOrchestratorBuilder` 负责创建或接收 `LLMClient`，并注入共享 `SlotExtractor`
- server 运行链继续正确返回 `PromptComplianceResult`

## 15. 风险与约束

1. 这是一次跨 `config / prompt / client / server` 的结构重构，必须分步骤落地
2. `PromptLoader` 删除后，所有仍依赖它的调用方和测试都需要一起清理
3. `catalog / provider / cache` 的语义将发生变化，命名和导出需要同步收口
4. builder 层一旦职责不清，容易重新长成“大一统工厂”，必须严格控制边界

## 16. 实施顺序建议

建议按以下顺序实施：

1. 配置模型迁移到 `config`
2. 引入 `PromptResourceSource` 及其本地实现
3. 让四类 A2A-T 资源 loader 改为依赖 source
4. 删除 `PromptLoader` 主链和远端具体实现
5. 新增 `PromptRuntimeComponents` 与公共 builder
6. 新增 client/server 各自 orchestrator builder
7. 改造 `PromptClient` 和 server 装配入口
8. 清理旧导出与冗余测试

## 17. 结论

本次重构的最终落点是：

- 配置模型归 `config`
- 公共运行时归 `prompt`
- 应用层 orchestrator builder 归 `client / server`
- 统一资源来源抽象命名为 `PromptResourceSource`
- 当前只保留本地来源实现

重构完成后，代码将从“分散装配、两套资源链并存”的状态，收敛为“统一配置域 + 统一资源来源抽象 + 统一公共依赖 builder + client/server 应用层 builder”的结构。

## 18. 相关讨论记录

本轮讨论结论已经全部收敛到本文档。

后续正式开发以本文档为准。
