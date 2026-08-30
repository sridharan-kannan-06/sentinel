"""Tests for the obligation state machine.

The first test is the important one. It is the executable form of the second
invariant of this system, and it fails if anyone ever adds an edge that lets an
obligation reach CLOSED without passing through the Evidence Gate.
"""

import pytest

from models import (
    LEGAL_TRANSITIONS,
    TERMINAL_STATUSES,
    IllegalTransition,
    ObligationStatus,
    assert_closed_is_gated,
    assert_transition_legal,
)

S = ObligationStatus


def test_closed_is_reachable_only_through_pending_evidence() -> None:
    assert_closed_is_gated()
    predecessors = {src for src, dst in LEGAL_TRANSITIONS.items() if S.CLOSED in dst}
    assert predecessors == {S.PENDING_EVIDENCE}


def test_open_cannot_close_directly() -> None:
    with pytest.raises(IllegalTransition):
        assert_transition_legal(S.OPEN, S.CLOSED)


def test_at_risk_cannot_close_directly() -> None:
    with pytest.raises(IllegalTransition):
        assert_transition_legal(S.AT_RISK, S.CLOSED)


def test_breached_cannot_close_directly() -> None:
    with pytest.raises(IllegalTransition):
        assert_transition_legal(S.BREACHED, S.CLOSED)


def test_pending_evidence_may_close() -> None:
    assert_transition_legal(S.PENDING_EVIDENCE, S.CLOSED)


def test_pending_evidence_may_fall_back_to_open() -> None:
    assert_transition_legal(S.PENDING_EVIDENCE, S.OPEN)


@pytest.mark.parametrize("terminal", sorted(TERMINAL_STATUSES, key=lambda s: s.value))
def test_terminal_statuses_have_no_outgoing_edges(terminal: ObligationStatus) -> None:
    assert LEGAL_TRANSITIONS[terminal] == frozenset()
    with pytest.raises(IllegalTransition):
        assert_transition_legal(terminal, S.OPEN)


def test_every_status_appears_in_the_table() -> None:
    assert set(LEGAL_TRANSITIONS) == set(ObligationStatus)


def test_no_edge_points_at_an_unknown_status() -> None:
    for source, targets in LEGAL_TRANSITIONS.items():
        for target in targets:
            assert target in ObligationStatus, f"{source} points at unknown {target}"


def test_proposed_may_be_rejected_but_not_opened_twice() -> None:
    assert_transition_legal(S.PROPOSED, S.REJECTED)
    assert_transition_legal(S.PROPOSED, S.OPEN)
    with pytest.raises(IllegalTransition):
        assert_transition_legal(S.PROPOSED, S.PENDING_EVIDENCE)


def test_cancellation_is_available_from_every_live_status() -> None:
    for status in ObligationStatus:
        if status in TERMINAL_STATUSES:
            continue
        assert_transition_legal(status, S.CANCELLED)
