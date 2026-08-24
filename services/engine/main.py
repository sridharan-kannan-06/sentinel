"""Sentinel engine: the obligation ledger, its state machine, and its timers.

Phase 0 scope. There is deliberately no model call anywhere in this service yet.
The Interpreter, the Coordinator, the Policy Engine, and the Evidence Gate arrive
in later phases and all of them write status through `ledger.transition`.
"""

from __future__ import annotations

import hashlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request

import ledger
import logs
import timers
from config import get_settings
from models import (
    IllegalTransition,
    ObligationCreate,
    ObligationStatus,
    WakePayload,
)

def policy_path() -> Path | None:
    """Locate policy.yaml in both the repo checkout and the container image.

    In the repo the file sits two levels up at policy/policy.yaml; in the image
    the service is flattened into /app, where indexing a fixed number of parents
    walks off the end of the path. Search the parents that actually exist.
    """
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


def policy_config_hash() -> str:
    """Hash of the policy config, logged at boot and surfaced in the trust panel.

    The policy engine lands in Phase 2. Until the file exists this reports
    "absent" rather than a hash of nothing, so the UI never shows a hash that
    corresponds to no policy.
    """
    path = policy_path()
    if path is None:
        return "absent"
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def trace_id_from(request: Request) -> str | None:
    """Cloud Run sets X-Cloud-Trace-Context as TRACE_ID/SPAN_ID;o=1."""
    header = request.headers.get("X-Cloud-Trace-Context")
    if not header:
        return None
    return header.split("/")[0] or None


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logs.info(
        "engine starting",
        service="sentinel-engine",
        git_sha=settings.git_sha,
        policy_config_hash=policy_config_hash(),
        gemini_model=settings.gemini_model,
        gemini_location=settings.gemini_location,
        evidence_gate=settings.evidence_gate,
    )
    if not settings.evidence_gate_enabled:
        logs.warning(
            "EVIDENCE_GATE IS OFF. Closure proposals will be accepted without "
            "authoritative evidence. This setting exists only for the ablation "
            "demonstration and must never be off in a real deployment."
        )
    yield


app = FastAPI(title="sentinel-engine", lifespan=lifespan)


@app.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    return {
        "service": "sentinel-engine",
        "git_sha": settings.git_sha,
        "policy_config_hash": policy_config_hash(),
        "evidence_gate": "on" if settings.evidence_gate_enabled else "off",
        "gemini_model": settings.gemini_model,
        "gemini_location": settings.gemini_location,
    }


@app.post("/obligations", status_code=201)
def create_obligation(payload: ObligationCreate, request: Request) -> dict:
    trace_id = trace_id_from(request)
    try:
        obligation = ledger.create_obligation(
            payload,
            actor="system:ingest",
            reason="Obligation admitted from inbound event",
            trace_id=trace_id,
        )
    except IllegalTransition as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    logs.info(
        "obligation created",
        obligation_id=obligation.id,
        type=obligation.type,
        subject_token=obligation.subject_token,
        status=obligation.status.value,
        deadline=obligation.deadline.isoformat(),
        trace_id=trace_id,
    )

    # The timer is enqueued after the write commits. If the enqueue fails the
    # obligation still exists and the sweep will re-arm it.
    task_names = timers.schedule_all(obligation.id, obligation.checkpoints, trace_id)

    return {"obligation": obligation.model_dump(mode="json"), "tasks": task_names}


@app.post("/wake")
def wake(payload: WakePayload, request: Request) -> dict:
    """A Cloud Task fired for one checkpoint on one obligation.

    Phase 0 records the wake and marks the checkpoint fired. Deciding what the
    wake means, nudging, and escalating arrive in Phase 1.
    """
    trace_id = trace_id_from(request)
    obligation = ledger.get_obligation(payload.obligation_id)
    if obligation is None:
        logs.warning(
            "wake for unknown obligation",
            obligation_id=payload.obligation_id,
            trace_id=trace_id,
        )
        raise HTTPException(status_code=404, detail="obligation not found")

    if obligation.status in {
        ObligationStatus.CLOSED,
        ObligationStatus.REJECTED,
        ObligationStatus.CANCELLED,
    }:
        # A late timer against a settled obligation is normal, not an error.
        ledger.record_entry(
            obligation.id,
            actor="system:timer",
            action="wake.ignored",
            reason=f"Checkpoint {payload.checkpoint_kind.value} fired after the "
            f"obligation reached {obligation.status.value}",
            trace_id=trace_id,
        )
        return {"obligation_id": obligation.id, "status": obligation.status.value, "acted": False}

    ledger.record_entry(
        obligation.id,
        actor="system:timer",
        action="wake.received",
        reason=f"Checkpoint {payload.checkpoint_kind.value} scheduled for "
        f"{payload.scheduled_for.isoformat()} fired",
        trace_id=trace_id,
    )
    logs.info(
        "wake received",
        obligation_id=obligation.id,
        checkpoint=payload.checkpoint_kind.value,
        scheduled_for=payload.scheduled_for.isoformat(),
        status=obligation.status.value,
        trace_id=trace_id,
    )
    return {"obligation_id": obligation.id, "status": obligation.status.value, "acted": True}


@app.get("/obligations")
def list_obligations(limit: int = 100) -> dict:
    obligations = ledger.list_obligations(limit=limit)
    return {
        "count": len(obligations),
        "obligations": [o.model_dump(mode="json") for o in obligations],
    }


@app.get("/obligations/{obligation_id}")
def get_obligation(obligation_id: str) -> dict:
    obligation = ledger.get_obligation(obligation_id)
    if obligation is None:
        raise HTTPException(status_code=404, detail="obligation not found")
    entries = ledger.get_ledger(obligation_id)
    return {
        "obligation": obligation.model_dump(mode="json"),
        "ledger": [e.model_dump(mode="json") for e in entries],
    }
