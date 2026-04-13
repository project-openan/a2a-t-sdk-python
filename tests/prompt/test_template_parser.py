from __future__ import annotations

import importlib
import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class TemplateParserRemovalTest(unittest.TestCase):
    def test_prompt_resources_parser_module_is_not_importable(self) -> None:
        with self.assertRaises(ModuleNotFoundError):
            importlib.import_module("a2a_t.prompt.resources.parser")

    def test_prompt_package_stops_exporting_parser_runtime(self) -> None:
        package = importlib.import_module("a2a_t.prompt")

        self.assertFalse(hasattr(package, "MarkdownPromptParser"))
        self.assertFalse(hasattr(package, "PromptParser"))
        self.assertFalse(hasattr(package, "PromptParserRegistry"))
        self.assertFalse(hasattr(package, "build_default_prompt_parser_registry"))


if __name__ == "__main__":
    unittest.main()
