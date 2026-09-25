"""Google Sheets access."""

from typing import List, Dict, Any
import logging
import os
import gspread
import gspread.exceptions
import google.auth.exceptions
import requests
from google.oauth2.service_account import Credentials
from gspread.worksheet import Worksheet
from gspread.client import Client

from core.errors import (
    MissingCredentialsError,
    ClientAuthorizationError,
    SheetNotFoundError,
)

logger = logging.getLogger(__name__)

scope: List[str] = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive",
]


def get_creds() -> Credentials:
    """Loads the service account key from GOOGLE_SHEETS_KEYFILE."""
    keyfile = os.getenv("GOOGLE_SHEETS_KEYFILE", "credentials.json")
    if not os.path.exists(keyfile):
        raise MissingCredentialsError(keyfile)
    try:
        return Credentials.from_service_account_file(keyfile, scopes=scope)
    except (ValueError, KeyError, OSError) as e:
        raise MissingCredentialsError(
            keyfile, f"Could not read Google API credentials file '{keyfile}': {e}"
        ) from e


def get_client() -> Client:
    """Returns a logged-in gspread client."""
    try:
        creds = get_creds()
        return gspread.authorize(creds)
    except MissingCredentialsError:
        raise
    except (
        google.auth.exceptions.GoogleAuthError,
        gspread.exceptions.GSpreadException,
        requests.exceptions.RequestException,
    ) as e:
        raise ClientAuthorizationError(
            f"Could not authorize with Google: {str(e)}"
        ) from e


def get_sheet(sheet_name: str, worksheet_index: int = 0) -> Worksheet:
    """Opens a worksheet (the first tab by default) in a spreadsheet, by name."""
    try:
        client = get_client()
        return client.open(sheet_name).get_worksheet(worksheet_index)
    except gspread.SpreadsheetNotFound:
        raise SheetNotFoundError(
            sheet_name, worksheet_index, f"Could not find document '{sheet_name}'"
        ) from None
    except (
        gspread.exceptions.GSpreadException,
        requests.exceptions.RequestException,
    ) as e:
        raise SheetNotFoundError(
            sheet_name, worksheet_index, f"Error opening document: {str(e)}"
        ) from e


def green_format() -> Dict[str, Any]:
    """Green background, black text."""
    return {
        "backgroundColor": {"red": 0.0, "green": 1.0, "blue": 0.0},
        "textFormat": {"foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}},
    }


def red_format() -> Dict[str, Any]:
    """Red background, black text."""
    return {
        "backgroundColor": {"red": 1.0, "green": 0.0, "blue": 0.0},
        "textFormat": {"foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}},
    }


def yellow_format() -> Dict[str, Any]:
    """Yellow background, black text."""
    return {
        "backgroundColor": {"red": 1.0, "green": 1.0, "blue": 0.0},
        "textFormat": {"foregroundColor": {"red": 0.0, "green": 0.0, "blue": 0.0}},
    }
