"""The Interpreter: a de-identified event becomes obligation proposals.

This is the one place a model is allowed to exercise judgement, and it is
deliberately fenced in three ways.

It proposes and never commits. The agent is constructed with no tools and no
Firestore access, so there is no code path from a model response to a write. The
caller decides what to do with a proposal.

It does not do date arithmetic. The model proposes an SLA in hours and
deterministic code turns that into a deadline and checkpoints. Models are poor
at calendars and a hallucinated timestamp would silently corrupt the timers.

Its output is checked against reality, not trusted. A proposal naming a subject
token that did not appear in the event, an unknown obligation type, or an SLA
outside the permitted range is rejected. A malformed response is a rejected
proposal, never an exception that loses the event.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from google.adk.agents import LlmAgent
from google.adk.runners import InMemoryRunner
from pydantic import BaseModel, Field, ValidationError

import logs
import tracing
from config import get_settings
from models import Checkpoint, CheckpointKind, ObligationCreate, RiskTier

# The model may not invent obligation types. Anything outside this set is
# rejected, which keeps the ledger's vocabulary closed and the policy engine's
# rules exhaustive.
KNOWN_OBLIGATION_TYPES = {
    "critical_result_acknowledgement",
    "insurance_preauthorisation",
    "claim_document_chase",
    "discharge_blocker",
    "referral_followup",
}

# The tier that governs is derived here, from the obligation type, and never
# taken from the model. Asked to tier a critical potassium result the model
# returns T3, reading the clinical seriousness of the subject rather than the
# authority the agent needs; T3 is a permanent denial, so that one word would
# silently kill the whole workflow. The model's suggestion is kept alongside for
# the trust panel, because a disagreement is worth showing.
#
# No obligation type maps to T3. T3 is the tier of actions Sentinel may never
# take at all, and an obligation whose discharge required one could never be
# discharged.
RISK_TIER_FOR: dict[str, RiskTier] = {
    "critical_result_acknowledgement": RiskTier.T1,
    "insurance_preauthorisation": RiskTier.T2,
    "claim_document_chase": RiskTier.T1,
    "discharge_blocker": RiskTier.T1,
    "referral_followup": RiskTier.T2,
}

MIN_SLA_HOURS = 0.25
MAX_SLA_HOURS = 720.0

INSTRUCTION = """\
You are the interpreter for a hospital continuity system. You read one
de-identified operational event and identify the obligations it creates.

An obligation is a commitment the hospital's workflow has implicitly made that
is not yet discharged. It always has someone who owes it and something that
would prove it was done.

Rules you must follow:

Every person is already a token such as PT-8119 or DR-0385. Use those tokens
exactly as they appear. Never invent a token and never write a human name.

Choose obligation_type from exactly this list:
critical_result_acknowledgement, insurance_preauthorisation,
claim_document_chase, discharge_blocker, referral_followup.

Set subject_token to a token that literally appears in the event text.

Express urgency as sla_hours, a number of hours from now. Do not produce dates
or times; something else computes those.

suggested_risk_tier is advisory only and does not decide anything. Judge the
action this system would have to take, not how clinically serious the subject
is. This system only ever notifies people, requests documents, and chases
external parties. It never writes to a clinical record and never forms a
clinical opinion, so a life-threatening result that merely needs a clinician
notified is a low tier, not a high one.
T0 internal notification, T1 internal task or document request,
T2 anything leaving the organisation such as external email or claim
submission, T3 anything touching a clinical record or expressing clinical
judgement.

required_evidence describes the authoritative fact that would prove completion,
for example an acknowledgement record or an insurer reference number. It is a
description of proof, not an assertion that the work happened.

If the event creates no obligation, return an empty list. Inventing work is
worse than missing it.
"""


class ObligationProposal(BaseModel):
    obligation_type: str = Field(description="One of the permitted obligation types")
    subject_token: str = Field(description="A token that appears in the event text")
    owner_role: str = Field(description="The role that owes this work")
    owner_id: str = Field(description="Token or role identifier of the owner")
    sla_hours: float = Field(description="Hours from now by which this is due")
    required_evidence: str = Field(description="The authoritative fact that would prove completion")
    suggested_risk_tier: str = Field(
        description="Advisory only, one of T0, T1, T2, T3. The system derives the tier it enforces."
    )
    rationale: str = Field(description="One sentence on why this obligation exists")
    confidence: float = Field(description="0 to 1")


class InterpreterOutput(BaseModel):
    proposals: list[ObligationProposal] = Field(default_factory=list)


class RejectedProposal(BaseModel):
    reason: str
    raw: dict | str


class InterpretResult(BaseModel):
    accepted: list[ObligationCreate] = Field(default_factory=list)
    rejected: list[RejectedProposal] = Field(default_factory=list)
    prompt: str = ""
    raw_response: str = ""


def build_agent() -> LlmAgent:
    settings = get_settings()
    # No tools. The interpreter is structurally incapable of acting.
    return LlmAgent(
        name="sentinel_interpreter",
        model=settings.gemini_model,
        description="Turns one de-identified hospital event into obligation proposals",
        instruction=INSTRUCTION,
        output_schema=InterpreterOutput,
        output_key="interpretation",
        tools=[],
    )


def build_prompt(event: dict) -> str:
    """The exact text handed to the model.

    Returned so the caller can log it and so a test can assert that no real name
    ever appears in it.
    """
    return json.dumps(
        {
            "event_type": event.get("event_type"),
            "source": event.get("source"),
            "occurred_at": event.get("occurred_at"),
            "tokens_present": event.get("tokens", []),
            "text": event.get("text", ""),
        },
        indent=2,
    )


def checkpoints_for(deadline: datetime, opened_at: datetime) -> list[Checkpoint]:
    """Derive SLA checkpoints deterministically from the deadline.

    The nudge lands halfway to the deadline, the breach on it, and the
    escalation half an SLA past it. No model chooses these.
    """
    window = deadline - opened_at
    return [
        Checkpoint(kind=CheckpointKind.NUDGE, at=opened_at + window / 2),
        Checkpoint(kind=CheckpointKind.BREACH, at=deadline),
        Checkpoint(kind=CheckpointKind.ESCALATE, at=deadline + window / 2),
    ]


def validate_proposal(
    proposal: ObligationProposal,
    event: dict,
    now: datetime,
) -> tuple[ObligationCreate | None, str | None]:
    """Deterministic checks applied to every proposal before it can become real."""
    if proposal.obligation_type not in KNOWN_OBLIGATION_TYPES:
        return None, f"unknown obligation type {proposal.obligation_type!r}"

    tokens = set(event.get("tokens", []))
    text = event.get("text", "")
    if proposal.subject_token not in tokens and proposal.subject_token not in text:
        return None, (
            f"subject token {proposal.subject_token!r} does not appear in the event"
        )

    if not (MIN_SLA_HOURS <= proposal.sla_hours <= MAX_SLA_HOURS):
        return None, (
            f"sla_hours {proposal.sla_hours} outside the permitted range "
            f"{MIN_SLA_HOURS} to {MAX_SLA_HOURS}"
        )

    # Derived, not taken from the model. See RISK_TIER_FOR.
    risk_tier = RISK_TIER_FOR[proposal.obligation_type]
    suggested = proposal.suggested_risk_tier.strip().upper()
    if suggested != risk_tier.value:
        logs.info(
            "model risk tier overridden by the derived tier",
            obligation_type=proposal.obligation_type,
            suggested=suggested,
            enforced=risk_tier.value,
        )

    if not proposal.required_evidence.strip():
        return None, "required_evidence is empty, so nothing could ever close this"

    deadline = now + timedelta(hours=proposal.sla_hours)
    return (
        ObligationCreate(
            type=proposal.obligation_type,
            subject_token=proposal.subject_token,
            owner_role=proposal.owner_role,
            owner_id=proposal.owner_id,
            created_from_event=str(event.get("idempotency_key", "unknown")),
            deadline=deadline,
            checkpoints=checkpoints_for(deadline, now),
            required_evidence=proposal.required_evidence.strip(),
            risk_tier=risk_tier,
        ),
        None,
    )


async def interpret(event: dict, trace_id: str | None = None) -> InterpretResult:
    """Run the interpreter over one de-identified event."""
    now = datetime.now(timezone.utc)
    prompt = build_prompt(event)
    result = InterpretResult(prompt=prompt)

    try:
        tracing.annotate(
            idempotency_key=event.get("idempotency_key"),
            event_type=event.get("event_type"),
            tokens=",".join(event.get("tokens", [])),
        )
        runner = InMemoryRunner(agent=build_agent(), app_name="sentinel")
        session_id = f"interpret-{event.get('idempotency_key', 'adhoc')}"
        events = await runner.run_debug(
            prompt, session_id=session_id, user_id="sentinel-engine", quiet=True
        )
        raw = ""
        for adk_event in events:
            if adk_event.content and adk_event.content.parts:
                for part in adk_event.content.parts:
                    if getattr(part, "text", None):
                        raw = part.text
        result.raw_response = raw
    except Exception as exc:
        # A model or transport failure must not lose the event. The obligation
        # simply is not proposed and the failure is recorded.
        logs.error(
            "interpreter call failed",
            idempotency_key=event.get("idempotency_key"),
            error=str(exc),
            trace_id=trace_id,
        )
        result.rejected.append(RejectedProposal(reason=f"interpreter failed: {exc}", raw=""))
        return result

    try:
        parsed = InterpreterOutput.model_validate_json(result.raw_response)
    except (ValidationError, ValueError) as exc:
        logs.warning(
            "interpreter returned a response that did not match the schema",
            idempotency_key=event.get("idempotency_key"),
            error=str(exc),
            trace_id=trace_id,
        )
        result.rejected.append(
            RejectedProposal(reason=f"malformed response: {exc}", raw=result.raw_response)
        )
        return result

    for proposal in parsed.proposals:
        create, reason = validate_proposal(proposal, event, now)
        if create is None:
            logs.warning(
                "proposal rejected",
                idempotency_key=event.get("idempotency_key"),
                reason=reason,
                proposed_type=proposal.obligation_type,
                trace_id=trace_id,
            )
            result.rejected.append(
                RejectedProposal(reason=reason or "rejected", raw=proposal.model_dump())
            )
        else:
            result.accepted.append(create)

    return result
