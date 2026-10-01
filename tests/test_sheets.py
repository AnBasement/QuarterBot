"""Tests for sheets.py"""

from unittest.mock import MagicMock
import google.auth.exceptions
import pytest
from cogs import sheets
from core.errors import ClientAuthorizationError, MissingCredentialsError


def test_get_creds_none(monkeypatch):
    monkeypatch.delenv("GOOGLE_SHEETS_KEYFILE", raising=False)
    monkeypatch.setattr("os.path.exists", lambda x: False)
    with pytest.raises(MissingCredentialsError) as exc_info:
        sheets.get_creds()

    # The whole message, so a garbled one can't pass.
    assert str(exc_info.value) == (
        "Could not find Google API credentials file: 'credentials.json'"
    )


def test_get_creds_returns_creds(monkeypatch):

    class DummyCreds:
        """Dummy credentials object for testing."""

    monkeypatch.setattr("os.path.exists", lambda x: True)
    monkeypatch.setattr(
        "cogs.sheets.Credentials.from_service_account_file",
        lambda file, scopes: DummyCreds(),
    )
    creds = sheets.get_creds()
    assert isinstance(creds, DummyCreds)


def test_get_client_returns_client(monkeypatch):
    dummy_client = "client_instance"
    monkeypatch.setattr(sheets, "get_creds", lambda: "creds")
    monkeypatch.setattr(sheets.gspread, "authorize", lambda creds: dummy_client)
    client = sheets.get_client()
    assert client == dummy_client


def test_get_sheet(monkeypatch):
    dummy_sheet = MagicMock()
    dummy_client = MagicMock()
    dummy_client.open.return_value.get_worksheet.return_value = dummy_sheet

    monkeypatch.setattr(sheets, "get_client", lambda: dummy_client)
    sheet = sheets.get_sheet("MySheet")
    dummy_client.open.assert_called_once_with("MySheet")
    dummy_client.open.return_value.get_worksheet.assert_called_once_with(0)
    assert sheet == dummy_sheet


def test_get_client_logs_in_once_and_reuses_the_client(monkeypatch):
    logins = []

    def fake_authorize(creds):
        logins.append(creds)
        return "client_instance"

    monkeypatch.setattr(sheets, "get_creds", lambda: "creds")
    monkeypatch.setattr(sheets.gspread, "authorize", fake_authorize)

    first = sheets.get_client()
    second = sheets.get_client()

    assert first == second == "client_instance"
    assert len(logins) == 1


def test_failed_login_is_not_remembered(monkeypatch):
    """A failed login must be retried on the next call, not stick forever."""
    attempts = []

    def flaky_authorize(creds):
        attempts.append(creds)
        if len(attempts) == 1:
            raise google.auth.exceptions.TransportError("Google unreachable")
        return "client_instance"

    monkeypatch.setattr(sheets, "get_creds", lambda: "creds")
    monkeypatch.setattr(sheets.gspread, "authorize", flaky_authorize)

    with pytest.raises(ClientAuthorizationError):
        sheets.get_client()
    client = sheets.get_client()

    assert client == "client_instance"
    assert len(attempts) == 2
