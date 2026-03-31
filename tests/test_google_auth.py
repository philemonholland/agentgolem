"""Tests for Google OAuth readiness helpers."""

from __future__ import annotations

import json

import pytest

from agentgolem.tools.google_auth import (
    google_oauth_setup_status,
    load_google_desktop_oauth_client,
)


def test_load_google_desktop_oauth_client_validates_installed_shape(tmp_path) -> None:
    client_file = tmp_path / "google_oauth_client.json"
    client_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id.apps.googleusercontent.com",
                    "client_secret": "super-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )

    client = load_google_desktop_oauth_client(client_file)

    assert client.client_id == "client-id.apps.googleusercontent.com"
    assert client.client_secret.get_secret_value() == "super-secret"
    assert client.redirect_uris == ["http://localhost"]


def test_load_google_desktop_oauth_client_rejects_missing_installed(tmp_path) -> None:
    client_file = tmp_path / "google_oauth_client.json"
    client_file.write_text(json.dumps({"web": {}}), encoding="utf-8")

    with pytest.raises(ValueError, match="installed"):
        load_google_desktop_oauth_client(client_file)


def test_google_oauth_setup_status_reports_validation(tmp_path) -> None:
    client_file = tmp_path / "google_oauth_client.json"
    token_file = tmp_path / "oauth_token.json"
    client_file.write_text(
        json.dumps(
            {
                "installed": {
                    "client_id": "client-id.apps.googleusercontent.com",
                    "client_secret": "super-secret",
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost"],
                }
            }
        ),
        encoding="utf-8",
    )

    status = google_oauth_setup_status(client_file, token_file)

    assert status == {
        "client_file_exists": True,
        "token_file_exists": False,
        "client_config_valid": True,
    }
