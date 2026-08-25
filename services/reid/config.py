"""Environment configuration for the re-identification service."""

import os
from dataclasses import dataclass
from functools import lru_cache


@dataclass(frozen=True)
class Settings:
    project_id: str
    region: str
    kms_key: str
    wrapped_key_secret: str
    git_sha: str


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings(
        project_id=os.environ.get("GOOGLE_CLOUD_PROJECT", ""),
        region=os.environ.get("GOOGLE_CLOUD_REGION", "us-central1"),
        kms_key=os.environ.get("KMS_KEY", ""),
        wrapped_key_secret=os.environ.get("WRAPPED_KEY_SECRET", "phi-wrapped-key"),
        git_sha=os.environ.get("GIT_SHA", "unknown"),
    )
