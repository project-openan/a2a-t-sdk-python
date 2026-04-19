"""Client extensions for a2a_t."""

from a2a_t.client.a2at_client import A2ATClient
from a2a_t.client.extended_client import ExtendedClient
from a2a_t.client.prompt_client import PromptClient
from a2a_t.client.compression_client import CompressionClient

__all__ = ["A2ATClient", "ExtendedClient", "PromptClient", "CompressionClient"]
