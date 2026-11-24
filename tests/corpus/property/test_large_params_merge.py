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

"""Large-parameter-space property (port of Java ``LargeParamsMergePropertyTest``, design §8.3).

Extraction and merge correctness for parameter maps of 50+ keys in the shape of telecom
network-element configuration negotiation. The scripted semantic validator returns every generated
key with its exact value, plus sentinel values for the three context keys. The property asserts
the merged result carries every extracted key losslessly, keeps the exact map size (context keys +
extracted keys, collisions notwithstanding), and lets the context win every collision.
"""

from __future__ import annotations

from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from a2a_t.core.metadata import NegotiationContext
from a2a_t.core.validation_pipeline import FilledParamData
from tests.corpus.property.arbitraries import contexts, languages
from tests.corpus.property.harness import (
    object_schema,
    scripted,
    semantic_verdict,
    service,
    template_uri,
    type_schema,
)

#: Parameter name bases of the telecom network-element shape.
PARAM_BASES: tuple[str, ...] = (
    "gnb.cell",
    "gnb.carrier",
    "amf.slice",
    "amf.paging",
    "smf.pdu_session",
    "smf.charging",
    "upf.tunnel",
    "upf.queuing",
    "ne.power",
    "ne.cooling",
    "transport.bearer",
    "transport.latency",
)

INFORMATION_PROPOSE_URI = "Negotiation-T/information-negotiation/propose/v1"

#: Sentinel values the scripted verdict writes onto the three context keys.
SENTINEL_SESSION_ID = "sentinel-session-id"
SENTINEL_ROUND = -999
SENTINEL_MAX_ROUNDS = -1


def _telecom_param_keys() -> st.SearchStrategy[str]:
    """Strategy of telecom parameter names: one of the twelve bases plus a 0-99 index."""
    return st.builds(
        lambda base, index: f"{base}.{index}",
        st.sampled_from(PARAM_BASES),
        st.integers(min_value=0, max_value=99),
    )


def _telecom_param_values() -> st.SearchStrategy[Any]:
    """Strategy of parameter values of the four JSON Schema types."""
    return st.one_of(
        st.text(alphabet="abcdefghijklmnopqrstuvwxyz_0123456789", min_size=1, max_size=12),
        st.integers(min_value=-1000, max_value=100000),
        st.booleans(),
        st.floats(min_value=0.0, max_value=100.0),
    )


def _telecom_params() -> st.SearchStrategy[dict[str, Any]]:
    """Strategy of telecom parameter maps of 50 to 80 keys."""
    return st.dictionaries(_telecom_param_keys(), _telecom_param_values(), min_size=50, max_size=80)


@settings(max_examples=300)
@given(language=languages(), context=contexts(), params=_telecom_params())
def test_large_telecom_parameter_spaces_merge_losslessly(
    language: str,
    context: NegotiationContext,
    params: dict[str, Any],
) -> None:
    """Every extracted key survives the merge losslessly and the context wins every collision."""
    with_collisions: dict[str, Any] = dict(params)
    with_collisions["id"] = SENTINEL_SESSION_ID
    with_collisions["round"] = SENTINEL_ROUND
    with_collisions["maxRounds"] = SENTINEL_MAX_ROUNDS
    properties = {key: type_schema(value) for key, value in params.items()}
    llm = scripted(semantic_verdict("information", with_collisions))
    wired = service(language, llm)
    filled: FilledParamData = wired.validate_propose_prompt_and_data_filling(
        "Telecom network element parameter filling request.",
        context,
        object_schema(properties),
        template_uri(INFORMATION_PROPOSE_URI),
    )
    assert filled.data.get("id") == context.id, "the context id must win the collision"
    assert filled.data.get("round") == context.round, "the context round must win the collision"
    assert filled.data.get("maxRounds") == context.max_rounds, "the context maxRounds must win the collision"
    for key, value in params.items():
        assert filled.data.get(key) == value, f"extracted parameter '{key}' must survive the merge"
    assert len(filled.data) == len(params) + 3, "merged size = context keys + non-colliding extracted keys"
    assert llm.call_count == 1
