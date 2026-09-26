#!/usr/bin/env python3
"""
Rebuild (and optionally deploy) a GitBook-style static site from a Notion
page tree, including only pages and database rows that are currently
published to the web in Notion (i.e. have a public_url).

Usage:
    python build_site.py --dry-run          # see what would be included, no writes
    python build_site.py                    # build the site into OUTPUT_DIR
    python build_site.py --deploy           # build, then git add/commit/push OUTPUT_DIR

Configuration comes from a .env file (see .env.example) or real environment
variables. Required:
    NOTION_TOKEN     - internal integration token (kept out of this repo)
    ROOT_PAGE_ID      - the "Advanced Engineering Electives" page, as an ID or URL

Optional:
    SITE_TITLE        - defaults to the root page's own title
    OUTPUT_DIR         - defaults to ./site
    BASE_PATH          - url path prefix, e.g. "/advanced-engineering-electives"
                          for a GitHub Pages *project* site (username.github.io/repo).
                          Leave blank for a user/org site or a custom domain.
    GIT_COMMIT_MESSAGE  - defaults to "Update site from Notion"
"""
from __future__ import annotations

import argparse
import os
import sys
import subprocess
from pathlib import Path

from layout import organize
from media import copy_media, localize
from notion_api import NotionClient, extract_id_from_url
from walker import Node, build_tree
from render import render_site

PROJECT_ROOT = Path(__file__).resolve().parent
MEDIA_DIR = PROJECT_ROOT / ".media_cache"


def load_env_file(path: Path) -> None:
    """Minimal .env loader (KEY=VALUE per line) - avoids a hard dependency
    on python-dotenv. Does not override variables already set in the real
    environment."""
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def collect_publish_report(node: Node, depth: int = 0, lines: list[str] | None = None) -> list[str]:
    if lines is None:
        lines = []
    indent = "  " * depth
    if node.kind in ("database", "section"):
        mark = "[section]"
    else:
        mark = "[PUBLISHED]" if node.is_published else "[-]"
    lines.append(f"{indent}{mark} {node.title}")
    for child in node.children:
        collect_publish_report(child, depth + 1, lines)
    return lines


def iter_publishable(node: Node):
    if node.kind in ("page", "database_row") and node.is_published:
        yield node
    for child in node.children:
        yield from iter_publishable(child)


def run_git_deploy(output_dir: Path, commit_message: str) -> None:
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=output_dir, capture_output=True, text=True)

    check = git("rev-parse", "--is-inside-work-tree")
    if check.returncode != 0:
        print(
            f"ERROR: {output_dir} is not a git repository. Set OUTPUT_DIR to a "
            "directory that's already a clone of your GitHub Pages repo "
            "(see README.md), then re-run with --deploy.",
            file=sys.stderr,
        )
        sys.exit(1)

    git("add", "-A")
    commit = git("commit", "-m", commit_message)
    if commit.returncode != 0 and "nothing to commit" in (commit.stdout + commit.stderr):
        print("Nothing changed since the last deploy - skipping commit/push.")
        return
    if commit.returncode != 0:
        print(commit.stdout)
        print(commit.stderr, file=sys.stderr)
        sys.exit(1)

    push = git("push")
    print(push.stdout)
    if push.returncode != 0:
        print(push.stderr, file=sys.stderr)
        sys.exit(1)
    print("Pushed. GitHub Pages will redeploy in a minute or two.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--dry-run", action="store_true", help="Print what would be included; write nothing.")
    parser.add_argument("--deploy", action="store_true", help="After building, git add/commit/push OUTPUT_DIR.")
    parser.add_argument("--env-file", default=str(PROJECT_ROOT / ".env"), help="Path to a .env file.")
    args = parser.parse_args()

    load_env_file(Path(args.env_file))

    token = os.environ.get("NOTION_TOKEN")
    root_page_raw = os.environ.get("ROOT_PAGE_ID")
    if not token or not root_page_raw:
        print(
            "ERROR: NOTION_TOKEN and ROOT_PAGE_ID must be set (via .env or the "
            "environment). Copy .env.example to .env and fill it in - see README.md.",
            file=sys.stderr,
        )
        sys.exit(1)

    root_page_id = extract_id_from_url(root_page_raw)
    output_dir = Path(os.environ.get("OUTPUT_DIR", str(PROJECT_ROOT / "site")))
    base_path = os.environ.get("BASE_PATH", "")
    commit_message = os.environ.get("GIT_COMMIT_MESSAGE", "Update site from Notion")

    client = NotionClient(token)

    print(f"Walking Notion tree from root page {root_page_id} ...")
    root, slugs, home_id = organize(build_tree(client, root_page_id), PROJECT_ROOT / "nav.txt")

    site_title = os.environ.get("SITE_TITLE") or root.title

    report = collect_publish_report(root)
    print("\n".join(report))

    publishable = list(iter_publishable(root))
    print(f"\n{len(publishable)} published page(s)/entries found under '{root.title}'.")

    if args.dry_run:
        print("\n--dry-run: nothing written, nothing deployed.")
        return

    print("\nFetching markdown for published pages ...")
    markdown_by_id: dict[str, str] = {}
    to_fetch = list(publishable)
    if root.is_published and not home_id:
        to_fetch.append(root)
    for node in to_fetch:
        result = client.get_page_markdown(node.id)
        markdown_by_id[node.id] = localize(result.get("markdown", ""), MEDIA_DIR)
        if result.get("truncated"):
            print(f"  NOTE: '{node.title}' was truncated by the Notion API (very long page).")

    print(f"\nRendering site (title='{site_title}', base_path='{base_path or '/'}') ...")
    log = render_site(
        root=root,
        markdown_by_id=markdown_by_id,
        output_dir=output_dir,
        site_title=site_title,
        project_root=PROJECT_ROOT,
        base_path=base_path,
        slugs=slugs,
        home_id=home_id,
    )
    print("\n".join(log))
    media_count = copy_media(MEDIA_DIR, output_dir)
    if media_count:
        print(f"  copied {media_count} image/file(s) into {output_dir / 'media'}")

    if args.deploy:
        print(f"\nDeploying {output_dir} ...")
        run_git_deploy(output_dir, commit_message)
    else:
        print(f"\nBuilt locally at {output_dir}. Re-run with --deploy to push it live.")


if __name__ == "__main__":
    main()
