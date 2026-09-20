"""Routing behavior of the D31 resource access layer.

Pins the routing table of ``a2a_t.common.prompt_resources.resource_access``: source-type dispatch,
the frozen local snapshot (D9), the built-in packaged fallback of the missing ``local_file``
business content with its one-time-per-path warning (Java ADR 0005), the package-fixed categories
(``prompts/``, ``errors/``) with the CustomRootPromptsIgnored semantics, and the configuration
failures of ``local_file`` mode.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest

from a2a_t.common.prompt_resources import PromptResourceAccess, create
from a2a_t.common.prompt_resources.resource_access import (
    LocalFilePromptResourceAccess,
    PackagedPromptResourceAccess,
)
from a2a_t.config.errors import ConfigError
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.core.standard_templates import ENERGY_SAVING_URI, NEGOTIATION_ABORT_URI
from tests.common.prompt_resources.conftest import (
    LANGUAGES,
    copy_packaged_vocabulary,
    packaged_file_text,
    packaged_root,
    write_local_resource,
)

LOGGER_NAME = "a2a_t.common.prompt_resources.resource_access"

#: Parent of every prompt-resources logger; used where the child logger name must not be pinned.
_PROMPT_RESOURCES_LOGGER = "a2a_t.common.prompt_resources"


def _local_access(root: Path) -> LocalFilePromptResourceAccess:
    """Create one local_file-mode access object for one root directory."""
    return create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(root)))


class TestSourceRouting:
    """Source-type dispatch of the ``create`` factory."""

    def test_packaged_mode_serves_routed_resources_from_the_package(
        self, packaged_access: PromptResourceAccess
    ) -> None:
        assert isinstance(packaged_access, PackagedPromptResourceAccess)
        assert packaged_access.packaged() is True
        assert packaged_access.local_root_dir() is None
        assert packaged_access.template_text(ENERGY_SAVING_URI, "en-US") == packaged_file_text(
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md"
        )

    def test_local_file_mode_serves_routed_resources_from_the_local_root(self, tmp_path: Path) -> None:
        write_local_resource(
            tmp_path,
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            "LOCAL TEMPLATE",
        )
        access = _local_access(tmp_path)
        assert isinstance(access, LocalFilePromptResourceAccess)
        assert access.packaged() is False
        assert access.local_root_dir() == tmp_path
        assert access.template_text(ENERGY_SAVING_URI, "en-US") == "LOCAL TEMPLATE"

    @pytest.mark.parametrize("source_type", ["url", "agent", "classpath", "", "PACKAGED"])
    def test_unsupported_source_type_is_a_config_error(self, source_type: str) -> None:
        with pytest.raises(ConfigError) as info:
            create(PromptRuntimeConfig(source_type=source_type, local_root_dir=""))
        assert info.value.code is ErrorCatalog.INFRA_CONFIG_INVALID


class TestSourceTypeDefault:
    """The D10 step-2 release flip: the out-of-the-box source type is ``packaged``.

    The pre-1.1.0 default (``local_file``) is restored by setting
    ``A2AT_PROMPT_SOURCE_TYPE=local_file``; the routing itself is unchanged and pinned above.
    """

    def test_dataclass_default_is_packaged(self) -> None:
        assert PromptRuntimeConfig().source_type == "packaged"

    @pytest.mark.parametrize(
        "values",
        [{}, {"A2AT_PROMPT_SOURCE_TYPE": ""}],
        ids=["absent", "blank"],
    )
    def test_from_mapping_defaults_to_packaged(self, values: dict[str, str]) -> None:
        # Only an absent or empty value falls back to the default; a whitespace value keeps the
        # pre-existing parsing semantics and fails in ``create`` as an unsupported source type.
        assert PromptRuntimeConfig.from_mapping(values).source_type == "packaged"

    @pytest.mark.parametrize("source_type", ["packaged", "local_file"])
    def test_from_mapping_honors_an_explicit_source_type(self, source_type: str) -> None:
        values = {"A2AT_PROMPT_SOURCE_TYPE": source_type}
        assert PromptRuntimeConfig.from_mapping(values).source_type == source_type

    def test_fresh_env_without_source_type_reads_the_installed_package_tree(self, tmp_path: Path) -> None:
        from a2a_t.config.models import A2ATConfig

        env_path = tmp_path / ".env"
        env_path.write_text("A2AT_LANGUAGE=en-US\n", encoding="utf-8")

        config = A2ATConfig.load(env_path)

        assert config.prompt.source_type == "packaged"
        access = create(config.prompt)
        assert isinstance(access, PackagedPromptResourceAccess)
        assert access.template_text(ENERGY_SAVING_URI, "en-US") == packaged_file_text(
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md"
        )


class TestLocalRootRequirements:
    """Configuration failures of ``local_file`` mode."""

    @pytest.mark.parametrize("local_root_dir", ["", "   "])
    def test_missing_local_root_configuration_is_a_config_error(self, local_root_dir: str) -> None:
        with pytest.raises(ConfigError) as info:
            create(PromptRuntimeConfig(source_type="local_file", local_root_dir=local_root_dir))
        assert info.value.code is ErrorCatalog.INFRA_CONFIG_INVALID
        assert "A2AT_PROMPT_RESOURCE_LOCAL_ROOT_DIR" in str(info.value)

    def test_non_directory_local_root_is_a_config_error(self, tmp_path: Path) -> None:
        missing = tmp_path / "does-not-exist"
        with pytest.raises(ConfigError) as info:
            create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(missing)))
        assert info.value.code is ErrorCatalog.INFRA_CONFIG_INVALID
        assert str(missing) in str(info.value)

    def test_file_path_local_root_is_a_config_error(self, tmp_path: Path) -> None:
        root_file = tmp_path / "not-a-directory"
        root_file.write_text("not a directory", encoding="utf-8")
        with pytest.raises(ConfigError):
            create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(root_file)))


class TestPackagedModeLocalRootWarning:
    """The packaged-mode local-root-ignored warning."""

    def test_configured_local_root_warns_ignored(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            access = create(PromptRuntimeConfig(source_type="packaged", local_root_dir=str(tmp_path)))
        assert isinstance(access, PackagedPromptResourceAccess)
        warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert any(
            "prompt_resource_local_root_ignored" in message and str(tmp_path) in message for message in warnings
        ), warnings

    def test_default_packaged_root_does_not_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            create(PromptRuntimeConfig(source_type="packaged"))
        warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert not any("prompt_resource_local_root_ignored" in message for message in warnings), warnings

    def test_blank_local_root_does_not_warn(self, caplog: pytest.LogCaptureFixture) -> None:
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            create(PromptRuntimeConfig(source_type="packaged", local_root_dir=""))
        warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert not any("prompt_resource_local_root_ignored" in message for message in warnings), warnings


class TestFrozenSnapshot:
    """The D9 frozen-snapshot semantics of ``local_file`` mode."""

    def test_file_edits_after_capture_are_invisible_until_restart(self, tmp_path: Path) -> None:
        template = write_local_resource(
            tmp_path,
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            "original content",
        )
        access = _local_access(tmp_path)
        assert access.template_text(ENERGY_SAVING_URI, "en-US") == "original content"

        template.write_text("edited content", encoding="utf-8")
        assert access.template_text(ENERGY_SAVING_URI, "en-US") == "original content"

    def test_files_added_after_capture_stay_invisible_but_the_package_serves_the_fallback(self, tmp_path: Path) -> None:
        access = _local_access(tmp_path)
        write_local_resource(
            tmp_path,
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            "added later",
        )
        assert access.template_text(ENERGY_SAVING_URI, "en-US") == packaged_file_text(
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md"
        )

    def test_files_deleted_after_capture_stay_served(self, tmp_path: Path) -> None:
        template = write_local_resource(
            tmp_path,
            "templates/Negotiation-T/common/abort/v1/en-US/template.md",
            "frozen abort template",
        )
        access = _local_access(tmp_path)
        template.unlink()
        assert access.template_text(NEGOTIATION_ABORT_URI, "en-US") == "frozen abort template"

    def test_a_new_access_object_sees_the_edited_files(self, tmp_path: Path) -> None:
        template = write_local_resource(
            tmp_path,
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            "original content",
        )
        _local_access(tmp_path)
        template.write_text("edited content", encoding="utf-8")
        assert _local_access(tmp_path).template_text(ENERGY_SAVING_URI, "en-US") == "edited content"


class TestBuiltinFallback:
    """The Java ADR 0005 overlay: packaged fallback for missing local_file business content.

    A routed resource missing from the frozen snapshot falls back to the packaged copy; each
    resource path warns once (``prompt_resource_builtin_fallback``) and only after the packaged
    load actually succeeded.
    """

    FALLBACK_LOGGER = "a2a_t.common.prompt_resources.builtin_fallback"

    def test_missing_template_falls_back_to_the_packaged_copy(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            text = access.template_text(ENERGY_SAVING_URI, "en-US")
        assert text == packaged_file_text("templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md")
        messages = [record.getMessage() for record in caplog.records]
        assert messages == [
            "prompt_resource_builtin_fallback "
            "path=prompt_resources/templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md "
            "source=packaged"
        ]

    def test_missing_scenarios_fall_back_to_the_packaged_catalog(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            scenarios = access.load_scenarios("en-US")
        assert {scenario.scenario_code for scenario in scenarios} >= {"ran-energy-saving", "subscribe-incident"}
        assert any(
            "prompt_resource_builtin_fallback path=prompt_resources/scenarios/en-US/scenarios.json source=packaged"
            in record.getMessage()
            for record in caplog.records
        )

    def test_missing_vocabulary_falls_back_to_the_packaged_copy(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            vocabulary = access.load_vocabulary("en-US")
        assert (
            vocabulary.get("punct.list_colon")
            == json.loads(packaged_file_text("negotiation-vocabulary/en-US/vocabulary.json"))["punct.list_colon"]
        )
        assert any(
            "path=prompt_resources/negotiation-vocabulary/en-US/vocabulary.json source=packaged" in record.getMessage()
            for record in caplog.records
        )

    def test_fallback_warns_only_once_per_path_across_repeated_reads(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            access.load_scenarios("en-US")
            access.load_scenarios("en-US")
        scenario_warnings = [
            record
            for record in caplog.records
            if "prompt_resources/scenarios/en-US/scenarios.json" in record.getMessage()
        ]
        assert len(scenario_warnings) == 1

    def test_double_missing_resource_raises_without_a_fallback_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            with pytest.raises(A2ATError) as info:
                access.load_scenarios("xx-XX")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
        fallback_warnings = [record for record in caplog.records if record.name == self.FALLBACK_LOGGER]
        assert fallback_warnings == []

    def test_package_fixed_categories_never_fall_back(self, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
        """``prompts/`` and ``errors/`` are read from the package directly, without the overlay."""
        access = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            prompt = access.load_prompt("slot_extraction", "en-US", "system.md")
            errors = access.load_errors("en-US")
        assert prompt == packaged_file_text("prompts/slot_extraction/en-US/system.md")
        assert (
            errors["template.not_found"]
            == json.loads(packaged_file_text("errors/en-US/errors.json"))["template.not_found"]
        )
        assert caplog.records == []

    def test_template_entry_origins_mark_the_overlay(self, tmp_path: Path) -> None:
        write_local_resource(
            tmp_path,
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            "LOCAL TEMPLATE",
        )
        access = _local_access(tmp_path)
        entries = access.template_entries_with_origins()
        assert entries["Task-T/network-layer/ran-energy-saving/v1/en-US/template.md"] == (
            "LOCAL TEMPLATE",
            "local",
        )
        assert entries["Negotiation-T/common/abort/v1/en-US/template.md"][1] == "packaged"

    def test_packaged_mode_origins_are_uniform(self, packaged_access: PromptResourceAccess) -> None:
        entries = packaged_access.template_entries_with_origins()
        assert entries
        assert {origin for _text, origin in entries.values()} == {"packaged"}

    def test_packaged_mode_never_emits_fallback_warnings(
        self, packaged_access: PromptResourceAccess, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The overlay reader is wired only in local_file mode; packaged reads never warn."""
        with caplog.at_level(logging.WARNING, logger=_PROMPT_RESOURCES_LOGGER):
            packaged_access.template_text(ENERGY_SAVING_URI, "en-US")
            packaged_access.slot_schema(ENERGY_SAVING_URI, "en-US")
            packaged_access.load_scenarios("en-US")
        assert caplog.records == []

    def test_fallback_warning_dedup_resets_per_access_object(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """The dedup set lives on the access object: a fresh access warns again (Java parity)."""
        first = _local_access(tmp_path)
        with caplog.at_level(logging.WARNING, logger=self.FALLBACK_LOGGER):
            first.load_scenarios("en-US")
            second = _local_access(tmp_path)
            second.load_scenarios("en-US")
        scenario_warnings = [
            record
            for record in caplog.records
            if "prompt_resources/scenarios/en-US/scenarios.json" in record.getMessage()
        ]
        assert len(scenario_warnings) == 2


class TestCustomRootPromptsIgnored:
    """The CustomRootPromptsIgnored semantics: package-fixed categories vs routed ones (D31)."""

    def test_local_prompts_are_ignored_while_negotiation_resources_are_honored(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_local_resource(tmp_path, "prompts/slot_extraction/en-US/system.md", "CUSTOM SYSTEM PROMPT")
        write_local_resource(
            tmp_path,
            "templates/Negotiation-T/common/abort/v1/en-US/template.md",
            "LOCAL ABORT TEMPLATE",
        )
        entries = copy_packaged_vocabulary(tmp_path, "en-US")
        entries["punct.list_colon"] = "LOCAL COLON"
        write_local_resource(
            tmp_path,
            "negotiation-vocabulary/en-US/vocabulary.json",
            json.dumps(entries, ensure_ascii=False),
        )

        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            access = _local_access(tmp_path)

        warnings = " ".join(record.getMessage() for record in caplog.records if record.levelno == logging.WARNING)
        assert "prompt_resource_local_directories_ignored" in warnings
        assert "prompts" in warnings
        assert "Negotiation-T" not in warnings, warnings
        assert "negotiation-vocabulary" not in warnings, warnings

        assert access.load_prompt("slot_extraction", "en-US", "system.md") != "CUSTOM SYSTEM PROMPT"
        assert access.template_text(NEGOTIATION_ABORT_URI, "en-US") == "LOCAL ABORT TEMPLATE"
        assert access.load_vocabulary("en-US").get("punct.list_colon") == "LOCAL COLON"

    def test_local_errors_directory_is_ignored_with_a_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_local_resource(tmp_path, "errors/en-US/errors.json", '{"template.not_found": "CUSTOM"}')
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            access = _local_access(tmp_path)
        warnings = " ".join(record.getMessage() for record in caplog.records if record.levelno == logging.WARNING)
        assert "prompt_resource_local_directories_ignored" in warnings
        assert "errors" in warnings
        assert access.load_errors("en-US")["template.not_found"] != "CUSTOM"

    def test_no_warning_without_package_fixed_directories(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        write_local_resource(
            tmp_path,
            "templates/Task-T/network-layer/ran-energy-saving/v1/en-US/template.md",
            "LOCAL TEMPLATE",
        )
        with caplog.at_level(logging.WARNING, logger=LOGGER_NAME):
            _local_access(tmp_path)
        warnings = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
        assert not any("prompt_resource_local_directories_ignored" in message for message in warnings), warnings


class TestPackagedFixedLoads:
    """The package-fixed accessors shared by both modes."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_load_prompt_serves_the_packaged_prompt(self, packaged_access: PromptResourceAccess, language: str) -> None:
        prompt = packaged_access.load_prompt("slot_extraction", language, "system.md")
        assert prompt == packaged_file_text(f"prompts/slot_extraction/{language}/system.md")

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_load_prompt_missing_raises_resource_read_failed(
        self, packaged_access: PromptResourceAccess, language: str
    ) -> None:
        with pytest.raises(A2ATError) as info:
            packaged_access.load_prompt("does-not-exist", language, "system.md")
        assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED

    def test_local_mode_still_serves_prompts_from_the_package(self, tmp_path: Path) -> None:
        write_local_resource(tmp_path, "prompts/slot_extraction/en-US/system.md", "CUSTOM SYSTEM PROMPT")
        access = _local_access(tmp_path)
        assert access.load_prompt("slot_extraction", "en-US", "system.md") == packaged_file_text(
            "prompts/slot_extraction/en-US/system.md"
        )

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_load_prompt_rejects_traversal_categories(
        self, packaged_access: PromptResourceAccess, language: str
    ) -> None:
        with pytest.raises(ValueError):
            packaged_access.load_prompt("../escape", language, "system.md")
        with pytest.raises(ValueError):
            packaged_access.load_prompt("slot_extraction", "../escape", "system.md")
        with pytest.raises(ValueError):
            packaged_access.load_prompt("slot_extraction", language, "../system.md")


def test_packaged_root_matches_the_package_tree() -> None:
    """The packaged root helper points at the tree the packaged reader serves."""
    root = packaged_root()
    assert (root / "templates").is_dir()
    assert (root / "prompts").is_dir()
