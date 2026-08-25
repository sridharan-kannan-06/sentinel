"""The Obligation Ledger.

Two rules hold this together. Every status write goes through `transition`, which
is the only function in the service permitted to touch the status field, and every
write is guarded by the version field inside a Firestore transaction so two agents
cannot race the same obligation. History is append-only: ledger entries are created
and never updated.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import Enum
from typing import Any

from google.cloud import firestore
from pydantic import BaseModel

from config import get_settings
from models import (
    TERMINAL_STATUSES,
    Checkpoint,
    IllegalTransition,
    LedgerEntry,
    Obligation,
    ObligationCreate,
    ObligationStatus,
    assert_transition_legal,
    utcnow,
)

OBLIGATIONS = "obligations"
LEDGER_ENTRIES = "ledger_entries"

_client: firestore.Client | None = None


def client() -> firestore.Client:
    global _client
    if _client is None:
        _client = firestore.Client(project=get_settings().project_id)
    return _client


def _to_firestore(value: Any) -> Any:
    """Convert a dumped model into types the Firestore client accepts.

    Datetimes must survive as datetimes so the reconciliation sweep can run a
    range query on next_checkpoint. Enums have to be flattened to their value or
    they round-trip as opaque objects.
    """
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, BaseModel):
        return _to_firestore(value.model_dump(mode="python"))
    if isinstance(value, dict):
        return {key: _to_firestore(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_to_firestore(item) for item in value]
    return value


def new_obligation_id() -> str:
    return f"OBL-{uuid.uuid4().hex[:10]}"


def _earliest_pending(checkpoints: list[Checkpoint]) -> datetime | None:
    pending = sorted((c for c in checkpoints if not c.fired), key=lambda c: c.at)
    return pending[0].at if pending else None


def create_obligation(
    payload: ObligationCreate,
    actor: str,
    reason: str,
    trace_id: str | None = None,
    policy_decision_id: str | None = None,
) -> Obligation:
    """Create an obligation and its first ledger entry in one transaction.

    The obligation is born PROPOSED and moved to OPEN in the same transaction, so
    the ledger records the admission decision rather than an obligation that
    silently appeared already open.
    """
    obligation = Obligation(
        id=new_obligation_id(),
        **payload.model_dump(),
        status=ObligationStatus.PROPOSED,
        version=0,
    )
    assert_transition_legal(ObligationStatus.PROPOSED, ObligationStatus.OPEN)
    obligation.status = ObligationStatus.OPEN
    obligation.version = 1
    obligation.updated_at = utcnow()

    ref = client().collection(OBLIGATIONS).document(obligation.id)
    entry = LedgerEntry(
        actor=actor,
        action="obligation.created",
        reason=reason,
        policy_decision_id=policy_decision_id,
        trace_id=trace_id,
    )

    document = _to_firestore(obligation.model_dump(mode="python"))
    document["next_checkpoint"] = _earliest_pending(obligation.checkpoints)

    @firestore.transactional
    def _create(transaction: firestore.Transaction) -> None:
        snapshot = ref.get(transaction=transaction)
        if snapshot.exists:
            raise IllegalTransition(f"obligation {obligation.id} already exists")
        transaction.set(ref, document)
        transaction.set(
            ref.collection(LEDGER_ENTRIES).document(),
            _to_firestore(entry.model_dump(mode="python")),
        )

    _create(client().transaction())
    return obligation


def transition(
    obligation_id: str,
    target: ObligationStatus,
    actor: str,
    action: str,
    reason: str,
    expected_version: int | None = None,
    evidence_ref: str | None = None,
    policy_decision_id: str | None = None,
    trace_id: str | None = None,
    checkpoint_fired: str | None = None,
) -> Obligation:
    """Move an obligation to a new status. The only writer of the status field."""
    ref = client().collection(OBLIGATIONS).document(obligation_id)

    @firestore.transactional
    def _transition(transaction: firestore.Transaction) -> Obligation:
        snapshot = ref.get(transaction=transaction)
        if not snapshot.exists:
            raise KeyError(f"obligation {obligation_id} not found")

        current = Obligation.model_validate(snapshot.to_dict())

        if expected_version is not None and current.version != expected_version:
            raise IllegalTransition(
                f"version conflict on {obligation_id}: "
                f"expected {expected_version}, found {current.version}"
            )

        if target != current.status:
            assert_transition_legal(current.status, target)

        checkpoints = current.checkpoints
        if checkpoint_fired is not None:
            for checkpoint in checkpoints:
                if checkpoint.kind.value == checkpoint_fired and not checkpoint.fired:
                    checkpoint.fired = True
                    checkpoint.fired_at = utcnow()
                    break

        current.status = target
        current.version = current.version + 1
        current.updated_at = utcnow()
        current.checkpoints = checkpoints

        update = _to_firestore(
            {
                "status": current.status,
                "version": current.version,
                "updated_at": current.updated_at,
                "checkpoints": checkpoints,
            }
        )
        update["next_checkpoint"] = _earliest_pending(checkpoints)

        entry = LedgerEntry(
            actor=actor,
            action=action,
            reason=reason,
            evidence_ref=evidence_ref,
            policy_decision_id=policy_decision_id,
            trace_id=trace_id,
        )

        transaction.update(ref, update)
        transaction.set(
            ref.collection(LEDGER_ENTRIES).document(),
            _to_firestore(entry.model_dump(mode="python")),
        )
        return current

    return _transition(client().transaction())


def record_entry(
    obligation_id: str,
    actor: str,
    action: str,
    reason: str,
    evidence_ref: str | None = None,
    policy_decision_id: str | None = None,
    trace_id: str | None = None,
    recipient: str | None = None,
) -> LedgerEntry:
    """Append history without changing status. Used for wakes and observations."""
    entry = LedgerEntry(
        actor=actor,
        action=action,
        reason=reason,
        evidence_ref=evidence_ref,
        policy_decision_id=policy_decision_id,
        recipient=recipient,
        trace_id=trace_id,
    )
    ref = client().collection(OBLIGATIONS).document(obligation_id)
    ref.collection(LEDGER_ENTRIES).document().set(
        _to_firestore(entry.model_dump(mode="python"))
    )
    return entry


def get_obligation(obligation_id: str) -> Obligation | None:
    snapshot = client().collection(OBLIGATIONS).document(obligation_id).get()
    if not snapshot.exists:
        return None
    return Obligation.model_validate(snapshot.to_dict())


def list_obligations(limit: int = 100) -> list[Obligation]:
    query = (
        client()
        .collection(OBLIGATIONS)
        .order_by("created_at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    return [Obligation.model_validate(doc.to_dict()) for doc in query.stream()]


def overdue_checkpoints(now: datetime, limit: int = 50) -> list[Obligation]:
    """Obligations whose next checkpoint is in the past and has not fired.

    Firestore will not combine an inequality on next_checkpoint with a not-in
    filter on status in one query, so the status filter is applied here. The
    range query is the selective one, so the difference does not matter at this
    size.
    """
    query = (
        client()
        .collection(OBLIGATIONS)
        .where(filter=firestore.FieldFilter("next_checkpoint", "<=", now))
        .order_by("next_checkpoint")
        .limit(limit)
    )
    overdue: list[Obligation] = []
    for document in query.stream():
        record = document.to_dict() or {}
        # A null next_checkpoint sorts before every timestamp and would otherwise
        # be swept every single time.
        if record.get("next_checkpoint") is None:
            continue
        obligation = Obligation.model_validate(record)
        if obligation.status in TERMINAL_STATUSES:
            continue
        overdue.append(obligation)
    return overdue


def count_entries(obligation_id: str, action: str) -> int:
    return sum(1 for entry in get_ledger(obligation_id) if entry.action == action)


def get_ledger(obligation_id: str) -> list[LedgerEntry]:
    query = (
        client()
        .collection(OBLIGATIONS)
        .document(obligation_id)
        .collection(LEDGER_ENTRIES)
        .order_by("at")
    )
    return [LedgerEntry.model_validate(doc.to_dict()) for doc in query.stream()]
