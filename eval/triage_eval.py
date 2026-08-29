"""Measure the in-boundary Gemma triage classifier.

The twelve fixture events in their de-identified form, plus four messages that a
hospital ops channel actually carries and that must not create work. Sixteen
labelled items is a small set and is stated as such.

The label this produces is advisory. This measurement exists to decide whether it
is worth recording at all, not to decide whether an obligation is created.

    python eval/triage_eval.py
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from collections import Counter
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

TRIAGE = (
    os.environ.get("TRIAGE_LOCAL_URL") or os.environ.get("TRIAGE_BASE_URL", "")
).rstrip("/")

# The fixture events as they look after the trust boundary has replaced every
# identifier. This is exactly what triage sees in production.
LABELLED: list[tuple[str, str, str]] = [
    ("clinical", "CRITICAL_LAB_RESULT",
     "Critical potassium 6.8 mmol/L for PT-8119, MRN-747d. Result released 40 minutes "
     "ago. Ordering clinician DR-0385 has not opened the result."),
    ("clinical", "CRITICAL_LAB_RESULT",
     "Troponin I 2.41 ng/mL for PT-2b60, MRN-91ac, admitted under DR-7712 in "
     "cardiology. Flagged critical by the analyser. No acknowledgement recorded."),
    ("clinical", "CRITICAL_LAB_RESULT",
     "Creatinine 5.9 mg/dL for PT-4c72, a rise from 2.1 yesterday. DR-0385 was paged; "
     "nothing has come back."),
    ("clinical", "CRITICAL_LAB_RESULT",
     "Haemoglobin 5.8 g/dL for PT-91de under DR-3c11. Repeat sample confirms. Ward has "
     "not been called back."),
    ("revenue", "ADMISSION_REQUIRING_PREAUTH",
     "PT-a94f admitted for elective caesarean under DR-7712. Insurer policy needs "
     "pre-authorisation before the procedure. Nothing submitted yet."),
    ("revenue", "ADMISSION_REQUIRING_PREAUTH",
     "PT-6d18 listed for total knee replacement by DR-55f0 on Thursday. TPA "
     "pre-authorisation not raised. Surgery is in three days."),
    ("revenue", "INSURER_QUERY",
     "From the TPA desk regarding PT-88b1: we cannot process the pre-authorisation "
     "without the treating consultant's clinical justification note."),
    ("revenue", "CLAIM_DOCUMENT_REQUEST",
     "Claim for PT-c204 is on hold. The discharge summary and the implant sticker "
     "sheet from DR-55f0's procedure are both missing from the submission."),
    ("revenue", "CLAIM_DOCUMENT_REQUEST",
     "PT-1f39 discharged four days ago under DR-7712. The final bill has not been "
     "uploaded to the insurer portal and the claim window closes in ten days."),
    ("pathway", "DISCHARGE_INITIATED",
     "Discharge initiated for PT-70ae by DR-0385. Pharmacy reconciliation is still "
     "open and the take-home prescription has not been signed."),
    ("pathway", "DISCHARGE_INITIATED",
     "PT-b3c6 is medically fit for discharge per DR-3c11. Physiotherapy sign-off and "
     "the dietician note are both outstanding and the bed is needed."),
    ("pathway", "REFERRAL_CREATED",
     "DR-55f0 referred PT-e901 to the spine clinic at the tertiary centre. The "
     "referral went out on Monday and no acknowledgement has come back."),
    ("none", "FACILITIES_NOTICE",
     "The staff cafeteria will close at 4pm on Friday for maintenance."),
    ("none", "ROSTER_NOTICE",
     "Reminder: the revised nursing roster for September is now on the noticeboard."),
    ("none", "IT_NOTICE",
     "Planned network maintenance this Sunday between 2am and 4am. No action needed."),
    ("none", "GENERAL_NOTICE",
     "The annual fire drill will take place next Wednesday morning."),
]


def classify(text: str, event_type: str) -> dict:
    body = json.dumps({"text": text, "event_type": event_type}).encode("utf-8")
    request = urllib.request.Request(
        f"{TRIAGE}/triage",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def main() -> int:
    if not TRIAGE:
        raise SystemExit("No triage URL configured")

    print(f"Triage endpoint: {TRIAGE}")
    with urllib.request.urlopen(f"{TRIAGE}/health", timeout=240) as response:
        health = json.loads(response.read().decode("utf-8"))
    print(f"Model: {health['model']}  loaded: {health['model_loaded']}\n")

    correct = 0
    degraded_count = 0
    confusion: Counter = Counter()
    rows = []

    for expected, event_type, text in LABELLED:
        result = classify(text, event_type)
        got = result["domain"]
        degraded = result.get("degraded", False)
        # A fallback that happens to be right is not the model being right.
        # Counting it would make an unreliable classifier look accurate.
        ok = got == expected and not degraded
        correct += ok
        degraded_count += degraded
        confusion[(expected, got)] += 1
        rows.append(
            {"expected": expected, "got": got, "raw": result.get("raw", ""),
             "degraded": degraded}
        )
        mark = "ok  " if ok else ("FELL BACK" if degraded else "MISS")
        print(
            f"  {mark:9} expected={expected:9} got={got:9} "
            f"raw={result.get('raw','')[:26]!r}"
        )

    total = len(LABELLED)
    print(f"\naccuracy: {correct}/{total} = {correct / total * 100:.1f}%")

    # The dangerous error is calling something "none" that actually needed doing.
    # Everything else is a routing hint that the interpreter overrides anyway.
    missed_work = sum(
        count for (expected, got), count in confusion.items()
        if expected != "none" and got == "none"
    )
    invented_work = sum(
        count for (expected, got), count in confusion.items()
        if expected == "none" and got != "none"
    )
    print(f"real work labelled 'none' (the dangerous error): {missed_work}")
    print(f"noise labelled as work (harmless, costs one Gemini call): {invented_work}")

    (REPO_ROOT / "eval" / "triage_results.json").write_text(
        json.dumps(
            {
                "model": health["model"],
                "samples": total,
                "correct": correct,
                "accuracy": correct / total,
                "degraded": degraded_count,
                "missed_work": missed_work,
                "invented_work": invented_work,
                "rows": rows,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
