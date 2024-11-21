"""Sets up Google Credentials for the spreadsheet"""

import argparse

from oauth2client import client  # type: ignore[import]
from oauth2client import tools  # type: ignore[import]
from oauth2client.file import Storage  # type: ignore[import]

from settings import CLIENT_SECRET_FILE, CREDENTIALS_PATH

# probably won't ever be changed, no point in putting them in settings.py?
# If modifying these scopes, delete your previously saved credentials
# at ~/.credentials/sheets.googleapis.com-python-nextalbums.json
SCOPES = "https://www.googleapis.com/auth/spreadsheets"
APPLICATION_NAME = "Next Albums"

# Set up OAuth2 flow to obtain new credentials
flags = argparse.ArgumentParser(parents=[tools.argparser]).parse_args()

store = Storage(CREDENTIALS_PATH)
credentials = store.get()
if not credentials or credentials.invalid:
    flow = client.flow_from_clientsecrets(CLIENT_SECRET_FILE, SCOPES)
    flow.user_agent = APPLICATION_NAME
    credentials = tools.run_flow(flow, store, flags)
    print("Storing credentials to ", CREDENTIALS_PATH)
else:
    print("Credentials already exist at", CREDENTIALS_PATH)
