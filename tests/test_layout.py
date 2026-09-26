#!/usr/bin/env python3
"""
Offline tests for layout.py (nav.txt parsing and tree reorganizing).

Run with:  python3 tests/test_layout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from layout import apply_layout, first_page, layout_from_tree, parse_layout, unlisted_section  # noqa: E402
from render import assign_slugs  # noqa: E402
from walker import Node  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool) -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)


def page(node_id: str, title: str, *children: Node, published: bool = True, kind: str = "page") -> Node:
    return Node(id=node_id, title=title, kind=kind,
                public_url=f"https://x/{node_id}" if published else None, children=list(children))


def fixture() -> Node:
    return page(
        "root", "Root",
        page("ov", "Program Overview"),
        page("outcomes", "Outcomes",
             page("o-arch", "Software Architecture", kind="database_row"),
             page("o-req", "Requirements", kind="database_row"),
             published=False, kind="database"),
        page("lib", "Content Library",
             page("l-arch", "Software Architecture", kind="database_row"),
             page("l-draw", "Sample 3-View Drawing ", kind="database_row"),
             page("l-new", "Brand New Reading", kind="database_row"),
             published=False, kind="database"),
        page("courses", "Courses",
             page("scs", "Software and Control Systems 1", page("scs-new", "Week 1"))),
        page("draft", "Draft", published=False),
        published=False,
    )


def shape(node: Node) -> list:
    return [(c.sidebar_title, shape(c)) if c.children else c.sidebar_title for c in node.children]


def main() -> int:
    root = fixture()

    nav = """
# comment
[Start Here]
  Program overview | Welcome
  Courses
Outcomes
  Requirements
  Outcomes > Software Architecture
[Readings]
  Content Library > Software Architecture
[Hidden]
  Sample 3-View Drawing
Not A Real Page
  Draft
Software Architecture
"""
    new_root, notes, unlisted = apply_layout(root, parse_layout(nav))
    check("sections, renames and nesting follow nav.txt", shape(new_root) == [
        ("Start Here", ["Welcome", "Courses"]),
        ("Outcomes", ["Requirements", "Software Architecture"]),
        ("Readings", ["Software Architecture"]),
        "Draft",
    ])
    check("first listed page is the home page", first_page(new_root).id == "ov")
    readings = new_root.children[2]
    check('"Parent > Title" picks the right duplicate', readings.children[0].id == "l-arch")
    check("title match ignores case and stray whitespace",
          new_root.children[0].children[0].id == "ov")
    check("a rename only changes the sidebar name",
          new_root.children[0].children[0].title == "Program Overview")
    check("unknown title is reported", any('"Not A Real Page" skipped' in n for n in notes))
    check("ambiguous title is reported with the fix",
          any("Content Library > Software Architecture" in n and "more than one" in n for n in notes))
    unlisted_notes = [n for n in notes if "isn't in nav.txt" in n]
    check("published pages missing from nav.txt are reported and left off", unlisted_notes == [
        '"Brand New Reading" (under "Content Library" in Notion) is published but '
        "isn't in nav.txt - left off the site.",
        '"Software and Control Systems 1" (under "Courses" in Notion) is published but '
        "isn't in nav.txt - left off the site.",
        '"Week 1" (under "Software and Control Systems 1" in Notion) is published but '
        "isn't in nav.txt - left off the site.",
    ] and not {"scs", "scs-new", "l-new"} & _ids(new_root))
    check("unlisted pages are collected for the bottom of nav.txt", unlisted == [
        ("Content Library", "Brand New Reading"),
        ("Courses", "Software and Control Systems 1"),
        ("Software and Control Systems 1", "Week 1"),
    ])
    section = unlisted_section(unlisted + [("Outcomes", "Outcomes > Software Architecture")])
    listed_again, _, _ = apply_layout(root, parse_layout(nav + "\n" + section))
    check("the unlisted list at the bottom isn't read as part of the outline",
          shape(listed_again) == shape(new_root))
    readded, _, still_unlisted = apply_layout(
        root, parse_layout(nav + "\n".join(l for _, l in unlisted)))
    check("moving a listed line up into the outline adds the page",
          {"scs", "scs-new", "l-new"} <= _ids(readded) and still_unlisted == [])
    check("hidden page is gone, without a note",
          "l-draw" not in _ids(new_root) and not any("3-View" in n for n in notes))
    check("section labels aren't pages",
          new_root.children[0].kind == "section" and not new_root.children[0].is_published)

    slugs = assign_slugs(new_root, existing=assign_slugs(root))
    check("renaming in nav.txt keeps the Notion-based URL", slugs["ov"] == "program-overview")
    check("section labels get slugs", slugs["section-start-here"] == "start-here")

    generated = layout_from_tree(root)
    regenerated, notes, _ = apply_layout(root, parse_layout(generated))
    check("generated nav.txt applies cleanly", notes == [])
    check("generated nav.txt reproduces the Notion order",
          shape(_published_only(regenerated)) == shape(_published_only(root)))
    check("generated nav.txt disambiguates duplicate titles",
          "Content Library > Software Architecture" in generated)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


def _ids(node: Node) -> set[str]:
    return {node.id} | {i for c in node.children for i in _ids(c)}


def _published_only(node: Node) -> Node:
    return Node(id=node.id, title=node.title, kind=node.kind, public_url=node.public_url,
                children=[_published_only(c) for c in node.children if c.any_published()])


if __name__ == "__main__":
    sys.exit(main())
