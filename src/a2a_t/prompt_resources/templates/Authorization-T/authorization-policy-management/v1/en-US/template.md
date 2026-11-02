## Authorization Policy Operation Type
{{authorization_policy_operation_type}} (required)

Requirement:
Provide the operation type of the authorization policy. Allowed values: add authorization policy, modify authorization policy, delete authorization policy, query authorization policy

## Authorization Policy Operation Description
Please complete the corresponding authorization operation based on [Authorization Policy Operation Type](#authorization-policy-operation-type) and [Network Operation Authorization Policy List](#network-operation-authorization-policy-list), and return the authorization policy operation execution result in the structure defined in [Expected Output](#expected-output). [Expected Output](#expected-output) indicates the expected return content.

## Network Operation Authorization Policy List
{{network_operation_authorization_policy_list}} (required for add, required for modify, required for delete, optional for query)

Requirement:
1. Supports a list format, where each item in the list contains the following information:
   - Network operation authorization policy identifier: uniquely identifies a network operation authorization policy. Example: 7d8c7b00-3c8c-4f8e-9b1e-9b17b6a3e5c3
   - Network operation business scenario: the business scenario supported by the network operation. Example: service complaint diagnosis
   - Network operation handling type. Example: service recovery
   - Network operation name: the specific name of the authorized network operation. Example: tunnel optimization
   - Validity period: the validity time range of the authorization policy. If the value is "permanently valid", it means the authorization is permanently valid with no validity period limit. Example: 2026-06-01T12:00:00Z ~ 2030-06-18T12:00:00Z
   - Creation time: the time when the authorization policy was created. Example: 2026-06-18T12:00:00Z
   - Last modification time: the time when the authorization policy was last modified. Example: 2026-06-18T12:00:00Z

2. When adding an authorization policy, the required parameters include: network operation business scenario, network operation handling type, network operation name, validity period
   When modifying an authorization policy, the parameter that can be modified is: validity period
   When querying authorization policies, the supported query conditions are: network operation business scenario, network operation handling type, network operation name
   When deleting authorization policies, the required parameters include: network operation authorization policy identifier

## Expected Output
1. Authorization operation execution result; Allowed values: success, failure, partial success
2. When the authorization operation is executed successfully, return the [Network Operation Authorization Policy List](#network-operation-authorization-policy-list) that was executed successfully
3. When the authorization operation fails or is partially successful, return a failure list containing the authorization policies and the failure reasons

## Terminology Explanation
1. Service complaint diagnosis
   - Synonyms: Leased Line Service Complaint, corporate leased line complaint, corporate business complaint, leased line complaint, corporate leased line business complaint diagnosis, leased line business complaint diagnosis, leased line complaint diagnosis, circuit complaint, leased line business appeal, leased line service complaint, group leased line business complaint, government-enterprise leased line customer complaint, leased line complaint event
2. Service recovery
   - Synonyms: Service Restoration, Service Recovery, restore service, service recovery, temporary service recovery, rapid service recovery, rapid service restoration, emergency service restoration, service restoration handling
3. Tunnel optimization
   - Synonyms: Tunnel Optimization, tunnel optimization, tunnel path optimization, tunnel service optimization, tunnel resource optimization, tunnel rerouting optimization, tunnel routing regularization, tunnel path reorganization
