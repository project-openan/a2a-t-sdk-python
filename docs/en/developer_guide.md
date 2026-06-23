# 1 A2A-T Python SDK Developer Guide

## 1.1 Feature Introduction

See the [User Guide 1.1 Feature Introduction](https://github.com/project-openan/a2a-t-sdk-python/blob/main/docs/en/user_guide.md) section.

## 1.2 Constraints and Limitations

1. Python version requirement is 3.12+.
2. Built-in resource coverage is limited; the current bundled resources are primarily scenarios such as `subscribe_incident`.
3. Negotiation state storage only provides `in_memory`; state is lost after the process exits.
4. The SDK does not provide Agent HTTP service framework, registry-center client, authentication, or key management capabilities; these must be integrated by the business system.

## 1.3 Environment Preparation

### 1.3.1 Obtain Source Code

```bash
git clone git@github.com:project-openan/a2a-t-sdk-python.git
cd a2a-t-sdk-python
```

### 1.3.2 Install Development Dependencies

```bash
uv sync --dev
```

### 1.3.3 Prepare Configuration

```bash
cp package_data/env.example package_data/.env
```

Configuration example:

```properties
A2AT_LANGUAGE=zh-CN
A2AT_PROMPT_SOURCE_TYPE=local_file
A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR=
A2AT_PROMPT_COMPLIANCE_ENABLED=true
A2AT_LLM_PROVIDER=deepseek
A2AT_LLM_MODEL=deepseek-chat
A2AT_LLM_API_KEY={your_api_key}
A2AT_LLM_BASE_URL=https://api.deepseek.com
A2AT_NEGOTIATION_STATE_STORE_TYPE=in_memory
```

### 1.3.4 Verify the Project

```bash
uv run pytest
uv run ruff check .
uv run mypy src
```

## 1.4 SDK Basic Usage
### 1.4.1 Client Generating Task Prompt

```python
from pathlib import Path

from a2a_t.client.a2at_client import A2ATClient

client = A2ATClient(env_path=Path("package_data/.env"))

result = client.generate_task_prompt(
    {
        "scenario": "subscribe_incident",
        "objective": "Subscribe to incident notifications from network devices.",
        "subscription_condition_incident_level": ["critical"],
        "subscription_condition_incident_name": ["fiber break"],
    }
)

if result.success:
    print(result.prompt_text)
else:
    print(result.failure.to_dict())
```

### 1.4.2 Server Validating Task Prompt

```python
from pathlib import Path

from a2a_t.server.a2at_server import A2ATServer

server = A2ATServer(env_path=Path("package_data/.env"))

check_result = server.check_task_prompt(processed_prompt_text=prompt_text)
if check_result["success"]:
    print("prompt check passed")
else:
    print(check_result["failure"])
```

### 1.4.3 Initiating Negotiation

```python
from a2a_t.negotiation.common.enums import NegotiationType
from a2a_t.negotiation.common.models import StartNegotiationInput

payload = server.start_negotiation(
    StartNegotiationInput(
        type=NegotiationType.INFORMATION,
        content_text="Please provide incident level.",
        facts={"missingFields": ["subscription_condition_incident_level"]},
    )
)
```

The negotiation response will contain negotiation text and context. The business system needs to pass the context along with the next round of A2A message to the peer, and subsequently advance the negotiation state through `receive_negotiation` and `continue_negotiation`.

## 1.5 Complete Integration Development Flow

### 1.5.1 Client Agent

Client Agent typically follows this integration flow:

1. Receive user natural language or structured input.
2. Call `A2ATClient.generate_task_prompt` to generate a processed task prompt.
3. Place the generated result into the A2A message body or extension fields.
4. Send to the target Agent.
5. If negotiation context is received, call `receive_negotiation` and `continue_negotiation` to generate the next round of message.

Example:

```python
from pathlib import Path

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.negotiation.common.enums import NegotiationStatus
from a2a_t.negotiation.common.models import ContinueNegotiationInput, NegotiationContext

client = A2ATClient(env_path=Path(".env"))

prompt_result = client.generate_task_prompt(
    {
        "scenario": "subscribe_incident",
        "objective": "Subscribe to incident notifications from network devices.",
    }
)

if not prompt_result.success:
    raise RuntimeError(prompt_result.failure.to_dict())

processed_prompt = prompt_result.prompt_text

# Continue negotiation after receiving server negotiation message
receive_result = client.receive_negotiation(server_message, server_context)
continue_payload = client.continue_negotiation(
    ContinueNegotiationInput(
        context=NegotiationContext.from_context(receive_result["context"]),
        status=NegotiationStatus.IN_PROGRESS,
        content_text=processed_prompt,
    )
)
```

### 1.5.2 Server Agent

Server Agent typically follows this integration flow:

1. Extract processed task prompt from the A2A request.
2. Call `A2ATServer.check_task_prompt`.
3. After validation passes, proceed to business execution.
4. When validation fails or the business layer finds insufficient information, call `start_negotiation` or `continue_negotiation`.
5. Return negotiation text and context to the client.

Example:

```python
from pathlib import Path

from a2a_t.negotiation.common.enums import NegotiationType
from a2a_t.negotiation.common.models import StartNegotiationInput
from a2a_t.server.a2at_server import A2ATServer

server = A2ATServer(env_path=Path(".env"))
result = server.check_task_prompt(processed_prompt_text=processed_prompt)

if result["success"]:
    business_result = execute_business(processed_prompt)
else:
    negotiation_payload = server.start_negotiation(
        StartNegotiationInput(
            type=NegotiationType.INFORMATION,
            content_text="Please provide missing subscription information.",
            facts={"failure": result["failure"]},
        )
    )
```

## 1.6 Prompt Resource Extension
### 1.6.1 File Extension

When customizing scenarios, you need to prepare files consistent with the built-in resource structure:

```text
prompt_resources/
  scenarios/zh-CN/scenarios.json
  slots/{scenario_code}/zh-CN/slot.json
  templates/{scenario_code}/zh-CN/template.md
  prompts/scenario_recognition/zh-CN/system.md
  prompts/scenario_recognition/zh-CN/user.md
  prompts/slot_extraction/zh-CN/system.md
  prompts/slot_extraction/zh-CN/user.md
  prompts/semantic_validation/zh-CN/system.md
  prompts/semantic_validation/zh-CN/user.md
```

Then configure in `.env`:

```properties
A2AT_PROMPT_SOURCE_TYPE=local_file
A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR={your_prompt_resources_root}
A2AT_LANGUAGE=zh-CN
```

It is recommended to supplement the following tests after adding new resources:

1. Scenario recognition tests.
2. Slot extraction tests.
3. Slot JSON Schema validation tests.
4. Client generation and server validation end-to-end tests.

### 1.6.2 How to Define Prompt Templates
#### 1.6.2.1 Core Value
A2A-T structured Prompt provides a reusable structured approach for providing clear and consistent prompts to LLMs. By separating core logic from variable data, it makes interactions between agents more reliable, efficient, and scalable. The main benefits of using structured Prompts include:
- 	Consistency: Ensures prompts follow a standardized format, making agent output more predictable.
-	Efficiency: Avoids writing each Prompt from scratch, saving time and effort. It also avoids repeating complex instructions.
-	Scalability: Makes it easier to generate prompts for various business scenarios.
-	Optimization: Allows templates to be refined and optimized for better results.

#### 1.6.2.2 Classification of A2A-T Prompt Templates
For agent communication in the telecom domain, to ensure the completeness of request content and improve reasoning efficiency and accuracy, A2A-T defines structured Prompt templates for each AN high-value scenario, and has published the industry A2A-T protocol standards at TMF:
《IG1453A_Structured_Prompt_of_Agent_to_Agent_Protocol_for_Telecoms_A2AT_v1.0.0》
《IG1453_Agent_to_Agent_Protocol_for_Telecoms_A2AT_v2.0.0》

Structured Prompt template definitions are divided into two layers:
- L0 Base Template:
Defines the foundational framework for structured Prompts of ICT domain tasks, without specifying variables and ontologies for particular scenarios.
L0 template list:

	| Template Name | Description   |
	|--|--|
	|Task-T  | Defines the basic structure of ICT domain tasks, but does not specify commonly used variables and general ontology specifications for specific scenarios. Parsing of the base template relies on the LLM's reasoning capability and the Agent's context processing capability. |
	|Notification-T |Defines a structured prompt-based network event subscription and reporting mechanism for the ICT domain. This mechanism ensures real-time awareness of network events and provides consistent task descriptions across different levels and domains through structured prompts. |

- L1 Value Scenario Prompt Template:
Built upon L0 templates, defines commonly used "variables" for different high-value scenario tasks, so that during task generation, agents can input corresponding content based on these variables, and during task execution, identify relevant content to improve reasoning efficiency and accuracy.

#### 1.6.2.3 Core Composition Elements
A complete A2A-T Prompt template generally contains the following two parts:
1. Instruction
	- Definition: Core directive or context.
	- Purpose: Provides the basic requirements and framework for the task.
	- Syntax: Uses ## to directly mark the instruction name (e.g., ## Task Description).
2. Variable
	- Definition: Dynamic slots, filled with specific data each time used.
	- Purpose: Provides more specific information, improving reasoning efficiency.
	- Syntax: Uses double curly braces {{}} to identify variable names (e.g., {{Fault Occurrence Time}}).

##### 1.6.2.3.1 Instruction

1. Instruction syntax requirements: When declaring an "instruction", use "##" for marking, followed by the name of the "instruction", so that the Agent can recognize it and perform content input or corresponding reasoning and execution.
2. Instruction set: The structured Prompt defined by A2A-T has established the foundational framework for ICT task Prompt templates, deconstructing typical information of ICT tasks into the following instructions.


|Instruction Name | Required/Optional|Description and Example |
|--|--|--|
| Task Description |	Required 	| Describes the basic requirements of the task. Example:<br> `## Task Description` <br> `Analyze the root cause of the fault based on "Target Object", "Task Context", and "Constraints", and provide repair suggestions. Please respond to the task according to the structure defined in "Expected Output".`
|Task Type|Optional  |Identifies the task type (e.g., fault diagnosis, energy efficiency optimization). Example:<br>`## Task Type`<br>`Fault Diagnosis ` |
|Target Object|Optional|  Describes the direct object of the task operation. Example:<br> `## Target Object`<br>`Fault identifier (fault-csn) is "OSS-FAULT-20250405-001".`|
|Task Context|Optional  |Provides background information for task execution. Example:<br>`## Task Context`<br>`Fault occurrence time (occur-time) is "2025-04-05T14:30:00Z". ` |
| Expected Output|Optional  | Defines the format of the expected result. Example:<br>`## Expected Output`<br> `Fault diagnosis results should include the following information: 1. Diagnosis status: success or failure 2. Fault diagnosis analysis results 3. Repair suggestions 4. Fault root cause list 5. Domain-specific information` |

##### 1.6.2.3.2 Variable
1. Variable syntax requirements: When using "variables", use double curly braces "{{}}" for identification, and place the variable name inside the double curly braces, so that the agent can recognize it and perform content input or corresponding reasoning and execution.
2. Variable instantiation methods: Variables need to be correctly instantiated to deliver value; two methods are recommended
	- Natural language subject-verb-object structure: The fault occurred on January 8, 2026 at 16:38:18
	- Key-value concise format: Fault Occurrence Time: 2026/1/8/16:38:18
3. Common variable set:
Based on best practices, A2A-T has summarized commonly used variables in AN L4 high-value scenarios, which help effectively describe tasks in AN L4 high-value scenarios:
	|Variable Name | Required/Optional|Description and Example |
	|--|--|--|
	| Identifier |	Required 	| Used to specify the target identifier associated with the task.<br> Example:<br>`## Target Object`<br> `{{Identifier}}` <br>Its instantiation example is as follows: <br>`## Target Object`<br>`Fault identifier (fault-csn) is "OSS-FAULT-20250405-001".`
	|Affected Object|Optional  |Used to specify the network resource object affected by the fault.<br>Example:<br>`## Task Context`<br>`{{Affected Object}}`<br>Its instantiation example is as follows:<br>`## Task Context `<br>`The ID of the affected object is "BTS-001", the type is "Base Station Transceiver", the name is "Base Station 001", and the location is "Chaoyang District, Beijing".` |
	|Related Information|Optional| Its general ontology can be a list of events or alarms related to the fault.<br> Example:<br>`## Task Context `<br>`{{Related Information}} `<br>Its instantiation example is as follows:<br>`## Task Context`<br>`The associated alarm list is as follows: - Alarm identifier (alarm-csn) is "ALM-20250405-001", alarm ID (alarm-id) is "ALM-001", alarm name is "Base Station Signal Loss", network element name is "BTS-001", alarm location is "Chaoyang District, Beijing", alarm occurrence time (alarm-create-time) is "2025-04-05T14:28:00Z". - Alarm identifier (alarm-csn) is "ALM-20250405-002", alarm ID (alarm-id) is "ALM-002", alarm name is "Transmission Link Interruption", network element name is "TRX-002", alarm location is "Haidian District, Beijing", alarm occurrence time (alarm-create-time) is "2025-04-05T14:29:00Z".`|
	|Fault Occurrence Time| Required | Its general ontology is the time when the fault occurred.<br>Example:<br>`## Task Context `<br>`{{Related Information}}`<br>Its instantiation example is as follows:<br>`## Task Context `<br>`Fault occurrence time is "2025-04-05T14:30:00Z".` |
	| Fault Context Object | Required  | Its general ontology can be fault pre-processing information from OSS, or alarm reporting information from EMS.<br>Example:<br>`## Task Context`<br>`{{Fault Context Object}}`<br>Its instantiation example is as follows:<br>`## Task Context `<br>`Fault context object is: "Alarm Management System: FMC, Alarm Location: Beijing, Alarm Name: Base Station Signal Loss, Alarm Time: 2025-04-05T14:28:00Z, Alarm Network Element: BTS-001".` |
    
#### 1.6.2.4 Format and Specification
A2A-T requires syntax format specifications for commonly used text formats, including paragraphs, lists, and links, using Markdown format to ensure structured and readable output:
- Paragraph: Separate text blocks with blank lines
- Ordered list: Number followed by period (1. Item One)
- Unordered list: Dash prefix (- Item One)
- Link: Square brackets followed by parentheses ([Text](Link))

##### 1.6.2.4.1 Paragraph
To create a paragraph, you can use blank lines to separate one or more lines of text. Example:
```
## Task Description
Handle 5G service fault in Community A

Complete service recovery

Identify the root cause of the fault and perform repair
```

##### 1.6.2.4.2 Ordered List
To create an ordered list, add items represented by numbers followed by periods. The numbers do not need to be in sequential order, but the list should start with the number 1. Example:
```
## Expected Output 
1. Bar
2. Foo
```

##### 1.6.2.4.3 Unordered List
To create an unordered list, add a dash (-) before each line item. Indent one or more items to create a nested list. Example:
```
## Expected Output
- Item
  - Item1
- Bar2
- Foo
```
##### 1.6.2.4.4 Link
To create a link, enter the link text in square brackets, followed immediately by the URL enclosed in parentheses. Example:
```
## Task Description
Handle the issue where [TM Forum AN] (Autonomous Network Project Homepage) cannot be loaded.
```


#### 1.6.2.5 Steps for Template Definition
It is recommended to follow these steps to define prompt templates:
1.	Determine the task type: Clarify the task type and collaboration mode of the current business scenario, and locate the corresponding template category from the A2A-T task classification system (e.g., Task-T);
2.	Write necessary instructions: Select key instructions, and use a structured format to declare task objectives, execution conditions, input parameters, expected output, etc.;
3.	Fill in common variables: Declare the specific parameters involved in this task instance; variable references must follow A2A-T variable syntax specifications;
4.	Bind context information: Supplement the context information required for the agent to complete the task;
5.	Set output definition: Clearly define output format, acceptance criteria, and exception handling rules;
6.	Verify template completeness: Conduct sufficient testing in actual cross-model environments to verify syntax compliance and cross-LLM compatibility;
7.	Version iteration optimization: Incorporate validated templates into the version management system for continuous governance, iteration, and evolution.