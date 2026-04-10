from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.prompt.cache import LocalFilePromptStore
from a2a_t.prompt.catalog import LocalPromptCatalog
from a2a_t.prompt.config import PromptLoaderConfig
from a2a_t.prompt.loader import PromptLoader
from a2a_t.prompt.parser import MarkdownPromptParser
from a2a_t.prompt.providers import LocalFileProvider
from a2a_t.server.prompt_compliance.errors import GuardrailRejectedError
from a2a_t.server.prompt_compliance.extractor import PromptSlotExtractor
from a2a_t.server.prompt_compliance.guardrails import SafetyGuardrailFactory
from a2a_t.server.prompt_compliance.models import PromptComplianceProviderConfig, SlotSchemaConfig
from a2a_t.server.prompt_compliance.origin_resolver import PromptOriginResolver
from a2a_t.server.prompt_compliance.parser import ProcessedPromptParser
from a2a_t.server.prompt_compliance.service import PromptComplianceService
from a2a_t.server.prompt_compliance.slot_config import SlotConfigResolver
from a2a_t.server.prompt_compliance.validator import SlotValidator
from a2a_t.server.prompt_handler import PromptHandler
from tests.test_support import ManagedTempDirTestCase, build_markdown


class FakeCatalogRegistry:
    def __init__(self, catalogs: dict[str, object]) -> None:
        self._catalogs = catalogs

    def list_catalogs(self) -> dict[str, object]:
        return self._catalogs


class FakeStructuredAdapter:
    def __init__(self, response_text: str) -> None:
        self._response_text = response_text

    def structured(self, *, messages: list[dict[str, str]], json_schema: dict[str, object], **kwargs: object) -> object:
        return type(
            "LLMResponseLike",
            (),
            {
                "content": self._response_text,
                "model": "fake",
                "usage": {},
                "metadata": {},
            },
        )()


class PromptComplianceIntegrationTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.temp_root = self.make_temp_dir("prompt_compliance_integration")
        self.cache_root = self.temp_root / "cache"
        self.prompts_root = self.temp_root / "prompts"
        self.prompt_dir = self.prompts_root / "network diagnosis" / "1.0.0" / "zh-CN"
        self.prompt_dir.mkdir(parents=True, exist_ok=True)
        self.prompt_path = self.prompt_dir / "prompt.md"
        self.prompt_path.write_text(
            build_markdown(
                name="network diagnosis",
                language="zh-CN",
                version="1.0.0",
                title="Network Diagnosis",
                description="Diagnose network issues.",
                body="original prompt body",
            ),
            encoding="utf-8",
        )

    def _build_service(self, *, adapter_response: str, slot_policy: str = "strict") -> PromptComplianceService:
        catalog = LocalPromptCatalog(prompt_dir=str(self.prompts_root))
        loader = PromptLoader(
            config=PromptLoaderConfig(default_ttl=timedelta(hours=1), cache_dir=str(self.cache_root)),
            parser=MarkdownPromptParser(),
            cache_store=LocalFilePromptStore(self.cache_root),
            providers={"local_file": LocalFileProvider()},
            now_provider=lambda: datetime(2026, 4, 10, 12, 0, tzinfo=timezone.utc),
        )
        resolver = PromptOriginResolver(
            catalog_registry=FakeCatalogRegistry({"local": catalog}),
            prompt_loader=loader,
        )
        extractor = PromptSlotExtractor(adapter=FakeStructuredAdapter(adapter_response))
        guardrail = SafetyGuardrailFactory.create(PromptComplianceProviderConfig(provider="noop"))
        return PromptComplianceService(
            guardrail=guardrail,
            parser=ProcessedPromptParser(),
            origin_resolver=resolver,
            extractor=extractor,
            slot_config_resolver=SlotConfigResolver(
                SlotSchemaConfig(root_dir=str(self.cache_root), slot_root_name="slots", file_name="slot.yaml")
            ),
            validator=SlotValidator(),
            slot_not_found_policy=slot_policy,
        )

    def _write_slot_yaml(self, content: str) -> None:
        slot_dir = self.cache_root / "slots" / "network diagnosis" / "1.0.0" / "zh-CN"
        slot_dir.mkdir(parents=True, exist_ok=True)
        (slot_dir / "slot.yaml").write_text(content, encoding="utf-8")

    def test_handler_process_succeeds_with_real_components(self) -> None:
        self._write_slot_yaml(
            """
prompt_identity:
  name: "network diagnosis"
  language: "zh-CN"
  version: "1.0.0"
slots:
  - name: "device_type"
    required: true
    type: "string"
rules: []
""".strip()
        )
        service = self._build_service(
            adapter_response='{"slots": {"device_type": "router"}, "notes": ["ok"], "confidence": 0.9}'
        )
        handler = PromptHandler(validator=service)

        result = handler.process(
            "task-1",
            {
                "processed_prompt_text": "---\nname: network diagnosis\nlanguage: zh-CN\nversion: 1.0.0\n---\nprocessed body"
            },
        )

        self.assertTrue(result["passed"])
        self.assertEqual(result["stage"], "passed")
        self.assertEqual(result["extracted_slots"], {"device_type": "router"})

    def test_handler_process_returns_slot_validation_error_for_dependency_rule(self) -> None:
        self._write_slot_yaml(
            """
prompt_identity:
  name: "network diagnosis"
  language: "zh-CN"
  version: "1.0.0"
slots:
  - name: "operation"
    required: true
    type: "enum"
    allowed_values:
      - "query"
      - "restart"
  - name: "location"
    required: false
    type: "string"
rules:
  - type: "dependency"
    when:
      slot: "operation"
      equals: "restart"
    requires:
      - "location"
""".strip()
        )
        service = self._build_service(
            adapter_response='{"slots": {"operation": "restart"}, "notes": ["ok"], "confidence": 0.9}'
        )
        handler = PromptHandler(validator=service)

        result = handler.process(
            "task-1",
            {
                "processed_prompt_text": "---\nname: network diagnosis\nlanguage: zh-CN\nversion: 1.0.0\n---\nprocessed body"
            },
        )

        self.assertFalse(result["passed"])
        self.assertEqual(result["stage"], "slot_validation")
        self.assertEqual(result["error_code"], "slot_validation_error")

    def test_service_normalizes_guardrail_rejected_error(self) -> None:
        class RejectingGuardrail:
            def check(self, prompt_text: str, context: dict[str, object] | None = None) -> object:
                raise GuardrailRejectedError("rejected by guardrail policy")

        service = PromptComplianceService(
            guardrail=RejectingGuardrail(),
            parser=ProcessedPromptParser(),
            origin_resolver=object(),
            extractor=object(),
            slot_config_resolver=object(),
            validator=object(),
            slot_not_found_policy="strict",
        )

        result = service.check(
            processed_prompt_text="---\nname: network diagnosis\nlanguage: zh-CN\nversion: 1.0.0\n---\nprocessed body"
        )

        self.assertEqual(result.stage, "guardrail")
        self.assertEqual(result.error_code, "guardrail_rejected")
        self.assertFalse(result.passed)


if __name__ == "__main__":
    unittest.main()
