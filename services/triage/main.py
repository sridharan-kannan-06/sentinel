"""In-boundary triage on Gemma.

The first pass over an event runs on a small open model inside the hospital's own
trust boundary, on CPU, in this container. Nothing leaves the boundary to produce
this classification.

That is the whole point of it being here rather than being another Gemini call.
Gemini is excellent and it is also somebody else's service; the argument this
system makes about where data goes is stronger if the cheapest, highest-volume
pass over that data never goes anywhere at all.

What it produces is advisory and stays advisory. It labels the department that
most likely owes the work and whether the event looks like it creates an
obligation at all. The interpreter still reads every event and the ledger still
admits obligations through the same validated path, because a one billion
parameter model deciding that nothing needs doing is exactly the failure this
system exists to prevent. Its label is recorded and compared, never obeyed.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from pydantic import BaseModel, Field

import logs

OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MODEL = os.environ.get("TRIAGE_MODEL", "gemma3:4b")
TIMEOUT_SECONDS = float(os.environ.get("TRIAGE_TIMEOUT", "25"))

DOMAINS = ("clinical", "revenue", "pathway", "none")

# Few-shot rather than instructions alone. A small model asked to "answer with
# one word" returns a word from the message rather than a label from the list;
# shown four labelled examples it returns a label. The examples cover one of each
# class, including the one that must not create work.
PROMPT = """Classify each hospital operations message into exactly one label.

Labels:
clinical = lab results, clinician acknowledgement, clinical follow-up
revenue = insurance, pre-authorisation, claims, billing, claim documents
pathway = discharge blockers, referrals, appointments, transfers
none = nothing needs doing

Message: Critical troponin for PT-1111, DR-2222 has not acknowledged the result.
Label: clinical

Message: PT-3333 admitted for surgery, TPA pre-authorisation not yet raised.
Label: revenue

Message: Discharge for PT-4444 held, pharmacy reconciliation still open.
Label: pathway

Message: The staff car park will be resurfaced on Saturday.
Label: none

Message: {text}
Label:"""


class TriageRequest(BaseModel):
    text: str = Field(min_length=1)
    event_type: str = ""


class TriageResult(BaseModel):
    domain: str
    creates_obligation: bool
    model: str
    raw: str = ""
    degraded: bool = False
    reason: str = ""


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    logs.info(
        "triage starting",
        service="sentinel-triage",
        model=MODEL,
        note="runs inside the trust boundary, on CPU, in this container",
    )
    yield


app = FastAPI(title="sentinel-triage", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    ready, detail = model_ready()
    return {
        "service": "sentinel-triage",
        "model": MODEL,
        "model_loaded": ready,
        "detail": detail,
    }


def model_ready() -> tuple[bool, str]:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as response:
            tags = json.loads(response.read().decode("utf-8"))
        names = [m.get("name", "") for m in tags.get("models", [])]
        return (MODEL in names, f"available: {', '.join(names) or 'none'}")
    except Exception as exc:
        return (False, f"ollama unreachable: {exc}")


def normalise(raw: str) -> tuple[str, bool]:
    """Map what the model said onto the four labels.

    Returns the label and whether it actually came from the model. An empty or
    unrecognised answer falls back to "clinical", the label that creates work
    rather than the one that suppresses it, but the caller is told that it fell
    back. Reporting a fallback as though the model had produced it would make an
    unreliable classifier look accurate whenever the fallback happened to be
    right, which is exactly the measurement error this flag exists to prevent.
    """
    lowered = raw.strip().lower()
    if not lowered:
        return "clinical", False
    for domain in DOMAINS:
        if domain in lowered:
            return domain, True
    return "clinical", False


@app.post("/triage", response_model=TriageResult)
def triage(request: TriageRequest) -> TriageResult:
    body = json.dumps(
        {
            "model": MODEL,
            "prompt": PROMPT.format(text=request.text[:1200]),
            "stream": False,
            # Eight tokens was not enough: gemma3:4b emits leading whitespace
            # and newlines before the label, so the response came back empty and
            # every event silently took the fallback.
            "options": {"num_predict": 24, "temperature": 0},
        }
    ).encode("utf-8")

    try:
        call = urllib.request.Request(
            f"{OLLAMA}/api/generate",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(call, timeout=TIMEOUT_SECONDS) as response:
            answer = json.loads(response.read().decode("utf-8"))
        raw = (answer.get("response") or "").strip()
    except Exception as exc:
        # Triage is advisory, so failing it must not stop an event being
        # processed. Degraded means downstream should treat the label as absent
        # rather than as a finding.
        logs.warning("triage failed, returning degraded", error=str(exc), model=MODEL)
        return TriageResult(
            domain="clinical",
            creates_obligation=True,
            model=MODEL,
            degraded=True,
            reason=f"triage unavailable: {exc}",
        )

    domain, from_model = normalise(raw)
    result = TriageResult(
        domain=domain,
        creates_obligation=domain != "none",
        model=MODEL,
        raw=raw[:120],
        degraded=not from_model,
        reason="" if from_model else "model returned no recognisable label; fell back",
    )
    logs.info(
        "event triaged in boundary",
        domain=result.domain,
        creates_obligation=result.creates_obligation,
        event_type=request.event_type,
        model=MODEL,
    )
    return result
