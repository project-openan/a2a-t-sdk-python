## Operation Type
{{operation_type}} (required)

Requirement:
1. Please provide the operation type. Allowed values: Create, Modify

## Task Type
Wireless network energy saving

## Task Description
{{task_description}} (required)

Requirement:
1. Please dynamically provide the task description based on <Operation Type> (Create/Modify):
    - When the type is `Create`: Based on <Task Object> and <Task Context>, output a wireless network energy saving plan that achieves the wireless network energy saving goal defined in <Task Target>, and return the wireless network energy saving result in the format defined in <Expected Output>.
    - When the type is `Modify`: Based on <Task Object> and <Task Context>, output a wireless network energy saving plan that achieves the wireless network energy saving goal defined in <Task Target>.

## Task Object
{{task_object}} (required)

Requirement:
1. Area name: (required)
    - Explicitly specify the energy saving coverage scope information, a geographic or logical area (e.g., district-level administrative unit)
    - Must be a concrete and physically real community name such as "Songshanhu Administration Committee", rather than a description without a specific physical location such as "all communities", "indoor communities", or "outdoor communities"
    - Only one region name is allowed
    - Example: Area: "Songshanhu Administration Committee"

## Task Target
{{task_target}} (optional)

Requirement:
1. Define the quantitative or qualitative goals the task must achieve. Quantitative and qualitative goals cannot be selected at the same time, and the default is a qualitative goal. The specific description of energy saving goals is as follows:
    - Quantitative goal: energy saving gain target. The target value format must be a percentage, such as the percentage by which total power consumption is reduced. Example: energy saving gain target: 15%
    - Qualitative goal: maximize energy saving under the conditions defined in the task context
2. When <Operation Type> is set to `Modify`, updating the task target is supported.

## Task Context
{{task_context}} (optional)

Requirement:
1. Cell radio access technology for energy saving (LTE/NR/all). Example: Cell RAT for energy saving: NR; (optional)
2. The time range for executing energy saving, recurring daily. If omitted, energy saving is performed for the full period. **The rate goal is guaranteed only within the energy saving enabled time range**. Example: Energy saving time range: start time "08:00:00", end time "12:00:00"; (optional)
3. Carrier frequencies that can be turned off during energy saving. Example: Downlink frequency numbers: 1650, 1600; (optional)
4. Rate guarantee target: the minimum downlink throughput target that must be guaranteed and its effective time period; (optional)

    - **For the area-level setting**, the following information needs to be provided:

      Option 1: Time-segmented rate guarantee target
        - Time Window: Up to 3 non-overlapping time windows can be configured. Specific and explicit time windows must be provided; vague time intervals such as busy hours or idle hours are not allowed. For any time window where no throughput guarantee target is specified, energy saving will default to a non-disruptive user experience mode. All configured time windows must fall within the active energy-saving execution period; (required)
        - Rate: Specify a target throughput for each time window, e.g., 10 Mbps; (required)
        - Example: Time-segmented rate guarantee target: {00:00~06:00, 2Mbps}, {06:00~18:00, 10Mbps}

      Option 2: Full-period rate guarantee target
        - Rate: If no throughput guarantee target is configured, energy saving will default to a non-disruptive user experience mode; (optional)
        - Example: Rate guarantee target: 10Mbps

    - **For the site-level setting**, the following information needs to be provided:
        - Site list; (required)
        - Time-segmented rate guarantee target:
          - Time Window: Up to 3 non-overlapping time windows can be configured. Specific and explicit time windows must be provided; vague time intervals such as busy hours or idle hours are not allowed. For any time window where no throughput guarantee target is specified, energy saving will default to a non-disruptive user experience mode; (required)
          - Rate: Specify a target throughput for each time window, e.g., 10 Mbps; (required)
        - Background event and event duration; (optional)
        - Example:
            - Site list 1: ["Site Name 1", "Site Name 2"], Time-segmented rate guarantee target: {00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}, Background event: power outage from 00:00 to 18:00.
            - Site list 2: ["Site Name 3", "Site Name 4"], Time-segmented rate guarantee target: {01:00~06:00,2Mbps}, {06:00~18:00,10Mbps}, Background event: power outage from 01:00 to 18:00.
5. When <Operation Type> is set to `Modify`, the configurable parameter is: Rate guarantee target.

## Expected Output
{{expected_output}} (optional when creating, not required when modifying)

Requirement:
1. When the task object is a geographic/logical area, output the intent report for the network energy saving task. The intent report should contain the following information:
    - Basic information about the intent execution result, including:
        - Area
        - Intent goal achievement status
    - Detailed information about the intent execution result, including:
        - Energy saving gain achievement status, current energy saving gain goal, actual energy saving gain percentage, target adjustment suggestions
        - Rate goal achievement status, current rate goal, actual rate, target adjustment suggestions

## Terminology Explanation
1. LTE
   - Synonyms: 4G, E-UTRAN
2. NR
   - Synonyms: 5G, NG-RAN
3. LNR
   - Explanation: Includes both LTE and NR RATs
4. Aggressive Energy Saving
   - Synonyms: Maximize Energy Saving
5. Conservative Energy Saving
   - Synonyms: Maximize User Experience Assurance, Maximize KPI Assurance
6. GS
   - Explanation: A network energy saving parameter defined in 3GPP, used to control the trade-off between energy consumption and performance. The higher the GS value, the more aggressive the energy saving strategy.
7. Video Bitrate
   - Explanation: Mapping between video bitrate and downlink throughput: 720p → 2 Mbps, 1080p → 10 Mbps, 4K → 20 Mbps.
8. Rate guarantee target
   - Synonyms: guaranteed throughput target, downlink throughput target, DL throughput target, User Experience Target, UX target, SLA
9. Site
   - Synonyms: NE, base station
10. Capacity Layer
    - Synonyms: Shutdown Layer
11. Coverage Layer
    - Synonyms: Retained Layer, Basic Layer
