import sys
import json
from pathlib import Path

sys.path.insert(0, "src")

from a2a_t.config.models import A2ATConfig, PromptRuntimeConfig, PromptComplianceConfig, GuardrailProviderConfig
from a2a_t.llm.client import LLMClient
from a2a_t.client.prompt_generation.prompt_generation_orchestrator_builder import PromptGenerationOrchestratorBuilder


def generate_incident_subscription_prompt(raw_input):
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parents[2]
    prompt_resources_dir = project_root / "package_data" / "prompt_resources"
    
    prompt_config = PromptRuntimeConfig(
        language="zh-CN",
        prompt_resource_version="0.0.1",
        source_type="local_file",
        local_root_dir=str(prompt_resources_dir),
    )
    
    guardrail_config = GuardrailProviderConfig(
        provider="noop",
        timeout=10.0,
    )
    
    compliance_config = PromptComplianceConfig(
        enabled=False,
        guardrail=guardrail_config,
    )
    
    config = A2ATConfig(
        prompt=prompt_config,
        prompt_compliance=compliance_config,
    )
    
    print(f"Language: {config.prompt.language}")
    print(f"Prompt resource version: {config.prompt.prompt_resource_version}")
    print(f"Local root dir: {config.prompt.local_root_dir}")
    print()
    
    llm_client = LLMClient()
    
    builder = PromptGenerationOrchestratorBuilder()
    orchestrator = builder.build(config=config, llm_client=llm_client)
    
    print(f"User input: {raw_input}")
    print()
    print("=" * 60)
    print("Generating prompt...")
    print("=" * 60)
    print()
    
    result = orchestrator.generate(raw_input)
    
    print("Result:")
    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    
    if result.success and result.prompt_text:
        print()
        print("=" * 60)
        print("Generated Prompt Text:")
        print("=" * 60)
        print(result.prompt_text)
        
        output_file = project_root / "output" / "generated_prompt.md"
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(result.prompt_text, encoding="utf-8")
        print()
        print(f"Prompt saved to: {output_file}")


if __name__ == "__main__":
    user_input = "我想订阅 eth-los 和 光纤中断 这两种故障，级别为 严重 和 高"

    generate_incident_subscription_prompt(user_input)