from __future__ import annotations

from pathlib import Path
from typing import Any

from a2a_t.config.errors import ConfigFileNotFoundError
from a2a_t.config.source import DotEnvConfigSource

from .in_memory import InMemoryNegotiationStateStore


class NegotiationStateStoreFactory:
    def build(
        self,
        *,
        env_path: str | Path | None = None,
        logger: Any | None = None,
    ) -> InMemoryNegotiationStateStore:
        if env_path is None:
            return InMemoryNegotiationStateStore()

        try:
            values = DotEnvConfigSource.load(Path(env_path))
        except ConfigFileNotFoundError:
            self._warning(logger, "Negotiation state store env file is missing. Falling back to in_memory store.")
            return InMemoryNegotiationStateStore()
        except Exception:
            self._warning(logger, "Negotiation state store config is invalid. Falling back to in_memory store.")
            return InMemoryNegotiationStateStore()

        store_type = values.get("A2AT_NEGOTIATION_STATE_STORE_TYPE", "in_memory").strip() or "in_memory"
        if store_type == "in_memory":
            return InMemoryNegotiationStateStore()

        self._warning(
            logger,
            "Negotiation state store type %s is unsupported. Falling back to in_memory store.",
            store_type,
        )
        return InMemoryNegotiationStateStore()

    @staticmethod
    def _warning(logger: Any | None, message: str, *args: object) -> None:
        if logger is not None and hasattr(logger, "warning"):
            logger.warning(message, *args)
