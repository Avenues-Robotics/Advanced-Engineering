"""
Save Notion-hosted images and files next to the site.

Notion serves uploaded files from signed URLs that expire about 5 minutes
after the page is exported, so linking to them directly breaks the site
almost immediately. Right after a page's markdown is fetched, `localize`
downloads each such file into a media directory and rewrites the markdown to
`media/<name>`; `copy_media` then ships that directory with the built site.
"""
from __future__ import annotations

import hashlib
import re
import shutil
from pathlib import Path
from typing import Callable
from urllib.parse import unquote, urlparse

import requests

_URL_IN_MARKDOWN = re.compile(
    r'(!\[[^\]]*\]\(|<(?:file|pdf|audio|video|embed)\b[^>]*?\bsrc=")(https://[^)"\s]+)'
)
_EXT_BY_TYPE = {
    "image/png": ".png", "image/jpeg": ".jpg", "image/gif": ".gif", "image/webp": ".webp",
    "image/svg+xml": ".svg", "application/pdf": ".pdf", "video/mp4": ".mp4", "audio/mpeg": ".mp3",
}
_KNOWN_EXTS = set(_EXT_BY_TYPE.values()) | {".jpeg", ".mov", ".webm", ".wav", ".txt", ".csv"}


def is_notion_hosted(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if host == "file.notion.so" or host.endswith("notion-static.com"):
        return True
    return host.endswith(".amazonaws.com") and "X-Amz-Signature" in url


def _filename(url: str, content_type: str | None) -> str:
    parsed = urlparse(url)
    # The signature changes on every export, but host + path identify the upload.
    digest = hashlib.sha1((parsed.netloc + parsed.path).encode()).hexdigest()[:16]
    ext = Path(unquote(parsed.path)).suffix.lower()
    if ext not in _KNOWN_EXTS:
        ext = _EXT_BY_TYPE.get((content_type or "").split(";")[0].strip().lower(), ".bin")
    return f"{digest}{ext}"


def _existing(media_dir: Path, url: str) -> Path | None:
    parsed = urlparse(url)
    digest = hashlib.sha1((parsed.netloc + parsed.path).encode()).hexdigest()[:16]
    return next(iter(media_dir.glob(f"{digest}.*")), None) if media_dir.exists() else None


def localize(markdown: str, media_dir: Path, *, session: requests.Session | None = None,
             log: Callable[[str], None] = print) -> str:
    """Download Notion-hosted files referenced in `markdown` into `media_dir` and
    return the markdown pointing at `media/<name>`. Files that can't be
    downloaded keep their original URL (and are reported through `log`)."""
    session = session or requests.Session()

    def replace(match: re.Match) -> str:
        prefix, url = match.groups()
        if not is_notion_hosted(url):
            return match.group(0)
        saved = _existing(media_dir, url)
        if saved is None:
            try:
                resp = session.get(url, timeout=60)
                resp.raise_for_status()
            except requests.RequestException as exc:
                log(f"  WARNING: could not download {urlparse(url).path.rsplit('/', 1)[-1]}: {exc}")
                return match.group(0)
            media_dir.mkdir(parents=True, exist_ok=True)
            saved = media_dir / _filename(url, resp.headers.get("Content-Type"))
            saved.write_bytes(resp.content)
        return f"{prefix}media/{saved.name}"

    return _URL_IN_MARKDOWN.sub(replace, markdown)


def copy_media(media_dir: Path, output_dir: Path) -> int:
    """Copy the media directory into the built site. Returns the file count."""
    if not media_dir.exists():
        return 0
    shutil.copytree(media_dir, output_dir / "media", dirs_exist_ok=True)
    return sum(1 for p in media_dir.iterdir() if p.is_file())
