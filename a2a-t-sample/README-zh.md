# a2a-t-sample

`a2a-t-sample` 是 A2A-T Python SDK 的示例用例集，以用例独立目录组织，包含客户端与服务端可直接运行的入口。

当前示例基于官方 Python A2A SDK（`a2a-sdk`）运行真实的 A2A `HTTP+JSON/REST` 链路：
- `a2a-t-sdk` 客户端仅用于生成结构化 prompt
- `a2a-t-sdk` 服务端仅用于校验结构化 prompt

## 用例清单

| 目录 | 说明 |
| --- | --- |
| [subscribe-incident/](subscribe-incident/) | 事件订阅用例——客户端生成 Notification-T prompt → 服务端校验 → 流式推送 Incident artifact（含注册中心） |
| [negotiation/](negotiation/) | 协商闭环用例——离线 propose → accept 往返（脚本化 mock LLM，双语言） |

## 模块内资源

- 共享环境配置模板：`env.example`（复制为 `a2a-t-sample/.env` 使用）
- 共享依赖：`requirements.txt`
- 订阅用例客户端/服务端/注册中心入口：`subscribe-incident/src/{client_example,server_example,agentcard_example}/`
- 订阅用例 mock LLM 响应数据：`subscribe-incident/resources/mock_responses/`（zh-CN / en-US）
- 协商用例入口：`negotiation/src/negotiation_demo/`（demo 运行时与生成策略）
- 协商用例场景数据与脚本化 mock LLM 响应：`negotiation/resources/`

新用例直接在 `a2a-t-sample/` 下以 `subscribe-incident/` 的同级目录添加。

## 快速开始（共享）

```bash
cd a2a-t-sample
# 首次使用：先在仓库根目录执行 uv sync --dev（创建 .venv 并安装 SDK 依赖）
cp env.example .env      # 复制后确认 .env 中 A2AT_LLM_API_KEY 为空
uv pip install -r requirements.txt
```

> 如果 `A2AT_LLM_API_KEY` 留空，两个用例都会自动使用用例各自的脚本化 mock LLM 响应，无需真实 API 即可跑通完整流程。使用 mock 时每次响应前会输出一行带角色前缀的独立日志 `[<角色>] llm-mock: using canned mock LLM response`（客户端为 `[client]`、服务端为 `[server]`），用于区分 mock 与真实 LLM。

## 协商（Negotiation）闭环样例

协商样例是**离线**协商闭环 demo（Java `a2a-t-sample` NegotiationDemoApp 的 Python 对应物，内嵌 HTTP 服务器替换为**进程内运行时调用**）。与 Java 版协商样例需真实 LLM API key 不同，本样例未配置 key 时用脚本化 mock LLM 应答，整个往返完全离线运行。

4 报文流转：

| 报文 | 方向 | 内容 | 任务状态 |
| --- | --- | --- | --- |
| 1 | client→server | Task-T（参数缺失） | → |
| 2 | server→client | Negotiation-T 信息协商请求（动态列出缺失参数） | INPUT_REQUIRED |
| 3 | client→server | Task-T（参数补齐）+ Negotiation-T accept | → |
| 4 | server→client | 诊断结果（从提取参数动态生成） | COMPLETED |

**LLM 参与约定**：`fromData` 策略下协商消息生成环节是纯确定性渲染（不调 LLM），Task-T 槽位提取、语义校验与 fromText 协商抽取仍调用 LLM（缺 key 时由脚本化 mock LLM 应答，因此样例离线可跑）。配置了真实 API key 时同一流程调用真实 LLM。

### 协商样例结构

| 目录 | 作用 |
| --- | --- |
| `negotiation/src/negotiation_demo/` | 入口 `__main__` / `demo_app`：解析 `--fromText` / `--language` 并驱动 4 报文往返 |
| `negotiation/src/negotiation_demo/client_runtime.py` | 客户端运行时：Task-T prompt 生成 + accept 生成（`generate_task_prompt_from_data_with_schema` + `generate_negotiation_accept_prompt_from_data`） |
| `negotiation/src/negotiation_demo/server_runtime.py` | 服务端运行时：`validate_task_prompt_and_data_filling` 驱动的缺失检测 → propose 生成 → 诊断渲染 |
| `negotiation/src/negotiation_demo/shared/` | 策略层（fromData / fromText）、mock LLM 桩、场景数据加载 |
| `negotiation/resources/` | 场景数据（`scenario.json` 等）+ 双语言 mock LLM 响应 |

### 协商样例启动

```bash
# 从 a2a-t-sample 目录执行；PYTHONPATH 指向用例 src
$env:PYTHONPATH = "$pwd\negotiation\src"     # PowerShell；bash 下用 export PYTHONPATH=...

uv run python -m negotiation_demo                 # fromData 策略（协商消息确定性生成）
uv run python -m negotiation_demo --fromText      # fromText 策略（每条协商消息一次 LLM 抽取）
uv run python -m negotiation_demo --language zh-CN
```

本样例为进程内运行时（无 HTTP 服务端），不涉及传输端点开关。Windows 控制台如遇中文乱码，先执行 `chcp 65001`。

## 事件订阅（subscribe-incident）样例

最小端到端用例，基于 `a2a-sdk` 的真实 HTTP+JSON 链路，演示故障订阅场景：客户端生成 prompt → 服务端校验 → 流式推送 Incident artifact。流程包含注册中心交互、客户端 prompt 生成、服务端校验与流式 artifact 推送，并保留 LLM mock 能力。

| 阶段 | 谁调 SDK | SDK 做什么 | LLM 调用次数 |
| --- | --- | --- | --- |
| 启动 | client + server | A2ATClient / A2ATServer 初始化 | 0 |
| Prompt 生成 | 客户端 | 场景识别 + slot 提取 + 模板渲染 | 2 |
| Prompt 校验 | 服务端 | 场景识别 → slot 提取 → 语义校验 | 3 |
| 流式推送 | 客户端 | 归一化 stream 事件 | 0 |

### 消息体约定

| 位置 | 内容 |
| --- | --- |
| text part | scenario 名（`"create incident subscription"`） |
| `metadata[Notification-T/v1]` | 生成的 promptText |
| header `A2A-Extensions` | `https://projects.tmforum.org/a2aproject/telecommunication/extensions/Notification-T/v1` |

- Prompt 生成输入为**硬编码自然语言**，按照 `A2AT_LANGUAGE` 自动选择：
  - `zh-CN`：`"请生成一个Incident事件订阅任务：通知主题为Incident，订阅条件为订阅级别为critical的ETH-LOS的故障，上报通知数据格式为DataPart"`
  - `en-US`：`"Generate an Incident event subscription task: notification topic is Incident, subscription condition is a critical ETH-LOS fault, and the notification data format is DataPart"`

### 服务端校验流程

`execute_server_flow` 的状态机：

1. **校验 `A2A-Extensions` header**：必须包含 Notification-T 扩展 URI，否则抛 `ValueError("a2a client extensions is not exist.")`
2. **从 `metadata[Notification-T/v1]` 提取 promptText**（不再从 `parts[0].text` 读取）
3. **`SUBMITTED`** → 调用 `A2ATServer.check_task_prompt` 校验
   - 校验失败 → 发 **`REJECTED`** 状态（不抛异常）
   - 校验通过 → 发 **`WORKING`** 状态
4. **循环推送 Incident artifact**（每 `ARTIFACT_SEND_INTERVAL_SECONDS = 5.0s` 一次，默认无限推，可被 `max_artifacts` 截断）
5. 推送过程异常 → 发 **`FAILED`** 状态

### AgentCard 数据

- name：`SPN Domain Agent`，provider：`Huawei`
- 仅声明 `Notification-T/v1` 扩展（subscribe 用例不涉及 Task-T）

### 事件订阅样例启动

> 模块位于 `subscribe-incident/src/`，而 `.env` 位于 `a2a-t-sample/`，因此需要**在 `a2a-t-sample` 目录下设置 `PYTHONPATH` 指向 `subscribe-incident/src`**。

```powershell
# 进入 sample 目录（.env 所在位置）
cd a2a-t-sample

# 设置模块搜索路径（每个终端都要执行）
$env:PYTHONPATH = "$pwd\subscribe-incident\src"
```

```bash
# 终端1：启动注册中心（端口 5001）
uv run python -m agentcard_example.registry_main

# 终端2：启动服务端（端口 8000）
uv run python -m server_example.server_main

# 终端3：启动客户端（持续接收 artifact，Ctrl+C 停止）
uv run python -m client_example.client_main
```

### 限制接收数量（可选）

```powershell
$env:A2AT_SAMPLE_MAX_ARTIFACTS = "5"
uv run python -m client_example.client_main
```

### 关键点

- **SDK 是中间层**：client/server 不直接调 LLM，通过 A2ATClient/A2ATServer 间接调用
- **LLM 只在 prompt 阶段用**：推送 artifact 时不调 LLM
- **无协商**：客户端一次性提交完整输入，服务端校验通过直接推送
- **三层解耦**：client（发现 + 消费）→ server（注册 + 推送）→ registry（注册中心）
- **mock 能力保留**：`common/mock_llm.py` + `resources/mock_responses/` 在 key 为空时自动启用，完整流程无需真实 API

## 运行测试

```bash
# 运行全部用例测试（从 a2a-t-sample 目录）
uv run pytest subscribe-incident/test/ -v
uv run pytest negotiation/test/ -v
```