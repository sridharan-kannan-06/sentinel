"""The Policy Engine.

Deterministic code. There is no model call in this module and there must never
be one: a system where the thing being governed can argue with the governor is
not governed. An agent may request an action; it has no influence on the answer.

Deny by default, in three layers. An agent that is not declared is denied. An
action that is not declared is denied. A declared action that is not in that
agent's allow list is denied. Only after all three pass does the tier decide
between allow, allow with approval, and deny.

Every decision carries the hash of the policy file that produced it, so a
decision in the ledger can be tied to an exact configuration rather than to
whatever the file happens to say today.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from enum import Enum
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel


class Outcome(str, Enum):
    ALLOW = "ALLOW"
    ALLOW_WITH_APPROVAL = "ALLOW_WITH_APPROVAL"
    DENY = "DENY"


HANDLING_TO_OUTCOME = {
    "allow": Outcome.ALLOW,
    "allow_with_approval": Outcome.ALLOW_WITH_APPROVAL,
    "deny": Outcome.DENY,
}


class PolicyDecision(BaseModel):
    decision_id: str
    outcome: Outcome
    reason: str
    agent: str
    action: str
    tier: str | None = None
    resource: str | None = None
    policy_version: int | None = None
    policy_hash: str = "absent"
    obligation_id: str | None = None

    @property
    def permitted(self) -> bool:
        return self.outcome is not Outcome.DENY

    @property
    def needs_approval(self) -> bool:
        return self.outcome is Outcome.ALLOW_WITH_APPROVAL


class PolicyUnavailable(Exception):
    """Raised when the policy file cannot be found or parsed.

    This is deliberately fatal rather than falling back to a permissive default.
    A system that cannot read its own policy has no business acting.
    """


def policy_path() -> Path | None:
    """Find policy.yaml in the repo checkout and in the flattened container."""
    override = os.environ.get("POLICY_PATH")
    if override:
        candidate = Path(override)
        return candidate if candidate.exists() else None

    here = Path(__file__).resolve()
    candidates = [here.parent / "policy.yaml"]
    candidates += [parent / "policy" / "policy.yaml" for parent in here.parents]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@lru_cache(maxsize=1)
def load_policy() -> tuple[dict, str]:
    """Return the parsed policy and the hash of the exact bytes it came from."""
    path = policy_path()
    if path is None:
        raise PolicyUnavailable("policy.yaml was not found")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    try:
        parsed = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        raise PolicyUnavailable(f"policy.yaml did not parse: {exc}") from exc
    if not isinstance(parsed, dict):
        raise PolicyUnavailable("policy.yaml did not contain a mapping")
    return parsed, digest


def policy_hash() -> str:
    try:
        return load_policy()[1]
    except PolicyUnavailable:
        return "absent"


def policy_version() -> int | None:
    try:
        return load_policy()[0].get("version")
    except PolicyUnavailable:
        return None


def allowed_actions(agent: str) -> frozenset[str]:
    """The tool allowlist for one agent, enforced before any dispatch."""
    try:
        policy, _ = load_policy()
    except PolicyUnavailable:
        return frozenset()
    entry = (policy.get("agents") or {}).get(agent)
    if not entry:
        return frozenset()
    return frozenset(entry.get("allow") or [])


def known_agents() -> frozenset[str]:
    try:
        policy, _ = load_policy()
    except PolicyUnavailable:
        return frozenset()
    return frozenset((policy.get("agents") or {}).keys())


def _decision(
    outcome: Outcome,
    reason: str,
    agent: str,
    action: str,
    digest: str,
    version: int | None,
    obligation_id: str | None,
    tier: str | None = None,
    resource: str | None = None,
) -> PolicyDecision:
    return PolicyDecision(
        decision_id=f"PD-{uuid.uuid4().hex[:12]}",
        outcome=outcome,
        reason=reason,
        agent=agent,
        action=action,
        tier=tier,
        resource=resource,
        policy_version=version,
        policy_hash=digest,
        obligation_id=obligation_id,
    )


def decide(agent: str, action: str, obligation_id: str | None = None) -> PolicyDecision:
    """Answer whether this agent may take this action.

    The action's own tier governs, not the obligation's. An obligation's risk
    tier describes what discharging it will eventually require; it does not make
    reading the ledger about it a sensitive act. Conflating the two would put an
    approval gate in front of every read on an external obligation.
    """
    try:
        policy, digest = load_policy()
    except PolicyUnavailable as exc:
        return _decision(
            Outcome.DENY,
            f"Policy configuration is unavailable, so nothing is permitted: {exc}",
            agent,
            action,
            "absent",
            None,
            obligation_id,
        )

    version = policy.get("version")
    agents = policy.get("agents") or {}
    actions = policy.get("actions") or {}
    tiers = policy.get("tiers") or {}

    agent_entry = agents.get(agent)
    if agent_entry is None:
        return _decision(
            Outcome.DENY,
            f"Agent {agent!r} is not declared in the policy, so it has no permissions.",
            agent,
            action,
            digest,
            version,
            obligation_id,
        )

    action_entry = actions.get(action)
    if action_entry is None:
        return _decision(
            Outcome.DENY,
            f"Action {action!r} is not declared in the policy. Undeclared actions are denied.",
            agent,
            action,
            digest,
            version,
            obligation_id,
        )

    tier = action_entry.get("tier")
    resource = action_entry.get("resource")
    tier_entry = tiers.get(tier) or {}

    # T3 is checked before the allow list on purpose. Every agent is refused a
    # T3 action for the same reason, and that reason is the interesting one:
    # Sentinel has no clinical authority at all. Reaching it through the allow
    # list instead would report the narrower and less true "wrong department".
    if HANDLING_TO_OUTCOME.get(tier_entry.get("handling")) is Outcome.DENY:
        return _decision(
            Outcome.DENY,
            (
                f"{action!r} is tier {tier}, which is denied for every agent without "
                f"exception and has no approval path. "
                f"{' '.join((tier_entry.get('description') or '').split())}"
            ),
            agent,
            action,
            digest,
            version,
            obligation_id,
            tier,
            resource,
        )

    if action not in set(agent_entry.get("allow") or []):
        return _decision(
            Outcome.DENY,
            (
                f"Agent {agent!r} is not permitted to perform {action!r}. "
                f"That action belongs to a different trust domain."
            ),
            agent,
            action,
            digest,
            version,
            obligation_id,
            tier,
            resource,
        )

    outcome = HANDLING_TO_OUTCOME.get(tier_entry.get("handling"))
    if outcome is None:
        return _decision(
            Outcome.DENY,
            f"Tier {tier!r} has no valid handling in the policy, so the action is denied.",
            agent,
            action,
            digest,
            version,
            obligation_id,
            tier,
            resource,
        )

    if outcome is Outcome.ALLOW_WITH_APPROVAL:
        reason = (
            f"{action!r} is tier {tier} and reaches outside the hospital, so a "
            f"named human must approve the exact payload before it is sent."
        )
    else:
        reason = f"{action!r} is tier {tier} and is permitted for {agent!r} without approval."

    return _decision(
        outcome, reason, agent, action, digest, version, obligation_id, tier, resource
    )
