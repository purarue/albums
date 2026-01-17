from __future__ import annotations
import os
import re
import string
from datetime import date
from dataclasses import dataclass, replace
from urllib.parse import urlparse
from typing import (
    Any,
    Union,
    TYPE_CHECKING,
)
from collections.abc import Sequence
from time import sleep

import click
import backoff  # type: ignore[import]
import httplib2  # type: ignore[import]
from more_itertools import unique_everseen
from googleapiclient import discovery

from nextalbums.image_proxy import image_db

from . import SETTINGS
from .core_gsheets import get_credentials, get_values
from .common import CustomReason, WorksheetData, WorksheetRow, eprint
from .discogs_cache import fetch_discogs, backoff_hdlr, discogsClient
from .export import export_data, Album, _split_separated

AlbumOrErr = Union[Album, Exception]


def _pad_data(row: WorksheetRow, col_count: int) -> WorksheetRow:
    while len(row) > 0 and str(row[-1]).strip() == "":
        row.pop(-1)
    while len(row) < col_count:
        row.append("")
    assert len(row) == col_count, f"{row} {col_count}"
    return row


ALLOWED = string.ascii_letters + string.digits + " "


def remove_image_formula(img_cell: str) -> str:
    assert img_cell.startswith("=IMAGE(")
    if img_cell.startswith("=IMAGE("):
        return img_cell[7:-1].strip('"').strip("'")
    return img_cell


def slugify(data: str, allow_period: bool = False) -> str:
    allow = ALLOWED + "." if allow_period else ALLOWED
    slug = "".join(s for s in data.strip() if s in allow)
    return slug.replace(" ", "_").casefold().strip()


def slugify_data(album: str, artist: str, year: str, discogs_url: str) -> str:
    album_id_raw = f"{album} {artist} {year}"
    discogs_id = ""
    if discogs_url.strip():
        discogs_id = discogs_url.split("/")[-1]
        album_id_raw += f" {discogs_id}"
    return slugify(album_id_raw)


@dataclass
class AlbumInfo:
    score: str
    album: str
    artist: str
    year: str
    listened_on: str
    reason: str
    album_artwork: str
    discogs_url: str
    main_artist_id: str
    genres: str
    styles: str

    @classmethod
    def from_row(cls, row: WorksheetRow) -> AlbumInfo:
        strd = [
            str(c) for c in _pad_data(row, len(list(AlbumInfo.__annotations__.keys())))
        ]
        return cls(*strd)

    def has_discogs_link(self) -> bool:
        return bool(self.discogs_url.strip())

    def to_row(self) -> list[str]:
        fields = list(AlbumInfo.__annotations__.keys())
        vals: list[str] = []
        for f in fields:
            vals.append(getattr(self, f))
        assert len(vals) == 11, str(vals)
        return vals

    def slugify_hash(self) -> str:
        return slugify_data(self.album, self.artist, self.year, self.discogs_url)


def _fix_discogs_link(link: str, resolve: bool) -> str:
    """Removes unnecessary parts of Discogs URLs"""
    urlparse_path = urlparse(link).path
    if master_id := re.search(r"\/master\/(?:view\/)?(\d+)", urlparse_path):
        return f"https://www.discogs.com/master/{master_id.groups()[0]}"
    else:  # if there is no master id
        if release_id := re.search(r"\/release\/(?:view\/)?(\d+)", urlparse_path):
            if resolve:
                release_match = release_id.groups()[0]
                eprint(f"Attempting to resolve release {release_match} to master.")
                rel = discogsClient().release(int(release_match))
                sleep(2)
                if rel.master is not None:
                    eprint(f"Resolved release {release_match} to {rel.master.id}.")
                    return f"https://www.discogs.com/master/{rel.master.id}"
                else:
                    return f"https://www.discogs.com/release/{release_match}"
            else:
                return f"https://www.discogs.com/release/{release_id.groups()[0]}"
        else:
            raise Exception(f"Unknown discogs link: {link}. Exiting...")


ARTIST_NAME_REGEX = re.compile(r"\(\d+\)$")


def _fix_artist_name(artist_name: str) -> str:
    return str(re.sub(ARTIST_NAME_REGEX, "", artist_name)).strip()


def _artist_ids(artists: list[dict[str, Any]]) -> str:
    artist_ids = [str(a["id"]) for a in artists if int(a.get("id", 0) or 0) != 0]
    return "; ".join(artist_ids)


ASK_FIELDS = list(AlbumInfo.__annotations__.keys())
ASK_FIELDS.remove("score")
ASK_FIELDS.remove("listened_on")


def print_changes(
    old_info: AlbumInfo, new_info: AlbumInfo, ignore_fields: Sequence[str] = ()
) -> None:
    """Asks the user to confirm changes resulting from discogs data."""
    printed_album_info: bool = False
    for field in ASK_FIELDS:
        old_data = getattr(old_info, field)
        new_data = getattr(new_info, field)
        if str(old_data) != str(new_data):
            if field in ignore_fields:
                continue
            if not printed_album_info:
                print(f"\n{new_info}\n")
                printed_album_info = True
            eprint(f"'{old_data}' → '{new_data}'")


# titles like '4:30', time of day, get malformed because gsheets thinks its math
TIME_DIGITS = set(string.digits + ":")


def _escape_title(title: str) -> str:
    if set(title).issubset(TIME_DIGITS) or "'" in title:
        return f'=T("{title}")'
    return title


def _add_image_formula(img_url: str) -> str:
    assert isinstance(img_url, str)
    return f'=IMAGE("{img_url}")'


def _discogs_image(album: Album) -> str | None:
    for d in album.datas():
        if image_list := d.get("images"):
            assert isinstance(image_list, list)
            for d in image_list:
                return _add_image_formula(d["uri"])
    return None


ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png"]

printed = False


def _s3_proxy_image(info: AlbumInfo) -> str:
    """
    use s3 to reupload the image so I'm not hitting discogs cdn all the time
    https://github.com/purarue/s3-image-server

    If user doesn't have this configured/isn't installed,
    just return the url that's already there
    """
    if "USE_S3_URL" not in os.environ:
        return info.album_artwork
    try:
        from .image_proxy import proxy_image
    except ImportError as e:
        global printed
        if not printed:
            eprint(f"Could not import image_proxy, skipping image upload to s3 {e}")
            printed = True
        return info.album_artwork

    # if empty, dont try to upload
    if info.album_artwork.strip() == "":
        return info.album_artwork

    img_url = remove_image_formula(info.album_artwork)
    proxied = proxy_image(img_url, info.slugify_hash(), info.discogs_url)

    db = image_db()
    db.save()

    if proxied is None:
        return info.album_artwork
    return _add_image_formula(proxied)


def discogs_update_info(info: AlbumInfo, album: AlbumOrErr) -> AlbumInfo:
    """Gets values from discogs API and prompts the user to confirm changes."""

    new_info = replace(info)  # copy dataclass
    link = info.discogs_url
    assert link.strip(), str(info)
    metadata: dict[str, Any] = fetch_discogs(link).metadata

    assert "title" in metadata
    assert "artists" in metadata
    assert isinstance(metadata["artists"], list), str(metadata)

    new_info.album = _escape_title(metadata["title"])
    assert len(metadata["artists"]) > 0, str(metadata)
    new_info.main_artist_id = _artist_ids(metadata["artists"])
    new_info.artist = ", ".join(
        unique_everseen(
            _fix_artist_name(artist["name"]) for artist in metadata["artists"]
        )
    )

    if isinstance(album, Album):
        new_info.year = str(album.release_date().year)

    # TODO: use discogs URL to create a dummy album so this can use .datas() without already being validated by the Album.from_blob?
    # otherwise I have to run discogs-update twice to fetch the album art
    if isinstance(album, Album):
        if img_cell := _discogs_image(album):
            new_info.album_artwork = img_cell
        else:
            new_info.album_artwork = info.album_artwork

    new_info.genres = "; ".join(sorted(set(metadata.get("genres", []))))
    new_info.styles = "; ".join(sorted(set(metadata.get("styles", []))))

    print_changes(info, new_info, ignore_fields=["album_artwork"])
    return new_info


def non_discogs_update(info: AlbumInfo, album: AlbumOrErr) -> AlbumInfo:
    new_info = replace(info)  # copy dataclass
    new_info.genres = "; ".join(_split_separated(info.genres))
    new_info.styles = "; ".join(_split_separated(info.styles))

    print_changes(info, new_info)
    return new_info


def upkeep(info: AlbumInfo, album: AlbumOrErr) -> AlbumInfo:
    new_info = replace(info)  # copy dataclass
    new_info.reason = "; ".join(_split_separated(info.reason))

    try:
        new_info.album_artwork = _s3_proxy_image(new_info)
    except Exception as e:
        click.echo(f"Error uploading image to s3: {e}", err=True)

    print_changes(info, new_info, ignore_fields=["album_artwork"])
    return new_info


def update_row(info: AlbumInfo, album: AlbumOrErr, *, resolve: bool) -> AlbumInfo:
    """Updates with Discogs data if necessary."""
    if info.has_discogs_link():  # if this has a discogs link
        info.discogs_url = _fix_discogs_link(info.discogs_url, resolve)
        info = discogs_update_info(info, album)
    else:
        info = non_discogs_update(info, album)
    info = upkeep(info, album)
    return info


def updates(values: WorksheetData, resolve: bool) -> WorksheetData:
    """Error Handling, exits cleanly on exceptions."""
    header = values.pop(0)
    all_links: set[str] = set()

    albums: list[AlbumOrErr] = list(
        export_data(data_source=values, remove_header=False)
    )

    for index, (album, row) in enumerate(zip(albums, values, strict=True)):
        info: AlbumInfo = AlbumInfo.from_row(row)
        if isinstance(album, Exception):
            eprint(info)
            eprint(album)
        if not isinstance(album, Exception) and info.discogs_url:
            assert (
                album.discogs_url == info.discogs_url
            ), f"{album.discogs_url} != {info.discogs_url}"
        info = update_row(info, album, resolve=resolve)
        values[index] = info.to_row()

        # check for duplicate links, i.e. duplicate entries
        if info.has_discogs_link():
            if info.discogs_url not in all_links:
                all_links.add(info.discogs_url)
            else:  # exit if there's a duplicate discogs link (meaning duplicate entry)
                eprint(f"Found duplicate of {info.discogs_url}. Exiting...")
                break

    values.insert(0, header)  # put header back
    return values


@backoff.on_exception(
    lambda: backoff.constant(interval=10),
    httplib2.error.ServerNotFoundError,
    max_tries=5,
    on_backoff=backoff_hdlr,
)
def update_values(values, credentials):
    """Updates the values on the spreadsheet"""
    # Uses batchUpdate instead of update since its difficult to format 'date listened on' from FORMULA valueRenderOption
    service = discovery.build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    )
    no_of_rows = len(values)
    update_data = [
        {
            # Album, Artist, Year
            "range": f"Music!B1:D{no_of_rows}",
            "values": [_pad_data(vals[1:4], 3) for vals in values],
        },
        {
            # Reason, Album Artwork, Discogs Link, Artist ID(s), Genre, Style
            "range": f"Music!F1:K{no_of_rows}",
            "values": [_pad_data(vals[5:], 6) for vals in values],
        },
    ]
    update_body = {
        "valueInputOption": "USER_ENTERED",  # to allow images to display
        "data": update_data,
    }
    request = (
        service.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=SETTINGS.SPREADSHEET_ID, body=update_body)
    )
    return request.execute()


def update_new_entries(resolve: bool) -> int:
    """Returns the number of cells updated"""
    credentials = get_credentials()
    values = get_values(
        credentials=credentials,
        sheetRange="Music!A1:K",
        valueRenderOption="FORMULA",
        remove_escapes=False,
    )
    if len(values) == 0:
        eprint("No values returned")
        raise SystemExit(1)
    response = update_values(values=updates(values, resolve), credentials=credentials)
    return int(response["totalUpdatedCells"])


if TYPE_CHECKING:
    from .export import Album


def mark_listened(
    album: Album,
    spreadsheet: WorksheetData,
    *,
    score: float,
    listened_on: date | None = None,
) -> None:
    """Marks the album as listened to on the spreadsheet"""
    change_index = None
    for index, row in enumerate(spreadsheet):
        if album.discogs_url == row[6] or (
            album.album_name == row[1]
            and album.cover_artists == row[2]
            and album.year == row[3]
        ):
            row[0] = score
            if listened_on is not None:
                print(f"Setting listened on to {listened_on}")
                row[4] = listened_on.strftime("%Y-%m-%d")
            change_index = index
            break
    else:
        raise Exception(f"Could not find {album} in spreadsheet")
    assert change_index is not None

    credentials = get_credentials()
    service = discovery.build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    )
    update_data = [
        {
            # Score
            "range": f"Music!A{change_index + 1}",
            "values": [[score]],
        },
    ]

    if listened_on is not None:
        update_data += [
            {
                # Listened on
                "range": f"Music!E{change_index + 1}",
                "values": [[listened_on.strftime("%Y-%m-%d")]],
            }
        ]

    update_body = {
        "valueInputOption": "USER_ENTERED",  # to allow images to display
        "data": update_data,
    }

    request = (
        service.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=SETTINGS.SPREADSHEET_ID, body=update_body)
    )
    request.execute()


def add_album(discogs_url: str, reason: CustomReason) -> None:
    """
    adds a new album to the spreadsheet

    this just adds the album to the bottom of the spreadsheet
    """
    credentials = get_credentials()
    service = discovery.build(
        "sheets", "v4", credentials=credentials, cache_discovery=False
    )
    values = get_values(
        credentials=credentials,
        sheetRange="Music!A1:K",
        valueRenderOption="FORMULA",
        remove_escapes=False,
    )

    # check if the last row is empty. if it is, then skip adding an empty row
    add_empty_row = "".join(map(str, values[-1])).strip() != ""

    if add_empty_row:
        eprint("Adding empty row to spreadsheet")

        data = (
            service.spreadsheets().get(spreadsheetId=SETTINGS.SPREADSHEET_ID).execute()
        )
        sheetId = data["sheets"][0]["properties"]["sheetId"]
        assert isinstance(sheetId, int)

        # update row count
        service.spreadsheets().batchUpdate(
            spreadsheetId=SETTINGS.SPREADSHEET_ID,
            body={
                "requests": [
                    {
                        "insertDimension": {
                            "range": {
                                "sheetId": sheetId,
                                "dimension": "ROWS",
                                "startIndex": len(values),
                                "endIndex": len(values) + 1,
                            },
                            "inheritFromBefore": True,
                        }
                    }
                ]
            },
        ).execute()

    update_data = [
        {
            # Discogs Link
            "range": f"Music!H{len(values) + 1}",
            "values": [[discogs_url]],
        },
        {
            # Reason
            "range": f"Music!F{len(values) + 1}",
            "values": [[reason]],
        },
    ]

    update_body = {
        "valueInputOption": "USER_ENTERED",  # to allow images to display
        "data": update_data,
    }

    eprint(f"Adding {discogs_url} to spreadsheet")

    request = (
        service.spreadsheets()
        .values()
        .batchUpdate(spreadsheetId=SETTINGS.SPREADSHEET_ID, body=update_body)
    )
    request.execute()
