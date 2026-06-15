# a2a-t-sdk

## Project Overview

`a2a-t-sdk` is a Python SDK targeting telecom scenarios, used to generate task prompts and handle task negotiation flows.

This SDK is primarily aimed at two types of users:

- Client: Generates task prompts based on user input, and initiates, receives, and advances negotiation flows.
- Server: Validates `processed task prompts` that conform to the SDK format, and initiates, receives, and advances negotiation flows.

## Core Capabilities

- Task prompt generation pipeline: Covers input normalization, scenario recognition, slot extraction, slot validation, and task prompt rendering.
- Client API: Provides a task prompt generation result stream, along with negotiation entry points such as `start_negotiation`, `receive_negotiation`, and `continue_negotiation`.
- Server validation API: Targets `processed task prompts` that conform to the SDK format, performing metadata parsing, slot extraction, and slot validation, with support for optional guardrail hooks.
- Negotiation types: Includes four built-in negotiation types: `information`, `clarification`, `feasibility`, and `fulfillment`.
- Resource organization: Built-in prompt resources are located in `package_data/prompt_resources`, containing `prompts`, `scenarios`, `slots`, and `templates`.
- Built-in example scenario: The current package only provides `subscribe_incident`.

## Project Structure

The core code of the repository is located in `src/a2a_t`, with the main modules as follows:

- `client`: Client wrapper, providing task prompt generation and negotiation entry points.
- `server`: Server wrapper, providing `processed task prompt` validation and negotiation entry points.
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
- `pytest-asyncio`
- `pytest-cov`
- `ruff`
- `mypy`

The recommended minimal development workflow is as follows:

```bash
uv sync --dev
uv run pytest
uv run ruff check .
uv run mypy src
```

The `tests/` directory contains test cases for client prompt generation, server validation, negotiation runtime, prompt resources, and LLM adaptation. For external contributors, it is recommended to prioritize running the tests and static checks relevant to the current change.

## Current Scope of Support

Before use, it is recommended to confirm the following limitations:

- The built-in LLM invocation chain is unified externally as an OpenAI-compatible adaptation layer.
- The built-in guardrail mechanism currently only provides `noop`.
- Prompt resources currently only support local files.
- Negotiation state storage currently only provides an in-memory implementation and does not guarantee persistence.
- The bundled resources and language coverage are limited, and do not include remote resource loading capabilities such as `registry`.
- This document primarily introduces the SDK itself, and does not cover the CLI, hosted services, deployment processes, or ready-to-use application solutions.

## License

This project is licensed under the [Apache-2.0](LICENSE) license.
