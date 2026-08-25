"""Tests for the deterministic checks applied to model output.

The interpreter is the only component permitted to exercise judgement, so every
one of these tests is about what happens when that judgement is wrong. They run
against the real validation functions with no network involved.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from interpreter import (
    KNOWN_OBLIGATION_TYPES,
    MAX_SLA_HOURS,
    RISK_TIER_FOR,
    ObligationProposal,
    build_agent,
    build_prompt,
    checkpoints_for,
    validate_proposal,
)
from models import CheckpointKind, RiskTier

NOW = datetime(2026, 8, 24, 12, 0, tzinfo=timezone.utc)

EVENT = {
    "idempotency_key": "evt-1",
    "event_type": "CRITICAL_LAB_RESULT",
    "tokens": ["PT-8119", "DR-0385"],
    "text": "Critical potassium for PT-8119, ordering clinician DR-0385 has not acknowledged.",
}


def proposal(**overrides) -> ObligationProposal:
    base = {
        "obligation_type": "critical_result_acknowledgement",
        "subject_token": "PT-8119",
        "owner_role": "ordering_clinician",
        "owner_id": "DR-0385",
        "sla_hours": 2.0,
        "required_evidence": "Acknowledgement record from DR-0385",
        "suggested_risk_tier": "T1",
        "rationale": "Critical result unacknowledged",
        "confidence": 0.9,
    }
    base.update(overrides)
    return ObligationProposal(**base)


def test_valid_proposal_is_accepted() -> None:
    create, reason = validate_proposal(proposal(), EVENT, NOW)
    assert reason is None
    assert create is not None
    assert create.type == "critical_result_acknowledgement"
    assert create.deadline == NOW + timedelta(hours=2)


def test_unknown_obligation_type_is_rejected() -> None:
    create, reason = validate_proposal(
        proposal(obligation_type="schedule_surgery"), EVENT, NOW
    )
    assert create is None
    assert "unknown obligation type" in reason


def test_subject_token_not_in_the_event_is_rejected() -> None:
    # A token the event never mentioned means the model attached the obligation
    # to the wrong person, which is the most dangerous failure available to it.
    create, reason = validate_proposal(proposal(subject_token="PT-9999"), EVENT, NOW)
    assert create is None
    assert "does not appear in the event" in reason


def test_sla_beyond_the_permitted_range_is_rejected() -> None:
    create, reason = validate_proposal(
        proposal(sla_hours=MAX_SLA_HOURS + 1), EVENT, NOW
    )
    assert create is None
    assert "outside the permitted range" in reason


def test_zero_sla_is_rejected() -> None:
    create, reason = validate_proposal(proposal(sla_hours=0), EVENT, NOW)
    assert create is None


def test_empty_required_evidence_is_rejected() -> None:
    # Without a description of proof the obligation could never satisfy the
    # Evidence Gate, so it would stay open forever.
    create, reason = validate_proposal(proposal(required_evidence="   "), EVENT, NOW)
    assert create is None
    assert "required_evidence is empty" in reason


def test_risk_tier_is_derived_and_not_taken_from_the_model() -> None:
    """The model returns T3 for clinically serious subjects. T3 is a permanent
    denial, so accepting it would silently make the obligation undischargeable."""
    create, reason = validate_proposal(
        proposal(suggested_risk_tier="T3"), EVENT, NOW
    )
    assert reason is None
    assert create is not None
    assert create.risk_tier == RiskTier.T1


def test_no_obligation_type_maps_to_the_always_denied_tier() -> None:
    assert RiskTier.T3 not in RISK_TIER_FOR.values()


def test_external_obligations_are_tiered_for_human_approval() -> None:
    assert RISK_TIER_FOR["insurance_preauthorisation"] == RiskTier.T2
    assert RISK_TIER_FOR["referral_followup"] == RiskTier.T2


def test_checkpoints_are_derived_from_the_deadline_not_from_the_model() -> None:
    deadline = NOW + timedelta(hours=4)
    checkpoints = checkpoints_for(deadline, NOW)
    kinds = [c.kind for c in checkpoints]
    assert kinds == [CheckpointKind.NUDGE, CheckpointKind.BREACH, CheckpointKind.ESCALATE]
    assert checkpoints[0].at == NOW + timedelta(hours=2)
    assert checkpoints[1].at == deadline
    assert checkpoints[2].at == deadline + timedelta(hours=2)


def test_checkpoints_are_strictly_ordered() -> None:
    checkpoints = checkpoints_for(NOW + timedelta(hours=1), NOW)
    times = [c.at for c in checkpoints]
    assert times == sorted(times)


def test_prompt_carries_only_tokenised_text() -> None:
    prompt = build_prompt(EVENT)
    for name in ("Meena", "Raghavan", "Anil", "Kumar"):
        assert name not in prompt
    assert "PT-8119" in prompt
    assert "DR-0385" in prompt


@pytest.mark.parametrize("obligation_type", sorted(RISK_TIER_FOR))
def test_every_known_type_has_a_derived_tier(obligation_type: str) -> None:
    create, reason = validate_proposal(
        proposal(obligation_type=obligation_type), EVENT, NOW
    )
    assert reason is None
    assert create is not None
    assert create.risk_tier in {RiskTier.T0, RiskTier.T1, RiskTier.T2}


def test_the_interpreter_holds_no_tools() -> None:
    """An injection that reaches the model still finds nothing to operate.

    This is the property the boundary is not allowed to be the only defence for.
    Model Armor's detection weakens as an injection is diluted by surrounding
    legitimate text, so the system must survive one getting through.
    """
    assert build_agent().tools == []


def test_closure_is_not_in_the_interpreter_vocabulary() -> None:
    """It can propose obligations. It has no way to express closing one."""
    for obligation_type in KNOWN_OBLIGATION_TYPES:
        assert "close" not in obligation_type
        assert "approve" not in obligation_type
    assert len(KNOWN_OBLIGATION_TYPES) == 5


def test_an_injected_instruction_cannot_become_an_obligation_type() -> None:
    """The vocabulary is closed, so an invented type is rejected rather than run."""
    for invented in ("mark_claim_approved", "close_obligation", "administrator_mode"):
        create, reason = validate_proposal(
            proposal(obligation_type=invented), EVENT, NOW
        )
        assert create is None
        assert "unknown obligation type" in reason
