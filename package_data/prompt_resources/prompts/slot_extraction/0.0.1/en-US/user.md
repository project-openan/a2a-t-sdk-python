Extract slot values from the normalized input based on the slot schema and template provided below.

## Extraction Guidelines
1. Identify explicit values for each slot from the input text
2. Validate extracted values against each slot's value_constraint
3. For slots without explicit input, determine if they are required or optional
4. Format list-type slots as JSON array strings

## Error Handling
- If a required slot cannot be extracted: set value=null, report code="missing_input"
- If an optional slot cannot be extracted: set value=null, no error
- If a value violates value_constraint: set value=null, report code="invalid_value"

## Example Output
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

Process the input now and return your extraction result.