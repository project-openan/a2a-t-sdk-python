from __future__ import annotations

import json
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class SubscribeIncidentSlotSchemaConstraintsTest(unittest.TestCase):
    def test_subscribe_incident_slot_schema_keeps_optional_condition_and_has_mapping_hint(self) -> None:
        slot_json_path = (
            PROJECT_ROOT
            / "package_data"
            / "prompt_resources"
            / "slots"
            / "subscribe_incident"
            / "zh-CN"
            / "slot.json"
        )
        payload = json.loads(slot_json_path.read_text(encoding="utf-8"))

        required = payload.get("required")
        self.assertIsInstance(required, list)
        self.assertNotIn("订阅条件", required, "订阅条件 should remain optional")

        properties = payload.get("properties")
        self.assertIsInstance(properties, dict)
        condition_schema = properties.get("订阅条件")
        self.assertIsInstance(condition_schema, dict)
        self.assertIn("x-a2at-value-constraint", condition_schema)
        constraint = condition_schema["x-a2at-value-constraint"]
        self.assertIsInstance(constraint, str)
        self.assertIn("语义支持中英文同义表达", constraint)
        self.assertIn("紧急/critical", constraint)
        self.assertIn("高/high", constraint)
        self.assertIn("中/medium", constraint)
        self.assertIn("低/low", constraint)
        self.assertIn("中文或英文任一命中即可视为有效", constraint)
        self.assertIn("取值范围为网络侧故障名称列表", constraint)


if __name__ == "__main__":
    unittest.main()
