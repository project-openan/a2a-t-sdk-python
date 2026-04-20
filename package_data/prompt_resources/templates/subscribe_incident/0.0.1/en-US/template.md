## Subscription Instruction
Please complete the autonomous data event subscription and reporting task based on the following <Event Topic>, <Subscription Condition>, <Reported Event Data Format>, and <Expected Output> information.
<Event Topic> indicates the subject content or event type to subscribe.
<Subscription Condition> indicates the metrics, thresholds, or triggering methods for event reporting.
<Reported Event Data Format> indicates the structure and field requirements of the reported data.
<Expected Output> indicates the subscription execution result and returned content.

## Event Topic
Topic name: incident

## Subscription Condition
Requirements: Current subscription condition includes "Incident Name" and "Incident Level". Both are optional parameters.
Incident name parameter supports list input, the value range is the network-side incident name list. For example: fiber fault, fiber break, board fault, optical module fault, fiber connector contamination, NE power loss, ambient temperature abnormal, service board fault, fan board fault, NPE to core network route unreachable, laser aging, PWE3 QoS rate limiting, MPLS-TP static tunnel rate limiting, SR-TP static tunnel rate limiting, dedicated line access port rate limiting, bandwidth utilization overload, etc.
Incident level parameter supports list input, the value range includes Critical, Major, Minor, Warning.

Incident name is {subscription_condition_incident_name};
Incident level is {subscription_condition_incident_level}

## Reported Event Data Format
Requirements: Report event data according to the schema and target model definition in the following schema link.
Model definition schema: https://projects.tmforum.org/a2aproject/telecommunication/extensions/faultManagement/Incident/v1
Model name: IncidentObject

## Expected Output
1. Subscription result: success or failure;
2. Subscription failure reason (optional)