"""
uses https://github.com/purarue/url_cache to cache
discogs API data locally
"""

import os
import functools
from functools import cache
from typing import Any, Dict
from datetime import datetime

import requests
import backoff  # type: ignore[import]
import discogs_client  # type: ignore[import]
from discogs_client.exceptions import HTTPError  # type: ignore[import]
from url_cache.core import URLCache
from url_cache.model import Summary

from . import SETTINGS
from .common import eprint, parse_url_type


def backoff_hdlr(details):
    print(
        "Backing off {wait:0.1f} seconds afters {tries} tries "
        "calling function {target} with args {args} and kwargs "
        "{kwargs}".format(**details)
    )


@functools.lru_cache(1)
def discogsClient() -> discogs_client.Client:
    return discogs_client.Client(
        SETTINGS.DISCOGS_CREDS["user_agent"], user_token=SETTINGS.DISCOGS_CREDS["token"]
    )


@backoff.on_exception(
    lambda: backoff.constant(interval=10),
    (requests.exceptions.RequestException, HTTPError),
    max_tries=5,
    on_backoff=backoff_hdlr,
)
def discogs_get(_type: str, _id: int, /) -> Dict[str, Any]:
    """Gets data from discogs API."""
    eprint(f"[Discogs] Requesting {_type} {_id}")
    if _type == "master":  # if Master
        resp = discogsClient().master(_id)
    elif _type == "release":
        resp = discogsClient().release(_id)
    else:
        raise RuntimeError(f"Unknown discogs request type: {_type}")
    resp.refresh()
    data = dict(resp.data)
    return data


class DiscogsCache(URLCache):
    """
    Subclass URLCache to handle caching the Summary data to a local directory cache
    """

    def request_data(self, url: str) -> Summary:  # type: ignore[override]
        """
        Override the request data function to fetch from the discogs API
        """
        self.sleep()
        uurl = self.preprocess_url(url)
        assert uurl.strip(), f"No url: '{url}'"
        _type, _id = parse_url_type(uurl)
        data = discogs_get(_type, int(_id))
        assert len(data.keys()) > 3, str(data)
        # raises before it returns summary which would then get saved by 'URLCache.get'
        return Summary(url=uurl, data={}, metadata=data, timestamp=datetime.now())


@cache
def discogs_urlcache() -> DiscogsCache:
    default_local = os.path.join(os.environ["HOME"], ".local", "share")
    cache_dir = os.path.join(default_local, "discogs_urlcache")
    cache_dir_path = os.environ.get("DISCOGS_CACHE_DIR", cache_dir)
    # refresh data every 6 weeks
    return DiscogsCache(
        cache_dir=cache_dir_path, sleep_time=1, options={"expiry_duration": "32w"}
    )


def _fetch_discogs(url: str, refresh: bool = False) -> Summary:
    uc = discogs_urlcache()
    if refresh:
        uurl = uc.preprocess_url(url)
        data = uc.request_data(uurl)
        uc.summary_cache.put(uurl, data)
    else:
        data = uc.get(url)
    _type, _id = parse_url_type(url)
    # if this is the master release, request the main release for this as well
    if _type == "master":
        assert "main_release" in data.metadata, str(data)
        main_release_url = (
            f"https://discogs.com/release/{int(data.metadata['main_release'])}"
        )
        if not uc.summary_cache.has(main_release_url):
            eprint(f"[Discogs] Requesting main release for {_id}")
        uc.get(main_release_url)
    return data


@cache
def fetch_discogs(url: str) -> Summary:
    return _fetch_discogs(url)
