## Subscription Instruction
Please complete the autonomous data event subscription and reporting task based on the following <Event Topic>, <Subscription Condition>, <Reported Event Data Format>, and <Expected Output> information.
<Event Topic> indicates the subject content or event type to subscribe.
<Subscription Condition> indicates the metrics, thresholds, or triggering methods for event reporting.
<Reported Event Data Format> indicates the structure and field requirements of the reported data.
<Expected Output> indicates the subscription execution result and returned content.


## Event Topic
The subscription topic name is "incident"


## Subscription Condition
Required parameters:
    Incident name {incident_name};
Optional parameters:
    Incident level {incident_level};
Additional explanation for subscription condition (optional): {extra_incident_subscription_condition};
Requirements: 1) If the user has additional explanation for the subscription condition, the final subscription condition parameters are the required parameters plus the user's additional requirements, note that the user's requirements have higher priority; 2) If the user has no additional explanation, the subscription condition includes all required and optional parameters.


## Reported Event Data Format
### Event Basic Information
Required parameters: incident serial number, incident name, incident occurrence time, incident category, incident status, incident root cause alarm serial number, related alarm serial number.
Optional parameters: severity level, domain, incident update time, incident clear time, message type (including create, update, clear), incident phenomenon network-side resource identifier, type, name, detailed location information.
Additional explanation for event basic information (optional): {extra_incident_basic_info}.
Requirements: 1) If the user has additional explanation for event basic information, the final event basic information parameters are the required parameters plus the user's additional requirements, note that the user's requirements have higher priority; 2) If the user has no additional explanation, report all required and optional parameter information.

### Event Analysis Result
Default analysis result description: Must include incident root cause name, detailed description, remediation suggestion, incident root cause point resource object identifier, type, name, detailed location information. Note: When completing incident root cause analysis and reporting incident update message, incident serial number, incident update time, incident root cause, incident details, and remediation suggestion are required parameters.
Additional explanation for event analysis result (optional): {extra_incident_analysis_result}.
Requirements: 1) If the user has additional explanation for event analysis result, the final event analysis result should be based on "Default analysis result description", then adjust according to the user's additional explanation, note that the user's explanation has higher priority; 2) If the user has no additional explanation, report data based on "Default analysis result description".

### Event Business Impact Result
Business impact result description: Must include all business objects affected by the incident, the degree of business impact caused by the incident (service interruption, quality degradation, service downgrade, etc.). This is an optional parameter.
Additional explanation for business impact result (optional): {extra_incident_business_impact}.
Requirements: 1) If the user has additional explanation for business impact result, the final impact result should be based on "Business impact result description", then adjust according to the user's additional explanation, note that the user's requirements have higher priority; 2) If the user has no additional explanation, report data based on "Business impact result description".


## Expected Output
1. Subscription result: success or failure;
2. If subscription fails, the subscription failure reason