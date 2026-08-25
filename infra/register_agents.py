"""Publish the fleet to Agent Registry as versioned A2A agent cards.

The Fortified Enterprise Fleet track asks how agents are catalogued for
cross-department use. This is the answer: each department agent is registered as
a Service carrying an A2A agent card, discoverable and versioned.

The skills on each card are generated from policy.yaml rather than written by
hand. A catalogue that advertises something the policy would refuse is worse than
no catalogue, because it invites another team to build against a capability that
does not exist. Here the advertised skills and the enforced allow list are the
same list.

    python infra/register_agents.py            show what would be published
    python infra/register_agents.py --apply    publish it
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "services" / "engine"))

for line in (REPO_ROOT / ".env").read_text(encoding="utf-8").splitlines():
    line = line.strip()
    if line and "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip())

import policy  # noqa: E402

PROJECT = os.environ["GOOGLE_CLOUD_PROJECT"]
LOCATION = os.environ.get("AGENT_REGISTRY_LOCATION", "us-central1")
BASE = "https://agentregistry.googleapis.com/v1"
PARENT = f"projects/{PROJECT}/locations/{LOCATION}"

# The registry id has to be stable across republishes or every deploy creates a
# duplicate entry rather than updating the existing one.
AGENTS = {
    "clinical-followup": ("clinical_followup", "AGENT_CLIN_URL"),
    "revenue-cycle": ("revenue_cycle", "AGENT_REV_URL"),
    "care-pathway": ("care_pathway", "AGENT_PATH_URL"),
}


def access_token() -> str:
    return subprocess.run(
        ["gcloud", "auth", "print-access-token"],
        capture_output=True,
        text=True,
        check=True,
        shell=True,
    ).stdout.strip()


def git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "-C", str(REPO_ROOT), "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    except subprocess.CalledProcessError:
        return "unknown"


def agent_card(service_id: str, role: str, url: str) -> dict:
    declared, digest = policy.load_policy()
    description = (declared.get("agents") or {}).get(role, {}).get("description", "")
    actions = sorted(policy.allowed_actions(role))

    return {
        "protocolVersion": "0.3.0",
        "name": service_id,
        "description": description,
        "url": f"{url}/act",
        # The card version tracks the policy that defines the agent's authority,
        # so a change in permissions is a change in published version.
        "version": f"{declared.get('version')}.0.0+{git_sha()}",
        "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["application/json"],
        "defaultOutputModes": ["application/json"],
        "skills": [
            {
                "id": action,
                "name": action.replace("_", " "),
                "description": (
                    f"Permitted for {role} under Sentinel policy version "
                    f"{declared.get('version')} (hash {digest})."
                ),
                "tags": [role, "sentinel"],
            }
            for action in actions
        ],
    }


def request(method: str, path: str, body: dict | None = None) -> tuple[int, str]:
    url = f"{BASE}/{path}" if not path.startswith("http") else path
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token()}")
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Goog-User-Project", PROJECT)
    try:
        with urllib.request.urlopen(req, timeout=60) as response:
            return response.status, response.read().decode("utf-8")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8")
    except urllib.error.URLError as exc:
        return 0, str(exc)


def main() -> int:
    apply = "--apply" in sys.argv
    published = 0

    for service_id, (role, url_var) in AGENTS.items():
        url = os.environ.get(url_var, "")
        if not url:
            print(f"SKIP  {service_id}: {url_var} is not set in .env")
            continue

        card = agent_card(service_id, role, url)
        # interfaces must be empty for an A2A_AGENT_CARD. The card carries its
        # own url, and supplying both is rejected as an invalid argument naming
        # only the field, with no explanation.
        body = {
            "displayName": f"Sentinel {role.replace('_', ' ').title()}",
            "description": card["description"],
            "agentSpec": {"type": "A2A_AGENT_CARD", "content": card},
        }

        print(f"\n=== {service_id} ({role}) ===")
        print(f"  url     : {card['url']}")
        print(f"  version : {card['version']}")
        print(f"  skills  : {', '.join(s['id'] for s in card['skills'])}")

        if not apply:
            continue

        status, text = request("POST", f"{PARENT}/services?serviceId={service_id}", body)
        if status in (200, 201):
            print("  published")
            published += 1
        elif status == 409:
            # Already registered, so this is a version bump rather than a new entry.
            status, text = request(
                "PATCH", f"{PARENT}/services/{service_id}", body
            )
            print(f"  updated (HTTP {status})")
            if status in (200, 201):
                published += 1
            else:
                print(f"  {text[:400]}")
        else:
            print(f"  FAILED HTTP {status}")
            print(f"  {text[:600]}")

    if not apply:
        print("\nNothing was published. Re-run with --apply.")
        return 0

    print(f"\nPublished or updated {published} of {len(AGENTS)} agents.")
    status, text = request("GET", f"{PARENT}/services")
    print(f"\nRegistry now lists (HTTP {status}):")
    try:
        for service in json.loads(text).get("services", []):
            print(f"  {service.get('name')}  {service.get('displayName')}")
    except ValueError:
        print(text[:400])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
