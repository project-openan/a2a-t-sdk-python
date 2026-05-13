请基于以下输入执行语义校验，并严格返回 JSON。

输入说明：
- 你将收到一个 JSON 对象，包含：
  - scenario_code
  - language
  - processed_prompt_text
  - template_text
  - slot_schema_summary
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
1. 默认从严，不要“善意脑补”用户未提供的信息。
2. 若值是弱锚定、不可执行、占位词、歧义值，应判定失败。
3. passed=true 时 errors 必须为空数组；passed=false 时 errors 至少一条。
4. 仅输出 JSON，不要输出 Markdown，不要输出解释性前后缀。
