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
</p>
<p align="center">
  <a href="./README.md">English</a>
</p>

---

## 项目简介

A2A-T（Agent-to-Agent Telecom）是基于 A2A 协议扩展的电信领域多智能体互联协议，针对电信业务场景的信息模型、任务协商、协作安全等能力进行增强，支撑电信领域多智能体之间确定性、高可靠、高效且安全的协同。

`a2a-t-sdk-python` 是 A2A-T 协议的 Python SDK，核心职责是在 A2A-T 交互中**生成、校验与协商任务提示词**（结构化协议报文）。SDK 主要面向两类使用方：

- **客户端 Agent**：将自然语言或结构化输入转化为符合 A2A-T 格式的任务提示词，并发起、接收和推进协商流程。
- **服务端 Agent**：校验客户端下发的 A2A-T 报文是否满足场景、模板与槽位约束，提取参数并推进协商流程。

A2A-T SDK 独立于 A2A SDK，两者配合使用即可构建完整支持 A2A-T 协议的智能体（Python 生态中可搭配的 A2A SDK 为 `a2a-sdk`）：

```mermaid
flowchart LR
    subgraph Server["服务端 Agent"]
        B2["A2A SDK (a2a-sdk)<br/>A2A 传输"] --> B1["A2A-T Server SDK<br/>报文校验 / 参数提取 / 协商"]
        B1 --> B0["业务代码"]
    end
    subgraph Client["客户端 Agent"]
        A0["业务代码"] --> A1["A2A-T Client SDK<br/>生成任务提示词 / 协商报文"]
        A1 --> A2["A2A SDK (a2a-sdk)<br/>A2A 传输"]
    end
    Client -- "HTTP A2A-T 请求" --> Server
    Server -- "HTTP A2A-T 响应" --> Client
```

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 任务提示词生成（客户端） | 覆盖输入归一化、场景识别、槽位提取与模板渲染，支持自然语言与结构化数据两种输入 |
| 报文校验与提参（服务端） | 对符合 SDK 格式的任务提示词执行元数据解析、槽位提取与语义校验，按 Schema 提取参数并返回缺失/非法槽位明细 |
| 协商内容 API | 支持 `information` / `feasibility` / `target` 三类协商及 `abort` 终止消息，提供模板驱动的协商报文生成与校验；协商会话状态随消息 metadata（`negotiationContext`）往返，SDK 本身无状态 |
| 资源组织 | 内置提示词资源（`prompts` / `scenarios` / `slots` / `templates` / `negotiation-vocabulary`）随包提供，支持 `packaged`（已安装包）与 `local_file`（本地文件）两种加载方式 |
| LLM 适配 | 通过 OpenAI 兼容调用链接入外部大模型，可重试失败码支持有界重试 |
| 内置示例 | 随仓库提供 `subscribe_incident`（事件订阅）与 `negotiation`（协商闭环）等可运行示例场景，未配置 API Key 时自动降级为脚本化 Mock LLM，零外部依赖即可端到端跑通 |

## 项目结构

仓库以 `uv` 组织，核心代码位于 `src/a2a_t`：

| 模块 | 说明 |
| --- | --- |
| `client` | 客户端封装，提供任务提示词生成与协商入口（`A2ATClient`） |
| `server` | 服务端封装，提供 A2A-T 协议报文的校验与协商入口（`A2ATServer`） |
| `core` | 模板寻址、metadata 模型、校验管线与结构化双语错误模型 |
| `common/prompt_resources` | 内置提示词资源的打包与加载（`packaged` / `local_file`） |
| `config` | 基于 `.env` 的配置加载与配置模型 |
| `llm` | LLM 适配层，默认提供 OpenAI 兼容客户端，支持自定义 LLM 接入 |
| `prompt` | 提示词资源分析、槽位提取、模板渲染与校验 |
| `negotiation` | 协商内容模型、生成管线与校验管线 |
| `a2a-t-sample` | 可运行的客户端/服务端示例用例集 |
| `a2a-t-corpus` | 准确性验证语料（纯测试资产）：数据驱动工作流用例，与 Java 仓逐字节共享 |

`tests/` 目录镜像包结构，覆盖提示词生成、服务端校验、协商管线、提示词资源与 LLM 适配等测试用例。

## 快速开始

### 环境要求

| 项 | 要求 |
| --- | --- |
| Python | `>=3.12` |
| 依赖管理 | `uv`（推荐） |
| LLM | 可选。未配置 API Key 时示例自动降级为脚本化 Mock LLM，零外部依赖即可跑通 |

### 三步跑通首个 Demo

以 `subscribe_incident`（事件订阅）场景为例：客户端基于自然语言输入生成 Notification-T 任务提示词，经真实 HTTP A2A 链路发送给服务端；服务端校验报文、建立事件订阅并流式推送 Incident 通知。以下命令均在 **`a2a-t-sample` 目录**执行（`.env` 位于该目录）：

```bash
# 1. 安装依赖并准备环境配置（A2AT_LLM_API_KEY 留空时自动使用 mock LLM）
cp env.example .env
uv pip install -r requirements.txt

# 2. 终端一：启动注册中心（端口 5001）
#    启动前先设置模块搜索路径（.env 位于 a2a-t-sample 目录）：
#    PowerShell：$env:PYTHONPATH = "$pwd\subscribe-incident\src"
#    bash：      export PYTHONPATH="$(pwd)/subscribe-incident/src"
uv run python -m agentcard_example.registry_main

# 3. 终端二启动服务端（端口 8000）、终端三启动客户端（持续接收 artifact，Ctrl+C 停止）
#    两个终端同样先按第 2 步设置 PYTHONPATH
uv run python -m server_example.server_main
uv run python -m client_example.client_main
```

启动后可观察到完整链路日志：客户端场景识别与槽位提取、生成的任务提示词、A2A 请求报文，以及服务端校验结果和 Incident 通知推送。

> Windows 控制台如遇中文乱码，先执行 `chcp 65001`。

### 接入真实 LLM（可选）

编辑 `a2a-t-sample/.env`，填入任一 OpenAI 兼容端点：

```properties
# LLM协议类型
A2AT_LLM_PROVIDER=openai
# 模型名称
A2AT_LLM_MODEL=<模型名>
# 模型地址
A2AT_LLM_BASE_URL=<OpenAI 兼容地址>
# LLM API KEY
A2AT_LLM_API_KEY=<你的 API Key>
```

全部配置项说明见根目录 `env.example`。

### 开发与测试

```bash
cd {项目路径}/a2a-t-sdk-python
uv sync --dev
uv run pytest        # 运行全部测试
uv run ruff check .  # 静态检查
uv run mypy src      # 类型检查
```

更多可运行示例（事件订阅端到端、协商闭环等）见 [a2a-t-sample/README-zh.md](a2a-t-sample/README-zh.md)。

## 更多文档

| 文档 | 位置 | 内容 |
| --- | --- | --- |
| 开发指南 | [docs/zh/开发指南.md](docs/zh/开发指南.md) | 特性介绍、安装接入、参数配置与最小实践 |
| API 参考 | [docs/zh/API参考.md](docs/zh/API参考.md) | `A2ATClient` / `A2ATServer` 全量 API 定义与使用说明 |

## 当前支持范围

使用前建议先确认以下限制：

- 内置 LLM 调用链对外统一为 OpenAI 适配层。
- 大模型提示词资源已内置，不支持自定义扩展（`prompts` 与 `errors` 恒从已安装包加载）。
- 协商会话状态随消息 metadata 往返（SDK 无状态），不提供协商状态存储；旧的状态机协商 API（`start_negotiation` / `receive_negotiation` / `continue_negotiation`）自 1.1.0 起废弃。
- 随包资源与语言覆盖有限，不包含 `registry-center`（注册中心）等远程资源加载能力。
- 本文档主要介绍 SDK 本身，不涉及 CLI、托管服务、部署流程或可直接使用的应用方案。

## 许可证

本项目采用 [Apache-2.0](LICENSE) 许可证。
