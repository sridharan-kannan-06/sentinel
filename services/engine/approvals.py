"""The human approval queue for tier two actions.

An action that leaves the hospital is parked here with the exact payload that
would be sent, so the person approving is looking at the real thing rather than
a description of it. Approving is not the same as sending: approval records a
decision and releases the action, and the send happens afterwards and is recorded
separately. Conflating the two would make it impossible to tell an approval that
was never acted on from one that failed.

A reason is mandatory on both paths. An approval queue where "approve" needs no
justification produces an audit trail that says only that somebody clicked.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from google.cloud import firestore
from pydantic import BaseModel, Field

import ledger

APPROVALS = "approvals"


class ApprovalStatus(str, Enum):
    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"


class ApprovalRequest(BaseModel):
    id: str
    obligation_id: str
    agent: str
    action: str
    risk_tier: str
    # The exact bytes that would go out. Shown verbatim in the approval queue.
    payload: dict[str, Any] = Field(default_factory=dict)
    rendered_payload: str = ""
    policy_decision_id: str | None = None
    policy_hash: str = ""
    status: ApprovalStatus = ApprovalStatus.PENDING
    requested_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    decided_at: datetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None


class Decision(BaseModel):
    decided_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)


def _db() -> firestore.Client:
    return ledger.client()


def create(
    obligation_id: str,
    agent: str,
    action: str,
    risk_tier: str,
    payload: dict[str, Any],
    rendered_payload: str,
    policy_decision_id: str | None,
    policy_hash: str,
    trace_id: str | None = None,
) -> ApprovalRequest:
    request = ApprovalRequest(
        id=f"APR-{uuid.uuid4().hex[:10]}",
        obligation_id=obligation_id,
        agent=agent,
        action=action,
        risk_tier=risk_tier,
        payload=payload,
        rendered_payload=rendered_payload,
        policy_decision_id=policy_decision_id,
        policy_hash=policy_hash,
    )
    _db().collection(APPROVALS).document(request.id).set(
        ledger._to_firestore(request.model_dump(mode="python"))
    )
    ledger.record_entry(
        obligation_id,
        actor="system:policy",
        action="approval.requested",
        reason=f"{action!r} by {agent!r} is tier {risk_tier} and is waiting for a "
        f"human decision as {request.id}.",
        policy_decision_id=policy_decision_id,
        trace_id=trace_id,
    )
    return request


def get(approval_id: str) -> ApprovalRequest | None:
    snapshot = _db().collection(APPROVALS).document(approval_id).get()
    if not snapshot.exists:
        return None
    return ApprovalRequest.model_validate(snapshot.to_dict())


def list_pending(limit: int = 100) -> list[ApprovalRequest]:
    query = (
        _db()
        .collection(APPROVALS)
        .where(filter=firestore.FieldFilter("status", "==", ApprovalStatus.PENDING.value))
        .limit(limit)
    )
    requests = [ApprovalRequest.model_validate(d.to_dict()) for d in query.stream()]
    return sorted(requests, key=lambda r: r.requested_at)


class AlreadyDecided(Exception):
    """Raised when a second decision is attempted on the same request."""


def decide(
    approval_id: str,
    approved: bool,
    decision: Decision,
    trace_id: str | None = None,
) -> ApprovalRequest:
    """Record a human decision. Transactional so it cannot be decided twice."""
    ref = _db().collection(APPROVALS).document(approval_id)

    @firestore.transactional
    def _decide(transaction: firestore.Transaction) -> ApprovalRequest:
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            raise KeyError(f"approval {approval_id} not found")
        request = ApprovalRequest.model_validate(snapshot.to_dict())
        if request.status is not ApprovalStatus.PENDING:
            raise AlreadyDecided(
                f"{approval_id} was already {request.status.value.lower()} by "
                f"{request.decided_by} at {request.decided_at}"
            )
        request.status = ApprovalStatus.APPROVED if approved else ApprovalStatus.DENIED
        request.decided_at = datetime.now(timezone.utc)
        request.decided_by = decision.decided_by
        request.decision_reason = decision.reason
        transaction.update(
            ref,
            ledger._to_firestore(
                {
                    "status": request.status,
                    "decided_at": request.decided_at,
                    "decided_by": request.decided_by,
                    "decision_reason": request.decision_reason,
                }
            ),
        )
        return request

    request = _decide(_db().transaction())

    ledger.record_entry(
        request.obligation_id,
        actor=f"human:{decision.decided_by}",
        action="approval.approved" if approved else "approval.denied",
        reason=f"{request.action!r} by {request.agent!r} was "
        f"{'approved' if approved else 'denied'}: {decision.reason}",
        policy_decision_id=request.policy_decision_id,
        trace_id=trace_id,
    )
    return request
