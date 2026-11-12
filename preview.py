#!/usr/bin/env python3
"""
Build the site locally from a saved snapshot of the Notion content, so
formatting and feature changes can be tried without rerunning the GitHub Action.

Usage:
    python3 preview.py                # rebuild ./preview_site from snapshot/
    python3 preview.py --serve        # rebuild, then serve at http://localhost:8000/
    python3 preview.py --pull         # refresh snapshot/ from Notion first (needs NOTION_TOKEN)
    python3 preview.py --pull-page <notion link>   # refresh just that page (and its sub-pages)
    python3 preview.py --write-nav    # create nav.txt from the snapshot's current Notion order

The snapshot is snapshot/tree.json (the page tree with publish status) plus
snapshot/markdown/<page-id>.md (Notion's markdown export for each published
page) - the same inputs build_site.py feeds to render.py. To put changes
live, use publish.py.
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
        path = _markdown_path(snapshot_dir, node.id)
        if path.exists():
            markdown_by_id[node.id] = path.read_text(encoding="utf-8")
    return root, markdown_by_id, meta["site_title"]


def _markdown_path(snapshot_dir: Path, node_id: str) -> Path:
    return snapshot_dir / "markdown" / f"{strip_dashes(node_id).lower()}.md"


def _write_tree(root: Node, snapshot_dir: Path) -> None:
    (snapshot_dir / "tree.json").write_text(
        json.dumps(root.to_dict(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )


def save_snapshot(root: Node, markdown_by_id: dict[str, str], site_title: str,
                  snapshot_dir: Path, source: str) -> None:
    md_dir = snapshot_dir / "markdown"
    if md_dir.exists():
        shutil.rmtree(md_dir)
    md_dir.mkdir(parents=True)
    for node_id, markdown in markdown_by_id.items():
        _markdown_path(snapshot_dir, node_id).write_text(markdown, encoding="utf-8")
    _write_tree(root, snapshot_dir)
    (snapshot_dir / "meta.json").write_text(
        json.dumps({"site_title": site_title, "taken": date.today().isoformat(), "source": source},
                   indent=2) + "\n",
        encoding="utf-8",
    )


def _notion_client():
    from build_site import load_env_file
    from notion_api import NotionClient

    load_env_file(PROJECT_ROOT / ".env")
    token = os.environ.get("NOTION_TOKEN")
    if not token or not os.environ.get("ROOT_PAGE_ID"):
        sys.exit("ERROR: NOTION_TOKEN and ROOT_PAGE_ID must be set (via .env or the environment).")
    return NotionClient(token)


def _site_title(snapshot_dir: Path, root: Node) -> str:
    """SITE_TITLE if set, else the title the snapshot already has, else the root page's."""
    if os.environ.get("SITE_TITLE"):
        return os.environ["SITE_TITLE"]
    meta = snapshot_dir / "meta.json"
    if meta.exists():
        return json.loads(meta.read_text(encoding="utf-8"))["site_title"]
    return root.title


def pull_from_notion(snapshot_dir: Path, client=None) -> None:
    from build_site import iter_publishable
    from notion_api import extract_id_from_url
    from walker import build_tree

    client = client or _notion_client()
    root_page_raw = os.environ["ROOT_PAGE_ID"]

    media_dir = snapshot_dir / "media"
    shutil.rmtree(media_dir, ignore_errors=True)

    print("Walking Notion tree ...")
    root = build_tree(client, extract_id_from_url(root_page_raw))
    to_fetch = list(iter_publishable(root)) + ([root] if root.is_published else [])
    markdown_by_id = {}
    for node in to_fetch:
        print(f"  fetching {node.title}")
        markdown = localize(client.get_page_markdown(node.id).get("markdown", ""), media_dir)
        if markdown.strip():
            markdown_by_id[node.id] = markdown
    site_title = _site_title(snapshot_dir, root)
    save_snapshot(root, markdown_by_id, site_title, snapshot_dir, source="Notion API")
    print(f"Saved snapshot of {len(to_fetch)} published page(s) to {snapshot_dir}")


def _find_with_parent(node: Node, bare_id: str, parent: Node | None = None) -> tuple[Node | None, Node | None]:
    if strip_dashes(node.id).lower() == bare_id:
        return node, parent
    for child in node.children:
        found = _find_with_parent(child, bare_id, node)
        if found[0]:
            return found
    return None, None


def _new_page_parent(root: Node, page: dict) -> Node | None:
    """Where a page that isn't in the snapshot yet belongs, from its Notion parent."""
    parent = page.get("parent", {})
    parent_id = parent.get("page_id") or parent.get("database_id")
    if not parent_id:
        return None
    return _find_with_parent(root, strip_dashes(parent_id).lower())[0]


def pull_page(snapshot_dir: Path, page_ref: str, client=None) -> tuple[str, list[str]]:
    """Re-pull one page (a share link, URL or ID) and its sub-pages into the
    snapshot, leaving the rest of it alone. Returns (its title, titles of the
    published pages fetched)."""
    from build_site import iter_publishable
    from notion_api import NotionAPIError, extract_id_from_url
    from walker import TreeWalker

    client = client or _notion_client()
    bare_id = extract_id_from_url(page_ref).lower()
    if len(bare_id) != 32 or any(c not in "0123456789abcdef" for c in bare_id):
        sys.exit(f"ERROR: couldn't find a Notion page ID in {page_ref!r}.")

    root, _, _ = load_snapshot(snapshot_dir)
    old, parent = _find_with_parent(root, bare_id)
    if old is root:
        sys.exit("ERROR: that's the root page - run a full pull instead.")

    walker = TreeWalker(client)
    if old is not None and old.kind == "database":
        new = walker._build_database_node(old.id, old.title)
    else:
        try:
            page = client.get_page(bare_id)
        except NotionAPIError as e:
            sys.exit(f"ERROR: Notion couldn't find that page ({e.status}). Check the link, and that "
                     "the page is shared with your integration.")
        new = walker._build_page_node(page["id"], fallback_title=None)
        if old is not None:
            new.kind = old.kind  # keep "database_row"; the walker builds every page as "page"
        else:
            parent = _new_page_parent(root, page)
            if parent is None:
                sys.exit(f'ERROR: "{new.title}" isn\'t in the snapshot and its parent isn\'t either - '
                         "run a full pull instead.")

    if old is not None:
        parent.children[parent.children.index(old)] = new
        for node in _walk(old):
            _markdown_path(snapshot_dir, node.id).unlink(missing_ok=True)
    else:
        parent.children.append(new)

    refreshed = []
    _markdown_path(snapshot_dir, new.id).parent.mkdir(parents=True, exist_ok=True)
    for node in iter_publishable(new):
        print(f"  fetching {node.title}")
        markdown = localize(client.get_page_markdown(node.id).get("markdown", ""), snapshot_dir / "media")
        if markdown.strip():
            _markdown_path(snapshot_dir, node.id).write_text(markdown, encoding="utf-8")
        refreshed.append(node.title.strip())
    _write_tree(root, snapshot_dir)
    if not refreshed:
        print(f'  "{new.title.strip()}" isn\'t published in Notion, so it (and anything under it) '
              "will be left off the site.")
    return new.title.strip(), refreshed


def build(snapshot_dir: Path, output_dir: Path, nav_file: Path = NAV_FILE, base_path: str = "") -> None:
    root, markdown_by_id, site_title = load_snapshot(snapshot_dir)
    root, slugs, home_id = organize(root, nav_file)
    log = render_site(root=root, markdown_by_id=markdown_by_id, output_dir=output_dir,
                      site_title=site_title, project_root=PROJECT_ROOT, base_path=base_path,
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
    parser.add_argument("--pull-page", metavar="LINK",
                        help="Refresh one page (and its sub-pages) from Notion before building.")
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
    elif args.pull_page:
        pull_page(SNAPSHOT_DIR, args.pull_page)
    build(SNAPSHOT_DIR, OUTPUT_DIR)
    if args.serve:
        serve(OUTPUT_DIR, args.port)


if __name__ == "__main__":
    main()
