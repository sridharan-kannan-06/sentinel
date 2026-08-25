"""Sentinel engine: the obligation ledger, its state machine, and its timers.

The Interpreter, the Coordinator, the Policy Engine, and the Evidence Gate all
run here, and none of them writes an obligation's status directly. Every status
change goes through `ledger.transition`, which is the only function permitted to
touch that field, and CLOSED is reachable from exactly one predecessor.

The health endpoint is `/health` and deliberately not `/healthz`. Google's edge
intercepts `/healthz` on run.app hostnames and answers with its own 404 page
before the request reaches the container, which is indistinguishable from a
service that failed to deploy.
"""

from __future__ import annotations

import base64
import binascii
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, HTTPException, Request
from pydantic import BaseModel

import approvals
import coordinator
import escalation
import evidence as evidence_gate
import interpreter
import ledger
import logs
import policy
import timers
from config import get_settings
from models import (
    CheckpointKind,
    IllegalTransition,
    ObligationCreate,
    ObligationStatus,
    WakePayload,
)


def policy_config_hash() -> str:
    """Hash of the policy that is actually in force, for the trust panel.

    Delegated to the policy module so the hash shown in the UI is the same one
    the policy engine used to decide, not a second reading of the file.
    """
    return policy.policy_hash()


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
        "policy_version": policy.policy_version(),
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

    # The ladder decides who hears about this and the rate limit decides whether
    # they hear about it again. Both are configuration, and both are counted from
    # the ledger, so neither resets when this service scales to zero.
    outcome = escalation.notify_rung(obligation, kind, subject, body, trace_id=trace_id)

    delivered = ", ".join(outcome["delivered"]) or "nobody"
    suppressed = (
        f" Suppressed by the rate limit: {', '.join(outcome['suppressed'])}."
        if outcome["suppressed"]
        else ""
    )

    updated = ledger.transition(
        obligation.id,
        target=target,
        actor="system:timer",
        action=f"checkpoint.{kind.value}",
        reason=(
            f"Checkpoint {kind.value} scheduled for {payload.scheduled_for.isoformat()} "
            f"fired. Notified {delivered} via {outcome['notifier']}.{suppressed}"
        ),
        evidence_ref=outcome["references"][0] if outcome["references"] else None,
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
        roles=outcome["roles"],
        delivered=outcome["delivered"],
        suppressed=outcome["suppressed"],
        failed=outcome["failed"],
        trace_id=trace_id,
    )
    return {
        "obligation_id": obligation.id,
        "status": updated.status.value,
        "acted": True,
        "delivered": outcome["delivered"],
        "suppressed": outcome["suppressed"],
        "failed": outcome["failed"],
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


@app.post("/coordinate/{obligation_id}")
async def coordinate(obligation_id: str, request: Request) -> dict:
    """Route one obligation to the department that owes its next move.

    Always returns 200 for an obligation that exists, including when routing
    failed or policy refused. Those are outcomes the ledger records, not
    transport errors, and reporting them as failures would make a policy denial
    look like an outage.
    """
    trace_id = trace_id_from(request)
    obligation = ledger.get_obligation(obligation_id)
    if obligation is None:
        raise HTTPException(status_code=404, detail="obligation not found")

    if obligation.status in {
        ObligationStatus.CLOSED,
        ObligationStatus.REJECTED,
        ObligationStatus.CANCELLED,
    }:
        return {
            "obligation_id": obligation_id,
            "outcome": "settled",
            "detail": f"Obligation is already {obligation.status.value}",
        }

    result = await coordinator.coordinate(obligation, trace_id=trace_id)
    return {"obligation_id": obligation_id, **result.model_dump(mode="json")}


@app.get("/policy")
def policy_summary() -> dict:
    """The authority model as the running service sees it."""
    return {
        "policy_hash": policy.policy_hash(),
        "policy_version": policy.policy_version(),
        "agents": {
            role: sorted(policy.allowed_actions(role))
            for role in sorted(policy.known_agents())
        },
    }


class EvidenceSubmission(BaseModel):
    source: str
    external_reference: str
    observed_at: datetime
    subject_token: str
    assertion: str


@app.post("/obligations/{obligation_id}/evidence")
def submit_evidence(
    obligation_id: str, submission: EvidenceSubmission, request: Request
) -> dict:
    """Offer a fact that may close an obligation.

    The obligation moves to PENDING_EVIDENCE first, so the attempt is on the
    record whether or not it succeeds. Only a verdict from the gate can then move
    it to CLOSED, and CLOSED has no other legal predecessor.
    """
    trace_id = trace_id_from(request)
    obligation = ledger.get_obligation(obligation_id)
    if obligation is None:
        raise HTTPException(status_code=404, detail="obligation not found")
    if obligation.status in {
        ObligationStatus.CLOSED,
        ObligationStatus.REJECTED,
        ObligationStatus.CANCELLED,
    }:
        raise HTTPException(
            status_code=409, detail=f"obligation is already {obligation.status.value}"
        )

    fact = evidence_gate.Evidence(**submission.model_dump())
    verdict = evidence_gate.evaluate(obligation, fact)

    if obligation.status is not ObligationStatus.PENDING_EVIDENCE:
        obligation = ledger.transition(
            obligation_id,
            target=ObligationStatus.PENDING_EVIDENCE,
            actor="system:evidence",
            action="evidence.submitted",
            reason=(
                f"Evidence offered from {fact.source!r} with reference "
                f"{fact.external_reference!r}."
            ),
            evidence_ref=verdict.evidence_hash,
            trace_id=trace_id,
        )

    for check in verdict.checks:
        ledger.record_entry(
            obligation_id,
            actor="system:evidence",
            action="evidence.check.passed" if check.passed else "evidence.check.failed",
            reason=f"{check.name}: {check.detail}",
            evidence_ref=verdict.evidence_hash,
            trace_id=trace_id,
        )

    if not verdict.accepted:
        logs.warning(
            "evidence rejected",
            obligation_id=obligation_id,
            summary=verdict.summary,
            evidence_hash=verdict.evidence_hash,
            trace_id=trace_id,
        )
        return {
            "obligation_id": obligation_id,
            "accepted": False,
            "status": ObligationStatus.PENDING_EVIDENCE.value,
            "gate_enabled": verdict.gate_enabled,
            "summary": verdict.summary,
            "checks": [c.model_dump() for c in verdict.checks],
            "evidence_hash": verdict.evidence_hash,
        }

    gate_note = (
        ""
        if verdict.gate_enabled
        else " EVIDENCE GATE WAS OFF: this closed without qualifying evidence."
    )
    closed = ledger.transition(
        obligation_id,
        target=ObligationStatus.CLOSED,
        actor="system:evidence",
        action="obligation.closed",
        reason=(
            f"{verdict.summary}. Closed on {fact.source!r} reference "
            f"{fact.external_reference!r}.{gate_note}"
        ),
        evidence_ref=verdict.evidence_hash,
        trace_id=trace_id,
    )
    logs.info(
        "obligation closed on evidence",
        obligation_id=obligation_id,
        gate_enabled=verdict.gate_enabled,
        evidence_hash=verdict.evidence_hash,
        source=fact.source,
        trace_id=trace_id,
    )
    return {
        "obligation_id": obligation_id,
        "accepted": True,
        "status": closed.status.value,
        "gate_enabled": verdict.gate_enabled,
        "summary": verdict.summary,
        "checks": [c.model_dump() for c in verdict.checks],
        "evidence_hash": verdict.evidence_hash,
    }


@app.post("/reconcile")
def reconcile(request: Request, limit: int = 50) -> dict:
    """Repair obligations whose next checkpoint passed without firing.

    Cloud Tasks can drop a task silently, so the obligation rather than the timer
    is the source of truth. The sweep re-derives the checkpoint from the
    obligation and re-arms it, which is why losing a timer costs one sweep
    interval rather than the whole obligation.
    """
    trace_id = trace_id_from(request)
    now = datetime.now(timezone.utc)
    repaired: list[dict] = []
    examined = 0

    for obligation in ledger.overdue_checkpoints(now, limit=limit):
        examined += 1
        checkpoint = obligation.next_checkpoint
        if checkpoint is None:
            continue

        attempt = ledger.count_entries(obligation.id, "sweep.repaired") + 1
        task = timers.schedule_checkpoint(
            obligation.id, checkpoint, attempt=attempt, trace_id=trace_id
        )
        ledger.record_entry(
            obligation.id,
            actor="system:sweep",
            action="sweep.repaired",
            reason=(
                f"Checkpoint {checkpoint.kind.value} was due at "
                f"{checkpoint.at.isoformat()} and had not fired. The sweep re-armed it "
                f"as attempt {attempt}. The timer was lost; the obligation was not."
            ),
            evidence_ref=task,
            trace_id=trace_id,
        )
        repaired.append(
            {
                "obligation_id": obligation.id,
                "checkpoint": checkpoint.kind.value,
                "due_at": checkpoint.at.isoformat(),
                "attempt": attempt,
                "task": task,
            }
        )
        logs.warning(
            "sweep repaired a missed checkpoint",
            obligation_id=obligation.id,
            checkpoint=checkpoint.kind.value,
            due_at=checkpoint.at.isoformat(),
            attempt=attempt,
            trace_id=trace_id,
        )

    logs.info(
        "reconciliation sweep complete",
        examined=examined,
        repaired=len(repaired),
        trace_id=trace_id,
    )
    return {"examined": examined, "repaired": len(repaired), "details": repaired}


@app.get("/approvals")
def list_approvals(limit: int = 100) -> dict:
    pending = approvals.list_pending(limit=limit)
    return {
        "count": len(pending),
        "approvals": [a.model_dump(mode="json") for a in pending],
    }


@app.get("/approvals/{approval_id}")
def get_approval(approval_id: str) -> dict:
    found = approvals.get(approval_id)
    if found is None:
        raise HTTPException(status_code=404, detail="approval not found")
    return found.model_dump(mode="json")


def _decide_approval(
    approval_id: str, approved: bool, decision: approvals.Decision, request: Request
) -> dict:
    trace_id = trace_id_from(request)
    try:
        result = approvals.decide(approval_id, approved, decision, trace_id=trace_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except approvals.AlreadyDecided as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    logs.info(
        "approval decided",
        approval_id=approval_id,
        obligation_id=result.obligation_id,
        approved=approved,
        decided_by=decision.decided_by,
        trace_id=trace_id,
    )
    return result.model_dump(mode="json")


@app.post("/approvals/{approval_id}/approve")
def approve(approval_id: str, decision: approvals.Decision, request: Request) -> dict:
    return _decide_approval(approval_id, True, decision, request)


@app.post("/approvals/{approval_id}/deny")
def deny(approval_id: str, decision: approvals.Decision, request: Request) -> dict:
    return _decide_approval(approval_id, False, decision, request)
