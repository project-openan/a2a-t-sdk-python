# Incident订阅 Prompt 评测设计

**目标：** 为客户端 SDK 的 `subscribe_incident` prompt 生成能力构建一套大规模 `zh-CN` 评测集，并提供可执行脚本，对场景识别、槽位命中和 prompt 效果进行统计。

**范围：** 本设计只覆盖当前内置场景 `subscribe_incident`。输入以中文为主，少量保留领域英文术语，例如 `Incident`、`critical`、`major`、`DataPart`。

## 背景

当前 SDK 只内置了一个场景：`subscribe_incident`。仓库里已经有一些成功生成、未识别输入、资源缺失和生成后校验失败的测试资产，但这些资产主要偏向流程校验，还不能充分衡量 prompt 生成效果本身。

这次新增评测集需要重点覆盖两类问题：

1. 场景识别错误
2. 场景识别正确，但槽位抽取或渲染漏掉核心内容

## 评测目标

每条用例同时从两层进行评估：

1. **结构化期望**
   - 输入是否应被识别为 `subscribe_incident`
   - 哪些语义槽位必须被体现
   - 哪些字段允许缺失且不算识别失败
   - 当不应成功生成时，对应的失败类型是什么
2. **Prompt效果期望**
   - 最终 prompt 不要求逐字匹配
   - 核心语义和关键点必须保留
   - 轻微措辞差异是允许的

## 数据集规模与构成

首批数据集规模固定为 **160 条**。

### 用例类型分布

- `positive_complete`: 50
- `positive_partial`: 30
- `positive_recognized_but_slot_risky`: 25
- `negative_near_intent`: 20
- `negative_non_incident_subscription`: 20
- `negative_ambiguous`: 15

### 语义变体标签

每条用例至少带一个或多个以下标签：

- `topic_expression`
- `condition_fault_name`
- `condition_severity`
- `condition_combination`
- `report_format`
- `subscription_target_context`
- `noise_interference`
- `instruction_purity`

### 完整度标签

- `L1`：只明确了订阅对象或主题
- `L2`：明确了订阅和部分条件
- `L3`：明确了订阅、条件和上报格式
- `L4`：明确了订阅、多重条件和附加上下文

## 用例结构

每条用例必须包含以下字段：

- `id`
- `input`
- `scenario_description`
- `expected_result`
- `expected_prompt_effect`
- `tags`

### expected_result

- `should_recognize`
- `expected_scenario_code`
- `expected_slots`
- `allowed_missing_slots`
- `failure_type`

### expected_prompt_effect

- `must_include_points`
- `preferred_keywords`
- `must_not_include_points`

### tags

- `case_type`
- `semantic_variant`
- `completeness_level`

## 识别判定规则

### 识别成功

满足以下条件即可算识别成功：

- `should_recognize = true`
- 模型输出结果为成功

### 当前 SDK 限制

当前客户端 API 返回的只有最终渲染后的 prompt body，不返回 front matter，也不返回中间场景识别结果。因此这次评测里的“识别成功”只能通过间接方式推断：

- 正样本生成成功，视为识别成了 `subscribe_incident`
- 负样本生成成功，视为误识别成了 `subscribe_incident`

由于当前 SDK 只内置了这一个场景，这种代理判断在这批评测里是成立的。

### 识别失败类型

- `scenario_false_negative`：本应识别为 `subscribe_incident`，但生成没有成功
- `scenario_false_positive`：本不应识别，但生成成功了

## 槽位与内容判定规则

当前场景主要按三类语义槽位评估：

- `通知主题`
- `订阅条件`
- `上报通知数据格式`

因为公共生成接口只返回最终 prompt，所以这里的槽位准确率本质上是对最终 prompt 语义内容的检查，而不是直接读取模型的中间抽槽结果。

### 槽位结果等级

- `slot_exact`：所有期望语义都被体现
- `slot_partial`：场景识别正确，但次要或可选内容有缺失
- `slot_wrong`：场景识别正确，但必需语义缺失或错误

### Prompt效果等级

- `prompt_good`：所有必需点都出现，且没有禁止语义
- `prompt_acceptable`：核心点基本齐全，但存在轻微措辞偏差或次要遗漏
- `prompt_bad`：关键点缺失，或出现错误语义

## 负样本策略

应当识别失败的用例主要来自两类：

1. 输入与目标场景很接近，但本质上不是在请求生成订阅任务
   - 解释
   - 翻译
   - 改写
   - 总结
   - 文案检查
2. 输入确实是“订阅”意图，但订阅目标不是 `subscribe_incident`
   - 其他通知主题
   - 泛化的告警订阅，但没有落到 incident
   - 状态、性能、日志等非 incident 对象

另外单独加入“模糊输入”类样本，用于测模型是否会过度猜测。

## 正样本策略

正样本采用混合设计：

- 一部分信息完整
- 一部分缺少上报格式
- 一部分只有 incident 主题和部分条件
- 一部分句子较长、噪声较多，但主意图仍然明确

这样可以分别统计：

- 场景识别准确率
- 识别成功但内容不完整的比例
- prompt 关键点命中率

## 样本生成规则

为了避免样本只是模板换词，数据集必须同时在词汇和句式两层做变化。

### 句式类型

- 直接指令
- 任务式描述
- 需求说明式
- 口语请求式
- 背景说明 + 请求
- 请求中夹带附加要求

### 正样本变化维度

- `Incident` 的不同主题表达
- 故障名称表达差异
- 级别表达差异
- 多个条件的排列顺序差异
- 上报格式的不同描述方式
- 订阅对象上下文，例如基站设备、网元、站点

### 负样本变化维度

- 纯解释/翻译类任务
- 订阅非 incident 主题
- 非常短的模糊指令
- 多意图混合但主意图不清

## 统计指标

Runner 至少要输出以下指标：

- 总体场景识别准确率
- 正样本识别准确率
- 负样本误识别率
- 已识别正样本中的槽位语义准确率
- 已识别正样本中的 prompt 核心点命中率
- 按 `case_type` 统计的准确率
- 按 `semantic_variant` 统计的准确率
- 按 `completeness_level` 统计的准确率

## 交付物

实现完成后应产出：

1. 一份机器可读的用例文件
2. 一份便于人工 review 的 Markdown 用例预览
3. 一份可执行的评测脚本
4. 一份包含汇总指标和失败样例的评测报告

## 约束

- 不做逐字级 prompt golden matching
- 优先最小实现，不做过度框架设计
- 新脚本不要求补充 UT
- 所有文件都不需要提交
