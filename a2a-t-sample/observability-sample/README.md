# observability-sample — A2A-T 可观测性演示（v3.0 装饰器架构）

基于 `a2a-t-sdk[observability]` v3.0 的端到端可观测性样例：一行装饰器接线、零手动 OTel 代码。

## 接线（v3.0）

- Server：`A2ATRequestHandlerDecorator(LegacyRequestHandler(...))` —— 自动创建 SERVER 入口 Span
  （从 `ServerCallContext.state["headers"]` 提取 traceparent 作为父上下文，无需 ASGI 中间件）、
  包装 executor 产生 per-event Span、包装 push sender 产生 push Span
- Client：`register_client_factory(factory)` —— 之后 `factory.create(card)` 产出的 client 自动携带
  观测 transport（CLIENT Span + traceparent 注入，经 `ClientCallContext.service_parameters` 落到 HTTP 头）
- OTel 自动配置：SDK 在首次使用时自动装配 Tracer/Meter/LoggerProvider（未配置
  `OTEL_EXPORTER_OTLP_ENDPOINT` 时回退 Console 导出），样例不含任何手动 setup 代码

## a2a-sdk 1.1.x handler caveat（必须 LegacyRequestHandler）

完整 per-event Span 富化需要 `LegacyRequestHandler`。a2a-sdk 1.1.2 默认的 `DefaultRequestHandler`
（V2）在构造期即捕获裸的 `agent_executor`/`_push_sender` 引用（ActiveTaskRegistry），装饰器构造期的
executor/push 包装会被绕过。本样例固定使用 `LegacyRequestHandler`（服务端行为与 V2 兼容）。

## 运行（离线，默认 Console 输出）

```powershell
cd a2a-t-sample
$env:PYTHONPATH = "$pwd\observability-sample\src"
$env:A2AT_OBSERVABILITY_ENABLED = "true"   # 观测总开关（默认即 true；置 false 时装饰器完全直通）

# 终端 1：server（端口 8100）
uv run python -m obs_sample.server_main

# 终端 2：client（原生 a2a-python 流量）
uv run python -m obs_sample.client_main --scenario=native

# 或 client（A2ATClient 模板生成 + trace_facade L4 Span，需仓库 a2a-t-sample/.env：
# 不存在时从 env.example 复制并将 A2AT_LLM_API_KEY 置空（A2AT_LLM_API_KEY=），置空后自动走 mock LLM）
uv run python -m obs_sample.client_main --scenario=a2at

# 协商场景：server 需以 negotiation 模式启动（返回 Negotiation-T 终态 Message）
uv run python -m obs_sample.server_main --scenario=negotiation
# 然后另一个终端：
uv run python -m obs_sample.client_main --scenario=negotiation
```

`--scenario=a2at` 分支会调用一次幂等的 `setup()`（SDK 公共自动配置入口）：L4 门面 Span
产生在首个 A2A 请求之前，需先完成 OTel 自动装配才不会丢失；native 场景无需任何调用，
首个请求即触发自动配置。

端口可用 `A2AT_OBS_SAMPLE_PORT` 覆盖（默认 8100）。样例 client 会请求 push notification
（回环到 server 的 `/push-sink`），用于演示 `SendMessage-pushNotification` Span。

## 预期输出（v3.0 Span 命名，a2a-java 对齐）

Client 终端（CLIENT Span）：

- `SendStreamingMessage`（入口 Span，含 `extension.name`、`gen_ai.operation.name`、
  `gen_ai.client.operation.duration` 指标；traceparent 注入请求头）
- `SendStreamingMessage-event`（per-event，LINK 到入口 Span）
- `--scenario=a2at` 另有 `a2at.sdk.client.generate_task_prompt`（L4）
- `--scenario=negotiation` 另有 `SendStreamingMessage-negotiation`（PARENT 到入口 Span，
  含 `negotiation.id/round/max_rounds/performative` 属性；服务端返回 Negotiation-T 终态 Message 时自动识别）

Server 终端（SERVER/INTERNAL Span，与 Client 同 trace_id —— W3C traceparent 端到端贯通）：

- `SendStreamingMessage`（SERVER 入口 Span，父 = Client 入口 Span）
- `SendStreamingMessage-event`（per-event INTERNAL Span，父 = SERVER 入口 Span）
- `SendMessage-pushNotification`（CLIENT kind Span，LINK 到 SERVER 入口 Span）
- `--scenario=negotiation` 时 server 返回终态协商 Message（不产生 per-event Span 流）

日志与指标：

- logger `a2at.observability` 自动经 OTLP/Console 导出（`task.status_changed` / `task.artifact` 等）
- 指标：`gen_ai.client.operation.duration`（client）、`a2at.task.request.duration`（side=client/server）

## 配置开关

| 环境变量 | 默认 | 说明 |
| --- | --- | --- |
| `A2AT_OBSERVABILITY_ENABLED` | `true` | 观测总开关；`false` → 装饰器完全直通（无任何观测） |
| `A2AT_TRACE_ENABLED` | `true` | Trace 信号开关；`false` → 不产生 Span，指标/日志仍工作 |
| `A2AT_METRIC_ENABLED` | `true` | Metric 信号开关 |
| `A2AT_LOG_ENABLED` | `true` | Log 信号开关 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 未设置 | 未设置 → Console 导出（dev 模式） |
| `A2AT_EXPORTER_PROTOCOL` | `grpc` | OTLP 协议（`grpc` / `http/protobuf`） |
| `OTEL_SERVICE_NAME`（或 `A2AT_SERVICE_NAME`） | `a2a-t-agent` | Resource `service.name` |
| `OTEL_INSTRUMENTATION_A2AT_SDK_ENABLED` | `true` | OTel 主开关；`false` → 全部 NoOp 直通 |

## 切换 OTLP 后端

```powershell
$env:OTEL_EXPORTER_OTLP_ENDPOINT = "http://collector:4317"
```

（需另装 `opentelemetry-exporter-otlp-proto-grpc`；默认 Console 输出无需任何额外依赖。）

## 说明

- 断言式端到端验证见 `tests/integration/test_e2e_observability.py`（`A2AT_TEST_A2A=1`）。
- a2at 场景的 mock LLM 资源复用 `../subscribe-incident/resources/mock_responses`。
