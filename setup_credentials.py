import os
import argparse

from oauth2client import client  # type: ignore[import]
from oauth2client import tools  # type: ignore[import]
from oauth2client.file import Storage  # type: ignore[import]

from settings import CLIENT_SECRET_FILE

# probably wont ever be changed, no point in putting them in settings.py?
# If modifying these scopes, delete your previously saved credentials
# at ~/.credentials/sheets.googleapis.com-python-nextalbums.json
SCOPES = "https://www.googleapis.com/auth/spreadsheets"
APPLICATION_NAME = "Next Albums"

# Set up OAuth2 flow to obtain new credentials
flags = argparse.ArgumentParser(parents=[tools.argparser]).parse_args()
home_dir = os.path.expanduser("~")
credential_dir = os.path.join(home_dir, ".credentials")
if not os.path.exists(credential_dir):
    os.makedirs(credential_dir)
credential_path = os.path.join(
    credential_dir, "sheets.googleapis.com-python-nextalbums.json"
)
store = Storage(credential_path)
credentials = store.get()
if not credentials or credentials.invalid:
    flow = client.flow_from_clientsecrets(CLIENT_SECRET_FILE, SCOPES)
    flow.user_agent = APPLICATION_NAME
    credentials = tools.run_flow(flow, store, flags)
    print("Storing credentials to ", credential_path)
else:
    print("Credentials already exist at", credential_path)
