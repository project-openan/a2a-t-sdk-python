## Operation Type

{{operation_type}} (required)

Requirement:

1. Please provide the operation type. Allowed values: Create, Modify

## Task Type

Wireless Intelligent Backup Power Management

## Task Description

{{task_description}} (required)

Requirement:

1. Based on <Task Object> and <Task Context>, perform wireless intelligent backup power management to achieve the backup power target defined in <Task Target>, and return the task processing result according to the structure defined in <Expected Output>.

## Task Target

{{task_target}} (required)

Requirement:

1. The task target supports 3 options: Create New Tiered Power-Down Task, Create Restoration Task, Modify Tiered Power-Down Task. Among them, `Create Restoration Task` is used to roll back the configuration delivered by an existing tiered power-down task.
The specific requirements for these 3 options are as follows:
   - When selecting `Create New Tiered Power-Down Task` or `Modify Tiered Power-Down Task`, the following information must be provided:
     - Backup power endurance duration target; example: "4h";
   - When selecting `Create Restoration Task`, the content is:
     - Roll back wireless intelligent backup power configuration;

## Task Object

{{task_object}} (required for Create New Tiered Power-Down Task, optional for Modify Tiered Power-Down Task, optional for Create Restoration Task)

Requirement:

1. Supports list format, where each item in the list contains the following information:
    - Site Name: The name of the site that needs to execute the backup power task; example: "Longyan Xinluo Crystal Lanting-HRHH"; (Required for Create New Tiered Power-Down Task, Optional for Modify Tiered Power-Down Task, Optional for Create Restoration Task)
    - The list of cells included in the site, which may contain the following: (Optional for Create New Tiered Power-Down Task, Optional for Modify Tiered Power-Down Task, Not required for Create Restoration Task)
      - Cell Identifier: Using CGI; example: "460-00-2136088-512";
      - Cell Protection Level; allowed values include: Critical Protection, Key, General; example: "Critical Protection";
      - Cell Frequency Band; example: 2.6GHz;
      - Cell Technology; allowed values: 4G (or LTE), 5G (or NR); example: 5G;
      - Resident Users; example: 326 households;
      - Current RRC Users; example: 216 households;
      - Traffic; example: 12.6GB;
      - Voice Traffic; example: 72Erl;

Example:
 - Longyan Xinluo Crystal Lanting-HRHH:
   - 460-00-2136088-511: Critical Protection, 4G, 2.3GHz
   - 460-00-2136088-512: Critical Protection, 4G, 2.3GHz
   - 460-00-2136088-513: Key, 4G, 2.3GHz
 - Longyan Xinluo Longhui Community-HRHH:
   - 460-00-2134161-514: Key, 5G, 2.6GHz
   - 460-00-2134161-515: General, 5G, 2.6GHz
   - 460-00-2134161-516: General, 5G, 2.6GHz

## Task Context

{{task_context}} (optional)

## Expected Output

{{expected_output}} (optional)

Requirement:

1. When `Create New Tiered Power-Down Task` or `Modify Tiered Power-Down Task`, provide the following content:
    - Execution Result; allowed values include: Success, Failure; (required)
    - Provide the cell lists for non-shutdown and executed shutdown separately (required)
      - Cell list not to be shut down in this task:
        - Site Name
        - The list of cells included in the site, which must contain the following:
          - Cell Identifier: Using CGI; example: "460-00-2136088-511";
      - Cell list for executed shutdown:
        - Site Name
        - The list of cells included in the site, which must contain the following:
          - Cell Identifier: Using CGI; example: "460-00-2136088-513";
          - Frequency Band; example: 2.3GHz;
          - Cell Shutdown Execution Result; example: Execution Success;
          - Shutdown Execution Time; example: 2026/9/16 21:42:09;

2. When `Create Restoration Task`, provide the following content:
    - Execution Result; allowed values include: Success, Failure; (required)
    - Provide the cell lists for no-rollback and executed rollback separately (required)
      - Cell list not requiring rollback in this task:
        - Site Name
        - The list of cells included in the site, which must contain the following:
          - Cell Identifier: Using CGI; example: "460-00-2136088-511";
      - Cell list for executed rollback:
        - Site Name
        - The list of cells included in the site, which must contain the following:
          - Cell Identifier: Using CGI; example: "460-00-2136088-513";
          - Frequency Band; example: 2.3GHz;
          - Cell Rollback Execution Result; example: Rollback Success;
          - Rollback Execution Time; example: 2026/9/16 23:30:00;
