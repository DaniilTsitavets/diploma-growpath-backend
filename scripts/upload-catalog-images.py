#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["boto3>=1.34", "pillow>=10.3"]
# ///
"""Upload the catalog images listed in media/catalog-images.csv to S3.

Each image becomes `<slug>.webp` (at most --max-side px on the long side) in the media bucket, next
to a `credits.json` with the author, licence and source page of every image. The CSV is the only
source: its `url` is an https link to download, or a file under media/source/ (stock sites that do
not allow downloads by link). Transparent images are put on a light neutral background.

Local LocalStack: set AWS_ENDPOINT_URL (`make catalog-images` does). Real S3: leave it unset and
use the normal AWS credential chain (AWS_PROFILE, environment variables, or a role); nothing is
stored here.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import ClientError
from PIL import Image, ImageOps, UnidentifiedImageError

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CSV = ROOT / "media" / "catalog-images.csv"
DEFAULT_BUCKET = "growpath-media"
LOCAL_SOURCE_DIR = ROOT / "media" / "source"
COLUMNS = ["slug", "url", "author", "license", "page"]
SLUG = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")
# Licences that allow commercial use; the author is always credited in credits.json: CC0 and public
# domain, CC BY, CC BY-SA, and the free licences of the stock sites we take photos from.
ALLOWED_LICENSE = re.compile(
    r"^(CC0|Public domain|CC BY \d\.\d|CC BY-SA \d\.\d"
    r"|Pixabay Content License|Pexels License|Unsplash License|Magnific Free License)",
    re.IGNORECASE,
)
BACKGROUND = (246, 246, 243)  # light neutral, used under transparent areas
MAX_DOWNLOAD_BYTES = 25 * 1024 * 1024
USER_AGENT = "growpath-catalog-images/1.0 (diploma project; open-licence images)"
CREDITS_KEY = "credits.json"
DOWNLOAD_ATTEMPTS = 6
PAUSE_BETWEEN_DOWNLOADS = 1.0  # image hosts such as Wikimedia throttle bursts of requests
PROCESSING_VERSION = "1"  # bump when the way images are processed changes, to re-upload everything


@dataclass(frozen=True)
class Entry:
    slug: str
    url: str
    author: str
    license: str
    page: str

    @property
    def key(self) -> str:
        return f"{self.slug}.webp"

    @property
    def credit_source(self) -> str:
        return self.page or self.url


def local_source(url: str) -> Path | None:
    """The file under media/source for a `url` that is not an https link, None for a link."""
    if url.startswith("https://"):
        return None
    path = (ROOT / url).resolve()
    if not path.is_relative_to(LOCAL_SOURCE_DIR.resolve()):
        raise ValueError(f"{url!r} is neither an https link nor a file under media/source/")
    if not path.is_file():
        raise ValueError(f"{url!r}: no such file")
    return path


def read_entries(path: Path) -> list[Entry]:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            raise ValueError(
                f"{path}: columns must be {','.join(COLUMNS)}, got {reader.fieldnames}"
            )
        entries = [Entry(**{name: (row[name] or "").strip() for name in COLUMNS}) for row in reader]
    seen: set[str] = set()
    for entry in entries:
        if not SLUG.match(entry.slug):
            raise ValueError(f"bad slug {entry.slug!r}: use lowercase letters, digits and dashes")
        if entry.slug in seen:
            raise ValueError(f"duplicate slug {entry.slug!r}")
        seen.add(entry.slug)
        try:
            source = local_source(entry.url)
        except ValueError as error:
            raise ValueError(f"{entry.slug}: {error}") from error
        if source is not None and not entry.page.startswith("https://"):
            raise ValueError(f"{entry.slug}: a local file needs the https `page` of its source")
        if not entry.author:
            raise ValueError(f"{entry.slug}: the author is required")
        if not ALLOWED_LICENSE.match(entry.license):
            raise ValueError(
                f"{entry.slug}: licence {entry.license!r} is not CC0, public domain, CC BY, "
                "CC BY-SA or the free licence of Pixabay, Pexels, Unsplash or Magnific"
            )
    if not entries:
        raise ValueError(f"{path}: no images listed")
    return entries


def fingerprint(entry: Entry, local: bytes | None, max_side: int, quality: int) -> str:
    """Changes with the link, or with the content of a local file, and with the processing."""
    source = hashlib.sha256(local).hexdigest() if local is not None else entry.url
    material = f"{PROCESSING_VERSION}|{source}|{max_side}|{quality}"
    return hashlib.sha256(material.encode()).hexdigest()


def retry_delay(error: urllib.error.HTTPError, attempt: int) -> float:
    retry_after = error.headers.get("Retry-After", "")
    if retry_after.isdigit():
        return min(float(retry_after), 120.0)
    return min(5.0 * 2**attempt, 120.0)


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(DOWNLOAD_ATTEMPTS):
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                data = response.read(MAX_DOWNLOAD_BYTES + 1)
            break
        except urllib.error.HTTPError as error:
            if error.code not in {429, 503} or attempt == DOWNLOAD_ATTEMPTS - 1:
                raise
            delay = retry_delay(error, attempt)
            print(
                f"    {error.code} from {urlparse(url).netloc}, waiting {delay:.0f}s",
                file=sys.stderr,
            )
            time.sleep(delay)
    if len(data) > MAX_DOWNLOAD_BYTES:
        raise ValueError(f"larger than {MAX_DOWNLOAD_BYTES // (1024 * 1024)} MB")
    return data


def flatten(image: Image.Image) -> Image.Image:
    """RGB image; transparent areas become the light background instead of black."""
    if image.mode in {"RGBA", "LA", "PA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, (*BACKGROUND, 255))
        return Image.alpha_composite(canvas, rgba).convert("RGB")
    return image.convert("RGB")


def to_webp(data: bytes, max_side: int, quality: int) -> bytes:
    try:
        image = Image.open(io.BytesIO(data))
        image = flatten(ImageOps.exif_transpose(image))
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError(f"not a usable image: {error}") from error
    image.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    out = io.BytesIO()
    image.save(out, format="WEBP", quality=quality, method=6)
    return out.getvalue()


def stored_fingerprint(s3, bucket: str, key: str) -> str | None:
    try:
        head = s3.head_object(Bucket=bucket, Key=key)
    except ClientError as error:
        if error.response["Error"]["Code"] in {"404", "NoSuchKey", "NotFound"}:
            return None
        raise
    return head.get("Metadata", {}).get("source-fingerprint")


def upload_image(s3, bucket: str, entry: Entry, max_side: int, quality: int) -> str:
    source = local_source(entry.url)
    local = source.read_bytes() if source is not None else None
    wanted = fingerprint(entry, local, max_side, quality)
    if stored_fingerprint(s3, bucket, entry.key) == wanted:
        return "unchanged"
    if local is None:
        time.sleep(PAUSE_BETWEEN_DOWNLOADS)
    body = to_webp(local if local is not None else download(entry.url), max_side, quality)
    s3.put_object(
        Bucket=bucket,
        Key=entry.key,
        Body=body,
        ContentType="image/webp",
        CacheControl="public, max-age=86400",
        Metadata={"source-fingerprint": wanted},
    )
    return f"uploaded {len(body) // 1024} KB"


def upload_credits(s3, bucket: str, entries: list[Entry]) -> str:
    credits = [
        {
            "slug": e.slug,
            "key": e.key,
            "author": e.author,
            "license": e.license,
            "source": e.credit_source,
        }
        for e in entries
    ]
    body = (json.dumps(credits, ensure_ascii=False, indent=2) + "\n").encode()
    try:
        if s3.get_object(Bucket=bucket, Key=CREDITS_KEY)["Body"].read() == body:
            return "unchanged"
    except ClientError as error:
        if error.response["Error"]["Code"] not in {"NoSuchKey", "404"}:
            raise
    s3.put_object(
        Bucket=bucket,
        Key=CREDITS_KEY,
        Body=body,
        ContentType="application/json; charset=utf-8",
        CacheControl="public, max-age=300",
    )
    return "uploaded"


def public_base_url(bucket: str, region: str) -> str:
    endpoint = os.environ.get("AWS_ENDPOINT_URL")
    if endpoint:
        return f"{endpoint.rstrip('/')}/{bucket}"
    return f"https://{bucket}.s3.{region}.amazonaws.com"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_CSV,
        help="list of images (default: media/catalog-images.csv)",
    )
    parser.add_argument(
        "--bucket", default=os.environ.get("MEDIA_BUCKET", DEFAULT_BUCKET), help="target bucket"
    )
    parser.add_argument(
        "--max-side", type=int, default=1200, help="longest side in pixels (default 1200)"
    )
    parser.add_argument("--quality", type=int, default=82, help="WebP quality (default 82)")
    args = parser.parse_args()

    try:
        entries = read_entries(args.csv)
    except (OSError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2

    session = boto3.session.Session()
    region = session.region_name
    if not region:
        print("error: set AWS_REGION (or AWS_DEFAULT_REGION)", file=sys.stderr)
        return 2
    s3 = session.client("s3")
    target = os.environ.get("AWS_ENDPOINT_URL") or f"real S3 ({region})"
    print(f"bucket {args.bucket} on {target}, {len(entries)} images from {args.csv}")

    failed = 0
    for entry in entries:
        try:
            outcome = upload_image(s3, args.bucket, entry, args.max_side, args.quality)
            print(f"  {entry.key}: {outcome}")
        except (ClientError, OSError, ValueError) as error:
            failed += 1
            print(f"  {entry.key}: FAILED {error}", file=sys.stderr)
    try:
        print(f"  {CREDITS_KEY}: {upload_credits(s3, args.bucket, entries)}")
    except ClientError as error:
        failed += 1
        print(f"  {CREDITS_KEY}: FAILED {error}", file=sys.stderr)

    print(f"public base URL: {public_base_url(args.bucket, region)}  (image: <base>/<slug>.webp)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
