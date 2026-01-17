import json
import re
import warnings
from datetime import date, datetime
from time import strptime
from pathlib import Path
from typing import NamedTuple, Any
from collections.abc import Iterator


from .common import WorksheetData, eprint, parse_url_type

DROPPED = {"cant find", "nope"}

Json = dict[str, Any]

Note = str

DATE_REGEX = re.compile(r"(\d{4})-(\d{2})-(\d{2})")


class Album(NamedTuple):
    score: float | None
    note: Note | None  # .e.g some reason I couldn't listen to an album, can't find it
    listened_on: date | None
    album_name: str
    album_artwork_url: str
    cover_artists: str
    discogs_url: str | None
    year: int
    reasons: list[str]
    genres: list[str]
    styles: list[str]
    main_artists: list[int]

    @property
    def dt(self) -> datetime | None:
        if self.listened_on:
            return datetime.combine(self.listened_on, datetime.min.time())
        else:
            return None

    @property
    def dropped(self) -> bool:
        return self.note in DROPPED

    @property
    def listened(self) -> bool:
        """Whether or not I've listened to this album"""
        if self.dropped:
            return False
        elif self.score is None:
            return False
        elif self.listened_on is None:
            return False
        return True

    def master(self) -> Json:
        from .discogs_cache import fetch_discogs

        assert self.discogs_url is not None
        _type, _ = parse_url_type(self.discogs_url)
        assert _type == "master"
        return fetch_discogs(self.discogs_url).metadata

    def has_master(self) -> bool:
        try:
            self.master()
            return True
        except AssertionError:
            return False

    def release(self) -> Json:
        from .discogs_cache import fetch_discogs

        assert self.discogs_url is not None
        _type, _ = parse_url_type(self.discogs_url)
        if _type == "master" and self.has_master():
            release_id = self.master()["main_release"]
            main_release_url = f"https://discogs.com/release/{int(release_id)}"
            return fetch_discogs(main_release_url).metadata
        else:
            assert _type == "release"
            return fetch_discogs(self.discogs_url).metadata

    def has_release(self) -> bool:
        try:
            self.release()
            return True
        except AssertionError:
            return False

    def datas(self) -> list[Json]:
        _datas = []
        if self.has_master():
            _datas.append(self.master())
        if self.has_release():
            _datas.append(self.release())
        return _datas

    @staticmethod
    def _parse_release_date(rel_raw: str | int) -> date | None:
        if isinstance(rel_raw, int):
            rel_raw = str(rel_raw)
        assert isinstance(rel_raw, str), f"{rel_raw} {type(rel_raw)}"
        if str(rel_raw).isdigit() and len(str(rel_raw)) == 4:
            return date(year=int(rel_raw), month=1, day=1)
        else:
            if match := re.match(DATE_REGEX, rel_raw):
                year, month, day = (int(g) for g in match.groups())
                # sometimes the month and day aren't specified, so
                # default to 1
                if day == 0:
                    day = 1
                if month == 0:
                    month = 1
                return date(year=year, month=month, day=day)
        return None

    @staticmethod
    def _traverse_release_dates(blob: Json) -> date | None:
        if "release" in blob:
            if dt := Album._parse_release_date(blob["release"]):
                return dt
        elif "year" in blob:
            if dt := Album._parse_release_date(blob["year"]):
                return dt
        return None

    def release_date(self) -> date:
        if self.has_master() and self.has_release():
            # main release could possibly be different than the
            # main 'year' on the master. If they're the same year,
            # use the release 'released' data, since its more
            # accurate
            if "year" in self.master() and "released" in self.release():
                master_dt = Album._parse_release_date(self.master()["year"])
                release_dt = Album._parse_release_date(self.release()["released"])
                if master_dt is not None and release_dt is not None:
                    if master_dt.year == release_dt.year:
                        return release_dt
        # otherwise, use the year if that exists
        elif self.has_master():
            if parsed := Album._traverse_release_dates(self.master()):
                return parsed
        elif self.has_release():
            if parsed := Album._traverse_release_dates(self.release()):
                return parsed
        return date(year=self.year, month=1, day=1)


SEPARATORS = {";", "|"}


def _split_separated(data: str) -> list[str]:
    parts: list[str] = []
    for sep in SEPARATORS:
        if sep in data:
            for part in data.split(sep):
                if ps := part.strip():
                    parts.append(ps)
            return parts
    if data.strip():
        return [data.strip()]
    else:
        return []


# optionally can provide a datasource, which returns
# the same data as get_values with the below parameters
# this is so that I have the option to cache the response
# from the get_values function elsewhere, so this
# doesn't have to make the API call every time
def export_data(
    data_source: WorksheetData | None = None,
    remove_header: bool = True,
) -> Iterator[Exception | Album]:
    from .discogs_update import remove_image_formula
    from .core_gsheets import get_values

    import xlrd  # type: ignore[import]

    worksheet_vals: WorksheetData
    if data_source is None:
        worksheet_vals = get_values(sheetRange="Music!A:K", valueRenderOption="FORMULA")
    else:
        worksheet_vals = data_source
    if remove_header:
        worksheet_vals = worksheet_vals[1:]
    for vals in worksheet_vals:
        # pad list in case there are missing values
        while len(vals) < 11:
            vals.append("")
        # destructure row
        (
            score,
            album_name,
            artists_on_album_cover,
            year,
            dateval,
            reasons,
            album_artwork,
            discogs_url,
            martists,
            genres,
            styles,
        ) = list(map(str.strip, map(str, vals)))
        fscore: float | None = None
        note: str | None = None
        try:
            fscore = float(score)
        except ValueError:
            # couldn't parse, probably haven't listened to this yet
            # edge case, where I can't find an album online
            if score in DROPPED:
                note = score
            else:
                if score.strip():
                    eprint(f"Unexpected data in score field: {score} from {vals}")
        try:
            # default to 1
            if year == "":
                warnings.warn("Using '1' as default year")
                year = "1"
            iyear = int(year)
        except ValueError as e:
            # yield the error upwards and continue parsing, instead of crashing
            yield e
            continue

        listened_on: date | None = None
        if dateval:
            if fscore is None:
                yield RuntimeError(
                    f"{album_name} ({artists_on_album_cover}) has no 'score' but a 'listened on' date"
                )
                continue
            try:
                listened_on = xlrd.xldate_as_datetime(int(dateval), 0).date()
            except ValueError as v:
                yield v
                continue
        else:
            if fscore is not None:
                yield RuntimeError(
                    f"{album_name} {artists_on_album_cover} has no 'listened on' date but has a 'score'"
                )
                continue
        artwork: str
        try:
            artwork = remove_image_formula(album_artwork)
        except AssertionError:
            artwork = ""
            warnings.warn(
                f"Warning. No Album Artwork extracted from '{album_artwork}' for '{album_name}'"
            )

        main_artists = [int(u) for u in _split_separated(martists)]

        yield Album(
            score=fscore,
            note=note,
            album_name=album_name,
            cover_artists=artists_on_album_cover,
            year=iyear,
            listened_on=listened_on,
            album_artwork_url=artwork,
            discogs_url=discogs_url.strip() if discogs_url.strip() else None,
            reasons=_split_separated(reasons),
            genres=_split_separated(genres),
            styles=_split_separated(styles),
            main_artists=main_artists,
        )


def default(o: Any) -> Any:
    if isinstance(o, datetime) or isinstance(o, date):
        return str(o)
    raise TypeError(f"Couldn't serialize {o} of type {type(o)}")


def dump_results(data: Any) -> str:
    import simplejson

    return str(
        simplejson.dumps(
            data,
            default=default,
            namedtuple_as_object=True,
            sort_keys=True,
        )
    )


# helper to read the dump back into list of python object
def read_dump(p: Path) -> Iterator[Album]:
    for blob in json.loads(p.read_text()):
        fscore: float | None = None
        if blob["score"] is not None:
            fscore = float(blob["score"])
        dlistened_on: date | None = None
        if blob["listened_on"] is not None:
            d = strptime(blob["listened_on"], r"%Y-%m-%d")
            dlistened_on = date(year=d.tm_year, month=d.tm_mon, day=d.tm_mday)
        yield Album(
            score=fscore,
            note=blob["note"],
            album_name=blob["album_name"],
            cover_artists=blob["cover_artists"],
            year=int(blob["year"]),
            listened_on=dlistened_on,
            album_artwork_url=blob["album_artwork_url"],
            discogs_url=blob["discogs_url"],
            reasons=blob["reasons"],
            genres=blob["genres"],
            styles=blob["styles"],
            main_artists=blob["main_artists"],
        )
