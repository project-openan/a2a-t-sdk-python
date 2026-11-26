# OSP 用例与测试台

`eval/osp` 是 `/tester` 的用例层：用例语料、期望、判定与报告。它不包含独立控制台；标注、审核、
协议对比和 observe 控制台不在交付范围内。

## 目录

| 路径 | 内容 |
|---|---|
| `cases/*.yaml` | 用例语料（输入、期望、runner 配置） |
| `index.yaml` | 用例索引与分组 |
| `catalog.py` | 加载用例、按协议投影 |
| `expectations.py` | 期望解析与断言匹配 |
| `compare.py` | Task-T / 意图逐字段对比 |
| `conditions.py` | 客户端条件 |
| `principles.py` / `negotiation-principles.yaml` | 冻结的通用协商原则 |
| `runner.py` | SDK 阶段驱动 |
| `intent_judge.py` / `client_quality.py` | 独立的意图/决策质量判定 |
| `detail.py` / `followup.py` / `datapoints.py` | 报告明细 |

## 用例字段（摘要）

```yaml
id: 12
name: 缺速率可协商
user_input: "..."
runner:
  scenario: night
  pre_send_validation: always
expect:
  intent: { ... }
  protocols:
    a2a-t:
      task_t: { ... }
      negotiation: { expected: true, expected_rounds: 1, kinds: [information] }
      final_state: task_completed
assert:
  a2a-t: { task_t: true, negotiation: true }
```

- 用例的 Q/P 列是**期望参考**，不是回放脚本；客户端始终使用 live 决策 agent。
- 真机 run 的 accuracy 只比较终态与报告是否存在；演示录制序列不参与评分。
- 拦截命中时，原始请求按 record-only 记录，不继续下发。

## 运行

在 Web UI 打开 `/tester`，选择用例与协议后启动批次；或使用批次 API：

```bash
curl -X POST http://127.0.0.1:8080/api/tester/batches \
  -H 'content-type: application/json' \
  -d '{"caseIds":[1,2],"protocols":["a2a-t"],"stopAfter":"accepted"}'
```

批次完成后导出 xlsx 报告；报告内的 wire 证据必须逐帧原文。

## 维护

- 新增/修改用例后，跑一遍离线单元测试与一次 live 探针，确认期望与模型行为一致。
- 不写死运维原文之外的业务值；Task-T 只来自 operator 原文。
- `expectations-overlay.yaml` 的本地覆盖不进入交付仓库。
