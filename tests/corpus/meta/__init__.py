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

"""Meta layer of the negotiation test corpus: the corpus checked against its own contracts.

Two families of meta tests live here, the port of the Java ``a2a-t-corpus`` double meta layer
(design §6 Q6/§7/§8.4/§8.6):

* :mod:`tests.corpus.meta.test_contract` (Java ``CorpusContractTest``) — the corpus is checked
  against its own contracts instead of the production code, so a hole in the corpus is caught
  before it silently narrows the suites.
* :mod:`tests.corpus.meta.test_sensitivity` (Java ``CorpusSensitivitySelfTest``) — the engine is
  proven sensitive: every class of expectation assertion goes red on one flipped value.
"""
