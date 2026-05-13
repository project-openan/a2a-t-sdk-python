Perform semantic validation based on the following input and return JSON.
The input is a JSON object containing scenario, language, original text, template, slot schema summary, slot json schema, and extracted slots.

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
- If passed=true, errors must be an empty array.
- If passed=false, errors must contain at least one item.
- Output JSON only. No markdown, no explanatory prefix/suffix.
