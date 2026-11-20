"""The bot's own exception classes."""


class BotError(Exception):
    """Base class for all errors related to the bot."""


class PPRFetchError(BotError):
    """A team's PPR could not be fetched for the given season."""

    def __init__(self, team_name: str, season: str, message: str | None = None):
        self.team_name = team_name
        self.season = season
        self.message = message or (
            f"Could not fetch PPR for team '{team_name}' " f"in season {season}"
        )
        super().__init__(self.message)


class PPRSnapshotError(BotError):
    """The PPR snapshot could not be saved (usually Google Sheets is down)."""

    def __init__(self, message: str | None = None):
        self.message = message or "Could not save PPR snapshot"
        super().__init__(self.message)


class StreamerFetchError(BotError):
    """ESPN's answer for the Streamer award was missing something we need."""


class SheetsError(BotError):
    """Base class for errors related to Google Sheets."""


class MissingCredentialsError(SheetsError):
    """The Google credentials file is missing or unreadable."""

    def __init__(self, path="credentials.json", message: str | None = None):
        self.path = path
        self.message = (
            message or f"Could not find Google API credentials file: '{path}'"
        )
        super().__init__(self.message)


class ClientAuthorizationError(SheetsError):
    """Google rejected the credentials, or the service account lost access."""

    def __init__(self, message: str | None = None):
        self.message = message or ("Could not authorize Google Sheets client")
        super().__init__(self.message)


class SheetNotFoundError(SheetsError):
    """A spreadsheet, or a tab in it, could not be opened."""

    def __init__(
        self, sheet_name: str, worksheet_index: int = 0, message: str | None = None
    ):
        self.sheet_name = sheet_name
        self.worksheet_index = worksheet_index
        self.message = message or (
            f"Could not open tab {worksheet_index} of '{sheet_name}'"
        )
        super().__init__(self.message)


class PickemError(BotError):
    """Base class for all errors related to the pick'em cog."""


class APIFetchError(PickemError):
    """A request to ESPN's API failed. Keeps the URL and the original exception."""

    def __init__(self, url: str, original_exception: Exception | None = None):
        self.url = url
        self.original_exception = original_exception
        self.message = f"Could not fetch data from ESPN API: {url}"
        if original_exception:
            self.message += f" ({original_exception})"
        super().__init__(self.message)


class NoEventsFoundError(PickemError):
    """ESPN has no NFL games for the requested week."""

    def __init__(self, week: int | None = None):
        self.week = week
        self.message = f"No games found for week {week}" if week else "No games found"
        super().__init__(self.message)


class ExportError(PickemError):
    """The week's games or picks could not be exported to the spreadsheet."""

    def __init__(self, message: str | None = None):
        self.message = message or "Error exporting game data to Sheets"
        super().__init__(self.message)


class ResultsError(PickemError):
    """The week's results could not be written to the spreadsheet."""

    def __init__(self, message: str | None = None):
        self.message = message or ("Error updating results in Sheets")
        super().__init__(self.message)
