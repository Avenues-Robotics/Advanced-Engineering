"""
Walk a Notion page tree (sub-pages and database rows) starting from a root
page, tagging every node with its publish status (public_url).

Nothing here decides what makes it into the site -- that filtering happens
in render.py, based purely on whether a node (or one of its descendants) is
published. This module just mirrors the actual Notion structure.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from notion_api import NotionAPIError, NotionClient

MAX_PAGES = 2000  # safety cap so a runaway recursion can't walk forever


@dataclass
class Node:
    id: str
    title: str
    kind: str  # "page" | "database" | "database_row"
    public_url: str | None
    children: list["Node"] = field(default_factory=list)
    nav_title: str | None = None  # sidebar-only name, set from nav.txt
    in_nav: bool = True  # False for nav.txt's [Linked Only] pages: built, but not in the sidebar
    last_edited: str | None = None  # Notion's last_edited_time, for incremental pulls

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
            "last_edited": self.last_edited,
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
        page = self.client.get_page(root_page_id)  # let a bad root page fail loudly
        node = Node(id=root_page_id, title=_plain_title_from_page(page) or "Untitled",
                    kind="page", public_url=page.get("public_url"),
                    last_edited=page.get("last_edited_time"))
        node.children = self._page_children(node)
        return node

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

    def _get_page(self, page_id: str) -> dict:
        return self.client.get_page(page_id)

    def _page_children(self, node: Node) -> list[Node]:
        """The sub-pages and databases inside a page or database row."""
        return self._collect_block_children(node.id)

    def _build_page_node(self, page_id: str, fallback_title: str | None) -> Node | None:
        """Returns None for a sub-page that's trashed or that Notion won't let us read."""
        try:
            page = self._get_page(page_id)
        except NotionAPIError as e:
            if e.status not in (403, 404):
                raise
            print(f"WARNING: skipping {fallback_title or page_id!r}: Notion returned {e.status} "
                  f"(deleted, or not shared with the integration)")
            return None
        if page.get("in_trash") or page.get("archived"):
            return None
        title = _plain_title_from_page(page) or fallback_title or "Untitled"
        node = Node(
            id=page_id,
            title=title,
            kind="page",
            public_url=page.get("public_url"),
            last_edited=page.get("last_edited_time"),
        )
        node.children = self._page_children(node)
        return node

    def _collect_block_children(self, block_id: str) -> list[Node]:
        children: list[Node] = []
        try:
            blocks = list(self.client.iter_block_children(block_id))
        except NotionAPIError as e:
            if e.status not in (403, 404):
                raise
            print(f"WARNING: couldn't read the contents of {block_id}: Notion returned {e.status} "
                  f"(probably deleted during the pull); skipping it")
            return children
        for block in blocks:
            btype = block.get("type")
            block_id_inner = block["id"]

            if btype == "child_page":
                if self._guard(block_id_inner):
                    continue
                title = block.get("child_page", {}).get("title", "Untitled")
                child = self._build_page_node(block_id_inner, fallback_title=title)
                if child is not None:
                    children.append(child)

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
                    last_edited=row.get("last_edited_time"),
                )
                # Rows can themselves have sub-pages nested under them.
                row_node.children = self._page_children(row_node)
                db_node.children.append(row_node)
        return db_node


def _key(notion_id: str) -> str:
    return notion_id.replace("-", "").lower()


class IncrementalWalker(TreeWalker):
    """A TreeWalker that only reads the contents of pages edited since `previous`
    (the last pull's tree) was taken.

    `pages` is every page the integration can see, from one Notion search, keyed
    by bare lowercase ID: that supplies each page's title, publish status and
    last_edited_time without a request per page. Database rows still come from
    querying each database (a request per 100 rows), which is also how added,
    removed and reordered rows are noticed.

    A page whose last_edited_time matches the previous tree keeps its previous
    list of sub-pages and databases (adding, removing or moving a sub-page counts
    as an edit to the page it's in), and each of those is then checked the same
    way. Anything else - new, edited, or missing from the search - is walked
    in full, like TreeWalker does.
    """

    def __init__(self, client: NotionClient, previous: Node, pages: dict[str, dict]):
        super().__init__(client)
        self._previous: dict[str, Node] = {}
        stack = [previous]
        while stack:
            node = stack.pop()
            self._previous[_key(node.id)] = node
            stack.extend(node.children)
        self._pages = pages
        self.changed: set[str] = set()  # keys of pages/rows whose contents were re-read

    def is_unchanged(self, node: Node) -> bool:
        """True if `node` was in the previous tree and hasn't been edited since."""
        return _key(node.id) not in self.changed

    def previous(self, node: Node) -> Node | None:
        return self._previous.get(_key(node.id))

    def _get_page(self, page_id: str) -> dict:
        page = self._pages.get(_key(page_id))
        return page if page is not None else self.client.get_page(page_id)

    def _page_children(self, node: Node) -> list[Node]:
        old = self._previous.get(_key(node.id))
        if old is None or old.last_edited is None or old.last_edited != node.last_edited:
            self.changed.add(_key(node.id))
            return super()._page_children(node)
        children: list[Node] = []
        for child in old.children:
            if self._guard(child.id):
                continue
            if child.kind == "database":
                children.append(self._build_database_node(child.id, child.title))
            else:
                new = self._build_page_node(child.id, fallback_title=child.title)
                if new is not None:
                    children.append(new)
        return children


def build_tree(client: NotionClient, root_page_id: str) -> Node:
    return TreeWalker(client).build(root_page_id)
