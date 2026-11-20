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

"""Hypothesis profile registration of the corpus property layer (D23).

The default profile derandomizes every run — the Python counterpart of the Java jqwik fixed seeds
(``seed = "20260831"`` et al.) — so the property layer is byte-deterministic in CI: the same
examples run in the same order on every machine. ``deadline=None`` keeps the production-wired
service assembly (vocabulary loads, template renders) from tripping the per-example wall-clock
deadline on a busy machine; the ``max_examples`` counts mirror the Java ``tries`` per property.

Randomized local runs stay one flag away::

    uv run pytest tests/corpus/property --hypothesis-profile=corpus-random

The CLI profile wins over the derandomized default: when ``--hypothesis-profile`` is given the
hypothesis pytest plugin has already loaded it by the time this conftest runs, so the default load
is skipped.
"""

from __future__ import annotations

import pytest
from hypothesis import settings

#: Example counts of the property families, mirroring the Java jqwik ``tries`` (design §8.3).
DETERMINISM_EXAMPLES = 1000
ROUND_TRIP_EXAMPLES = 1000
ERROR_PATH_EXAMPLES = 100
LARGE_PARAMS_EXAMPLES = 300

_DEFAULT_PROFILE = "corpus-derandomized"
_RANDOM_PROFILE = "corpus-random"


def pytest_configure(config: pytest.Config) -> None:
    """Register the corpus property profiles and load the derandomized default.

    Args:
        config: the pytest configuration object, consulted for an explicitly selected
            ``--hypothesis-profile``.
    """
    settings.register_profile(
        _DEFAULT_PROFILE,
        derandomize=True,
        max_examples=DETERMINISM_EXAMPLES,
        deadline=None,
    )
    settings.register_profile(
        _RANDOM_PROFILE,
        derandomize=False,
        max_examples=DETERMINISM_EXAMPLES,
        deadline=None,
    )
    if not getattr(config.option, "hypothesis_profile", None):
        settings.load_profile(_DEFAULT_PROFILE)
