"""Structured logging.

Cloud Logging parses a single line of JSON on stdout and promotes severity,
message, and trace into first class fields. Every line carries obligation_id and
trace_id where one exists, because the audit story depends on being able to pull
one obligation's whole history out of Logging by filter alone.
"""

import json
import sys
from typing import Any


def log(severity: str, message: str, **fields: Any) -> None:
    entry: dict[str, Any] = {"severity": severity, "message": message}
    for key, value in fields.items():
        if value is not None:
            entry[key] = value
    sys.stdout.write(json.dumps(entry, default=str) + "\n")
    sys.stdout.flush()


def info(message: str, **fields: Any) -> None:
    log("INFO", message, **fields)


def warning(message: str, **fields: Any) -> None:
    log("WARNING", message, **fields)


def error(message: str, **fields: Any) -> None:
    log("ERROR", message, **fields)
