#!/usr/bin/env python3
"""
Update the site from this computer.

    python3 publish.py pull                   pull everything from Notion into snapshot/,
                                              update nav.txt and build a local preview
    python3 publish.py pull <notion link>     same, but only re-pull that page (and its
                                              sub-pages) - a share link, URL or ID works
    python3 publish.py pull ... --deploy      ...then put it on the live site
    python3 publish.py deploy                 put nav.txt and snapshot/ on the live site
                                              as they are, without pulling from Notion

Add --serve to any of these to look at the local preview at http://localhost:8000/.

Every command first gets the latest from GitHub (git pull), so edits made there
aren't lost. Deploying commits nav.txt and snapshot/, pushes, and starts the
"Build and deploy site from Notion" GitHub Action in snapshot mode: it builds
from exactly what was pushed (no Notion pull) and the site updates in a minute
or two. That needs the GitHub CLI (`gh`), signed in; without it the push still
happens and you start the Action from the repo's Actions tab.

The Action itself builds with:  python3 publish.py build --out site --base-path /Advanced-Engineering
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

from preview import NAV_FILE, OUTPUT_DIR, SNAPSHOT_DIR, build, pull_from_notion, pull_page, serve

PROJECT_ROOT = Path(__file__).resolve().parent
WORKFLOW = "build-and-deploy.yml"
DEPLOYED_PATHS = ["nav.txt", "snapshot"]


def git(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        sys.exit(f"ERROR: git {' '.join(args)} failed:\n{result.stdout}{result.stderr}")
    return result


def sync_with_github(required: bool) -> None:
    """git pull, keeping any uncommitted local edits on top."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        return
    print("Getting the latest from GitHub ...")
    result = git("pull", "--rebase", "--autostash", check=False)
    conflicts = git("diff", "--name-only", "--diff-filter=U").stdout.split()
    if conflicts:
        sys.exit("ERROR: your local edits clash with changes on GitHub in: " + ", ".join(conflicts) +
                 "\nOpen those files, keep the lines you want (remove the <<<<<<< ======= >>>>>>> "
                 "markers), then run this again.")
    if result.returncode != 0:
        message = f"Couldn't get the latest from GitHub:\n{result.stdout}{result.stderr}"
        if required:
            sys.exit("ERROR: " + message + "\nFix that (`git status` shows what's going on), then run this again.")
        print("WARNING: " + message + "Carrying on with the local copy.")


def deploy(commit_message: str) -> None:
    branch = git("rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    if branch != "main":
        sys.exit(f"ERROR: you're on branch {branch!r}; switch to main to deploy.")

    git("add", "-A", "--", *DEPLOYED_PATHS)
    if git("diff", "--cached", "--quiet", "--", *DEPLOYED_PATHS, check=False).returncode:
        git("commit", "-m", commit_message, "--", *DEPLOYED_PATHS)
        print(f'Committed "{commit_message}".')
    else:
        print("nav.txt and snapshot/ have no new changes to commit.")

    others = [l[3:] for l in git("status", "--porcelain").stdout.splitlines()
              if not any(l[3:].startswith(p) for p in DEPLOYED_PATHS)]
    if others:
        print("NOTE: these aren't committed, so the live site won't include them: " + ", ".join(others))

    print("Pushing to GitHub ...")
    git("push")

    if not shutil.which("gh"):
        print("\nPushed. To put it live, open the repo's Actions tab -> Build and deploy site from "
              'Notion -> Run workflow, and choose source "snapshot".')
        return
    started = git("rev-parse", "HEAD").stdout.strip()
    run = subprocess.run(["gh", "workflow", "run", WORKFLOW, "--ref", "main", "-f", "source=snapshot"],
                         cwd=PROJECT_ROOT, capture_output=True, text=True)
    if run.returncode != 0:
        sys.exit(f"ERROR: pushed, but couldn't start the GitHub Action:\n{run.stderr}"
                 '\nStart it from the repo\'s Actions tab instead (choose source "snapshot").')
    print("Started the GitHub Action - the live site updates in a minute or two.")
    for _ in range(5):
        time.sleep(2)
        runs = subprocess.run(["gh", "run", "list", "--workflow", WORKFLOW, "-L", "1", "--json", "url,headSha"],
                              cwd=PROJECT_ROOT, capture_output=True, text=True)
        latest = json.loads(runs.stdout or "[]")
        if latest and latest[0]["headSha"] == started:
            print(f"Follow it at {latest[0]['url']}")
            return


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)

    pull = commands.add_parser("pull", help="Pull from Notion into snapshot/ and update nav.txt.")
    pull.add_argument("link", nargs="?", help="Only re-pull this page (a Notion share link, URL or ID).")
    pull.add_argument("--deploy", action="store_true", help="Put the result on the live site.")
    pull.add_argument("--serve", action="store_true", help="Serve the local preview afterwards.")

    dep = commands.add_parser("deploy", help="Put nav.txt and snapshot/ on the live site, without pulling Notion.")
    dep.add_argument("--serve", action="store_true", help="Serve the local preview afterwards.")

    bld = commands.add_parser("build", help="Build the site from snapshot/ (what the GitHub Action runs).")
    bld.add_argument("--out", default=str(OUTPUT_DIR), help="Output folder (default: preview_site).")
    bld.add_argument("--base-path", default="", help='URL prefix, e.g. "/Advanced-Engineering".')

    args = parser.parse_args()

    if args.command == "build":
        build(SNAPSHOT_DIR, Path(args.out), NAV_FILE, base_path=args.base_path)
        return

    deploying = args.command == "deploy" or args.deploy
    sync_with_github(required=deploying)

    if args.command == "pull":
        if args.link:
            title, refreshed = pull_page(SNAPSHOT_DIR, args.link)
            message = f'Pull "{title}" from Notion'
            if len(refreshed) > 1:
                message += f" (and {len(refreshed) - 1} published sub-page(s))"
        else:
            pull_from_notion(SNAPSHOT_DIR)
            message = "Pull from Notion"
    else:
        message = "Update nav.txt"

    # Building applies nav.txt (printing a NOTE for anything that doesn't line
    # up) and rewrites the unlisted-pages list at the bottom of it.
    build(SNAPSHOT_DIR, OUTPUT_DIR, NAV_FILE)

    if deploying:
        deploy(message)
    else:
        print(f"\nPreview built in {OUTPUT_DIR.name}/. Nothing was pushed - add --deploy to put it live.")
    if args.serve:
        serve(OUTPUT_DIR, 8000)


if __name__ == "__main__":
    main()
