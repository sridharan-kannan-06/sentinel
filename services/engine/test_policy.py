"""Tests for the Policy Engine.

These are the tests that describe the authority model, so they are written as
statements about what the system may and may not do rather than as checks on
particular return values.
"""

from __future__ import annotations

import pytest
from policy import Outcome, allowed_actions, decide, known_agents, policy_hash

CLINICAL = "clinical_followup"
REVENUE = "revenue_cycle"
PATHWAY = "care_pathway"
COORDINATOR = "coordinator"

DEPARTMENT_AGENTS = [CLINICAL, REVENUE, PATHWAY]
ALL_AGENTS = [COORDINATOR, *DEPARTMENT_AGENTS]

CLINICAL_WRITE_ACTIONS = [
    "write_clinical_record",
    "amend_diagnosis",
    "record_clinical_opinion",
]


@pytest.mark.parametrize("agent", ALL_AGENTS)
@pytest.mark.parametrize("action", CLINICAL_WRITE_ACTIONS)
def test_no_agent_may_ever_touch_a_clinical_record(agent: str, action: str) -> None:
    """Sentinel has no clinical authority. There is no approval path to this."""
    decision = decide(agent, action)
    assert decision.outcome is Outcome.DENY
    assert decision.tier == "T3"


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_no_agent_may_override_the_evidence_gate(agent: str) -> None:
    decision = decide(agent, "override_evidence_gate")
    assert decision.outcome is Outcome.DENY


def test_revenue_agent_cannot_read_clinical_status() -> None:
    """A billing agent must be structurally incapable of reading a clinical record.
    This is a permissions question, not a prompt question."""
    decision = decide(REVENUE, "read_lab_status")
    assert decision.outcome is Outcome.DENY
    assert "different trust domain" in decision.reason


def test_clinical_agent_cannot_read_billing() -> None:
    decision = decide(CLINICAL, "read_claim_status")
    assert decision.outcome is Outcome.DENY


def test_pathway_agent_cannot_submit_claims() -> None:
    decision = decide(PATHWAY, "submit_claim")
    assert decision.outcome is Outcome.DENY


def test_coordinator_holds_no_write_or_external_actions() -> None:
    """The coordinator routes. It must not be able to act on anything it routes."""
    forbidden = [
        "notify_clinician",
        "request_internal_document",
        "submit_claim",
        "send_external_email",
        "message_patient",
        "schedule_followup",
    ]
    for action in forbidden:
        assert decide(COORDINATOR, action).outcome is Outcome.DENY


def test_external_actions_require_human_approval() -> None:
    assert decide(REVENUE, "submit_claim").outcome is Outcome.ALLOW_WITH_APPROVAL
    assert decide(REVENUE, "send_external_email").outcome is Outcome.ALLOW_WITH_APPROVAL
    assert decide(PATHWAY, "message_patient").outcome is Outcome.ALLOW_WITH_APPROVAL


def test_internal_actions_are_permitted_outright() -> None:
    assert decide(CLINICAL, "notify_clinician").outcome is Outcome.ALLOW
    assert decide(CLINICAL, "request_acknowledgement").outcome is Outcome.ALLOW
    assert decide(REVENUE, "request_internal_document").outcome is Outcome.ALLOW
    assert decide(PATHWAY, "check_discharge_blockers").outcome is Outcome.ALLOW


def test_an_undeclared_agent_has_no_permissions() -> None:
    decision = decide("rogue_agent", "read_ledger")
    assert decision.outcome is Outcome.DENY
    assert "not declared" in decision.reason


def test_an_undeclared_action_is_denied() -> None:
    """Deny by default. A new tool cannot be used until policy names it."""
    decision = decide(CLINICAL, "exfiltrate_everything")
    assert decision.outcome is Outcome.DENY
    assert "not declared" in decision.reason


def test_every_decision_is_attributable() -> None:
    decision = decide(CLINICAL, "notify_clinician", obligation_id="OBL-test")
    assert decision.decision_id.startswith("PD-")
    assert decision.policy_hash != "absent"
    assert decision.policy_version is not None
    assert decision.obligation_id == "OBL-test"


def test_policy_hash_is_stable_across_calls() -> None:
    assert policy_hash() == policy_hash()
    assert len(policy_hash()) == 16


def test_the_declared_fleet_is_exactly_the_expected_four() -> None:
    assert known_agents() == set(ALL_AGENTS)


def test_department_allowlists_are_disjoint_where_it_matters() -> None:
    """Shared read-only actions are fine. Domain actions must not overlap."""
    shared = {"read_ledger", "post_board_update"}
    clinical = allowed_actions(CLINICAL) - shared
    revenue = allowed_actions(REVENUE) - shared
    pathway = allowed_actions(PATHWAY) - shared
    assert clinical & revenue == set()
    assert clinical & pathway == set()
    assert revenue & pathway == set()


@pytest.mark.parametrize("agent", ALL_AGENTS)
def test_no_agent_is_allowed_an_undeclared_action(agent: str) -> None:
    """Catches an allow list drifting ahead of the action table."""
    for action in allowed_actions(agent):
        decision = decide(agent, action)
        assert "not declared in the policy" not in decision.reason


def test_the_t3_denial_explains_itself_as_absolute() -> None:
    """A denial that says only "no" is a denial nobody can act on."""
    decision = decide(REVENUE, "write_clinical_record")
    assert "every agent without exception" in decision.reason
    assert "no approval path" in decision.reason
