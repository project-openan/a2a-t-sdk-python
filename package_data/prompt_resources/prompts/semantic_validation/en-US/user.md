Perform semantic validation based on the following input and return JSON.
The input is a JSON object containing only `slot_json_schema` and `extracted_slots`.

Output format (strict):
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

Requirements:
- Follow the system prompt's relaxed-by-default policy and apply strict validation only when explicit strong constraints exist.
- Judge only from `slot_json_schema` and `extracted_slots`; do not invent missing context.
- If passed=true, errors must be an empty array.
- If passed=false, errors must contain at least one item.
- Output JSON only. No markdown, no explanatory prefix/suffix.
