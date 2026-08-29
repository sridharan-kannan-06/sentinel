"""Optional first pass on the in-boundary Gemma classifier.

Off unless TRIAGE_URL is set, and off in the deployed configuration. The reason
is measured rather than assumed: gemma3:4b on Cloud Run CPU takes about 44
seconds per classification, and the trust boundary cannot block for 44 seconds
per event. The measurement and the decision are in docs/DEFERRED.md.

The code path is real and works. What it is not is load-bearing, and the shape of
this module is what makes that true: the label is attached to the event and
nothing reads it to decide anything. An obligation is created by the interpreter
and admitted by the ledger whether this ran, timed out, or was never configured.
"""

from __future__ import annotations

import json
import urllib.request

import logs
from config import get_settings


def classify(text: str, event_type: str, trace_id: str | None = None) -> dict | None:
    """Label one de-identified event. Returns None when triage is not in use."""
    settings = get_settings()
    if not settings.triage_url:
        return None

    body = json.dumps({"text": text, "event_type": event_type}).encode("utf-8")
    request = urllib.request.Request(
        f"{settings.triage_url}/triage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=settings.triage_timeout) as response:
            result = json.loads(response.read().decode("utf-8"))
    except Exception as exc:
        # A slow or missing classifier must never hold up the boundary. The event
        # goes on without a label, which is the same state as triage being off.
        logs.warning(
            "in-boundary triage did not answer in time, continuing without a label",
            error=str(exc),
            event_type=event_type,
            trace_id=trace_id,
        )
        return None

    logs.info(
        "event triaged in boundary",
        domain=result.get("domain"),
        creates_obligation=result.get("creates_obligation"),
        degraded=result.get("degraded"),
        model=result.get("model"),
        event_type=event_type,
        trace_id=trace_id,
    )
    return {
        "domain": result.get("domain"),
        "creates_obligation": result.get("creates_obligation"),
        "model": result.get("model"),
        "degraded": result.get("degraded", False),
    }
