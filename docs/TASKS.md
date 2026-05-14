# TASKS

## Task: Prompt Compaction Experiment Design

- Status: Pending User Review
- Goal: Design and document an experiment that compares prompt generation approach A and approach B for fixed-format output preservation and token reduction.

### Subtasks

1. Explore current prompt generation and rendering flow
   - Status: Completed
2. Brainstorm candidate experiment approaches and constraints
   - Status: Completed
3. Write the experiment design spec
   - Status: Completed
4. Self-review the spec for consistency and ambiguity
   - Status: Completed
5. Ask the user to review the written spec before planning
   - Status: In Progress

## Task: Align Slot JSON Schemas For Business Scenarios

- Status: In Progress
- Goal: Align the packaged `slot.json` resources for `energy_saving` and `private_line_complaint` with the standard JSON Schema format used by `subscribe_incident`.

### Subtasks

1. Compare the reference schema and target scenario slot resources
   - Status: Completed
2. Add regression tests for packaged slot schema resources
   - Status: Completed
3. Convert `energy_saving` slot resource to standard JSON Schema
   - Status: Completed
4. Convert `private_line_complaint` slot resource to standard JSON Schema
   - Status: Completed
5. Run targeted unit tests for prompt resource loading
   - Status: Completed
6. Commit and push the slot schema alignment changes
   - Status: In Progress

## Task: Expand Client Server Prompt Generalization Sample Scale

- Status: Pending User Review
- Goal: Design a larger-scale generalization experiment for the current three business scenarios to validate client prompt generation and server prompt validation under broader paraphrase, structure, and value variation.

### Subtasks

1. Review the current 24-case generalization assets and execution pattern
   - Status: Completed
2. Define expansion dimensions, grouping strategy, and sample scale
   - Status: Completed
3. Write the large-scale experiment design spec
   - Status: Completed
4. Self-review the spec for consistency, scope, and ambiguity
   - Status: Completed
5. Ask the user to review the written spec before planning
   - Status: In Progress

## Task: Build SDK Release 0.1.5 In New Worktree

- Status: Completed
- Goal: Create an isolated git worktree for the `0.1.5` SDK release, update release version metadata, verify unit tests and package build, then push the release branch.

### Subtasks

1. Inspect current release baseline and version touchpoints
   - Status: Completed
2. Create a new isolated worktree and release branch for `0.1.5`
   - Status: Completed
3. Add or update unit tests for release version metadata
   - Status: Completed
4. Update SDK version metadata and release docs to `0.1.5`
   - Status: Completed
5. Run targeted unit tests and package build in the new worktree
   - Status: Completed
6. Commit and push the `0.1.5` release branch
   - Status: In Progress
