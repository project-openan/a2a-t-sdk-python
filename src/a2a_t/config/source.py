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

from __future__ import annotations

from pathlib import Path

from dotenv import dotenv_values

from a2a_t.config.errors import ConfigFileNotFoundError


class DotEnvConfigSource:
    """Read configuration only from a .env file."""

    @staticmethod
    def load(path: Path) -> dict[str, str]:
        """Load non-empty key/value pairs from the given .env file."""
        if not path.exists():
            raise ConfigFileNotFoundError(path)

        file_values = dotenv_values(path)
        return {key: value for key, value in file_values.items() if key is not None and value is not None}
