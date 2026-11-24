Extract slot values from the normalized input based on the slot schema and template provided below.
If explicit information for the same slot is scattered across multiple places, merge and keep it as long as no negation, exclusion, or replacement semantics are violated.

## Extraction Guidelines
1. Identify explicit values for each slot from the input text
2. Decide closed value ranges strictly per the ordered rules in the system prompt (rule 13)
3. For slots without explicit input, determine if they are required or optional
4. Format list-type slots as JSON array strings

## Error Handling
- If a required slot cannot be extracted: set value=null, report code="slot.not_provided" with facts {"slot_label": ...}
- If an optional slot cannot be extracted: set value=null, no error entry
- If the input word is outside a slot's closed value range: set value=null, report code="slot.constraint_violated" with facts {"slot_label": ..., "actual": ...}
- If the input provides content for a slot but its intent is unclear and the concrete value cannot be determined: set value=null, report code="slot.intent_unclear" with facts {"slot_label": ..., "actual": ...}
- Format-anomalous content (invalid dates, meaningless characters, and the like) is extracted as-is with no error entry; the validation stage decides

## Example Output
### Example 1: no errors
```json
{
  "slots": {
    "incident_name": "[\"eth-los\"]",
    "incident_level": "[\"critical\", \"major\"]",
    "extra_incident_subscription_condition": null,
    "extra_incident_basic_info": null,
    "extra_incident_analysis_result": null,
    "extra_incident_business_impact": null
  },
  "slot_errors": []
}
```

### Example 2: required slot not extracted
```json
{
  "slots": {
    "incident_name": "[\"eth-los\"]",
    "incident_level": "[\"critical\", \"major\"]",
    "extra_incident_subscription_condition": null
  },
  "slot_errors": [
    {
      "slot_name": "extra_incident_subscription_condition",
      "code": "slot.not_provided",
      "facts": {
        "slot_label": "Subscribe Condition"
      }
    }
  ]
}
```

Process the input now and return your extraction result.
