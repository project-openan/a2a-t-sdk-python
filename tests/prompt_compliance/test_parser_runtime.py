from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class ProcessedPromptParserRuntimeTest(unittest.TestCase):
    def test_parse_returns_identity_from_new_front_matter_fields(self) -> None:
        from a2a_t.server.prompt_compliance.parser import ProcessedPromptParser

        parser = ProcessedPromptParser()
        identity = parser.parse(
            "---\nscenario_code: energy_saving\nlanguage: en-US\nversion: 0.0.1\ndescription: Used for energy saving analysis.\n---\nbody"
        )

        self.assertEqual(identity.scenario_code, "energy_saving")
        self.assertEqual(identity.language, "en-US")
        self.assertEqual(identity.version, "0.0.1")

    def test_parse_rejects_missing_scenario_code(self) -> None:
        from a2a_t.server.prompt_compliance.errors import ProcessedPromptParseError
        from a2a_t.server.prompt_compliance.parser import ProcessedPromptParser

        parser = ProcessedPromptParser()

        with self.assertRaises(ProcessedPromptParseError):
            parser.parse("---\nlanguage: en-US\nversion: 0.0.1\n---\nbody")


if __name__ == "__main__":
    unittest.main()
