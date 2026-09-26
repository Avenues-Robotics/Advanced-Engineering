#!/usr/bin/env python3
"""
Round-trips the fake Notion fixture through preview.save_snapshot /
load_snapshot and checks the committed snapshot/ still builds.

Run with:  python3 tests/test_snapshot.py
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from preview import SNAPSHOT_DIR, build, load_snapshot, save_snapshot  # noqa: E402
from tests.fake_notion import build_fixture  # noqa: E402
from walker import build_tree  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


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

        # The committed snapshot must stay loadable and buildable.
        build(SNAPSHOT_DIR, tmp / "real")
        pages = [p for p in (tmp / "real").iterdir() if p.is_dir() and p.name not in ("static", "media")]
        check("committed snapshot builds all 35 published pages", len(pages) == 35)
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
