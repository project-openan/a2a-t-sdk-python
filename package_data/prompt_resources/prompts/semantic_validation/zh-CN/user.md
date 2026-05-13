请基于以下输入执行语义校验，并严格返回 JSON。

输入说明：
- 你将收到一个 JSON 对象，仅包含：
  - slot_json_schema
  - extracted_slots

输出格式（严格遵守）：
{
  "passed": true|false,
  "errors": [
    {
      "slot_name": "string",
      "code": "semantic_mismatch|fabricated_value|cross_scenario_pollution|insufficient_grounding",
      "message": "string"
    }
  ]
}

判定要求：
1. 严格遵守 system prompt 中的“默认放宽”和“仅明确强约束才严格校验”规则。
2. 只根据 `slot_json_schema` 与 `extracted_slots` 判断，不要补充不存在的上下文。
3. passed=true 时 errors 必须为空数组；passed=false 时 errors 至少一条。
4. 仅输出 JSON，不要输出 Markdown，不要输出解释性前后缀。
