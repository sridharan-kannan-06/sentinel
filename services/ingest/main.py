"""Sentinel ingest: the trust boundary.

Raw hospital text arrives here and de-identified text leaves. Three properties
matter and each is enforced structurally rather than by convention:

Repeat delivery of the same event does not produce a second obligation. The
idempotency key is claimed inside a Firestore transaction, so two concurrent
copies of the same event cannot both proceed.

Content Model Armor rejected is never forwarded. `boundary.process` returns no
de-identified payload at all when screening fails, so there is nothing to
publish even if a caller ignored the flag.

No raw text is persisted anywhere. The stored event document holds only the
de-identified form. The original is held in memory for the length of one
request and then discarded.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from google.cloud import firestore, pubsub_v1
from pydantic import BaseModel, Field

import boundary
import logs
import triage
from config import get_settings

EVENTS = "events"

# Alias to Sensitive Data Protection surrogate. Kept in its own collection so
# that services/reid can look one up by alias without being able to read events,
# and so that IAM can be scoped to this collection alone. The surrogate is
# ciphertext: reversing it still needs the KMS-wrapped key, which only reid and
# ingest can reach.
TOKEN_ALIASES = "token_aliases"

_firestore: firestore.Client | None = None
_publisher: pubsub_v1.PublisherClient | None = None


def db() -> firestore.Client:
    global _firestore
    if _firestore is None:
        _firestore = firestore.Client(project=get_settings().project_id)
    return _firestore


def publisher() -> pubsub_v1.PublisherClient:
    global _publisher
    if _publisher is None:
        _publisher = pubsub_v1.PublisherClient()
    return _publisher


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class RawEvent(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=200)
    source: str
    event_type: str
    text: str
    occurred_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    logs.info(
        "ingest starting",
        service="sentinel-ingest",
        git_sha=settings.git_sha,
        model_armor_template=settings.model_armor_template,
        pubsub_topic=settings.pubsub_topic,
    )
    yield


app = FastAPI(title="sentinel-ingest", lifespan=lifespan)


def trace_id_from(request: Request) -> str | None:
    header = request.headers.get("X-Cloud-Trace-Context")
    if not header:
        return None
    return header.split("/")[0] or None


@app.get("/health")
def health() -> dict:
    settings = get_settings()
    return {
        "service": "sentinel-ingest",
        "git_sha": settings.git_sha,
        "model_armor_template": settings.model_armor_template,
        "pubsub_topic": settings.pubsub_topic,
    }


def _record_aliases(aliases: dict[str, str], idempotency_key: str) -> None:
    """Store alias to surrogate, one document per alias.

    Tokenisation is deterministic, so the same person yields the same alias every
    time and this is an idempotent upsert rather than an append. first_seen is
    written once; last_seen moves.
    """
    now = utcnow()
    for alias, surrogate in aliases.items():
        ref = db().collection(TOKEN_ALIASES).document(alias)
        record = {
            "alias": alias,
            "surrogate": surrogate,
            "kind": alias.split("-", 1)[0],
            "last_seen": now,
            "last_event": idempotency_key,
        }
        # first_seen is written only when the alias is new. Merging it every time
        # would move it forward on each sighting and erase the evidence that this
        # token has meant the same person since the first event.
        if not ref.get().exists:
            record["first_seen"] = now
        ref.set(record, merge=True)


def _claim(key: str, event: RawEvent) -> dict | None:
    """Claim the idempotency key. Returns the existing record if already taken."""
    ref = db().collection(EVENTS).document(key)

    @firestore.transactional
    def claim(transaction: firestore.Transaction) -> dict | None:
        snapshot = ref.get(transaction=transaction)
        if snapshot.exists:
            return snapshot.to_dict()
        transaction.set(
            ref,
            {
                "idempotency_key": key,
                "source": event.source,
                "event_type": event.event_type,
                "status": "processing",
                "received_at": utcnow(),
                "occurred_at": event.occurred_at or utcnow(),
            },
        )
        return None

    return claim(db().transaction())


@app.post("/events")
def ingest_event(event: RawEvent, request: Request) -> dict:
    trace_id = trace_id_from(request)
    key = event.idempotency_key
    ref = db().collection(EVENTS).document(key)

    existing = _claim(key, event)
    if existing is not None:
        logs.info(
            "duplicate event ignored",
            idempotency_key=key,
            event_type=event.event_type,
            original_status=existing.get("status"),
            trace_id=trace_id,
        )
        return {
            "idempotency_key": key,
            "duplicate": True,
            "status": existing.get("status"),
            "tokens": existing.get("tokens", []),
            "published": existing.get("published", False),
        }

    screening, deidentified = boundary.process(event.text)

    if screening.blocked or deidentified is None:
        # The audit record is deliberately legible: it is shown on screen during
        # the injection demonstration.
        ref.update(
            {
                "status": "blocked",
                "blocked": True,
                "block_reason": screening.reason,
                "screening_match_state": screening.match_state,
                "screening_filters": screening.filters,
                "published": False,
                "screened_at": utcnow(),
            }
        )
        logs.warning(
            "event blocked at the trust boundary and not forwarded",
            idempotency_key=key,
            event_type=event.event_type,
            reason=screening.reason,
            filters=screening.filters,
            trace_id=trace_id,
        )
        return {
            "idempotency_key": key,
            "duplicate": False,
            "status": "blocked",
            "reason": screening.reason,
            "filters": screening.filters,
            "published": False,
        }

    # Persist the alias map before publishing. A token that reached an agent but
    # cannot be resolved back for an authorised human is worse than useless: it
    # is an audit trail nobody can read.
    _record_aliases(deidentified.aliases, key)

    settings = get_settings()

    # First pass on Gemma, inside the boundary, on the de-identified text. The
    # label is recorded and never obeyed: an obligation is created by the
    # interpreter and admitted by the ledger regardless of what this says.
    label = triage.classify(deidentified.text, event.event_type, trace_id=trace_id)

    payload = {
        "idempotency_key": key,
        "source": event.source,
        "event_type": event.event_type,
        "text": deidentified.text,
        "tokens": deidentified.tokens,
        "occurred_at": (event.occurred_at or utcnow()).isoformat(),
        "metadata": event.metadata,
        "triage": label,
    }

    topic = publisher().topic_path(settings.project_id, settings.pubsub_topic)
    message_id = publisher().publish(
        topic,
        data=json.dumps(payload).encode("utf-8"),
        event_type=event.event_type,
        idempotency_key=key,
    ).result(timeout=30)

    # Only the de-identified text is written. The raw text is never persisted.
    ref.update(
        {
            "status": "published",
            "blocked": False,
            "screening_match_state": screening.match_state,
            "screening_filters": screening.filters,
            "deidentified_text": deidentified.text,
            "tokens": deidentified.tokens,
            "token_aliases": deidentified.aliases,
            "published": True,
            "message_id": message_id,
            "screened_at": utcnow(),
            "triage": label,
        }
    )

    logs.info(
        "event de-identified and published",
        idempotency_key=key,
        event_type=event.event_type,
        tokens=deidentified.tokens,
        message_id=message_id,
        trace_id=trace_id,
    )

    return {
        "idempotency_key": key,
        "duplicate": False,
        "status": "published",
        "tokens": deidentified.tokens,
        "message_id": message_id,
        "published": True,
    }


@app.get("/events/{idempotency_key}")
def get_event(idempotency_key: str) -> dict:
    snapshot = db().collection(EVENTS).document(idempotency_key).get()
    if not snapshot.exists:
        raise HTTPException(status_code=404, detail="event not found")
    record = snapshot.to_dict() or {}
    # The alias map is the only reversible artefact in the record, so it is not
    # served here. services/reid is the one path that may resolve a token.
    record.pop("token_aliases", None)
    return record
