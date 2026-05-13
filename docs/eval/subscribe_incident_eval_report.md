# Subscribe Incident Evaluation Report

Generated at: 2026-05-13T06:26:16.449545+00:00

## Summary

- Total cases: 160
- Positive cases: 105
- Negative cases: 55
- Overall recognition accuracy: 0.6625
- Positive-case recognition accuracy: 1.0
- Negative-case false positive rate: 0.9818
- Recognized positive cases: 105
- Slot exact rate within recognized positive cases: 0.8762
- Slot group hit rate within recognized positive cases: 0.9672
- Prompt exact rate within recognized positive cases: 0.8762
- Prompt group hit rate within recognized positive cases: 0.9672

## By Case Type

| Bucket | Count | Recognition | Slot Exact | Slot Hit | Prompt Exact | Prompt Hit |
| --- | --- | --- | --- | --- | --- | --- |
| `negative_ambiguous` | 15 | 0.0 | None | None | None | None |
| `negative_near_intent` | 20 | 0.0 | None | None | None | None |
| `negative_non_incident_subscription` | 20 | 0.05 | None | None | None | None |
| `positive_complete` | 50 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| `positive_partial` | 30 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| `positive_recognized_but_slot_risky` | 25 | 1.0 | 0.48 | 0.9 | 0.48 | 0.9 |

## By Semantic Variant

| Bucket | Count | Recognition | Slot Exact | Slot Hit | Prompt Exact | Prompt Hit |
| --- | --- | --- | --- | --- | --- | --- |
| `condition_combination` | 75 | 1.0 | 0.8267 | 0.9636 | 0.8267 | 0.9636 |
| `condition_fault_name` | 105 | 0.8095 | 0.8471 | 0.9649 | 0.8471 | 0.9649 |
| `condition_severity` | 105 | 0.8095 | 0.8471 | 0.9655 | 0.8471 | 0.9655 |
| `instruction_purity` | 55 | 0.0182 | None | None | None | None |
| `noise_interference` | 40 | 0.625 | 0.48 | 0.9 | 0.48 | 0.9 |
| `report_format` | 75 | 1.0 | 0.8267 | 0.9636 | 0.8267 | 0.9636 |
| `subscription_target_context` | 125 | 0.848 | 0.8762 | 0.9672 | 0.8762 | 0.9672 |
| `topic_expression` | 140 | 0.75 | 0.8762 | 0.9672 | 0.8762 | 0.9672 |

## By Completeness Level

| Bucket | Count | Recognition | Slot Exact | Slot Hit | Prompt Exact | Prompt Hit |
| --- | --- | --- | --- | --- | --- | --- |
| `L1` | 45 | 0.2444 | 1.0 | 1.0 | 1.0 | 1.0 |
| `L2` | 40 | 0.5 | 1.0 | 1.0 | 1.0 | 1.0 |
| `L3` | 33 | 1.0 | 1.0 | 1.0 | 1.0 | 1.0 |
| `L4` | 42 | 1.0 | 0.6905 | 0.9373 | 0.6905 | 0.9373 |

## Sample Failures

### si_pr_003

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['电源模块告警']]`
- Missing prompt groups: `[['电源模块告警']]`
- Forbidden hits: `[]`

**Input**

最近站点设备在晚高峰时段波动明显，前面已经连续出现过光模块故障和电源模块告警，现在我想把故障Incident订阅补上，不是所有故障都要，只看严重和中，另外把风扇故障也纳入条件，用DataPart承载Incident数据进行上报。 如果描述里要带背景可以简短带一句，但不要改成分析任务。 当前是日常值守场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级：严重、中；故障名称：光模块故障、风扇故障（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过DataPart上报Incident数据（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_005

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['主控板异常'], ['端口异常']]`
- Missing prompt groups: `[['主控板异常'], ['端口异常']]`
- Forbidden hits: `[]`

**Input**

最近汇聚节点在晚高峰时段波动明显，前面已经连续出现过主控板异常和端口异常，现在我想把Incident订阅补上，不是所有故障都要，只看严重和major，另外把光纤中断也纳入条件，通过DataPart上报Incident数据。 如果描述里要带背景可以简短带一句，但不要改成分析任务。 当前是日常值守场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级为：严重、major。故障名称为：光纤中断。（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过DataPart上报Incident数据（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_006

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['电源模块告警'], ['风扇故障']]`
- Missing prompt groups: `[['电源模块告警'], ['风扇故障']]`
- Forbidden hits: `[]`

**Input**

最近OLT设备在晚高峰时段波动明显，前面已经连续出现过电源模块告警和风扇故障，现在我想把Incident事件订阅补上，不是所有故障都要，只看严重和高，另外把单板故障也纳入条件，通过TextPart上报Incident通知。 输出保持成订阅请求，不要写成说明文。 当前是日常值守场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级为：严重、高。故障名称为：单板故障。（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过TextPart上报Incident通知。（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_007

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['链路抖动'], ['温度过高']]`
- Missing prompt groups: `[['链路抖动'], ['温度过高']]`
- Forbidden hits: `[]`

**Input**

最近核心网网元在晚高峰时段波动明显，前面已经连续出现过链路抖动和温度过高，现在我想把故障Incident订阅补上，不是所有故障都要，只看高和中，另外把光模块故障也纳入条件，用DataPart承载Incident数据进行上报。 如果描述里要带背景可以简短带一句，但不要改成分析任务。 当前是日常值守场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级为：高、中。故障名称为：光模块故障。（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过DataPart上报Incident数据（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_008

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['端口异常']]`
- Missing prompt groups: `[['端口异常']]`
- Forbidden hits: `[]`

**Input**

最近园区站点在晚高峰时段波动明显，前面已经连续出现过端口异常和光纤中断，现在我想把智能故障Incident订阅补上，不是所有故障都要，只看严重和中，另外把尾纤故障也纳入条件，按DataPart格式上报Incident消息。 输出保持成订阅请求，不要写成说明文。 当前是日常值守场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
{"故障优先级": ["严重", "中"], "故障名称": ["尾纤故障"]}（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
DataPart（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_014

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['链路抖动']]`
- Missing prompt groups: `[['链路抖动']]`
- Forbidden hits: `[]`

**Input**

最近接入侧网元在晚高峰时段波动明显，前面已经连续出现过尾纤故障和链路抖动，现在我想把Incident事件订阅补上，不是所有故障都要，只看critical和major，另外把温度过高也纳入条件，通过TextPart上报Incident通知。 输出保持成订阅请求，不要写成说明文。 当前是割接观察场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
["critical", "major", "温度过高"]（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
TextPart（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_015

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['主控板异常'], ['端口异常']]`
- Missing prompt groups: `[['主控板异常'], ['端口异常']]`
- Forbidden hits: `[]`

**Input**

最近汇聚节点在晚高峰时段波动明显，前面已经连续出现过主控板异常和端口异常，现在我想把故障Incident订阅补上，不是所有故障都要，只看严重和major，另外把光纤中断也纳入条件，用DataPart承载Incident数据进行上报。 如果描述里要带背景可以简短带一句，但不要改成分析任务。 当前是割接观察场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级：严重、major；故障名称：光纤中断（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过DataPart上报Incident数据（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_016

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['电源模块告警'], ['风扇故障']]`
- Missing prompt groups: `[['电源模块告警'], ['风扇故障']]`
- Forbidden hits: `[]`

**Input**

最近OLT设备在晚高峰时段波动明显，前面已经连续出现过电源模块告警和风扇故障，现在我想把智能故障Incident订阅补上，不是所有故障都要，只看严重和高，另外把单板故障也纳入条件，按DataPart格式上报Incident消息。 输出保持成订阅请求，不要写成说明文。 当前是割接观察场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
{"故障优先级": ["严重", "高"], "故障名称": ["单板故障"]}（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
DataPart（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_017

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['链路抖动'], ['温度过高']]`
- Missing prompt groups: `[['链路抖动'], ['温度过高']]`
- Forbidden hits: `[]`

**Input**

最近核心网网元在晚高峰时段波动明显，前面已经连续出现过链路抖动和温度过高，现在我想把Incident订阅补上，不是所有故障都要，只看高和中，另外把光模块故障也纳入条件，通过DataPart上报Incident数据。 如果描述里要带背景可以简短带一句，但不要改成分析任务。 当前是割接观察场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级：["高", "中"]；故障名称：["光模块故障"]（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过DataPart上报Incident数据（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_018

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['端口异常']]`
- Missing prompt groups: `[['端口异常']]`
- Forbidden hits: `[]`

**Input**

最近园区站点在晚高峰时段波动明显，前面已经连续出现过端口异常和光纤中断，现在我想把Incident事件订阅补上，不是所有故障都要，只看严重和中，另外把尾纤故障也纳入条件，通过TextPart上报Incident通知。 输出保持成订阅请求，不要写成说明文。 当前是割接观察场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
{"故障优先级": ["严重", "中"], "故障名称": ["尾纤故障"]}（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过TextPart上报Incident通知（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_020

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['温度过高']]`
- Missing prompt groups: `[['温度过高']]`
- Forbidden hits: `[]`

**Input**

最近室分设备在晚高峰时段波动明显，前面已经连续出现过温度过高和光模块故障，现在我想把智能故障Incident订阅补上，不是所有故障都要，只看严重和major，另外把电源模块告警也纳入条件，按DataPart格式上报Incident消息。 输出保持成订阅请求，不要写成说明文。 当前是割接观察场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
{"故障优先级": ["严重", "major"], "故障名称": ["电源模块告警"]}（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
DataPart（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_023

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['电源模块告警']]`
- Missing prompt groups: `[['电源模块告警']]`
- Forbidden hits: `[]`

**Input**

最近站点设备在晚高峰时段波动明显，前面已经连续出现过光模块故障和电源模块告警，现在我想把故障Incident订阅补上，不是所有故障都要，只看严重和中，另外把风扇故障也纳入条件，用DataPart承载Incident数据进行上报。 如果描述里要带背景可以简短带一句，但不要改成分析任务。 当前是告警复盘后的补订阅场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级：严重、中；故障名称：光模块故障、风扇故障（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过DataPart上报Incident数据（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_pr_025

- Case type: `positive_recognized_but_slot_risky`
- Recognition correct: `True`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[['主控板异常'], ['端口异常']]`
- Missing prompt groups: `[['主控板异常'], ['端口异常']]`
- Forbidden hits: `[]`

**Input**

最近汇聚节点在晚高峰时段波动明显，前面已经连续出现过主控板异常和端口异常，现在我想把Incident订阅补上，不是所有故障都要，只看严重和major，另外把光纤中断也纳入条件，通过DataPart上报Incident数据。 如果描述里要带背景可以简短带一句，但不要改成分析任务。 当前是告警复盘后的补订阅场景。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
{"故障优先级": ["严重", "major"], "故障名称": ["光纤中断"]}（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
通过DataPart上报Incident数据（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_nn_001

- Case type: `negative_near_intent`
- Recognition correct: `False`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[]`
- Missing prompt groups: `[]`
- Forbidden hits: `[]`

**Input**

请把下面这段Incident订阅说明翻成英文：基站设备的Incident，关注光纤中断和尾纤故障，级别是严重和高。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级为：严重、高。故障名称为：光纤中断、尾纤故障。（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

### si_nn_002

- Case type: `negative_near_intent`
- Recognition correct: `False`
- Slot exact: `False`
- Prompt exact: `False`
- Failure code: `None`
- Failure stage: `None`
- Missing slot groups: `[]`
- Missing prompt groups: `[]`
- Forbidden hits: `[]`

**Input**

解释一下如何订阅无线网元的Incident事件，重点说明无线网元的Incident事件，关注单板故障和主控板异常，级别是高和中。

**Failure Message**

None

**Prompt**

```text
## 订阅描述
请根据以下 <通知主题>、<订阅条件>、<上报通知数据格式>及<预期输出> 信息，完成网络侧智能故障Incident订阅与上报任务。

## 通知主题
Incident（必选）
要求：提供智能故障Incident的主题名称，具体名称可以是Incident、Fault、智能故障、故障等。

## 订阅条件
故障优先级为：高、中。故障名称为：单板故障、主控板异常。（可选）
要求：订阅条件包括故障优先级，故障名称。
故障优先级：支持传入列表，该参数的取值范围包括 严重、高、中和低。
故障名称：支持传入列表，该参数的取值范围为 网络侧故障的名称列表。例如：尾纤故障，光纤中断，单板故障，光模块故障等。

## 上报通知数据格式
（可选）
要求：1、上报的数据类型：Incident、故障；2、上报的数据格式：基于A2A的哪一种Part承载（DataPart、TextPart）
例如：通过DataPart上报Incident数据

## 预期输出
1、订阅结果，成功或失败
2、订阅失败原因（可选）
```

