# 1 a2a-t-sdk-python User Guide

## 1.1 Feature Introduction
### 1.1.1 What is A2A-T?
A2A-T (Agent-to-Agent Telecom) is a multi-agent interconnection protocol for the telecom domain based on the A2A protocol, designed specifically for complex collaboration scenarios in the telecom domain.
General-purpose agent interconnection protocols in the industry mainly focus on agent interconnection and interaction frameworks, with insufficient attention to business scenarios and specific interaction content, resulting in low success rates for task completion. Business scenarios in the telecom domain are complex and demanding, requiring a dedicated protocol to support the interconnection and collaboration of O&M agents. The A2A-T solution is based on the A2A protocol, focusing on application extensions for enhanced capabilities related to telecom domain business flow information models, task negotiation, and collaboration security.

### 1.1.2 Relationship Between A2A-T SDK and A2A SDK
 
The A2A-T protocol is an extension based on the A2A protocol. The A2A-T SDK is provided for the protocol extension content, supporting rapid construction of agents for complex collaboration scenarios in the telecom domain. A2A-T SDK is independent of A2A SDK. By integrating both A2A-T SDK and A2A SDK, you can build agents that support the A2A-T protocol, enabling deterministic, highly reliable, efficient, and secure collaboration among multiple agents in the telecom domain.

### 1.1.3 Capability Introduction

a2a-t-sdk-python is a Python SDK designed for telecom agent collaboration scenarios, used to generate, validate, and negotiate task prompts in A2A-T interactions. The SDK is suitable for integration by client Agents, server Agents, and upper-layer orchestration systems.

Main capabilities include:

- **Task Prompt Generation**: The client generates a processed task prompt conforming to the A2A-T format based on natural language or structured input.
- **Server Prompt Validation**: The server validates whether the processed task prompt submitted by the client matches the scenario, template, and slot constraints.
- **Multi-round Negotiation**: Supports negotiation processes: `information`.
- **Prompt Resource Management**: Built-in scenario, slot, template, and system prompt resources, with support for local file resource loading.
- **LLM Adaptation**: Connects to external large language models through OpenAI-compatible API calls.

## 1.2 Application Scenarios

The Python SDK is typically used in the following scenarios:

1. The client Agent receives user intent, generates a structured task prompt, and sends it to the target Agent via the A2A protocol.
2. The server Agent receives the task prompt, performs format, scenario, and slot validation, and then proceeds to business processing.
3. When task information is insufficient, task objectives are unclear, or capability feasibility needs confirmation, both parties advance multi-round interaction through the SDK negotiation interface.

## 1.3 Environment Requirements

| Item                  | Requirement                                                                        |
|-----------------------|------------------------------------------------------------------------------------|
| Python SDK            | Python 3.12+                                                                       |
| Dependency management | Recommended to use `uv`                                                            |
| LLM                   | Requires an accessible OpenAI service and API Key                                  |
| Operating system      | Linux, Windows, and macOS are all suitable for development and integration testing |

## 1.4 Installation

### 1.4.1 Install SDK from Source

```bash
cd {project_path}/a2a-t-sdk-python
uv sync --dev
```

Run tests to confirm the environment is ready:

```bash
uv run pytest
```

### 1.4.2 Prepare SDK Configuration

Copy the environment variable template:

```bash
cd {project_path}/a2a-t-sdk-python
cp package_data/env.example package_data/.env
```

At least the following must be configured:

```properties
A2AT_LANGUAGE=zh-CN
A2AT_PROMPT_SOURCE_TYPE=local_file
A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=
A2AT_PROMPT_COMPLIANCE_ENABLED=true
A2AT_LLM_PROVIDER=deepseek
A2AT_LLM_MODEL=deepseek-chat
A2AT_LLM_API_KEY={your_api_key}
A2AT_LLM_BASE_URL=https://api.deepseek.com
A2AT_NEGOTIATION_STATE_STORE_TYPE=in_memory
```

If `A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR` is empty, the SDK uses the built-in package resources by default. When custom scenarios, slots, or templates are needed, set this configuration to point to the custom resource root directory.

## 1.5 Configuration Quick Reference

| Configuration Item                    | Description                                               |
|---------------------------------------|-----------------------------------------------------------|
| `A2AT_LANGUAGE`                       | Prompt resource language, commonly `zh-CN` or `en-US`     |
| `A2AT_PROMPT_SOURCE_TYPE`             | Prompt resource source, currently supports `local_file`   |
| `A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR` | Custom prompt resource root directory                     |
| `A2AT_PROMPT_COMPLIANCE_ENABLED`      | Whether to enable server prompt compliance validation     |
| `A2AT_LLM_PROVIDER`                   | LLM provider                                              |
| `A2AT_LLM_MODEL`                      | Model name                                                |
| `A2AT_LLM_API_KEY`                    | LLM API Key                                               |
| `A2AT_LLM_BASE_URL`                   | LLM service address                                       |
| `A2AT_NEGOTIATION_STATE_STORE_TYPE`   | Negotiation state storage, currently supports `in_memory` |

## 1.6 Constraints and Limitations

1. The built-in negotiation state storage is an in-memory implementation and does not provide persistence.
2. Prompt resources are currently mainly local files and do not include remote resource loading from the registry center.
3. The SDK is responsible for A2A-T prompt generation, validation, and negotiation, and is not responsible for AgentCard registration, authentication, service hosting, or business execution.