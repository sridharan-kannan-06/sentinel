"""Environment configuration for a department agent.

One image serves all three agents. AGENT_ROLE selects which set of permissions
the process runs under, and each deployment of the image runs as a different
service account. The role and the service account are checked against each other
at boot: a container claiming to be the revenue agent while running as the
clinical identity is a misconfiguration serious enough to refuse to start.
"""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    project_id: str
    region: str
    agent_role: str
    engine_base_url: str
    notifier: str
    notify_from: str
    notify_to: str
    notify_sheet_id: str
    gmail_oauth_secret: str
    git_sha: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        region=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"),
        agent_role=os.environ.get("AGENT_ROLE", ""),
        engine_base_url=os.environ.get("ENGINE_BASE_URL", "").rstrip("/"),
        notifier=os.environ.get("NOTIFIER", "log"),
        notify_from=os.environ.get("NOTIFY_FROM", ""),
        notify_to=os.environ.get("NOTIFY_TO", ""),
        notify_sheet_id=os.environ.get("NOTIFY_SHEET_ID", ""),
        gmail_oauth_secret=os.environ.get("GMAIL_OAUTH_SECRET", "gmail-oauth"),
        git_sha=os.environ.get("GIT_SHA", "unknown"),
    )
