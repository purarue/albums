import os
import io
import json
import time
import shutil
import atexit
from typing import cast
from pathlib import Path
from urllib.parse import urlparse
from functools import cache

import click
import backoff
import httpx
import boto3  # type: ignore[import]
import pickledb  # type: ignore[import]


from . import SETTINGS
from .common import eprint

client = boto3.client(
    "s3",
    aws_access_key_id=os.environ["S3_ACCESS_KEY"],
    aws_secret_access_key=os.environ["S3_SECRET_KEY"],
)

AUTO_DUMP = True

image_data_file = SETTINGS.IMAGE_DATA


def setup_db() -> pickledb.PickleDB:
    backup = f"{image_data_file}.bak"
    try:
        pdb = pickledb.load(image_data_file, auto_dump=AUTO_DUMP)
    except Exception:
        assert Path(backup).exists()
        eprint(
            f"image_proxy: failed to load {image_data_file}, restoring from backup", err=True
        )
        shutil.copy(backup, image_data_file)
        pdb = pickledb.load(image_data_file, auto_dump=AUTO_DUMP)

    Path(backup).write_text(json.dumps(pdb.db))

    if not AUTO_DUMP:
        atexit.register(lambda: cast(object, pdb.dump()))

    eprint(f"image_proxy: loaded {len(pdb.db)} entries from {image_data_file} {AUTO_DUMP=} ")
    return pdb


@cache
def image_db() -> pickledb.PickleDB:
    return setup_db()


s3_prefix = os.environ["USE_S3_URL"]
s3_bucket = os.environ["S3_BUCKET"]


def _prefix_url(path: str) -> str:
    return f"{s3_prefix}/{path}"


@backoff.on_exception(backoff.expo, httpx.HTTPError, max_tries=3)
def _get_image_bytes(url: str) -> bytes | None:
    with httpx.Client() as client:
        resp = client.get(url)
        if resp.status_code == 429:
            eprint(f"image_proxy: got 429 for {url}", err=True)
            time.sleep(60)
            resp.raise_for_status()
        elif resp.status_code >= 400:
            eprint(f"image_proxy: got {resp.status_code} for {url}", err=True)
            return None
        resp.raise_for_status()
        return resp.content


CONTENT_MAPPING = {
    "jpeg": "image/jpg",
    "jpg": "image/jpg",
    "png": "image/png",
}


def _get_image_content_type(url: str) -> str:
    path = urlparse(url).path
    ext = Path(path).suffix.strip(".")
    assert ext in {"jpeg", "jpg", "png"}, f"invalid extension {ext}"
    return CONTENT_MAPPING[ext]


def _upload_image(
    image_bytes: bytes, s3_bucket: str, key: str, content_type: str
) -> None:
    client.upload_fileobj(
        io.BytesIO(image_bytes),
        Bucket=s3_bucket,
        Key=key,
        ExtraArgs={"ContentType": content_type},
    )


def proxy_image(
    url: str, album_id: str, discogs_url: str, retry: bool = True
) -> str | None:
    db = image_db()
    if db.exists(album_id):
        resp = db.get(album_id)
        if resp == 404:
            return None
        assert isinstance(resp, str)
        return _prefix_url(resp)
    else:
        eprint(f"image_proxy: uploading {album_id} {url}", err=True)
        time.sleep(2)
        path = urlparse(url).path
        content_type = _get_image_content_type(url)

        # download image to memory
        image_bytes = _get_image_bytes(url)
        if image_bytes is None:
            # set BG=1 to skip prompts, then can run without setting the envvar to prompt all at once
            if "BG" not in os.environ:
                prompted_url = click.prompt(f"Image URL for {album_id}").strip()
                if prompted_url.strip():
                    return proxy_image(prompted_url, album_id, discogs_url, retry=False)

            # db.set(album_id, 404)
            return None

        # slugify path
        from .discogs_update import slugify

        key = slugify(path.replace("/", "_"), allow_period=True)

        # upload to aws s3
        _upload_image(image_bytes, s3_bucket, key, content_type)

        assert db.set(album_id, key)
        https_url = _prefix_url(key)
        eprint(f"image_proxy: uploaded to {https_url}")

        return https_url
