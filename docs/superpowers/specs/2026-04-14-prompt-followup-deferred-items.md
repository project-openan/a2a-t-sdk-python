# Prompt Follow-up Deferred Items

## Context
This file records prompt follow-up items that were reviewed on 2026-04-14 but explicitly deferred by the user for a later slice.

## Deferred Items

### 2. LLM retry strategy
- Status: deferred
- Reason: user requested not to implement retries in this slice.
- Current state:
  - `PromptGenerationOrchestrator` still returns `LLM_EXECUTION_FAILED` directly on execution exceptions.
  - No per-stage retry wrapper exists for scenario recognition or slot extraction.
- Next slice suggestion:
  - add TDD coverage for retryable vs non-retryable failures
  - implement a shared per-stage retry helper inside the orchestrator

### 3. Client result model rollback to the older design shape
- Status: deferred
- Reason: user stated the older design document is outdated.
- Current state:
  - `PromptGenerationResult` remains flattened with `scenario_code`
  - `ValidationResult.missing_required_fields` remains derived from `slot_errors`
- Next slice suggestion:
  - do nothing unless a new agreed result contract replaces the current flattened shape

### 7. Explicit `catalog / provider / cache` assembly inside `PromptRuntimeComponentsBuilder`
- Status: deferred
- Reason: user requested not to change this in the current slice.
- Current state:
  - `PromptRuntimeComponentsBuilder` still instantiates `LocalPromptResourceSource(...)`
  - `LocalPromptResourceSource` still wires default `LocalPromptResourceCatalog` and `LocalPromptResourceProvider` internally
- Next slice suggestion:
  - add builder-level tests that lock explicit dependency assembly
  - move `catalog/provider` creation from `LocalPromptResourceSource` defaults into the builder

## Scope Guardrail
- Deferred items listed here were intentionally excluded from the 2026-04-14 implementation scope.
- Any future work should re-confirm scope before changing them.
