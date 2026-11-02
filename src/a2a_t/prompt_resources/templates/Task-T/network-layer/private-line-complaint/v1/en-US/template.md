## Task Type
Transport private line service complaint diagnosis

## Task Description
Based on [Task Object](#task-object) and [Task Context](#task-context), perform network-side fault root cause diagnosis in the complaint scenario, achieve the complaint diagnosis goal defined in [Task Target](#task-target), and return the task processing result in the structure defined in [Expected Output](#expected-output).

## Task Target
Diagnose network-side faults and return diagnostic result information such as fault root causes and repair suggestions.

## Task Object
{{task_object}} (required)

Requirement: Supports passing in private line service name / private line service identifier / private line service access port name. Choose one of these methods to identify the private line service object
1. Private line service name (optional): the private line service name on the network side. Example: SZ_Aggregation_Private_Line_Service_100M_SPN
2. Private line service identifier (optional): the unique identifier of the private line service on the network side. Example: 5571d707-5aad-11ea-8629-286ed488cff3
3. Access port name (optional): the access port name of the service, which can be a physical port or a logical port (sub-interface)
   - Physical port format: network element name + board number + board model + port number. Example: P180-SZ-PTN7900-23-TPA1EG24-17
   - Logical port format: network element name + board number + board model + port number (cvlan=access vlan id). Example: P180-SZ-PTN7900-23-TPA1EG24-17(cvlan=100)

## Task Context
{{task_context}} (required)

Requirement: Please provide the following information
1. Complaint category (required): includes two scenarios, "private line interruption" and "poor private line quality". Example: "poor private line quality"
2. Problem occurrence time (optional); Example: "2026-05-16T08:21:46Z"
3. OSS-side event sequence number (required): fill in the private line service complaint work order sequence number on the OSS side. Example: "event-id-20260511-09013"
4. Complaint details (optional): may include the private line user's description of the service complaint and the OSS-side preprocessing information. Example: "Starting from 8:30 this morning, the response latency from SZ to GZ suddenly increased from an average of 12ms to 320ms. Accessing the core trading system of the GZ data center is very slow. The trading interfaces of the counter and mobile banking frequently report 'connection timeout'. Normal business operations take a dozen seconds or even half a minute to return results!"

## Expected Output
Requirement: The complaint diagnosis task result should include the following information:
1. Diagnosis result (required); Allowed values: success, failure
2. Diagnosis result details (required)
3. Repair suggestions (optional)
4. Fault root cause list(optional): where each fault root cause includes fault root cause name, detailed description, repair suggestions, fault root cause point location, etc.

## Terminology Explanation
1. Private line interruption
   - Synonyms: private line interruption, service interruption, network unreachable, service down, service inaccessible
2. Poor private line quality
   - Synonyms: private line poor quality, service stutter, service access timeout, service packet loss, service high latency, service jitter, service congestion, service experience degradation, service high error rate, service optical power abnormal
