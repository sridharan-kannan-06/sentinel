"""Tests for board ordering and root cause.

The blocker walk reads the ledger, so these substitute a fake store. What is
under test is the traversal, not Firestore.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import audit
import pytest
from models import Obligation, ObligationStatus, RiskTier

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)


def make(
    obligation_id: str,
    status: ObligationStatus = ObligationStatus.OPEN,
    blocked_by: list[str] | None = None,
    opened_hours_ago: float = 1.0,
    deadline_hours_ahead: float = 3.0,
) -> Obligation:
    return Obligation(
        id=obligation_id,
        type="discharge_blocker",
        subject_token="PT-8119",
        owner_role="discharge_coordinator",
        owner_id="STAFF-d1",
        created_from_event="evt",
        deadline=NOW + timedelta(hours=deadline_hours_ahead),
        required_evidence="Pharmacy reconciliation signed off",
        blocked_by=blocked_by or [],
        risk_tier=RiskTier.T1,
        status=status,
        version=1,
        created_at=NOW - timedelta(hours=opened_hours_ago),
        updated_at=NOW,
    )


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch):
    records: dict[str, Obligation] = {}
    monkeypatch.setattr(audit.ledger, "get_obligation", lambda i: records.get(i))
    return records


def test_a_breached_obligation_outranks_one_merely_at_risk() -> None:
    breached = make("OBL-a", ObligationStatus.BREACHED)
    at_risk = make("OBL-b", ObligationStatus.AT_RISK)
    assert audit.risk_score(breached, NOW) > audit.risk_score(at_risk, NOW)


def test_a_nearly_expired_short_sla_outranks_a_barely_started_long_one() -> None:
    """Ordering by deadline alone would get this backwards."""
    nearly_done = make("OBL-a", opened_hours_ago=3.5, deadline_hours_ahead=0.5)
    just_started = make("OBL-b", opened_hours_ago=0.1, deadline_hours_ahead=200)
    assert audit.risk_score(nearly_done, NOW) > audit.risk_score(just_started, NOW)


def test_being_overdue_keeps_increasing_urgency() -> None:
    # Both had a four hour window. One is an hour past it, the other a day.
    an_hour_over = make(
        "OBL-a", ObligationStatus.BREACHED, opened_hours_ago=5, deadline_hours_ahead=-1
    )
    a_day_over = make(
        "OBL-b", ObligationStatus.BREACHED, opened_hours_ago=28, deadline_hours_ahead=-24
    )
    assert audit.risk_score(a_day_over, NOW) > audit.risk_score(an_hour_over, NOW)


def test_the_walk_returns_the_root_not_the_proximate_blocker(store) -> None:
    """Discharge is blocked by pharmacy, which is blocked by an unsigned script."""
    store["OBL-discharge"] = make("OBL-discharge", blocked_by=["OBL-pharmacy"])
    store["OBL-pharmacy"] = make("OBL-pharmacy", blocked_by=["OBL-prescription"])
    store["OBL-prescription"] = make("OBL-prescription")

    chain = audit.blocker_chain("OBL-discharge")
    assert chain == ["OBL-pharmacy", "OBL-prescription"]

    answer = audit.why_stuck(store["OBL-discharge"])
    assert answer["root_blocker"] == "OBL-prescription"
    assert answer["proximate_blocker"] == "OBL-pharmacy"
    assert answer["depth"] == 2
    assert "actually has to happen first" in answer["answer"]


def test_a_discharged_blocker_stops_blocking(store) -> None:
    store["OBL-discharge"] = make("OBL-discharge", blocked_by=["OBL-done"])
    store["OBL-done"] = make("OBL-done", ObligationStatus.CLOSED)
    assert audit.blocker_chain("OBL-discharge") == []
    assert audit.why_stuck(store["OBL-discharge"])["blocked"] is False


def test_a_cycle_does_not_hang_the_board(store) -> None:
    store["OBL-a"] = make("OBL-a", blocked_by=["OBL-b"])
    store["OBL-b"] = make("OBL-b", blocked_by=["OBL-a"])
    chain = audit.blocker_chain("OBL-a")
    assert chain == ["OBL-b"]


def test_a_missing_blocker_is_skipped_rather_than_crashing(store) -> None:
    store["OBL-a"] = make("OBL-a", blocked_by=["OBL-deleted"])
    assert audit.blocker_chain("OBL-a") == []


def test_pending_evidence_explains_itself_as_waiting_on_proof(store) -> None:
    obligation = make("OBL-a", ObligationStatus.PENDING_EVIDENCE)
    store["OBL-a"] = obligation
    answer = audit.why_stuck(obligation)
    assert answer["blocked"] is True
    assert answer["root_blocker"] is None
    assert "waiting on evidence" in answer["answer"]


def test_an_unblocked_obligation_says_so_plainly(store) -> None:
    obligation = make("OBL-a", ObligationStatus.OPEN)
    store["OBL-a"] = obligation
    assert "Nothing is blocking this" in audit.why_stuck(obligation)["answer"]
