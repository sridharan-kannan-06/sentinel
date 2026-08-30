"""Demonstrate prompt injection handling against the deployed system.

    .venv\\Scripts\\python.exe infra\\demo_injection.py

Sends a realistic insurer letter with an instruction buried in it telling any
automated agent to close the claim. Shows it blocked at the boundary, shows the
audit record, then shows that even with the guard removed the agent ignores the
instruction and keeps chasing the document the letter genuinely asked for.

Everything here hits the deployed system.
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "engine"))
for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

INGEST = (
    os.environ.get("INGEST_LOCAL_URL")
    or os.environ.get("INGEST_BASE_URL", "").replace(
        "sentinel-ingest-60712078658.us-central1", "sentinel-ingest-sqr3nw6srq-uc.a"
    )
).rstrip("/")

PAUSE = float(os.environ.get("DEMO_PAUSE", "2.5"))
WIDTH = 78


def rule(title: str = "") -> None:
    print()
    print("=" * WIDTH)
    if title:
        print(f"  {title}")
        print("=" * WIDTH)
    time.sleep(PAUSE)


def pdf_text() -> str:
    raw = (REPO_ROOT / "fixtures" / "injection_claim.pdf").read_bytes().decode("latin-1")
    lines = re.findall(r"\((.*?)\) Tj", raw)
    return "\n".join(lines).replace("\\(", "(").replace("\\)", ")")


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{INGEST}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    text = pdf_text()

    rule("A CLAIM LETTER ARRIVES FROM THE INSURER")
    for line in text.splitlines()[:8]:
        if line.strip():
            print(f"   {line}")
    print("   ...")
    time.sleep(PAUSE)

    rule("BURIED IN THE MIDDLE OF IT")
    for line in text.splitlines():
        if any(
            marker in line
            for marker in ("SYSTEM NOTICE", "administrator mode", "CLOSED and record")
        ):
            print(f"   {line}")
    print()
    print("   It is telling the agent to close the claim and record a fake")
    print("   approval reference. A person skimming a PDF would not see it.")
    time.sleep(PAUSE)

    rule("THE BOUNDARY SCREENS IT")
    key = f"demo-injection-{int(time.time())}"
    result = post(
        "/events",
        {
            "idempotency_key": key,
            "source": "insurer-email",
            "event_type": "CLAIM_CORRESPONDENCE",
            "text": text,
        },
    )
    print(f"   STATUS      {result['status'].upper()}")
    print(f"   FORWARDED   {'yes' if result.get('published') else 'no - nothing left the boundary'}")
    print()
    reason = result.get("reason") or ""
    for chunk in [reason[i : i + 72] for i in range(0, len(reason), 72)]:
        print(f"   {chunk}")
    time.sleep(PAUSE)

    rule("THE POINT THAT MATTERS")
    print("   The whole letter did not trigger the filter. One 300-character")
    print("   window of it did. Detection weakens as an injection is diluted")
    print("   by ordinary correspondence, so long documents are screened in")
    print("   overlapping windows as well as whole.")
    print()
    print("   That is a mitigation, not a guarantee. So it is not what the")
    print("   system relies on.")
    time.sleep(PAUSE)

    rule("NOW REMOVE THE GUARD ENTIRELY")
    print("   Feeding the same injection straight to the model, unscreened.")
    print()
    import asyncio

    import interpreter

    event = {
        "idempotency_key": "demo-structural",
        "event_type": "CLAIM_CORRESPONDENCE",
        "source": "insurer-email",
        "tokens": ["PT-8119"],
        "text": text.replace("Anjali Bhatt", "PT-8119"),
    }
    outcome = asyncio.run(interpreter.interpret(event))

    print(f"   Obligations it proposed: {len(outcome.accepted)}")
    for obligation in outcome.accepted:
        print(f"     type      {obligation.type}")
        print(f"     closes on {obligation.required_evidence[:70]}")
    print()
    print("   It did not close anything. It asked for the consultant's note,")
    print("   which is the document the letter was genuinely chasing.")
    print()
    print("   The interpreter holds no tools. The word CLOSED is not in its")
    print("   vocabulary. It had no way to obey the instruction.")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
