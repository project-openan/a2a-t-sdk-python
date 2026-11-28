# RAN 节能 —— 面向真机网管集成的 A2A-T 客户端

本样例是 RAN 节能集成的**客户端**：ADK agent 读取运维人员请求，A2A-T SDK 生成 Task-T，
客户端与厂商网管（或本地参考 RAN 服务端）协商，直到任务被接受并执行。所有决策均由模型产生，
不再提供 scripted / offline 决策模式。

同一个客户端对外提供两个入口：

- **Web UI**（presentation 或 run explorer），供工程师操作；
- **A2A gateway**，供 Gemini Enterprise 等调用方接入。

样例还附带一个**本地 RAN 参考服务端**，便于在没有厂商网管时自测。

```
工程师 ── Web UI ──┐
                   ├── A2A-T / 标准 A2A 客户端 ── 真机网管（message:send + webhook）
Gemini Enterprise ── gateway ──┘
                   └── 本地参考服务端（可选）
```

## 1. 快速开始（真机网管）

1. 复制 `env.example` 为 `.env`，填写：
   - `ADK_MODEL_*`（必填），以及需要单独配置的 `A2AT_LLM_*`（SDK 结构化调用）；
   - `A2AT_SAMPLE_NMS_*`（A2A 地址、token 接口、用户名/密码、服务区域与站点）；
   - 网管经 NAT/代理访问本机时，填写 `A2AT_SAMPLE_WEBHOOK_PUBLIC_URL`；
   - 使用 gateway 时填写 `A2AT_SAMPLE_GATEWAY_PUBLIC_URL`。
2. 启动客户端对接厂商：

```bash
python run_demo.py --nms --ui --protocol both
```

真机 `--nms` 固定使用 webhook transport，交付 `.env` 模板中应显式设置
`A2AT_SAMPLE_LONG_TASK_TRACKING=1`。

3. 打开 `http://127.0.0.1:8080/`，选择协议（A2A-T 或标准 A2A），输入运维请求，并在页面时间线
   或 `/tester` 查看证据。

### Gemini Enterprise 网关

```bash
python run_demo.py --nms --ui --gateway \
  --gateway-public-url https://ran-client.example/gateway \
  --gateway-port 8001 --gateway-webhook-port 9001 \
  --gateway-downstream-protocol a2a-t
```

- 网关 AgentCard 使用 `A2AT_SAMPLE_GATEWAY_PUBLIC_URL`；设置 `A2AT_SAMPLE_AUTH_TOKEN` 时卡片声明
  bearer 方案并由网关校验。
- 一条入站消息对应一个客户端 run 和一个新的入站 Task；不支持同一 Task 的多轮续传。
- 调用方身份来自 A2A 认证上下文（`user_name`），不再使用固定占位符。
- UI 与 gateway 各绑定一个回调接收端口，两个端口都必须被网管访问到。

### 本地参考服务端

```bash
python run_demo.py --ui            # 本地 RAN 服务端 + UI，AgentCard 直连发现
python run_demo.py --stop          # 只停本实例启动的进程
python run_demo.py --stop --reclaim-port  # 另回收本实例遗留的 Web UI
```

## 2. 客户端链路

1. ADK intent 层读取运维请求（仅用于展示与 approval 1）。
2. SDK 从**运维原文**生成 Task-T，样例不重写请求、不猜测 slot。
3. 执行 SDK slot 抽取；`slot.*` 失败在 Task-T 生成前拦截，生成的 Task-T 只记录为本地通过证据
   （不再做第二次校验调用）。
4. 与网管协商：information / target / feasibility，客户端 ADK agent 负责每一次答复与决策。
5. 发送已接受请求，跟踪 Task 到终态（含长任务），发布报告。

Web UI 保留 approval 1（`--mode confirm`）。执行审批属于厂商网管，样例不再提供本地 MIE/MML
审批环节。

## 3. 测试台

`/tester` 使用 OSP 用例集（`eval/osp/cases`）对配置的端点执行验收，并导出 xlsx 报告，包含
Task-T、校验、发送、协商、accepted、working、final 各阶段证据。客户端始终使用 live 决策
agent；Q/P 列仅作为期望参考，不再回放。

```bash
# 先启动 UI，再打开 /tester。
python run_demo.py --nms --ui
```

正式验收必须对准真机网管；本地参考服务端只用于进场前演练。

### 语言

样例只有一种进程级语言：`--language en-US|zh-CN`（也可用 `A2AT_LANGUAGE`），启动器默认
`en-US`。它决定 SDK 提示词资源、场景数据、presentation、`/tester` 与 `/status` 的语言，修改后
需要重启。tester 会记录每个批次的创建语言；切换语言后打开旧批次，消息与导出仍使用该批次的
原语言，界面会给出提示。OSP 用例原文、wire/trace 证据与协议错误文本不做翻译。

## 4. 可观测性

- `/runs` 是只读运行控制台：持久化 run、wire 原始报文、transcript、trace 时间线。
- 每个 run 写入 `runs/<runId>/wire.jsonl` 与 `runs/<runId>/server-trace.jsonl`。
- Langfuse 导出可选（`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`）；未配置时为空操作，
  且不影响协议结果。

## 5. 配置

所有键见 `env.example`。优先级：CLI 参数 > shell 环境 > 每 run `.env` > 源 `.env` > 默认值。

交付关键键：

| 键 | 含义 |
|---|---|
| `ADK_MODEL_*` / `A2AT_LLM_*` | ADK agent 与 SDK 结构化调用使用的模型端点 |
| `A2AT_SAMPLE_NMS_*` | 厂商 A2A 地址、token 接口与服务清单 |
| `A2AT_SAMPLE_WEBHOOK_*` | UI 回调接收端口及其公网 URL |
| `A2AT_SAMPLE_GATEWAY_*` | gateway 公网 URL、回调端口与下游协议 |
| `A2AT_SAMPLE_LONG_TASK_TRACKING` | 观察窗口结束后继续跟踪 Task |
| `A2AT_SAMPLE_AUTH_TOKEN` / `SERVER_AUTH_TOKEN` | UI/gateway 与本地服务端的 bearer token |

## 6. 测试

```bash
PYTHONPATH=. ../../.venv/Scripts/python.exe -m pytest tests -q
../../.venv/Scripts/python.exe -m ruff check shared protocol demo tests run_demo.py
```

单元测试通过 `tests/support/` 下的 test-only double 替换模型与 SDK 结构化调用；集成与验收测试
使用配置的 live 端点与真机网管。样例不在 CI 中运行，本地全量套件是唯一自动化门禁。
