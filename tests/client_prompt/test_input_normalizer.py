from __future__ import annotations

import sys
from pathlib import Path
import unittest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = PROJECT_ROOT / "src"

if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))


class InputNormalizerTest(unittest.TestCase):
    def test_normalize_string_input_returns_natural_language_kind(self) -> None:
        from a2a_t.client.prompt_generation.input_normalizer import InputNormalizer

        normalizer = InputNormalizer()
        result = normalizer.normalize("Analyze Site A energy usage.")

        self.assertEqual(result.input_kind, "natural_language")
        self.assertEqual(result.normalized_input, "Analyze Site A energy usage.")

    def test_normalize_dict_input_returns_json_kind(self) -> None:
        from a2a_t.client.prompt_generation.input_normalizer import InputNormalizer

        normalizer = InputNormalizer()
        result = normalizer.normalize({"site": "Site A", "time_range": "2026-04-01 to 2026-04-07"})

        self.assertEqual(result.input_kind, "json")
        self.assertEqual(result.normalized_input, '{"site": "Site A", "time_range": "2026-04-01 to 2026-04-07"}')

    def test_normalize_json_object_string_returns_json_kind(self) -> None:
        from a2a_t.client.prompt_generation.input_normalizer import InputNormalizer

        normalizer = InputNormalizer()
        result = normalizer.normalize('{"site": "Site A"}')

        self.assertEqual(result.input_kind, "json")
        self.assertEqual(result.normalized_input, '{"site": "Site A"}')

    def test_normalize_non_object_json_string_keeps_natural_language_kind(self) -> None:
        from a2a_t.client.prompt_generation.input_normalizer import InputNormalizer

        normalizer = InputNormalizer()
        result = normalizer.normalize('["site-a", "site-b"]')

        self.assertEqual(result.input_kind, "natural_language")
        self.assertEqual(result.normalized_input, '["site-a", "site-b"]')

    def test_normalize_empty_string_raises_value_error(self) -> None:
        from a2a_t.client.prompt_generation.input_normalizer import InputNormalizer

        normalizer = InputNormalizer()

        with self.assertRaises(ValueError):
            normalizer.normalize("   ")

    def test_normalize_empty_dict_raises_value_error(self) -> None:
        from a2a_t.client.prompt_generation.input_normalizer import InputNormalizer

        normalizer = InputNormalizer()

        with self.assertRaises(ValueError):
            normalizer.normalize({})

    def test_normalize_non_string_non_dict_raises_type_error(self) -> None:
        from a2a_t.client.prompt_generation.input_normalizer import InputNormalizer

        normalizer = InputNormalizer()

        with self.assertRaises(TypeError):
            normalizer.normalize(123)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
