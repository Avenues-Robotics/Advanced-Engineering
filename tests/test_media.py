#!/usr/bin/env python3
"""
Offline tests for media.py (downloading Notion-hosted images) and how
notion_md.py renders the resulting `media/<name>` references.

Run with:  python3 tests/test_media.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from media import copy_media, is_notion_hosted, localize  # noqa: E402
from notion_md import notion_markdown_to_html  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, detail: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)
        if detail:
            print("    " + detail.replace("\n", "\n    "))


class FakeResponse:
    def __init__(self, content: bytes, status: int = 200, content_type: str = "image/png"):
        self.content, self.status_code = content, status
        self.headers = {"Content-Type": content_type}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code} error")


class FakeSession:
    def __init__(self, responses: dict[str, FakeResponse]):
        self.responses, self.calls = responses, []

    def get(self, url: str, timeout: int = 0) -> FakeResponse:
        self.calls.append(url)
        return self.responses.get(url, FakeResponse(b"", 403))


S3 = "https://prod-files-secure.s3.us-west-2.amazonaws.com/ws/abc-123/image.png"
SIGNED = S3 + "?X-Amz-Expires=300&X-Amz-Signature=deadbeef"
SIGNED_LATER = S3 + "?X-Amz-Expires=300&X-Amz-Signature=cafef00d"


def main() -> int:
    check("signed S3 URL counts as Notion-hosted", is_notion_hosted(SIGNED))
    check("YouTube / ordinary URLs do not", not is_notion_hosted("https://youtu.be/x") and
          not is_notion_hosted("https://example.com/a.png"))

    tmp = Path(tempfile.mkdtemp())
    try:
        media_dir = tmp / "media"
        session = FakeSession({SIGNED: FakeResponse(b"PNGDATA"), SIGNED_LATER: FakeResponse(b"PNGDATA")})
        md = (f"Before\n![]({SIGNED})\n"
              f"<columns>\n\t<column>\n\t\t![]({SIGNED})\n\t</column>\n</columns>\n"
              "[keep](https://example.com/page) and ![ext](https://example.com/a.png)")
        out = localize(md, media_dir, session=session, log=lambda m: None)

        files = list(media_dir.iterdir())
        check("image downloaded once and saved with its extension",
              len(files) == 1 and files[0].suffix == ".png" and files[0].read_bytes() == b"PNGDATA")
        check("every occurrence rewritten to media/<name>",
              out.count(f"media/{files[0].name}") == 2 and "amazonaws" not in out, out)
        check("ordinary links and external images untouched",
              "[keep](https://example.com/page)" in out and "![ext](https://example.com/a.png)" in out, out)
        check("same file re-exported with a new signature is not downloaded again",
              (localize(f"![]({SIGNED_LATER})", media_dir, session=session, log=lambda m: None)
               == f"![](media/{files[0].name})") and session.calls == [SIGNED])

        logged: list[str] = []
        failed = localize("![](" + S3.replace("abc-123", "gone") + "?X-Amz-Signature=x)", media_dir,
                          session=session, log=logged.append)
        check("a failed download keeps the original URL and warns",
              "amazonaws" in failed and any("WARNING" in m for m in logged), failed)

        pdf = "https://prod-files-secure.s3.us-west-2.amazonaws.com/ws/f1/report.pdf?X-Amz-Signature=1"
        got = localize(f'<file src="{pdf}">Report</file>', media_dir,
                       session=FakeSession({pdf: FakeResponse(b"%PDF", content_type="application/pdf")}),
                       log=lambda m: None)
        check("<file src> attachments are localized too", 'src="media/' in got and got.count(".pdf") == 1, got)

        site = tmp / "site"
        site.mkdir()
        check("copy_media ships the folder with the site",
              copy_media(media_dir, site) == 2 and (site / "media" / files[0].name).exists())
        check("copy_media with no media dir is a no-op", copy_media(tmp / "nope", site) == 0)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    html = notion_markdown_to_html("![Diagram](media/0123456789abcdef.png)", base_path="/Advanced-Engineering")
    check("media/<name> images render under the site's base path",
          'src="/Advanced-Engineering/media/0123456789abcdef.png"' in html, html)
    html = notion_markdown_to_html("![](media/0123456789abcdef.png)")
    check("and at the site root when there is no base path", 'src="/media/0123456789abcdef.png"' in html, html)
    html = notion_markdown_to_html("![x](media/../../etc/passwd)")
    check("path traversal in a media reference is rejected", "etc/passwd" not in html, html)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
