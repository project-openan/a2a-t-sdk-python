from __future__ import annotations

import json

from .models import NormalizedInput


class InputNormalizer:
    def normalize(self, user_input: str | dict[str, object]) -> NormalizedInput:
        if isinstance(user_input, str):
            if not user_input.strip():
                raise ValueError("user_input must not be empty.")
            parsed_json = self._try_parse_json_object(user_input)
            if parsed_json is not None:
                return NormalizedInput(input_kind="json", normalized_input=user_input)
            return NormalizedInput(input_kind="natural_language", normalized_input=user_input)

        if isinstance(user_input, dict):
            if not user_input:
                raise ValueError("user_input dict must not be empty.")
            return NormalizedInput(
                input_kind="json",
                normalized_input=json.dumps(user_input, ensure_ascii=False),
            )

        raise TypeError("user_input must be str or dict.")

    def _try_parse_json_object(self, user_input: str) -> dict[str, object] | None:
        try:
            parsed = json.loads(user_input)
        except json.JSONDecodeError:
            return None

        if not isinstance(parsed, dict):
            return None
        return parsed
