"""A department agent.

One image, three deployments. AGENT_ROLE decides which permissions the process
runs under and each deployment runs as a different service account, so the
separation between clinical, revenue, and pathway work is enforced by IAM and by
policy rather than by prompt.

The agent checks its own allow list before running anything, even though the
coordinator already checked policy before dispatching. That is deliberate
duplication: a coordinator bug, a replayed request, or a caller that skipped the
coordinator entirely all still hit a closed door.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

import logs
import policy
import tools
from config import get_settings

METADATA_SA_URL = (
    "http://metadata.google.internal/computeMetadata/v1/"
    "instance/service-accounts/default/email"
)


def running_service_account() -> str | None:
    """The identity this container actually runs as, from the metadata server."""
    try:
        request = urllib.request.Request(
            METADATA_SA_URL, headers={"Metadata-Flavor": "Google"}
        )
        with urllib.request.urlopen(request, timeout=2) as response:
            return response.read().decode("utf-8").strip()
    except (urllib.error.URLError, OSError, TimeoutError):
        # Absent when running locally. Not a failure; the check below only
        # asserts a mismatch it can actually observe.
        return None


class RoleMismatch(Exception):
    """The declared role and the running identity disagree."""


def verify_identity() -> dict[str, Any]:
    """Refuse to serve if the role and the service account do not match.

    A container claiming to be the revenue agent while holding the clinical
    identity would make every policy decision meaningless, so this is fatal
    rather than a warning.
    """
    settings = get_settings()
    role = settings.agent_role

    if role not in policy.known_agents():
        raise RoleMismatch(
            f"AGENT_ROLE {role!r} is not declared in policy.yaml. "
            f"Known roles: {sorted(policy.known_agents())}"
        )

    declared, _ = policy.load_policy()
    expected = (declared.get("agents") or {}).get(role, {}).get("service_account")
    actual = running_service_account()

    if actual and expected and not actual.startswith(f"{expected}@"):
        raise RoleMismatch(
            f"Agent role {role!r} must run as {expected}, but this container runs "
            f"as {actual}."
        )

    return {
        "role": role,
        "expected_service_account": expected,
        "running_as": actual or "unknown (not on Cloud Run)",
        "allowed_actions": sorted(policy.allowed_actions(role)),
    }


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    identity = verify_identity()
    logs.info(
        "agent starting",
        service=f"sentinel-agent-{settings.agent_role}",
        git_sha=settings.git_sha,
        policy_hash=policy.policy_hash(),
        policy_version=policy.policy_version(),
        **identity,
    )
    yield


app = FastAPI(title="sentinel-agent", lifespan=lifespan)


class ActRequest(BaseModel):
    obligation: dict[str, Any]
    action: str
    payload: dict[str, Any] = Field(default_factory=dict)
    policy_decision_id: str | None = None
    trace_id: str | None = None


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    try:
        identity = verify_identity()
    except RoleMismatch as exc:
        return {"service": "sentinel-agent", "role": settings.agent_role, "error": str(exc)}
    return {
        "service": "sentinel-agent",
        "git_sha": settings.git_sha,
        "policy_hash": policy.policy_hash(),
        "policy_version": policy.policy_version(),
        **identity,
    }


@app.post("/act")
def act(request: ActRequest) -> dict:
    settings = get_settings()
    role = settings.agent_role
    obligation_id = request.obligation.get("id")

    decision = policy.decide(role, request.action, obligation_id=obligation_id)
    if not decision.permitted:
        logs.warning(
            "agent refused an action its own policy does not permit",
            obligation_id=obligation_id,
            agent=role,
            action=request.action,
            reason=decision.reason,
            policy_decision_id=decision.decision_id,
            upstream_decision_id=request.policy_decision_id,
            trace_id=request.trace_id,
        )
        return {
            "status": "denied",
            "agent": role,
            "action": request.action,
            "reason": decision.reason,
            "policy_decision_id": decision.decision_id,
            "policy_hash": decision.policy_hash,
        }

    if decision.needs_approval:
        # The agent does not decide whether approval was granted. It reports
        # that approval is required and stops.
        logs.info(
            "agent stopped short of a tier two action pending approval",
            obligation_id=obligation_id,
            agent=role,
            action=request.action,
            policy_decision_id=decision.decision_id,
            trace_id=request.trace_id,
        )
        return {
            "status": "requires_approval",
            "agent": role,
            "action": request.action,
            "reason": decision.reason,
            "policy_decision_id": decision.decision_id,
            "policy_hash": decision.policy_hash,
        }

    result = tools.run(request.action, request.obligation, request.payload)
    logs.info(
        "agent acted",
        obligation_id=obligation_id,
        agent=role,
        action=request.action,
        ok=result.ok,
        summary=result.summary,
        external_ref=result.external_ref,
        policy_decision_id=decision.decision_id,
        trace_id=request.trace_id,
    )
    if not result.ok:
        raise HTTPException(
            status_code=502,
            detail={
                "status": "failed",
                "agent": role,
                "action": request.action,
                "reason": result.summary,
                "policy_decision_id": decision.decision_id,
            },
        )
    return {
        "status": "executed",
        "agent": role,
        "action": request.action,
        "summary": result.summary,
        "external_ref": result.external_ref,
        "data": result.data,
        "policy_decision_id": decision.decision_id,
        "policy_hash": decision.policy_hash,
    }
