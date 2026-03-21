# a2a-t-sdk

Python A2A SDK for Telecom Scenarios.

## Overview

This SDK extends the official [a2a-python](https://github.com/a2a/a2a-python) SDK with features tailored for telecom operator environments.

## Features

- **A2A Client SDK**: O域客户端，支持发送自然语言请求
- **A2A Server SDK**: OMC域服务端，接收并处理请求
- **Prompt场景化**: 模板加载、匹配、校验，支持远端和内置模板
- **上下文压缩**: 多种压缩策略可配置
- **连接池管理**: 客户端和服务端双侧连接池
- **限流保护**: 服务端限流，保护领域大模型
- **LLM集成**: 支持HTTP/gRPC/MQ/插件四种集成方式

## Installation

```bash
pip install a2a-t-sdk
```

## Quick Start

```python
from a2a_t_sdk import ExtendedClient

# Create client
client = ExtendedClient(url="http://localhost:8080")

# Send request
response = client.send(task_id="task-001", params={"prompt": "查询基站状态"})
```

## Development

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest

# Lint
ruff check src/

# Type check
mypy src/
```

## Project Structure

```
a2a-t-sdk/
├── src/a2a_t_sdk/     # Main package
│   ├── common/        # Common utilities
│   ├── client/        # Client extensions
│   ├── server/        # Server extensions
│   ├── prompt/        # Prompt management
│   ├── compression/   # Compression strategies
│   ├── llm/          # LLM adapters
│   └── config/       # Configuration
├── templates/         # Built-in templates
├── tests/            # Test suite
├── docs/             # Documentation
└── examples/         # Usage examples
```

## License

Apache License 2.0
