## Task Type
IPRAN service assurance

## Task Description
Based on [Task Object](#task-object) and [Task Context](#task-context), perform IPRAN service SLA assurance, achieve the service assurance goal defined in [Task Target](#task-target), and return the task processing result in the structure defined in [Expected Output](#expected-output).

## Task Target
Provide assurance for the affected services so that the SLA is not affected.

## Task Object
{{task_object}} (required)

Requirement: Fill in the list of affected services. List format is supported, and each item in the list contains the following information:
- Service identifier (required): service name; Example: "s1-l3vnp"
  - Service scenario (required): service scenario type, which can be ToB or ToC; Example: "ToB"
  - Base station name (optional when the service scenario is ToC, not required when the service scenario is ToB): base station name; Example: "Base Station 1"
  - Base station IP (optional when the service scenario is ToC, not required when the service scenario is ToB): base station IP; Example: "192.168.1.2"
  - Priority (required): service priority; allowed values: high, medium, low
  - Service type (required): service type; Example: "L3VPN"
  - UNI device name (required): network element name; Example: "NE1"
  - UNI interface (required): interface name; Example: "Ge1/0/1"
  - UNI interface IP (optional when the service scenario is ToC, not required when the service scenario is ToB): interface IP address; Example: "192.168.1.1"
  - Customer name (required when the service scenario is ToB, not required when the service scenario is ToC): customer name; Example: "Customer 1"

## Task Context
{{task_context}} (required)

Requirement: Please provide the following information:
- Incident serial number (required): fill in the CSN of the incident that reports the affected services. Example: 1673735459373056

## Expected Output
Requirement: The service assurance task output should contain the following information:
1. Service restoration result (required); Allowed values: success, failure, partial success
2. Service restoration details (required)
    - Number of restored services: includes the expected number of services to be restored, the number of successfully restored services, and the number of failed services. Example: 10 services are expected to be restored, of which 9 are successfully restored and 1 fails to be restored.
    - Service list:
        - Service identifier (required): service name; Example: "s1-l3vnp"
        - Service scenario (required): service scenario type, which can be ToB or ToC; Example: "ToB"
        - Base station name (optional when the service scenario is ToC, not required when the service scenario is ToB): base station name; Example: "Base Station 1"
        - Base station IP (optional when the service scenario is ToC, not required when the service scenario is ToB): base station IP; Example: "192.168.1.2"
        - Priority (required): service priority; allowed values: high, medium, low
        - Service type (required): service type; Example: "L3VPN"
        - UNI device name (required): network element name; Example: "NE1"
        - UNI interface (required): interface name; Example: "Ge1/0/1"
        - UNI interface IP (optional when the service scenario is ToC, not required when the service scenario is ToB): interface IP address; Example: "192.168.1.1"
        - Customer name (required when the service scenario is ToB, not required when the service scenario is ToC): customer name; Example: "Customer 1"
        - Restoration result (required): result of the restoration operation; allowed values: success, failure
        - Restoration plan (optional): details of the restoration plan, including operation objects, operation object types, and operations, etc.; fill in when the restoration result is success
        - Failure reason (optional): reason for the restoration failure; fill in when the restoration result is failure
3. Unchanged service details (required)
    - Number of unchanged services: includes the number of services that remain unchanged.
    - Service list:
        - Service identifier (required): service name; Example: "s1-l3vnp"
        - Service scenario (required): service scenario type, which can be ToB or ToC; Example: "ToB"
        - Base station name (optional when the service scenario is ToC, not required when the service scenario is ToB): base station name; Example: "Base Station 1"
        - Base station IP (optional when the service scenario is ToC, not required when the service scenario is ToB): base station IP; Example: "192.168.1.2"
        - Priority (required): service priority; allowed values: high, medium, low
        - Service type (required): service type; Example: "L3VPN"
        - UNI device name (required): network element name; Example: "NE1"
        - UNI interface (required): interface name; Example: "Ge1/0/1"
        - UNI interface IP (optional when the service scenario is ToC, not required when the service scenario is ToB): interface IP address; Example: "192.168.1.1"
        - Customer name (required when the service scenario is ToB, not required when the service scenario is ToC): customer name; Example: "Customer 1"
        - Reason for remaining unchanged (required): reason why the service remains unchanged

## Terminology Explanation
1. ToC
   - Synonyms: To Consumer, services for individual consumers
2. ToB
   - Synonyms: To Business, services for enterprise customers
