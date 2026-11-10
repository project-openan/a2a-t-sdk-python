"""Vendor-NMS task inventory and cancellation for one AI evaluation instance.

The vendor ``GET /tasks`` list is the authority for a Task that still holds quota; the local
``task_snapshots`` table only supplies ownership. Cancellation is deliberately sent once and then
re-checked: the vendor answers the known empty 500 on a repeat, while the first request already
succeeded and its task list is only eventually consistent.
"""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import Generator, Mapping, Sequence
from contextlib import contextmanager

from demo.web.nms_tasks import cancel_nms_task, list_active_tasks
from shared.config import SampleConfig, load_config

from eval.osp.ai.types import Profile

#: Re-check delay after the single cancel request; matches the field cleanup strategy.
CANCEL_RECHECK_SECONDS: tuple[float, ...] = (10.0, 30.0, 60.0)


def active_tasks(profile: Profile) -> list[dict[str, object]]:
    """Return every non-terminal vendor Task, newest first.

    Raises:
        shared.config.ValueError: when the profile has no usable NMS configuration.
        protocol.nms_client.NmsError: when the vendor rejects or cannot be reached.
    """
    config = _config(profile)
    return asyncio.run(list_active_tasks(config))


def cancel_task(profile: Profile, task_id: str) -> bool:
    """Cancel one vendor Task and return whether the request was accepted."""
    config = _config(profile)
    return asyncio.run(cancel_nms_task(config, task_id))


def cancel_and_wait(
    profile: Profile,
    task_id: str,
    *,
    delays: Sequence[float] = CANCEL_RECHECK_SECONDS,
    sleep: object = time.sleep,
) -> dict[str, object]:
    """Cancel one Task once, then re-check until it leaves the active list or the budget ends.

    Returns a record with ``accepted``, ``cleared``, ``state`` and ``checks`` so the report can
    distinguish "cancel accepted" from "Task observed terminal".
    """
    accepted = cancel_task(profile, task_id)
    checks: list[dict[str, object]] = []
    for delay in delays:
        if callable(sleep):
            sleep(delay)
        rows = active_tasks(profile)
        match = next((row for row in rows if row.get("taskId") == task_id), None)
        checks.append(
            {
                "afterSeconds": delay,
                "active": match is not None,
                "state": match.get("state") if match else "",
            }
        )
        if match is None:
            return {"taskId": task_id, "accepted": accepted, "cleared": True, "state": "", "checks": checks}
    return {
        "taskId": task_id,
        "accepted": accepted,
        "cleared": False,
        "state": checks[-1]["state"] if checks else "",
        "checks": checks,
    }


def _config(profile: Profile) -> SampleConfig:
    """Load the sample configuration with this profile's environment temporarily applied."""
    with _profile_env(profile):
        return load_config(env_path=profile.source_path)


@contextmanager
def _profile_env(profile: Profile) -> Generator[None]:
    """Temporarily overlay the profile environment on the process environment.

    ``shared.config.load_config`` reads process environment before the ``.env`` file; the overlay
    makes the vendor credentials in the profile win without leaking them into later calls.
    """
    keys = set(profile.env) | {
        "A2AT_SAMPLE_RUN_DIR",
        "A2AT_SAMPLE_RUNS_DIR",
        "A2AT_SAMPLE_WEBHOOK_HOST",
        "A2AT_SAMPLE_WEBHOOK_PORT",
        "A2AT_SAMPLE_OSS_WEBHOOK_PORT",
        "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL",
        "A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL",
        "A2AT_SAMPLE_PROTOCOL_MODE",
        "A2AT_SAMPLE_MODEL_MODE",
        "A2AT_LANGUAGE",
    }
    overlay: Mapping[str, str] = {
        **{str(key): str(value) for key, value in profile.env.items()},
        "A2AT_SAMPLE_RUN_DIR": str(profile.run_dir),
        "A2AT_SAMPLE_RUNS_DIR": str(profile.runs_dir),
        "A2AT_SAMPLE_WEBHOOK_HOST": profile.webhook_host,
        "A2AT_SAMPLE_WEBHOOK_PORT": str(profile.webhook_port),
        "A2AT_SAMPLE_OSS_WEBHOOK_PORT": str(profile.oss_webhook_port),
        "A2AT_SAMPLE_WEBHOOK_PUBLIC_URL": profile.webhook_public_url,
        "A2AT_SAMPLE_OSS_WEBHOOK_PUBLIC_URL": profile.oss_webhook_public_url,
        "A2AT_SAMPLE_PROTOCOL_MODE": profile.protocol,
        "A2AT_SAMPLE_MODEL_MODE": profile.model_mode,
        "A2AT_LANGUAGE": profile.language,
    }
    saved: dict[str, str | None] = {key: os.environ.get(key) for key in keys | set(overlay)}
    try:
        os.environ.update(overlay)
        yield
    finally:
        for key, value in saved.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
