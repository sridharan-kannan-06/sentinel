"""Board ordering, root cause, and the complete audit chain.

Three things the Continuity Board needs that the ledger does not answer directly.

Ordering by risk of breach rather than by deadline, because an obligation due in
six hours with nothing done is more urgent than one due in one hour that is
already waiting on a confirmed appointment.

The root blocker rather than the proximate one. "Discharge is blocked by pharmacy
reconciliation" is not useful if pharmacy reconciliation is itself blocked by an
unsigned prescription. The walk returns the thing that actually has to happen.

The whole chain for one obligation in one document, so that a claim about what
the system did can be checked rather than taken on trust.
"""

from __future__ import annotations

from datetime import datetime, timezone

import ledger
from models import Obligation, ObligationStatus

# How much each status contributes to urgency before time is considered. A
# breached obligation outranks everything; a blocked one is not urgent in itself
# because the urgency belongs to whatever is blocking it.
STATUS_WEIGHT = {
    ObligationStatus.BREACHED: 100.0,
    ObligationStatus.AT_RISK: 60.0,
    ObligationStatus.PENDING_EVIDENCE: 40.0,
    ObligationStatus.OPEN: 20.0,
    ObligationStatus.WAITING_EXTERNAL: 15.0,
    ObligationStatus.BLOCKED: 10.0,
    ObligationStatus.PROPOSED: 5.0,
}


def seconds_to_breach(obligation: Obligation, now: datetime) -> float:
    return (obligation.deadline - now).total_seconds()


def risk_score(obligation: Obligation, now: datetime) -> float:
    """Higher is more urgent.

    Combines status with how much of the obligation's own window has been used,
    so a short SLA nearly exhausted outranks a long one barely started.
    """
    weight = STATUS_WEIGHT.get(obligation.status, 0.0)
    window = (obligation.deadline - obligation.created_at).total_seconds()
    if window <= 0:
        return weight + 50.0
    elapsed = (now - obligation.created_at).total_seconds()
    # Past the deadline this exceeds 1 and keeps climbing, which is correct:
    # something a day overdue should outrank something an hour overdue.
    return weight + 50.0 * (elapsed / window)


def blocker_chain(obligation_id: str, seen: set[str] | None = None) -> list[str]:
    """Walk blocked_by to the thing that actually has to happen first.

    Depth first, taking the first unresolved blocker at each level. `seen` guards
    against a cycle, which would otherwise hang the board rather than merely
    reporting a confusing answer.
    """
    seen = seen or set()
    if obligation_id in seen:
        return []
    seen.add(obligation_id)

    obligation = ledger.get_obligation(obligation_id)
    if obligation is None:
        return []

    for candidate_id in obligation.blocked_by:
        # Checked before the candidate is appended, not after. Recursing first
        # and relying on the guard inside the call still adds the revisited
        # obligation to the chain, so a two node cycle reports itself as its own
        # root blocker.
        if candidate_id in seen:
            continue
        candidate = ledger.get_obligation(candidate_id)
        if candidate is None:
            continue
        if candidate.status in {
            ObligationStatus.CLOSED,
            ObligationStatus.CANCELLED,
            ObligationStatus.REJECTED,
        }:
            # A discharged blocker is not blocking anything.
            continue
        return [candidate_id, *blocker_chain(candidate_id, seen)]
    return []


def why_stuck(obligation: Obligation) -> dict:
    """The answer the detail view puts at the top of the page."""
    chain = blocker_chain(obligation.id)
    if not chain:
        if obligation.status is ObligationStatus.PENDING_EVIDENCE:
            return {
                "blocked": True,
                "root_blocker": None,
                "chain": [],
                "answer": (
                    "Nothing is blocking this. It is waiting on evidence: "
                    f"{obligation.required_evidence}"
                ),
            }
        return {
            "blocked": False,
            "root_blocker": None,
            "chain": [],
            "answer": f"Nothing is blocking this. It is {obligation.status.value} and "
            f"owed by {obligation.owner_role}.",
        }

    root_id = chain[-1]
    root = ledger.get_obligation(root_id)
    proximate = ledger.get_obligation(chain[0])

    if len(chain) == 1:
        answer = (
            f"Blocked by {root_id}: {root.type if root else 'unknown'} "
            f"({root.status.value if root else 'unknown'})."
        )
    else:
        answer = (
            f"Blocked by {chain[0]} ({proximate.type if proximate else 'unknown'}), "
            f"which is itself blocked. The thing that actually has to happen first "
            f"is {root_id}: {root.type if root else 'unknown'} "
            f"({root.status.value if root else 'unknown'})."
        )

    return {
        "blocked": True,
        "root_blocker": root_id,
        "proximate_blocker": chain[0],
        "chain": chain,
        "depth": len(chain),
        "answer": answer,
    }


def board_row(obligation: Obligation, now: datetime) -> dict:
    stuck = why_stuck(obligation)
    return {
        "id": obligation.id,
        "type": obligation.type,
        "subject_token": obligation.subject_token,
        "owner_role": obligation.owner_role,
        "owner_id": obligation.owner_id,
        "status": obligation.status.value,
        "risk_tier": obligation.risk_tier.value,
        "deadline": obligation.deadline.isoformat(),
        "opened_at": obligation.created_at.isoformat(),
        "seconds_to_breach": seconds_to_breach(obligation, now),
        "risk_score": round(risk_score(obligation, now), 2),
        "root_blocker": stuck.get("root_blocker"),
        "next_checkpoint": (
            obligation.next_checkpoint.at.isoformat()
            if obligation.next_checkpoint
            else None
        ),
        "next_checkpoint_kind": (
            obligation.next_checkpoint.kind.value if obligation.next_checkpoint else None
        ),
        "version": obligation.version,
    }


def board(limit: int = 100, include_closed: bool = False) -> list[dict]:
    now = datetime.now(timezone.utc)
    rows = []
    for obligation in ledger.list_obligations(limit=limit):
        if not include_closed and obligation.status in {
            ObligationStatus.CLOSED,
            ObligationStatus.CANCELLED,
            ObligationStatus.REJECTED,
        }:
            continue
        rows.append(board_row(obligation, now))
    return sorted(rows, key=lambda r: r["risk_score"], reverse=True)


def audit_chain(obligation_id: str) -> dict | None:
    """Everything that happened to one obligation, in one document.

    Grouped as well as listed. The flat history is the record; the grouping is
    what makes it possible to answer "which policy decisions fired" without
    reading every line.
    """
    obligation = ledger.get_obligation(obligation_id)
    if obligation is None:
        return None

    entries = ledger.get_ledger(obligation_id)
    now = datetime.now(timezone.utc)

    def matching(*prefixes: str) -> list[dict]:
        return [
            entry.model_dump(mode="json")
            for entry in entries
            if entry.action.startswith(prefixes)
        ]

    policy_decisions = [
        {
            "policy_decision_id": entry.policy_decision_id,
            "action": entry.action,
            "actor": entry.actor,
            "reason": entry.reason,
            "at": entry.at.isoformat(),
        }
        for entry in entries
        if entry.policy_decision_id
    ]

    evidence_checks = [
        {
            "outcome": "passed" if entry.action.endswith("passed") else "failed",
            "detail": entry.reason,
            "evidence_ref": entry.evidence_ref,
            "at": entry.at.isoformat(),
        }
        for entry in entries
        if entry.action.startswith("evidence.check.")
    ]

    return {
        "obligation": obligation.model_dump(mode="json"),
        "why_stuck": why_stuck(obligation),
        "risk_score": round(risk_score(obligation, now), 2),
        "seconds_to_breach": seconds_to_breach(obligation, now),
        "history": [entry.model_dump(mode="json") for entry in entries],
        "policy_decisions": policy_decisions,
        "evidence_checks": evidence_checks,
        "notifications": matching("notification."),
        "agent_actions": matching("acted.", "failed.", "route."),
        "sweep_repairs": matching("sweep."),
        "counts": {
            "history": len(entries),
            "policy_decisions": len(policy_decisions),
            "evidence_checks": len(evidence_checks),
            "notifications": len(matching("notification.")),
            "wakes": len(matching("checkpoint.", "wake.")),
        },
    }
