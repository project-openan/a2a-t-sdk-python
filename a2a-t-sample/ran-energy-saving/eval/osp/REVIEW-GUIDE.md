# 验收审核实例 · 启动与操作指南（黄区可执行）

本文件是 `/review` 的运行手册：如何在不重启现场 live 实例的前提下，用只读进程对已落盘的
tester 证据做**当前语料重判**、给出验收结论并导出报告。

配套说明见 `eval/osp/README.md` 第 13 节；期望值的维护见 `eval/osp/ANNOTATE-GUIDE.md`。

---

## 1. 铁律

1. **不要**把完整 Web UI（`run_demo.py` / `demo.web.serve`）指向现场 `runs/`：完整应用启动会执行
   `RunManager.recover_runs()`，把未结束的 run 标成 `interrupted`。
2. 审核实例只读源目录：`runs/tester/*.json` 与 `runs/client-state.sqlite3`。
3. 唯一允许写的文件是审核库（`--store`）。**显式放到源目录之外**。启用 `--fetch-traces` 后还会写
   轨迹快照（默认 `<runs>/review/langfuse`，可用 `--trace-store` 指到源目录之外）。
4. 不启动 run、不下发任务、不恢复 run 状态；复跑失败用例去 `/tester`。
5. 不要删除审核库：结论、审核人、证据 runId、用例指纹都在里面。
6. 用例 refresh 只会替换 `eval/osp/cases`；手写期望放在 overlay
   （`eval/osp/expectations-overlay.yaml` 或 `--overlay` 指定的文件），不要合并进用例 YAML。

---

## 2. 前置检查

```bash
cd <sample>/a2a-t-sample/ran-energy-saving     # 以现场实际路径为准
export PYTHONPATH=.
ls eval/osp/cases/*.yaml | wc -l                 # 期望 100
ls -d <现场 runs 目录>/tester
ls -l <现场 runs 目录>/client-state.sqlite3      # 缺失时协商报文证据降级为 partial
ls -l <现场 runs 目录>/annotations/annotations.sqlite3  # 标注库；审核默认直接读它
ls -l eval/osp/expectations-overlay.yaml        # 可选；有则会被 catalog 合并（标注库优先）
python -c "import uvicorn, starlette; print('ok')"
```

---

## 3. 启动（三步）

### 3.1 先验证指向

```bash
python -m demo.web.review \
  --runs-dir <现场 runs 目录> \
  --store  <审核库目录>/review.sqlite3 \
  --check
```

输出应包含 `source batches` / `source run store` / `review store` / `annotations` / `case corpus` /
`expectation overlay`。`WARNING: the client store is missing` 表示协商报文只能依赖批次内已存证据
（旧批次的报文会标 partial）。`annotations` 指向标注库；它不存在时只是没有手写期望，不是错误。

### 3.2 后台启动（推荐 8091，与 Web UI 8080、标注 8090 不冲突）

```bash
mkdir -p <审核库目录>
nohup python -m demo.web.review \
  --runs-dir <现场 runs 目录> \
  --store  <审核库目录>/review.sqlite3 \
  [--annotations <标注库路径>] \
  [--overlay <稳定的 overlay 路径>] \
  [--fetch-traces] \
  [--trace-store <轨迹快照目录>] \
  --host 127.0.0.1 --port 8091 \
  > <审核库目录>/review.log 2>&1 &
echo $! > <审核库目录>/review.pid
```

### 3.3 健康检查

```bash
curl -s http://127.0.0.1:8091/health
# {"status":"ok","service":"ran-energy-saving-review"}
curl -s http://127.0.0.1:8091/ready
# {"status":"ready","checks":{"cases":true,"store":true}}
```

打开 `http://127.0.0.1:8091/review`（`/` 会跳转过去）。

---

## 4. 页面操作

1. **采集证据**：顶栏选窗口（默认 7 天），点「采集证据」。它会先 reload 语料和标注库，再扫描批次；
   提示给出批次数、证据条数、新增数、跳过原因与是否达到批次上限。标注页确认的值不需要导出，这里
   直接生效。
2. **筛选**：左栏按编号/名称、协议、自动判定、审核结论、缺陷归属；可只看失败/部分/需重跑，或只看未审核。
3. **选用例与协议**：每个用例有两行（A2A-T / A2A），徽标显示失败维度和结论状态。
4. **看判定**：
   - 运行轮次下拉默认选**最新一条真机 + live**；「全部轮次」显示每轮各维度状态与 `k/n 通过`。
   - **最终意图**是硬门槛：表格并列用例预期 / 服务端最终 / SDK 抽取；默认只看差异。纯 A2A 直连
     厂商时同样比较服务端读数（代理/模拟器仍「仅记录」）。单元格超过 180 字符会折叠，点击摘要
     展开全文，长报文不会被截断丢失。
   - **SDK Task-T** 分节显示；「拦截」显示 slot_errors 命中；「协商」显示逐轮报文与备选。
   - 若显示「需重跑」，说明证据来自另一个输入，不能作为当前用例的判定。
5. **给结论**：选择 通过 / 缺陷（归属 + 备注）/ 已知问题（备注）/ 待复核 / 重置，点一次「保存结论」。
   自动判定不是通过时，确认通过必须写理由；自动判定通过却判缺陷也要写理由。
6. **过期提示**：结论上方出现「用例或期望已变更」或「有新轮次」时，重新复核后再保存。

### 4.1 轨迹（Langfuse）

轨迹是客户硬门槛。判定规则：**运行有 `traceId` 就审计**，不再要求批次声明 `nms/nmsSim`；只有显式
模拟器批次、SDK-only/preflight（无 `runId`）、没有或格式错误的 `traceId` 才「不适用」，并且详情页会
写出原因和下一步。**未采集**的轨迹判「缺证据」（partial，避免整批提前变红）；采集到快照后若轨迹点
或帧数不符才判「失败」。

- **抓取开关**：完整 Web UI（8080）默认开启；独立审核实例默认关闭，启动时加 `--fetch-traces`。
- **凭据与端点**：沿用 sample 自己的上报配置（`shared.config.load_config`）——`LANGFUSE_PUBLIC_KEY`
  / `LANGFUSE_SECRET_KEY` / `LANGFUSE_BASE_URL`（`A2AT_SAMPLE_LANGFUSE_ENABLED=0` 视为未配置）。
  `LANGFUSE_BASE_URL` 就是访问 `us.cloud.langfuse.com` 的本地代理地址，抓取请求走同一个端点；
  进程环境优先于 `.env`。审核实例按 `<runs>/.env` → `<runs>/../.env` 的顺序查找；完整 Web UI
  直接把已加载的配置传给审核页面。注意 `langfuse_export` 自己的 `LANGFUSE_HOST` 不再使用。
- **自动加载**：打开某一轮时，如果没有快照就自动抓一次；已有快照**永久缓存**，不会自动重抓。
- **手动操作**：按钮随状态显示「抓取轨迹」/「重试抓取」/「重新抓取（强制刷新）」。只读实例会提示
  启动命令；未配置凭据会提示缺哪个环境变量。
- **按轮抓取**：请求带 `traceId`/`runId`/`batchId`/`round`，多轮批次抓的是当前查看的轮。
- **限流**：同一进程一次只抓一个 trace；Langfuse 返回 429 后进入 60 秒冷却，页面提示剩余秒数。
  自动加载每个 run 每次会话只尝试一次，失败后由人工点「重试抓取」。
- **快照位置**：默认 `<runs>/review/langfuse/<traceId>_<时间>/`，可用 `--trace-store` 改到源目录之外。
  客户验收可直接拷走该目录。
- **页面查看**：判定汇总卡片里有 `traceId`、快照时间、NMS 帧数、归因；展开「轨迹观测」看每一条
  span；有 `langfuseUrl` 时提供「在 Langfuse 打开」链接。
- **常见原因**：`trace_not_exported` 未导出；`trace_nms_hop_unknown` 轨迹里没有
  `a2a-t-server` 的发送跳（网管侧未上报或 scope 不匹配）；`trace_missing_*` 预期轨迹点缺失，
  按本机/入库/厂商归因区分。

### 4.2 按 traceId / runId 复核

现场回报通常直接给一个 run id 或 Langfuse trace id。切到**轨迹视图**，把 id 粘贴进左栏即可，不必先
知道它属于哪个用例、哪个批次。

- **输入**：每行一个，或逗号/空格分隔；可以直接粘贴整段日志、`traceId=...` 行或 Langfuse URL。
  一次最多 200 个 id，超出会提示丢弃数量。`traceId` 与 `runId` 在本样例中通常相同，但一个
  presentation / correction-chain trace 可以对应多个 run，列表会按 run 展开。
- **解析顺序**：审核库 → tester 批次文件（不受 2 天窗口限制）→ 客户端库 → 已缓存轨迹快照。
  tester 批次命中的证据会**写入审核库**（与「采集证据」同一写语义），之后导出和结论都指向同一条证据。
- **判定**：能拿到 tester 阶段证据的 run 走完整六维判定；只有客户端运行记录的显示「仅运行」；
  只有轨迹快照的显示「仅轨迹」；三者都不会被伪造成通过或失败。
- **只读结论**：轨迹视图显示该用例×协议已有的审核结论，但不能在这里保存；点「到用例视图给结论」
  跳到原用例（并定位到该 run）。
- **批量抓取**：表格上方「抓取缺失轨迹」按 trace 串行抓取，遇 429 停止并显示已抓条数；快照仍为
  永久缓存，不会自动重抓。
- **导出**：轨迹视图导出 `osp-review-traces/v1`（JSON/YAML），只含这批 run，不要与完整的
  `osp-review/v1` 验收文件混用。
- **深链**：`/review?trace=<id1>,<id2>` 或重复 `?trace=` 参数会自动切到轨迹视图并解析，便于现场回报。

---

## 5. 导出与回传

| 出口 | 内容 |
|---|---|
| `/api/review/export.yaml` / `.json` | `osp-review/v1`：每个协议任务的自动判定、维度、人工结论与证据 |
| `/api/review/report.md` | 验收报告：结论汇总、用例矩阵、缺陷清单（按归属）、已认可偏差、待审核、已知问题/待复核 |

回传蓝区时随附：报告 Markdown、JSON、语料版本、证据窗口、未覆盖/需重跑的用例清单。

---

## 6. HTTP API（Agent 自动化）

```bash
BASE=http://127.0.0.1:8091

# 采集
curl -s -X POST "$BASE/api/review/scan?days=7"

# 用例 × 协议判定
curl -s "$BASE/api/review/cases"

# 单任务的全部轮次与结论
curl -s "$BASE/api/review/case/16/a2a-t"

# 抓取某一轮的 Langfuse 轨迹（需要 --fetch-traces；force=true 强制刷新）
curl -s -X POST "$BASE/api/review/case/16/a2a-t/trace" \
  -H 'content-type: application/json' \
  -d '{"traceId": "<32 位 trace id>", "round": 1, "force": false}'

# 保存结论（status: accepted|defect|known_issue|recheck|pending）
curl -s -X POST "$BASE/api/review/case/16/a2a-t/decision" \
  -H 'content-type: application/json' \
  -d '{"status": "defect", "category": "sdk", "note": "Task-T 丢失站点列表"}'

# 按 traceId / runId 列表复核（不是 GET：一次可能解析并写入多条证据）
curl -s -X POST "$BASE/api/review/traces" \
  -H 'content-type: application/json' \
  -d '{"ids": ["<traceId 或 runId>", "<另一个>"]}'

# 单条轨迹/运行详情（有证据时与 case detail 同构）
curl -s "$BASE/api/review/trace/<traceId>?runId=<runId>"

# 按 traceId 抓取轨迹（不需要先有用例证据）
curl -s -X POST "$BASE/api/review/trace/<traceId>/fetch" \
  -H 'content-type: application/json' \
  -d '{"runId": "<runId>", "force": false}'

# 导出这批 run（format: json|yaml，schema 为 osp-review-traces/v1）
curl -s -X POST "$BASE/api/review/traces/export" \
  -H 'content-type: application/json' \
  -d '{"ids": ["<traceId 或 runId>"], "format": "yaml"}' -o osp-review-traces.yaml

# 导出
curl -s "$BASE/api/review/export.yaml" -o osp-review.yaml
curl -s "$BASE/api/review/report.md" -o osp-review-report.md
```

---

## 7. 故障对照

| 现象 | 原因 | 处理 |
|---|---|---|
| 端口占用 | 8091 被占 | 换端口；不要 kill 现场进程 |
| 协议任务全是「未跑」 | 窗口内没有该用例证据 | 调大窗口、确认现场确实跑过；先采集 |
| 协商显示「缺证据」/ partial | 旧批次未存报文且 client store 缺失 | 确认 `--client-db` 指向现场 `client-state.sqlite3`，或重跑该用例 |
| 意图显示「仅记录」 | 模拟器/OSS 代理/无直连 payload；直连厂商的纯 A2A 会正常判定 | 代理或模拟器到真机 + live 重跑后再验收 |
| 显示「需重跑」 | 证据的 `refs.userInput` 与当前有效输入不同 | 用例输入已变更，重跑该用例 |
| 结论保存被拒 | 缺陷缺归属/备注，或覆盖自动判定未写理由 | 按提示补全 |
| 结论显示「待复核」 | 用例/期望变更或出现新轮次 | 复核后重新保存 |
| 轨迹显示「轨迹抓取未启用」 | 独立实例没加 `--fetch-traces` | 启动命令加 `--fetch-traces`；或在 8080 的完整 Web UI 里看 |
| 轨迹抓取报「未配置 Langfuse 凭据」 | 环境/`.env` 里缺 `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`，或 `A2AT_SAMPLE_LANGFUSE_ENABLED=0` | 配好凭据后重启；确认 `.env` 在 runs 目录或其上一级，`LANGFUSE_BASE_URL` 指向可达的本地代理 |
| 轨迹显示「不适用」 | 显式模拟器、无 `runId`、无/非法 `traceId` | 详情页 blocker 会写出具体原因；真机用例请重跑或检查 tester 是否记录了 `traceId` |
| 轨迹显示「NMS 跳未识别」 | 轨迹里没有 `a2a-t-server` 的发送 span | 确认网管侧是否上报 Langfuse，或 scope 名是否变化 |
| 轨迹抓取报 429 | Langfuse 限流 | 等页面提示的冷却秒数后点「重试抓取」；不要连续刷新 |
| 轨迹视图显示「未找到」 | 审核库、tester 批次和客户端库都没有该 id | 确认 id 是否完整；现场 tester 文件可能已轮转，改走客户端库或直接抓轨迹 |
| 轨迹视图显示「仅基线/重试」 | 该 runId 只出现在 Modify 的 baseline 或 attempts 里 | 该 run 没有独立的 tester 阶段证据；到 Run 控制台看运行，或抓它的轨迹 |
| 轨迹视图的结论不能保存 | 这是设计：结论按用例×协议保存 | 点「到用例视图给结论」，页面会定位到这条 run 所在用例 |

---

## 8. 与 tester / annotate 的分工

- `/tester`：发起批次、看单轮细节、复跑；审核实例不替代它。
- `/annotate`：维护 `slot_errors` / `messages`，导出 overlay。
- `/review`：跨批次、当前语料的自动判定 + 人工验收结论 + 报告；不启动 run，不写用例。
