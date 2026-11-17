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

"""Data-driven corpus suites: one parametrized test per shipped corpus record.

Each family directory of ``tests/resources/negotiation-cases/`` becomes one suite module — the
port of the Java ``*CorpusSuiteTest`` classes — driving every expanded record through the corpus
engines of :mod:`tests.corpus.engine` (see ``docs-local/python-port-plan-draft.md`` P8 and D21
for the corpus decisions).
"""
