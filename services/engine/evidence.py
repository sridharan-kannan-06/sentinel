"""The Evidence Gate.

The second invariant in CLAUDE.md says the model may propose closure but only an
authoritative external fact may close. This module is that fact-checker, and it
contains no model call. An agent can be as confident as it likes; if the five
checks below do not all pass, the obligation moves to PENDING_EVIDENCE and the
agent's next job becomes obtaining the proof rather than asserting it.

The ablation flag deserves a note. When EVIDENCE_GATE is off the checks still
run and are still recorded; only their verdict is ignored. That is deliberate.
Turning the gate off and seeing "closed, and here are the four checks that would
have stopped this" is a far better demonstration than seeing no checks at all,
and it keeps the audit trail honest about what was skipped.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml
from pydantic import BaseModel, Field

from config import get_settings
from models import Obligation

# Systems disagree about clocks by small amounts. Evidence timestamped slightly
# in the future is a clock difference, not a forgery.
CLOCK_SKEW = timedelta(minutes=5)


class Evidence(BaseModel):
    source: str = Field(description="The system that produced this fact")
    external_reference: str = Field(description="Its identifier in that system")
    observed_at: datetime = Field(description="When that system recorded it")
    subject_token: str = Field(description="Which subject it concerns")
    assertion: str = Field(description="What it says happened")


class Check(BaseModel):
    name: str
    passed: bool
    detail: str


class Verdict(BaseModel):
    accepted: bool
    gate_enabled: bool
    checks: list[Check] = Field(default_factory=list)
    evidence_hash: str = ""
    config_hash: str = "absent"

    @property
    def failures(self) -> list[Check]:
        return [c for c in self.checks if not c.passed]

    @property
    def summary(self) -> str:
        if not self.failures:
            return f"All {len(self.checks)} evidence checks passed"
        names = ", ".join(c.name for c in self.failures)
        return f"{len(self.failures)} of {len(self.checks)} evidence checks failed: {names}"


class EvidenceConfigUnavailable(Exception):
    """Raised when evidence.yaml is missing or unparseable."""


def config_path() -> Path | None:
    here = Path(__file__).resolve()
    candidates = [here.parent / "evidence.yaml"]
    candidates += [parent / "policy" / "evidence.yaml" for parent in here.parents]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@lru_cache(maxsize=1)
def load_config() -> tuple[dict, str]:
    path = config_path()
    if path is None:
        raise EvidenceConfigUnavailable("evidence.yaml was not found")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise EvidenceConfigUnavailable("evidence.yaml did not contain a mapping")
    return parsed, digest


def config_hash() -> str:
    try:
        return load_config()[1]
    except EvidenceConfigUnavailable:
        return "absent"


def rules_for(obligation_type: str) -> dict:
    config, _ = load_config()
    defaults = config.get("defaults") or {}
    specific = (config.get("obligation_types") or {}).get(obligation_type)
    if specific is None:
        # An unknown obligation type has no authoritative sources, so nothing can
        # satisfy it. That is the correct answer, not a reason to wave it through.
        return {
            "authoritative_sources": [],
            "acceptance_window_hours": defaults.get("acceptance_window_hours", 168),
            "assertion_keywords": [],
            "minimum_reference_length": defaults.get("minimum_reference_length", 4),
            "unknown_type": True,
        }
    return {
        "authoritative_sources": specific.get("authoritative_sources") or [],
        "acceptance_window_hours": specific.get(
            "acceptance_window_hours", defaults.get("acceptance_window_hours", 168)
        ),
        "assertion_keywords": specific.get("assertion_keywords") or [],
        "minimum_reference_length": defaults.get("minimum_reference_length", 4),
        "unknown_type": False,
    }


def evidence_hash(evidence: Evidence) -> str:
    material = "|".join(
        [
            evidence.source,
            evidence.external_reference,
            evidence.observed_at.isoformat(),
            evidence.subject_token,
            evidence.assertion,
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _check_source(evidence: Evidence, rules: dict) -> Check:
    allowed = rules["authoritative_sources"]
    if rules["unknown_type"]:
        return Check(
            name="source_is_authoritative",
            passed=False,
            detail="No authoritative sources are declared for this obligation type, "
            "so nothing can close it.",
        )
    passed = evidence.source in allowed
    return Check(
        name="source_is_authoritative",
        passed=passed,
        detail=(
            f"{evidence.source!r} is an authoritative source for this obligation type"
            if passed
            else f"{evidence.source!r} is not authoritative here. Accepted: {', '.join(allowed)}"
        ),
    )


def _check_reference(evidence: Evidence, rules: dict) -> Check:
    reference = evidence.external_reference.strip()
    minimum = rules["minimum_reference_length"]
    passed = len(reference) >= minimum
    return Check(
        name="external_reference_present",
        passed=passed,
        detail=(
            f"External reference {reference!r} recorded"
            if passed
            else f"External reference is missing or shorter than {minimum} characters. "
            "Without one there is nothing to audit against."
        ),
    )


def _check_window(evidence: Evidence, obligation: Obligation, rules: dict, now: datetime) -> Check:
    window = timedelta(hours=rules["acceptance_window_hours"])
    observed = evidence.observed_at

    if observed > now + CLOCK_SKEW:
        return Check(
            name="within_acceptance_window",
            passed=False,
            detail=f"Evidence is timestamped {observed.isoformat()}, which is in the future.",
        )
    if observed < obligation.created_at - CLOCK_SKEW:
        # Evidence that predates the obligation describes something else.
        return Check(
            name="within_acceptance_window",
            passed=False,
            detail=(
                f"Evidence predates the obligation. Observed {observed.isoformat()}, "
                f"obligation opened {obligation.created_at.isoformat()}."
            ),
        )
    if now - observed > window:
        return Check(
            name="within_acceptance_window",
            passed=False,
            detail=(
                f"Evidence is older than the {rules['acceptance_window_hours']} hour "
                f"acceptance window for this obligation type."
            ),
        )
    return Check(
        name="within_acceptance_window",
        passed=True,
        detail=f"Observed {observed.isoformat()}, inside the acceptance window",
    )


def _check_subject(evidence: Evidence, obligation: Obligation) -> Check:
    passed = evidence.subject_token == obligation.subject_token
    return Check(
        name="subject_token_matches",
        passed=passed,
        detail=(
            f"Evidence concerns {evidence.subject_token}, which is this obligation's subject"
            if passed
            else f"Evidence concerns {evidence.subject_token} but this obligation is "
            f"about {obligation.subject_token}."
        ),
    )


def _check_assertion(evidence: Evidence, obligation: Obligation, rules: dict) -> Check:
    keywords = rules["assertion_keywords"]
    assertion = evidence.assertion.strip().lower()

    if not assertion:
        return Check(
            name="assertion_matches_requirement",
            passed=False,
            detail="The evidence asserts nothing.",
        )
    if not keywords:
        return Check(
            name="assertion_matches_requirement",
            passed=False,
            detail="No assertion keywords are declared for this obligation type.",
        )

    matched = [k for k in keywords if k in assertion]
    return Check(
        name="assertion_matches_requirement",
        passed=bool(matched),
        detail=(
            f"Assertion matches {matched[0]!r}, consistent with the required evidence: "
            f"{obligation.required_evidence}"
            if matched
            else f"Assertion does not describe the required evidence "
            f"({obligation.required_evidence}). Expected one of: {', '.join(keywords)}"
        ),
    )


def evaluate(
    obligation: Obligation, evidence: Evidence, now: datetime | None = None
) -> Verdict:
    """Run all five checks. The only thing permitted to authorise CLOSED."""
    now = now or datetime.now(timezone.utc)
    gate_enabled = get_settings().evidence_gate_enabled

    try:
        rules = rules_for(obligation.type)
    except EvidenceConfigUnavailable as exc:
        # Unable to read the rules means unable to verify. Refuse rather than
        # assume, even with the gate off, because there is nothing to report.
        return Verdict(
            accepted=False,
            gate_enabled=gate_enabled,
            checks=[Check(name="configuration", passed=False, detail=str(exc))],
            evidence_hash=evidence_hash(evidence),
        )

    checks = [
        _check_source(evidence, rules),
        _check_reference(evidence, rules),
        _check_window(evidence, obligation, rules, now),
        _check_subject(evidence, obligation),
        _check_assertion(evidence, obligation, rules),
    ]
    all_passed = all(c.passed for c in checks)

    return Verdict(
        # With the gate off the checks are still run and still recorded; only the
        # verdict is ignored. The ablation is then legible rather than blank.
        accepted=all_passed or not gate_enabled,
        gate_enabled=gate_enabled,
        checks=checks,
        evidence_hash=evidence_hash(evidence),
        config_hash=config_hash(),
    )
