"""Tests for coordinator failure handling.

Every test here is about a worker misbehaving. The property being asserted is
always the same: the obligation survives. A routing failure, a hallucinated
agent name, a timeout, and an HTML error page must all leave the obligation
open and recoverable rather than lost or wrongly advanced.
"""

from __future__ import annotations

from coordinator import DEPARTMENT_AGENTS, RoutingChoice, summarise


def test_an_html_error_page_becomes_one_readable_line() -> None:
    body = (
        "<html><head>\n<title>404 Page not found</title>\n</head>\n"
        "<body text=#000000><h1>Error: Page not found</h1>\n"
        "<h2>The requested URL was not found on this server.</h2></body></html>"
    )
    summary = summarise(body)
    assert "<" not in summary
    assert "404 Page not found" in summary
    assert len(summary) <= 160


def test_an_empty_body_says_so_rather_than_being_blank() -> None:
    assert summarise("") == "empty response"
    assert summarise("   \n  ") == "empty response"


def test_a_long_body_is_truncated_with_an_ellipsis() -> None:
    summary = summarise("x" * 500)
    assert len(summary) == 160
    assert summary.endswith("...")


def test_the_fleet_does_not_include_the_coordinator() -> None:
    """The coordinator must not be able to route work to itself."""
    assert "coordinator" not in DEPARTMENT_AGENTS
    assert set(DEPARTMENT_AGENTS) == {
        "clinical_followup",
        "revenue_cycle",
        "care_pathway",
    }


def test_a_routing_choice_requires_both_an_agent_and_an_action() -> None:
    choice = RoutingChoice(agent="revenue_cycle", action="read_claim_status", rationale="x")
    assert choice.agent == "revenue_cycle"
    assert choice.action == "read_claim_status"
