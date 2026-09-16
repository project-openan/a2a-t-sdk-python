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
    - When the type is [Create]: Based on <Task Object> and <Task Context>, output a wireless network energy saving plan that achieves the wireless network energy saving goal defined in <Task Target>, and return the wireless network energy saving result in the format defined in <Expected Output>.
    - When the type is [Modify]: Based on <Task Object> and <Task Context>, output a wireless network energy saving plan that achieves the wireless network energy saving goal defined in <Task Target>.

## Task Object

{{task_object}} (required)

Requirement:

1. Area name: (required)
    - Explicitly specify the energy saving coverage scope information, a geographic or logical area (e.g., district-level administrative unit)
    - Must be a concrete and physically real community name such as "Songshanhu Administration Committee", rather than a description without a specific physical location such as "all communities", "indoor communities", or "outdoor communities"
    - Example: Area: "Songshanhu Administration Committee"

## Task Target

{{task_target}} (optional)

Requirement:

1. Define the quantitative or qualitative goals the task must achieve. Quantitative and qualitative goals cannot be selected at the same time, and the default is a qualitative goal. The specific description of energy saving goals is as follows:
    - Quantitative goal: Energy consumption goal; the gain target of energy saving. The target value format must be a percentage, such as the percentage by which total power consumption is reduced. Example: Energy consumption goal: 30%
    - Qualitative goal: Maximize energy saving under the conditions defined in the task context

## Task Context

{{task_context}} (optional)

Requirement:

1. Cell radio access technology for energy saving (LTE/NR/all). Example: Cell RAT for energy saving: NR
2. The time range for executing energy saving, recurring daily. If omitted, energy saving is performed for the full period. **The rate goal is guaranteed only within the energy saving enabled time range**. Example: Energy saving time range: start time "08:00:00", end time "12:00:00"
3. Carrier frequencies that can be turned off during energy saving. Example: Downlink frequency numbers: 1650, 1600
4. Rate guarantee goal: the minimum downlink throughput target that must be guaranteed and its effective time period.

    - **For the area-level setting**, the following information needs to be provided:

      Option 1:
        - Time-segmented rate guarantee goal (required). For time periods without a rate guarantee goal set, experience-lossless energy saving is adopted by default, with a maximum of three rate values
        - Example: Time-segmented rate guarantee goal: {00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}

      Option 2:
        - Full-period rate guarantee goal (required)
        - Example: Rate guarantee goal: 10Mbps

    - **For the site-level setting**, the following information needs to be provided:
        - Site list (required)
        - Time-segmented rate guarantee goal (required). For time periods without a rate guarantee goal set, experience-lossless energy saving is adopted by default, with a maximum of three rate values
        - Background event and event duration (optional)
        - Example:
            - Site list 1: ["Site Name 1", "Site Name 2"], Time-segmented rate guarantee goal: {00:00~06:00,2Mbps}, {06:00~18:00,10Mbps}, Background event: power outage from 00:00 to 18:00.
            - Site list 2: ["Site Name 3", "Site Name 4"], Time-segmented rate guarantee goal: {01:00~06:00,2Mbps}, {06:00~18:00,10Mbps}, Background event: power outage from 01:00 to 18:00.

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
