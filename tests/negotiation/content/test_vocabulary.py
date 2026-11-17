# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Tests of the domain negotiation vocabulary (port of the Java ``VocabularyTest``).

Pins :class:`a2a_t.negotiation.content.vocabulary.Vocabulary`: both bundled languages expose
exactly the 37 canonical keys (the live resource is the truth), the pinned wording constants match
the bundled template bytes verbatim, unknown keys and unsupported languages fail fast, and the
type consumes the D31 common access layer (packaged by default, locally overridable through an
injected access object).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from a2a_t.common.prompt_resources.resource_access import PackagedPromptResourceAccess
from a2a_t.common.prompt_resources.vocabulary import Vocabulary as CommonVocabulary
from a2a_t.config.models import PromptRuntimeConfig
from a2a_t.core.errors.catalog import ErrorCatalog
from a2a_t.core.errors.exceptions import A2ATError
from a2a_t.negotiation.content import CANONICAL_KEYS, Vocabulary
from tests.common.prompt_resources.conftest import copy_packaged_vocabulary, write_local_resource

LANGUAGES = ["zh-CN", "en-US"]

#: The live resource is the truth: 37 canonical keys.
CANONICAL_KEY_COUNT = 37


@pytest.mark.parametrize("language", LANGUAGES)
def test_both_languages_expose_exactly_the_canonical_keys(language: str) -> None:
    vocabulary = Vocabulary.for_language(language)
    assert set(vocabulary.canonical_keys()) == set(CANONICAL_KEYS)
    assert len(vocabulary.canonical_keys()) == CANONICAL_KEY_COUNT
    assert len(CANONICAL_KEYS) == CANONICAL_KEY_COUNT


def test_bilingual_parity_of_the_key_sets() -> None:
    zh_cn = Vocabulary.for_language("zh-CN")
    en_us = Vocabulary.for_language("en-US")
    assert set(zh_cn.canonical_keys()) == set(en_us.canonical_keys())
    assert sorted(zh_cn.canonical_keys()) == sorted(en_us.canonical_keys())


def test_canonical_keys_carry_no_duplicates() -> None:
    assert len(CANONICAL_KEYS) == len(set(CANONICAL_KEYS))


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_vocabulary_binds_its_language(language: str) -> None:
    assert Vocabulary.for_language(language).language == language


def test_termination_reason_slot_matches_the_common_abort_template_placeholder() -> None:
    zh_cn = Vocabulary.for_language("zh-CN")
    en_us = Vocabulary.for_language("en-US")
    assert zh_cn.get("slot.termination_reason") == "协商终止原因"
    assert en_us.get("slot.termination_reason") == "negotiation_termination_reason"
    assert zh_cn.get("section.termination_reason") == "协商终止原因"
    assert en_us.get("section.termination_reason") == "Negotiation Termination Reason"


def test_slot_placeholders_are_snake_case_for_english_and_cjk_for_chinese() -> None:
    assert Vocabulary.for_language("zh-CN").get("slot.target_intent") == "意图理解陈述"
    assert Vocabulary.for_language("en-US").get("slot.target_intent") == "intent_understanding_statement"


def test_summary_and_confirm_slots_differ_from_their_section_title() -> None:
    zh_cn = Vocabulary.for_language("zh-CN")
    en_us = Vocabulary.for_language("en-US")
    assert zh_cn.get("slot.feasibility") == "可行性协商概述"
    assert en_us.get("slot.feasibility") == "feasibility_negotiation_summary"
    assert zh_cn.get("slot.target") == "目标协商概述"
    assert en_us.get("slot.target") == "target_negotiation_summary"
    assert zh_cn.get("slot.feasibility_confirm") == "评估结果确认"
    assert en_us.get("slot.feasibility_confirm") == "evaluation_result_confirmation"


def test_section_values_match_template_section_titles() -> None:
    zh_cn = Vocabulary.for_language("zh-CN")
    en_us = Vocabulary.for_language("en-US")
    assert zh_cn.get("section.target_result_content") == "目标协商结果内容"
    assert en_us.get("section.target_result_content") == "Target Negotiation Result Content"
    assert zh_cn.get("section.feasibility_confirm") == "可行性评估结果确认"
    assert en_us.get("section.feasibility_confirm") == "Feasibility Assessment Result Confirmation"


def test_confirm_request_section_and_slot_keys_match_the_new_templates() -> None:
    zh_cn = Vocabulary.for_language("zh-CN")
    en_us = Vocabulary.for_language("en-US")
    assert zh_cn.get("section.target_confirm_request") == "目标澄清后的确认请求"
    assert en_us.get("section.target_confirm_request") == "Target Clarification Confirmation Request"
    assert zh_cn.get("slot.target_confirm_request") == "目标澄清后的确认请求"
    assert en_us.get("slot.target_confirm_request") == "target_confirm_request"
    assert zh_cn.get("section.feasibility_confirm_request") == "评估可行时的确认请求"
    assert en_us.get("section.feasibility_confirm_request") == "Feasible Evaluation Confirmation Request"
    assert zh_cn.get("slot.feasibility_confirm_request") == "评估可行时的确认请求"
    assert en_us.get("slot.feasibility_confirm_request") == "feasibility_confirm_request"


def test_english_relationship_label_carries_one_trailing_space() -> None:
    english_label = Vocabulary.for_language("en-US").get("label.relationship")
    chinese_label = Vocabulary.for_language("zh-CN").get("label.relationship")
    assert english_label == "Relationship between missing items: "
    assert english_label.endswith(" ")
    assert chinese_label == "缺失项之间的关系："


def test_list_colon_punctuation_differs_per_language() -> None:
    assert Vocabulary.for_language("zh-CN").get("punct.list_colon") == "："
    assert Vocabulary.for_language("en-US").get("punct.list_colon") == ": "


@pytest.mark.parametrize("language", LANGUAGES)
def test_unknown_key_throws_with_the_key_in_the_message(language: str) -> None:
    with pytest.raises(KeyError) as info:
        Vocabulary.for_language(language).get("section.unknown")
    assert "section.unknown" in str(info.value)


def test_unsupported_language_fails_fast_with_a_coded_read_failure() -> None:
    with pytest.raises(A2ATError) as info:
        Vocabulary.for_language("fr-FR")
    assert info.value.code is ErrorCatalog.INFRA_RESOURCE_READ_FAILED
    # The language is visible through the failing resource path, like the Java message.
    assert "fr-FR" in str(info.value)
    assert "A2AT_LANGUAGE" in str(info.value)


@pytest.mark.parametrize("language", ["../escape", "", "  ", "a/b"])
def test_language_that_is_not_a_simple_path_segment_throws(language: str) -> None:
    with pytest.raises(ValueError):
        Vocabulary.for_language(language)


@pytest.mark.parametrize("language", LANGUAGES)
def test_the_default_access_is_the_packaged_one(language: str) -> None:
    packaged = PackagedPromptResourceAccess().load_vocabulary(language)
    vocabulary = Vocabulary.for_language(language)
    assert isinstance(vocabulary, Vocabulary)
    assert isinstance(vocabulary, CommonVocabulary)
    assert vocabulary.entries == packaged.entries


@pytest.mark.parametrize("language", LANGUAGES)
def test_an_injected_local_access_is_honored(tmp_path: Path, language: str) -> None:
    """The wiring to the D31 access layer: an injected access overrides the packaged resource."""
    from a2a_t.common.prompt_resources.resource_access import create

    entries = copy_packaged_vocabulary(tmp_path, language)
    entries["punct.list_colon"] = "LOCAL COLON"
    write_local_resource(
        tmp_path,
        f"negotiation-vocabulary/{language}/vocabulary.json",
        json.dumps(entries, ensure_ascii=False),
    )
    access = create(PromptRuntimeConfig(source_type="local_file", local_root_dir=str(tmp_path)))
    assert Vocabulary.for_language(language, access).get("punct.list_colon") == "LOCAL COLON"


def test_resolved_vocabularies_are_cached_per_access_and_language() -> None:
    # Assembly-time snapshot semantics: one resolved vocabulary per (access, language) pair, like
    # the Java per-JVM-and-classloader cache.
    first = Vocabulary.for_language("zh-CN")
    second = Vocabulary.for_language("zh-CN")
    assert first is second
    assert Vocabulary.for_language("zh-CN", PackagedPromptResourceAccess()) is not first
