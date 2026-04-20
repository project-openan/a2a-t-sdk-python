根据下方提供的 slot schema 和模板，从归一化输入中提取 slot 值。

## 提取指南
1. 从输入文本中识别每个 slot 的明确值
2. 根据 slot 的 value_constraint 校验提取的值
3. 对于无明确输入的 slot，判断其是否为必填
4. 将 list 类型 slot 格式化为 JSON 数组字符串

## 错误处理
- 必填 slot 无法提取：值设为 null，报告 code="missing_input"
- 可选 slot 无法提取：值设为 null，无需错误
- 值违反 value_constraint：值设为 null，报告 code="invalid_value"

## 输出示例
```json
{
  "slots": {
    "subscription_condition_incident_name": "[\"eth-los\"]",
    "subscription_condition_incident_level": "[\"critical\", \"major\"]"
  },
  "slot_errors": []
}
```

现在处理输入并返回提取结果。