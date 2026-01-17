from __future__ import annotations
import os
from datetime import date, datetime
from typing import TYPE_CHECKING, get_args, Literal

import click

from nextalbums.common import CustomReason

from . import SETTINGS
from .common import eprint


@click.group(invoke_without_command=True)
@click.pass_context
def main(ctx: click.Context) -> None:
    """
    Interact with my albums spreadsheet!
    """
    # run print-next if I didn't specify anything else
    if ctx.invoked_subcommand is None:
        ctx.invoke(print_next)


@main.command(short_help="print next albums")
@click.argument("COUNT", default=10, type=int)
@click.option("-r", "--random", is_flag=True, default=False, help="Print random albums")
def print_next(count: int, random: bool) -> None:
    """Print the next albums I should listen to"""
    from .generate_table import generate_table

    click.echo(generate_table(count, random))


@main.command(short_help="export sheet as JSON")
@click.option(
    "-r",
    "--raise-errors",
    is_flag=True,
    default=False,
    help="If there are any errors while exporting, exit",
)
def export(raise_errors: bool) -> None:
    """Parse and print all of the information from the spreadsheet as JSON"""
    from .export import export_data, dump_results

    items = []
    for res in export_data():
        if isinstance(res, Exception):
            if raise_errors:
                raise res
            eprint(str(res))
        else:
            items.append(res)
    click.echo(dump_results(items))


@main.command(short_help="use discogs to update sheet")
@click.option(
    "-r",
    "--resolve-to-master",
    "_resolve",
    is_flag=True,
    default=False,
    help="Attempt to resolve release IDs to Master on Discogs",
)
def discogs_update(_resolve: bool) -> None:
    """
    Update rows on the spreadsheet which just have a discogs link

    Gets information from the Discogs API
    """
    from .discogs_update import update_new_entries

    updated: int = update_new_entries(_resolve)
    eprint(f"Updated {updated} cells")


@main.command(short_help="update csv datafiles")
def update_csv_datafiles() -> None:
    """Updates the CSV files in data directory"""
    from .update_datafiles import update_datafiles

    update_datafiles()


@main.command(short_help="update spreadsheet.csv")
def generate_csv() -> None:
    """Generate the spreadsheet.csv file in the root dir"""
    from .update_datafiles import write_to_spreadsheets_csv_file

    with open(SETTINGS.BASE_SPREADSHEETS_CSV_FILE, "w") as f:
        write_to_spreadsheets_csv_file(f)
    sfile = SETTINGS.BASE_SPREADSHEETS_CSV_FILE
    home = os.path.expanduser("~")
    if sfile.startswith(home):
        sfile = "~" + sfile[len(home) :]  # noqa
    eprint(f"Wrote to {sfile} successfully.")


if TYPE_CHECKING:
    from .export import Album


def _export_data() -> list[Album]:
    from nextalbums.export import export_data

    albums = [a for a in export_data() if not isinstance(a, Exception)]

    if not albums:
        raise click.BadParameter("No albums found in spreadsheet")

    return albums


def _handle_album_cli(
    ctx: click.Context, param: click.Argument, value: str | None
) -> Album:
    from pyfzf import FzfPrompt

    albums = _export_data()

    tagged_albums = [
        f"{i}|{a.album_name}|{a.cover_artists}|{a.year}" for i, a in enumerate(albums)
    ]

    picked = FzfPrompt().prompt(
        tagged_albums, "--header='Pick an album' --layout=reverse --no-multi"
    )
    if not picked:
        raise click.BadParameter("No album picked")
    # get index from string and index into albums
    index = int(picked[0].split("|")[0])
    assert index < len(albums)

    picked_album = albums[index]
    click.echo(
        f"Picked {picked_album.album_name} - {picked_album.cover_artists} ({picked_album.year})",
        err=True,
    )

    return picked_album


today = str(date.today())


@main.command(short_help="mark album listened")
@click.option(
    "-d",
    "--date",
    "_dateval",
    type=str,
    default=today,
    show_default=True,
    help="Date to give album, pass 'no-edit' to not edit date",
)
@click.option(
    "--album",
    callback=_handle_album_cli,
    required=False,
    type=click.UNPROCESSED,
    help="Album to mark",
)
@click.option(
    "-s",
    "--score",
    type=float,
    required=True,
    prompt=True,
    help="Score to give album",
)
def mark_listened(album: Album, score: float, _dateval: str) -> None:
    assert 0 <= score <= 10, "Score must be between 0 and 10"
    # parse date to a datetime
    from .discogs_update import mark_listened

    dt: date | Literal["no-edit"]
    try:
        dt = datetime.strptime(_dateval, "%Y-%m-%d").date()
    except ValueError:
        if _dateval == "no-edit":
            dt = "no-edit"
        else:
            raise click.BadParameter("Date must be in YYYY-MM-DD format")

    from .core_gsheets import get_values

    worksheet_vals = get_values(sheetRange="Music!A:K", valueRenderOption="FORMULA")
    mark_listened(
        album,
        worksheet_vals,
        score=score,
        listened_on=dt if isinstance(dt, date) else None,
    )


@main.command(short_help="add new album")
@click.option(
    "--reason",
    type=click.Choice(get_args(CustomReason)),
    default="Manual",
    help="Reason for adding",
)
@click.argument("DISCOGS_URL")
def add_album(discogs_url: str, reason: CustomReason) -> None:
    """
    This *just* adds the URL to the spreadsheet, you'll need to run
    nextalbums discogs-update to actually update the sheet with the data
    """

    from .discogs_update import add_album, _fix_discogs_link

    fixed_url = _fix_discogs_link(discogs_url, resolve=True)
    if fixed_url != discogs_url:
        eprint(f"Resolved {discogs_url} to {fixed_url}")

    add_album(fixed_url, reason)


@main.command(short_help="override image upload")
@click.argument("ALBUM_ID", type=str)
@click.argument("IMAGE_URL", type=str)
def override_image_upload(album_id: str, image_url: str) -> None:
    """Override the image upload for a specific album"""
    from .image_proxy import (
        image_db,
        _get_image_bytes,
        _get_image_content_type,
        _upload_image,
        s3_bucket,
    )

    db = image_db()
    assert db.exists(
        album_id
    ), f"Album ID {album_id} not found in database, should be the key in the JSON file"

    eprint(f"Requesting image from {image_url}")
    image_bytes = _get_image_bytes(image_url)
    if image_bytes is None:
        eprint(f"Could not get image bytes from {image_url}")
        return

    content_type = _get_image_content_type(image_url)
    click.echo("Uploading image to S3 bucket...")
    _upload_image(image_bytes, s3_bucket, album_id, content_type)

    assert db.set(album_id, album_id)


if __name__ == "__main__":
    main(prog_name="nextalbums")
