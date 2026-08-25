"""Re-identification.

The only code in Sentinel that can turn a token back into a name. It is a
separate service, running as a separate identity, and it is the one service
deployed closed to the internet: every other service is public and holds nothing
worth reading, while this one is private and holds the only reversal path.

Three properties matter here.

It cannot be reached anonymously. Cloud Run IAM permits only the Continuity
Board's identity to invoke it, so a token leaked from a log is not enough.

Every call is recorded before the answer is produced, with who asked, which
token, and when. A read that fails to log is a read that did not happen.

It resolves one token at a time and never lists. There is no endpoint that walks
the alias map, because a service that can enumerate every patient is a breach
waiting for one mistake.
"""

from __future__ import annotations

import base64
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from functools import lru_cache

from fastapi import FastAPI, HTTPException, Request
from google.cloud import dlp_v2, firestore, secretmanager
from pydantic import BaseModel, Field

import logs
from config import get_settings

TOKEN_ALIASES = "token_aliases"
ACCESS_LOG = "reidentification_access_log"

_firestore: firestore.Client | None = None


def db() -> firestore.Client:
    global _firestore
    if _firestore is None:
        _firestore = firestore.Client(project=get_settings().project_id)
    return _firestore


@lru_cache(maxsize=1)
def _dlp() -> dlp_v2.DlpServiceClient:
    return dlp_v2.DlpServiceClient()


@lru_cache(maxsize=1)
def _wrapped_key() -> bytes:
    settings = get_settings()
    client = secretmanager.SecretManagerServiceClient()
    name = (
        f"projects/{settings.project_id}/secrets/"
        f"{settings.wrapped_key_secret}/versions/latest"
    )
    payload = client.access_secret_version(name=name).payload.data.decode("utf-8").strip()
    return base64.b64decode(payload)


class ReidentifyRequest(BaseModel):
    token: str = Field(min_length=3, description="An alias such as PT-8119")
    reason: str = Field(min_length=1, description="Why this needs a name attached")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logs.info(
        "reid starting",
        service="sentinel-reid",
        git_sha=settings.git_sha,
        note="the only service permitted to reverse a token",
    )
    yield


app = FastAPI(title="sentinel-reid", lifespan=lifespan)


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {"service": "sentinel-reid", "git_sha": settings.git_sha}


def caller_from(request: Request) -> str:
    """Identify the human behind the request.

    Cloud Run sets these headers when the caller is authenticated through IAP or
    an identity token. Falling back to the raw authorisation subject rather than
    to anonymous is deliberate: an unattributable read should look wrong in the
    log rather than blend in.
    """
    for header in (
        "X-Goog-Authenticated-User-Email",
        "X-Endpoint-API-UserInfo",
        "X-Sentinel-Operator",
    ):
        value = request.headers.get(header)
        if value:
            return value
    return "unattributed"


def _record_access(
    token: str, caller: str, reason: str, outcome: str, trace_id: str | None
) -> None:
    db().collection(ACCESS_LOG).document().set(
        {
            "token": token,
            "caller": caller,
            "reason": reason,
            "outcome": outcome,
            "at": datetime.now(timezone.utc),
            "trace_id": trace_id,
        }
    )
    logs.warning(
        "re-identification requested",
        token=token,
        caller=caller,
        reason=reason,
        outcome=outcome,
        trace_id=trace_id,
    )


def _reidentify(surrogate: str, kind: str) -> str:
    settings = get_settings()
    crypto_key = {
        "kms_wrapped": {"wrapped_key": _wrapped_key(), "crypto_key_name": settings.kms_key}
    }
    response = _dlp().reidentify_content(
        request={
            "parent": f"projects/{settings.project_id}/locations/{settings.region}",
            "reidentify_config": {
                "info_type_transformations": {
                    "transformations": [
                        {
                            "info_types": [{"name": kind}],
                            "primitive_transformation": {
                                "crypto_deterministic_config": {
                                    "crypto_key": crypto_key,
                                    "surrogate_info_type": {"name": kind},
                                }
                            },
                        }
                    ]
                }
            },
            "inspect_config": {
                "custom_info_types": [
                    {"info_type": {"name": kind}, "surrogate_type": {}}
                ]
            },
            "item": {"value": surrogate},
        }
    )
    return response.item.value


@app.post("/reidentify")
def reidentify(payload: ReidentifyRequest, request: Request) -> dict:
    trace_id = (request.headers.get("X-Cloud-Trace-Context") or "").split("/")[0] or None
    caller = caller_from(request)
    token = payload.token.strip()

    snapshot = db().collection(TOKEN_ALIASES).document(token).get()
    if not snapshot.exists:
        # Logged before answering, so a probe for tokens that do not exist is on
        # the record too.
        _record_access(token, caller, payload.reason, "unknown_token", trace_id)
        raise HTTPException(status_code=404, detail="token not found")

    record = snapshot.to_dict() or {}
    surrogate = record.get("surrogate", "")
    kind = record.get("kind") or token.split("-", 1)[0]

    try:
        name = _reidentify(surrogate, kind)
    except Exception as exc:
        _record_access(token, caller, payload.reason, "failed", trace_id)
        logs.error("re-identification failed", token=token, error=str(exc), trace_id=trace_id)
        raise HTTPException(status_code=502, detail="re-identification failed") from exc

    _record_access(token, caller, payload.reason, "resolved", trace_id)
    return {
        "token": token,
        "identity": name,
        "kind": kind,
        "first_seen": record.get("first_seen"),
        "last_seen": record.get("last_seen"),
        "caller": caller,
        "note": "This read has been recorded in the re-identification access log.",
    }


@app.get("/access-log")
def access_log(limit: int = 50) -> dict:
    """Who has resolved which tokens.

    Deliberately readable. The point of logging every reversal is that somebody
    can look at the list.
    """
    query = (
        db()
        .collection(ACCESS_LOG)
        .order_by("at", direction=firestore.Query.DESCENDING)
        .limit(limit)
    )
    entries = []
    for document in query.stream():
        record = document.to_dict() or {}
        entries.append(
            {
                "token": record.get("token"),
                "caller": record.get("caller"),
                "reason": record.get("reason"),
                "outcome": record.get("outcome"),
                "at": record.get("at").isoformat() if record.get("at") else None,
            }
        )
    return {"count": len(entries), "entries": entries}
