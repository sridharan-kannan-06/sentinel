"""Obligation domain model and the legal state machine.

The transition table is the enforcement point for the second invariant in
this system: the model may propose closure, but only an authoritative external
fact may close. CLOSED has exactly one legal predecessor, PENDING_EVIDENCE, so
no code path can move an obligation from OPEN straight to CLOSED however
confident a model is. `assert_closed_is_gated` proves that property from the
table itself rather than trusting a reviewer to notice.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ObligationStatus(str, Enum):
    PROPOSED = "PROPOSED"
    OPEN = "OPEN"
    WAITING_EXTERNAL = "WAITING_EXTERNAL"
    BLOCKED = "BLOCKED"
    AT_RISK = "AT_RISK"
    BREACHED = "BREACHED"
    PENDING_EVIDENCE = "PENDING_EVIDENCE"
    CLOSED = "CLOSED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class RiskTier(str, Enum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class CheckpointKind(str, Enum):
    NUDGE = "nudge"
    BREACH = "breach"
    ESCALATE = "escalate"


TERMINAL_STATUSES: frozenset[ObligationStatus] = frozenset(
    {ObligationStatus.CLOSED, ObligationStatus.REJECTED, ObligationStatus.CANCELLED}
)

S = ObligationStatus

LEGAL_TRANSITIONS: dict[ObligationStatus, frozenset[ObligationStatus]] = {
    S.PROPOSED: frozenset({S.OPEN, S.REJECTED, S.CANCELLED}),
    S.OPEN: frozenset(
        {S.WAITING_EXTERNAL, S.BLOCKED, S.AT_RISK, S.BREACHED, S.PENDING_EVIDENCE, S.CANCELLED}
    ),
    S.WAITING_EXTERNAL: frozenset(
        {S.OPEN, S.BLOCKED, S.AT_RISK, S.BREACHED, S.PENDING_EVIDENCE, S.CANCELLED}
    ),
    S.BLOCKED: frozenset({S.OPEN, S.AT_RISK, S.BREACHED, S.PENDING_EVIDENCE, S.CANCELLED}),
    S.AT_RISK: frozenset(
        {S.OPEN, S.WAITING_EXTERNAL, S.BLOCKED, S.BREACHED, S.PENDING_EVIDENCE, S.CANCELLED}
    ),
    S.BREACHED: frozenset(
        {S.OPEN, S.WAITING_EXTERNAL, S.BLOCKED, S.PENDING_EVIDENCE, S.CANCELLED}
    ),
    # The only edge in the whole machine that reaches CLOSED. The Evidence Gate
    # guards this edge in services/engine/evidence.py.
    S.PENDING_EVIDENCE: frozenset({S.CLOSED, S.OPEN, S.CANCELLED}),
    S.CLOSED: frozenset(),
    S.REJECTED: frozenset(),
    S.CANCELLED: frozenset(),
}


class IllegalTransition(Exception):
    """Raised when a caller attempts a transition the machine does not permit."""


def assert_closed_is_gated() -> None:
    """Fail loudly if anything ever makes CLOSED reachable without evidence."""
    predecessors = {
        source for source, targets in LEGAL_TRANSITIONS.items() if S.CLOSED in targets
    }
    if predecessors != {S.PENDING_EVIDENCE}:
        raise AssertionError(
            "CLOSED must be reachable only from PENDING_EVIDENCE, "
            f"found predecessors {sorted(p.value for p in predecessors)}"
        )


def assert_transition_legal(current: ObligationStatus, target: ObligationStatus) -> None:
    if current in TERMINAL_STATUSES:
        raise IllegalTransition(
            f"{current.value} is terminal and cannot transition to {target.value}"
        )
    if target not in LEGAL_TRANSITIONS[current]:
        allowed = sorted(s.value for s in LEGAL_TRANSITIONS[current])
        raise IllegalTransition(
            f"{current.value} may not transition to {target.value}. Allowed: {allowed}"
        )


assert_closed_is_gated()


class Checkpoint(BaseModel):
    kind: CheckpointKind
    at: datetime
    fired: bool = False
    fired_at: datetime | None = None


class LedgerEntry(BaseModel):
    """One append-only record of something that happened to an obligation."""

    actor: str
    action: str
    reason: str
    evidence_ref: str | None = None
    policy_decision_id: str | None = None
    # Who was contacted, when this entry records a notification. The escalation
    # rate limit counts these, so it has to be a field rather than prose in the
    # reason.
    recipient: str | None = None
    trace_id: str | None = None
    at: datetime = Field(default_factory=utcnow)


class Obligation(BaseModel):
    id: str
    type: str
    subject_token: str
    owner_role: str
    owner_id: str
    created_from_event: str
    deadline: datetime
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    required_evidence: str
    blocked_by: list[str] = Field(default_factory=list)
    risk_tier: RiskTier = RiskTier.T0
    status: ObligationStatus = ObligationStatus.PROPOSED
    version: int = 0
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)

    @property
    def next_checkpoint(self) -> Checkpoint | None:
        pending = sorted((c for c in self.checkpoints if not c.fired), key=lambda c: c.at)
        return pending[0] if pending else None


class ObligationCreate(BaseModel):
    """Inbound payload for POST /obligations."""

    type: str
    subject_token: str
    owner_role: str
    owner_id: str
    created_from_event: str
    deadline: datetime
    checkpoints: list[Checkpoint] = Field(default_factory=list)
    required_evidence: str
    blocked_by: list[str] = Field(default_factory=list)
    risk_tier: RiskTier = RiskTier.T0


class WakePayload(BaseModel):
    """Inbound payload from a Cloud Task firing against POST /wake."""

    obligation_id: str
    checkpoint_kind: CheckpointKind
    scheduled_for: datetime
