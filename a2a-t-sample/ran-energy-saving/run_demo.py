#!/usr/bin/env python3
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

"""Cross-platform, non-blocking launcher for the ran-energy-saving (Task-T) sample.

The implementation lives in :mod:`demo.run_demo`; this shim keeps the documented
``python run_demo.py`` command working from the sample directory.
"""

from __future__ import annotations

from demo.run_demo import main

if __name__ == "__main__":
    raise SystemExit(main())
