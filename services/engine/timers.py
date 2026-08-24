"""Per-obligation timers on Cloud Tasks.

Cloud Scheduler is cron and cannot express "wake up about this specific
obligation at 16:00", so each SLA checkpoint becomes its own task with an
explicit scheduleTime. Tasks can still be lost, which is why the reconciliation
sweep re-derives checkpoints from the obligation and treats the ledger, not the
timer, as the source of truth.
"""

from __future__ import annotations

import json
from datetime import datetime

from google.cloud import tasks_v2
from google.protobuf import timestamp_pb2

import logs
from config import get_settings
from models import Checkpoint

_client: tasks_v2.CloudTasksClient | None = None


def client() -> tasks_v2.CloudTasksClient:
    global _client
    if _client is None:
        _client = tasks_v2.CloudTasksClient()
    return _client


def _queue_path() -> str:
    settings = get_settings()
    return client().queue_path(settings.project_id, settings.region, settings.tasks_queue)


def schedule_checkpoint(
    obligation_id: str,
    checkpoint: Checkpoint,
    attempt: int = 0,
    trace_id: str | None = None,
) -> str | None:
    """Enqueue one wake-up. Returns the task name, or None if it could not be enqueued."""
    settings = get_settings()
    if not settings.engine_base_url:
        logs.warning(
            "ENGINE_BASE_URL is unset so no timer was enqueued",
            obligation_id=obligation_id,
            checkpoint=checkpoint.kind.value,
            trace_id=trace_id,
        )
        return None

    schedule_time = timestamp_pb2.Timestamp()
    schedule_time.FromDatetime(checkpoint.at)

    body = json.dumps(
        {
            "obligation_id": obligation_id,
            "checkpoint_kind": checkpoint.kind.value,
            "scheduled_for": checkpoint.at.isoformat(),
        }
    ).encode()

    # A deterministic name makes a duplicate enqueue a no-op rather than a second
    # nudge to the same person. The attempt suffix lets the sweep re-arm a
    # checkpoint whose original task was lost.
    task_id = f"{obligation_id}-{checkpoint.kind.value}-{attempt}"

    task: dict = {
        "name": f"{_queue_path()}/tasks/{task_id}",
        "schedule_time": schedule_time,
        "http_request": {
            "http_method": tasks_v2.HttpMethod.POST,
            "url": f"{settings.engine_base_url}/wake",
            "headers": {"Content-Type": "application/json"},
            "body": body,
            "oidc_token": {
                "service_account_email": settings.tasks_service_account,
                "audience": settings.engine_base_url,
            },
        },
    }

    try:
        created = client().create_task(parent=_queue_path(), task=task)
        logs.info(
            "timer enqueued",
            obligation_id=obligation_id,
            checkpoint=checkpoint.kind.value,
            scheduled_for=checkpoint.at.isoformat(),
            task=created.name,
            trace_id=trace_id,
        )
        return created.name
    except Exception as exc:
        # A failed enqueue must not fail the obligation write. The sweep exists
        # precisely so that a missing timer is recoverable.
        logs.error(
            "timer enqueue failed, leaving it for the reconciliation sweep",
            obligation_id=obligation_id,
            checkpoint=checkpoint.kind.value,
            error=str(exc),
            trace_id=trace_id,
        )
        return None


def schedule_all(
    obligation_id: str, checkpoints: list[Checkpoint], trace_id: str | None = None
) -> list[str]:
    names: list[str] = []
    for checkpoint in checkpoints:
        if checkpoint.fired:
            continue
        name = schedule_checkpoint(obligation_id, checkpoint, trace_id=trace_id)
        if name:
            names.append(name)
    return names


def parse_iso(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
