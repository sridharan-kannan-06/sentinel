"""Tests for the agent tool layer."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("AGENT_ROLE", "revenue_cycle")

import notify  # noqa: E402
import policy  # noqa: E402
import tools  # noqa: E402
from config import get_settings  # noqa: E402

OBLIGATION = {
    "id": "OBL-test",
    "type": "insurance_preauthorisation",
    "subject_token": "PT-8119",
    "owner_role": "revenue_cycle",
    "owner_id": "STAFF-billing-01",
    "status": "OPEN",
    "required_evidence": "Insurer reference number",
    "blocked_by": [],
}

def _tier_of(action: str) -> str | None:
    declared, _ = policy.load_policy()
    return ((declared.get("actions") or {}).get(action) or {}).get("tier")


def _needs_no_tool(action: str) -> bool:
    """Tier two actions wait on the approval queue and have no tool yet.

    Derived from the policy rather than hand-listed, so adding a T2 action does
    not silently satisfy this exemption by being forgotten.
    """
    return _tier_of(action) == "T2" or action in {"read_ledger", "assemble_claim_packet"}


def test_a_role_identifier_is_not_used_as_an_email_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Gmail rejects STAFF-billing-01 with an unhelpful Invalid To header."""
    get_settings.cache_clear()
    monkeypatch.setenv("NOTIFY_TO", "operator@example.com")
    assert tools.resolve_recipient(OBLIGATION, {}) == "operator@example.com"
    get_settings.cache_clear()


def test_an_explicit_recipient_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NOTIFY_TO", "operator@example.com")
    assert (
        tools.resolve_recipient(OBLIGATION, {"to": "ward@example.com"})
        == "ward@example.com"
    )
    get_settings.cache_clear()


def test_no_deliverable_address_is_an_error_not_a_silent_send(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    get_settings.cache_clear()
    monkeypatch.setenv("NOTIFY_TO", "")
    with pytest.raises(notify.NotifierError):
        tools.resolve_recipient(OBLIGATION, {})
    get_settings.cache_clear()


def test_unimplemented_actions_report_rather_than_pretend() -> None:
    result = tools.run("submit_claim", OBLIGATION, {})
    assert result.ok is False
    assert "No tool implements" in result.summary


@pytest.mark.parametrize("role", sorted(policy.known_agents() - {"coordinator"}))
def test_every_actionable_permission_has_a_tool(role: str) -> None:
    """Catches an allow list that grew past the tools that implement it."""
    for action in policy.allowed_actions(role):
        if _needs_no_tool(action):
            continue
        assert action in tools.REGISTRY, f"{role} may {action} but no tool implements it"


def test_no_tool_exists_for_an_action_policy_never_permits() -> None:
    """A tool with no route through policy is dead code with a live footgun."""
    reachable: set[str] = set()
    for role in policy.known_agents():
        reachable |= policy.allowed_actions(role)
    for action in tools.REGISTRY:
        assert action in reachable, f"tool {action} is not reachable through any allow list"


def test_discharge_blockers_reports_what_is_blocking() -> None:
    blocked = dict(OBLIGATION, blocked_by=["OBL-aaa", "OBL-bbb"])
    result = tools.run("check_discharge_blockers", blocked, {})
    assert result.ok
    assert result.data["blocked_by"] == ["OBL-aaa", "OBL-bbb"]
    assert "2 obligation" in result.summary
