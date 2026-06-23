<!--
Copyright (c) 2026 Huawei Technologies Co., Ltd.
All Rights Reserved.

SPDX-License-Identifier: Apache-2.0

   Licensed under the Apache License, Version 2.0 (the "License"); you may
   not use this file except in compliance with the License. You may obtain
   a copy of the License at

        http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
   WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
   License for the specific language governing permissions and limitations
   under the License.
-->

# a2a-t-sdk-python

<p align="center">
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/python-3.12+-blue.svg" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-Apache%202.0-green.svg" alt="License"></a>
</p>

<p align="center">
  <strong>基于A2A-T协议用于生成任务提示词并处理任务协商流程的Python SDK。</strong>
  <br>
  Python SDK used to generate task prompts and handle task negotiation flows based on the A2A-T protocol.
</p>

<p align="center">
  <a href="./README.md">English</a>
</p>

---

## 项目简介

`a2a-t-sdk-python` 是一个面向电信场景的 Python SDK，用于生成任务提示词并处理任务协商流程。

这个 SDK 主要面向两类使用方：

- 客户端：根据用户输入生成任务提示词，并发起、接收和推进协商流程。
- 服务端：校验符合 SDK 格式的 `processed task prompt`（处理后的任务提示词），并发起、接收和推进协商流程。

## 核心能力

- 任务提示词生成链路：覆盖输入归一化、场景识别、槽位提取、任务提示词渲染。
- 客户端 API：提供任务提示词生成结果流，以及 `start_negotiation`、`receive_negotiation`、`continue_negotiation` 等协商入口。
- 服务端校验 API：面向符合 SDK 格式的 `processed task prompt`，执行场景识别、槽位提取和槽位校验。
- 协商类型：内置 `information`协商类型。
- 资源组织：内置提示词资源位于 `package_data/prompt_resources`，包含 `prompts`、`scenarios`、`slots`、`templates`。

## 项目结构

仓库的核心代码位于 `src/a2a_t`，主要模块如下：

- `client`：客户端封装，提供任务提示词生成与协商入口。
- `server`：服务端封装，提供 A2A-T 协议报文的校验和协商入口。
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

开始前，建议先将 `package_data/env.example` 复制为 `package_data/.env`。

## 开发与测试

项目使用 `uv_build` 作为构建后端，开发依赖包括：

- `pytest`
- `ruff`
- `mypy`

建议的最小开发流程如下：

```bash
cd {项目路径}/a2a-t-sdk-python
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

`tests/` 目录包含客户端提示词生成、服务端校验、协商运行时、提示词资源和 LLM 适配等测试用例。对于外部贡献者，建议优先执行与本次改动相关的测试和静态检查。

## 当前支持范围

使用前建议先确认以下限制：

- 内置 LLM 调用链对外统一为 OpenAI 适配层。
- 提示词资源目前仅支持本地文件。
- 协商状态存储目前仅提供内存实现，不保证持久化。
- 随包资源与语言覆盖有限，不包含 `registry-center`（注册中心）等远程资源加载能力。
- 本文档主要介绍 SDK 本身，不涉及 CLI、托管服务、部署流程或可直接使用的应用方案。

## 许可证

本项目采用 [Apache-2.0](LICENSE) 许可证。
