"""Environment configuration for the engine service."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    project_id: str
    project_number: str
    region: str
    gemini_location: str
    gemini_model: str
    tasks_queue: str
    tasks_service_account: str
    engine_base_url: str
    evidence_gate: str
    git_sha: str
    notifier: str
    notify_from: str
    notify_to: str
    notify_sheet_id: str
    gmail_oauth_secret: str
    agent_clin_url: str
    agent_rev_url: str
    agent_path_url: str

    @property
    def evidence_gate_enabled(self) -> bool:
        return self.evidence_gate.strip().lower() != "off"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        project_number=os.environ.get("GOOGLE_CLOUD_PROJECT_NUMBER", ""),
        region=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"),
        # Gemini 3.5 is served only from the global endpoint. Defaulting this to
        # the region is the single most likely way to get a 404 that looks like a
        # bad model name.
        gemini_location=os.environ.get("GOOGLE_CLOUD_LOCATION", "global"),
        gemini_model=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash"),
        tasks_queue=os.environ.get("TASKS_QUEUE", "sentinel-obligations"),
        tasks_service_account=os.environ.get("TASKS_SERVICE_ACCOUNT", ""),
        engine_base_url=os.environ.get("ENGINE_BASE_URL", "").rstrip("/"),
        evidence_gate=os.environ.get("EVIDENCE_GATE", "on"),
        git_sha=os.environ.get("GIT_SHA", "unknown"),
        # Google Chat incoming webhooks need a Workspace account, so the
        # delivery channel here is email or a shared sheet. See notify.py.
        notifier=os.environ.get("NOTIFIER", "log"),
        notify_from=os.environ.get("NOTIFY_FROM", ""),
        notify_to=os.environ.get("NOTIFY_TO", ""),
        notify_sheet_id=os.environ.get("NOTIFY_SHEET_ID", ""),
        gmail_oauth_secret=os.environ.get("GMAIL_OAUTH_SECRET", "gmail-oauth"),
        # One image is deployed three times, so the fleet is three URLs
        # rather than one service with a role parameter.
        agent_clin_url=os.environ.get("AGENT_CLIN_URL", "").rstrip("/"),
        agent_rev_url=os.environ.get("AGENT_REV_URL", "").rstrip("/"),
        agent_path_url=os.environ.get("AGENT_PATH_URL", "").rstrip("/"),
    )
