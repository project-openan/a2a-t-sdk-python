"""The template catalog in ``local_file`` mode: the built-in overlay and per-template origins.

The catalog is the headline consumer of the ADR 0005 overlay (and of the category-relative key
shape of ``LocalResourceSnapshot.category_files``): ``load_all`` must enumerate the packaged
templates union the locally captured ones with the local copy winning, every record must carry its
effective origin, and ``load`` must resolve both a locally overridden URI and a packaged-only URI
— the pre-overlay implementation produced garbage URIs (a redundant ``templates/`` prefix) and a
permanently ``None`` ``load()`` in this mode, so these tests pin the fix.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from a2a_t.common.prompt_resources import (
    SOURCE_LOCAL,
    SOURCE_PACKAGED,
    PromptTemplateCatalog,
    TemplateQueryService,
)
from a2a_t.core.template_uri import TemplateUri
from tests.common.prompt_resources.conftest import LANGUAGES, packaged_file_text, write_local_resource

_ENERGY_SAVING_URI = "Task-T/network-layer/ran-energy-saving/v1"
_ABORT_URI = "Negotiation-T/common/abort/v1"


def _catalog(root: Path, language: str) -> PromptTemplateCatalog:
    """Create one local_file-mode catalog over one local root."""
    return PromptTemplateCatalog(language, "local_file", str(root))


class TestLocalModeCatalog:
    """The overlay enumeration and the per-template origin of the local_file-mode catalog."""

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_load_all_enumerates_the_overlay_with_per_template_origins(self, tmp_path: Path, language: str) -> None:
        write_local_resource(
            tmp_path,
            f"templates/{_ENERGY_SAVING_URI}/{language}/template.md",
            f"<!-- Local energy saving -->\nLOCAL {language}",
        )
        templates = _catalog(tmp_path, language).load_all()
        by_uri = {template.template_uri.uri: template for template in templates}

        local_template = by_uri[_ENERGY_SAVING_URI]
        assert local_template.source == SOURCE_LOCAL
        assert local_template.content == f"<!-- Local energy saving -->\nLOCAL {language}"
        assert local_template.description == "Local energy saving"

        packaged_template = by_uri[_ABORT_URI]
        assert packaged_template.source == SOURCE_PACKAGED
        assert packaged_template.content == packaged_file_text(f"templates/{_ABORT_URI}/{language}/template.md")

    @pytest.mark.parametrize("language", LANGUAGES)
    def test_load_resolves_both_the_local_override_and_the_packaged_fallback(
        self, tmp_path: Path, language: str
    ) -> None:
        write_local_resource(
            tmp_path,
            f"templates/{_ENERGY_SAVING_URI}/{language}/template.md",
            f"LOCAL {language}",
        )
        catalog = _catalog(tmp_path, language)

        local_template = catalog.load(TemplateUri.parse(_ENERGY_SAVING_URI))
        assert local_template is not None
        assert local_template.source == SOURCE_LOCAL
        assert local_template.content == f"LOCAL {language}"

        packaged_template = catalog.load(TemplateUri.parse(_ABORT_URI))
        assert packaged_template is not None
        assert packaged_template.source == SOURCE_PACKAGED

    def test_query_service_lists_the_overlay_in_local_mode(self, tmp_path: Path) -> None:
        write_local_resource(
            tmp_path,
            f"templates/{_ENERGY_SAVING_URI}/en-US/template.md",
            "LOCAL TEMPLATE",
        )
        prompts = TemplateQueryService.from_config("en-US", "local_file", str(tmp_path)).get_prompts()

        assert {template.template_uri.uri for template in prompts} >= {_ENERGY_SAVING_URI, _ABORT_URI}
        assert prompts == sorted(prompts, key=lambda template: template.template_uri.uri)

    def test_shadowing_is_per_path_not_cross_layout(self, tmp_path: Path) -> None:
        """A local override at a non-canonical layout is listed as its own URI next to the
        packaged one — the overlay shadows identical paths only (Java catalog parity)."""
        write_local_resource(
            tmp_path,
            "templates/Task-T/ran-energy-saving/v1/en-US/template.md",
            "LOCAL PLAIN LAYOUT",
        )
        by_uri = {t.template_uri.uri: t for t in _catalog(tmp_path, "en-US").load_all()}

        assert by_uri["Task-T/ran-energy-saving/v1"].source == "local"
        assert by_uri["Task-T/network-layer/ran-energy-saving/v1"].source == "packaged"
