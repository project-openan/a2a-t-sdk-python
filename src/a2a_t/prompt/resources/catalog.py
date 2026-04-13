from __future__ import annotations

import json
from pathlib import Path
from typing import Protocol
from urllib import request
from urllib.parse import urljoin

try:
    from a2a.types import AgentCard
except ModuleNotFoundError:
    class AgentCard:  # pragma: no cover - optional dependency fallback
        pass

from ..common.config import PromptLoaderConfig
from ..common.errors import PromptSourceError
from ..common.models import CacheStatus, PromptReference, PromptSource
from .parser import PromptParser, PromptParserRegistry, build_default_prompt_parser_registry


class UrlIndexFetcher(Protocol):
    """Fetch the JSON index for a URL-backed prompt catalog."""

    def __call__(self, index_url: str) -> dict[str, object]: ...


class PromptCatalog(Protocol):
    """List all available prompt references from a source."""

    def list(self) -> list[PromptReference]: ...


class LocalPromptCatalog:
    """List all Markdown prompts from a local directory."""

    def __init__(
        self,
        *,
        prompt_dir: str | None = None,
        config: PromptLoaderConfig | None = None,
        parser: PromptParser | None = None,
        parser_registry: PromptParserRegistry | None = None,
        allowed_extensions: list[str] | None = None,
    ) -> None:
        resolved_prompt_dir = prompt_dir or (config.local_prompt_dir if config is not None else None)
        self._prompt_dir = Path(resolved_prompt_dir or "./prompts")
        self._parser = parser
        self._parser_registry = parser_registry or build_default_prompt_parser_registry()
        self._allowed_extensions = allowed_extensions or (config.allowed_extensions if config is not None else [".md"])

    def list(self) -> list[PromptReference]:
        references: list[PromptReference] = []

        for path in self._iter_prompt_paths():
            source = PromptSource(source_type="local_file", locator=str(path))
            prompt = self._resolve_parser(path).parse(
                content=path.read_text(encoding="utf-8"),
                source=source,
                cache_status=CacheStatus.MISS,
            )
            references.append(
                PromptReference(
                    name=prompt.name,
                    language=prompt.language,
                    version=prompt.version,
                    title=prompt.title,
                    description=prompt.description,
                    source=source,
                )
            )

        return references

    def _iter_prompt_paths(self) -> list[Path]:
        prompt_paths: set[Path] = set()
        for extension in self._allowed_extensions:
            normalized = extension if extension.startswith(".") else f".{extension}"
            prompt_paths.update(self._prompt_dir.rglob(f"*{normalized}"))
        return sorted(prompt_paths)

    def _resolve_parser(self, path: Path) -> PromptParser:
        if self._parser is not None and path.suffix == ".md":
            return self._parser
        return self._parser_registry.get_by_extension(path.suffix)


class UrlPromptCatalog:
    """List remote prompts from an index URL."""

    def __init__(self, *, index_url: str | None, fetcher: UrlIndexFetcher | None = None) -> None:
        self._index_url = index_url
        self._fetcher = fetcher or self._default_fetcher

    def list(self) -> list[PromptReference]:
        if not self._index_url:
            raise PromptSourceError("URL prompt catalog index is not configured.", source_type="url")

        payload = self._fetcher(self._index_url)
        prompts = payload.get("prompts")
        if not isinstance(prompts, list):
            raise PromptSourceError("URL prompt catalog index is invalid.", locator=self._index_url, source_type="url")

        return [self._build_reference(entry, self._index_url) for entry in prompts]

    def _default_fetcher(self, index_url: str) -> dict[str, object]:
        with request.urlopen(index_url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))

        if not isinstance(payload, dict):
            raise PromptSourceError("URL prompt catalog index is invalid.", locator=index_url, source_type="url")

        return payload

    def _build_reference(self, entry: object, base_url: str) -> PromptReference:
        if not isinstance(entry, dict):
            raise PromptSourceError("URL prompt catalog entry is invalid.", locator=base_url, source_type="url")

        url = entry.get("url")
        if not isinstance(url, str) or not url:
            raise PromptSourceError("URL prompt catalog entry is missing url.", locator=base_url, source_type="url")

        return PromptReference(
            name=self._require_text(entry, "name", base_url=base_url),
            language=self._require_text(entry, "language", base_url=base_url),
            version=self._require_text(entry, "version", base_url=base_url),
            title=self._require_text(entry, "title", base_url=base_url),
            description=self._require_text(entry, "description", base_url=base_url),
            source=PromptSource(source_type="url", locator=urljoin(base_url, url)),
        )

    def _require_text(self, entry: dict[str, object], field_name: str, *, base_url: str) -> str:
        value = entry.get(field_name)
        if not isinstance(value, str) or not value:
            raise PromptSourceError(
                f"URL prompt catalog entry is missing {field_name}.",
                locator=base_url,
                source_type="url",
                field=field_name,
            )

        return value


class AgentPromptCatalog:
    """Expand prompt references from AgentCard extensions."""

    def __init__(
        self,
        *,
        agent_cards: list[AgentCard],
        config: PromptLoaderConfig | None = None,
        default_prompt_extension_uri: str | None = None,
        prompt_extension_uri_overrides: dict[str, str] | None = None,
        default_prompt_index_url_param_key: str | None = None,
        prompt_index_url_param_key_overrides: dict[str, str] | None = None,
        fetcher: UrlIndexFetcher | None = None,
    ) -> None:
        self._agent_cards = list(agent_cards)
        self._default_prompt_extension_uri = default_prompt_extension_uri or (
            config.default_prompt_extension_uri if config is not None else None
        )
        self._prompt_extension_uri_overrides = prompt_extension_uri_overrides or (
            config.prompt_extension_uri_overrides if config is not None else {}
        )
        self._default_prompt_index_url_param_key = default_prompt_index_url_param_key or (
            config.default_prompt_index_url_param_key if config is not None else "promptIndexUrl"
        )
        self._prompt_index_url_param_key_overrides = prompt_index_url_param_key_overrides or (
            config.prompt_index_url_param_key_overrides if config is not None else {}
        )
        self._fetcher = fetcher or UrlPromptCatalog(index_url="unused")._default_fetcher

    def list(self) -> list[PromptReference]:
        references: list[PromptReference] = []

        for agent_card in self._agent_cards:
            agent_name = self._read_mapping_value(agent_card, "name")
            extension_uri = self._prompt_extension_uri_overrides.get(agent_name, self._default_prompt_extension_uri)
            if not extension_uri:
                continue

            extensions = self._read_mapping_value(agent_card, "extensions")
            if not isinstance(extensions, list):
                continue

            index_param_key = self._prompt_index_url_param_key_overrides.get(
                agent_name,
                self._default_prompt_index_url_param_key,
            )
            for extension in extensions:
                if self._read_mapping_value(extension, "uri") != extension_uri:
                    continue

                params = self._read_mapping_value(extension, "params")
                if not isinstance(params, dict):
                    continue

                index_url = params.get(index_param_key)
                if not isinstance(index_url, str) or not index_url:
                    continue

                payload = self._fetcher(index_url)
                prompts = payload.get("prompts")
                if not isinstance(prompts, list):
                    raise PromptSourceError("Agent prompt catalog index is invalid.", locator=index_url, source_type="url")

                url_catalog = UrlPromptCatalog(index_url=index_url, fetcher=self._fetcher)
                references.extend(url_catalog._build_reference(entry, index_url) for entry in prompts)
                break

        return references

    def _read_mapping_value(self, value: object, field_name: str) -> object:
        if isinstance(value, dict):
            return value.get(field_name)
        return getattr(value, field_name, None)
