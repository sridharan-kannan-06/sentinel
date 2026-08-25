"""The evaluation harness.

Two measurements, run separately because they are measurements of different
things.

Detection runs against the deployed system with real Cloud Tasks timers at a
compressed SLA. It answers: of the obligations that passed their deadline, how
many did the system notice without anybody looking, and how long did that take.
Nothing here is simulated; the harness waits in real time.

The ablation runs locally and deterministically. It answers a question the
Evidence Gate exists for: how often is a model wrong about whether something is
done. Both arms see the same corpus. One arm is the five checks in
policy/evidence.yaml; the other is gemini-3.5-flash asked whether the obligation
is discharged, which is what the system falls back to when EVIDENCE_GATE is off.

    python eval/replay.py --detection
    python eval/replay.py --ablation
    python eval/replay.py --all
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "engine"))
sys.path.insert(0, str(REPO_ROOT / "eval"))

for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")

import corpus  # noqa: E402
import evidence as evidence_gate  # noqa: E402
from models import Obligation, ObligationStatus, RiskTier  # noqa: E402

# Some networks refuse to resolve the SERVICE-NUMBER.REGION.run.app hostname.
# Cloud Run answers on both, so local tooling uses whichever one resolves.
ENGINE = (os.environ.get("ENGINE_LOCAL_URL") or os.environ.get("ENGINE_BASE_URL", "")).rstrip("/")

DETECTION_SLA_SECONDS = 90
DETECTION_COUNT = 12
DETECTION_WAIT_SECONDS = 200


def post(path: str, body: dict) -> dict:
    request = urllib.request.Request(
        f"{ENGINE}{path}",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def get(path: str) -> dict:
    with urllib.request.urlopen(f"{ENGINE}{path}", timeout=90) as response:
        return json.loads(response.read().decode("utf-8"))


def run_detection() -> dict:
    """Open obligations with a short SLA and wait, in real time, for them to breach."""
    if not ENGINE:
        raise SystemExit("No engine URL configured")

    print(f"Opening {DETECTION_COUNT} obligations with a {DETECTION_SLA_SECONDS}s SLA")
    opened_at = datetime.now(timezone.utc)
    deadline = opened_at + timedelta(seconds=DETECTION_SLA_SECONDS)
    created: list[dict] = []

    types = list(corpus.QUALIFYING)
    for index in range(DETECTION_COUNT):
        obligation_type = types[index % len(types)]
        required, _, _, _ = corpus.QUALIFYING[obligation_type]
        body = {
            "type": obligation_type,
            "subject_token": corpus.SUBJECTS[index % len(corpus.SUBJECTS)],
            "owner_role": "eval_harness",
            "owner_id": "STAFF-eval",
            "created_from_event": f"eval-detection-{index:02d}",
            "deadline": deadline.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "required_evidence": required,
            "risk_tier": "T1",
            "checkpoints": [
                {
                    "kind": "nudge",
                    "at": (opened_at + timedelta(seconds=DETECTION_SLA_SECONDS // 2)).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    ),
                },
                {"kind": "breach", "at": deadline.strftime("%Y-%m-%dT%H:%M:%SZ")},
            ],
        }
        result = post("/obligations", body)
        created.append(
            {"id": result["obligation"]["id"], "deadline": deadline, "type": obligation_type}
        )
        print(f"  {result['obligation']['id']}  {obligation_type}")

    print(f"\nWaiting {DETECTION_WAIT_SECONDS}s for the deadlines to pass and the timers to fire.")
    print("Nothing is being fast forwarded; this is real elapsed time.")
    for remaining in range(DETECTION_WAIT_SECONDS, 0, -20):
        time.sleep(min(20, remaining))
        print(f"  {remaining}s remaining")

    detected, latencies, undetected = 0, [], []
    for record in created:
        chain = get(f"/audit/{record['id']}")
        breach = next(
            (
                entry
                for entry in chain["history"]
                if entry["action"] == "checkpoint.breach"
            ),
            None,
        )
        if breach:
            detected += 1
            fired = datetime.fromisoformat(breach["at"].replace("Z", "+00:00"))
            latencies.append((fired - record["deadline"]).total_seconds())
        else:
            undetected.append(record["id"])

    return {
        "denominator": len(created),
        "detected": detected,
        "detection_rate": detected / len(created) if created else 0.0,
        "undetected": undetected,
        "median_seconds_to_detection": statistics.median(latencies) if latencies else None,
        "max_seconds_to_detection": max(latencies) if latencies else None,
        "min_seconds_to_detection": min(latencies) if latencies else None,
        "sla_seconds": DETECTION_SLA_SECONDS,
        "obligation_ids": [r["id"] for r in created],
    }


def to_obligation(record: dict) -> Obligation:
    return Obligation(
        id=record["id"],
        type=record["type"],
        subject_token=record["subject_token"],
        owner_role="eval",
        owner_id="STAFF-eval",
        created_from_event="eval",
        deadline=record["deadline"],
        required_evidence=record["required_evidence"],
        risk_tier=RiskTier.T1,
        status=ObligationStatus.PENDING_EVIDENCE,
        version=1,
        created_at=record["created_at"],
        updated_at=record["created_at"],
    )


def ask_model(obligation: Obligation, sample: corpus.Sample, observed: datetime) -> bool:
    """The gate-off path: a model asked whether the obligation is discharged."""
    from google import genai
    from google.genai import types

    client = genai.Client(
        vertexai=True,
        project=os.environ["GOOGLE_CLOUD_PROJECT"],
        location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
    )
    prompt = (
        "You are reviewing whether a hospital obligation has been discharged.\n\n"
        f"Obligation type: {obligation.type}\n"
        f"Subject: {obligation.subject_token}\n"
        f"Required evidence: {obligation.required_evidence}\n"
        f"Obligation opened: {obligation.created_at.isoformat()}\n\n"
        "Evidence offered:\n"
        f"  source: {sample.source}\n"
        f"  external reference: {sample.external_reference or '(none)'}\n"
        f"  observed at: {observed.isoformat()}\n"
        f"  concerns subject: {sample.subject_token}\n"
        f"  states: {sample.assertion}\n\n"
        "Is this obligation discharged? Answer with exactly one word, yes or no."
    )
    response = client.models.generate_content(
        model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        contents=prompt,
        config=types.GenerateContentConfig(max_output_tokens=2048),
    )
    text = ""
    for part in response.candidates[0].content.parts:
        if getattr(part, "text", None):
            text += part.text
    return text.strip().lower().startswith("y")


def run_ablation() -> dict:
    samples = corpus.build()
    now = datetime.now(timezone.utc)
    os.environ["EVIDENCE_GATE"] = "on"

    rows = []
    print(f"Evaluating {len(samples)} evidence samples against both arms.")
    for index, sample in enumerate(samples, start=1):
        record = corpus.as_obligation(sample, now)
        obligation = to_obligation(record)
        observed = corpus.observed_at(sample, now)
        fact = evidence_gate.Evidence(
            source=sample.source,
            external_reference=sample.external_reference or "-",
            observed_at=observed,
            subject_token=sample.subject_token,
            assertion=sample.assertion,
        )
        # The gate is deterministic, so this is the same answer every run.
        verdict = evidence_gate.evaluate(obligation, fact, now=now)
        gate_closes = verdict.accepted

        try:
            model_closes = ask_model(obligation, sample, observed)
        except Exception as exc:
            print(f"  [{index:2}] model call failed: {exc}")
            model_closes = None

        rows.append(
            {
                "type": sample.obligation_type,
                "category": sample.category,
                "should_close": sample.should_close,
                "gate_closes": gate_closes,
                "model_closes": model_closes,
                "why": sample.why,
            }
        )
        mark = "ok " if gate_closes == sample.should_close else "GATE WRONG"
        print(
            f"  [{index:2}] {sample.category:26} truth={str(sample.should_close):5} "
            f"gate={str(gate_closes):5} model={str(model_closes):5} {mark}"
        )

    def rate(key: str, over: list[dict]) -> float | None:
        usable = [r for r in over if r[key] is not None]
        if not usable:
            return None
        return sum(1 for r in usable if r[key]) / len(usable)

    should_not = [r for r in rows if not r["should_close"]]
    should = [r for r in rows if r["should_close"]]

    return {
        "samples": len(rows),
        "should_close": len(should),
        "should_not_close": len(should_not),
        "false_closure_rate_gate_on": rate("gate_closes", should_not),
        "false_closure_rate_gate_off": rate("model_closes", should_not),
        "true_closure_rate_gate_on": rate("gate_closes", should),
        "true_closure_rate_gate_off": rate("model_closes", should),
        "rows": rows,
    }


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()
    except Exception:
        return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detection", action="store_true")
    parser.add_argument("--ablation", action="store_true")
    parser.add_argument("--all", action="store_true")
    args = parser.parse_args()
    if not (args.detection or args.ablation or args.all):
        parser.print_help()
        return 2

    results: dict = {
        "run_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "git_sha": git_sha(),
        "engine": ENGINE,
        "model": os.environ.get("GEMINI_MODEL"),
    }

    if args.detection or args.all:
        print("=" * 68)
        print("DETECTION, against the deployed system, in real time")
        print("=" * 68)
        results["detection"] = run_detection()

    if args.ablation or args.all:
        print()
        print("=" * 68)
        print("ABLATION, deterministic gate against model judgement")
        print("=" * 68)
        results["ablation"] = run_ablation()

    out = REPO_ROOT / "eval" / "results.json"
    out.write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
