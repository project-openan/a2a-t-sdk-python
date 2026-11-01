## Target Negotiation Result
{{target_negotiation_result}} (required)

Requirement:
1. If all questions can be clarified, return Accept
2. If the target clarification confirmation request from the counterparty is received, and it is determined to proceed with this target, return Accept
3. If all questions cannot be fully clarified, return Reject
4. The conclusion must be one of the two, and vague states such as "partially agreed" are not allowed

## Target Negotiation Result Content
{{target_negotiation_result_content}} (required)

Requirement:
1. If all questions can be clarified, list the finally confirmed intent content
2. If the target clarification confirmation request from the counterparty is received, and it is determined to proceed with this target, reply with agreement
3. If all questions cannot be fully clarified, state the reason for failure

Example:
1. Accept Example: The clarified intent content is as follows. This negotiation is confirmed and ended:
   - The area is Songshanhu
   - The time-segmented rate guarantee goals are {00:00~07:00,2Mbps}, {07:00~23:00,20Mbps}, {23:00~24:00,10Mbps}
2. Accept Example: Agree to proceed with this target. This negotiation is confirmed and ended.
3. Reject Example: There are questions that cannot be clarified, and some information cannot be obtained. This negotiation is confirmed and ended.
