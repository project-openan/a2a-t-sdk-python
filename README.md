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
  <strong>Python SDK used to generate task prompts and handle task negotiation flows based on the A2A-T protocol.</strong>
  <br>
  基于A2A-T协议用于生成任务提示词并处理任务协商流程的Python SDK。
</p>

<p align="center">
  <a href="./README_zh.md">中文</a>
</p>

---

## Project Overview

`a2a-t-sdk-python` is a Python SDK targeting telecom scenarios, used to generate task prompts and handle task negotiation flows.

This SDK is primarily aimed at two types of users:

- Client: Generates task prompts based on user input, and initiates, receives, and advances negotiation flows.
- Server: Validates `processed task prompts` that conform to the SDK format, and initiates, receives, and advances negotiation flows.

## Core Capabilities

- Task prompt generation pipeline: Covers input normalization, scenario recognition, slot extraction, and task prompt rendering.
- Client API: Provides a task prompt generation result stream, along with negotiation entry points such as `start_negotiation`, `receive_negotiation`, and `continue_negotiation`.
- Server validation API: Targets `processed task prompts` that conform to the SDK format, performing metadata parsing, slot extraction.
- Negotiation types: Includes one built-in negotiation type: `information`.
- Resource organization: Built-in prompt resources are located in `package_data/prompt_resources`, containing `prompts`, `scenarios`, `slots`, and `templates`.

## Project Structure

The core code of the repository is located in `src/a2a_t`, with the main modules as follows:

- `client`: Client wrapper, providing task prompt generation and negotiation entry points.
- `server`: Server wrapper, providing validation and negotiation entry points for A2A-T protocol messages.
- `common`: Shared prompt resource loading and common runtime capabilities.
- `config`: Model-related configuration and its loading logic.
- `llm`: LLM adaptation layer, client, and session storage abstraction.
- `negotiation`: Negotiation types, runtime processing, and state storage.
- `prompt`: Capabilities related to task prompt formatting, analysis, rendering, and validation.

## Installation and Environment Requirements

- Python requirement: `>=3.12`
- Package name: `a2a-t-sdk`
- License: `Apache-2.0`
- Build backend: `uv_build`

Before getting started, it is recommended to first copy `package_data/env.example` to `package_data/.env`.

## Development and Testing

The project uses `uv_build` as its build backend. Development dependencies include:

- `pytest`
- `ruff`
- `mypy`

The recommended minimal development workflow is as follows:

```bash
cd {project_path}/a2a-t-sdk-python
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

The `tests/` directory contains test cases for client prompt generation, server validation, negotiation runtime, prompt resources, and LLM adaptation. For external contributors, it is recommended to prioritize running the tests and static checks relevant to the current change.

## Current Scope of Support

Before use, it is recommended to confirm the following limitations:

- The built-in LLM invocation chain is unified externally as an OpenAI adaptation layer.
- Prompt resources currently only support local files.
- Negotiation state storage currently only provides an in-memory implementation and does not guarantee persistence.
- The bundled resources and language coverage are limited, and do not include remote resource loading capabilities such as `registry-center`.
- This document primarily introduces the SDK itself, and does not cover the CLI, hosted services, deployment processes, or ready-to-use application solutions.

## License

This project is licensed under the [Apache-2.0](LICENSE) license.
