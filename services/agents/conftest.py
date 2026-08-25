"""Make the shared modules importable when running the tests from a checkout.

In the deployed image infra/deploy.ps1 stages policy.py, logs.py, and notify.py
into the same directory as this service. In the repository they live in
services/engine, so tests add that directory to the path to reproduce the
layout the container actually has.
"""

import sys
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE) not in sys.path:
    sys.path.append(str(ENGINE))
