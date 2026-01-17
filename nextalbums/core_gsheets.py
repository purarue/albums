import re
from typing import Any

import httplib2  # type: ignore[import]
from googleapiclient import discovery  # type: ignore[import]
from oauth2client.file import Storage  # type: ignore[import]
from oauth2client.client import OAuth2Credentials  # type: ignore[import]

from . import SETTINGS
from .common import WorksheetData, WorksheetRow, eprint


def get_credentials() -> OAuth2Credentials:
    """Gets valid user credentials from storage.

    If nothing has been stored, or if the stored credentials are invalid,
    exits, and tells user to run setup script.
    """
    store = Storage(SETTINGS.CREDENTIALS_PATH)
    credentials = store.get()
    if not credentials or credentials.invalid:
        eprint(
            f"""Credentials aren't setup properly. Run 'python3 setup_credentials.py'
If the problem persists, delete {SETTINGS.CREDENTIALS_PATH} and then try again"""
        )
        raise SystemExit(1)
    return credentials


ESCAPED_TEXT = re.compile(r'^=T\("(.*?)"\)$')


def _remove_escapes(data: WorksheetData) -> WorksheetData:
    new_data: WorksheetData = []
    for row in data:
        new_row: WorksheetRow = []
        for col in row:
            if match := re.match(ESCAPED_TEXT, str(col)):
                new_row.append(match.group(1))
            else:
                new_row.append(col)
        new_data.append(new_row)
    return new_data


def get_values(
    *,
    sheetRange: str,
    valueRenderOption: str,
    credentials: Any | None = None,
    remove_escapes: bool = True,
) -> WorksheetData:
    creds: Any
    if credentials is None:
        creds = get_credentials()
    else:
        creds = credentials
    http = creds.authorize(httplib2.Http())
    discoveryUrl = "https://sheets.googleapis.com/$discovery/rest?version=v4"
    service = discovery.build(
        "sheets",
        "v4",
        http=http,
        discoveryServiceUrl=discoveryUrl,
        cache_discovery=False,
    )
    result = (
        service.spreadsheets()
        .values()
        .get(
            spreadsheetId=SETTINGS.SPREADSHEET_ID,
            range=sheetRange,
            valueRenderOption=valueRenderOption,
        )
        .execute()
    )
    data: WorksheetData = result.get("values", [])
    if remove_escapes and valueRenderOption == "FORMULA":
        data = _remove_escapes(data)
    return data
