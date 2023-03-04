import os
import re
import string
from dataclasses import dataclass, replace
from functools import lru_cache
from urllib.parse import urlparse
from typing import List, Any, Set, Dict, Optional, Union, Sequence
from time import sleep

import click
import romkan  # type: ignore[import]
import backoff  # type: ignore[import]
import httplib2  # type: ignore[import]
from more_itertools import unique_everseen
from googleapiclient import discovery  # type: ignore[import]

from . import SETTINGS
from .core_gsheets import get_credentials, get_values
from .common import WorksheetData, WorksheetRow, eprint
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


SLUG_DIGITS = set(string.digits + " " + "_")


@lru_cache(maxsize=None)
def slugify(data: str) -> str:
    slug = "".join(s for s in data.strip() if s in ALLOWED)
    # if its just a date with underscores or spaces
    if set(slug.strip()).issubset(SLUG_DIGITS):
        # try to convert japanese text to romaji to prevent image clashes
        slug = "".join(s for s in romkan.to_roma(data).strip() if s in ALLOWED)
    return slug.replace(" ", "_").casefold().strip()


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
    def from_row(cls, row: WorksheetRow) -> "AlbumInfo":
        strd = [
            str(c) for c in _pad_data(row, len(list(AlbumInfo.__annotations__.keys())))
        ]
        return cls(*strd)

    def has_discogs_link(self) -> bool:
        return bool(self.discogs_url.strip())

    def to_row(self) -> List[str]:
        fields = list(AlbumInfo.__annotations__.keys())
        vals: List[str] = []
        for f in fields:
            vals.append(getattr(self, f))
        assert len(vals) == 11, str(vals)
        return vals

    def slugify_hash(self) -> str:
        album = romkan.to_roma(self.album)
        artist = romkan.to_roma(self.artist)
        album_id_raw = f"{album} {artist} {self.year}"
        return slugify(album_id_raw)


def _fix_discogs_link(link: str, resolve: bool) -> str:
    """Removes unnecessary parts of Discogs URLs"""
    urlparse_path = urlparse(link).path
    if master_id := re.search(r"\/master\/(?:view\/)?(\d+)", urlparse_path):
        return "https://www.discogs.com/master/{}".format(master_id.groups()[0])
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
                    return "https://www.discogs.com/release/{}".format(release_match)
            else:
                return f"https://www.discogs.com/release/{release_id.groups()[0]}"
        else:
            raise Exception(f"Unknown discogs link: {link}. Exiting...")


ARTIST_NAME_REGEX = re.compile(r"\(\d+\)$")


def _fix_artist_name(artist_name: str) -> str:
    return str(re.sub(ARTIST_NAME_REGEX, "", artist_name)).strip()


def _artist_ids(artists: List[Dict[str, Any]]) -> str:
    artist_ids = [str(a["id"]) for a in artists if int(a["id"]) != 0]
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
        return '=T("{}")'.format(title)
    return title


def _add_image_formula(img_url: str) -> str:
    return f'=IMAGE("{img_url}")'


def _discogs_image(album: Album) -> Optional[str]:
    for d in album.datas():
        if image_list := d.get("images"):
            assert isinstance(image_list, list)
            for d in image_list:
                return _add_image_formula(d["uri"])
    return None


ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png"]


def _s3_proxy_image(info: AlbumInfo) -> str:
    """
    use s3 to reupload the image so I'm not hitting discogs cdn all the time
    https://github.com/seanbreckenridge/s3-image-server

    If user doesnt have this configured/isnt installed,
    just return the url thats already there
    """
    if "INSTANCE_URL" not in os.environ:
        return info.album_artwork
    try:
        from s3_image_uploader import upload_with_index
        from s3_image_uploader.cache import has
    except ImportError:
        return info.album_artwork

    # if empty, dont try to upload
    if info.album_artwork.strip() == "":
        return info.album_artwork

    img_url = remove_image_formula(info.album_artwork)
    urlparse_path = urlparse(img_url).path
    name = urlparse_path.split("/")[-1]
    _, _, ext = name.rpartition(".")
    assert ext.strip(), f"no extension found in {name}"
    assert ext.casefold() in ALLOWED_EXTENSIONS, f"{ext} not in {ALLOWED_EXTENSIONS}"

    target_filename = f"{info.slugify_hash()}.{ext}"

    if not has(target_filename):
        click.echo("Uploading {} {} to S3".format(target_filename, img_url))

    uploaded_url = upload_with_index(
        url=img_url,
        target_filename=target_filename,
        use_s3_public_url=os.environ.get("USE_S3_URL"),
    )

    return _add_image_formula(uploaded_url)


def discogs_update_info(info: AlbumInfo, album: AlbumOrErr) -> AlbumInfo:
    """Gets values from discogs API and prompts the user to confirm changes."""

    new_info = replace(info)  # copy dataclass
    link = info.discogs_url
    assert link.strip(), str(info)
    metadata: Dict[str, Any] = fetch_discogs(link).metadata

    assert "title" in metadata
    assert "artists" in metadata
    assert isinstance(metadata["artists"], list), str(metadata)

    new_info.album = _escape_title(metadata["title"])
    assert len(metadata["artists"]) > 0, str(metadata)
    new_info.main_artist_id = _artist_ids(metadata["artists"])
    new_info.artist = ", ".join(
        unique_everseen(
            (_fix_artist_name(artist["name"]) for artist in metadata["artists"])
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
        click.echo("Error uploading image to s3: {}".format(e), err=True)

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
    all_links: Set[str] = set()

    albums: List[AlbumOrErr] = list(
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
            else:  # exit if theres a duplicate discogs link (meaning duplicate entry)
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
            "range": "Music!B1:D{}".format(no_of_rows),
            "values": [_pad_data(vals[1:4], 3) for vals in values],
        },
        {
            # Reason, Album Artwork, Discogs Link, Artist ID(s), Genre, Style
            "range": "Music!F1:K{}".format(no_of_rows),
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
