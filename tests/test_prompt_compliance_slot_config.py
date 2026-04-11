from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


from a2a_t.server.prompt_compliance.errors import SlotSchemaLoadError, SlotSchemaValidationError
from a2a_t.server.prompt_compliance.models import PromptIdentity, SlotSchemaConfig
from a2a_t.server.prompt_compliance.schema_builder import SlotSchemaBuilder
from a2a_t.server.prompt_compliance.slot_schema import SlotSchemaResolver
from a2a_t.server.prompt_compliance.validator import SlotValidator
from tests.test_support import ManagedTempDirTestCase


class SlotSchemaResolverTest(ManagedTempDirTestCase):
    def setUp(self) -> None:
        super().setUp()
        self.cache_root = self.make_temp_dir("slot_config")
        self.identity = PromptIdentity(name="network diagnosis", language="zh-CN", version="1.0.0")
        self.config = SlotSchemaConfig(root_dir=str(self.cache_root), slot_root_name="slots", file_name="slot.json")
        self.resolver = SlotSchemaResolver(self.config)

    def test_resolve_path_builds_mirrored_slot_location(self) -> None:
        path = self.resolver.resolve_path(self.identity)

        expected = self.cache_root / "slots" / "network diagnosis" / "1.0.0" / "zh-CN" / "slot.json"
        self.assertEqual(path, expected)

    def test_resolver_creates_slot_root_directory_on_init(self) -> None:
        self.assertTrue((self.cache_root / "slots").exists())

    def test_load_reads_and_validates_slot_json(self) -> None:
        slot_path = self.resolver.resolve_path(self.identity)
        slot_path.parent.mkdir(parents=True, exist_ok=True)
        slot_path.write_text(
            """
{
  "prompt_identity": {
    "name": "network diagnosis",
    "language": "zh-CN",
    "version": "1.0.0"
  },
  "slots": [
    {
      "name": "device_type",
      "required": true,
      "type": "string"
    },
    {
      "name": "operation",
      "required": true,
      "type": "enum",
      "allowed_values": ["query", "restart"]
    }
  ],
  "rules": [
    {
      "type": "dependency",
      "when": {
        "slot": "operation",
        "equals": "restart"
      },
      "requires": ["device_type"]
    }
  ]
}
""".strip() + "\n",
            encoding="utf-8",
        )

        slot_schema = self.resolver.load(self.identity)

        self.assertEqual(slot_schema.prompt_identity.name, "network diagnosis")
        self.assertEqual(slot_schema.slots[0].name, "device_type")
        self.assertEqual(slot_schema.slots[1].allowed_values, ["query", "restart"])
        self.assertEqual(slot_schema.rules[0].requires, ["device_type"])

    def test_load_rejects_invalid_slot_json(self) -> None:
        slot_path = self.resolver.resolve_path(self.identity)
        slot_path.parent.mkdir(parents=True, exist_ok=True)
        slot_path.write_text(
            """
{
  "prompt_identity": {
    "name": "network diagnosis",
    "language": "zh-CN",
    "version": "1.0.0"
  },
  "slots": [
    {
      "name": "operation",
      "type": "enum"
    }
  ]
}
""".strip() + "\n",
            encoding="utf-8",
        )

        with self.assertRaises(SlotSchemaValidationError):
            self.resolver.load(self.identity)

    def test_load_raises_when_slot_json_is_missing(self) -> None:
        with self.assertRaises(SlotSchemaLoadError):
            self.resolver.load(self.identity)


class SlotSchemaBuilderTest(unittest.TestCase):
    def setUp(self) -> None:
        self.builder = SlotSchemaBuilder()

    def test_build_maps_required_enum_pattern_and_dependency_rules(self) -> None:
        slot_schema = SlotSchemaResolver.build_slot_schema(
            {
                "prompt_identity": {
                    "name": "network diagnosis",
                    "language": "zh-CN",
                    "version": "1.0.0",
                },
                "slots": [
                    {"name": "device_type", "required": True, "type": "string", "pattern": "^router|switch$"},
                    {"name": "operation", "required": True, "type": "enum", "allowed_values": ["query", "restart"]},
                    {"name": "timeout", "required": False, "type": "number", "range": {"min": 0, "max": 30}},
                ],
                "rules": [
                    {
                        "type": "dependency",
                        "when": {"slot": "operation", "equals": "restart"},
                        "requires": ["device_type"],
                    }
                ],
            }
        )

        schema = self.builder.build(slot_schema)

        self.assertEqual(schema["type"], "object")
        self.assertEqual(schema["required"], ["device_type", "operation"])
        self.assertEqual(schema["properties"]["operation"]["enum"], ["query", "restart"])
        self.assertEqual(schema["properties"]["timeout"]["minimum"], 0)
        self.assertEqual(schema["properties"]["timeout"]["maximum"], 30)
        self.assertEqual(schema["allOf"][0]["if"]["properties"]["operation"]["const"], "restart")
        self.assertEqual(schema["allOf"][0]["then"]["required"], ["device_type"])


class SlotValidatorTest(unittest.TestCase):
    def setUp(self) -> None:
        self.slot_schema = SlotSchemaResolver.build_slot_schema(
            {
                "prompt_identity": {
                    "name": "network diagnosis",
                    "language": "zh-CN",
                    "version": "1.0.0",
                },
                "slots": [
                    {"name": "device_type", "required": True, "type": "string"},
                    {"name": "operation", "required": True, "type": "enum", "allowed_values": ["query", "restart"]},
                ],
                "rules": [
                    {
                        "type": "dependency",
                        "when": {"slot": "operation", "equals": "restart"},
                        "requires": ["device_type"],
                    }
                ],
            }
        )
        self.validator = SlotValidator()

    def test_validate_returns_success_for_matching_slots(self) -> None:
        result = self.validator.validate(
            extracted_slots={"device_type": "router", "operation": "restart"},
            slot_schema=self.slot_schema,
        )

        self.assertTrue(result.valid)
        self.assertEqual(result.errors, [])

    def test_validate_returns_errors_for_dependency_violation(self) -> None:
        result = self.validator.validate(
            extracted_slots={"operation": "restart"},
            slot_schema=self.slot_schema,
        )

        self.assertFalse(result.valid)
        self.assertGreaterEqual(len(result.errors), 1)


if __name__ == "__main__":
    unittest.main()
