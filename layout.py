"""
Reorganize the Notion page tree according to nav.txt, a hand-edited outline
of page titles, so the site's sidebar order and grouping don't have to match
Notion's (where database rows come back in no useful order).

Only the sidebar structure comes from nav.txt; every page's content, and
whether it's published at all, still comes from Notion on each build. A
published page nav.txt doesn't mention is left off the site, and the build
log names it so it can be placed. The first page listed is the home page.

nav.txt syntax (one entry per line, indent to nest):

    Program Overview                  a Notion page or database, by title
    [Units]                           a plain section label that isn't in Notion
      Requirements
      Engineering Requirements | Writing Requirements
                                      ...shown in the sidebar as "Writing Requirements"
    Content Library > Software Architecture
                                      "Parent > Title" when two pages share a title
    [Hidden]                          anything under this is left off the site
      Sample 3-View Drawing
    # a comment

Titles match case-insensitively. A Notion page ID (or its URL) also works
in place of a title.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from notion_api import strip_dashes
from render import assign_slugs, slugify
from walker import Node

HIDDEN_LABEL = "hidden"
UNLISTED_MARKER = "# ==== Published in Notion, not in the sidebar yet ===="
_ID_RE = re.compile(r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})", re.I)

HEADER = """\
# Site navigation. Controls the order and grouping of the sidebar; page
# content still comes from Notion on every build.
#
#   Page Title                  a Notion page or database, by title
#   [Section Name]              a plain label that isn't a Notion page
#   Page Title | Sidebar Name   show a page under a different name
#   Parent > Page Title         when two pages share a title
#   [Hidden]                    anything indented under this is left off the site
#
# Indent (2 spaces) to nest. The first page listed is the site's home page.
# Pages published in Notion but not listed here are left off the site; the
# build lists them at the bottom of this file so you can move them up.
"""


@dataclass
class Entry:
    text: str
    display: str | None
    is_label: bool
    lineno: int
    children: list["Entry"] = field(default_factory=list)


def parse_layout(text: str) -> list[Entry]:
    top: list[Entry] = []
    stack: list[tuple[int, Entry]] = []
    for lineno, raw in enumerate(text.splitlines(), start=1):
        if raw.strip() == UNLISTED_MARKER:
            break
        line = raw.expandtabs(4).rstrip()
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())

        if stripped.startswith("[") and stripped.endswith("]"):
            entry = Entry(stripped[1:-1].strip(), None, True, lineno)
        else:
            name, _, display = stripped.partition(" | ")
            entry = Entry(name.strip(), display.strip() or None, False, lineno)

        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1].children if stack else top).append(entry)
        stack.append((indent, entry))
    return top


def _norm(title: str) -> str:
    return " ".join(title.split()).lower()


class _Index:
    def __init__(self, root: Node):
        self.by_id: dict[str, Node] = {}
        self.by_title: dict[str, list[Node]] = {}
        self.parent: dict[str, Node] = {}

        def visit(node: Node) -> None:
            for child in node.children:
                self.parent[child.id] = node
                self.by_id[strip_dashes(child.id).lower()] = child
                self.by_title.setdefault(_norm(child.title), []).append(child)
                visit(child)

        visit(root)

    def ancestors(self, node: Node) -> list[Node]:
        chain = []
        while node.id in self.parent:
            node = self.parent[node.id]
            chain.append(node)
        return chain

    def line_for(self, node: Node) -> str:
        """How to refer to `node` in nav.txt: its title, or "Parent > Title"
        if another page has the same title."""
        name = node.title.strip()
        if len(self.by_title[_norm(node.title)]) > 1:
            name = f"{self.parent[node.id].title.strip()} > {name}"
        return name

    def find(self, text: str) -> tuple[Node | None, str | None]:
        """Returns (node, None) or (None, reason)."""
        id_match = _ID_RE.search(text)
        if id_match:
            node = self.by_id.get(strip_dashes(id_match.group(1)).lower())
            return (node, None) if node else (None, "no page with that ID")

        parts = [p for p in (_norm(p) for p in text.split(" > ")) if p]
        candidates = [
            n for n in self.by_title.get(parts[-1], [])
            if _ends_with_path([_norm(a.title) for a in self.ancestors(n)], parts[:-1])
        ]
        if len(candidates) == 1:
            return candidates[0], None
        if not candidates:
            return None, "no Notion page with that title"
        where = ", ".join(f'"{self.ancestors(n)[0].title} > {n.title}"' for n in candidates)
        return None, f"more than one page has that title - write one of {where}"


def _ends_with_path(ancestor_titles: list[str], wanted_parents: list[str]) -> bool:
    """ancestor_titles is nearest-first; wanted_parents is outermost-first."""
    return ancestor_titles[:len(wanted_parents)] == list(reversed(wanted_parents))


def apply_layout(root: Node, entries: list[Entry]) -> tuple[Node, list[str], list[tuple[str, str]]]:
    """Returns a new tree arranged per `entries`, human-readable notes about
    anything that didn't line up (unknown titles, unlisted pages), and the
    published pages left off as (Notion parent title, nav.txt line) pairs."""
    index = _Index(root)
    notes: list[str] = []
    placed: dict[str, Node] = {}   # original id -> node in the new tree
    hidden: set[str] = set()
    used_label_ids: set[str] = set()

    def label_node(text: str) -> Node:
        base = "section-" + slugify(text)
        node_id, i = base, 2
        while node_id in used_label_ids:
            node_id, i = f"{base}-{i}", i + 1
        used_label_ids.add(node_id)
        return Node(id=node_id, title=text, kind="section", public_url=None)

    def build(entry: Entry, in_hidden: bool) -> list[Node]:
        if entry.is_label and _norm(entry.text) == HIDDEN_LABEL:
            for child in entry.children:
                build(child, in_hidden=True)
            return []

        if entry.is_label:
            node = label_node(entry.text)
        else:
            original, reason = index.find(entry.text)
            if original is None:
                notes.append(f'nav.txt line {entry.lineno}: "{entry.text}" skipped - {reason}.')
                # Keep whatever was nested under it rather than losing it too.
                return [n for c in entry.children for n in build(c, in_hidden)]
            if original.id in placed or original.id in hidden:
                notes.append(f'nav.txt line {entry.lineno}: "{entry.text}" is listed twice; '
                             "only the first one is used.")
                return []
            if in_hidden:
                hidden.add(original.id)
                for child in entry.children:
                    build(child, in_hidden=True)
                return []
            node = Node(id=original.id, title=original.title, kind=original.kind,
                        public_url=original.public_url, nav_title=entry.display)
            placed[original.id] = node

        node.children = [n for c in entry.children for n in build(c, in_hidden)]
        return [node]

    new_root = Node(id=root.id, title=root.title, kind=root.kind, public_url=root.public_url)
    new_root.children = [n for e in entries for n in build(e, in_hidden=False)]

    # Anything Notion has published that nav.txt doesn't mention stays off
    # the site, with a note. Pages under a hidden page are hidden too.
    unlisted: list[tuple[str, str]] = []

    def report_unlisted(node: Node, parent_title: str) -> None:
        for child in node.children:
            if child.id in hidden or node.id in hidden:
                hidden.add(child.id)
            elif child.id not in placed and child.is_published:
                notes.append(f'"{child.title.strip()}" (under "{parent_title}" in Notion) is '
                             "published but isn't in nav.txt - left off the site.")
                unlisted.append((parent_title, index.line_for(child)))
            report_unlisted(child, child.title.strip())

    report_unlisted(root, root.title.strip())
    return new_root, notes, unlisted


def unlisted_section(unlisted: list[tuple[str, str]]) -> str:
    lines = [
        UNLISTED_MARKER,
        "# Rewritten by every build - edits below here are replaced. To add one",
        "# of these pages to the site, move its line up into the outline above.",
    ]
    if not unlisted:
        lines.append("# (none)")
    last_parent = None
    for parent, line in unlisted:
        if parent != last_parent:
            lines += ["", f"# in {parent}:"]
            last_parent = parent
        lines.append(line)
    return "\n".join(lines) + "\n"


def update_unlisted_section(layout_path: Path, unlisted: list[tuple[str, str]]) -> bool:
    """Replace the list at the bottom of nav.txt; returns True if it changed."""
    text = layout_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    cut = next((i for i, l in enumerate(lines) if l.strip() == UNLISTED_MARKER), len(lines))
    outline = "\n".join(lines[:cut]).rstrip()
    new_text = outline + "\n\n" + unlisted_section(unlisted)
    if new_text == text:
        return False
    layout_path.write_text(new_text, encoding="utf-8")
    return True


def first_page(node: Node) -> Node | None:
    for child in node.children:
        if child.kind in ("page", "database_row") and child.is_published:
            return child
        found = first_page(child)
        if found:
            return found
    return None


def organize(root: Node, layout_path: Path) -> tuple[Node, dict[str, str], str | None]:
    """Apply nav.txt (if it exists) and return (tree to render, url slugs,
    id of the home page - the first page listed, or None for the Notion root
    page). Slugs come from the Notion tree, not the rearranged one, so moving
    or renaming a page in nav.txt never changes its URL."""
    slugs = assign_slugs(root)
    if not layout_path.exists():
        return root, slugs, None
    new_root, notes, unlisted = apply_layout(root, parse_layout(layout_path.read_text(encoding="utf-8")))
    prefix = "::warning::" if os.environ.get("GITHUB_ACTIONS") == "true" else "NOTE: "
    for note in notes:
        print(prefix + note)
    if update_unlisted_section(layout_path, unlisted):
        print(f"Updated the unlisted-pages list at the bottom of {layout_path.name}.")
    home = first_page(new_root)
    return new_root, assign_slugs(new_root, existing=slugs), home.id if home else None


def layout_from_tree(root: Node) -> str:
    """A starting nav.txt that reproduces the current Notion order."""
    index = _Index(root)
    lines = [HEADER]

    def visit(node: Node, depth: int) -> None:
        for child in node.children:
            if not child.any_published():
                continue
            lines.append("  " * depth + index.line_for(child))
            visit(child, depth + 1)

    visit(root, 0)
    return "\n".join(lines) + "\n"
