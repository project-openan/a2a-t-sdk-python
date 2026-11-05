# observability-sample — 看见你的 A2A-T 智能体在做什么

一个可直接运行的样例：展示 **A2A-T 交互的完整生命周期**——客户端发起请求、服务端处理、
结果流式返回——每一步都由 `a2a-t-sdk` 的可观测层自动记录（trace / 指标 / 日志）。

样例里**没有任何手写的 OpenTelemetry 代码**：服务端加一个包装器、客户端做一次注册，
其余全部由 SDK 自动完成。

---

## 可以体验什么

| 场景（`--scenario=`） | 故事 |
|------------------------|------|
| `native` | 一个任务完整走完：客户端提交、服务端处理后流式返回 artifact。观察请求如何把两端"点亮"。 |
| `a2at` | 用 LLM 生成任务 prompt（默认 mock LLM——无需 API Key），并在 SDK 调用外手动加一个"L4" Span。 |
| `negotiation` | 智能体**先谈价、后干活**：两轮报价通过纯消息完成（此时还没有任务），双方达成一致后，真正的任务才启动并流式返回结果。 |

## 快速开始

前置条件：已在仓库根目录，且安装了 [uv](https://docs.astral.sh/uv/)。

```bash
# 仓库根目录——终端 1：启动服务端（端口 8100）
cd a2a-t-sample
export PYTHONPATH="$PWD/observability-sample/src"
export A2AT_OBSERVABILITY_ENABLED=true
uv run python -m obs_sample.server_main

# 终端 2：运行客户端场景
cd a2a-t-sample
export PYTHONPATH="$PWD/observability-sample/src"
uv run python -m obs_sample.client_main --scenario=native
```

Windows PowerShell：把 `export` 换成 `$env:PYTHONPATH = "$PWD\observability-sample\src"`。

两个终端都会输出日志（`[server] ...` / `[client] ...`）；默认配置下服务端还会把每个
span 以 JSON 打印到控制台。

## 场景说明

### native — 一个任务，两端可见

1. 终端 1 启动服务端；终端 2 以 `--scenario=native` 运行客户端。
2. 客户端提交任务；服务端经历 *submitted → working* 并流式返回 artifact。
3. 每一步在**两端**都以 span 呈现，并汇入同一条 trace（客户端自动把 W3C `traceparent`
   传给服务端）。

### a2at — 用 LLM 生成 prompt

```bash
uv run python -m obs_sample.client_main --scenario=a2at
```

客户端通过 SDK 的 LLM 门面生成任务 prompt。`a2a-t-sample/.env` 里 `A2AT_LLM_API_KEY`
留空时会使用**mock LLM**（固定回复），完全离线可跑。LLM 的 token 用量会被自动计量并
挂到 trace 上。

### negotiation — 先谈成，再执行

```bash
# 终端 1：以协商模式重启服务端
uv run python -m obs_sample.server_main --scenario=negotiation
# 终端 2：
uv run python -m obs_sample.client_main --scenario=negotiation
```

这是 Negotiation-T 谈判的真实时序：

1. **第 1 轮** — 客户端出价；服务端还一个不同的价并断开连接。*此时还没有任务——
   谈判通过纯消息进行。*
2. **第 2 轮** — 客户端重连并接受对方的还价；服务端确认达成一致，再次断开。
3. **达成一致后任务才启动** — 客户端以独立请求启动真正的任务生命周期并接收结果。

每一轮都是一次独立交互；样例把它们串起来，让你能在同一个地方看到整场谈判
（见下方 Langfuse）。

## 把所有数据发到 Langfuse（可选）

在启动**两个进程之前**设置三个变量，所有 span 就会进入你的 Langfuse 项目——
每次交互一条可浏览的 trace：

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://us.cloud.langfuse.com"
```

在 Langfuse 界面里，每一轮的客户端/服务端 span 和最终任务会呈现在同一条时间线上——
谁出了什么价、哪一轮被接受、每一步耗时多久，一目了然。

## 记录了什么（速查表）

| 你做的事 | SDK 记录的内容 |
|----------|----------------|
| 客户端发请求 | `SendStreamingMessage` span（CLIENT）+ 耗时指标 |
| 服务端收到请求 | `SendStreamingMessage` span（SERVER），是客户端 span 的子节点 |
| 服务端流式发出事件 | `SendStreamingMessage-event` span，LINK 关联入口 |
| 发出 / 收到协商消息 | `SendStreamingMessage-negotiation` span（含轮次与施为动词） |
| 服务端推送通知 | `SendMessage-pushNotification` span，LINK 关联服务端入口 |
| LLM 消耗 token | token 用量指标 + 下一跳 trace 上的 `gen_ai.usage.*` |
| 值得记录的事件 | logger `a2at.observability` 上的结构化日志 |

三类信号可独立开关——见下方环境变量。

## 环境变量

常用：

| 变量 | 默认值 | 含义 |
|------|--------|------|
| `A2AT_OBSERVABILITY_ENABLED` | `true` | 总开关——`false` 时 SDK 完全透明 |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | 未设置 | 把 trace/指标/日志发到 OTLP collector，而不是控制台 |
| `A2AT_CONSOLE_EXPORTER` | `true` | `false` 关闭 dev 模式控制台兜底（stdout 不再输出 JSON） |
| `OTEL_SERVICE_NAME`（或 `A2AT_SERVICE_NAME`） | `a2a-t-agent` | 本进程在后端的名称 |
| `A2AT_OBS_SAMPLE_PORT` | `8100` | 样例服务端端口 |
| `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL` | 未设置 | 启用 Langfuse 后端 |

进阶（较少用到）：`A2AT_TRACE_ENABLED`、`A2AT_METRIC_ENABLED`、`A2AT_LOG_ENABLED`
（按信号独立开关——例如关 trace 后指标与日志仍工作）、`A2AT_EXPORTER_PROTOCOL`
（`grpc` / `http/protobuf`）、`OTEL_INSTRUMENTATION_A2AT_SDK_ENABLED=false`（硬关闭）。

## 说明

- 服务端使用**官方默认**的 `DefaultRequestHandler`（a2a-sdk 1.1.x 的别名，指向
  `DefaultRequestHandlerV2`）：可观测装饰器会自动检测 V2 形状，并用装饰后的
  executor / push sender 重建其任务注册表——per-event 与 push span 在官方 handler
  上同样生效。（`LegacyRequestHandler` 通过逐属性包装的方式继续受支持。）
- 推送通知会回环到服务端的 `/push-sink`，因此样例能端到端展示
  `SendMessage-pushNotification` span。
- 离线验证辅助脚本与样例代码放在一起（`check_*.py`、`verify_langfuse.py`）——它们是
  一次性调试工具，不属于样例主流程。
