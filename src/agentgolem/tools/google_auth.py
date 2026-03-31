"""Shared Google OAuth helpers for future Gmail and Drive integrations."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, SecretStr

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


class GoogleDesktopOAuthClient(BaseModel):
    """Validated shape of the Google desktop OAuth ``installed`` block."""

    client_id: str
    client_secret: SecretStr
    auth_uri: str
    token_uri: str
    redirect_uris: list[str]


def load_google_desktop_oauth_client(client_file: Path) -> GoogleDesktopOAuthClient:
    """Load and validate a Google desktop OAuth client JSON file."""
    if not client_file.exists():
        raise FileNotFoundError(f"Google OAuth client file not found: {client_file}")

    payload = json.loads(client_file.read_text(encoding="utf-8"))
    installed = payload.get("installed")
    if not isinstance(installed, dict):
        raise ValueError("Google OAuth client JSON must contain an 'installed' object")

    required = {"client_id", "client_secret", "auth_uri", "token_uri", "redirect_uris"}
    missing = sorted(field for field in required if not installed.get(field))
    if missing:
        joined = ", ".join(missing)
        raise ValueError(f"Google OAuth client JSON is missing required installed fields: {joined}")

    redirect_uris = installed.get("redirect_uris")
    if not isinstance(redirect_uris, list) or not all(
        isinstance(uri, str) for uri in redirect_uris
    ):
        raise ValueError("Google OAuth client JSON field 'redirect_uris' must be a list of strings")

    return GoogleDesktopOAuthClient.model_validate(installed)


def google_oauth_setup_status(client_file: Path, token_file: Path) -> dict[str, Any]:
    """Return a non-secret readiness summary for local Google OAuth setup."""
    status = {
        "client_file_exists": client_file.exists(),
        "token_file_exists": token_file.exists(),
        "client_config_valid": False,
    }
    if client_file.exists():
        load_google_desktop_oauth_client(client_file)
        status["client_config_valid"] = True
    return status


def get_google_user_credentials(
    *,
    scopes: Sequence[str],
    client_file: Path,
    token_file: Path,
) -> Any:
    """Load or create user OAuth credentials for Google APIs."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
    except ImportError as exc:
        raise RuntimeError(
            "Google API dependencies are missing. Install "
            "google-api-python-client, google-auth-httplib2, and google-auth-oauthlib."
        ) from exc

    load_google_desktop_oauth_client(client_file)

    credentials = None
    if token_file.exists():
        credentials = Credentials.from_authorized_user_file(str(token_file), scopes)

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid:
        flow = InstalledAppFlow.from_client_secrets_file(str(client_file), scopes)
        credentials = flow.run_local_server(port=0)
        token_file.parent.mkdir(parents=True, exist_ok=True)
        token_file.write_text(credentials.to_json(), encoding="utf-8")

    return credentials


def build_google_service(
    *,
    api_name: str,
    api_version: str,
    scopes: Sequence[str],
    client_file: Path,
    token_file: Path,
) -> Any:
    """Create an authorized Google API service client."""
    try:
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google API client dependency is missing. Install google-api-python-client."
        ) from exc

    credentials = get_google_user_credentials(
        scopes=scopes,
        client_file=client_file,
        token_file=token_file,
    )
    return build(api_name, api_version, credentials=credentials, cache_discovery=False)
