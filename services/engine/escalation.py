"""The escalation ladder and its rate limit.

Who hears about an obligation widens as it ages: the owner on a nudge, the owner
and their coordinator on a breach, a supervisor beyond that. The ladder is
configuration, not code, so changing who gets told does not mean changing the
engine.

The rate limit is counted from the ledger rather than held in memory. This
service scales to zero, restarts freely, and can run two instances at once, so
an in-memory counter would silently reset and let a person be nudged far more
often than the configuration says. Counting the append-only history is the only
version of this that is actually true.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

import yaml

import ledger
import logs
import notify
from config import get_settings
from models import CheckpointKind, Obligation

NOTIFICATION_ACTION = "notification.sent"
SUPPRESSED_ACTION = "notification.suppressed"


class EscalationConfigUnavailable(Exception):
    """Raised when escalation.yaml is missing or unparseable."""


def config_path() -> Path | None:
    here = Path(__file__).resolve()
    candidates = [here.parent / "escalation.yaml"]
    candidates += [parent / "policy" / "escalation.yaml" for parent in here.parents]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


@lru_cache(maxsize=1)
def load_config() -> tuple[dict, str]:
    path = config_path()
    if path is None:
        raise EscalationConfigUnavailable("escalation.yaml was not found")
    raw = path.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()[:16]
    parsed = yaml.safe_load(raw)
    if not isinstance(parsed, dict):
        raise EscalationConfigUnavailable("escalation.yaml did not contain a mapping")
    return parsed, digest


def config_hash() -> str:
    try:
        return load_config()[1]
    except EscalationConfigUnavailable:
        return "absent"


def rung(obligation_type: str, kind: CheckpointKind) -> list[str]:
    """The roles that should hear about this checkpoint on this obligation type."""
    try:
        config, _ = load_config()
    except EscalationConfigUnavailable:
        return ["owner"]
    defaults = (config.get("defaults") or {}).get("ladder") or {}
    specific = ((config.get("obligation_types") or {}).get(obligation_type) or {}).get(
        "ladder"
    ) or {}
    return specific.get(kind.value) or defaults.get(kind.value) or ["owner"]


def limit_per_recipient() -> int:
    try:
        config, _ = load_config()
    except EscalationConfigUnavailable:
        return 2
    return int(
        (config.get("defaults") or {}).get("max_notifications_per_recipient_per_24h", 2)
    )


def resolve_address(role: str, obligation: Obligation) -> str:
    """Turn a ladder role into something a mail server accepts.

    A real deployment resolves roles against the hospital directory. Here every
    role lands on the configured operator address, which is honest about the
    scope of the build rather than inventing a directory that does not exist.
    """
    settings = get_settings()
    if settings.notify_to and "@" in settings.notify_to:
        return settings.notify_to
    owner = obligation.owner_id
    if "@" in owner:
        return owner
    raise notify.NotifierError(f"No deliverable address for role {role!r}")


def recent_notification_count(
    obligation_id: str, recipient: str, now: datetime | None = None
) -> int:
    """How many notifications this recipient has had about this obligation today.

    Read from the append-only ledger, so it survives a restart and is consistent
    across instances.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=24)
    count = 0
    for entry in ledger.get_ledger(obligation_id):
        if entry.action != NOTIFICATION_ACTION:
            continue
        if entry.recipient != recipient:
            continue
        if entry.at >= cutoff:
            count += 1
    return count


def notify_rung(
    obligation: Obligation,
    kind: CheckpointKind,
    subject: str,
    body: str,
    trace_id: str | None = None,
    now: datetime | None = None,
) -> dict:
    """Notify everyone on this rung, respecting the rate limit.

    Returns a summary rather than raising. A person who could not be reached is
    worth recording, but it must never stop the obligation's state from changing:
    the work is still overdue whether or not the email arrived.
    """
    roles = rung(obligation.type, kind)
    limit = limit_per_recipient()
    notifier = notify.get_notifier()

    delivered: list[str] = []
    suppressed: list[str] = []
    failed: list[str] = []
    references: list[str] = []
    seen: set[str] = set()

    for role in roles:
        try:
            address = resolve_address(role, obligation)
        except notify.NotifierError as exc:
            failed.append(f"{role}: {exc}")
            continue

        # Two ladder roles can resolve to the same person. Telling them twice in
        # one wake is the most obvious way to be annoying.
        if address in seen:
            continue
        seen.add(address)

        already = recent_notification_count(obligation.id, address, now=now)
        if already >= limit:
            suppressed.append(f"{role} ({address})")
            ledger.record_entry(
                obligation.id,
                actor="system:escalation",
                action=SUPPRESSED_ACTION,
                reason=(
                    f"Rate limit reached for {address}: {already} notifications about "
                    f"this obligation in the last 24 hours, limit is {limit}. "
                    f"The {kind.value} was not resent."
                ),
                recipient=address,
                trace_id=trace_id,
            )
            continue

        try:
            reference = notifier.send(
                notify.Notification(
                    to=address,
                    subject=subject,
                    body=body,
                    obligation_id=obligation.id,
                    kind=kind.value,
                    trace_id=trace_id,
                )
            )
        except notify.NotifierError as exc:
            failed.append(f"{role}: {exc}")
            logs.error(
                "escalation delivery failed",
                obligation_id=obligation.id,
                role=role,
                error=str(exc),
                trace_id=trace_id,
            )
            continue

        delivered.append(f"{role} ({address})")
        references.append(reference)
        ledger.record_entry(
            obligation.id,
            actor="system:escalation",
            action=NOTIFICATION_ACTION,
            reason=f"{kind.value} delivered to {role} at {address} via {notifier.name}",
            evidence_ref=reference,
            recipient=address,
            trace_id=trace_id,
        )

    logs.info(
        "escalation rung notified",
        obligation_id=obligation.id,
        checkpoint=kind.value,
        roles=roles,
        delivered=delivered,
        suppressed=suppressed,
        failed=failed,
        trace_id=trace_id,
    )
    return {
        "roles": roles,
        "delivered": delivered,
        "suppressed": suppressed,
        "failed": failed,
        "references": references,
        "notifier": notifier.name,
    }
