"""Outbound notification.

Three implementations behind one interface so the choice is configuration rather
than code, which matters because the obvious option is unavailable here: Google
Chat incoming webhooks require a Workspace account and this project runs on a
consumer account.

log     always available, writes a structured line and nothing else
sheets  appends a row to a shared spreadsheet, works with the service account
        alone once the sheet is shared with it
gmail   sends real email using an OAuth refresh token held in Secret Manager

Every implementation returns an external reference id. That id is what the
Evidence Gate will later be able to point at, so a notification that cannot
produce one is not a notification this system can reason about.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from datetime import datetime, timezone
from email.message import EmailMessage
from functools import lru_cache
from typing import Protocol

import logs
from config import get_settings


@dataclass
class Notification:
    to: str
    subject: str
    body: str
    obligation_id: str
    kind: str
    trace_id: str | None = None


class NotifierError(Exception):
    """Raised when an outbound notification could not be delivered."""


class Notifier(Protocol):
    name: str

    def send(self, notification: Notification) -> str:
        """Deliver, returning an external reference id."""


class LogNotifier:
    """Records the notification and nothing more.

    Used when no delivery channel is configured. It is honest rather than
    convenient: it returns a reference id that names itself as a log entry, so
    nothing downstream can mistake it for evidence that a person was reached.
    """

    name = "log"

    def send(self, notification: Notification) -> str:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S%f")
        reference = f"log:{notification.obligation_id}:{notification.kind}:{stamp}"
        logs.info(
            "notification recorded but not delivered, no channel is configured",
            obligation_id=notification.obligation_id,
            kind=notification.kind,
            to=notification.to,
            subject=notification.subject,
            reference=reference,
            trace_id=notification.trace_id,
        )
        return reference


class SheetsNotifier:
    """Appends one row to a spreadsheet shared with this service account.

    Needs no OAuth dance: the sheet is shared with the service account's address
    the same way it would be shared with a colleague.
    """

    name = "sheets"

    def __init__(self, spreadsheet_id: str, sheet_range: str = "Notifications!A:F") -> None:
        if not spreadsheet_id:
            raise NotifierError("NOTIFY_SHEET_ID is not set")
        self.spreadsheet_id = spreadsheet_id
        self.sheet_range = sheet_range

    @staticmethod
    @lru_cache(maxsize=1)
    def _service():
        import google.auth
        from googleapiclient.discovery import build

        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/spreadsheets"]
        )
        return build("sheets", "v4", credentials=credentials, cache_discovery=False)

    def send(self, notification: Notification) -> str:
        now = datetime.now(timezone.utc).isoformat()
        row = [
            now,
            notification.obligation_id,
            notification.kind,
            notification.to,
            notification.subject,
            notification.body,
        ]
        try:
            response = (
                self._service()
                .spreadsheets()
                .values()
                .append(
                    spreadsheetId=self.spreadsheet_id,
                    range=self.sheet_range,
                    valueInputOption="RAW",
                    insertDataOption="INSERT_ROWS",
                    body={"values": [row]},
                )
                .execute()
            )
        except Exception as exc:
            raise NotifierError(f"sheets append failed: {exc}") from exc

        reference = response.get("updates", {}).get("updatedRange", "")
        logs.info(
            "notification appended to sheet",
            obligation_id=notification.obligation_id,
            kind=notification.kind,
            reference=reference,
            trace_id=notification.trace_id,
        )
        return f"sheets:{reference}"


class GmailNotifier:
    """Sends real email through the Gmail API.

    A service account cannot send as a consumer Gmail user without domain wide
    delegation, which needs Workspace, so this uses an OAuth refresh token
    obtained once by the account owner and stored in Secret Manager.
    """

    name = "gmail"

    def __init__(self, secret_name: str, sender: str) -> None:
        if not secret_name or not sender:
            raise NotifierError("GMAIL_OAUTH_SECRET and NOTIFY_FROM must both be set")
        self.secret_name = secret_name
        self.sender = sender

    @lru_cache(maxsize=1)
    def _service(self):
        from google.cloud import secretmanager
        from google.oauth2.credentials import Credentials
        from googleapiclient.discovery import build

        settings = get_settings()
        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{settings.project_id}/secrets/{self.secret_name}/versions/latest"
        blob = client.access_secret_version(name=name).payload.data.decode("utf-8")
        stored = json.loads(blob)

        credentials = Credentials(
            token=None,
            refresh_token=stored["refresh_token"],
            client_id=stored["client_id"],
            client_secret=stored["client_secret"],
            token_uri="https://oauth2.googleapis.com/token",
            scopes=["https://www.googleapis.com/auth/gmail.send"],
        )
        return build("gmail", "v1", credentials=credentials, cache_discovery=False)

    def send(self, notification: Notification) -> str:
        message = EmailMessage()
        message["To"] = notification.to
        message["From"] = self.sender
        message["Subject"] = notification.subject
        message.set_content(notification.body)

        encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("utf-8")
        try:
            sent = (
                self._service()
                .users()
                .messages()
                .send(userId="me", body={"raw": encoded})
                .execute()
            )
        except Exception as exc:
            raise NotifierError(f"gmail send failed: {exc}") from exc

        reference = sent.get("id", "")
        logs.info(
            "notification emailed",
            obligation_id=notification.obligation_id,
            kind=notification.kind,
            to=notification.to,
            reference=reference,
            trace_id=notification.trace_id,
        )
        return f"gmail:{reference}"


@lru_cache(maxsize=1)
def get_notifier() -> Notifier:
    """Build the configured notifier, falling back to the log if it cannot start.

    A misconfigured channel must not stop the engine from detecting a breach.
    Losing the notification is bad; losing the detection is worse.
    """
    settings = get_settings()
    choice = settings.notifier.strip().lower()

    try:
        if choice == "gmail":
            return GmailNotifier(settings.gmail_oauth_secret, settings.notify_from)
        if choice == "sheets":
            return SheetsNotifier(settings.notify_sheet_id)
    except NotifierError as exc:
        logs.error(
            "configured notifier could not start, falling back to log only",
            notifier=choice,
            error=str(exc),
        )
        return LogNotifier()

    if choice != "log":
        logs.warning("unknown notifier requested, using log only", notifier=choice)
    return LogNotifier()
