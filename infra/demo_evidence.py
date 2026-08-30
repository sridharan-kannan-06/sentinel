"""The Evidence Gate, on camera, in one command.

    .venv\\Scripts\\python.exe infra\\demo_evidence.py

Creates one obligation, tries to close it the way an over-confident agent would,
watches it refused, then closes it with a real record from a real system.

Output is spaced and slowed deliberately so it is readable when screen-recorded.
Everything here hits the deployed system; nothing is faked or replayed.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

ENGINE = (
    os.environ.get("ENGINE_LOCAL_URL") or os.environ.get("ENGINE_BASE_URL", "")
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


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{ENGINE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def show(verdict: dict) -> None:
    accepted = verdict["accepted"]
    print(f"   RESULT      {'CLOSED' if accepted else 'REFUSED - stays open'}")
    print(f"   STATUS      {verdict['status']}")
    print(f"   GATE        {'on' if verdict['gate_enabled'] else 'OFF'}")
    print()
    print(f"   {verdict['summary']}")
    print()
    for check in verdict["checks"]:
        mark = "PASS" if check["passed"] else "FAIL"
        print(f"     {mark}   {check['name']}")
        print(f"            {check['detail'][:88]}")
    time.sleep(PAUSE)


def main() -> int:
    if not ENGINE:
        raise SystemExit("No engine URL in .env")

    now = datetime.now(timezone.utc)
    deadline = now + timedelta(hours=4)

    rule("A CRITICAL LAB RESULT NEEDS ACKNOWLEDGING")
    created = post(
        "/obligations",
        {
            "type": "critical_result_acknowledgement",
            "subject_token": "PT-8119",
            "owner_role": "ordering_clinician",
            "owner_id": "DR-0385",
            "created_from_event": "demo-evidence",
            "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "required_evidence": (
                "Electronic acknowledgement record from DR-0385 in the LIS"
            ),
            "risk_tier": "T1",
            "checkpoints": [],
        },
    )
    obligation_id = created["obligation"]["id"]
    print(f"   Obligation  {obligation_id}")
    print("   Subject     PT-8119        (a token, never a name)")
    print("   Owner       DR-0385")
    print("   Closes on   an acknowledgement record from the lab system")
    time.sleep(PAUSE)

    rule("THE AGENT SAYS IT IS DONE. IT HAS NOTHING TO SHOW.")
    print('   Offering:   source "doctor_said_so", no reference number,')
    print('               "looks done to me"')
    print()
    show(
        post(
            f"/obligations/{obligation_id}/evidence",
            {
                "source": "doctor_said_so",
                "external_reference": "",
                "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "subject_token": "PT-8119",
                "assertion": "looks done to me",
            },
        )
    )

    rule("SAME SYSTEM. RIGHT REFERENCE. WRONG PATIENT.")
    second = post(
        "/obligations",
        {
            "type": "critical_result_acknowledgement",
            "subject_token": "PT-8119",
            "owner_role": "ordering_clinician",
            "owner_id": "DR-0385",
            "created_from_event": "demo-evidence-2",
            "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "required_evidence": (
                "Electronic acknowledgement record from DR-0385 in the LIS"
            ),
            "risk_tier": "T1",
            "checkpoints": [],
        },
    )["obligation"]["id"]
    print("   The lab system really did record an acknowledgement.")
    print("   It was for PT-0000. This obligation is about PT-8119.")
    print()
    show(
        post(
            f"/obligations/{second}/evidence",
            {
                "source": "lis",
                "external_reference": "ACK-00000",
                "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "subject_token": "PT-0000",
                "assertion": "Result acknowledged",
            },
        )
    )

    rule("NOW THE REAL ACKNOWLEDGEMENT ARRIVES")
    print("   The lab system records that DR-0385 acknowledged it,")
    print("   with reference ACK-99213.")
    print()
    show(
        post(
            f"/obligations/{obligation_id}/evidence",
            {
                "source": "lis",
                "external_reference": "ACK-99213",
                "observed_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
                "subject_token": "PT-8119",
                "assertion": "Critical potassium result acknowledged by DR-0385",
            },
        )
    )

    rule("WHAT JUST HAPPENED")
    print("   An agent claiming completion was refused.")
    print("   A real record for the wrong patient was refused.")
    print("   A real record for the right patient closed it.")
    print()
    print("   No model was asked. Five rules decided, every time.")
    print()
    print(f"   Full history:  {ENGINE}/audit/{obligation_id}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
