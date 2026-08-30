"""Close the long-running obligation, on camera, in one command.

    .venv\\Scripts\\python.exe infra\\demo_close_canary.py              dry run
    .venv\\Scripts\\python.exe infra\\demo_close_canary.py --live       refusal only
    .venv\\Scripts\\python.exe infra\\demo_close_canary.py --live --close

Offers unqualified evidence first and watches it refused, then offers the real
insurer reference and watches it close. Doing this against the obligation that
has actually been chasing itself for six days is worth more than doing it against
one created thirty seconds earlier.

Nothing is written without --live, so the script is safe to run while rehearsing.
That matters more than usual here: submitting any evidence at all moves the
obligation out of BREACHED and into PENDING_EVIDENCE, and BREACHED after six days
is the thing worth filming. --close is separate again, because CLOSED is terminal
and there is no second take.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
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

CANARY = "OBL-a2ec59ecaf"
PAUSE = float(os.environ.get("DEMO_PAUSE", "3"))
WIDTH = 78

# Refused: a phone call leaves nothing anybody can audit later.
UNQUALIFIED = {
    "source": "verbal_report",
    "external_reference": "",
    "assertion": "Confirmed complete with the insurer over the phone",
}

# Accepted: the insurer's own portal, with the reference it issued.
QUALIFIED = {
    "source": "insurer_portal",
    "external_reference": "PA-2026-118422",
    "assertion": "Pre-authorisation approved, reference issued by the insurer",
}


def rule(title: str = "") -> None:
    print()
    print("=" * WIDTH)
    if title:
        print(f"  {title}")
        print("=" * WIDTH)
    time.sleep(PAUSE)


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{ENGINE}{path}", timeout=60) as response:
        return json.loads(response.read().decode("utf-8"))


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{ENGINE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=90) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return json.loads(exc.read().decode("utf-8"))


def offer(obligation_id: str, subject: str, evidence: dict) -> dict:
    body = dict(evidence)
    body["subject_token"] = subject
    # Computed here rather than typed. A hardcoded timestamp drifts out of the
    # acceptance window and fails a check the demonstration is not about.
    body["observed_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    print(f"   source      {body['source']}")
    print(f"   reference   {body['external_reference'] or '(none)'}")
    print(f"   states      {body['assertion']}")
    print()
    return post(f"/obligations/{obligation_id}/evidence", body)


def show(verdict: dict) -> None:
    if "checks" not in verdict:
        print(f"   {verdict.get('detail', verdict)}")
        time.sleep(PAUSE)
        return
    print(f"   RESULT      {'CLOSED' if verdict['accepted'] else 'REFUSED - stays open'}")
    print(f"   STATUS      {verdict['status']}")
    print()
    print(f"   {verdict['summary']}")
    print()
    for check in verdict["checks"]:
        print(f"     {'PASS' if check['passed'] else 'FAIL'}   {check['name']}")
        print(f"            {check['detail'][:86]}")
    time.sleep(PAUSE)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live", action="store_true", help="actually submit evidence")
    parser.add_argument("--close", action="store_true", help="also submit the qualifying evidence")
    parser.add_argument("--obligation", default=CANARY, help="target, for rehearsing on a throwaway")
    args = parser.parse_args()

    if not ENGINE:
        raise SystemExit("No engine URL in .env")

    chain = get(f"/audit/{args.obligation}")
    obligation = chain["obligation"]
    opened = datetime.fromisoformat(obligation["created_at"].replace("Z", "+00:00"))
    days = (datetime.now(timezone.utc) - opened).total_seconds() / 86400
    emails = len(chain["notifications"])

    rule("THE OBLIGATION THAT HAS BEEN CHASING ITSELF SINCE THE 24TH")
    print(f"   {obligation['id']}")
    print(f"   Subject     {obligation['subject_token']}        a token, never a name")
    print(f"   Status      {obligation['status']}")
    print(f"   Open for    {days:.1f} days")
    print(f"   Emails sent {emails}, without anybody asking")
    print(f"   Closes on   {obligation['required_evidence']}")
    time.sleep(PAUSE)

    if not args.live:
        rule("DRY RUN - NOTHING WAS SUBMITTED")
        print("   This is what would be offered, in order:")
        print()
        print(f"   1. REFUSED   {UNQUALIFIED['source']}, no reference,")
        print(f"                \"{UNQUALIFIED['assertion']}\"")
        print()
        print(f"   2. ACCEPTED  {QUALIFIED['source']}, {QUALIFIED['external_reference']},")
        print(f"                \"{QUALIFIED['assertion']}\"")
        print()
        print("   Add --live to submit the first. Add --close for both.")
        print()
        print("   Note: submitting anything moves this out of BREACHED and into")
        print("   PENDING_EVIDENCE, and CLOSED is terminal. There is no second take.")
        print()
        return 0

    rule("SOMEBODY SAYS IT IS SORTED")
    verdict = offer(args.obligation, obligation["subject_token"], UNQUALIFIED)
    show(verdict)

    if not args.close:
        rule("STILL OPEN")
        print("   Six days of chasing, and a phone call does not close it.")
        print()
        print("   Add --close to offer the insurer's own reference.")
        print()
        return 0

    rule("THE INSURER ISSUES THE REFERENCE")
    verdict = offer(args.obligation, obligation["subject_token"], QUALIFIED)
    show(verdict)

    rule("CLOSED, ON THE ONLY THING THAT COULD HAVE CLOSED IT")
    final = get(f"/audit/{args.obligation}")
    print(f"   Status      {final['obligation']['status']}")
    print(f"   Open for    {days:.1f} days, start to finish")
    print(f"   History     {final['counts']['history']} entries, every one of them recorded")
    print()
    print(f"   Full chain: {ENGINE}/audit/{args.obligation}")
    print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
