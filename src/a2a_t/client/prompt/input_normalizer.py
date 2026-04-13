from __future__ import annotations

import json

from .models import NormalizedInput


class InputNormalizer:
    def normalize(self, user_input: str | dict[str, object]) -> NormalizedInput:
        if isinstance(user_input, str):
            if not user_input.strip():
                raise ValueError("user_input must not be empty.")
            return NormalizedInput(input_kind="natural_language", normalized_input=user_input)

        if isinstance(user_input, dict):
            return NormalizedInput(
                input_kind="json",
                normalized_input=json.dumps(user_input, ensure_ascii=False),
            )

        raise TypeError("user_input must be str or dict.")
