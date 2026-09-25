"""Global test fixtures for mocking gspread/OAuth without credentials."""

import os
from unittest.mock import MagicMock
import pytest
import google.oauth2.service_account as google_auth
import gspread

# data/channel_ids.py reads these at import time, which happens during test
# collection, before any fixture runs.
os.environ.setdefault("REMINDER_CHANNEL_ID", "111111111111111111")
os.environ.setdefault("GAME_CHANNEL_ID", "222222222222222222")
os.environ.setdefault("ADMIN_CHANNEL_ID", "333333333333333333")


@pytest.fixture(autouse=True)
def mock_gspread(monkeypatch):
    """Mocks Google Sheets along the chain get_sheet() uses:
    authorize(creds) -> client.open(name) -> spreadsheet.get_worksheet(index).
    """
    monkeypatch.setattr(
        google_auth.Credentials,
        "from_service_account_file",
        lambda *a, **kw: MagicMock(),
    )

    mock_client = MagicMock()
    mock_spreadsheet = MagicMock()
    mock_sheet = MagicMock()

    # client.open(name) -> spreadsheet
    mock_client.open = lambda *a, **kw: mock_spreadsheet
    # spreadsheet.get_worksheet(index) -> the worksheet get_sheet() returns
    mock_spreadsheet.get_worksheet.return_value = mock_sheet
    # spreadsheet.worksheet("State") / .add_worksheet(...) -> used by _get_state_sheet()
    mock_spreadsheet.worksheet.return_value = mock_sheet
    mock_spreadsheet.add_worksheet.return_value = mock_sheet
    # worksheet.spreadsheet -> back to spreadsheet, used by _get_state_sheet()
    mock_sheet.spreadsheet = mock_spreadsheet

    monkeypatch.setattr(gspread, "authorize", lambda *a, **kw: mock_client)

    # Defaults; tests can override them on the returned mock_sheet.
    mock_sheet.row_values.return_value = ["ID1", "ID2", "ID3"]
    mock_sheet.get_all_values.return_value = [["Kamp1", "", ""], ["Kamp2", "", ""]]
    mock_sheet.cell.return_value.value = "Test"
    mock_sheet.update_cell.return_value = None
    mock_sheet.range.return_value = []
    mock_sheet.update_cells.return_value = None

    return mock_sheet
