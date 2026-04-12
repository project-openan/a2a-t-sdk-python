from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_prompt_compliance_env_files_include_default_settings() -> None:
    required_lines = {
        "A2AT_PROMPT_COMPLIANCE_ENABLED=false",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_PROVIDER=noop",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_TIMEOUT_SECONDS=10",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_POLICY_ID=",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_ENDPOINT=",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_REGION=",
        "A2AT_PROMPT_COMPLIANCE_GUARDRAIL_CREDENTIALS_REF=",
        "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_PROVIDER=",
        "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MODEL=",
        "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TIMEOUT_SECONDS=30",
        "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_TEMPERATURE=0",
        "A2AT_PROMPT_COMPLIANCE_SLOT_EXTRACTION_MAX_RETRIES=2",
        "A2AT_PROMPT_COMPLIANCE_SLOT_LOCAL_DIR=./slots",
        "A2AT_PROMPT_COMPLIANCE_SLOT_FILE_NAME=slot.json",
        "A2AT_PROMPT_COMPLIANCE_SLOT_NOT_FOUND_POLICY=strict",
    }

    for env_file_name in [".env", "env.example"]:
        env_lines = set((PROJECT_ROOT / "package_data" / env_file_name).read_text(encoding="utf-8").splitlines())

        assert required_lines.issubset(env_lines)


def test_prompt_compliance_default_slot_directory_is_preserved() -> None:
    assert (PROJECT_ROOT / "package_data" / "slots" / ".gitkeep").is_file()


def test_readme_uses_prompt_compliance_current_names() -> None:
    readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")

    assert "slot.json" in readme
    assert "GuardrailProviderConfig" in readme
    assert "google_model_armor" in readme
    assert "google-cloud-modelarmor" in readme
    assert "AWS / Azure" in readme
    assert "未实现" in readme
    assert "SlotSchemaResolver" in readme
    assert "PromptComplianceProviderConfig" not in readme
    assert "slot.yaml" not in readme
