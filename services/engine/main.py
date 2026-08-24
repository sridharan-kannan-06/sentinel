"""Sentinel engine: the obligation ledger, its state machine, and its timers.

The Interpreter runs here but cannot write: every status change in the service
goes through `ledger.transition`, and the Coordinator, Policy Engine, and
Evidence Gate that arrive in later phases will do the same.

The health endpoint is `/health` and deliberately not `/healthz`. Google's edge
intercepts `/healthz` on run.app hostnames and answers with its own 404 page
before the request reaches the container, which is indistinguishable from a
service that failed to deploy.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import interpreter
import ledger
import logs
import notify
import timers
from config import get_settings
from models import (
    CheckpointKind,
    IllegalTransition,
    ObligationCreate,
    ObligationStatus,
    WakePayload,
)

def policy_path() -> Path | None:
    """Locate policy.yaml in both the repo checkout and the container image.

    In the repo the file sits two levels up at policy/policy.yaml; in the image
    the service is flattened into /app, where indexing a fixed number of parents
    walks off the end of the path. Search the parents that actually exist.
    """
    override = os.environ.get("POLICY_PATH")
    if override:
        candidate = Path(override)
        return candidate if candidate.exists() else None

    here = Path(__file__).resolve()
    candidates = [here.parent / "policy.yaml"]
    candidates += [parent / "policy" / "policy.yaml" for parent in here.parents]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def policy_config_hash() -> str:
    """Hash of the policy config, logged at boot and surfaced in the trust panel.

    The policy engine lands in Phase 2. Until the file exists this reports
    "absent" rather than a hash of nothing, so the UI never shows a hash that
    corresponds to no policy.
    """
    path = policy_path()
    if path is None:
        return "absent"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def trace_id_from(request: Request) -> str | None:
    """Cloud Run sets X-Cloud-Trace-Context as TRACE_ID/SPAN_ID;o=1."""
    header = request.headers.get("X-Cloud-Trace-Context")
    if not header:
        return None
    return header.split("/")[0] or None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logs.info(
        "engine starting",
        service="sentinel-engine",
        git_sha=settings.git_sha,
        policy_config_hash=policy_config_hash(),
        gemini_model=settings.gemini_model,
        gemini_location=settings.gemini_location,
        evidence_gate=settings.evidence_gate,
    )
    if not settings.evidence_gate_enabled:
        logs.warning(
            "EVIDENCE_GATE IS OFF. Closure proposals will be accepted without "
            "authoritative evidence. This setting exists only for the ablation "
            "demonstration and must never be off in a real deployment."
        )
    yield


app = FastAPI(title="sentinel-engine", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "service": "sentinel-engine",
        "git_sha": settings.git_sha,
        "policy_config_hash": policy_config_hash(),
        "evidence_gate": "on" if settings.evidence_gate_enabled else "off",
        "gemini_model": settings.gemini_model,
        "gemini_location": settings.gemini_location,
    }


@app.post("/obligations", status_code=201)
def create_obligation(payload: ObligationCreate, request: Request) -> dict:
    trace_id = trace_id_from(request)
    try:
        obligation = ledger.create_obligation(
            payload,
            actor="system:ingest",
            reason="Obligation admitted from inbound event",
            trace_id=trace_id,
        )
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logs.info(
        "obligation created",
        obligation_id=obligation.id,
        type=obligation.type,
        subject_token=obligation.subject_token,
        status=obligation.status.value,
        deadline=obligation.deadline.isoformat(),
        trace_id=trace_id,
    )

    # The timer is enqueued after the write commits. If the enqueue fails the
    # obligation still exists and the sweep will re-arm it.
    task_names = timers.schedule_all(obligation.id, obligation.checkpoints, trace_id)

    return {"obligation": obligation.model_dump(mode="json"), "tasks": task_names}


@app.post("/wake")
def wake(payload: WakePayload, request: Request) -> dict:
    """A Cloud Task fired for one checkpoint on one obligation.

    The checkpoint kind decides the status change and the audience. Delivery is
    attempted but not required: an obligation is at risk whether or not the
    notification reached anyone, so a failed send is recorded and the transition
    still happens.
    """
    trace_id = trace_id_from(request)
    obligation = ledger.get_obligation(payload.obligation_id)
    if obligation is None:
        logs.warning(
            "wake for unknown obligation",
            obligation_id=payload.obligation_id,
            trace_id=trace_id,
        )
        raise HTTPException(status_code=404, detail="obligation not found")

    if obligation.status in {
        ObligationStatus.CLOSED,
        ObligationStatus.REJECTED,
        ObligationStatus.CANCELLED,
    }:
        # A late timer against a settled obligation is normal, not an error.
        ledger.record_entry(
            obligation.id,
            actor="system:timer",
            action="wake.ignored",
            reason=f"Checkpoint {payload.checkpoint_kind.value} fired after the "
            f"obligation reached {obligation.status.value}",
            trace_id=trace_id,
        )
        return {"obligation_id": obligation.id, "status": obligation.status.value, "acted": False}

    kind = payload.checkpoint_kind

    # The checkpoint decides the target status. A nudge means the obligation is
    # at risk, a breach means the deadline passed, and an escalation does not
    # move the status because the obligation is already breached; it widens the
    # audience instead.
    target = {
        CheckpointKind.NUDGE: ObligationStatus.AT_RISK,
        CheckpointKind.BREACH: ObligationStatus.BREACHED,
        CheckpointKind.ESCALATE: obligation.status,
    }[kind]

    audience = {
        CheckpointKind.NUDGE: obligation.owner_id,
        CheckpointKind.BREACH: f"{obligation.owner_id} and the department coordinator",
        CheckpointKind.ESCALATE: "the supervisor",
    }[kind]

    settings = get_settings()
    notifier = notify.get_notifier()
    recipient = settings.notify_to or obligation.owner_id
    subject = (
        f"[Sentinel] {kind.value.upper()} on {obligation.type} for {obligation.subject_token}"
    )
    body = (
        f"Obligation {obligation.id} is {kind.value} at "
        f"{payload.scheduled_for.isoformat()}.\n\n"
        f"Type: {obligation.type}\n"
        f"Subject: {obligation.subject_token}\n"
        f"Owner: {obligation.owner_role} ({obligation.owner_id})\n"
        f"Deadline: {obligation.deadline.isoformat()}\n"
        f"Opened: {obligation.created_at.isoformat()}\n\n"
        f"This will close only when the following is produced:\n"
        f"  {obligation.required_evidence}\n\n"
        f"Sentinel proposes and chases. It does not decide clinical matters.\n"
    )

    reference: str | None = None
    try:
        reference = notifier.send(
            notify.Notification(
                to=recipient,
                subject=subject,
                body=body,
                obligation_id=obligation.id,
                kind=kind.value,
                trace_id=trace_id,
            )
        )
    except notify.NotifierError as exc:
        # Failing to reach a person must not stop the state change. The
        # obligation is still at risk whether or not the email went out.
        logs.error(
            "notification failed but the checkpoint still fired",
            obligation_id=obligation.id,
            checkpoint=kind.value,
            error=str(exc),
            trace_id=trace_id,
        )

    updated = ledger.transition(
        obligation.id,
        target=target,
        actor="system:timer",
        action=f"checkpoint.{kind.value}",
        reason=(
            f"Checkpoint {kind.value} scheduled for {payload.scheduled_for.isoformat()} "
            f"fired. Notified {audience} via {notifier.name}."
        ),
        evidence_ref=reference,
        trace_id=trace_id,
        checkpoint_fired=kind.value,
    )

    logs.info(
        "wake handled",
        obligation_id=obligation.id,
        checkpoint=kind.value,
        scheduled_for=payload.scheduled_for.isoformat(),
        status_before=obligation.status.value,
        status_after=updated.status.value,
        notifier=notifier.name,
        notification_ref=reference,
        trace_id=trace_id,
    )
    return {
        "obligation_id": obligation.id,
        "status": updated.status.value,
        "acted": True,
        "notified": reference is not None,
        "notification_ref": reference,
    }


class PubSubMessage(BaseModel):
    data: str | None = None
    attributes: dict[str, str] = {}
    messageId: str | None = None


class PubSubPush(BaseModel):
    message: PubSubMessage
    subscription: str | None = None


@app.post("/interpret")
async def interpret_event(push: PubSubPush, request: Request) -> dict:
    """Pub/Sub push target. One de-identified event becomes obligations.

    Everything arriving here has already cleared Model Armor and Sensitive Data
    Protection at the ingest boundary; the topic carries nothing else. A message
    that cannot be decoded is acknowledged rather than retried forever, because
    redelivering a malformed payload will not make it parse.
    """
    trace_id = trace_id_from(request)

    if not push.message.data:
        logs.warning("interpret received a message with no data", trace_id=trace_id)
        return {"accepted": 0, "rejected": 0, "reason": "empty message"}

    try:
        event = json.loads(base64.b64decode(push.message.data).decode("utf-8"))
    except (ValueError, binascii.Error) as exc:
        logs.error(
            "interpret could not decode the message, acknowledging to stop redelivery",
            error=str(exc),
            message_id=push.message.messageId,
            trace_id=trace_id,
        )
        return {"accepted": 0, "rejected": 0, "reason": "undecodable message"}

    result = await interpreter.interpret(event, trace_id=trace_id)

    # The exact prompt is logged because it is a demonstration beat later: it is
    # everything the model saw, and it contains no names.
    logs.info(
        "interpreter prompt",
        idempotency_key=event.get("idempotency_key"),
        prompt=result.prompt,
        trace_id=trace_id,
    )

    created: list[str] = []
    for proposal in result.accepted:
        try:
            obligation = ledger.create_obligation(
                proposal,
                actor="agent:interpreter",
                reason=f"Proposed from event {event.get('idempotency_key')}",
                trace_id=trace_id,
            )
        except IllegalTransition as exc:
            logs.error(
                "could not admit a validated proposal",
                idempotency_key=event.get("idempotency_key"),
                error=str(exc),
                trace_id=trace_id,
            )
            continue
        created.append(obligation.id)
        timers.schedule_all(obligation.id, obligation.checkpoints, trace_id)

    logs.info(
        "event interpreted",
        idempotency_key=event.get("idempotency_key"),
        created=created,
        rejected=len(result.rejected),
        trace_id=trace_id,
    )
    return {
        "accepted": len(created),
        "rejected": len(result.rejected),
        "obligation_ids": created,
        "rejections": [r.reason for r in result.rejected],
    }


@app.get("/obligations")
def list_obligations(limit: int = 100) -> dict:
    obligations = ledger.list_obligations(limit=limit)
    return {
        "count": len(obligations),
        "obligations": [o.model_dump(mode="json") for o in obligations],
    }


@app.get("/obligations/{obligation_id}")
def get_obligation(obligation_id: str) -> dict:
    obligation = ledger.get_obligation(obligation_id)
    if obligation is None:
        raise HTTPException(status_code=404, detail="obligation not found")
    entries = ledger.get_ledger(obligation_id)
    return {
        "obligation": obligation.model_dump(mode="json"),
        "ledger": [e.model_dump(mode="json") for e in entries],
    }
