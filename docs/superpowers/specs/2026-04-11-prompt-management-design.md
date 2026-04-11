# Prompt 管理最终设计文档

## 1. 设计目标

Prompt 管理模块面向调用方提供两段核心能力：
1. 列出可用 Prompt
2. 加载调用方已选定的 Prompt 正文

当前实现同时覆盖以下目标：
- 提供默认可用的 Catalog Registry
- 支持本地、URL、Agent 三类 Prompt 来源
- 支持 Prompt front matter 解析与发布契约校验
- 支持远端 Prompt 的本地镜像落盘与冲突处理
- 提供统一的 `.env` 配置读取能力
- 为未来 `json` / `yaml` Prompt 扩展预留 parser 路由机制

## 3. 非目标

当前版本不负责：
- Prompt 编辑、发布、审核、治理
- 内建 Prompt 搜索/排序/优选策略
- 通用 CLI 能力
- 完整实现 `json` / `yaml` Prompt parser

## 4. 模块边界

当前 Prompt 管理主链路如下：

```text
PromptCatalogRegistry
-> PromptCatalog
-> PromptReference
-> PromptLoader
-> PromptProvider
-> PromptParser
-> Prompt
```

其中：
- `PromptCatalogRegistry` 负责管理可用 catalog
- `PromptCatalog` 负责列出某一来源下的 Prompt 引用
- `PromptLoader` 负责基于引用加载 Prompt 正文
- `PromptProvider` 负责按来源获取原始内容
- `PromptParser` 负责解析 front matter 与正文
- `PromptStore` 负责远端 Prompt 的本地镜像存储

## 5. 当前代码结构

当前相关代码位于：
- `src/a2a_t/prompt/catalog_registry.py`
- `src/a2a_t/prompt/catalog.py`
- `src/a2a_t/prompt/config.py`
- `src/a2a_t/prompt/loader.py`
- `src/a2a_t/prompt/cache.py`
- `src/a2a_t/prompt/parser.py`
- `src/a2a_t/prompt/providers.py`
- `src/a2a_t/prompt/models.py`
- `src/a2a_t/prompt/errors.py`

当前仓库中仍保留 `src/a2a_t/prompt/factory.py`，用于默认装配辅助；但模块的核心设计仍以组件显式装配为主，不要求上层必须依赖 factory。

## 6. Registry 设计

### 6.1 协议

当前保留协议：
- `PromptCatalogRegistry`

核心接口：
- `list_catalogs() -> dict[str, PromptCatalog]`

### 6.2 默认实现

当前已实现：
- `DefaultPromptCatalogRegistry`

核心能力：
- `register(name, catalog)`
- `unregister(name)`
- `get(name)`
- `list_catalogs()`

设计结论：
- 保留 registry 协议，便于替换
- 提供默认实现，解决“开箱即用”问题
- 用户自定义 Catalog 通过 `register()` 统一注册管理

## 7. Catalog 设计

### 7.1 LocalPromptCatalog

当前实现：
- 扫描 `PromptLoaderConfig.allowed_extensions` 指定的扩展名
- 不再硬编码只扫描 `*.md`
- 通过 `PromptParserRegistry` 按扩展名选择 parser

### 7.2 UrlPromptCatalog

当前实现：
- 读取索引 URL
- 解析索引中的 Prompt 条目
- 生成 `PromptReference`

### 7.3 AgentPromptCatalog

当前实现：
- 基于 `AgentCard.extensions` 定位 Prompt 扩展
- 支持从配置中读取默认 extension URI 与 override
- 支持从配置中读取索引参数 key 与 override
- 最终通过 URL 索引展开出 `PromptReference`

## 8. Parser 设计

### 8.1 PromptParserRegistry

当前已实现：
- `PromptParserRegistry`
- `build_default_prompt_parser_registry()`

默认行为：
- 默认注册 `MarkdownPromptParser`
- 默认支持扩展名 `.md`

设计结论：
- “按格式选择 parser”的机制已经建立
- 当前默认只落地 Markdown
- 后续新增 `json` / `yaml` 时，沿用 registry 扩展即可

### 8.2 Markdown front matter 契约

当前代码实现的 front matter 契约为：
- 必选：`name`、`version`、`description`
- 可选：`language`、`title`
- `language` 缺失时补为 `default`
- `title` 缺失时补为空字符串

Prompt 身份仍由以下字段唯一确定：
- `name`
- `version`
- `language`

## 9. 配置设计

### 9.1 当前配置入口

当前 Prompt 模块统一使用：
- `PromptLoaderConfig.from_env(env)`

配置来自：
- `src/a2a_t/config/env.py`
- `.env`
- `env.example`

### 9.2 当前生效配置项

`PromptLoaderConfig` 当前包含：
- `default_ttl`
- `local_prompt_dir`
- `allowed_extensions`
- `default_prompt_extension_uri`
- `prompt_extension_uri_overrides`
- `default_prompt_index_url_param_key`
- `prompt_index_url_param_key_overrides`

对应环境变量：
- `A2AT_PROMPT_DEFAULT_TTL_SECONDS`
- `A2AT_PROMPT_LOCAL_DIR`
- `A2AT_PROMPT_ALLOWED_EXTENSIONS`
- `A2AT_DEFAULT_PROMPT_EXTENSION_URI`
- `A2AT_PROMPT_EXTENSION_URI_OVERRIDES`
- `A2AT_DEFAULT_PROMPT_INDEX_URL_PARAM_KEY`
- `A2AT_PROMPT_INDEX_URL_PARAM_KEY_OVERRIDES`

### 9.3 设计结论

- Prompt 配置已统一从 `.env` 读取
- 不再区分额外的 runtime config / loader config
- 各组件直接接收 `PromptLoaderConfig` 或从中拆出的明确参数

## 10. 本地镜像存储设计

### 10.1 存储根目录

当前远端 Prompt 直接镜像存放到：
- `A2AT_PROMPT_LOCAL_DIR`

默认值：
- `./prompts`

### 10.2 布局规则

当前本地镜像布局为：

```text
<A2AT_PROMPT_LOCAL_DIR>/
  <prompt_name>/
    <version>/
      <language>/
        prompt.<ext>
        metadata.json
```

示例：

```text
./prompts/network diagnosis/1.0.0/zh-CN/prompt.md
./prompts/network diagnosis/1.0.0/zh-CN/metadata.json
```

### 10.3 metadata.json

当前 `metadata.json` 至少承载：
- `name`
- `version`
- `language`
- `format`
- `source_type`
- `source_locator`
- `parser_name`
- `content_hash`
- `fetched_at`
- `expires_at`

在发生覆盖写入时，还会补充：
- `overwrite_reason`
- `previous_content_hash`

## 11. 冲突处理设计

### 11.1 冲突策略接口

当前保留：
- `ConflictResolutionPolicy`

### 11.2 当前实现

当前已实现：
- `OverwriteOnConflictPolicy`
- `OverwriteIfNewerVersionPolicy`

默认策略为：
- `OverwriteIfNewerVersionPolicy`

### 11.3 版本号比较规则

当前实现使用“逐位比较”的点分数字版本号规则：
- `1.2` 等价于 `1.2.0`
- `1.10.0` > `1.2.9`
- 非纯数字点分版本号会报错并拒绝比较

### 11.4 当前行为结论

当发生身份冲突时：
- 新版本大于旧版本：允许覆盖
- 新版本等于旧版本：允许覆盖同路径内容
- 新版本小于旧版本：拒绝覆盖
- 覆盖旧版本目录时，会清理旧目录并记录覆盖原因

## 12. PromptLoader 设计

### 12.1 主入口

当前主入口为：
- `PromptLoader.load(reference=..., refresh=False)`

同时为了兼容已有测试与内部调用，仍保留 legacy kwargs 解析逻辑，但设计主路径以 `PromptReference` 为准。

### 12.2 行为

- 本地文件来源直接读取并解析
- 远端来源先查本地镜像
- 命中未过期缓存且未强制刷新时直接返回
- 过期或刷新时重新拉取
- 拉取成功后按身份镜像布局写回本地
- 远端刷新失败且已有 stale cache 时允许回退使用旧内容

## 13. 对外导出

当前 `src/a2a_t/prompt/__init__.py` 已导出：
- `DefaultPromptCatalogRegistry`
- `PromptLoaderConfig`
- `PromptParserRegistry`
- `LocalPromptCatalog`
- `UrlPromptCatalog`
- `AgentPromptCatalog`
- `PromptLoader`
- `LocalFilePromptStore`
- `OverwriteIfNewerVersionPolicy`
- 其他相关模型、provider、error

## 14. 测试结论

当前实现已通过以下方向的测试覆盖：
- Prompt catalog / registry
- parser registry 与格式路由
- PromptLoader 行为
- cache store 与冲突策略
- Prompt 领域模型与配置读取

## 15. 最终结论

Prompt 管理模块的最终设计可总结为：
- 使用 `DefaultPromptCatalogRegistry` 统一管理 Catalog
- 使用 `PromptLoaderConfig.from_env()` 统一管理配置
- 使用 `PromptParserRegistry` 实现按格式路由 parser
- 使用身份镜像布局统一管理本地 Prompt
- 使用可替换的冲突策略处理远端 Prompt 落盘冲突
- 当前默认以 Markdown Prompt 为主，已为后续多格式扩展预留机制

