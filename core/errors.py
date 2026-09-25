"""Handles bot errors."""

__all__ = (
    "BotError",
    "PPRFetchError",
    "PPRSnapshotError",
    "SheetsError",
    "MissingCredentialsError",
    "ClientAuthorizationError",
    "SheetNotFoundError",
)


class BotError(Exception):
    """Base class for all errors related to the bot."""


class PPRFetchError(BotError):
    """Raised when PPR (Player Power Rating) cannot be fetched for a team.

    Attributes:
        team_name (str): Name of the team PPR could not be fetched for
        season (str): Season attempted to fetch PPR for
    """

    def __init__(self, team_name: str, season: str, message: str | None = None):
        self.team_name = team_name
        self.season = season
        self.message = message or (
            f"Could not fetch PPR for team '{team_name}' " f"in season {season}"
        )
        super().__init__(self.message)


class PPRSnapshotError(BotError):
    """Raised when a snapshot of PPR values could not be saved.

    This typically occurs when Google Sheets API is unavailable or
    when there are problems writing to the snapshot sheet.
    """

    def __init__(self, message: str | None = None):
        self.message = message or "Could not save PPR snapshot"
        super().__init__(self.message)


class SheetsError(BotError):
    """Base class for errors related to Google Sheets."""


class MissingCredentialsError(SheetsError):
    """Raised when Google API credentials file is missing or unreadable.

    Attributes:
        path (str): Path to the missing credentials file
    """

    def __init__(self, path="credentials.json", message: str | None = None):
        self.path = path
        self.message = (
            message or f"Could not find Google API credentials file: '{path}'"
        )
        super().__init__(self.message)


class ClientAuthorizationError(SheetsError):
    """Raised when the Google Sheets client cannot be authorized.

    Typical causes are problems with Google API permissions or the service
    account has lost access to Sheets documents.
    """

    def __init__(self, message: str | None = None):
        self.message = message or ("Could not authorize Google Sheets client")
        super().__init__(self.message)


class SheetNotFoundError(SheetsError):
    """Raised when a specific Google Sheet or worksheet cannot be opened.

    Attributes:
        sheet_name (str): Name of the Sheets document
        worksheet_index (int): Index of the specific worksheet
    """

    def __init__(
        self, sheet_name: str, worksheet_index: int = 0, message: str | None = None
    ):
        self.sheet_name = sheet_name
        self.worksheet_index = worksheet_index
        self.message = message or (
            f"Could not open '{sheet_name}' or {worksheet_index}"
        )
        super().__init__(self.message)


class PickemError(BotError):
    """Base class for all errors related to the betting cog."""


class APIFetchError(PickemError):
    """Raised when data cannot be fetched from ESPN's API.

    Attributes:
        url (str): URL attempted to be fetched
        original_exception (Exception): The cause of the error.
    """

    def __init__(self, url: str, original_exception: Exception | None = None):
        self.url = url
        self.original_exception = original_exception
        self.message = f"Could not fetch data from ESPN API: {url}"
        if original_exception:
            self.message += f" ({original_exception})"
        super().__init__(self.message)


class NoEventsFoundError(PickemError):
    """Raised when no NFL games can be found for a week

    Attributes:
        week (int): The week number attempted to be fetched
    """

    def __init__(self, week: int | None = None):
        self.week = week
        self.message = f"No games found for week {week}" if week else "No games found"
        super().__init__(self.message)


class ExportError(PickemError):
    """Raised when match data cannot be exported to Google Sheets.

    Typically occurs when an API call fails or when the data format is
    unrecognized.
    """

    def __init__(self, message: str | None = None):
        self.message = message or "Error exporting game data to Sheets"
        super().__init__(self.message)


class ResultsError(PickemError):
    """Raised when results cannot be written to Google Sheets.

    This can occur when the Sheets API does not respond or when data
    validation fails.
    """

    def __init__(self, message: str | None = None):
        self.message = message or ("Error updating results in Sheets")
        super().__init__(self.message)


class ReminderError(PickemError):
    """Raised when a reminder task fails.

    Used by the reminder-schedulers when something goes wrong while attempting
    to send Discord messages.
    """

    def __init__(self, message: str | None = None):
        self.message = message or "Error in reminder task"
        super().__init__(self.message)
