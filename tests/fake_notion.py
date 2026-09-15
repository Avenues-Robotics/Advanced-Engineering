"""A fake Notion client used only for the offline self-test (tests/run_self_test.py).

Implements the same methods walker.TreeWalker calls on a real NotionClient,
backed by an in-memory fixture instead of the network, so the walker/render
logic can be exercised without a live Notion token.
"""
from __future__ import annotations


def _title_prop(text: str) -> dict:
    return {"title": {"type": "title", "title": [{"plain_text": text}]}}


class FakeNotionClient:
    def __init__(self):
        self.pages: dict[str, dict] = {}
        self.block_children: dict[str, list[dict]] = {}
        self.databases: dict[str, dict] = {}
        self.data_source_rows: dict[str, list[dict]] = {}
        self.markdown: dict[str, str] = {}

    # -- fixture-building helpers -----------------------------------------

    def add_page(self, page_id: str, title: str, *, published: bool, markdown: str = "") -> None:
        self.pages[page_id] = {
            "id": page_id,
            "properties": _title_prop(title),
            "public_url": f"https://example.notion.site/{page_id}" if published else None,
        }
        self.block_children.setdefault(page_id, [])
        self.markdown[page_id] = markdown

    def add_child_page_block(self, parent_id: str, child_id: str, title: str) -> None:
        self.block_children.setdefault(parent_id, []).append(
            {"id": child_id, "type": "child_page", "child_page": {"title": title}, "has_children": False}
        )

    def add_child_database_block(self, parent_id: str, db_id: str, title: str) -> None:
        self.block_children.setdefault(parent_id, []).append(
            {"id": db_id, "type": "child_database", "child_database": {"title": title}, "has_children": False}
        )
        self.databases[db_id] = {"id": db_id, "data_sources": [{"id": f"{db_id}-ds"}]}
        self.data_source_rows[f"{db_id}-ds"] = []

    def add_database_row(self, db_id: str, row_id: str, title: str, *, published: bool, markdown: str = "") -> None:
        self.data_source_rows[f"{db_id}-ds"].append(
            {
                "id": row_id,
                "properties": _title_prop(title),
                "public_url": f"https://example.notion.site/{row_id}" if published else None,
            }
        )
        self.block_children.setdefault(row_id, [])
        self.markdown[row_id] = markdown

    # -- NotionClient-compatible interface --------------------------------

    def get_page(self, page_id: str) -> dict:
        return self.pages[page_id]

    def iter_block_children(self, block_id: str):
        yield from self.block_children.get(block_id, [])

    def get_database(self, database_id: str) -> dict:
        return self.databases[database_id]

    def iter_data_source_rows(self, data_source_id: str):
        yield from self.data_source_rows.get(data_source_id, [])

    def get_page_markdown(self, page_id: str) -> dict:
        return {"object": "page_markdown", "id": page_id, "markdown": self.markdown.get(page_id, ""),
                "truncated": False, "unknown_block_ids": []}


def build_fixture() -> FakeNotionClient:
    """
    Mirrors (a trimmed version of) the real Advanced Engineering Electives
    tree, deliberately covering the tricky cases:
      - a published page directly under an unpublished root
      - an unpublished page with a published child (should show as an inert
        section label, not a link, but still appear so its child is reachable)
      - an unpublished page with no published descendants (should vanish
        entirely)
      - a database with a mix of published/unpublished rows
      - two pages that share a title (slug collision handling)
    """
    c = FakeNotionClient()

    c.add_page("root", "Advanced Engineering Electives", published=False)

    c.add_page("overview", "Program Overview", published=True, markdown="# Program Overview\n\nThis is the plan.")
    c.add_child_page_block("root", "overview", "Program Overview")

    c.add_page("reflections", "Reflections and Engineering Notebook", published=False)
    c.add_child_page_block("root", "reflections", "Reflections and Engineering Notebook")
    c.add_page("initial-reflection", "Initial Reflection", published=True, markdown="## Initial Reflection\n\nWhat are your goals?")
    c.add_child_page_block("reflections", "initial-reflection", "Initial Reflection")

    c.add_page("meeting-plans", "Full Course Meeting Plans", published=False)
    c.add_child_page_block("root", "meeting-plans", "Full Course Meeting Plans")
    # no children at all -> should vanish from the nav entirely

    c.add_child_database_block("root", "content-library", "Content Library")
    c.add_database_row("content-library", "eng-reqs", "Engineering Requirements", published=True,
                        markdown="# Engineering Requirements\n\nEvery project begins with requirements.")
    c.add_database_row("content-library", "prior-art-template", "Prior Art Research Notes Template", published=False)

    # duplicate title to exercise slug collision handling
    c.add_page("overview-2", "Program Overview", published=True, markdown="# Program Overview\n\n(A second, differently-scoped page that happens to share a title.)")
    c.add_child_page_block("root", "overview-2", "Program Overview")

    return c
