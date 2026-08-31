"""Environment configuration for the ingest service.

Deliberately a separate module from the engine's config rather than a shared
package. Each service builds from its own directory as its own container, and a
shared parent package would mean every image carried every service's code.
"""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    project_id: str
    region: str
    pubsub_topic: str
    model_armor_template: str
    kms_key: str
    wrapped_key_secret: str
    git_sha: str
    triage_url: str
    triage_timeout: float


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        region=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"),
        pubsub_topic=os.environ.get("PUBSUB_TOPIC", "raw-events"),
        model_armor_template=os.environ.get("MODEL_ARMOR_TEMPLATE", "sentinel-boundary"),
        kms_key=os.environ.get("KMS_KEY", ""),
        wrapped_key_secret=os.environ.get("WRAPPED_KEY_SECRET", "phi-wrapped-key"),
        git_sha=os.environ.get("GIT_SHA", "unknown"),
        # In-boundary Gemma triage. Empty means skip it entirely, which is
        # the deployed default: see the README for the measurement
        # that led there.
        triage_url=os.environ.get("TRIAGE_URL", "").rstrip("/"),
        triage_timeout=float(os.environ.get("TRIAGE_TIMEOUT", "8")),
    )
