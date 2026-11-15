# 预期标注实例 · 启动与操作指南（黄区可执行）

本文件是给现场（黄区）AI Agent / 操作员的**运行手册**：如何在**不改动现场 live 实例**的前提下，
用一个只读进程启动 `/annotate`，从现场已有的 tester 批次里采集候选、人工确认、导出期望值。

配套说明见 `eval/osp/README.md` 第 12 节；本文件只讲"怎么起、怎么点、怎么交回"。

---

## 1. 铁律（违反会污染现场数据）

1. **不要**把完整的 Web UI（`run_demo.py` / `demo.web.serve` / `demo.web.app`）指向现场 `runs/` 目录。
   完整应用启动时会执行 `RunManager.recover_runs()`，把现场尚未结束的 run 标成 `interrupted`。
2. 标注实例**只读**源目录：只读 `runs/tester/*.json` 与 `runs/client-state.sqlite3`。
3. 唯一允许写的文件是标注库（`--store`）。**始终显式传 `--store` 到源目录之外**，让源目录零写入。
4. 不启动 run、不下发任务、不答复审批、不恢复 run 状态——该进程没有 `RunManager`，这是设计，不是巧合。
5. 不要删除标注库：进度、采纳、确认人、来源 runId 都在里面；重启实例要沿用同一个 `--store`。

---

## 2. 前置检查（逐条确认，失败就停）

```bash
cd <sample>/a2a-t-sample/ran-energy-saving     # 本仓库的样例目录
export PYTHONPATH=.                             # 必须；模块按样例根导入
ls eval/osp/cases/*.yaml | wc -l                # 期望 100
ls -d <现场 runs 目录>/tester                   # 现场批次目录，形如 .../runs/tester
ls -l <现场 runs 目录>/client-state.sqlite3     # 服务端报文来源；缺失时只能标注 slot_errors
python -c "import uvicorn, starlette; print('ok')"
```

- Python 用样例的虚拟环境即可（蓝区是 `../../.venv`，黄区以现场实际为准）。
- 不需要现场 `.env`：标注实例不读取模型/NMS 配置，只读已落盘的产物。
- 权限：源目录**只读**即可；`--store` 目录需要写权限。

---

## 3. 启动（三步）

### 3.1 先验证指向，不开端口

```bash
python -m demo.web.annotate \
  --runs-dir <现场 runs 目录> \
  --store  <标注库目录>/annotations.sqlite3 \
  --check
```

期望输出（路径按现场替换）：

```
[annotate] source batches : /…/runs/tester
[annotate] source run store: /…/runs/client-state.sqlite3
[annotate] annotation store: /…/annotations.sqlite3
[annotate] case corpus     : packaged eval/osp/cases
```

判定：

| 输出 | 含义 | 动作 |
|---|---|---|
| 出现 `WARNING: the client store is missing` | 找不到 `client-state.sqlite3`（或 WAL 未 checkpoint 导致只读打不开） | 确认路径；确认现场实例在运行；或接受"只能标注 slot_errors" |
| 出现 `note: the annotation database lives inside the source directory` | `--store` 落在源目录内 | 改到源目录之外再继续 |
| 退出码非 0 | 参数/路径错误 | 按报错修正后重跑 |

### 3.2 后台启动（推荐 8090；与 Web UI 的 8080 不冲突）

```bash
mkdir -p <标注库目录>
nohup python -m demo.web.annotate \
  --runs-dir <现场 runs 目录> \
  --store  <标注库目录>/annotations.sqlite3 \
  --host 127.0.0.1 --port 8090 \
  > <标注库目录>/annotate.log 2>&1 &
echo $! > <标注库目录>/annotate.pid
```

Windows（Git Bash 同样可用）：

```bash
python -m demo.web.annotate --runs-dir "<现场 runs 目录>" \
  --store "<标注库目录>\annotations.sqlite3" --port 8090 \
  > "<标注库目录>\annotate.log" 2>&1 &
```

- 端口被占用就换一个（8091、8092…），不要 kill 现场进程。
- `--host 0.0.0.0` 只在确实需要局域网访问时使用；该页面会展示请求原文与协商报文。

### 3.3 健康检查

```bash
curl -s http://127.0.0.1:8090/health
# {"status":"ok","service":"ran-energy-saving-annotate"}

curl -s http://127.0.0.1:8090/ready
# {"status":"ready","checks":{"cases":true,"store":true}}   → HTTP 200
# {"status":"not_ready",...}                                 → HTTP 503，按 checks 定位
```

`cases: false` 表示用例目录读不到；`store: false` 表示标注库目录不可写。

打开页面：`http://127.0.0.1:8090/annotate`（`/` 会跳转过去）。

---

## 4. 页面操作（对齐语义）

页面结构：`用例 → 协议任务（A2A-T / A2A）→ 子任务`（`拦截 slot_errors` / `协商报文`）。
左侧固定、占满整屏高度并独立滚动；时间窗与"采集候选/导出"在顶栏。

1. **采集候选**：顶栏选时间窗（默认最近 1 天），点「采集候选」。返回后看提示：扫了多少批次、得到多少候选、
   新增多少、跳过了什么（`outside_window` / `limit` / `unreadable`）。出现"已达批次上限"是显式提示，不是静默截断。
2. **选用例与协议**：左栏搜索编号或名称；点 `A2A-T` 或 `A2A` 行。没有子任务的协议显示「无需标注」，
   可勾选隐藏/显示。过滤项：协议、子任务、状态、有/无候选。
3. **选运行轮次**：右侧顶部下拉只列**当前用例 + 当前协议**的观测，按时间倒序；默认选**最新一条真机 + live**。
   需要合并多次渲染时选「全部轮次」，候选会并列展示并按内容聚合 `出现 k/n 次`。
   三个勾选框：只看真机 / 只看 live / 只看一致候选（默认全开）。
4. **采纳**：
   - `拦截 slot_errors`：勾选要采纳的错误行；可在「关键词」里加逗号分隔的 `contains`（中英各写一个即可，
     例如 `gain, 增益`）；右上 `mode` 选 `any`（任一命中，默认）或 `cover`（观测到的每条都要被覆盖）。
   - `协商报文`：按轮次分组；同一轮可采纳**多个备选**（任意一个命中即通过）；措辞需要收窄时点「编辑备选」。
5. **确认**：填备注（建议写"依据哪几轮的多数渲染"），点「确认」。确认后再改候选会自动退回"草稿"。
6. **降噪规则**：红底行表示"与用例现有预期不一致"（该拦截没拦截、轮数或类型不符），**不可采纳**；
   需要记录时写进备注，别把它固化成期望。

进度口径：子任务 151 个（52 拦截 + 10 A2A-T 报文 + 89 A2A 报文），顶栏按 `未采集 / 草稿 / 已确认` 统计。

---

## 5. 导出与合并（只导出，不写用例）

### 5.1 两条出口

| 出口 | 位置 | 内容 |
|---|---|---|
| 单用例片段 | 右侧「生成期望片段」 | 该用例已确认的两个协议块，已按 `expect.protocols` 层级（6 空格）缩进 |
| 全量导出 | 顶栏「导出全部 YAML / JSON」 | `osp-annotations/v1`，仅含已确认的用例 |

API（Agent 可用 curl 直接驱动，见第 7 节）。

### 5.2 合并到用例

把片段粘贴到 `eval/osp/cases/<用例>.yaml` 的 `expect.protocols` 下，示例：

```yaml
    a2a-t:
      validate: blocked
      validate_label: 拦截
      slot_errors:
        mode: any
        expect:
          - code: slot.constraint_violated
            contains:
              - gain
              - 增益
```

```yaml
      negotiation:
        expected: true
        expected_rounds: 1
        kinds: [feasibility]
        rounds:
          - …
        messages:
          - - |-
              ## Feasibility Negotiation
              …
```

- `slot_errors` 只允许出现在 `validate: blocked` 的 A2A-T 用例上。
- `messages` 外层下标 = 轮次（1 基），内层 = 该轮可接受的渲染；`expected_rounds > 1` 时必须每轮一组。
- `eval/osp/index.yaml` 不含这两类期望，**不需要**改。
- 用例文件在现场可能被热更新替换：合并前先 `git status` 确认没有他人未提交改动。

### 5.3 合并后必须验证

```bash
cd <sample>/a2a-t-sample/ran-energy-saving
PYTHONPATH=. python -m pytest tests/test_osp_eval_cases.py -q      # 契约：新键可解析、只在 blocked 用例上
PYTHONPATH=. python -m pytest tests/test_osp_expectations.py -q    # 比较器自检
```

可选（不连网管、只跑 SDK 三阶段，验证判定是否按新期望工作）：

```bash
PYTHONPATH=. python -m eval.osp.runner --ids <改动用例编号> --stop-after validate --verbose
```

---

## 6. 收尾与产物

```bash
kill "$(cat <标注库目录>/annotate.pid)"    # 只停标注实例，不动现场实例
```

| 产物 | 位置 | 说明 |
|---|---|---|
| 标注库 | `--store` 指定路径 | 候选、采纳、确认人、来源 runId；跨重启沿用 |
| 运行日志 | `<标注库目录>/annotate.log` | 启动参数、告警 |
| 全量导出 | 下载得到的 `osp-annotations.yaml / .json` | 交接给蓝区的最低成本形式 |
| 用例改动 | `eval/osp/cases/*.yaml` | 合并后提交；蓝区据此 review |

回传蓝区时随附：导出的 YAML/JSON、改了哪些用例、`pytest` 两条命令的输出、以及"哪些子任务仍未采集"。

---

## 7. HTTP API（Agent 自动化）

所有接口无鉴权，默认只绑 `127.0.0.1`。`{case}`=用例编号，`{protocol}`=`a2a-t|a2a`，
`{dimension}`=`slot_errors|messages`。

```bash
BASE=http://127.0.0.1:8090

# 采集
curl -s -X POST "$BASE/api/annotate/scan?days=1"

# 用例树（含两个协议任务、子任务状态与候选数）
curl -s "$BASE/api/annotate/cases"

# 某子任务的候选与已采纳
curl -s "$BASE/api/annotate/case/16/a2a-t/slot_errors"

# 采纳 / 取消采纳（observationId 来自上一步；editedText 为关键词或改写后的报文）
curl -s -X POST "$BASE/api/annotate/case/16/a2a-t/slot_errors/choice" \
  -H 'content-type: application/json' \
  -d '{"observationId": 12, "accepted": true, "editedText": "gain, 增益"}'

# 拦截匹配模式
curl -s -X POST "$BASE/api/annotate/case/16/a2a-t/slot_errors/mode" \
  -H 'content-type: application/json' -d '{"mode": "any"}'

# 备注 / 确认
curl -s -X POST "$BASE/api/annotate/case/16/a2a-t/slot_errors/note" \
  -H 'content-type: application/json' -d '{"note": "依据 11-15 三批 live"}'
curl -s -X POST "$BASE/api/annotate/case/16/a2a-t/slot_errors/confirm" \
  -H 'content-type: application/json' -d '{}'

# 导出单用例片段 / 全量
curl -s "$BASE/api/annotate/export/case/16"
curl -s "$BASE/api/annotate/export.yaml" -o osp-annotations.yaml
curl -s "$BASE/api/annotate/export.json" -o osp-annotations.json
```

只采纳 `consistent: true` 的观测；`false` 的只作为证据，不可写进期望。

---

## 8. 故障对照

| 现象 | 原因 | 处理 |
|---|---|---|
| `EADDRINUSE` / 端口占用 | 端口被占 | 换 `--port`；不要 kill 现场进程 |
| 页面能开但"全部轮次（0）" | 时间窗内没有该协议/子任务的观测 | 调大时间窗、确认现场确实跑过该用例该协议；先点「采集候选」 |
| 只有拦截候选、没有报文候选 | 缺少 `client-state.sqlite3`（或 WAL 无 `-shm` 打不开） | 看启动日志 WARNING；确认现场实例在运行；必要时改用源目录副本 |
| `扫描 … 已达批次上限` | 批次多于上限（默认 500） | 缩小时间窗，或调大上限后再采 |
| `store: false`（/ready 503） | `--store` 目录不可写 | 换目录/修权限后重启 |
| 片段里写"没有已确认的期望" | 该用例还没有确认任何子任务 | 先采纳并「确认」 |
| 合并后契约测试失败 | 键放错层级 / 给非 blocked 用例加了 `slot_errors` / 多轮没嵌套 | 按第 5.2 节修正 |

---

## 9. 附：现场实例数据的时间戳口径

- 时间窗按批次文件的**完成时间**（`finishedAt`，缺失时用 `createdAt`）过滤，默认最近 1 天。
- 批次轮次（`item.round`）与协商轮次（`negotiation_events.round`）是两个概念：前者是整批重复执行的第几遍，
  后者是对话的第几轮；页面上分别对应「运行轮次」下拉与「第 N 轮」分组。
