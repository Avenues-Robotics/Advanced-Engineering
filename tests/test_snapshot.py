#!/usr/bin/env python3
"""
Round-trips the fake Notion fixture through preview.save_snapshot /
load_snapshot, checks single-page pulls (preview.pull_page), and checks the
committed snapshot/ still builds.

Run with:  python3 tests/test_snapshot.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from preview import NAV_FILE, SNAPSHOT_DIR, build, load_snapshot, pull_page, save_snapshot  # noqa: E402
from tests.fake_notion import FakeNotionClient, build_fixture  # noqa: E402
from walker import build_tree  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


# Single-page pulls take real Notion IDs (32 hex characters).
ROOT, GUIDE, STEP, NEW, LIB, ROW, OTHER, LATE = (c * 32 for c in "a123d45f")


def _dashed(bare: str) -> str:
    return f"{bare[:8]}-{bare[8:12]}-{bare[12:16]}-{bare[16:20]}-{bare[20:]}"


def check_pull_page(snap: Path) -> None:
    c = FakeNotionClient()
    c.add_page(ROOT, "Root", published=False)
    for page_id, title, parent in [(GUIDE, "Guide", ROOT), (STEP, "Step 1", GUIDE), (OTHER, "Other", ROOT)]:
        c.add_page(page_id, title, published=True, markdown=f"{title} v1")
        c.add_child_page_block(parent, page_id, title)
    c.add_child_database_block(ROOT, LIB, "Library")
    c.add_database_row(LIB, ROW, "Row", published=True, markdown="Row v1")
    save_snapshot(build_tree(c, ROOT), dict(c.markdown), "Test Site", snap, source="test")

    def markdown(page_id: str) -> str | None:
        path = snap / "markdown" / f"{page_id}.md"
        return path.read_text(encoding="utf-8") if path.exists() else None

    # Edit a sub-page, add a new one, and change a page that won't be pulled.
    c.markdown[STEP] = "Step 1 v2"
    c.markdown[OTHER] = "Other v2"
    c.add_page(NEW, "Step 2", published=True, markdown="Step 2 v1")
    c.add_child_page_block(GUIDE, NEW, "Step 2")

    title, refreshed = pull_page(snap, f"https://example.notion.site/Guide-{GUIDE}?pvs=4", client=c)
    root, _, site_title = load_snapshot(snap)
    guide = next(n for n in root.children if n.id == GUIDE)
    check("single-page pull refreshes the page and its sub-pages",
          title == "Guide" and refreshed == ["Guide", "Step 1", "Step 2"] and markdown(STEP) == "Step 1 v2")
    check("single-page pull picks up a new sub-page",
          [n.title for n in guide.children] == ["Step 1", "Step 2"] and markdown(NEW) == "Step 2 v1")
    check("single-page pull leaves other pages alone",
          markdown(OTHER) == "Other v1" and site_title == "Test Site"
          and [n.id for n in root.children] == [GUIDE, OTHER, LIB])

    c.markdown[ROW] = "Row v2"
    pull_page(snap, _dashed(ROW), client=c)
    root, _, _ = load_snapshot(snap)
    row = root.children[2].children[0]
    check("a database row stays a database row", row.kind == "database_row" and markdown(ROW) == "Row v2")

    c.add_page(LATE, "Late Page", published=True, markdown="Late v1")
    c.add_child_page_block(OTHER, LATE, "Late Page")
    pull_page(snap, LATE, client=c)
    root, _, _ = load_snapshot(snap)
    check("a page that isn't in the snapshot yet goes under its Notion parent",
          [n.id for n in root.children[1].children] == [LATE] and markdown(LATE) == "Late v1")

    c.pages[STEP]["public_url"] = None
    pull_page(snap, GUIDE, client=c)
    check("a page unpublished in Notion loses its saved content", markdown(STEP) is None)


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    try:
        client = build_fixture()
        root = build_tree(client, "root")
        save_snapshot(root, dict(client.markdown), "Test Site", tmp / "snap", source="test")
        loaded_root, markdown, title = load_snapshot(tmp / "snap")

        check("site title survives the round trip", title == "Test Site")
        check("tree survives the round trip", loaded_root.to_dict() == root.to_dict())
        check("markdown for a published page survives",
              markdown["overview"] == client.markdown["overview"])

        build(tmp / "snap", tmp / "out", nav_file=tmp / "no-nav.txt")
        check("snapshot builds a site", (tmp / "out" / "program-overview" / "index.html").exists())

        check_pull_page(tmp / "single")

        # The committed snapshot must stay loadable and buildable (against a
        # copy of nav.txt - building rewrites the list at the bottom of it).
        nav_copy = tmp / "nav.txt"
        shutil.copy(NAV_FILE, nav_copy)
        build(SNAPSHOT_DIR, tmp / "real", nav_file=nav_copy, base_path="/Advanced-Engineering")
        pages = [p for p in (tmp / "real").iterdir() if p.is_dir() and p.name not in ("static", "media")]
        home = (tmp / "real" / "index.html").read_text(encoding="utf-8")
        check("committed snapshot builds, with the base path in links",
              len(pages) > 0 and 'href="/Advanced-Engineering/static/style.css"' in home)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

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
