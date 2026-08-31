"""Tests for service wiring that differs between the repo and the container image.

The container flattens the service into /app, so anything that walks a fixed
number of parent directories works locally and crashes on Cloud Run. The first
revision of this service failed to boot for exactly that reason.
"""

import shutil
import subprocess
import sys
from pathlib import Path

import main

HERE = Path(__file__).resolve().parent


def test_policy_hash_is_a_hash_or_an_honest_absence() -> None:
    # A missing policy file reports the literal string absent rather than a hash
    # of nothing, so the trust panel can never show a hash that corresponds to no
    # policy at all.
    assert main.policy_config_hash() == "absent" or len(main.policy_config_hash()) == 16


def test_health_reports_the_global_gemini_location() -> None:
    body = main.health()
    assert body["service"] == "sentinel-engine"
    # Regional endpoints do not serve gemini-3.5-flash. If this ever reads
    # us-central1 the interpreter will fail with a confusing 404.
    assert body["gemini_location"] == "global"


def test_module_imports_when_flattened_into_a_single_directory(tmp_path: Path) -> None:
    """Reproduces the container layout that broke revision 00001."""
    for source in HERE.glob("*.py"):
        if source.name.startswith("test_"):
            continue
        shutil.copy(source, tmp_path / source.name)

    result = subprocess.run(
        [sys.executable, "-c", "import main; main.health()"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
