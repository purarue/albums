# Changing this file updates the settings for this entire project
# to test this you can run 'python3 settings.py'

import os
import sys
import shlex

try:
    import yaml
except ImportError:
    print(
        "Could not import yaml. Fix this by running: 'python3 -m pip install PyYAML'",
        file=sys.stderr,
    )
    sys.exit(1)

this_dir = os.path.abspath(os.path.dirname(__file__))

IMAGE_DATA = os.environ.get(
    "ALBUMS_IMAGE_DATA",
    os.path.join(
        os.environ.get("XDG_DOCUMENTS_DIR", os.path.expanduser("~/.local/share")),
        "image_data.json",
    ),
)

BASE_SPREADSHEETS_CSV_FILE = os.path.join(this_dir, "spreadsheet.csv")

SPREADSHEET_ID = "12htSAMg67czl8cpkj1mX0TuAFvqL_PJLI4hv1arG5-M"
DISCOGS_CREDS = {}

discogs_data_file = os.path.join(this_dir, "discogs_token.yaml")
if os.path.exists(discogs_data_file):
    with open(discogs_data_file) as f:
        DISCOGS_CREDS = yaml.load(f, Loader=yaml.FullLoader)

CLIENT_SECRET_FILE = os.path.join(this_dir, "client_secret.json")

CREDENTIALS_DIR = os.path.join(os.path.expanduser("~"), ".credentials")
os.makedirs(CREDENTIALS_DIR, exist_ok=True)

CREDENTIALS_PATH = os.path.join(
    CREDENTIALS_DIR, "sheets.googleapis.com-python-nextalbums.json"
)

CSV_DATADIR = os.path.join(this_dir, "csv_data")
assert os.path.exists(CSV_DATADIR)


def print_options():
    for name, obj in globals().items():
        if name.isupper():
            print(f"{name}={shlex.quote(str(obj))}")


if __name__ == "__main__":
    print_options()
