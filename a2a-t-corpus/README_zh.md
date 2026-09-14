# a2a-t-corpus — A2A-T 准确性验证语料

A2A-T SDK 的数据驱动准确性验证：把 JSON 工作流用例经生产 SDK 装配链路对**真实 LLM** 执行，产出逐步 API/LLM 完整转录、时延与 Token 指标，并以 95%+ 准确率达标线支撑提示词调优。

当前实现扩展：**Task-T**（专线投诉诊断）与 **Negotiation-T**（无线节能目标/信息/可行性协商），均为标杆迭代；Notification-T / Authorization-T 待参考场景稳定后按同结构扩展（套件模块 + `resources/` + 注册表条目，零分叉）。

- **纯测试模块**：位于 `a2a-t-corpus/`，不进 `a2a_t` wheel，CI 以 `-m 'not live'` 排除。
- **必须配置真实 LLM**：缺少三个必填 `A2AT_LLM_*` 配置时 fail-fast 并给出配置指引。`.env` 键名与项目根 `env.example` 保持一致，corpus 模板即根模板的精调子集。结构自守卫（无 LLM）仍进 CI。
- **用例 JSON 是 Java/Python 双边契约**：语料文件与 `schemas/` 两个 Schema 与 Java 仓库同源复用；`tools/` 下是两个仓库共用的同一套 Python 工具。

## 目录

```
a2a-t-corpus/
├── env.example / .env（密钥，gitignore）/ README.md / README_zh.md
├── schemas/          input-case.schema.json、output-result.schema.json（格式一手定义）
├── tools/            inputCsvToJson(.py)、outputJsonToCsv(.py)、csv-templates/
├── conftest.py       pytest 选项、运行时 fixture、用例参数化钩子
├── engine/           框架核心：config/constants/loader/discover/registry/recorder/
│                     from_step/errors_serializer/assertion/engine/assembler/
│                     client_apis/server_apis/negotiation_apis/report/suite
└── suites/           各扩展的准确性验证套件
    ├── task/         test_task_t_from_text_workflow、test_task_t_from_data_workflow、
    │   │             test_self_guard
    │   └── resources/<场景>/   input_case_from_text.json 与 input_case_from_data.json
    │                          （用例设计，人工构造）；output_result_*.json 由引擎每次运行写回
    └── negotiation/  test_negotiation_t_from_text_workflow、
        │             test_negotiation_t_from_data_workflow、test_negotiation_self_guard
        └── resources/<场景>/   ran-energy-saving-target-negotiation、
                                ran-energy-saving-information-negotiation、
                                ran-energy-saving-feasibility-negotiation
```

## 快速开始

```bash
cp a2a-t-corpus/env.example a2a-t-corpus/.env   # 填写 A2AT_LLM_BASE_URL / API_KEY / MODEL
uv run pytest a2a-t-corpus/suites/task/test_task_t_from_text_workflow.py -m live
uv run pytest a2a-t-corpus/suites/task -m live --corpus-scenario=private-line-complaint
uv run pytest a2a-t-corpus/suites/negotiation -m live --corpus-scenario='ran-energy-saving-*'
```

- `--corpus-scenario`：场景名 glob（逗号分隔）；`--case-filter`：用例 id glob。测试 id 形如 `<场景名>/<id>`，`-k` 亦可直达单条用例。
- 每条已执行用例完成即打印到控制台；失败/崩溃用例同时打印完整 interaction 轨迹；转录文件只反映本次执行（过滤运行会以命中的用例整体覆盖文件）。
- `--corpus-output-dir=<dir>`：把输出重定向到指定目录（缺省转录写回场景目录；summary 默认落 `a2a-t-corpus/.corpus/`）。
- 无 LLM 时先跑结构自守卫（此测试进 CI）：
  `uv run pytest a2a-t-corpus/suites/task/test_task_self_guard.py a2a-t-corpus/suites/negotiation/test_negotiation_self_guard.py`。

## 用例 JSON 契约（v1，一手定义见 `schemas/`）

```json
{
  "id": "TC00000001", "caseDimension": "01-意图清晰参数完整", "caseDesc": "标准专线业务中断投诉",
  "caseCategory": "正常",
  "input": [
    { "api": "generateTaskPromptFromText",
      "args": { "text": "发生专线业务中断，接入端口名称为P781-……", "templateUri": "Task-T/network-layer/private-line-complaint/v1" } },
    { "api": "validateTaskPromptAndDataFilling",
      "args": { "schema": { "…": "JSON Schema" }, "templateUri": "…", "promptText": { "$fromStep": 1, "$field": "promptText" } } }
  ],
  "expected": [
    { "result": "success", "data": {}, "error": { "errCode": "", "errMessage": "" } },
    { "result": "success", "data": { "accessPort": "P781-……" }, "error": { "errCode": "", "errMessage": "" } }
  ]
}
```

- `id` 使用 `TC`（TestCase）前缀，如 `TC00000001`；id 在单个用例文件内唯一。
- `input` / `expected` 为**等长数组**，顺序即执行序；步数不限，同一 API 可多次出现。
- `expected.result`：`success` / `error`；`data` 为子集断言（可选 `dataExact` 收紧为全量相等）；`error.errCode` 必须位于 SDK 错误码目录（`ErrorCatalog`）。
- `{"$fromStep": N, "$field": "promptText"}` 按步序（1-based）引用更早步骤响应中的字段；显式字面值优先。
- `caseCategory` 为开放枚举：`正常` / `异常` / `边界` / `模糊语义` …（仅作统计与组织维度，断言与之间无耦合）。
- 加载为严格解析：未知键、缺期望、数组不等长、api 未注册、errCode 不在目录、`$fromStep` 非法 → 加载期汇总报错。
- 业务载荷（输入文本、schema 描述、期望槽位值）与用例元数据保留中文；其余（工具、消息、代码）一律英文。
- `api` 字段为 Java 门面名（camelCase，与 Java 仓库对齐），Python 侧由注册表映射到 snake_case 实现方法。

## 输出与指标

- 每场景：`output_result_from_text.json` / `output_result_from_data.json`，每个用例一条记录，其 interactions 交错记录 **API 步与其底层 LLM 调用**，携带完整请求/响应（异常步 dump 每个属性 + cause 链）、时延、Token——**完整输出，不截断**。
- `a2a-t-corpus/.corpus/summary_report_<flow>.json` + 控制台表：总体/按场景/按维度/按分类/按 API 步的准确率（**95% 达标线标注**）、时延 p50/p95、Token 合计。

## 工具闭环

```bash
python a2a-t-corpus/tools/inputCsvToJson.py --template --out my-cases.csv   # 用例设计表（含一行示例）
# 填表后转换（默认开启产物结构校验）
python a2a-t-corpus/tools/inputCsvToJson.py --csv my-cases.csv \
    --out a2a-t-corpus/suites/task/resources/<场景>/input_case_from_text.json
# 跑套件后审视（一行=一个用例，含每步完整请求/响应）
python a2a-t-corpus/tools/outputJsonToCsv.py \
    --json a2a-t-corpus/suites/task/resources/<场景>/output_result_from_text.json --out review.csv
# 可选：把 input JSON 回填为设计表
python a2a-t-corpus/tools/inputCsvToJson.py --reverse --csv <input_case_from_text.json> --out back.csv
```

## 新增场景（零 Python 改动）

新建 `suites/<扩展>/resources/<场景>/` 并放入两个 input JSON 即可——套件收集期扫描发现场景。结构门禁先用对应扩展的 `test_*_self_guard.py`（无 LLM）跑一遍。

## 后续扩展（参考场景稳定后）

Notification-T / Authorization-T：新增 `suites/<扩展>/` 套件与对应 `resources/`，在 `engine/assembler.py` 登记该扩展的生成/校验 API，框架与工具链原样复用。

## 配置同源说明

corpus 复用项目根 `env.example` 的 `A2AT_LLM_*` 键族（provider/base_url/api_key/model/temperature/timeout_seconds/max_attempts/max_tokens），corpus 模板即精调子集，多余键忽略；装载优先级为**环境变量 > `a2a-t-corpus/.env`**。corpus 装配直接构造 `A2ATConfig`/`LLMClientConfig`，从不触碰生产 `.env` 装载路径。
