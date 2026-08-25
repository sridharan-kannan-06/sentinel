"""Remove test obligations, their history, their timers, and test events.

The canary is protected. It has been open since 24 August and cannot be
recreated, so it is excluded by id and the script refuses to run if it cannot
work out which obligation that is.

    python infra/cleanup_test_data.py            list what would be removed
    python infra/cleanup_test_data.py --apply    remove it
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "engine"))

for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

from google.cloud import firestore, tasks_v2  # noqa: E402

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
REGION = os.environ["GOOGLE_CLOUD_REGION"]
QUEUE = os.environ["TASKS_QUEUE"]


def canary_id() -> str:
    """Read the canary's id from the record written when it was opened."""
    doc = REPO_ROOT / "docs" / "CANARY.md"
    if not doc.exists():
        raise SystemExit(
            "docs/CANARY.md is missing, so the canary cannot be identified. "
            "Refusing to delete anything."
        )
    match = re.search(r"Obligation id: `(OBL-[a-z0-9]+)`", doc.read_text(encoding="utf-8"))
    if not match:
        raise SystemExit("No obligation id in docs/CANARY.md. Refusing to delete anything.")
    return match.group(1)


def main() -> int:
    apply = "--apply" in sys.argv
    keep = canary_id()
    db = firestore.Client(project=PROJECT)

    doomed: list[str] = []
    for snapshot in db.collection("obligations").stream():
        record = snapshot.to_dict() or {}
        if snapshot.id == keep:
            print(f"KEEP    {snapshot.id}  {record.get('status')}  canary")
            continue
        doomed.append(snapshot.id)
        print(f"DELETE  {snapshot.id}  {record.get('status')}  {record.get('type')}")

    events = [s.id for s in db.collection("events").stream()]
    for event_id in events:
        print(f"DELETE  event {event_id}")

    client = tasks_v2.CloudTasksClient()
    parent = client.queue_path(PROJECT, REGION, QUEUE)
    doomed_tasks = [
        task.name
        for task in client.list_tasks(parent=parent)
        if any(task.name.rsplit("/", 1)[-1].startswith(o) for o in doomed)
    ]
    for name in doomed_tasks:
        print(f"DELETE  task {name.rsplit('/', 1)[-1]}")

    if not apply:
        print("\nNothing was removed. Re-run with --apply to carry this out.")
        return 0

    for name in doomed_tasks:
        try:
            client.delete_task(name=name)
        except Exception as exc:
            print(f"  could not delete task {name.rsplit('/', 1)[-1]}: {exc}")

    for obligation_id in doomed:
        ref = db.collection("obligations").document(obligation_id)
        # The ledger is a subcollection and is not removed by deleting the
        # parent document, so it has to go first or it is orphaned.
        for entry in ref.collection("ledger_entries").stream():
            entry.reference.delete()
        ref.delete()

    for event_id in events:
        db.collection("events").document(event_id).delete()

    print(
        f"\nRemoved {len(doomed)} obligations, {len(events)} events, "
        f"{len(doomed_tasks)} timers. Canary {keep} untouched."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
