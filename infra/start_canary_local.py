"""Start the canary without going through the service's public URL.

infra/start-canary.ps1 is the normal path and posts to the deployed service. It
is blocked while a brand new Cloud Run service's default hostname is still being
provisioned at Google's edge, which has nothing to do with the service being
healthy and can take a while on a project's first deployment.

This script does the same work in-process against the same real backends: it
writes the obligation to the real Firestore database and enqueues real Cloud
Tasks whose target is the deployed service's /wake endpoint. The timers fire on
26, 28 and 30 August, by which point the hostname will be routing. No clock is
simulated either way, which is the only property the canary actually needs.

Requires application default credentials:

    gcloud auth application-default login
"""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "engine"))


def load_env() -> None:
    env_file = REPO_ROOT / ".env"
    if not env_file.exists():
        raise SystemExit("No .env at the repository root. Run infra/deploy-engine.ps1 first.")
    for line in env_file.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())


load_env()

import ledger  # noqa: E402
import timers  # noqa: E402
from config import get_settings  # noqa: E402
from models import Checkpoint, CheckpointKind, ObligationCreate, RiskTier  # noqa: E402


def utc(year: int, month: int, day: int, hour: int, minute: int) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def main() -> int:
    settings = get_settings()
    if not settings.engine_base_url:
        raise SystemExit("ENGINE_BASE_URL is empty in .env. Deploy the engine first.")

    # Checkpoints in the IST working day. A nudge that fires at three in the
    # morning is not a nudge anyone acts on.
    nudge = utc(2026, 8, 26, 5, 30)
    breach = utc(2026, 8, 28, 5, 30)
    escalate = utc(2026, 8, 30, 5, 30)

    payload = ObligationCreate(
        type="insurance_preauthorisation",
        # Hardcoded for this phase. From Phase 1 the token comes out of Sensitive
        # Data Protection at the trust boundary and no caller chooses one.
        subject_token="PT-a94f",
        owner_role="revenue_cycle",
        owner_id="STAFF-billing-01",
        created_from_event="canary:manual-start",
        deadline=breach,
        required_evidence=(
            "Insurer pre-authorisation reference number issued for the subject"
        ),
        risk_tier=RiskTier.T2,
        checkpoints=[
            Checkpoint(kind=CheckpointKind.NUDGE, at=nudge),
            Checkpoint(kind=CheckpointKind.BREACH, at=breach),
            Checkpoint(kind=CheckpointKind.ESCALATE, at=escalate),
        ],
    )

    obligation = ledger.create_obligation(
        payload,
        actor="human:operator",
        reason="Canary opened to establish a continuously running multi-day obligation",
    )
    print(f"Canary obligation id: {obligation.id}")
    print(f"Status:               {obligation.status.value}")
    print(f"Deadline:             {obligation.deadline.isoformat()}")

    task_names = timers.schedule_all(obligation.id, obligation.checkpoints)
    print(f"Timers enqueued:      {len(task_names)}")
    for name in task_names:
        print(f"  {name}")

    started = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    canary_doc = REPO_ROOT / "docs" / "CANARY.md"
    canary_doc.write_text(
        "\n".join(
            [
                "# Canary",
                "",
                "One real obligation, opened once and left running until submission.",
                "Nothing about its timing is simulated: Cloud Tasks fires on real dates",
                "and the elapsed time recorded in the ledger is real elapsed time.",
                "",
                f"- Obligation id: `{obligation.id}`",
                f"- Opened at: {started} UTC",
                f"- Checkpoints: {nudge:%Y-%m-%d %H:%MZ}, {breach:%Y-%m-%d %H:%MZ}, "
                f"{escalate:%Y-%m-%d %H:%MZ}",
                f"- Service: {settings.engine_base_url}",
                "",
                "Inspect it with:",
                "",
                "```powershell",
                f"curl.exe -s {settings.engine_base_url}/obligations/{obligation.id}",
                "```",
                "",
            ]
        ),
        encoding="utf-8",
    )
    print("\nRecorded in docs/CANARY.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
