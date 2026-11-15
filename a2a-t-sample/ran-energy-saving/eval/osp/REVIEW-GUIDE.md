# 验收审核实例 · 启动与操作指南（黄区可执行）

本文件是 `/review` 的运行手册：如何在不重启现场 live 实例的前提下，用只读进程对已落盘的
tester 证据做**当前语料重判**、给出验收结论并导出报告。

配套说明见 `eval/osp/README.md` 第 13 节；期望值的维护见 `eval/osp/ANNOTATE-GUIDE.md`。

---

## 1. 铁律

1. **不要**把完整 Web UI（`run_demo.py` / `demo.web.serve`）指向现场 `runs/`：完整应用启动会执行
   `RunManager.recover_runs()`，把未结束的 run 标成 `interrupted`。
2. 审核实例只读源目录：`runs/tester/*.json` 与 `runs/client-state.sqlite3`。
3. 唯一允许写的文件是审核库（`--store`）。**显式放到源目录之外**。
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
   - **最终意图**是硬门槛：表格并列用例预期 / 服务端最终 / SDK 抽取；默认只看差异。
   - **SDK Task-T** 分节显示；「拦截」显示 slot_errors 命中；「协商」显示逐轮报文与备选。
   - 若显示「需重跑」，说明证据来自另一个输入，不能作为当前用例的判定。
5. **给结论**：选择 通过 / 缺陷（归属 + 备注）/ 已知问题（备注）/ 待复核 / 重置，点一次「保存结论」。
   自动判定不是通过时，确认通过必须写理由；自动判定通过却判缺陷也要写理由。
6. **过期提示**：结论上方出现「用例或期望已变更」或「有新轮次」时，重新复核后再保存。

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

# 保存结论（status: accepted|defect|known_issue|recheck|pending）
curl -s -X POST "$BASE/api/review/case/16/a2a-t/decision" \
  -H 'content-type: application/json' \
  -d '{"status": "defect", "category": "sdk", "note": "Task-T 丢失站点列表"}'

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
| 意图显示「仅记录」 | 模拟器/纯 A2A 代理/无直连 payload | 到真机 + live 重跑后再验收 |
| 显示「需重跑」 | 证据的 `refs.userInput` 与当前有效输入不同 | 用例输入已变更，重跑该用例 |
| 结论保存被拒 | 缺陷缺归属/备注，或覆盖自动判定未写理由 | 按提示补全 |
| 结论显示「待复核」 | 用例/期望变更或出现新轮次 | 复核后重新保存 |

---

## 8. 与 tester / annotate 的分工

- `/tester`：发起批次、看单轮细节、复跑；审核实例不替代它。
- `/annotate`：维护 `slot_errors` / `messages`，导出 overlay。
- `/review`：跨批次、当前语料的自动判定 + 人工验收结论 + 报告；不启动 run，不写用例。
