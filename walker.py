"""
Walk a Notion page tree (sub-pages and database rows) starting from a root
page, tagging every node with its publish status (public_url).

Nothing here decides what makes it into the site -- that filtering happens
in render.py, based purely on whether a node (or one of its descendants) is
published. This module just mirrors the actual Notion structure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from notion_api import NotionClient

MAX_PAGES = 2000  # safety cap so a runaway recursion can't walk forever


@dataclass
class Node:
    id: str
    title: str
    kind: str  # "page" | "database" | "database_row"
    public_url: str | None
    children: list["Node"] = field(default_factory=list)
    nav_title: str | None = None  # sidebar-only name, set from nav.txt

    @property
    def sidebar_title(self) -> str:
        return self.nav_title or self.title

    @property
    def is_published(self) -> bool:
        return self.public_url is not None

    def any_published(self) -> bool:
        """True if this node or anything under it is published."""
        if self.is_published:
            return True
        return any(child.any_published() for child in self.children)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "kind": self.kind,
            "public_url": self.public_url,
            "children": [c.to_dict() for c in self.children],
        }


def _plain_title_from_page(page: dict) -> str:
    props = page.get("properties", {})
    for prop in props.values():
        if prop.get("type") == "title":
            parts = prop.get("title", [])
            text = "".join(p.get("plain_text", "") for p in parts)
            if text:
                return text
    return page.get("id", "Untitled")


class TreeWalker:
    def __init__(self, client: NotionClient):
        self.client = client
        self._visited: set[str] = set()
        self._count = 0

    def build(self, root_page_id: str) -> Node:
        return self._build_page_node(root_page_id, fallback_title=None)

    # -- internals -----------------------------------------------------

    def _guard(self, node_id: str) -> bool:
        """Returns True if we should skip this id (already seen, or over cap)."""
        if node_id in self._visited:
            return True
        if self._count >= MAX_PAGES:
            return True
        self._visited.add(node_id)
        self._count += 1
        return False

    def _build_page_node(self, page_id: str, fallback_title: str | None) -> Node:
        page = self.client.get_page(page_id)
        title = _plain_title_from_page(page) or fallback_title or "Untitled"
        node = Node(
            id=page_id,
            title=title,
            kind="page",
            public_url=page.get("public_url"),
        )
        node.children = self._collect_block_children(page_id)
        return node

    def _collect_block_children(self, block_id: str) -> list[Node]:
        children: list[Node] = []
        for block in self.client.iter_block_children(block_id):
            btype = block.get("type")
            block_id_inner = block["id"]

            if btype == "child_page":
                if self._guard(block_id_inner):
                    continue
                title = block.get("child_page", {}).get("title", "Untitled")
                children.append(self._build_page_node(block_id_inner, fallback_title=title))

            elif btype == "child_database":
                if self._guard(block_id_inner):
                    continue
                title = block.get("child_database", {}).get("title", "Untitled database")
                children.append(self._build_database_node(block_id_inner, title))

            elif block.get("has_children"):
                # Nested structural blocks (toggles, columns, synced blocks, etc.)
                # can themselves contain child_page/child_database blocks.
                children.extend(self._collect_block_children(block_id_inner))

        return children

    def _build_database_node(self, database_id: str, title: str) -> Node:
        db_node = Node(id=database_id, title=title, kind="database", public_url=None)
        database = self.client.get_database(database_id)
        for ds in database.get("data_sources", []):
            ds_id = ds["id"]
            for row in self.client.iter_data_source_rows(ds_id):
                row_id = row["id"]
                if self._guard(row_id):
                    continue
                row_title = _plain_title_from_page(row)
                row_node = Node(
                    id=row_id,
                    title=row_title,
                    kind="database_row",
                    public_url=row.get("public_url"),
                )
                # Rows can themselves have sub-pages nested under them.
                row_node.children = self._collect_block_children(row_id)
                db_node.children.append(row_node)
        return db_node


def build_tree(client: NotionClient, root_page_id: str) -> Node:
    return TreeWalker(client).build(root_page_id)
