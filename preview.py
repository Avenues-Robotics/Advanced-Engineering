#!/usr/bin/env python3
"""
Build the site locally from a saved snapshot of the Notion content, so
formatting and feature changes can be tried without rerunning the GitHub Action.

Usage:
    python3 preview.py                # rebuild ./preview_site from snapshot/
    python3 preview.py --serve        # rebuild, then serve at http://localhost:8000/
    python3 preview.py --pull         # refresh snapshot/ from Notion first (needs NOTION_TOKEN)
    python3 preview.py --write-nav    # create nav.txt from the snapshot's current Notion order

The snapshot is snapshot/tree.json (the page tree with publish status) plus
snapshot/markdown/<page-id>.md (Notion's markdown export for each published
page) - the same inputs build_site.py feeds to render.py.
"""
from __future__ import annotations

import argparse
import errno
import functools
import http.server
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path

from layout import layout_from_tree, organize
from media import copy_media, localize
from notion_api import strip_dashes
from render import render_site
from walker import Node

PROJECT_ROOT = Path(__file__).resolve().parent
SNAPSHOT_DIR = PROJECT_ROOT / "snapshot"
OUTPUT_DIR = PROJECT_ROOT / "preview_site"
NAV_FILE = PROJECT_ROOT / "nav.txt"


def _node_from_dict(d: dict) -> Node:
    return Node(
        id=d["id"],
        title=d["title"],
        kind=d["kind"],
        public_url=d["public_url"],
        children=[_node_from_dict(c) for c in d["children"]],
    )


def _walk(node: Node):
    yield node
    for child in node.children:
        yield from _walk(child)


def load_snapshot(snapshot_dir: Path) -> tuple[Node, dict[str, str], str]:
    root = _node_from_dict(json.loads((snapshot_dir / "tree.json").read_text(encoding="utf-8")))
    meta = json.loads((snapshot_dir / "meta.json").read_text(encoding="utf-8"))
    markdown_by_id: dict[str, str] = {}
    for node in _walk(root):
        path = snapshot_dir / "markdown" / f"{strip_dashes(node.id).lower()}.md"
        if path.exists():
            markdown_by_id[node.id] = path.read_text(encoding="utf-8")
    return root, markdown_by_id, meta["site_title"]


def save_snapshot(root: Node, markdown_by_id: dict[str, str], site_title: str,
                  snapshot_dir: Path, source: str) -> None:
    md_dir = snapshot_dir / "markdown"
    if md_dir.exists():
        shutil.rmtree(md_dir)
    md_dir.mkdir(parents=True)
    for node_id, markdown in markdown_by_id.items():
        (md_dir / f"{strip_dashes(node_id).lower()}.md").write_text(markdown, encoding="utf-8")
    (snapshot_dir / "tree.json").write_text(
        json.dumps(root.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    (snapshot_dir / "meta.json").write_text(
        json.dumps({"site_title": site_title, "taken": date.today().isoformat(), "source": source},
                   indent=2) + "\n",
        encoding="utf-8",
    )


def pull_from_notion(snapshot_dir: Path) -> None:
    from build_site import iter_publishable, load_env_file
    from notion_api import NotionClient, extract_id_from_url
    from walker import build_tree

    load_env_file(PROJECT_ROOT / ".env")
    token = os.environ.get("NOTION_TOKEN")
    root_page_raw = os.environ.get("ROOT_PAGE_ID")
    if not token or not root_page_raw:
        sys.exit("ERROR: NOTION_TOKEN and ROOT_PAGE_ID must be set (via .env or the environment).")

    media_dir = snapshot_dir / "media"
    shutil.rmtree(media_dir, ignore_errors=True)

    client = NotionClient(token)
    print("Walking Notion tree ...")
    root = build_tree(client, extract_id_from_url(root_page_raw))
    to_fetch = list(iter_publishable(root)) + ([root] if root.is_published else [])
    markdown_by_id = {}
    for node in to_fetch:
        print(f"  fetching {node.title}")
        markdown = localize(client.get_page_markdown(node.id).get("markdown", ""), media_dir)
        if markdown.strip():
            markdown_by_id[node.id] = markdown
    site_title = os.environ.get("SITE_TITLE") or root.title
    save_snapshot(root, markdown_by_id, site_title, snapshot_dir, source="Notion API")
    print(f"Saved snapshot of {len(to_fetch)} published page(s) to {snapshot_dir}")


def build(snapshot_dir: Path, output_dir: Path, nav_file: Path = NAV_FILE) -> None:
    root, markdown_by_id, site_title = load_snapshot(snapshot_dir)
    root, slugs, home_id = organize(root, nav_file)
    log = render_site(root=root, markdown_by_id=markdown_by_id, output_dir=output_dir,
                      site_title=site_title, project_root=PROJECT_ROOT, base_path="",
                      slugs=slugs, home_id=home_id)
    print(log[0])
    copy_media(snapshot_dir / "media", output_dir)


def serve(directory: Path, port: int) -> None:
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    try:
        server = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler)
    except OSError as e:
        if e.errno != errno.EADDRINUSE:
            raise
        sys.exit(f"Port {port} is already in use - probably an earlier `preview.py --serve` that's "
                 f"still running. It serves the same folder, so just refresh "
                 f"http://localhost:{port}/ to see this build, or stop it with Ctrl+C in its "
                 f"terminal (or pass --port to use another port).")
    with server:
        print(f"Serving {directory} at http://localhost:{port}/  (Ctrl+C to stop)")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--pull", action="store_true", help="Refresh snapshot/ from Notion before building.")
    parser.add_argument("--serve", action="store_true", help="Serve the built site locally afterwards.")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--write-nav", action="store_true",
                        help="Create nav.txt from the snapshot's current Notion order, then exit.")
    args = parser.parse_args()

    if args.write_nav:
        if NAV_FILE.exists():
            sys.exit(f"{NAV_FILE.name} already exists - delete it first to regenerate it.")
        root, _, _ = load_snapshot(SNAPSHOT_DIR)
        NAV_FILE.write_text(layout_from_tree(root), encoding="utf-8")
        print(f"Wrote {NAV_FILE.name}. Rearrange its lines, then run: python3 preview.py --serve")
        return

    if args.pull:
        pull_from_notion(SNAPSHOT_DIR)
    build(SNAPSHOT_DIR, OUTPUT_DIR)
    if args.serve:
        serve(OUTPUT_DIR, args.port)


if __name__ == "__main__":
    main()
