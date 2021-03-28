import os
import re
import csv

from . import SETTINGS
from .core_gsheets import get_values


def generate_csv_file(write_to: str) -> None:
    values = get_values(sheetRange="Music!A2:L", valueRenderOption="FORMULA")
    values = [
        row
        for row in values
        if not set(map(lambda s: s.lower(), re.split("\s*,\s*", row[5]))).issubset(
            set(["manual", "relation", "recommendation"])
        )
    ]
    with open(write_to, "w") as csv_file:
        csv_writer = csv.writer(csv_file, quoting=csv.QUOTE_ALL)
        max_row_len = max(map(len, values))
        for row in values:
            # write '' to empty cells, to make all the row lengths in the csv file the same
            padded = row + [""] * (max_row_len - len(row))
            # remove score and listened on dates
            padded[0] = ""
            padded[4] = ""
            csv_writer.writerow(padded)