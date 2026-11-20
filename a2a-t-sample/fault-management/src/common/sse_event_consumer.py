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

"""Normalize A2A StreamResponse events into simple dicts for client-side consumption."""

from __future__ import annotations

from a2a.types import StreamResponse, TaskState


def normalize_event(stream_response: StreamResponse) -> dict[str, object]:
    """Convert a StreamResponse (status/message/artifact/task) into a flat dict with a 'kind' field."""
    if stream_response.HasField("status_update"):
        status = stream_response.status_update.status
        state_name = TaskState.Name(status.state)
        text = ""
        if status.HasField("message") and status.message.parts:
            text = status.message.parts[0].text
        return {
            "kind": "status",
            "task_id": stream_response.status_update.task_id,
            "state": state_name,
            "text": text,
        }

    if stream_response.HasField("message"):
        text = ""
        if stream_response.message.parts:
            text = stream_response.message.parts[0].text
        return {
            "kind": "message",
            "text": text,
        }

    if stream_response.HasField("artifact_update"):
        artifact = stream_response.artifact_update.artifact
        return {
            "kind": "artifact",
            "task_id": stream_response.artifact_update.task_id,
            "artifact_id": artifact.artifact_id,
            "name": artifact.name,
        }

    if stream_response.HasField("task"):
        task = stream_response.task
        state_name = ""
        if task.HasField("status"):
            state_name = TaskState.Name(task.status.state)
        return {
            "kind": "status",
            "task_id": task.id,
            "state": state_name,
        }

    raise ValueError("Unsupported StreamResponse: no recognized event field set")
