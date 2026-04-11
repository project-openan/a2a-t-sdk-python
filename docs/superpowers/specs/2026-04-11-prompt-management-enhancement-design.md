# Prompt 管理模块能力增强设计文档

## 1. 背景

当前 `prompt` 管理模块已经提供以下基础能力：

1. 列出可用 Prompt
2. 选择目标 Prompt
3. 加载 Prompt 正文
4. 对远端 Prompt 做解析、校验与缓存

随着 Prompt 资产逐步增多，当前模块在目录组织、注册管理、元数据契约、扩展能力和配置获取方式上暴露出若干问题。本设计文档用于统一规划 Prompt 管理模块增强方案，在保持现有模块边界清晰的前提下，提升可用性、扩展性和可维护性。

## 2. 目标与范围

## 2.1 本次目标

本次设计一次性覆盖以下 8 个增强点：

1. 为 `PromptCatalogRegistry` 提供默认实现
2. 清理 `PromptLoaderConfig` 中无效或未兑现的配置项
3. 审视并补齐 `PromptLoaderError` 相关错误模型
4. 重设计 Prompt front matter 元数据契约
5. 重设计 Prompt 本地缓存布局，改为身份镜像路径
6. 重设计远端 Prompt 与本地缓存发生身份冲突时的冲突决策机制
7. 为 `LocalPromptCatalog` 提供按格式选择 parser 的扩展机制
8. 提供从 `.env` 获取配置的通用能力，并切换当前 `prompt` 模块已使用配置

## 2.2 本次范围

- 保持现有 `prompt` 模块分层边界
- 新增默认 registry 实现，而不是移除现有协议
- 为未来 `json/yaml` Prompt 扩展设计 parser 路由机制
- 提供 SDK 级通用 `.env` 配置读取能力
- 本次仅切换 `prompt` 模块已使用到的配置
- 支持新旧缓存布局的兼容读取
- 统一新布局写入策略

## 2.3 不在本次范围

- 不修改 `docs/SPEC.md`
- 不重做整套 Prompt 管理架构
- 不在本次完整实现 `json/yaml` Prompt 解析协议
- 不要求全 SDK 所有模块立即迁移到 `.env`
- 不修改与 Prompt 管理无关的客户端或服务端逻辑

## 3. 当前问题总结

### 3.1 Catalog Registry 无默认实现

当前 `PromptCatalogRegistry` 仅是协议，调用方需要自行管理 registry 容器，导致：

- SDK 使用门槛偏高
- 用户自定义 Catalog 缺乏统一注册方式
- 上层能力需要额外约定 registry 的实现形态

### 3.2 Prompt 元数据契约偏刚性

当前 Markdown Prompt 解析器将 `title` 视为必填字段，不利于轻量 Prompt 资产管理；同时 `language` 字段缺失时没有统一的默认语义。

### 3.3 本地缓存路径不可读

当前远端 Prompt 缓存使用 `cache_key` 路径，虽然实现简单，但存在以下问题：

- 无法从目录结构直接观察 Prompt 身份
- 排障成本高
- 无法与 `slots/<name>/<version>/<language>/slot.yaml` 形成统一镜像结构

### 3.4 冲突策略能力不足

当前冲突处理主要围绕“是否覆盖”，但未很好支持：

- 按版本号判断新旧
- 非标准版本的降级处理
- 用户自定义冲突决策

### 3.5 LocalPromptCatalog 扩展性不足

当前 `LocalPromptCatalog` 基本将“扫描文件”与“Markdown 解析”耦合在一起，不利于未来扩展 `json/yaml` 等新格式。

### 3.6 配置获取方式分散

当前 Prompt 模块所需配置未统一走 `.env` 配置能力，用户集成时不够一致，也缺少 `env.example` 作为标准示例。

## 4. 设计原则

1. **保持边界稳定**：优先在现有模块之上增强，而不是新起并行体系
2. **身份优先**：Prompt 的主标识始终由 `name/language/version` 确定
3. **目录可读**：本地存储布局应可从路径直接推断 Prompt 身份
4. **扩展优先**：catalog、parser、冲突策略都应支持用户扩展
5. **默认可用**：SDK 提供默认实现，调用方无需手写基础容器
6. **兼容迁移**：对旧缓存布局采用兼容读取策略，避免强制迁移
7. **配置统一**：新增通用 `.env` 能力，减少模块各自零散取值

## 5. 总体架构

本次设计保持 `prompt` 模块现有分层，但在关键位置补充默认实现与扩展点：

- `catalog.py`
  - 负责列出 Prompt 引用
- `catalog_registry.py`
  - 新增默认 registry 实现
- `parser.py`
  - 负责 Prompt 解析
- `loader.py`
  - 负责基于 provider 加载、校验与缓存
- `cache.py`
  - 负责本地镜像存储与冲突决策
- `config.py`
  - 衔接 Prompt 运行时配置
- `.env` 通用配置能力
  - 作为 SDK 级能力，向 `prompt` 模块暴露稳定读取接口

新增或增强的核心抽象如下：

1. `DefaultPromptCatalogRegistry`
2. `PromptParserRegistry` 或 `PromptFormatRouter`
3. `PromptConflictResolutionPolicy` 的版本策略增强
4. SDK 通用 `.env` 配置加载入口

## 6. 模块设计

### 6.1 DefaultPromptCatalogRegistry

#### 设计目标

为 `PromptCatalogRegistry` 提供默认实现，统一管理当前注册的 Catalog，并支持用户注入自定义 Catalog。

#### 建议职责

- 管理 catalog 实例注册表
- 支持默认接入：
  - `LocalPromptCatalog`
  - `UrlPromptCatalog`
  - `AgentPromptCatalog`
- 支持用户自定义 Catalog 的注册、注销与枚举

#### 建议接口

```python
class DefaultPromptCatalogRegistry:
    def register(self, name: str, catalog: PromptCatalog) -> None: ...
    def unregister(self, name: str) -> None: ...
    def get(self, name: str) -> PromptCatalog: ...
    def list_catalogs(self) -> dict[str, PromptCatalog]: ...
```

#### 关键决策

- 保留 `PromptCatalogRegistry` 协议不变
- 新增默认实现，而不是要求所有调用方手写 registry
- 上层依赖继续面向协议，默认实现只解决“开箱即用”问题

### 6.2 PromptParserRegistry / PromptFormatRouter

#### 设计目标

解耦“发现文件”和“如何解析文件”，为未来 `json/yaml` 格式扩展预留机制。

#### 建议职责

- 根据文件扩展名、显式 format 或 source metadata 选择 parser
- 默认注册 `MarkdownPromptParser`
- 允许后续追加：
  - `JsonPromptParser`
  - `YamlPromptParser`

#### 建议接口

```python
class PromptParserRegistry:
    def register(self, format_name: str, parser: PromptParser, extensions: list[str]) -> None: ...
    def get_by_extension(self, extension: str) -> PromptParser: ...
    def list_supported_extensions(self) -> list[str]: ...
```

#### 关键决策

- `LocalPromptCatalog` 不再硬编码 `*.md`
- 改为按“允许扩展名列表”扫描文件
- 扫描到文件后通过 parser registry 路由
- 本次只要求机制设计完整，`json/yaml` 解析协议细节后续单独扩展

### 6.3 Front Matter 元数据契约重设计

#### 新契约

必填字段：

- `name`
- `version`
- `description`

可选字段：

- `language`
  - 缺失时自动补为 `default`
- `title`
  - 缺失时允许为空，不再视为非法

#### 影响范围

- `MarkdownPromptParser`
- `Prompt` / `PromptReference` 模型构造
- catalog 展示逻辑
- metadata 校验错误提示

#### 关键决策

- Prompt 身份仍由 `name/language/version` 确定
- `language` 缺失等价于 `default`
- `title` 不再纳入最小发布契约
- `description` 保持必填，保证 Prompt 的最小语义可读性

### 6.4 Prompt 本地存储布局重设计

#### 目标布局

```text
<cache_root>/
  prompts/
    <prompt_name>/
      <version>/
        <language>/
          prompt.<ext>
          metadata.json
  slots/
    <prompt_name>/
      <version>/
        <language>/
          slot.yaml
```

#### 示例

```text
<cache_root>/prompts/network diagnosis/1.0.0/zh-CN/prompt.md
<cache_root>/prompts/network diagnosis/1.0.0/zh-CN/metadata.json
<cache_root>/slots/network diagnosis/1.0.0/zh-CN/slot.yaml
```

#### 关键决策

- `name/language/version` 作为主存储路径
- 文件名不再依赖 `cache_key`
- `prompt.<ext>` 中的扩展名由 parser / source format 决定
- `metadata.json` 存储来源、抓取时间、冲突处理结果等附加信息

### 6.5 冲突策略设计

#### 冲突定义

当加载远端 Prompt 时，如果目标身份镜像路径已存在内容，则视为发生冲突。

#### 默认策略

- Prompt 主身份固定为：`name/language/version`
- 默认冲突处理规则：
  - **新版本覆盖旧版本**

#### 版本比较规则

- 优先使用语义化版本比较
- 若版本字符串不符合 semver：
  - 可降级为受控比较逻辑
  - 或交给用户自定义策略处理

#### 建议接口

```python
class PromptConflictResolutionPolicy(Protocol):
    def should_overwrite(
        self,
        *,
        existing_record: PromptRecord,
        new_record: PromptRecord,
    ) -> bool: ...
```

#### 默认实现

- `OverwriteOnConflictPolicy`：无条件覆盖
- `OverwriteIfNewerVersionPolicy`：仅新版本覆盖旧版本

本次默认推荐使用 `OverwriteIfNewerVersionPolicy`。

### 6.6 PromptLoaderConfig 清理

#### 问题

`PromptLoaderConfig` 中存在 `allow_stale_fallback` 等当前行为与需求不完全一致、或语义上需要重新审视的字段。

#### 设计要求

- 对 `PromptLoaderConfig` 的字段逐项审视：
  - 是否真的被使用
  - 是否与当前行为一致
  - 是否仍然属于 Prompt 模块应暴露的配置

#### 处理原则

- 未兑现的能力不应继续以正式配置项暴露
- 若能力保留，则需要补齐设计语义与实现
- 若能力不保留，则需要在本次中移除或标记弃用

### 6.7 PromptLoaderError 审视与补齐

#### 设计目标

让错误模型能够准确表达：

- 配置错误
- 源获取错误
- 解析错误
- 元数据契约错误
- 本地存储错误
- 冲突策略拒绝
- 版本比较失败

#### 原则

- “获取失败” 与 “解析失败” 分开
- “Prompt 格式不合法” 与 “元数据不满足契约” 分开
- “冲突存在” 与 “冲突策略拒绝覆盖” 分开

本次应审视当前 `PromptLoaderError` 体系中所有未兑现错误类型，并明确：

1. 保留并补齐实现
2. 删除
3. 合并进其他错误类型

### 6.8 通用 `.env` 配置能力

#### 设计目标

新增 SDK 通用 `.env` 配置加载能力，并将当前 `prompt` 模块所使用配置迁移到这套机制之上。

#### 范围

- 本次提供通用配置读取能力
- 本次切换 `prompt` 模块当前用到的配置
- 其他模块是否迁移，后续按需推进

#### 建议能力

- 支持读取 `.env`
- 支持默认值
- 支持必填校验
- 支持环境变量覆盖
- 提供 `env.example`

#### 建议边界

- Prompt 模块不直接散落读取环境变量
- 而是通过统一配置入口获取所需值

## 7. 数据流设计

### 7.1 Catalog 列举流程

1. `DefaultPromptCatalogRegistry` 管理所有 catalog
2. 调用方从 registry 获取 catalog 列表
3. `LocalPromptCatalog` 扫描允许扩展名文件
4. `PromptParserRegistry` 选择 parser
5. parser 解析为 `PromptReference`

### 7.2 Prompt 加载流程

1. 调用方提供 `PromptReference`
2. `PromptLoader` 调用 provider 获取内容
3. parser 解析内容并校验元数据契约
4. 根据 Prompt 身份计算镜像路径
5. 如本地已有记录，则执行冲突策略
6. 写入：
   - `prompt.<ext>`
   - `metadata.json`

### 7.3 冲突处理流程

1. 发现目标身份路径已有内容
2. 构造 existing/new 两份记录
3. 进行版本比较
4. 默认策略判断是否覆盖
5. 若拒绝覆盖，返回明确错误

## 8. 兼容策略

### 8.1 Registry 兼容

- 保留 `PromptCatalogRegistry` 作为协议
- 新增默认实现，不破坏现有注入方式

### 8.2 Parser 兼容

- 默认继续支持 Markdown
- `MarkdownPromptParser` 只放宽契约，不移除原有主路径

### 8.3 缓存布局兼容

采用以下策略：

- **兼容读取旧布局**
- **统一写入新布局**

原因：

1. 避免强制用户迁移既有缓存
2. 降低升级风险
3. 允许系统逐步自然收敛到新布局

### 8.4 配置兼容

- 新增 `.env` 配置能力
- Prompt 模块迁移到统一配置入口
- 保持必要的默认值与显式错误提示

## 9. 测试策略

### 9.1 单元测试

覆盖以下模块：

- `DefaultPromptCatalogRegistry`
  - 注册
  - 注销
  - 枚举
  - 获取
- `MarkdownPromptParser`
  - `name/version/description` 必填
  - `language` 缺失补 `default`
  - `title` 缺失可接受
- `PromptParserRegistry`
  - 按扩展名路由
  - 未注册格式报错
- `LocalPromptCatalog`
  - 可配置扩展名扫描
  - 文件发现后正确路由 parser
- `PromptLoader`
  - 新身份镜像路径写入
  - 旧布局兼容读取
  - metadata 正确写入
- 冲突策略
  - 新版本覆盖旧版本
  - 同版本冲突
  - 非标准版本降级比较
- `.env` 配置能力
  - 默认值
  - 必填校验
  - 覆盖顺序

### 9.2 集成测试

覆盖真实链路：

- registry + catalog + parser
- loader + provider + cache
- 远端 Prompt 写入新布局
- 旧布局兼容读取
- 用户自定义 catalog 注册后可被发现与使用

### 9.3 文档与配置验证

- 校验 `env.example` 是否与实际使用配置一致
- 校验 README / 用法文档是否反映新注册方式与缓存布局

## 10. 风险与待确认项

1. 当前 `PromptLoaderConfig.allow_stale_fallback` 是否应彻底移除，还是需要在能力上补齐并保留
2. 旧缓存布局兼容读取的时间窗口是否需要限制
3. 非标准版本字符串的默认比较策略是否需要固定为某种受控规则
4. `prompt.<ext>` 的格式来源，是以文件扩展名为准，还是以解析后的显式 format 字段为准
5. `.env` 通用能力应放在 `config` 模块内部，还是提炼成 SDK 级更通用的配置工具

## 11. 关键决策总结

1. `PromptCatalogRegistry` 保留协议，并新增默认实现 `DefaultPromptCatalogRegistry`
2. 用户自定义 Catalog 通过默认 registry 的注册接口统一管理
3. Prompt parser 改为按格式路由，为 `json/yaml` 扩展预留机制
4. front matter 新契约为：
   - 必填：`name`、`version`、`description`
   - 可选：`language`、`title`
   - `language` 缺失补 `default`
5. Prompt 本地存储改为身份镜像路径，不再以 `cache_key` 作为主定位路径
6. 冲突处理走策略接口，默认采用“新版本覆盖旧版本”
7. 版本比较优先使用语义化版本，非标准版本支持降级处理或交由策略接管
8. 提供 SDK 通用 `.env` 配置能力，并在本次将 `prompt` 模块配置切换过去
9. 缓存布局迁移采用“兼容读旧布局、统一写新布局”
