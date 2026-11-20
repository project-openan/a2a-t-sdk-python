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

"""Registry of the behavior contracts a corpus expectation can reference.

Port of the Java ``a2a-t-corpus`` ``Contract.java`` enum (design document §8.1, Q18): the registry
defines all twelve contracts at once with their P0/P1 level, so it doubles as the readable
expectation list of the corpus-generation workflow. The engine implements the four P0 contracts;
referencing a P1 contract is an explicit engine failure ("not yet lit") rather than a silent pass,
so a corpus author always knows what is actually asserted.

The enum itself lives in :mod:`tests.corpus.models` together with the other corpus record types —
mirroring the Java package layout, where ``Contract.java`` sits next to the records and the strict
loader validates contract names against it — so the loader and the engine share one single source
of the registry. This module is the engine-facing front door of that registry: it re-exports the
enum and adds the registration-order name list the engine's failure messages quote.
"""

from __future__ import annotations

from typing import Final

from tests.corpus.models import Contract

__all__ = ["KNOWN_CONTRACT_NAMES", "Contract"]

#: JSON names of every registered contract, in registration order (engine failure messages).
KNOWN_CONTRACT_NAMES: Final[tuple[str, ...]] = tuple(contract.json_name for contract in Contract)
