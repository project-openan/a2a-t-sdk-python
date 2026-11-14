# Langfuse 导出：support-agent

| 字段 | 值 |
| --- | --- |
| traceId | `9f2c1a7de4b34c0e8a5f2d1c6b9e3a70` |
| name | support-agent |
| sessionId | `sess_3f9a1c` |
| userId | user_88213 |
| tags | `prod`, `rag`, `support` |
| release | v1.42.0 |
| environment | production |
| 时间范围 | 2026-11-06T22:14:02.090Z → 2026-11-06T22:14:05.520Z |
| 耗时 | 3.430s |
| 成本 | $0.0041 |
| Token | 输入 1,193 / 输出 312 / 合计 1,505 |
| Observation | 11 条 |
| Scores | 3 条 |
| 导出时间 | 2026-11-07T10:20:11+08:00 |
| host | `https://cloud.langfuse.com`（source: default, region: EU） |
| 网络 | 直连（默认） · TLS 校验=开启 |
| API | v4（backend: v4） |
| 导出状态 | complete |

## 调用树

```text
└── support-agent [AGENT] 3.420s DEFAULT (root)
    ├── trace opened [EVENT] 0.000s DEFAULT
    ├── input-guard [CHAIN] 0.041s DEFAULT
    │   └── moderation [GUARDRAIL] 0.031s DEFAULT
    ├── rewrite [CHAIN] 0.202s DEFAULT
    ├── plan [CHAIN] 0.510s DEFAULT
    │   └── retrieve-docs [RETRIEVER] 0.311s DEFAULT (5 docs)
    ├── answer-generation [GENERATION] gpt-4.1-mini 1.200s 1,193/312 tok $0.0041 WARNING
    ├── call-tool [TOOL] 0.044s DEFAULT
    ├── rerank [SPAN] 0.122s DEFAULT
    └── upstream error [EVENT] 0.010s ERROR
```

## Observation 详情

### 01 [EVENT] trace opened

- id: `obs_a10000000000000000000010`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:02.090Z → 2026-11-06T22:14:02.090Z（0.000s）
- level: DEFAULT

---

### 02 [AGENT] support-agent

- id: `obs_a10000000000000000000001`
- 父节点: （无，root）
- 时间: 2026-11-06T22:14:02.100Z → 2026-11-06T22:14:05.520Z（3.420s）
- level: DEFAULT

**Input**

```json
{
  "question": "如何修改发货地址？"
}
```

**Output**

```json
{
  "answer": "在订单详情页点击“修改地址”。"
}
```

**Metadata**

```json
{
  "channel": "web"
}
```

---

### 03 [CHAIN] input-guard

- id: `obs_a10000000000000000000002`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:02.120Z → 2026-11-06T22:14:02.161Z（0.041s）
- level: DEFAULT

**Input**

```json
{
  "raw": "帮我改地址"
}
```

**Output**

```json
{
  "ok": true
}
```

---

### 04 [GUARDRAIL] moderation

- id: `obs_a10000000000000000000003`
- 父节点: `obs_a10000000000000000000002（input-guard）`
- 时间: 2026-11-06T22:14:02.130Z → 2026-11-06T22:14:02.161Z（0.031s）
- level: DEFAULT

**Output**

```json
{
  "flagged": false
}
```

---

### 05 [CHAIN] rewrite

- id: `obs_a10000000000000000000004`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:02.170Z → 2026-11-06T22:14:02.372Z（0.202s）
- level: DEFAULT

**Output**

```json
{
  "rewritten": "如何修改订单收货地址？"
}
```

---

### 06 [CHAIN] plan

- id: `obs_a10000000000000000000005`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:02.380Z → 2026-11-06T22:14:02.890Z（0.510s）
- level: DEFAULT

**Output**

```json
{
  "steps": [
    "retrieve",
    "answer"
  ]
}
```

---

### 07 [RETRIEVER] retrieve-docs

- id: `obs_a10000000000000000000006`
- 父节点: `obs_a10000000000000000000005（plan）`
- 时间: 2026-11-06T22:14:02.400Z → 2026-11-06T22:14:02.711Z（0.311s）
- level: DEFAULT

**Input**

```json
{
  "query": "修改收货地址"
}
```

**Output**

```json
{
  "documents": [
    {
      "id": "doc_0",
      "score": 0.9
    },
    {
      "id": "doc_1",
      "score": 0.8
    },
    {
      "id": "doc_2",
      "score": 0.7
    },
    {
      "id": "doc_3",
      "score": 0.6
    },
    {
      "id": "doc_4",
      "score": 0.5
    }
  ]
}
```

---

### 08 [GENERATION] answer-generation

- id: `obs_a10000000000000000000007`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:02.900Z → 2026-11-06T22:14:04.100Z（1.200s）
- level: **WARNING** — output length close to limit
- model: `gpt-4.1-mini`（temperature=0.2, top_p=0.9）
- prompt: `rag-answer` v7
- usage: 输入 1,193 / 输出 312 / 合计 1,505 tok，成本 $0.0041

**Input**

```json
{
  "messages": [
    {
      "role": "system",
      "content": "你是客服助手，只根据检索段落回答。"
    },
    {
      "role": "user",
      "content": "如何修改配送地址？"
    },
    {
      "role": "context",
      "content": "RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落。RAG 检索到的原始段落
```

```text
... [truncated: 4,000 of 9,318 chars shown] ...
```

**Output**

```json
{
  "answer": "在订单详情页点击“修改地址”，填入新地址后保存即可。"
}
```

**Metadata**

```json
{
  "retrieved_docs": 5,
  "attachment": "截图说明：xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx@@@langfuseMedia:type=image/png|id=med_7c3a91f2|source=base64_data_uri@@@"
}
```

---

### 09 [TOOL] call-tool

- id: `obs_a10000000000000000000008`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:04.200Z → 2026-11-06T22:14:04.244Z（0.044s）
- level: DEFAULT

**Input**

```json
{
  "tool": "order.lookup",
  "args": {
    "orderId": "A-1001"
  }
}
```

**Output**

```json
{
  "status": "ok"
}
```

---

### 10 [SPAN] rerank

- id: `obs_a10000000000000000000009`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:04.300Z → 2026-11-06T22:14:04.422Z（0.122s）
- level: DEFAULT

**Output**

```json
{
  "top_k": 3
}
```

---

### 11 [EVENT] upstream error

- id: `obs_a10000000000000000000011`
- 父节点: `obs_a10000000000000000000001（support-agent）`
- 时间: 2026-11-06T22:14:05.000Z → 2026-11-06T22:14:05.010Z（0.010s）
- level: **ERROR** — upstream timeout after 3s

## Scores

| name | dataType | value | source | subject | comment |
| --- | --- | --- | --- | --- | --- |
| helpfulness | NUMERIC | 0.86 | EVAL | trace `9f2c1a7de4b34c0e8a5f2d1c6b9e3a70` | 整体有帮助 |
| groundedness | BOOLEAN | true | EVAL | trace `9f2c1a7de4b34c0e8a5f2d1c6b9e3a70` | — |
| needs_review | CATEGORICAL | "no" | ANNOTATION | observation `obs_a10000000000000000000007` | 人工抽检通过 |

## Media

| mediaId | 类型 | 大小 | 本地文件 | URL 过期 | 备注 |
| --- | --- | --- | --- | --- | --- |
| `med_7c3a91f2` | image/png | 18,432 B | `media/med_7c3a91f2.png` | 2026-11-07T10:35:00Z | — |

## Raw data

| 文件 | 说明 |
| --- | --- |
| `trace-summary.json` | 派生概要（非 API 原文） |
| `transcript.md` | 本文件 |
| `raw/observations.jsonl` | 11 行，observations 原始行 |
| `raw/scores.json` | 3 条，scores 原始响应 |
| `media/med_7c3a91f2.png` | 媒体下载结果 |

文件大小与 sha256 见 `manifest.json`。

---

<sub>由 langfuse-export 0.5.1 生成 · 数据时间统一为 UTC</sub>
