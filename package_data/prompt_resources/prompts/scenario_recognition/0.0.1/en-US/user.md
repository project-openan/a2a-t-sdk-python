Analyze the normalized input and match it to the best supported scenario.

## Scenario Hints
- **energy_saving**: Keywords include "energy", "power", "consumption", "saving", "efficiency", "optimize". Intent: analyze energy usage and suggest optimizations.
- **subscribe_incident**: Keywords include "subscribe", "incident", "alarm", "event", "notification", "alert". Intent: subscribe to incident/alarm events from network devices.

## Decision Rules
1. If input clearly indicates one task type, select that scenario
2. If input is ambiguous, choose the most likely scenario based on domain keywords
3. If input cannot be mapped to any scenario, return matched=false with explanation

Process the input now and return your matching result.