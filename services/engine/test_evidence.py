"""Tests for the Evidence Gate.

Each test is one way an agent might be wrong about a thing being done. The
closing test is the ablation: with the gate off the same unqualified evidence
closes the obligation, and the checks that would have stopped it are still on
the record.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from config import get_settings
from evidence import Evidence, evaluate, rules_for
from models import Obligation, ObligationStatus, RiskTier

NOW = datetime(2026, 8, 25, 12, 0, tzinfo=timezone.utc)
OPENED = NOW - timedelta(hours=6)


def obligation(**overrides) -> Obligation:
    base = {
        "id": "OBL-test",
        "type": "critical_result_acknowledgement",
        "subject_token": "PT-8119",
        "owner_role": "ordering_clinician",
        "owner_id": "DR-0385",
        "created_from_event": "evt-1",
        "deadline": NOW + timedelta(hours=2),
        "required_evidence": "Electronic acknowledgement record from DR-0385 in the LIS",
        "risk_tier": RiskTier.T1,
        "status": ObligationStatus.PENDING_EVIDENCE,
        "version": 3,
        "created_at": OPENED,
        "updated_at": OPENED,
    }
    base.update(overrides)
    return Obligation(**base)


def evidence(**overrides) -> Evidence:
    base = {
        "source": "lis",
        "external_reference": "ACK-99213",
        "observed_at": NOW - timedelta(minutes=10),
        "subject_token": "PT-8119",
        "assertion": "Result acknowledged by DR-0385",
    }
    base.update(overrides)
    return Evidence(**base)


@pytest.fixture(autouse=True)
def gate_on(monkeypatch: pytest.MonkeyPatch):
    get_settings.cache_clear()
    monkeypatch.setenv("EVIDENCE_GATE", "on")
    yield
    get_settings.cache_clear()


def failed(verdict) -> set[str]:
    return {c.name for c in verdict.checks if not c.passed}


def test_qualifying_evidence_closes_the_obligation() -> None:
    verdict = evaluate(obligation(), evidence(), now=NOW)
    assert verdict.accepted
    assert failed(verdict) == set()


def test_a_source_that_is_not_authoritative_is_refused() -> None:
    """An email saying the work was done is not the system that records it."""
    verdict = evaluate(obligation(), evidence(source="doctor_said_so"), now=NOW)
    assert not verdict.accepted
    assert "source_is_authoritative" in failed(verdict)


def test_evidence_without_an_external_reference_is_refused() -> None:
    verdict = evaluate(obligation(), evidence(external_reference=""), now=NOW)
    assert not verdict.accepted
    assert "external_reference_present" in failed(verdict)


def test_evidence_that_predates_the_obligation_is_refused() -> None:
    """Something recorded before the obligation existed describes other work."""
    verdict = evaluate(
        obligation(), evidence(observed_at=OPENED - timedelta(hours=1)), now=NOW
    )
    assert not verdict.accepted
    assert "within_acceptance_window" in failed(verdict)


def test_evidence_timestamped_in_the_future_is_refused() -> None:
    verdict = evaluate(obligation(), evidence(observed_at=NOW + timedelta(hours=3)), now=NOW)
    assert not verdict.accepted
    assert "within_acceptance_window" in failed(verdict)


def test_small_clock_differences_are_tolerated() -> None:
    verdict = evaluate(
        obligation(), evidence(observed_at=NOW + timedelta(minutes=2)), now=NOW
    )
    assert verdict.accepted


def test_evidence_about_a_different_patient_is_refused() -> None:
    """The most dangerous failure available: closing the wrong person's work."""
    verdict = evaluate(obligation(), evidence(subject_token="PT-0000"), now=NOW)
    assert not verdict.accepted
    assert "subject_token_matches" in failed(verdict)


def test_an_assertion_that_does_not_describe_the_requirement_is_refused() -> None:
    verdict = evaluate(
        obligation(), evidence(assertion="Patient moved to ward 4"), now=NOW
    )
    assert not verdict.accepted
    assert "assertion_matches_requirement" in failed(verdict)


def test_an_empty_assertion_is_refused() -> None:
    verdict = evaluate(obligation(), evidence(assertion="   "), now=NOW)
    assert not verdict.accepted


def test_an_obligation_type_with_no_declared_sources_can_never_close() -> None:
    verdict = evaluate(obligation(type="something_invented"), evidence(), now=NOW)
    assert not verdict.accepted
    assert "source_is_authoritative" in failed(verdict)


def test_every_known_obligation_type_declares_sources_and_keywords() -> None:
    for obligation_type in (
        "critical_result_acknowledgement",
        "insurance_preauthorisation",
        "claim_document_chase",
        "discharge_blocker",
        "referral_followup",
    ):
        rules = rules_for(obligation_type)
        assert rules["authoritative_sources"], obligation_type
        assert rules["assertion_keywords"], obligation_type
        assert not rules["unknown_type"]


def test_all_five_checks_always_run() -> None:
    verdict = evaluate(obligation(), evidence(source="nonsense"), now=NOW)
    assert {c.name for c in verdict.checks} == {
        "source_is_authoritative",
        "external_reference_present",
        "within_acceptance_window",
        "subject_token_matches",
        "assertion_matches_requirement",
    }


def test_the_verdict_is_attributable() -> None:
    verdict = evaluate(obligation(), evidence(), now=NOW)
    assert len(verdict.evidence_hash) == 16
    assert verdict.config_hash != "absent"


def test_the_same_evidence_hashes_the_same_way() -> None:
    a = evaluate(obligation(), evidence(), now=NOW)
    b = evaluate(obligation(), evidence(), now=NOW)
    assert a.evidence_hash == b.evidence_hash


def test_ablation_the_gate_off_accepts_what_the_gate_on_refuses(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The demonstration in one test.

    Identical unqualified evidence: refused with the gate on, accepted with it
    off. The checks are recorded either way, so the audit trail shows exactly
    what was skipped rather than going quiet.
    """
    unqualified = evidence(
        source="doctor_said_so",
        external_reference="",
        assertion="looks done to me",
    )

    get_settings.cache_clear()
    monkeypatch.setenv("EVIDENCE_GATE", "on")
    with_gate = evaluate(obligation(), unqualified, now=NOW)

    get_settings.cache_clear()
    monkeypatch.setenv("EVIDENCE_GATE", "off")
    without_gate = evaluate(obligation(), unqualified, now=NOW)
    get_settings.cache_clear()

    assert with_gate.accepted is False
    assert without_gate.accepted is True

    # Same failures observed in both. Only the verdict differs.
    assert failed(with_gate) == failed(without_gate)
    assert len(failed(without_gate)) >= 3
    assert without_gate.gate_enabled is False
