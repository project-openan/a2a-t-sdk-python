# Subscribe Incident Prompt Evaluation Design

**Goal:** Build a large zh-CN evaluation set for client-side prompt generation focused on `subscribe_incident`, plus a runnable script that executes the cases and reports recognition, slot, and prompt-effect accuracy.

**Scope:** This design covers only the built-in `subscribe_incident` scenario. Inputs are primarily Chinese, with a small amount of preserved domain English such as `Incident`, `critical`, `major`, and `DataPart`.

## Background

The SDK currently ships a single built-in scenario, `subscribe_incident`. Existing assets already separate successful generation, unrecognized input, resource failures, and post-generation validation failures. The new evaluation set should measure real prompt-generation quality rather than only checking a few golden examples.

The dataset must support two independent failure categories:

1. Scenario recognition failure
2. Scenario recognized correctly, but slot extraction or rendering misses core content

## Evaluation Targets

Each case evaluates two layers:

1. **Structured expectation**
   - Whether the input should be recognized as `subscribe_incident`
   - Which semantic slot values must be present
   - Which fields may be absent without counting as a recognition failure
   - Which failure type applies when generation should not succeed
2. **Prompt effect expectation**
   - The generated prompt does not need to match expected text exactly
   - Core semantics and key points must still be present
   - Minor wording differences are acceptable

## Dataset Size And Composition

The first batch should contain **160 cases**.

### Case Type Distribution

- `positive_complete`: 50
- `positive_partial`: 30
- `positive_recognized_but_slot_risky`: 25
- `negative_near_intent`: 20
- `negative_non_incident_subscription`: 20
- `negative_ambiguous`: 15

### Semantic Variant Labels

Each case carries one or more labels from:

- `topic_expression`
- `condition_fault_name`
- `condition_severity`
- `condition_combination`
- `report_format`
- `subscription_target_context`
- `noise_interference`
- `instruction_purity`

### Completeness Labels

- `L1`: only the subscription target or theme is clear
- `L2`: subscription plus one or more conditions
- `L3`: subscription, conditions, and report format
- `L4`: subscription, multiple conditions, and additional context

## Case Schema

Each case must contain:

- `id`
- `input`
- `scenario_description`
- `expected_result`
- `expected_prompt_effect`
- `tags`

### expected_result

- `should_recognize`
- `expected_scenario_code`
- `expected_slots`
- `allowed_missing_slots`
- `failure_type`

### expected_prompt_effect

- `must_include_points`
- `preferred_keywords`
- `must_not_include_points`

### tags

- `case_type`
- `semantic_variant`
- `completeness_level`

## Recognition Rules

### Recognition Success

A case counts as recognized successfully when:

- `should_recognize = true`
- the model output indicates success

### Current SDK Limitation

The current client API returns only the rendered prompt body, not prompt front matter or intermediate scenario-resolution data. For this evaluation, recognition is therefore inferred indirectly:

- success on a positive case is treated as a proxy for correct `subscribe_incident` recognition
- success on a negative case is treated as a false positive for `subscribe_incident`

This proxy is acceptable for this batch because the SDK currently ships only one built-in scenario.

### Recognition Failures

- `scenario_false_negative`: the case should be recognized as `subscribe_incident`, but generation does not succeed
- `scenario_false_positive`: the case should not be recognized, but generation succeeds

## Slot And Content Rules

The current scenario is evaluated against three semantic slot groups:

- `通知主题`
- `订阅条件`
- `上报通知数据格式`

Because the public generation API only returns the final prompt, the evaluation treats slot accuracy as a semantic check against the rendered prompt body.

### Slot Outcome Levels

- `slot_exact`: all expected semantic content is reflected
- `slot_partial`: the scenario is recognized, but some expected optional or secondary content is missing
- `slot_wrong`: the scenario is recognized, but required semantic content is missing or wrong

### Prompt Effect Outcome Levels

- `prompt_good`: all required points are present and no forbidden semantics appear
- `prompt_acceptable`: core points are mostly present, with only minor wording differences or secondary omissions
- `prompt_bad`: required points are missing or incorrect semantics appear

## Negative Case Policy

Cases that should fail recognition come from two groups:

1. Inputs near the target intent but not asking for subscription generation
   - explanation
   - translation
   - rewrite
   - summary
   - quality check
2. Inputs that ask for subscription, but not `subscribe_incident`
   - other notification subjects
   - generic alarms without landing on incident
   - status, performance, logs, or other non-incident events

Ambiguous inputs are included separately to measure over-guessing.

## Positive Case Policy

Positive cases are intentionally mixed:

- Some provide complete information
- Some omit report format
- Some provide only incident topic plus partial conditions
- Some contain longer, noisier descriptions while still clearly requesting subscription

This allows separate reporting for:

- recognition accuracy
- recognized-but-incomplete content rate
- prompt core point hit rate

## Generation Rules

To avoid repetitive data, the dataset must vary both vocabulary and sentence structure.

### Sentence Style Mix

- direct instruction
- task-style description
- requirement statement
- colloquial request
- background plus request
- request with side requirements

### Positive Variation Axes

- topic expression variants around `Incident`
- fault name expression
- severity expression
- multiple conditions joined in different orders
- report format placement and wording
- subscription target context such as base station, network element, or site

### Negative Variation Axes

- pure explanation or translation tasks
- subscriptions to non-incident subjects
- short ambiguous commands
- mixed instructions with unclear main intent

## Metrics

The runner must report at least:

- overall scenario recognition accuracy
- positive-case recognition accuracy
- negative-case false positive rate
- slot semantic accuracy within recognized positive cases
- prompt core point hit rate within recognized positive cases
- accuracy by `case_type`
- accuracy by `semantic_variant`
- accuracy by `completeness_level`

## Deliverables

The implementation should produce:

1. A machine-readable case file
2. A human-readable markdown summary derived from that case file
3. A runnable evaluation script
4. A generated report showing aggregate metrics and sample failures

## Constraints

- No exact prompt-text golden matching
- Minimal implementation is preferred over reusable framework design
- No unit tests are required for the new script
- Files do not need to be committed
