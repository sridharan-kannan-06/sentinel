"""Obtain a Gmail refresh token once and store it in Secret Manager.

A service account cannot send mail as a consumer Gmail user; that needs domain
wide delegation, which needs Workspace. Google Chat incoming webhooks are
Workspace-only too. So the account owner consents once, here, and the resulting
refresh token is what the engine uses from then on.

Before running this, in the Google Cloud console:

  1. APIs and Services, OAuth consent screen. Choose External. Fill in an app
     name and your own address for both support fields. Add your own address
     under Test users. Add the scope https://www.googleapis.com/auth/gmail.send
  2. APIs and Services, Credentials, Create credentials, OAuth client ID,
     application type Desktop app. Download the JSON.

Then:

    python infra/setup_gmail_oauth.py path\\to\\client_secret.json

A browser window opens, you approve once, and the refresh token is written to
Secret Manager as gmail-oauth. The token itself is never printed and never
written to disk.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
PROJECT_ID = "sentinel-506512"
SECRET_NAME = "gmail-oauth"


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2

    client_secret_path = Path(sys.argv[1])
    if not client_secret_path.exists():
        print(f"No such file: {client_secret_path}")
        return 2

    try:
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError:
        print("Install the dependency first:")
        print("    .venv\\Scripts\\python.exe -m pip install google-auth-oauthlib")
        return 2

    from google.api_core import exceptions as gexc
    from google.cloud import secretmanager

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    print("A browser window is opening. Approve the gmail.send scope for your own account.")
    credentials = flow.run_local_server(port=0)

    if not credentials.refresh_token:
        print(
            "No refresh token was returned. This usually means the account has already\n"
            "granted this client. Revoke it at https://myaccount.google.com/permissions\n"
            "and run this again."
        )
        return 1

    payload = json.dumps(
        {
            "refresh_token": credentials.refresh_token,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
        }
    ).encode("utf-8")

    client = secretmanager.SecretManagerServiceClient()
    parent = f"projects/{PROJECT_ID}"
    try:
        client.create_secret(
            request={
                "parent": parent,
                "secret_id": SECRET_NAME,
                "secret": {"replication": {"automatic": {}}},
            }
        )
        print(f"Created secret {SECRET_NAME}")
    except gexc.AlreadyExists:
        print(f"Secret {SECRET_NAME} already exists, adding a new version")

    client.add_secret_version(
        request={"parent": f"{parent}/secrets/{SECRET_NAME}", "payload": {"data": payload}}
    )
    print(f"Stored the refresh token as {SECRET_NAME}. It was not written to disk.")
    print()
    print("Now run these two:")
    print("    powershell -ExecutionPolicy Bypass -File infra/grant-secrets.ps1")
    print("    powershell -ExecutionPolicy Bypass -File infra/deploy.ps1 -Service engine")
    print()
    print("and set NOTIFIER=gmail in .env before that second command.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
