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

"""Live-LLM corpus family (D24): real-endpoint sibling of the offline corpus suites.

Everything except the model itself is production assembly — the live engine drives the same
:class:`~tests.corpus.assemblers.TaskApiAssembler` real-builder wiring as the offline engine,
wrapped in the recording client — and the whole family is opt-in through the ``A2AT_TEST_LLM_*``
environment variables (same names as Java, D24): absent any of the three required variables every
live test skips, never fails and never hangs, so ``pytest`` and CI stay offline-deterministic.
"""
