# Python A2A SDK 电信场景扩展版规格文档

## 一、需求简介

### 1.1、需求背景

A2A（Agent2Agent）协议是一个开放标准，旨在实现独立 AI Agent 系统之间的通信和互操作。随着电信运营商数字化转型的深入，O域（Operations域，负责网络运维）与 OMC域（Operations and Maintenance Center，负责集中管理）之间的智能化交互需求日益增长。

当前场景下，O域的业务系统需要向 OMC域的智能Agent发送自然语言形式的请求，由 OMC域进行意图识别、参数提取、业务校验后，调度领域大模型进行处理，最后将结果返回。整个流程涉及：
- **A2A通信**：跨域的标准化Agent通信
- **Prompt场景化**：基于模板的请求校验与管理
- **上下文压缩**：减少冗余信息传递，降低大模型调用开销

本项目基于官方 `a2a-python` SDK 进行扩展，开发一套适用于电信场景的 A2A SDK（代号 a2a-t-sdk），支持快速集成与部署。

### 1.2、需求范围

**主要功能范围：**

| 功能 | 说明 |
|------|------|
| A2A Client SDK | O域客户端，支持发送自然语言请求 |
| A2A Server SDK | OMC域服务端，接收并处理请求 |
| Prompt场景化 | 模板加载、匹配、校验，支持远端和内置模板 |
| 上下文压缩 | 多种压缩策略可配置，保证准确性的同时提升效率 |
| 连接池管理 | 客户端和服务端双侧连接池 |
| 限流保护 | 服务端限流，保护领域大模型 |
| LLM集成 | 支持HTTP/gRPC/MQ/插件四种集成方式 |
| 错误处理 | 结构化错误信息，支持可扩展的错误格式模板 |

**非功能范围：**
- SDK基于Python 3.14开发
- 遵循官方a2a-python SDK的接口规范
- 支持YAML配置 + 环境变量覆盖

---

## 二、系统上下文

### 2.1、概述

```
┌──────────────────────────────────────────────────────────────────────┐
│                           a2a-t-sdk 整体架构                           │
├──────────────────────────────────────────────────────────────────────┤
│                                                                      │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │                         O域 (Operations)                        │  │
│  │  ┌─────────────────┐    ┌──────────────────────────────────┐  │  │
│  │  │  A2A Client SDK │◀──▶│          A2A Agent               │  │  │
│  │  │  - 连接池        │    │          (业务逻辑，外部系统)      │  │  │
│  │  │  - 模板构造      │    └──────────────────────────────────┘  │  │
│  │  │  - 压缩能力      │                                              │  │
│  │  └─────────────────┘                                              │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                               │                                     │
│                    A2A协议 (HTTP/JSON-RPC)                           │
│                               │                                     │
│                               ▼                                     │
│  ┌─────────────────────────────────────────────────────────────────┐  │
│  │                         OMC域                                    │  │
│  │  ┌───────────────────────────────────────────────────────────┐  │  │
│  │  │                  A2A Server SDK                          │  │  │
│  │  │                                                          │  │  │
│  │  │  ┌─────────────────┐    ┌────────────────────────────┐   │  │  │
│  │  │  │  Prompt处理层    │───│    压缩处理层             │   │  │  │
│  │  │  │  - 模板加载器   │    │    - 可配置策略链        │   │  │  │
│  │  │  │  - 模板校验器   │    │    - 关键词抽取          │   │  │  │
│  │  │  │  - 错误返回     │    │    - LLM摘要             │   │  │  │
│  │  │  └─────────────────┘    │    - 混合策略            │   │  │  │
│  │  │           │              └────────────┬─────────────┘   │  │  │
│  │  │           │                           │                 │  │  │
│  │  │           ▼                           ▼                 │  │  │
│  │  │  ┌─────────────────────────────────────────────┐        │  │  │
│  │  │  │            LLM集成适配层                    │        │  │  │
│  │  │  │  HTTP │ gRPC │ 消息队列 │ 插件化           │        │  │  │
│  │  │  └─────────────────────────────────────────────┘        │  │  │
│  │  │                          │                             │  │  │
│  │  │  ┌─────────────────┐    ┌─────────────────────────┐   │  │  │
│  │  │  │   连接池         │    │   限流器                 │   │  │  │
│  │  │  └─────────────────┘    └─────────────────────────┘   │  │  │
│  │  └───────────────────────────────────────────────────────────┘  │  │
│  └─────────────────────────────────────────────────────────────────┘  │
│                                                                      │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.2、外部依赖

| 依赖项 | 说明 | 来源 |
|--------|------|------|
| a2a-python | 官方A2A Python SDK | https://github.com/a2aproject/a2a-python |
| httpx | HTTP客户端 | 官方SDK传递依赖 |
| uvicorn/starlette | ASGI服务器 | 官方SDK传递依赖 |
| pydantic | 数据验证 | 官方SDK传递依赖 |
| pyyaml | YAML配置解析 | 配置加载模块 |
| aiokafka | Kafka异步客户端 | MQ集成模块（可选） |
| redis | Redis客户端 | 限流配置模块（可选） |

### 2.3、系统上下文

**通信流程：**

1. O域A2A Client 发送自然语言请求
2. OMC域A2A Server 接收请求
3. Server通过Prompt处理层（模板校验）
4. 通过压缩处理层压缩上下文（可选）
5. 领域大模型处理请求
6. 结果通过A2A协议返回给O域A2A Client

**关键设计决策：**

| 决策点 | 选择 |
|--------|------|
| SDK基础 | 基于官方a2a-python SDK扩展 |
| 模板格式 | Markdown格式的自然语言模板 |
| 校验失败处理 | 返回结构化错误信息给客户端 |
| 压缩失败处理 | 重试 → 失败后跳过压缩（静默降级） |
| 模板来源 | 远端HTTP URL + 内置本地模板 |
| 模板缓存 | 启动时加载 + 定期刷新 + 手动刷新 |
| 压缩策略 | 可配置策略链 |
| LLM集成 | HTTP/gRPC/Kafka MQ/插件四种方式 |
| MQ消息格式 | JSON |
| MQ通信模式 | 同步请求-响应 + 异步回调 |
| 插件机制 | entry_points注册 + 版本号校验 |
| 限流方式 | 单机限流（Redis存储配置） |
| 语义匹配 | 可配置开关，通过外部LLM实现 |

---

## 三、系统功能性分析

### 3.1、A2A客户端扩展

#### 功能列表

| 功能 | 说明 | 优先级 |
|------|------|--------|
| 连接池 | 复用HTTP/gRPC连接，减少开销 | P0 |
| Prompt模板构造 | 使用模板构造请求消息 | P1 |
| 上下文压缩 | 客户端侧压缩后再发送 | P1 |
| 基础A2A通信 | 继承官方Client全部能力 | P0 |

#### 使用场景

- O域业务系统集成A2A Client SDK
- 发送自然语言请求到OMC域
- 可选使用模板构造标准化的请求
- 可选在发送前压缩上下文

### 3.2、A2A服务端扩展

#### 功能列表

| 功能 | 说明 | 优先级 |
|------|------|--------|
| Prompt校验 | 模板匹配 + 格式校验 + 业务规则校验 | P0 |
| 上下文压缩 | 可配置压缩策略链 | P0 |
| 限流保护 | 保护领域大模型不被过载 | P0 |
| 连接池 | 服务端侧连接管理 | P1 |
| LLM集成 | 多种方式调用领域大模型 | P0 |

#### 使用场景

- OMC域Agent集成A2A Server SDK
- 接收O域发送的请求
- 校验通过后调用领域大模型
- 将大模型结果通过A2A返回

### 3.3、Prompt场景化

#### 模板管理

| 功能 | 说明 |
|------|------|
| 远端模板加载 | 支持HTTP URL获取模板 |
| 内置模板加载 | 支持本地文件作为模板 |
| 模板缓存 | 启动加载 + 定期刷新 + 手动刷新 |
| 模板注册 | 运行时注册和管理模板 |
| 模板匹配 | 根据请求内容匹配适用模板 |

#### 模板校验

| 校验类型 | 说明 |
|----------|------|
| 必填字段 | 检查必填字段是否存在 |
| 格式校验 | 检查字段格式是否正确（如日期、电话） |
| 业务规则 | 检查业务规则是否满足（如金额范围、权限） |

#### 错误处理

校验失败时返回结构化错误信息：

```json
{
  "errorCode": 400,
  "errorInfo": "Prompt校验失败",
  "details": [
    {
      "field": "device_type",
      "code": "MISSING_REQUIRED_FIELD",
      "message": "必填字段 device_type 缺失"
    }
  ]
}
```

### 3.4、上下文压缩

#### 压缩原则

- **准确性优先**：保证信息准确，关键信息不丢失
- **LLM可解析**：压缩后内容需能被领域大模型正确解析
- **效率兼顾**：在满足前两条的前提下提升压缩效率

#### 压缩策略

| 策略 | 说明 | 适用场景 |
|------|------|----------|
| 关键词抽取 | 提取关键信息，去除冗余 | 简单查询 |
| LLM摘要 | 使用LLM生成摘要 | 复杂分析 |
| 混合策略 | 组合多种策略 | 通用场景 |

#### LLM摘要策略配置

**使用的LLM**：可配置选择
- 与领域大模型共用同一套LLM配置
- 或配置独立的LLM adapter（用于摘要的轻量LLM）

**摘要Prompt**：支持多种配置方式
- 内置默认Prompt
- 可配置的Prompt模板
- 支持模板文件

**配置示例**：
```yaml
compression:
  summarizer:
    # LLM adapter选择：domain（共用水大模型）或 dedicated（独立摘要LLM）
    adapter_mode: "domain"  # domain | dedicated
    dedicated_adapter_type: "http"  # 当adapter_mode=dedicated时使用
    dedicated_adapter_config:
      url: "http://lightweight-llm.example.com/api/v1/summarize"
      api_key: "${SUMMARIZER_API_KEY}"
    # Prompt配置
    prompt_mode: "template"  # builtin | template | file
    prompt_template: |
      请将以下文本压缩为简洁的摘要，保留关键信息：
      {{content}}
    prompt_file: "./templates/compression_prompt.md"  # 当prompt_mode=file时使用
```

#### 策略配置

```yaml
compression:
  enabled: true
  default_strategies:
    - "keyword_extractor"
    - "llm_summarizer"
  max_retries: 3
  retry_delay: 1.0
```

#### 失败处理

| 阶段 | 处理策略 |
|------|----------|
| 压缩执行失败 | 重试（最多3次） |
| 重试失败 | 跳过压缩，使用原始内容 |
| 不返回错误 | 静默降级，不影响业务流程 |

### 3.5、LLM集成

#### 支持的集成方式

| 方式 | 说明 | 配置参数 |
|------|------|----------|
| HTTP | 直接HTTP API调用 | url, api_key, timeout |
| gRPC | gRPC协议调用 | host, port, proto_path |
| MQ | Kafka消息队列 | bootstrap_servers, request_topic, response_topic, group_id |
| 插件 | 用户自定义适配器 | plugin配置 |

#### MQ集成详细设计

**Broker类型**：Apache Kafka

**消息格式**：JSON

**通信模式**：
| 模式 | 说明 | 适用场景 |
|------|------|----------|
| 同步 | 发送请求后等待响应返回 | 实时性要求高的场景 |
| 异步 | 发送请求后通过回调接收响应 | 需要高吞吐量的场景 |

**异步回调机制**：
- 请求消息包含 `correlation_id` 用于匹配响应
- 响应消息通过 `response_topic` 主题投递
- SDK内部维护消费者线程池监听响应主题
- 通过 `correlation_id` 将响应路由到对应的请求

**消息格式示例**：
```json
// 请求消息
{
  "correlation_id": "uuid-xxx",
  "prompt": "用户请求内容",
  "model": "domain-llm",
  "temperature": 0.7,
  "timestamp": "2026-03-21T10:00:00Z"
}

// 响应消息
{
  "correlation_id": "uuid-xxx",
  "content": "LLM响应内容",
  "model": "domain-llm",
  "usage": {"prompt_tokens": 100, "completion_tokens": 50},
  "timestamp": "2026-03-21T10:00:01Z"
}
```

#### 插件化架构详细设计

**插件发现机制**：通过Python `entry_points` 注册

**插件配置示例**：
```python
# setup.py 或 pyproject.toml
from setuptools import setup

setup(
    name="my-llm-adapter",
    entry_points={
        "a2a_t_sdk.llm_adapters": [
            "my_adapter=my_package.adapter:MyLLMAdapter",
        ],
    },
)
```

**版本管理**：接口版本号校验

- `LLMAdapter` 接口包含 `interface_version` 属性
- SDK加载插件时校验版本兼容性
- 不兼容版本拒绝加载并抛出异常

**运行环境**：与主进程共享环境

**配置传递**：
| 方式 | 说明 |
|------|------|
| 配置文件 | 适配器可读取共享配置文件 |
| 构造函数参数 | 通过 `adapter_config` 字典传入 |

**插件接口契约**：
```python
class LLMAdapter(ABC):
    """LLM适配器基类"""
    interface_version: str = "1.0"  # 接口版本号

    @property
    def adapter_type(self) -> str:
        """适配器类型标识"""
        pass

    @abstractmethod
    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """调用LLM"""
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
```

---

## 四、系统其他DFX需求分析

### 4.1、性能需求

| 指标 | 要求 |
|------|------|
| 连接池复用 | 减少连接建立开销，提升吞吐量 |
| 模板缓存 | 避免每次请求都从远端加载模板 |
| 异步处理 | 全链路异步设计，支持高并发 |

### 4.2、可靠性需求

#### 错误分类与处理策略

| 错误类型 | 触发场景 | 处理策略 | 返回给客户端 | 说明 |
|----------|----------|----------|-------------|------|
| **校验类错误** | Prompt校验失败（模板不匹配、字段缺失、格式错误、业务规则不通过） | 返回结构化错误信息 | **是** | 业务问题，需要客户端知晓并修正 |
| **处理类错误** | 压缩失败（策略执行异常、内容超出处理能力） | 重试 → 失败后跳过压缩 | **否** | 内部优化问题，优雅降级不影响业务流程 |
| **LLM调用错误** | LLM服务不可用、超时、响应格式错误 | 可配置重试策略 | **是** | 需要客户端知晓异常情况 |
| **限流错误** | 请求超出限流阈值 | 返回限流提示 | **是** | 告知客户端限流状态 |
| **系统错误** | 内部异常、未预期错误 | 记录日志、返回通用错误 | **是**（脱敏后） | 保护内部实现细节 |

### 4.3、可扩展性

| 扩展点 | 说明 |
|--------|------|
| LLM适配器 | 插件化，可接入任何LLM |
| 压缩策略 | 策略链可配置，可新增策略 |
| 错误格式 | 模板化，支持自定义 |
| 模板格式 | 支持Markdown模板 |

---

## 五、特性实现分析与设计概述

### 5.1、模块划分

| 模块 | 职责 |
|------|------|
| `common` | 公共模块：错误定义、连接池、日志、工具函数 |
| `client` | 客户端扩展：连接池、模板构造、压缩客户端 |
| `server` | 服务端扩展：Prompt处理、压缩处理、限流、连接池 |
| `prompt` | Prompt管理：模板加载、注册、校验、缓存 |
| `compression` | 压缩模块：策略基类、多种策略、策略链 |
| `llm` | LLM集成：适配器基类、多种适配器、适配器工厂 |
| `config` | 配置管理：配置加载、环境变量覆盖 |

### 5.2、核心设计原则

1. **基于官方SDK扩展**：继承官方 `a2a-python` 的核心能力，保持兼容性
2. **可插拔架构**：各组件通过接口抽象，支持替换和扩展
3. **配置驱动**：通过配置文件管理行为，减少代码改动
4. **错误友好**：校验错误返回给客户端，压缩错误静默降级
5. **异步优先**：全链路异步设计

---

## 六、实现模型

### 6.1、项目结构

```
a2a-t-sdk/
├── src/a2a_t_sdk/
│   ├── __init__.py
│   │
│   ├── common/                           # 公共模块
│   │   ├── __init__.py
│   │   ├── errors.py                    # 公共错误定义 + 错误格式化器
│   │   ├── connection_pool.py           # 连接池（客户端+服务端共用）
│   │   ├── logging.py                   # 日志工具
│   │   └── utils.py                     # 通用工具函数
│   │
│   ├── client/                           # 客户端扩展
│   │   ├── __init__.py
│   │   ├── extended_client.py            # 扩展客户端（继承官方Client）
│   │   ├── prompt_client.py               # 模板构造客户端
│   │   └── compression_client.py         # 压缩客户端
│   │
│   ├── server/                           # 服务端扩展
│   │   ├── __init__.py
│   │   ├── extended_server.py            # 扩展服务端
│   │   ├── prompt_handler.py             # Prompt校验中间件
│   │   ├── compression_handler.py        # 压缩处理中间件
│   │   └── rate_limiter.py               # 限流器
│   │
│   ├── prompt/                           # Prompt管理
│   │   ├── __init__.py
│   │   ├── models.py                     # 模板数据模型
│   │   ├── loader.py                     # 模板加载器（远端+内置）
│   │   ├── registry.py                   # 模板注册表
│   │   ├── validator.py                  # 校验引擎
│   │   ├── cache.py                      # 模板缓存
│   │   └── errors.py                     # 校验错误定义
│   │
│   ├── compression/                      # 压缩模块
│   │   ├── __init__.py
│   │   ├── base.py                       # 压缩策略基类
│   │   ├── strategies/
│   │   │   ├── __init__.py
│   │   │   ├── keyword_extractor.py      # 关键词抽取
│   │   │   ├── llm_summarizer.py         # LLM摘要
│   │   │   └── hybrid_strategy.py        # 混合策略
│   │   ├── chain.py                      # 策略链管理器
│   │   └── errors.py                     # 压缩错误定义
│   │
│   ├── llm/                              # LLM集成层
│   │   ├── __init__.py
│   │   ├── base.py                       # LLM适配器基类
│   │   ├── adapters/
│   │   │   ├── __init__.py
│   │   │   ├── http_adapter.py           # HTTP API适配器
│   │   │   ├── grpc_adapter.py           # gRPC适配器
│   │   │   ├── mq_adapter.py             # 消息队列适配器
│   │   │   └── plugin_adapter.py         # 插件化适配器
│   │   └── factory.py                    # 适配器工厂
│   │
│   └── config/                           # 配置管理
│       ├── __init__.py
│       ├── loader.py                     # 配置加载器
│       └── models.py                     # 配置数据模型
│
├── templates/                            # 内置模板
│   └── *.md
│
├── tests/                               # 测试
│   ├── __init__.py
│   ├── test_client/
│   ├── test_server/
│   ├── test_prompt/
│   ├── test_compression/
│   └── test_llm/
│
├── docs/                                # 文档
├── examples/                            # 示例
├── requirements.txt
├── pyproject.toml
└── README.md
```

### 6.2、数据流

```
┌─────────────────────────────────────────────────────────────────────┐
│                         客户端请求流程                                │
├─────────────────────────────────────────────────────────────────────┤
│                                                                     │
│  O域 Client                      OMC域 Server                       │
│  ───────────                    ──────────────                     │
│                                                                     │
│  ┌─────────────┐                                                 │
│  │ 1. 构造请求  │                                                 │
│  │ - 自然语言   │                                                 │
│  │ - 可选：模板 │                                                 │
│  └──────┬──────┘                                                 │
│         │                                                         │
│         ▼                                                         │
│  ┌─────────────┐                                                 │
│  │ 2. 连接池    │  ── 复用连接 ──▶                                 │
│  │   获取连接  │                                                 │
│  └──────┬──────┘                                                 │
│         │                                                         │
│         │ A2A SendMessage                                         │
│         │ ───────────────────────────────────────▶                │
│         │                                                         │
│         │                    ┌─────────────────┐                   │
│         │                    │ 3. 接收请求     │                   │
│         │                    └────────┬────────┘                   │
│         │                             │                            │
│         │                             ▼                            │
│         │                    ┌─────────────────┐                   │
│         │                    │ 4. 限流检查     │                   │
│         │                    └────────┬────────┘                   │
│         │                             │                            │
│         │                             ▼                            │
│         │                    ┌─────────────────┐                   │
│         │                    │ 5. Prompt校验   │                   │
│         │                    │   - 模板匹配    │                   │
│         │                    │   - 格式校验    │                   │
│         │                    │   - 业务校验    │                   │
│         │                    └────────┬────────┘                   │
│         │                             │                            │
│         │                    ┌────────┴────────┐                   │
│         │                    │ 校验失败?       │                   │
│         │                    └────────┬────────┘                   │
│         │                      是 ↙    ↘ 否                       │
│         │                        │         │                        │
│         │                        ▼         ▼                        │
│         │              ┌─────────────┐  ┌─────────────────┐        │
│         │              │ 6. 返回错误  │  │ 7. 压缩处理     │        │
│         │              │ 结构化信息   │  │   - 策略链     │        │
│         │              └──────┬──────┘  │   - 重试机制    │        │
│         │                     │         └────────┬────────┘        │
│         │                     │                  │                   │
│         │                     │          ┌───────┴───────┐          │
│         │                     │          │  压缩失败?     │          │
│         │                     │          └───────┬───────┘          │
│         │                     │            是 ↙     ↘ 否            │
│         │                     │              │         │             │
│         │                     │              ▼         ▼             │
│         │                     │     ┌──────────┐  ┌─────────┐         │
│         │                     │     │ 重试...  │  │ 8. LLM  │         │
│         │                     │     └─────┬────┘  │  调用   │         │
│         │                     │           │       └────┬────┘        │
│         │                     │           ▼            │              │
│         │                     │     ┌────────┐        │              │
│         │                     │     │ 跳过压缩│        │              │
│         │                     │     └────┬───┘        │              │
│         │                     │          │            │              │
│         │                     │          └──────┬──────┘              │
│         │                     │                 ▼                      │
│         │                     │     ┌─────────────────────┐         │
│         │                     │     │ 9. 领域LLM处理结果   │         │
│         │                     │     └──────────┬──────────┘         │
│         │                     │                │                     │
│         │                     │                ▼                     │
│         │                     │     ┌─────────────────────┐         │
│         │                     │     │ 10. 返回A2A响应      │         │
│         │                     │     └──────────┬──────────┘         │
│         │ ◀─────────────────────────────────────                    │
│         │ A2A响应                                                │
│         │                                                         │
│         ▼                                                         │
│  ┌─────────────┐                                                 │
│  │ 11. 归还连接 │                                                 │
│  │   到连接池   │                                                 │
│  └─────────────┘                                                 │
│                                                                     │
└─────────────────────────────────────────────────────────────────────┘
```

---

## 七、接口设计

### 7.1、Prompt模块接口

```python
# prompt/models.py

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class PromptField:
    """模板字段定义 - 用于描述模板中的参数"""
    name: str                           # 字段名（如: intent, parameters）
    description: str                    # 字段描述
    field_type: str                     # 类型: string, number, boolean, object, array
    required: bool = False              # 是否必填
    default: Optional[str] = None       # 默认值
    validation_rules: list[str] = field(default_factory=list)  # 校验规则列表


@dataclass
class PromptTemplate:
    """Prompt模板 - Markdown格式的自然语言模板"""
    id: str                             # 模板ID
    name: str                           # 模板名称
    version: str                        # 模板版本
    description: str = ""               # 模板描述
    content: str = ""                   # Markdown格式的自然语言模板内容
    fields: list[PromptField] = field(default_factory=list)   # 参数字段定义
    compression_enabled: bool = True    # 是否启用压缩
    compression_strategies: list[str] = field(default_factory=list)  # 压缩策略列表
    metadata: dict = field(default_factory=dict)  # 自定义元数据


@dataclass
class ValidationError:
    """校验错误 - 返回给客户端的结构化错误信息"""
    field: str                          # 出错的字段名
    code: str                           # 错误码（如: MISSING_REQUIRED_FIELD, INVALID_FORMAT）
    message: str                        # 人类可读的错误描述
    details: dict = field(default_factory=dict)  # 额外详情


@dataclass
class ValidationResult:
    """校验结果

    状态说明：
    - success=True: 校验通过
      - template: 匹配的模板
      - resolved_content: 填充参数后的最终内容
      - errors: 空列表

    - success=False: 校验失败
      - template: None
      - resolved_content: 原始消息内容
      - errors: 错误列表
    """
    success: bool                       # 是否校验通过
    errors: list[ValidationError] = field(default_factory=list)  # 错误列表
    template: Optional[PromptTemplate] = None  # 匹配的模板（成功时有值，失败时为None）
    resolved_content: Optional[str] = None     # 填充参数后的最终内容（成功时有值，失败时返回原始消息内容）
```

### 7.2、压缩模块接口

```python
# compression/base.py

from abc import ABC, abstractmethod
from typing import Optional


class CompressionStrategy(ABC):
    """压缩策略基类

    validate方法契约：
    1. 验证压缩比是否合理（如 > 0.3，避免过度压缩）
    2. 验证关键信息是否保留（基于preserve_key_fields）
    3. 验证LLM能否正确解析（语法完整性检查）

    preserve_key_fields的使用方式：
    - 压缩前：策略根据这些字段标记关键信息
    - 验证时：确保这些字段的内容完整保留

    返回值：
    - True: 压缩内容满足所有验证条件
    - False: 任一验证条件不满足，压缩策略链将重试或降级
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        pass

    @abstractmethod
    async def compress(
        self,
        content: str,
        context: Optional[dict] = None
    ) -> str:
        """
        压缩内容

        Args:
            content: 待压缩的内容
            context: 压缩上下文（可选），包含preserve_key_fields等信息

        Returns:
            压缩后的内容
        """
        pass

    @abstractmethod
    async def validate(self, content: str, original: str) -> bool:
        """
        验证压缩后内容是否满足要求

        Args:
            content: 压缩后的内容
            original: 原始内容（用于对比关键信息是否保留）

        Returns:
            是否有效（True表示满足所有验证条件）
        """
        pass
```

```python
# compression/chain.py

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompressionResult:
    """压缩结果"""
    success: bool                           # 是否成功
    original_content: str                   # 原始内容
    compressed_content: Optional[str] = None  # 压缩后内容（失败时为None）
    strategies_used: list[str] = field(default_factory=list)  # 使用的策略
    compression_ratio: Optional[float] = None  # 压缩比
    error: Optional[str] = None             # 错误信息（如果失败）
```

### 7.3、LLM集成接口

```python
# llm/base.py

from abc import ABC, abstractmethod
from typing import Optional, Any
from dataclasses import dataclass, field


@dataclass
class LLMRequest:
    """LLM请求"""
    prompt: str                              # 提示词
    model: Optional[str] = None              # 模型名称
    temperature: float = 0.7                  # 温度参数
    max_tokens: Optional[int] = None         # 最大token数
    extra_params: dict = field(default_factory=dict)  # 额外参数


@dataclass
class LLMResponse:
    """LLM响应"""
    content: str                             # 响应内容
    model: str                               # 实际使用的模型
    usage: dict = field(default_factory=dict)  # token使用量
    metadata: dict = field(default_factory=dict)  # 元数据


class LLMAdapter(ABC):
    """LLM适配器基类 - 支持多种集成方式"""

    @property
    @abstractmethod
    def adapter_type(self) -> str:
        """适配器类型: http, grpc, mq, plugin"""
        pass

    @abstractmethod
    async def invoke(self, request: LLMRequest) -> LLMResponse:
        """
        调用LLM

        Args:
            request: LLM请求

        Returns:
            LLM响应
        """
        pass

    @abstractmethod
    async def health_check(self) -> bool:
        """健康检查"""
        pass
```

### 7.4、配置接口

```python
# config/models.py

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConnectionPoolConfig:
    """连接池配置"""
    max_size: int = 10                    # 最大连接数
    min_size: int = 2                     # 最小连接数
    timeout: float = 30.0                 # 连接超时（秒）
    keep_alive: bool = True               # 保活


@dataclass
class RateLimitConfig:
    """限流配置"""
    enabled: bool = True                   # 是否启用
    requests_per_second: int = 100         # 每秒请求数
    max_concurrent: int = 50               # 最大并发数
    burst_size: int = 200                  # 突发容量
    redis:
        enabled: bool = True               # 是否从Redis读取配置
        key_prefix: str = "a2a_sdk:ratelimit:"  # Redis键前缀


@dataclass
class PromptConfig:
    """Prompt配置"""
    template_sources: list[str] = field(default_factory=list)  # 模板源列表
    cache_ttl: int = 3600                  # 缓存TTL（秒）
    refresh_interval: int = 300             # 定期刷新间隔（秒）


@dataclass
class CompressionConfig:
    """压缩配置"""
    enabled: bool = True                   # 是否启用
    default_strategies: list[str] = field(default_factory=list)  # 默认策略
    max_retries: int = 3                   # 最大重试次数
    retry_delay: float = 1.0               # 重试延迟（秒）
    preserve_key_fields: list[str] = field(default_factory=list)  # 必须保留的字段


@dataclass
class LLMConfig:
    """LLM配置"""
    adapter_type: str = "http"             # 适配器类型
    adapter_config: dict = field(default_factory=dict)  # 适配器配置


@dataclass
class SDKConfig:
    """SDK全局配置"""
    connection_pool: ConnectionPoolConfig = field(default_factory=ConnectionPoolConfig)
    rate_limit: Optional[RateLimitConfig] = None
    prompt: PromptConfig = field(default_factory=PromptConfig)
    compression: CompressionConfig = field(default_factory=CompressionConfig)
    llm: LLMConfig = field(default_factory=LLMConfig)
    log_level: str = "INFO"
```

---

## 八、算法实现设计

### 8.1、模板匹配算法

**输入**：A2A消息内容
**输出**：匹配的PromptTemplate或None

#### 8.1.1、类设计

```python
# prompt/matcher.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


class MatcherStrategy(ABC):
    """模板匹配策略基类"""

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        pass

    @abstractmethod
    async def match(
        self,
        message: str,
        template: PromptTemplate
    ) -> float:
        """
        计算消息与模板的匹配得分

        Args:
            message: 待匹配的消息内容
            template: 模板

        Returns:
            匹配得分 (0.0 - 1.0)
        """
        pass


class KeywordMatcher(MatcherStrategy):
    """关键词匹配策略"""

    def __init__(
        self,
        mode: str = "string",  # "string" | "regex" | "tokenize"
        tokenizer: Optional[callable] = None,
    ):
        self.mode = mode
        self.tokenizer = tokenizer or self._default_tokenize

    @property
    def name(self) -> str:
        return f"keyword_{self.mode}"

    async def match(
        self,
        message: str,
        template: PromptTemplate
    ) -> float:
        keywords = template.metadata.get("keywords", [])
        if not keywords:
            return 0.0

        message_lower = message.lower()
        matched = 0

        for keyword in keywords:
            if self.mode == "string":
                if keyword.lower() in message_lower:
                    matched += 1
            elif self.mode == "regex":
                import re
                if re.search(keyword, message, re.IGNORECASE):
                    matched += 1
            elif self.mode == "tokenize":
                tokens = self.tokenizer(message_lower)
                if keyword.lower() in tokens:
                    matched += 1

        return matched / len(keywords)


class SemanticMatcher(MatcherStrategy):
    """语义匹配策略"""

    def __init__(self, llm_adapter: "LLMAdapter"):
        self._llm_adapter = llm_adapter

    @property
    def name(self) -> str:
        return "semantic"

    async def match(
        self,
        message: str,
        template: PromptTemplate
    ) -> float:
        prompt = f"""
请判断用户消息与模板的匹配程度。

模板名称: {template.name}
模板描述: {template.description}
模板内容摘要: {template.content[:200]}...

用户消息: {message}

请返回一个0到1之间的分数，表示匹配程度。只返回一个数字。
"""
        response = await self._llm_adapter.invoke(
            LLMRequest(prompt=prompt, max_tokens=10)
        )
        try:
            return float(response.content.strip())
        except ValueError:
            return 0.0


@dataclass
class MatchResult:
    """匹配结果"""
    template: PromptTemplate
    score: float
    matched_strategies: list[str]


class TemplateMatcher:
    """
    模板匹配器
    支持多种匹配策略组合
    """

    def __init__(
        self,
        keyword_weight: float = 0.6,
        semantic_weight: float = 0.4,
        threshold: float = 0.5,
    ):
        self._keyword_weight = keyword_weight
        self._semantic_weight = semantic_weight
        self._threshold = threshold
        self._strategies: list[MatcherStrategy] = []

    def add_strategy(self, strategy: MatcherStrategy):
        """添加匹配策略"""
        self._strategies.append(strategy)

    async def match(
        self,
        message: str,
        templates: list[PromptTemplate]
    ) -> Optional[MatchResult]:
        """
        匹配消息与模板

        Args:
            message: 待匹配的消息
            templates: 模板列表

        Returns:
            匹配结果，无匹配时返回None
        """
        best_result: Optional[MatchResult] = None
        best_score = 0.0

        for template in templates:
            scores: dict[str, float] = {}
            total_score = 0.0
            weight_sum = 0.0

            for strategy in self._strategies:
                score = await strategy.match(message, template)
                scores[strategy.name] = score

                if strategy.name.startswith("keyword"):
                    total_score += score * self._keyword_weight
                    weight_sum += self._keyword_weight
                elif strategy.name == "semantic":
                    total_score += score * self._semantic_weight
                    weight_sum += self._semantic_weight

            final_score = total_score / weight_sum if weight_sum > 0 else 0.0

            if final_score >= self._threshold and final_score > best_score:
                best_score = final_score
                best_result = MatchResult(
                    template=template,
                    score=final_score,
                    matched_strategies=[
                        k for k, v in scores.items() if v > 0
                    ]
                )

        return best_result
```

#### 8.1.2、使用示例

```python
# 创建匹配器
matcher = TemplateMatcher(
    keyword_weight=0.6,
    semantic_weight=0.4,
    threshold=0.5,
)

# 添加策略
matcher.add_strategy(KeywordMatcher(mode="string"))
matcher.add_strategy(KeywordMatcher(mode="tokenize"))
matcher.add_strategy(SemanticMatcher(llm_adapter=llm_adapter))

# 执行匹配
result = await matcher.match(
    message="查询北京数据中心的路由器状态",
    templates=[template1, template2, template3]
)

if result:
    print(f"匹配模板: {result.template.name}, 得分: {result.score}")
```

#### 8.1.3、评分机制详解

| 策略 | 得分范围 | 计算方式 |
|------|----------|----------|
| 关键词匹配(string) | 0.0 - 1.0 | 命中关键词数 / 总关键词数 |
| 关键词匹配(regex) | 0.0 - 1.0 | 匹配pattern数 / 总pattern数 |
| 关键词匹配(tokenize) | 0.0 - 1.0 | 命中token数 / 总关键词数 |
| 语义匹配 | 0.0 - 1.0 | LLM返回的相似度分数 |

**综合得分计算**：
```
综合得分 = (关键词得分 × 0.6 + 语义得分 × 0.4) / (0.6 + 0.4)
```

**匹配阈值**：默认0.5，可通过配置调整

### 8.2、压缩策略链执行

#### 8.2.1、压缩策略基类

```python
# compression/base.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CompressionContext:
    """压缩上下文"""
    preserve_key_fields: list[str] = field(default_factory=list)  # 必须保留的字段
    max_length: Optional[int] = None  # 最大长度限制
    extra: dict = field(default_factory=dict)  # 额外参数


class CompressionStrategy(ABC):
    """
    压缩策略基类

    validate方法的三重验证：
    1. 压缩比验证：compressed_length / original_length > min_ratio
    2. 关键字段保留验证：preserve_key_fields中的内容必须出现在压缩结果中
    3. 语法完整性验证：压缩后内容可被正确解析
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """策略名称"""
        pass

    @abstractmethod
    async def compress(
        self,
        content: str,
        context: CompressionContext
    ) -> str:
        """
        压缩内容

        Args:
            content: 待压缩的内容
            context: 压缩上下文

        Returns:
            压缩后的内容
        """
        pass

    @abstractmethod
    async def validate(
        self,
        compressed: str,
        original: str,
        context: CompressionContext
    ) -> bool:
        """
        验证压缩后内容是否满足要求

        Args:
            compressed: 压缩后的内容
            original: 原始内容
            context: 压缩上下文

        Returns:
            是否有效（True表示满足所有验证条件）
        """
        pass

    def _validate_ratio(self, compressed: str, original: str, min_ratio: float = 0.3) -> bool:
        """验证压缩比"""
        if len(original) == 0:
            return True
        ratio = len(compressed) / len(original)
        return ratio >= min_ratio

    def _validate_key_fields(
        self,
        compressed: str,
        original: str,
        key_fields: list[str]
    ) -> bool:
        """验证关键字段是否保留"""
        for field in key_fields:
            # 简单实现：检查关键字段是否在原始内容中
            # 如果在原始内容中，也必须在压缩结果中
            if field.lower() in original.lower():
                if field.lower() not in compressed.lower():
                    return False
        return True
```

#### 8.2.2、关键词抽取策略

```python
# compression/strategies/keyword_extractor.py

import re
from typing import Optional
from compression.base import CompressionStrategy, CompressionContext


class KeywordExtractorStrategy(CompressionStrategy):
    """
    关键词抽取压缩策略

    通过正则和词典抽取关键信息，去除冗余文本
    """

    # 预定义实体模式
    ENTITY_PATTERNS = {
        "device_id": r"设备[ID编号][:：]?\s*([A-Za-z0-9_-]+)",
        "ip_address": r"\b(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b",
        "time_range": r"(\d{4}[-/年]\d{1,2}[-/月]\d{1,2}[\s-至]+\d{4}[-/月]\d{1,2})",
        "location": r"([A-Za-z]+(?:数据中心|机房|区域))[A-Za-z]*",
    }

    # 预定义词典
    ENTITY_DICTS = {
        "device_type": ["路由器", "交换机", "服务器", "防火墙"],
        "operation": ["查询", "配置", "重启", "监控"],
        "status": ["正常", "故障", "告警", "离线"],
    }

    def __init__(
        self,
        patterns: Optional[dict[str, str]] = None,
        dicts: Optional[dict[str, list[str]]] = None,
        remove_patterns: Optional[list[str]] = None,
    ):
        self.patterns = patterns or self.ENTITY_PATTERNS
        self.dicts = dicts or self.ENTITY_DICTS
        self.remove_patterns = remove_patterns or [
            r"请注意[\s\S]*",
            r"麻烦[\s\S]*",
            r"谢谢[\s\S]*",
            r"\s+",  # 多余空白
        ]

    @property
    def name(self) -> str:
        return "keyword_extractor"

    async def compress(
        self,
        content: str,
        context: CompressionContext
    ) -> str:
        result = content

        # 1. 提取实体
        entities = self._extract_entities(result)

        # 2. 移除冗余文本
        for pattern in self.remove_patterns:
            result = re.sub(pattern, "", result)

        # 3. 还原实体占位符（如果使用了占位符）
        # 简化实现：直接返回处理后的文本

        return result.strip()

    async def validate(
        self,
        compressed: str,
        original: str,
        context: CompressionContext
    ) -> bool:
        # 1. 验证压缩比
        if not self._validate_ratio(compressed, original):
            return False

        # 2. 验证关键字段保留
        if not self._validate_key_fields(
            compressed, original, context.preserve_key_fields
        ):
            return False

        return True

    def _extract_entities(self, content: str) -> dict[str, list[str]]:
        """提取实体"""
        entities = {}
        for name, pattern in self.patterns.items():
            matches = re.findall(pattern, content)
            if matches:
                entities[name] = matches
        return entities
```

#### 8.2.3、LLM摘要策略

```python
# compression/strategies/llm_summarizer.py

from compression.base import CompressionStrategy, CompressionContext


class LLMSummarizerStrategy(CompressionStrategy):
    """
    LLM摘要压缩策略

    使用LLM生成内容摘要
    """

    DEFAULT_PROMPT = """请将以下文本压缩为简洁的摘要，保留关键信息。

要求：
1. 保留所有关键实体（设备ID、IP地址、时间等）
2. 保留用户意图
3. 去除冗余的客套话和重复内容
4. 摘要长度控制在原文的30%-70%

原文：
{content}

摘要："""

    def __init__(
        self,
        llm_adapter: "LLMAdapter",
        prompt_template: Optional[str] = None,
        max_retries: int = 2,
    ):
        self._llm_adapter = llm_adapter
        self._prompt_template = prompt_template or self.DEFAULT_PROMPT
        self._max_retries = max_retries

    @property
    def name(self) -> str:
        return "llm_summarizer"

    async def compress(
        self,
        content: str,
        context: CompressionContext
    ) -> str:
        prompt = self._prompt_template.format(content=content)

        response = await self._llm_adapter.invoke(
            LLMRequest(prompt=prompt)
        )

        return response.content.strip()

    async def validate(
        self,
        compressed: str,
        original: str,
        context: CompressionContext
    ) -> bool:
        # 1. 验证压缩比
        if not self._validate_ratio(compressed, original, min_ratio=0.2):
            return False

        # 2. 验证关键字段保留
        if not self._validate_key_fields(
            compressed, original, context.preserve_key_fields
        ):
            return False

        # 3. 语法完整性验证（简单检查）
        if not compressed or len(compressed) < 5:
            return False

        return True
```

#### 8.2.4、压缩策略链

```python
# compression/chain.py

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Optional

from compression.base import CompressionStrategy, CompressionContext


@dataclass
class CompressionConfig:
    """压缩配置"""
    strategies: list[str] = field(default_factory=list)  # 策略名称列表
    max_retries: int = 3                                   # 最大重试次数
    retry_delay: float = 1.0                               # 重试延迟（秒）
    preserve_key_fields: list[str] = field(default_factory=list)  # 必须保留的字段
    min_compression_ratio: float = 0.3                    # 最小压缩比


@dataclass
class CompressionResult:
    """压缩结果"""
    success: bool                           # 是否成功
    original_content: str                   # 原始内容
    compressed_content: Optional[str] = None  # 压缩后内容
    strategies_used: list[str] = field(default_factory=list)  # 使用的策略
    compression_ratio: Optional[float] = None  # 压缩比
    error: Optional[str] = None             # 错误信息


class CompressionChain:
    """
    压缩策略链

    按顺序执行压缩策略，失败时重试或降级
    """

    def __init__(self, config: CompressionConfig):
        self._config = config
        self._strategies: dict[str, CompressionStrategy] = {}
        self._logger = logging.getLogger(__name__)

    def register_strategy(self, name: str, strategy: CompressionStrategy):
        """注册压缩策略"""
        self._strategies[name] = strategy

    async def execute(
        self,
        content: str,
        strategies: Optional[list[str]] = None,
        context: Optional[CompressionContext] = None,
    ) -> CompressionResult:
        """
        执行压缩

        Args:
            content: 待压缩内容
            strategies: 使用的策略列表（None使用配置中的默认策略）
            context: 压缩上下文

        Returns:
            压缩结果
        """
        strategy_names = strategies or self._config.strategies
        ctx = context or CompressionContext(
            preserve_key_fields=self._config.preserve_key_fields
        )

        result = CompressionResult(
            success=False,
            original_content=content,
        )

        for strategy_name in strategy_names:
            strategy = self._strategies.get(strategy_name)
            if not strategy:
                self._logger.warning(f"Strategy {strategy_name} not found")
                continue

            compressed = await self._execute_with_retry(
                strategy, content, ctx
            )

            if compressed:
                result.compressed_content = compressed
                result.strategies_used.append(strategy_name)
                result.compression_ratio = (
                    len(compressed) / len(content)
                    if len(content) > 0 else 1.0
                )
                result.success = True
                return result

        # 所有策略都失败
        result.error = "All strategies failed"
        return result

    async def _execute_with_retry(
        self,
        strategy: CompressionStrategy,
        content: str,
        context: CompressionContext,
    ) -> Optional[str]:
        """执行策略并重试"""
        for attempt in range(self._config.max_retries):
            try:
                compressed = await strategy.compress(content, context)

                # 验证压缩结果
                if await strategy.validate(compressed, content, context):
                    return compressed
                else:
                    self._logger.debug(
                        f"Strategy {strategy.name} validation failed on attempt {attempt + 1}"
                    )

            except Exception as e:
                self._logger.warning(
                    f"Strategy {strategy.name} failed on attempt {attempt + 1}: {e}"
                )

            if attempt < self._config.max_retries - 1:
                await asyncio.sleep(self._config.retry_delay)

        return None
```

#### 8.2.5、使用示例

```python
# 初始化配置和策略链
config = CompressionConfig(
    strategies=["keyword_extractor", "llm_summarizer"],
    max_retries=3,
    retry_delay=1.0,
    preserve_key_fields=["device_id", "ip_address", "intent"],
)

chain = CompressionChain(config)

# 注册策略
chain.register_strategy(
    "keyword_extractor",
    KeywordExtractorStrategy()
)
chain.register_strategy(
    "llm_summarizer",
    LLMSummarizerStrategy(llm_adapter=llm_adapter)
)

# 执行压缩
result = await chain.execute(
    content="用户想要查询北京数据中心的核心路由器BR-001的状态，请帮我查询一下，谢谢。",
    context=CompressionContext(preserve_key_fields=["北京", "BR-001"])
)

if result.success:
    print(f"压缩后: {result.compressed_content}")
    print(f"压缩比: {result.compression_ratio:.2%}")
    print(f"使用策略: {result.strategies_used}")
else:
    print("压缩失败，使用原始内容")
```

### 8.3、限流算法

#### 8.3.1、令牌桶实现

```python
# server/rate_limiter.py

import asyncio
import time
import logging
from dataclasses import dataclass, field
from typing import Optional

import redis.asyncio as redis


@dataclass
class RateLimitConfig:
    """限流配置"""
    enabled: bool = True                   # 是否启用
    requests_per_second: float = 100.0      # 每秒请求数
    max_concurrent: int = 50               # 最大并发数
    burst_size: int = 200                  # 突发容量
    redis:
        enabled: bool = True
        key_prefix: str = "a2a_sdk:ratelimit:"
        # 连接配置
        host: str = "localhost"
        port: int = 6379
        db: int = 0


class TokenBucket:
    """
    令牌桶算法实现

    使用异步锁保证线程安全
    """

    def __init__(
        self,
        rate: float,
        burst: int,
    ):
        """
        Args:
            rate: 每秒添加的令牌数
            burst: 桶容量（最大突发请求数）
        """
        self.rate = rate
        self.burst = burst
        self.tokens = float(burst)
        self.last_update = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self, tokens: int = 1) -> bool:
        """
        尝试获取令牌

        Args:
            tokens: 需要获取的令牌数

        Returns:
            True: 获取成功
            False: 获取失败（被限流）
        """
        async with self._lock:
            now = time.monotonic()
            # 计算应该添加的令牌数
            elapsed = now - self.last_update
            self.tokens = min(self.burst, self.tokens + elapsed * self.rate)
            self.last_update = now

            if self.tokens >= tokens:
                self.tokens -= tokens
                return True
            return False

    async def get_available_tokens(self) -> float:
        """获取当前可用令牌数"""
        async with self._lock:
            now = time.monotonic()
            elapsed = now - self.last_update
            return min(self.burst, self.tokens + elapsed * self.rate)


class SlidingWindowCounter:
    """
    滑动窗口计数器实现

    用于更精确的QPS限流
    """

    def __init__(
        self,
        window_size: float = 1.0,
        max_requests: int = 100,
    ):
        self.window_size = window_size
        self.max_requests = max_requests
        self.requests: list[float] = []
        self._lock = asyncio.Lock()

    async def is_allowed(self) -> bool:
        """检查请求是否允许"""
        async with self._lock:
            now = time.monotonic()
            # 移除窗口外的请求
            self.requests = [
                t for t in self.requests
                if now - t < self.window_size
            ]

            if len(self.requests) < self.max_requests:
                self.requests.append(now)
                return True
            return False

    async def get_current_count(self) -> int:
        """获取当前窗口内的请求数"""
        async with self._lock:
            now = time.monotonic()
            self.requests = [
                t for t in self.requests
                if now - t < self.window_size
            ]
            return len(self.requests)


@dataclass
class RateLimitResult:
    """限流检查结果"""
    allowed: bool                          # 是否允许
    remaining_tokens: Optional[float] = None  # 剩余令牌数
    retry_after: Optional[float] = None   # 重试等待时间（秒）
    current_rps: Optional[float] = None   # 当前RPS


class RateLimiter:
    """
    限流器

    支持QPS限流和并发限流
    """

    def __init__(self, config: RateLimitConfig):
        self._config = config
        self._logger = logging.getLogger(__name__)
        self._redis: Optional[redis.Redis] = None

        # QPS限流（令牌桶）
        self._token_bucket = TokenBucket(
            rate=config.requests_per_second,
            burst=config.burst_size,
        )

        # 并发限流（信号量）
        self._semaphore = asyncio.Semaphore(config.max_concurrent)

        # 滑动窗口（用于统计当前RPS）
        self._window = SlidingWindowCounter(
            window_size=1.0,
            max_requests=config.requests_per_second,
        )

    async def initialize(self):
        """初始化（连接Redis等）"""
        if self._config.redis.enabled:
            try:
                self._redis = redis.Redis(
                    host=self._config.redis.host,
                    port=self._config.redis.port,
                    db=self._config.redis.db,
                    decode_responses=True,
                )
                # 测试连接
                await self._redis.ping()
                self._logger.info("Redis connection established for rate limit config")
            except Exception as e:
                self._logger.warning(f"Failed to connect to Redis: {e}")
                self._redis = None

    async def check(self) -> RateLimitResult:
        """
        检查请求是否允许通过限流

        Returns:
            RateLimitResult: 检查结果
        """
        if not self._config.enabled:
            return RateLimitResult(allowed=True)

        # 1. QPS限流检查
        if not await self._token_bucket.acquire():
            retry_after = 1.0 / self._config.requests_per_second
            return RateLimitResult(
                allowed=False,
                retry_after=retry_after,
            )

        # 2. 滑动窗口检查
        if not await self._window.is_allowed():
            return RateLimitResult(
                allowed=False,
                retry_after=1.0,
            )

        # 3. 返回成功结果
        remaining = await self._token_bucket.get_available_tokens()
        current_rps = await self._window.get_current_count()

        return RateLimitResult(
            allowed=True,
            remaining_tokens=remaining,
            current_rps=float(current_rps),
        )

    async def acquire(self) -> bool:
        """
        获取并发限流的信号量

        Returns:
            True: 获取成功
            False: 被限流
        """
        if not self._config.enabled:
            return True

        # 先检查QPS
        result = await self.check()
        if not result.allowed:
            return False

        # 获取信号量（带超时）
        try:
            await asyncio.wait_for(
                self._semaphore.acquire(),
                timeout=1.0,
            )
            return True
        except asyncio.TimeoutError:
            return False

    def release(self):
        """释放信号量"""
        if self._config.enabled:
            self._semaphore.release()

    async def get_config_from_redis(self) -> Optional[dict]:
        """从Redis获取限流配置"""
        if not self._redis:
            return None

        try:
            key = f"{self._config.redis.key_prefix}config"
            config_str = await self._redis.get(key)
            if config_str:
                import json
                return json.loads(config_str)
        except Exception as e:
            self._logger.warning(f"Failed to get config from Redis: {e}")

        return None

    async def close(self):
        """关闭资源"""
        if self._redis:
            await self._redis.close()
```

#### 8.3.2、使用示例

```python
# 初始化限流器
config = RateLimitConfig(
    enabled=True,
    requests_per_second=100,
    max_concurrent=50,
    burst_size=200,
    redis=RateLimitConfig.RedisConfig(
        enabled=True,
        host="redis.example.com",
        port=6379,
    ),
)

limiter = RateLimiter(config)
await limiter.initialize()

# 在请求处理中使用
async def handle_request(request):
    result = await limiter.check()

    if not result.allowed:
        return ErrorResponse(
            error_code=429,
            error_info="Rate limit exceeded",
            extra={"retry_after": result.retry_after},
        )

    # 处理请求...
    return SuccessResponse()

# 清理
await limiter.close()
```

#### 8.3.3、限流维度说明

| 维度 | 算法 | 说明 |
|------|------|------|
| QPS限流 | 令牌桶 | 限制每秒请求数，支持突发 |
| 并发限流 | 信号量 | 限制同时处理的请求数 |
| 滑动窗口 | 滑动窗口计数器 | 用于统计和监控当前RPS |

---

## 九、数据模型设计

### 9.1、核心数据模型

参见第七节接口设计中的 `prompt/models.py`、`compression/chain.py`、`llm/base.py`、`config/models.py`。

### 9.2、错误数据模型

```python
# common/errors.py

from dataclasses import dataclass, field
from typing import Optional, Any
from enum import Enum


class ErrorFormatType(Enum):
    """错误格式类型"""
    STANDARD = "standard"          # 默认标准格式
    A2A_COMPATIBLE = "a2a"         # A2A兼容格式
    CUSTOM = "custom"              # 自定义格式


@dataclass
class ErrorDetail:
    """错误详情"""
    field: str = ""                       # 出错字段
    code: str = ""                        # 错误码
    message: str = ""                     # 错误描述
    details: dict = field(default_factory=dict)  # 额外详情


@dataclass
class ErrorContext:
    """错误上下文"""
    error_code: int                        # HTTP风格错误码
    error_type: str                        # 错误类型
    error_info: str                        # 错误信息
    details: list[ErrorDetail] = field(default_factory=list)  # 详细错误列表
    request_id: str = ""                   # 请求ID（可选）
    timestamp: str = ""                    # 时间戳（可选）
    extra: dict = field(default_factory=dict)  # 额外字段
```

### 9.3、错误格式模板

```python
# 标准格式
{
  "errorCode": 400,
  "errorInfo": "错误描述",
  "details": [...]
}

# A2A兼容格式
{
  "error": {
    "code": 400,
    "status": "BAD_REQUEST",
    "message": "错误描述",
    "details": [...]
  }
}

# 自定义格式（通过Jinja2模板配置）
```

---

## 十、安全设计

### 10.1、认证支持

#### 10.1.1、认证接口设计

```python
# common/auth.py

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional
import base64


class AuthScheme(ABC):
    """认证方案基类"""

    @property
    @abstractmethod
    def scheme_name(self) -> str:
        """认证方案名称"""
        pass

    @abstractmethod
    def apply(self, request: "Request") -> None:
        """应用认证到请求"""
        pass


class APIKeyAuth(AuthScheme):
    """API Key认证"""

    def __init__(self, api_key: str, header_name: str = "X-API-Key"):
        self._api_key = api_key
        self._header_name = header_name

    @property
    def scheme_name(self) -> str:
        return "api_key"

    def apply(self, request: "Request") -> None:
        request.headers[self._header_name] = self._api_key


class BearerTokenAuth(AuthScheme):
    """Bearer Token认证 (OAuth 2.0)"""

    def __init__(self, token: str):
        self._token = token

    @property
    def scheme_name(self) -> str:
        return "bearer"

    def apply(self, request: "Request") -> None:
        request.headers["Authorization"] = f"Bearer {self._token}"


class BasicAuth(AuthScheme):
    """Basic认证"""

    def __init__(self, username: str, password: str):
        self._credentials = base64.b64encode(
            f"{username}:{password}".encode()
        ).decode()

    @property
    def scheme_name(self) -> str:
        return "basic"

    def apply(self, request: "Request") -> None:
        request.headers["Authorization"] = f"Basic {self._credentials}"


@dataclass
class AuthConfig:
    """认证配置"""
    type: str = "none"  # "none" | "api_key" | "bearer" | "basic"
    api_key: Optional[str] = None
    header_name: str = "X-API-Key"
    token: Optional[str] = None
    username: Optional[str] = None
    password: Optional[str] = None

    def create_auth_scheme(self) -> Optional[AuthScheme]:
        """创建认证方案实例"""
        if self.type == "api_key" and self.api_key:
            return APIKeyAuth(self.api_key, self.header_name)
        elif self.type == "bearer" and self.token:
            return BearerTokenAuth(self.token)
        elif self.type == "basic" and self.username and self.password:
            return BasicAuth(self.username, self.password)
        return None


class AuthManager:
    """认证管理器 - 客户端侧"""
    def __init__(self, config: AuthConfig):
        self._config = config
        self._auth_scheme = config.create_auth_scheme()

    def apply_auth(self, request: "Request") -> None:
        if self._auth_scheme:
            self._auth_scheme.apply(request)


class AuthMiddleware:
    """认证中间件 - 服务端侧"""
    def __init__(self, config: AuthConfig):
        self._config = config

    async def verify(self, request: "Request") -> bool:
        """验证请求认证"""
        if self._config.type == "none":
            return True
        # 验证逻辑实现
        ...
        return False
```

#### 10.1.2、配置示例

```yaml
# 客户端认证配置
security:
  auth:
    type: "api_key"
    api_key: "${A2A_CLIENT_API_KEY}"
    header_name: "X-API-Key"

# 服务端认证配置
security:
  server_auth:
    enabled: true
    schemes:
      - type: "api_key"
        name: "X-API-Key"
        in: "header"
        required: true
```

### 10.2、传输安全

#### 10.2.1、TLS配置

```python
# common/tls.py

import ssl
from dataclasses import dataclass
from typing import Optional


@dataclass
class TLSConfig:
    """TLS配置"""
    enabled: bool = True
    verify_server: bool = True
    ca_cert: Optional[str] = None
    client_cert: Optional[str] = None
    client_key: Optional[str] = None
    sni: Optional[str] = None

    def create_ssl_context(self) -> ssl.SSLContext:
        """创建SSL上下文"""
        if not self.enabled:
            return None

        context = ssl.create_default_context()

        if not self.verify_server:
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        elif self.ca_cert:
            context.load_verify_locations(self.ca_cert)

        if self.client_cert and self.client_key:
            context.load_cert_chain(
                certfile=self.client_cert,
                keyfile=self.client_key,
            )

        return context


class HTTPSClient:
    """HTTPS客户端"""
    def __init__(self, tls_config: Optional[TLSConfig] = None):
        self._tls_config = tls_config or TLSConfig()

    def create_session(self) -> "httpx.AsyncClient":
        import httpx
        ssl_context = self._tls_config.create_ssl_context()
        return httpx.AsyncClient(
            verify=ssl_context if ssl_context else True,
            timeout=httpx.Timeout(30.0),
        )
```

#### 10.2.2、配置示例

```yaml
security:
  tls:
    enabled: true
    verify_server: true
    ca_cert: "/etc/ssl/certs/ca.pem"
    client_cert: "/etc/ssl/private/client.pem"
    client_key: "/etc/ssl/private/client.key"
    sni: "agent.example.com"
```

### 10.3、敏感信息处理

#### 10.3.1、环境变量替换

```python
# common/env.py

import os
import re
from typing import Any


class EnvVarResolver:
    """环境变量解析器 - 支持 ${ENV_VAR} 和 ${ENV_VAR:default} 语法"""
    PATTERN = re.compile(r'\$\{([^}:]+)(?::([^}]*))?\}')

    def resolve(self, value: str) -> str:
        def replacer(match):
            var_name = match.group(1)
            default_value = match.group(2)
            env_value = os.getenv(var_name)
            if env_value is not None:
                return env_value
            return default_value if default_value is not None else match.group(0)
        return self.PATTERN.sub(replacer, value)

    def resolve_dict(self, data: dict) -> dict:
        result = {}
        for key, value in data.items():
            if isinstance(value, str):
                result[key] = self.resolve(value)
            elif isinstance(value, dict):
                result[key] = self.resolve_dict(value)
            elif isinstance(value, list):
                result[key] = [
                    self.resolve(v) if isinstance(v, str) else v for v in value
                ]
            else:
                result[key] = value
        return result
```

#### 10.3.2、日志脱敏

```python
# common/logging.py

import json
import logging
from dataclasses import dataclass, field


@dataclass
class SensitiveFieldsConfig:
    """敏感字段配置"""
    fields: list[str] = field(default_factory=lambda: [
        "api_key", "token", "password", "secret", "credential"
    ])
    mask_request_body: bool = True
    mask_response_body: bool = True
    mask_char: str = "*"


class SensitiveDataMasker:
    """敏感数据脱敏器"""
    def __init__(self, config: SensitiveFieldsConfig):
        self._config = config

    def mask_dict(self, data: dict, depth: int = 0) -> dict:
        if depth > 10:
            return {"_max_depth_exceeded": True}
        result = {}
        for key, value in data.items():
            if self._is_sensitive_key(key):
                result[key] = self._config.mask_char * 8
            elif isinstance(value, dict):
                result[key] = self.mask_dict(value, depth + 1)
            elif isinstance(value, list):
                result[key] = [
                    self.mask_dict(v, depth + 1) if isinstance(v, dict) else v
                    for v in value
                ]
            else:
                result[key] = value
        return result

    def _is_sensitive_key(self, key: str) -> bool:
        key_lower = key.lower()
        return any(field in key_lower for field in self._config.fields)


class JSONFormatter(logging.Formatter):
    """JSON格式日志格式化器"""
    def __init__(self, masker: SensitiveDataMasker):
        super().__init__()
        self._masker = masker

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if hasattr(record, "extra") and isinstance(record.extra, dict):
            extra = record.extra.copy()
            if self._masker._config.mask_request_body and "request_body" in extra:
                extra["request_body"] = self._masker.mask_dict(extra["request_body"])
            log_data["context"] = extra
        return json.dumps(log_data)
```

### 10.4、安全中间件

```python
# common/security.py

from dataclasses import dataclass


@dataclass
class SecurityConfig:
    """安全配置"""
    require_auth: bool = True
    require_tls: bool = True
    input_validation: bool = True
    max_request_size: int = 10 * 1024 * 1024  # 10MB


class SecurityMiddleware:
    """安全中间件 - 统一安全检查"""
    def __init__(self, auth_middleware: AuthMiddleware, config: SecurityConfig):
        self._auth = auth_middleware
        self._config = config

    async def process(self, request: "Request", handler: callable) -> "Response":
        # 1. 请求大小检查
        content_length = request.headers.get("content-length")
        if content_length and int(content_length) > self._config.max_request_size:
            return ErrorResponse(error_code=413, error_info="Request too large")

        # 2. TLS检查
        if self._config.require_tls:
            if not request.headers.get("X-Forwarded-Proto") == "https":
                if not request.client.host.startswith(("127.", "localhost")):
                    return ErrorResponse(error_code=403, error_info="TLS required")

        # 3. 认证检查
        if self._config.require_auth:
            if not await self._auth.verify(request):
                return ErrorResponse(error_code=401, error_info="Unauthorized")

        # 4. 输入校验（防注入）
        if self._config.input_validation:
            if not self._validate_input(request):
                return ErrorResponse(error_code=400, error_info="Invalid input")

        return await handler(request)

    def _validate_input(self, request: "Request") -> bool:
        """简单的注入检测"""
        import re
        injection_patterns = [r"<\s*script", r"javascript:", r"on\w+\s*="]
        body = getattr(request, "body", "")
        if body:
            for pattern in injection_patterns:
                if re.search(pattern, body, re.IGNORECASE):
                    return False
        return True
```

### 10.5、安全检查清单

| 检查项 | 实现位置 | 说明 |
|--------|----------|------|
| 认证检查 | AuthMiddleware | 所有请求必须通过认证 |
| 传输加密 | TLSConfig | 所有传输必须使用HTTPS/TLS |
| 输入校验 | SecurityMiddleware | 防注入攻击 |
| 错误信息 | ErrorFormatter | 不泄露内部实现细节 |
| 日志脱敏 | SensitiveDataMasker | 不记录敏感信息 |
| 超时控制 | httpx.Timeout | 防止资源耗尽 |
| 请求大小限制 | SecurityMiddleware | 防止大文件攻击 |

---

## 十一、DFX设计

### 11.1、可观测性

#### 11.1.1、日志实现

```python
# common/logging.py

import logging
import sys
import json
from logging.handlers import RotatingFileHandler
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime


@dataclass
class LoggingConfig:
    """日志配置"""
    level: str = "INFO"           # DEBUG | INFO | WARNING | ERROR | CRITICAL
    format: str = "json"          # json | text
    output: str = "stdout"        # stdout | file
    file_path: Optional[str] = None
    rotation_max_bytes: int = 10 * 1024 * 1024  # 10MB
    rotation_backup_count: int = 5


class JSONFormatter(logging.Formatter):
    """JSON格式日志格式化器"""

    def __init__(self, masker: Optional["SensitiveDataMasker"] = None):
        super().__init__()
        self._masker = masker

    def format(self, record: logging.LogRecord) -> str:
        log_data = {
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # 添加上下文
        if hasattr(record, "context") and record.context:
            context = record.context.copy()
            # 可选：脱敏处理
            if self._masker:
                context = self._masker.mask_dict(context)
            log_data["context"] = context

        # 添加异常信息
        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data)


class TextFormatter(logging.Formatter):
    """文本格式日志格式化器"""

    FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"

    def __init__(self):
        super().__init__(self.FORMAT)


class LoggerFactory:
    """日志工厂"""

    _loggers: dict[str, logging.Logger] = {}
    _default_config: Optional[LoggingConfig] = None

    @classmethod
    def configure(cls, config: LoggingConfig):
        """配置全局日志"""
        cls._default_config = config

        # 配置根日志器
        root_logger = logging.getLogger()
        root_logger.setLevel(getattr(logging, config.level.upper()))

        # 清除现有处理器
        root_logger.handlers.clear()

        # 添加处理器
        if config.output == "file" and config.file_path:
            handler: logging.Handler = RotatingFileHandler(
                filename=config.file_path,
                maxBytes=config.rotation_max_bytes,
                backupCount=config.rotation_backup_count,
            )
        else:
            handler = logging.StreamHandler(sys.stdout)

        # 设置格式化器
        if config.format == "json":
            handler.setFormatter(JSONFormatter())
        else:
            handler.setFormatter(TextFormatter())

        root_logger.addHandler(handler)

    @classmethod
    def get_logger(cls, name: str) -> logging.Logger:
        """获取日志器"""
        if name not in cls._loggers:
            logger = logging.getLogger(name)
            cls._loggers[name] = logger
        return cls._loggers[name]

    @classmethod
    def with_context(cls, logger: logging.Logger, **context) -> logging.LoggerAdapter:
        """创建带上下文的日志器"""
        return logging.LoggerAdapter(logger, {"context": context})
```

#### 11.1.2、指标实现

```python
# common/metrics.py

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional
from datetime import datetime
from enum import Enum
import asyncio


class MetricType(Enum):
    """指标类型"""
    COUNTER = "counter"      # 累计值
    GAUGE = "gauge"         # 当前值
    HISTOGRAM = "histogram" # 直方图


@dataclass
class Metric:
    """指标"""
    name: str
    type: MetricType
    value: float
    tags: dict = field(default_factory=dict)
    timestamp: Optional[str] = None

    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = datetime.utcnow().isoformat() + "Z"


class MetricsCollector(ABC):
    """指标收集器基类"""

    @abstractmethod
    async def record(self, metric: Metric) -> None:
        """记录指标"""
        pass

    @abstractmethod
    async def increment(self, name: str, value: float = 1, tags: dict = None) -> None:
        """增加计数器"""
        pass

    @abstractmethod
    async def gauge(self, name: str, value: float, tags: dict = None) -> None:
        """设置仪表值"""
        pass

    @abstractmethod
    async def histogram(self, name: str, value: float, tags: dict = None) -> None:
        """记录直方图值"""
        pass


class CallbackMetricsCollector(MetricsCollector):
    """回调式指标收集器"""

    def __init__(self, callback: callable):
        self._callback = callback
        self._values: dict[str, float] = {}
        self._lock = asyncio.Lock()

    async def record(self, metric: Metric) -> None:
        await self._callback(metric)

    async def increment(self, name: str, value: float = 1, tags: dict = None) -> None:
        async with self._lock:
            key = f"{name}:{tags or {}}"
            self._values[key] = self._values.get(key, 0) + value

        await self.record(Metric(
            name=name,
            type=MetricType.COUNTER,
            value=value,
            tags=tags or {},
        ))

    async def gauge(self, name: str, value: float, tags: dict = None) -> None:
        async with self._lock:
            self._values[f"{name}:{tags or {}}"] = value

        await self.record(Metric(
            name=name,
            type=MetricType.GAUGE,
            value=value,
            tags=tags or {},
        ))

    async def histogram(self, name: str, value: float, tags: dict = None) -> None:
        await self.record(Metric(
            name=name,
            type=MetricType.HISTOGRAM,
            value=value,
            tags=tags or {},
        ))


class MetricsContext:
    """
    指标上下文管理器

    用于自动记录请求时长等指标
    """

    def __init__(self, collector: MetricsCollector, metric_name: str, tags: dict = None):
        self._collector = collector
        self._metric_name = metric_name
        self._tags = tags or {}
        self._start_time: Optional[float] = None

    async def __aenter__(self):
        self._start_time = asyncio.get_event_loop().time()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._start_time is not None:
            duration = asyncio.get_event_loop().time() - self._start_time
            await self._collector.histogram(
                self._metric_name,
                duration * 1000,  # 转换为毫秒
                self._tags,
            )


class MetricsManager:
    """指标管理器"""

    _instance: Optional["MetricsManager"] = None

    def __init__(self, collector: MetricsCollector):
        self._collector = collector

    @classmethod
    def initialize(cls, collector: MetricsCollector):
        """初始化指标管理器"""
        cls._instance = cls(collector)

    @classmethod
    def get_instance(cls) -> "MetricsManager":
        if cls._instance is None:
            raise RuntimeError("MetricsManager not initialized")
        return cls._instance

    @property
    def collector(self) -> MetricsCollector:
        return self._collector

    def context(self, metric_name: str, tags: dict = None) -> MetricsContext:
        """创建指标上下文"""
        return MetricsContext(self._collector, metric_name, tags)
```

#### 11.1.3、追踪实现

```python
# common/tracing.py

from dataclasses import dataclass
from typing import Optional, Callable
import contextvars
from contextlib import asynccontextmanager


# Trace context传播
TRACE_CONTEXT: contextvars.ContextVar[Optional[dict]] = contextvars.ContextVar(
    "trace_context", default=None
)


@dataclass
class Span:
    """追踪跨度"""
    name: str
    trace_id: str
    span_id: str
    parent_span_id: Optional[str] = None
    tags: dict = None
    start_time: Optional[float] = None
    end_time: Optional[float] = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = {}


class TraceContextManager:
    """
    追踪上下文管理器

    支持W3C Trace Context传播
    """

    @staticmethod
    def get_trace_context() -> Optional[dict]:
        """获取当前追踪上下文"""
        return TRACE_CONTEXT.get()

    @staticmethod
    def set_trace_context(context: dict):
        """设置追踪上下文"""
        TRACE_CONTEXT.set(context)

    @staticmethod
    def extract_from_headers(headers: dict) -> dict:
        """
        从HTTP headers提取追踪上下文

        W3C Trace Context格式：
        - traceparent: 00-{trace-id}-{span-id}-{flags}
        - tracestate: 用于额外属性
        """
        traceparent = headers.get("traceparent", "")

        if not traceparent:
            return {}

        parts = traceparent.split("-")
        if len(parts) < 4:
            return {}

        return {
            "trace_id": parts[1],
            "span_id": parts[2],
            "version": parts[0],
        }

    @staticmethod
    def inject_to_headers(headers: dict, context: dict) -> dict:
        """将追踪上下文注入到HTTP headers"""
        trace_id = context.get("trace_id", "00000000000000000000000000000000")
        span_id = context.get("span_id", "0000000000000000")

        headers["traceparent"] = f"00-{trace_id}-{span_id}-01"

        if "tracestate" in context:
            headers["tracestate"] = context["tracestate"]

        return headers

    @asynccontextmanager
    async def span(
        self,
        name: str,
        tags: dict = None,
    ):
        """创建追踪跨度"""
        import uuid

        parent_context = self.get_trace_context()

        span = Span(
            name=name,
            trace_id=parent_context.get("trace_id", uuid.uuid4().hex[:32]) if parent_context else uuid.uuid4().hex[:32],
            span_id=uuid.uuid4().hex[:16],
            parent_span_id=parent_context.get("span_id") if parent_context else None,
            tags=tags or {},
        )

        # 更新上下文
        new_context = {
            "trace_id": span.trace_id,
            "span_id": span.span_id,
        }
        token = TRACE_CONTEXT.set(new_context)

        try:
            yield span
        finally:
            TRACE_CONTEXT.reset(token)


class TracingMiddleware:
    """追踪中间件"""

    def __init__(self, trace_manager: TraceContextManager):
        self._trace_manager = trace_manager

    async def process(self, request: "Request", handler: Callable) -> "Response":
        """处理请求的追踪"""
        # 提取追踪上下文
        headers = dict(request.headers)
        trace_context = self._trace_manager.extract_from_headers(headers)
        self._trace_manager.set_trace_context(trace_context)

        # 执行处理
        async with self._trace_manager.span(
            name=f"{request.method} {request.url.path}",
            tags={
                "http.method": request.method,
                "http.url": str(request.url),
            }
        ):
            response = await handler(request)

        # 将追踪上下文注入响应头
        current_context = self._trace_manager.get_trace_context()
        if current_context:
            self._trace_manager.inject_to_headers(
                response.headers,
                current_context
            )

        return response
```

### 11.2、可配置性

#### 配置项完整列表

| 模块 | 配置项 | 类型 | 默认值 | 说明 |
|------|--------|------|--------|------|
| 连接池 | `connection_pool.max_size` | int | 10 | 最大连接数 |
| 连接池 | `connection_pool.min_size` | int | 2 | 最小连接数 |
| 连接池 | `connection_pool.timeout` | float | 30.0 | 连接超时(秒) |
| 连接池 | `connection_pool.keep_alive` | bool | True | 保活开关 |
| 限流 | `rate_limit.enabled` | bool | True | 是否启用 |
| 限流 | `rate_limit.requests_per_second` | float | 100.0 | 每秒请求数 |
| 限流 | `rate_limit.max_concurrent` | int | 50 | 最大并发数 |
| 限流 | `rate_limit.burst_size` | int | 200 | 突发容量 |
| 限流 | `rate_limit.redis.enabled` | bool | True | 从Redis读取配置 |
| 限流 | `rate_limit.redis.key_prefix` | str | "a2a_sdk:ratelimit:" | Redis键前缀 |
| Prompt | `prompt.template_sources` | list[str] | [] | 模板源列表 |
| Prompt | `prompt.cache_ttl` | int | 3600 | 缓存TTL(秒) |
| Prompt | `prompt.refresh_interval` | int | 300 | 刷新间隔(秒) |
| Prompt | `prompt.semantic_matching.enabled` | bool | False | 语义匹配开关 |
| 压缩 | `compression.enabled` | bool | True | 是否启用 |
| 压缩 | `compression.default_strategies` | list[str] | [] | 默认策略 |
| 压缩 | `compression.max_retries` | int | 3 | 最大重试次数 |
| 压缩 | `compression.retry_delay` | float | 1.0 | 重试延迟(秒) |
| 压缩 | `compression.preserve_key_fields` | list[str] | [] | 必须保留字段 |
| LLM | `llm.adapter_type` | str | "http" | 适配器类型 |
| LLM | `llm.adapter_config` | dict | {} | 适配器配置 |
| 错误 | `error.format_type` | str | "standard" | 错误格式类型 |
| 日志 | `logging.level` | str | "INFO" | 日志级别 |
| 日志 | `logging.format` | str | "json" | 日志格式 |
| 日志 | `logging.output` | str | "stdout" | 输出位置 |
| 指标 | `metrics.enabled` | bool | True | 是否启用 |
| 指标 | `metrics.collector_type` | str | "callback" | 收集器类型 |
| 追踪 | `tracing.enabled` | bool | False | 是否启用 |
| 追踪 | `tracing.propagate` | bool | True | 是否传播上下文 |
| 安全 | `security.tls.enabled` | bool | True | TLS开关 |
| 安全 | `security.auth.type` | str | "none" | 认证类型 |

### 11.2、可配置性

**配置项完整列表**：

| 模块 | 配置项 | 说明 |
|------|--------|------|
| 连接池 | `connection_pool.max_size` | 最大连接数 |
| 连接池 | `connection_pool.min_size` | 最小连接数 |
| 连接池 | `connection_pool.timeout` | 连接超时(秒) |
| 连接池 | `connection_pool.keep_alive` | 保活开关 |
| 限流 | `rate_limit.enabled` | 是否启用 |
| 限流 | `rate_limit.requests_per_second` | 每秒请求数 |
| 限流 | `rate_limit.max_concurrent` | 最大并发数 |
| 限流 | `rate_limit.burst_size` | 突发容量 |
| 限流 | `rate_limit.redis.enabled` | 是否从Redis读取配置 |
| Prompt | `prompt.template_sources` | 模板源列表 |
| Prompt | `prompt.cache_ttl` | 缓存TTL(秒) |
| Prompt | `prompt.refresh_interval` | 刷新间隔(秒) |
| Prompt | `prompt.semantic_matching.enabled` | 语义匹配开关 |
| 压缩 | `compression.enabled` | 是否启用 |
| 压缩 | `compression.default_strategies` | 默认策略列表 |
| 压缩 | `compression.max_retries` | 最大重试次数 |
| 压缩 | `compression.retry_delay` | 重试延迟(秒) |
| 压缩 | `compression.preserve_key_fields` | 必须保留的字段 |
| LLM | `llm.adapter_type` | 适配器类型 |
| LLM | `llm.adapter_config` | 适配器配置 |
| 错误 | `error.format_type` | 错误格式类型 |
| 日志 | `logging.level` | 日志级别 |
| 日志 | `logging.format` | 日志格式 |
| 安全 | `security.*` | 安全相关配置 |

### 11.3、可扩展性

#### 扩展点定义

| 扩展点 | 基类/接口 | 必须实现方法 | 说明 |
|--------|-----------|--------------|------|
| LLM适配器 | `LLMAdapter` | `invoke()`, `health_check()` | 接入新的LLM集成方式 |
| 压缩策略 | `CompressionStrategy` | `compress()`, `validate()` | 实现新的压缩算法 |
| 错误格式 | `ErrorFormatter` | `format()` | 支持新的错误格式 |
| 指标收集 | `MetricsCollector` | `record()`, `increment()`, `gauge()`, `histogram()` | 接入监控系统 |
| 模板匹配 | `MatcherStrategy` | `match()` | 实现新的匹配算法 |

#### 扩展注册机制

```python
# common/registry.py

from typing import TypeVar, Type, Dict, Callable
import logging


T = TypeVar('T')


class Registry(Generic[T]):
    """通用注册表"""

    def __init__(self, name: str):
        self._name = name
        self._items: Dict[str, Type[T] | Callable] = {}
        self._logger = logging.getLogger(__name__)

    def register(self, name: str, item: Type[T] | Callable) -> None:
        """注册项"""
        if name in self._items:
            self._logger.warning(f"{self._name}: {name} already registered, overwriting")
        self._items[name] = item
        self._logger.info(f"{self._name}: Registered {name}")

    def get(self, name: str) -> Optional[Type[T] | Callable]:
        """获取注册的项"""
        return self._items.get(name)

    def list_registered(self) -> list[str]:
        """列出所有注册的项"""
        return list(self._items.keys())


# 全局注册表实例
LLM_ADAPTER_REGISTRY = Registry["LLMAdapter"]("llm_adapter")
COMPRESSION_STRATEGY_REGISTRY = Registry["CompressionStrategy"]("compression_strategy")
ERROR_FORMATTER_REGISTRY = Registry["ErrorFormatter"]("error_formatter")
MATCHER_STRATEGY_REGISTRY = Registry["MatcherStrategy"]("matcher_strategy")
```

#### 扩展使用示例

```python
# 1. 注册自定义LLM适配器
LLM_ADAPTER_REGISTRY.register("my_adapter", MyLLMAdapter)

# 2. 注册自定义压缩策略
COMPRESSION_STRATEGY_REGISTRY.register("my_strategy", MyCompressionStrategy)

# 3. 在配置中使用
config = SDKConfig(
    llm=LLMConfig(
        adapter_type="my_adapter",
        adapter_config={...}
    ),
    compression=CompressionConfig(
        strategies=["keyword_extractor", "my_strategy"]
    )
)

# 4. 工厂创建时自动解析
adapter = LLM_ADAPTER_REGISTRY.get(config.llm.adapter_type)(config.llm.adapter_config)
```

#### entry_points插件机制

```python
# pyproject.toml
[project.entry-points."a2a_t_sdk.llm_adapters"]
my_adapter = "my_package.adapter:MyLLMAdapter"

[project.entry-points."a2a_t_sdk.compression_strategies"]
my_strategy = "my_package.strategy:MyStrategy"


# 插件自动加载
import importlib.metadata

def load_plugins():
    """从entry_points加载插件"""
    # 加载LLM适配器
    for name, ep in importlib.metadata.entry_points(group="a2a_t_sdk.llm_adapters"):
        cls = ep.load()
        LLM_ADAPTER_REGISTRY.register(name, cls)

    # 加载压缩策略
    for name, ep in importlib.metadata.entry_points(group="a2a_t_sdk.compression_strategies"):
        cls = ep.load()
        COMPRESSION_STRATEGY_REGISTRY.register(name, cls)
```

### 11.4、可测试性

#### Mock组件定义

```python
# tests/mocks.py

import asyncio
from typing import Optional, Any
from dataclasses import dataclass, field


@dataclass
class MockLLMResponse:
    """Mock LLM响应"""
    content: str = "Mock response"
    model: str = "mock"
    usage: dict = field(default_factory=lambda: {"prompt_tokens": 10, "completion_tokens": 5})


class MockLLMAdapter(LLMAdapter):
    """Mock LLM适配器"""

    def __init__(
        self,
        config: dict = None,
        response: Optional[MockLLMResponse] = None,
        error: Optional[Exception] = None,
    ):
        self._config = config or {}
        self._response = response or MockLLMResponse()
        self._error = error
        self._call_count = 0

    @property
    def adapter_type(self) -> str:
        return "mock"

    async def invoke(self, request: LLMRequest) -> LLMResponse:
        self._call_count += 1
        if self._error:
            raise self._error
        return LLMResponse(
            content=self._response.content,
            model=self._response.model,
            usage=self._response.usage,
        )

    async def health_check(self) -> bool:
        return True

    @property
    def call_count(self) -> int:
        return self._call_count


class MockTemplateLoader(TemplateLoader):
    """Mock模板加载器"""

    def __init__(self, templates: list[PromptTemplate] = None):
        self._templates = templates or []
        self._load_count = 0

    async def load(self, source: str) -> list[PromptTemplate]:
        self._load_count += 1
        return self._templates

    @property
    def load_count(self) -> int:
        return self._load_count


class MockRateLimiter:
    """Mock限流器"""

    def __init__(self, allowed: bool = True):
        self._allowed = allowed
        self._check_count = 0

    async def check(self) -> RateLimitResult:
        self._check_count += 1
        return RateLimitResult(allowed=self._allowed)

    @property
    def check_count(self) -> int:
        return self._check_count
```

#### 测试Fixture

```python
# tests/fixtures.py

import pytest
import asyncio
from typing import Generator


@pytest.fixture
def event_loop():
    """创建事件循环"""
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def mock_llm_adapter():
    """Mock LLM适配器"""
    return MockLLMAdapter(
        response=MockLLMResponse(content="Test response")
    )


@pytest.fixture
def sample_template():
    """示例模板"""
    return PromptTemplate(
        id="test_template",
        name="Test Template",
        version="1.0",
        description="A test template",
        content="# Test\n\nPlease process: {{input}}",
        fields=[
            PromptField(
                name="input",
                description="Input text",
                field_type="string",
                required=True,
            )
        ],
    )


@pytest.fixture
def sample_config():
    """示例配置"""
    return SDKConfig(
        connection_pool=ConnectionPoolConfig(max_size=5),
        rate_limit=RateLimitConfig(enabled=False),
        prompt=PromptConfig(
            template_sources=["file://./templates/"],
            cache_ttl=3600,
        ),
        compression=CompressionConfig(
            enabled=True,
            strategies=["keyword_extractor"],
        ),
        llm=LLMConfig(adapter_type="mock"),
        log_level="DEBUG",
    )


@pytest.fixture
async def configured_chain(sample_config, mock_llm_adapter):
    """配置好的压缩链"""
    chain = CompressionChain(sample_config.compression)
    chain.register_strategy(
        "keyword_extractor",
        KeywordExtractorStrategy()
    )
    chain.register_strategy(
        "llm_summarizer",
        LLMSummarizerStrategy(mock_llm_adapter)
    )
    return chain
```

#### 测试示例

```python
# tests/test_compression.py

import pytest
from compression.chain import CompressionChain
from compression.strategies.keyword_extractor import KeywordExtractorStrategy
from compression.base import CompressionContext


@pytest.mark.asyncio
async def test_keyword_extractor_success(configured_chain):
    """测试关键词抽取成功"""
    result = await configured_chain.execute(
        content="查询北京数据中心的路由器BR-001的状态",
        context=CompressionContext(preserve_key_fields=["北京", "BR-001"])
    )

    assert result.success
    assert result.compression_ratio < 1.0
    assert "北京" in result.compressed_content
    assert "BR-001" in result.compressed_content


@pytest.mark.asyncio
async def test_compression_skips_on_failure():
    """测试压缩失败时跳过"""
    chain = CompressionChain(CompressionConfig(
        strategies=["nonexistent_strategy"],
        max_retries=1,
    ))

    result = await chain.execute(
        content="Test content",
        context=CompressionContext()
    )

    assert not result.success
    assert result.compressed_content is None


@pytest.mark.asyncio
async def test_all_strategies_fail():
    """测试所有策略都失败时返回None"""
    chain = CompressionChain(CompressionConfig(
        strategies=["keyword_extractor"],
        max_retries=0,
    ))
    chain.register_strategy("keyword_extractor", KeywordExtractorStrategy())

    result = await chain.execute(
        content="Test",
        context=CompressionContext()
    )

    # 关键词抽取不适用于短文本
    assert result.success or not result.success
```

### 11.5、可维护性

#### 版本管理

```python
# version.py

__version__ = "0.1.0"
__version_info__ = tuple(int(x) for x in __version__.split("."))

VERSION = __version__
```

#### 健康检查

```python
# common/health.py

from dataclasses import dataclass
from typing import Optional
import asyncio


@dataclass
class HealthStatus:
    """健康状态"""
    healthy: bool
    component: str
    message: Optional[str] = None
    latency_ms: Optional[float] = None


class HealthChecker:
    """健康检查器"""

    def __init__(self):
        self._checks: dict[str, callable] = {}

    def register_check(self, name: str, check: callable):
        """注册健康检查"""
        self._checks[name] = check

    async def check_all(self) -> list[HealthStatus]:
        """执行所有健康检查"""
        results = []
        for name, check in self._checks.items():
            try:
                if asyncio.iscoroutinefunction(check):
                    healthy, latency = await check()
                else:
                    healthy, latency = check()

                results.append(HealthStatus(
                    healthy=healthy,
                    component=name,
                    latency_ms=latency,
                ))
            except Exception as e:
                results.append(HealthStatus(
                    healthy=False,
                    component=name,
                    message=str(e),
                ))

        return results

    async def is_healthy(self) -> bool:
        """检查整体是否健康"""
        results = await self.check_all()
        return all(r.healthy for r in results)
```

#### 使用示例

```python
# 在应用启动时注册健康检查
health = HealthChecker()

health.register_check("redis", async lambda: await check_redis())
health.register_check("llm", async lambda: await llm_adapter.health_check())
health.register_check("template_cache", lambda: (cache.is_ready(), None))

# 在健康检查端点中使用
@app.get("/health")
async def health_check():
    results = await health.check_all()
    all_healthy = all(r.healthy for r in results)

    return {
        "status": "healthy" if all_healthy else "unhealthy",
        "checks": [
            {
                "component": r.component,
                "healthy": r.healthy,
                "latency_ms": r.latency_ms,
            }
            for r in results
        ]
    }
```

---

## 附录A：配置示例

```yaml
# config.yaml

connection_pool:
  max_size: 20
  min_size: 5
  timeout: 30.0
  keep_alive: true

rate_limit:
  enabled: true
  requests_per_second: 100
  max_concurrent: 50
  burst_size: 200
  redis:
    enabled: true
    key_prefix: "a2a_sdk:ratelimit:"

prompt:
  template_sources:
    - "https://template-server.example.com/templates/"
    - "file://./templates/"
  cache_ttl: 3600
  refresh_interval: 300
  semantic_matching:
    enabled: false  # 默认关闭语义匹配
    llm_adapter_type: "http"
    llm_adapter_config:
      url: "http://semantic-llm.example.com/api/v1/embed"
      api_key: "${SEMANTIC_LLM_API_KEY}"

compression:
  enabled: true
  default_strategies:
    - "keyword_extractor"
    - "llm_summarizer"
  max_retries: 3
  retry_delay: 1.0
  preserve_key_fields:
    - "intent"
    - "device_id"
    - "operation"
  summarizer:
    adapter_mode: "domain"
    prompt_mode: "builtin"

llm:
  adapter_type: "http"
  adapter_config:
    url: "http://domain-llm.example.com/api/v1/completions"
    api_key: "${LLM_API_KEY}"
    timeout: 60

error:
  format_type: "standard"

log_level: "INFO"
```

---

## 附录B：Markdown模板示例

```markdown
# 网络设备查询模板

## 意图识别
请识别用户的网络操作意图：{{intent}}

## 参数提取
- 设备类型：{{parameters.device_type}}
- 设备位置：{{parameters.location}}
- 查询时间范围：{{parameters.time_range}}

## 业务规则
当设备类型为"核心路由器"时，必须验证操作权限。
```
