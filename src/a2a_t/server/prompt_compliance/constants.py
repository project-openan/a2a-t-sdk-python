from __future__ import annotations

GUARDRAIL_REJECTED = "guardrail_rejected"
GUARDRAIL_EXECUTION_ERROR = "guardrail_execution_error"
PROMPT_RESOURCE_PARSE_ERROR = "prompt_resource_parse_error"
PROMPT_RESOURCE_ACCESS_ERROR = "prompt_resource_access_error"

GUARDRAIL_STAGE = "guardrail"
GENERATION_STAGE = "generation"
PROMPT_PARSE_STAGE = "prompt_parse"
SLOT_EXTRACTION_STAGE = "slot_extraction"
SLOT_VALIDATION_STAGE = "slot_validation"
PASSED_STAGE = "passed"

PROCESSED_PROMPT_PARSE_ERROR = "processed_prompt_parse_error"
TEMPLATE_LOAD_ERROR = "template_load_error"
SLOT_SCHEMA_LOAD_ERROR = "slot_schema_load_error"
PROMPT_RESOURCE_LOAD_ERROR = "prompt_resource_load_error"
SLOT_EXTRACTION_ERROR = "slot_extraction_error"
SLOT_VALIDATION_ERROR = "slot_validation_error"
