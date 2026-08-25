"""The Coordinator.

It reads an obligation, decides which department owes the next move, and hands
it over. It holds no tools of its own: the policy denies it every write and
every external action, so even a compromised or looping coordinator can only
misroute, never act.

The model chooses. Deterministic code then decides whether that choice is
permitted, which is the same division used for risk tiers and deadlines. A
routing choice outside the vocabulary, an agent that times out, and an agent
that returns nonsense are all handled the same way: record what happened and
leave the obligation open for the reconciliation sweep. An obligation that fails
to route is still an obligation.
"""

from __future__ import annotations

import json
import re

import httpx
from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from pydantic import BaseModel, Field, ValidationError

import approvals
import ledger
import logs
import policy
from config import get_settings
from models import Obligation

# A worker that loops must not be able to hold the coordinator open. Two
# attempts at thirty seconds bounds the whole exchange at about a minute.
DISPATCH_TIMEOUT_SECONDS = 30.0
DISPATCH_ATTEMPTS = 2

DEPARTMENT_AGENTS = ("clinical_followup", "revenue_cycle", "care_pathway")


class RoutingChoice(BaseModel):
    agent: str = Field(description="Which department agent should act next")
    action: str = Field(description="Which single action that agent should take")
    rationale: str = Field(description="One sentence on why this is the next move")


class CoordinationResult(BaseModel):
    routed: bool = False
    agent: str | None = None
    action: str | None = None
    outcome: str = ""
    detail: str = ""
    policy_decision_id: str | None = None
    external_ref: str | None = None
    approval_id: str | None = None


def summarise(body: str, limit: int = 160) -> str:
    """Reduce an error body to one readable line.

    An unreachable Cloud Run service answers with an HTML error page, and
    pasting that into the ledger buries the actual reason under markup. The
    ledger is read by people.
    """
    text = re.sub(r"<[^>]+>", " ", body or "")
    text = " ".join(text.split())
    if not text:
        return "empty response"
    return text if len(text) <= limit else text[: limit - 3] + "..."


def agent_url(role: str) -> str | None:
    settings = get_settings()
    return {
        "clinical_followup": settings.agent_clin_url,
        "revenue_cycle": settings.agent_rev_url,
        "care_pathway": settings.agent_path_url,
    }.get(role)


def build_instruction() -> str:
    """Describe the fleet to the model using the policy as the source of truth.

    Generated rather than written out, so the vocabulary the model is offered
    cannot drift from the vocabulary the policy engine will accept.
    """
    lines = [
        "You are the coordinator for a hospital continuity system. You are given "
        "one obligation that is not yet discharged. Decide which department "
        "agent should make the next move, and which single action it should take.",
        "",
        "You do not act. You route. Choose exactly one agent and one action from "
        "the lists below, copying the names exactly.",
        "",
    ]
    for role in DEPARTMENT_AGENTS:
        declared, _ = policy.load_policy()
        description = (declared.get("agents") or {}).get(role, {}).get("description", "")
        actions = sorted(policy.allowed_actions(role))
        lines.append(f"{role}: {description}")
        lines.append(f"  actions: {', '.join(actions)}")
        lines.append("")
    lines += [
        "Prefer the action that would most directly produce the evidence the "
        "obligation needs to close. If the obligation is already past its "
        "deadline, prefer escalation over a further request.",
        "",
        "already_attempted lists what has been done on this obligation before. "
        "Do not repeat an action that is already in it. Reading a status is "
        "useful once; after that, choose the action that actually discharges the "
        "obligation, even if that action needs human approval. An action parked "
        "for approval is progress. Reading the same status a third time is not.",
    ]
    return "\n".join(lines)


def build_agent() -> LlmAgent:
    settings = get_settings()
    # No tools. The coordinator cannot act even if it decides it wants to.
    return LlmAgent(
        name="sentinel_coordinator",
        model=settings.gemini_model,
        description="Routes an obligation to the department that owes the next move",
        instruction=build_instruction(),
        output_schema=RoutingChoice,
        output_key="routing",
        tools=[],
    )


def recent_actions(obligation_id: str, limit: int = 8) -> list[str]:
    """What has already been tried on this obligation.

    Without this the coordinator re-reads the same status on every wake and the
    obligation never advances, because reading is always the safest-looking
    choice. Showing the history is what turns a loop into a sequence.
    """
    interesting = ("acted.", "failed.", "approval.", "policy.denied", "route.")
    actions = [
        entry.action
        for entry in ledger.get_ledger(obligation_id)
        if entry.action.startswith(interesting)
    ]
    return actions[-limit:]


def describe(obligation: Obligation) -> str:
    return json.dumps(
        {
            "id": obligation.id,
            "type": obligation.type,
            "subject_token": obligation.subject_token,
            "owner_role": obligation.owner_role,
            "owner_id": obligation.owner_id,
            "status": obligation.status.value,
            "risk_tier": obligation.risk_tier.value,
            "deadline": obligation.deadline.isoformat(),
            "opened_at": obligation.created_at.isoformat(),
            "required_evidence": obligation.required_evidence,
            "blocked_by": obligation.blocked_by,
            "already_attempted": recent_actions(obligation.id),
        },
        indent=2,
    )


async def choose_route(obligation: Obligation, trace_id: str | None) -> RoutingChoice | None:
    try:
        runner = InMemoryRunner(agent=build_agent(), app_name="sentinel")
        events = await runner.run_debug(
            describe(obligation),
            session_id=f"route-{obligation.id}",
            user_id="sentinel-engine",
            quiet=True,
        )
        raw = ""
        for event in events:
            if event.content and event.content.parts:
                for part in event.content.parts:
                    if getattr(part, "text", None):
                        raw = part.text
        return RoutingChoice.model_validate_json(raw)
    except (ValidationError, ValueError) as exc:
        logs.warning(
            "coordinator returned a routing choice that did not parse",
            obligation_id=obligation.id,
            error=str(exc),
            trace_id=trace_id,
        )
        return None
    except Exception as exc:
        logs.error(
            "coordinator model call failed",
            obligation_id=obligation.id,
            error=str(exc),
            trace_id=trace_id,
        )
        return None


async def dispatch(
    role: str,
    obligation: Obligation,
    action: str,
    decision_id: str,
    trace_id: str | None,
) -> tuple[bool, str, str | None]:
    """Call one agent. Returns (ok, detail, external reference)."""
    url = agent_url(role)
    if not url:
        return False, f"No URL configured for {role}", None

    body = {
        "obligation": obligation.model_dump(mode="json"),
        "action": action,
        "payload": {},
        "policy_decision_id": decision_id,
        "trace_id": trace_id,
    }

    last = "no attempt made"
    for attempt in range(1, DISPATCH_ATTEMPTS + 1):
        try:
            async with httpx.AsyncClient(timeout=DISPATCH_TIMEOUT_SECONDS) as client:
                response = await client.post(f"{url}/act", json=body)
            if response.status_code == 200:
                data = response.json()
                return (
                    data.get("status") == "executed",
                    data.get("summary") or data.get("reason") or data.get("status", ""),
                    data.get("external_ref"),
                )
            last = f"agent returned HTTP {response.status_code}: {summarise(response.text)}"
        except httpx.TimeoutException:
            last = f"agent did not respond within {DISPATCH_TIMEOUT_SECONDS:.0f}s"
        except httpx.HTTPError as exc:
            last = f"transport error: {exc}"
        logs.warning(
            "dispatch attempt failed",
            obligation_id=obligation.id,
            agent=role,
            action=action,
            attempt=attempt,
            detail=last,
            trace_id=trace_id,
        )
    return False, last, None


async def coordinate(obligation: Obligation, trace_id: str | None = None) -> CoordinationResult:
    choice = await choose_route(obligation, trace_id)

    if choice is None:
        ledger.record_entry(
            obligation.id,
            actor="agent:coordinator",
            action="route.failed",
            reason="The coordinator could not produce a usable routing choice. "
            "The obligation stays open and the sweep will retry it.",
            trace_id=trace_id,
        )
        return CoordinationResult(outcome="route_failed", detail="no usable routing choice")

    if choice.agent not in DEPARTMENT_AGENTS:
        # The model named something outside the fleet. Treated as a routing
        # failure rather than an error, because the obligation is still fine.
        ledger.record_entry(
            obligation.id,
            actor="agent:coordinator",
            action="route.rejected",
            reason=f"Coordinator chose {choice.agent!r}, which is not a department "
            f"agent. The obligation stays open.",
            trace_id=trace_id,
        )
        return CoordinationResult(
            outcome="route_rejected", detail=f"unknown agent {choice.agent!r}"
        )

    decision = policy.decide(choice.agent, choice.action, obligation_id=obligation.id)

    if not decision.permitted:
        ledger.record_entry(
            obligation.id,
            actor="agent:coordinator",
            action="policy.denied",
            reason=f"Routed {choice.action!r} to {choice.agent!r} and policy refused: "
            f"{decision.reason}",
            policy_decision_id=decision.decision_id,
            trace_id=trace_id,
        )
        logs.warning(
            "policy denied a routed action",
            obligation_id=obligation.id,
            agent=choice.agent,
            action=choice.action,
            reason=decision.reason,
            policy_decision_id=decision.decision_id,
            trace_id=trace_id,
        )
        return CoordinationResult(
            agent=choice.agent,
            action=choice.action,
            outcome="denied",
            detail=decision.reason,
            policy_decision_id=decision.decision_id,
        )

    if decision.needs_approval:
        # Park the exact request that would have been dispatched, so the person
        # approving reads the real payload rather than a description of it.
        outbound = {
            "obligation": obligation.model_dump(mode="json"),
            "action": choice.action,
            "payload": {},
            "policy_decision_id": decision.decision_id,
        }
        request = approvals.create(
            obligation_id=obligation.id,
            agent=choice.agent,
            action=choice.action,
            risk_tier=decision.tier or "unknown",
            payload=outbound,
            rendered_payload=json.dumps(outbound, indent=2),
            policy_decision_id=decision.decision_id,
            policy_hash=decision.policy_hash,
            trace_id=trace_id,
        )
        return CoordinationResult(
            agent=choice.agent,
            action=choice.action,
            outcome="requires_approval",
            detail=f"{decision.reason} Parked as {request.id}.",
            policy_decision_id=decision.decision_id,
            approval_id=request.id,
        )

    ok, detail, external_ref = await dispatch(
        choice.agent, obligation, choice.action, decision.decision_id, trace_id
    )

    ledger.record_entry(
        obligation.id,
        actor=f"agent:{choice.agent}",
        action=f"acted.{choice.action}" if ok else f"failed.{choice.action}",
        reason=f"{choice.rationale} Result: {detail}",
        evidence_ref=external_ref,
        policy_decision_id=decision.decision_id,
        trace_id=trace_id,
    )
    return CoordinationResult(
        routed=ok,
        agent=choice.agent,
        action=choice.action,
        outcome="executed" if ok else "agent_failed",
        detail=detail,
        policy_decision_id=decision.decision_id,
        external_ref=external_ref,
    )
