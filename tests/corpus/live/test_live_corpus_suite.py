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

"""Data-driven suite of the live-LLM family corpus (port of the Java ``LiveCorpusSuiteTest``;
live design document §2): every case under ``negotiation-cases/live/`` becomes one parametrized
test executed by the :class:`~tests.corpus.live.engine.LiveCaseEngine` against the real
OpenAI-compatible endpoint of the dedicated test configuration.

The family is opt-in exactly like its configuration (Q1): the module fixtures pass through the
adjudicated skip gate :func:`~tests.corpus.live.config.assume_configured` — an unconfigured
environment skips the whole family with the configuration hint, and a resolved but invalid
configuration is a red failure, not a skip — so ``pytest`` and CI stay offline-deterministic. A
configured run creates its transcript directory at fixture set-up time (§5 [R-C3]) and flushes
the transcript and summary after the last case; a failed write is a warning, not a verdict: the
case outcomes have already surfaced through their tests, so a locked or read-only
``.live-corpus/`` must not turn a green run red.

The whole module carries the ``live`` marker: CI deselects it with ``-m 'not live'`` (D24), and a
local run of a subset uses the same ``-k`` rule as the offline families (``-k LIVE-GEN-01``
selects the case, ``-k 'LIVE-GEN and zh-CN'`` narrows it further).
"""

from __future__ import annotations

from collections.abc import Generator

import pytest

from tests.corpus.conftest import corpus_live_case_parametrize
from tests.corpus.live.config import LiveLlmConfig, assume_configured
from tests.corpus.live.engine import LiveCaseEngine
from tests.corpus.live.transcript import LiveTranscript, LiveTranscriptRun
from tests.corpus.models import LiveCase

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live_run() -> Generator[LiveTranscriptRun, None, None]:
    """The transcript run of the configured live session, flushed once after the last case."""
    assume_configured()
    run = LiveTranscript.create_run()
    yield run
    try:
        transcript = run.write()
        print(f"[corpus] live transcript written to {transcript}: {run.summary()}")
    except OSError as error:
        print(
            f"[corpus] WARNING: failed to write the live transcript to {run.directory} "
            f"({error}); the case verdicts above stay authoritative"
        )


@pytest.fixture(scope="module")
def live_engine(live_run: LiveTranscriptRun) -> LiveCaseEngine:
    """The live engine of the configured run: the real endpoint client behind the env bridge."""
    config = LiveLlmConfig.from_current_process()
    assert config is not None, "the live_run fixture already gated on a resolved configuration"
    return LiveCaseEngine(config, live_run)


@corpus_live_case_parametrize("live")
def test_live_corpus_case(live_engine: LiveCaseEngine, live_case: LiveCase) -> None:
    """Execute one expanded live case of the corpus against the real endpoint."""
    live_engine.run(live_case)
