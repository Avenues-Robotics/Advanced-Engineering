#!/usr/bin/env python3
"""
Offline self-test: exercises walker.py + render.py against an in-memory
fixture (tests/fake_notion.py) instead of the live Notion API, since this
environment has no real Notion integration token to test against.

Run with:  python3 tests/run_self_test.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from walker import build_tree  # noqa: E402
from render import render_site  # noqa: E402
from tests.fake_notion import build_fixture  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    status = "PASS" if condition else "FAIL"
    print(f"[{status}] {label}")
    if not condition:
        failures.append(label)


def main() -> int:
    client = build_fixture()
    root = build_tree(client, "root")

    # -- tree-shape assertions -------------------------------------------
    by_title = {}

    def index(node):
        by_title.setdefault(node.title, []).append(node)
        for c in node.children:
            index(c)

    index(root)

    check("root itself is unpublished", root.public_url is None)
    check("root.any_published() is True (has published descendants)", root.any_published())

    overview_nodes = by_title["Program Overview"]
    check("two 'Program Overview' pages found (title collision case)", len(overview_nodes) == 2)
    check("both 'Program Overview' pages are published", all(n.is_published for n in overview_nodes))

    reflections = by_title["Reflections and Engineering Notebook"][0]
    check("'Reflections...' page itself is unpublished", not reflections.is_published)
    check("'Reflections...' has a published child (Initial Reflection)", reflections.any_published())

    meeting_plans = by_title["Full Course Meeting Plans"][0]
    check("'Full Course Meeting Plans' has no published descendants", not meeting_plans.any_published())

    content_library = by_title["Content Library"][0]
    check("Content Library database node has a published row", content_library.any_published())
    check(
        "Content Library has one published + one unpublished row",
        sum(1 for r in content_library.children if r.is_published) == 1
        and sum(1 for r in content_library.children if not r.is_published) == 1,
    )

    # -- render assertions --------------------------------------------------
    markdown_by_id = dict(client.markdown)
    out_dir = Path(tempfile.mkdtemp()) / "site"
    log = render_site(
        root=root,
        markdown_by_id=markdown_by_id,
        output_dir=out_dir,
        site_title="Test Site",
        project_root=PROJECT_ROOT,
        base_path="",
    )
    print("\n".join(log))

    written_dirs = sorted(p.name for p in out_dir.iterdir() if p.is_dir() and p.name != "static")
    check(
        "exactly 4 published pages got their own directory",
        len(written_dirs) == 4,
    )
    check("slug collision produced program-overview and program-overview-2",
          "program-overview" in written_dirs and "program-overview-2" in written_dirs)
    check("unpublished 'Full Course Meeting Plans' produced no directory",
          "full-course-meeting-plans" not in written_dirs)
    check("unpublished 'Prior Art Research Notes Template' produced no directory",
          "prior-art-research-notes-template" not in written_dirs)
    check("'Content Library' itself (a database, never a page) produced no directory",
          "content-library" not in written_dirs)

    index_html = (out_dir / "index.html").read_text(encoding="utf-8")
    check("home page links to Initial Reflection (nested under inert 'Reflections...' label)",
          "Initial Reflection" in index_html and "initial-reflection" in index_html)
    check("home page shows 'Reflections and Engineering Notebook' as a label, not a dead link",
          '<span class="nav-label">Reflections and Engineering Notebook</span>' in index_html)
    check("home page does not mention the unpublished meeting-plans page",
          "Full Course Meeting Plans" not in index_html)

    overview_html = (out_dir / "program-overview" / "index.html").read_text(encoding="utf-8")
    check("rendered markdown became real HTML (h1) on the Program Overview page",
          "<h1" in overview_html and ">Program Overview</h1>" in overview_html)
    check("Program Overview page links back to the Notion source",
          "view in Notion" in overview_html)

    eng_reqs_html = (out_dir / "engineering-requirements" / "index.html").read_text(encoding="utf-8")
    check("database row page rendered its own markdown",
          "Every project begins with requirements" in eng_reqs_html)

    shutil.rmtree(out_dir.parent, ignore_errors=True)

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
