"""The tools a department agent can actually run.

Every tool is registered against the action name the policy declares, so the
allow list in policy.yaml and the code that executes are keyed on the same
string. A tool with no policy entry can never be reached, and a policy entry
with no tool reports honestly that it is not implemented rather than silently
succeeding.

No tool writes to the obligation ledger. Agents act in the world and report what
happened; only the engine changes an obligation's status, and only through the
Evidence Gate.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import logs
import notify
from config import get_settings


def resolve_recipient(obligation: dict, payload: dict) -> str:
    """Turn an obligation owner into something a mail server will accept.

    owner_id is a role identifier such as STAFF-billing-01, not an address. A
    real deployment resolves it against the hospital directory; here it falls
    back to the configured operator address, because handing Gmail a role
    identifier fails with an unhelpful "Invalid To header".
    """
    for candidate in (payload.get("to"), get_settings().notify_to):
        if candidate and "@" in candidate:
            return candidate
    owner = obligation.get("owner_id", "")
    if "@" in owner:
        return owner
    raise notify.NotifierError(
        f"No deliverable address for owner {owner!r}. Set NOTIFY_TO or supply one."
    )


@dataclass
class ToolResult:
    ok: bool
    summary: str
    external_ref: str | None = None
    data: dict[str, Any] | None = None


ToolFn = Callable[[dict, dict], ToolResult]

REGISTRY: dict[str, ToolFn] = {}


def tool(action: str) -> Callable[[ToolFn], ToolFn]:
    def register(fn: ToolFn) -> ToolFn:
        REGISTRY[action] = fn
        return fn

    return register


def _notify(obligation: dict, payload: dict, subject: str, body: str) -> ToolResult:
    notifier = notify.get_notifier()
    try:
        recipient = resolve_recipient(obligation, payload)
        reference = notifier.send(
            notify.Notification(
                to=recipient,
                subject=subject,
                body=body,
                obligation_id=obligation.get("id", "unknown"),
                kind=payload.get("kind", "agent"),
            )
        )
    except notify.NotifierError as exc:
        logs.error(
            "agent could not deliver a notification",
            obligation_id=obligation.get("id"),
            error=str(exc),
        )
        return ToolResult(ok=False, summary=f"delivery failed: {exc}")
    return ToolResult(ok=True, summary=f"notified {recipient} via {notifier.name}",
                      external_ref=reference)


def _obligation_summary(obligation: dict) -> str:
    return (
        f"Obligation {obligation.get('id')}\n"
        f"Type: {obligation.get('type')}\n"
        f"Subject: {obligation.get('subject_token')}\n"
        f"Owner: {obligation.get('owner_role')} ({obligation.get('owner_id')})\n"
        f"Deadline: {obligation.get('deadline')}\n"
        f"Opened: {obligation.get('created_at')}\n\n"
        f"This closes only when the following is produced:\n"
        f"  {obligation.get('required_evidence')}\n\n"
        f"Sentinel coordinates. It does not decide clinical matters.\n"
    )


@tool("notify_clinician")
def notify_clinician(obligation: dict, payload: dict) -> ToolResult:
    return _notify(
        obligation,
        payload,
        f"[Sentinel] Unacknowledged result for {obligation.get('subject_token')}",
        _obligation_summary(obligation),
    )


@tool("request_acknowledgement")
def request_acknowledgement(obligation: dict, payload: dict) -> ToolResult:
    return _notify(
        obligation,
        payload,
        f"[Sentinel] Acknowledgement required for {obligation.get('subject_token')}",
        "An acknowledgement record is required to close this.\n\n"
        + _obligation_summary(obligation),
    )


@tool("escalate_internal")
def escalate_internal(obligation: dict, payload: dict) -> ToolResult:
    return _notify(
        obligation,
        payload,
        f"[Sentinel] ESCALATION on {obligation.get('type')} for "
        f"{obligation.get('subject_token')}",
        "This obligation has passed its deadline without the required evidence.\n\n"
        + _obligation_summary(obligation),
    )


@tool("request_internal_document")
def request_internal_document(obligation: dict, payload: dict) -> ToolResult:
    document = payload.get("document", "the outstanding document")
    return _notify(
        obligation,
        payload,
        f"[Sentinel] Document required: {document}",
        f"{document} is outstanding.\n\n" + _obligation_summary(obligation),
    )


@tool("read_lab_status")
def read_lab_status(obligation: dict, payload: dict) -> ToolResult:
    # Reads the obligation's own view of clinical progress. There is no
    # laboratory system integrated behind this yet, and inventing a response
    # would be fabricating a data source.
    return ToolResult(
        ok=True,
        summary=f"Lab obligation {obligation.get('id')} is {obligation.get('status')}",
        data={"status": obligation.get("status"), "evidence": obligation.get("required_evidence")},
    )


@tool("read_order_status")
def read_order_status(obligation: dict, payload: dict) -> ToolResult:
    return ToolResult(
        ok=True,
        summary=f"Order for {obligation.get('subject_token')} is {obligation.get('status')}",
        data={"status": obligation.get("status")},
    )


@tool("read_claim_status")
def read_claim_status(obligation: dict, payload: dict) -> ToolResult:
    return ToolResult(
        ok=True,
        summary=f"Claim obligation {obligation.get('id')} is {obligation.get('status')}",
        data={"status": obligation.get("status")},
    )


@tool("read_document_status")
def read_document_status(obligation: dict, payload: dict) -> ToolResult:
    return ToolResult(
        ok=True,
        summary=f"Required evidence outstanding: {obligation.get('required_evidence')}",
        data={"required_evidence": obligation.get("required_evidence")},
    )


@tool("check_discharge_blockers")
def check_discharge_blockers(obligation: dict, payload: dict) -> ToolResult:
    blocked_by = obligation.get("blocked_by") or []
    if not blocked_by:
        return ToolResult(ok=True, summary="Nothing is blocking this discharge",
                          data={"blocked_by": []})
    return ToolResult(
        ok=True,
        summary=f"Blocked by {len(blocked_by)} obligation(s): {', '.join(blocked_by)}",
        data={"blocked_by": blocked_by},
    )


@tool("check_referral_status")
def check_referral_status(obligation: dict, payload: dict) -> ToolResult:
    return ToolResult(
        ok=True,
        summary=f"Referral for {obligation.get('subject_token')} is {obligation.get('status')}",
        data={"status": obligation.get("status")},
    )


@tool("schedule_followup")
def schedule_followup(obligation: dict, payload: dict) -> ToolResult:
    when = payload.get("when", "unspecified")
    return _notify(
        obligation,
        payload,
        f"[Sentinel] Follow-up to schedule for {obligation.get('subject_token')}",
        f"A follow-up is required at {when}.\n\n" + _obligation_summary(obligation),
    )


@tool("post_board_update")
def post_board_update(obligation: dict, payload: dict) -> ToolResult:
    logs.info(
        "board update",
        obligation_id=obligation.get("id"),
        note=payload.get("note", ""),
    )
    return ToolResult(ok=True, summary="Board updated")


def run(action: str, obligation: dict, payload: dict) -> ToolResult:
    fn = REGISTRY.get(action)
    if fn is None:
        # Reachable only for an action the policy permits but no tool implements,
        # such as the tier two actions that wait on the approval queue.
        return ToolResult(ok=False, summary=f"No tool implements {action!r}")
    return fn(obligation, payload)
