"""Tests for the escalation ladder configuration.

The delivery path needs Firestore, so these cover the parts that decide who is
told and how often, which are the parts that would quietly misbehave.
"""

from __future__ import annotations

import pytest
from escalation import config_hash, limit_per_recipient, load_config, rung
from models import CheckpointKind

KNOWN_TYPES = [
    "critical_result_acknowledgement",
    "insurance_preauthorisation",
    "claim_document_chase",
    "discharge_blocker",
    "referral_followup",
]


def test_the_ladder_widens_as_an_obligation_ages() -> None:
    for obligation_type in KNOWN_TYPES:
        nudge = rung(obligation_type, CheckpointKind.NUDGE)
        breach = rung(obligation_type, CheckpointKind.BREACH)
        escalate = rung(obligation_type, CheckpointKind.ESCALATE)
        assert len(nudge) == 1, obligation_type
        assert len(breach) > len(nudge), obligation_type
        assert escalate, obligation_type


def test_a_nudge_only_ever_reaches_the_owner() -> None:
    for obligation_type in KNOWN_TYPES:
        assert rung(obligation_type, CheckpointKind.NUDGE) == ["owner"]


def test_escalation_does_not_go_back_to_the_owner_alone() -> None:
    """Escalating to the same person who already ignored two nudges is noise."""
    for obligation_type in KNOWN_TYPES:
        assert rung(obligation_type, CheckpointKind.ESCALATE) != ["owner"]


def test_an_unknown_obligation_type_still_reaches_someone() -> None:
    assert rung("something_invented", CheckpointKind.BREACH)


def test_the_rate_limit_is_small_enough_to_stay_credible() -> None:
    limit = limit_per_recipient()
    assert 1 <= limit <= 3


def test_the_config_is_attributable() -> None:
    assert len(config_hash()) == 16
    config, _ = load_config()
    assert config.get("version") is not None


@pytest.mark.parametrize("obligation_type", KNOWN_TYPES)
def test_every_type_declares_all_three_rungs(obligation_type: str) -> None:
    config, _ = load_config()
    declared = ((config.get("obligation_types") or {}).get(obligation_type) or {}).get(
        "ladder"
    ) or {}
    assert set(declared) == {"nudge", "breach", "escalate"}, obligation_type
