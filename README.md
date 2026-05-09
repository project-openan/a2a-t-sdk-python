# a2a-t-sdk

## 项目简介

`a2a-t-sdk` 是一个面向电信场景的 Python SDK，用于生成任务提示词并处理任务协商流程。当前版本为 `0.1.0`，处于 **Alpha** 阶段，适合用于原型验证、接口联调和能力评估，暂不建议直接用于生产环境。

这个 SDK 主要面向两类使用方：

- 客户端：根据用户输入生成任务提示词，并发起、接收和推进协商流程。
- 服务端：校验符合 SDK 格式的 `processed task prompt`（处理后的任务提示词），并发起、接收和推进协商流程。

## 核心能力

- 任务提示词生成链路：覆盖输入归一化、场景识别、槽位提取、槽位校验、任务提示词渲染。
- 客户端 API：提供任务提示词生成结果流，以及 `start_negotiation`、`receive_negotiation`、`continue_negotiation` 等协商入口。
- 服务端校验 API：面向符合 SDK 格式的 `processed task prompt`，执行元数据解析、槽位提取和槽位校验，并支持可选的 guardrail（防护）钩子。
- 协商类型：内置 `information`、`clarification`、`feasibility`、`fulfillment` 四类协商类型。
- 资源组织：内置提示词资源位于 `package_data/prompt_resources`，包含 `prompts`、`scenarios`、`slots`、`templates`。
- 内置示例场景：当前随包仅提供 `subscribe_incident`。

## 项目结构

仓库的核心代码位于 `src/a2a_t`，主要模块如下：

- `client`：客户端封装，提供任务提示词生成与协商入口。
- `server`：服务端封装，提供 `processed task prompt` 校验与协商入口。
- `common`：共享的提示词资源加载与运行时公共能力。
- `config`：模型相关配置及其加载逻辑。
- `llm`：LLM 适配层、客户端和会话存储抽象。
- `negotiation`：协商类型、运行时处理与状态存储。
- `prompt`：任务提示词格式、分析、渲染与校验相关能力。

## 安装与环境要求

- Python 要求：`>=3.12`
- 包名：`a2a-t-sdk`
- 许可证：`Apache-2.0`
- 构建后端：`uv_build`
当前版本更适合通过源码方式试用、联调和评估。开始前，建议先将 `package_data/env.example` 复制为 `package_data/.env`。

## 开发与测试

项目使用 `uv_build` 作为构建后端，开发依赖包括：

- `pytest`
- `pytest-asyncio`
- `pytest-cov`
- `ruff`
- `mypy`

建议的最小开发流程如下：

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

`tests/` 目录包含客户端提示词生成、服务端校验、协商运行时、提示词资源和 LLM 适配等测试用例。对于外部贡献者，建议优先执行与本次改动相关的测试和静态检查。

## 当前支持范围

使用前建议先确认以下限制：

- 项目整体处于 **Alpha** 阶段，接口和资源组织仍可能调整。
- 内置 LLM 调用链对外统一为 OpenAI-compatible 适配层。
- 内置 guardrail 机制目前仅提供 `noop`。
- 提示词资源目前仅支持本地文件。
- 协商状态存储目前仅提供内存实现，不保证持久化。
- 随包资源与语言覆盖有限，不包含 `registry`（注册中心）等远程资源加载能力。
- 本文档主要介绍 SDK 本身，不涉及 CLI、托管服务、部署流程或可直接使用的应用方案。

## 许可证

本项目采用 [Apache-2.0](LICENSE) 许可证。
