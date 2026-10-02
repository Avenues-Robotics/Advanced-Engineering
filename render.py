"""
Turn a walker.Node tree (already tagged with publish status) plus a
{page_id: markdown} map into a static HTML site: one file per published
page/database row, a shared sidebar nav, and an index/home page.

Only nodes where `any_published()` is true appear in the nav at all.
A node that isn't itself published but has published descendants shows
up as an inert section label (its content was never public, so we never
render it), matching how Notion's own "hide subpages" behaves.
"""
from __future__ import annotations

import html
import re
import shutil
from pathlib import Path

from notion_api import strip_dashes
from notion_md import PageResolver, notion_markdown_to_html
from walker import Node


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "page"


def assign_slugs(root: Node, existing: dict[str, str] | None = None) -> dict[str, str]:
    """Map node id -> unique url slug (folder name), walking the whole tree.
    Ids already in `existing` keep their slug."""
    slugs: dict[str, str] = dict(existing or {})
    used: set[str] = set(slugs.values())

    def visit(node: Node) -> None:
        if node.id in slugs:
            for child in node.children:
                visit(child)
            return
        base = slugify(node.title)
        slug = base
        i = 2
        while slug in used:
            slug = f"{base}-{i}"
            i += 1
        used.add(slug)
        slugs[node.id] = slug
        for child in node.children:
            visit(child)

    visit(root)
    return slugs


def _find(node: Node, node_id: str) -> Node | None:
    if node.id == node_id:
        return node
    return next((f for c in node.children if (f := _find(c, node_id))), None)


def _contains(node: Node, node_id: str) -> bool:
    return node.id == node_id or any(_contains(c, node_id) for c in node.children)


def _render_nav(node: Node, slugs: dict[str, str], current_id: str, base_path: str,
                collapsible: bool = True) -> str:
    if not node.any_published():
        return ""

    child_html = "".join(
        _render_nav(c, slugs, current_id, base_path, collapsible) for c in node.children
    )
    title = html.escape(node.sidebar_title)

    if node.kind in ("page", "database_row") and node.is_published:
        cls = " active" if node.id == current_id else ""
        label = f'<a class="nav-link{cls}" href="{base_path}/{slugs[node.id]}/">{title}</a>'
    else:
        label = f'<span class="nav-label">{title}</span>'

    if not child_html:
        return f"<li>{label}</li>"
    if not collapsible:
        return f"<li>{label}<ul>{child_html}</ul></li>"

    # Only the sections containing the page being viewed start open, so
    # opening a page elsewhere in the sidebar collapses the previous section.
    open_attr = " open" if current_id and _contains(node, current_id) else ""
    return (
        f"<li><details{open_attr}>"
        f"<summary>{label}</summary><ul>{child_html}</ul></details></li>"
    )


EMBED_ICON = (
    '<svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" fill="none" '
    'stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">'
    '<polyline points="16 18 22 12 16 6"/><polyline points="8 6 2 12 8 18"/></svg>'
)


def _embed_controls(embed_href: str, page_title: str) -> str:
    """The </> button beside the breadcrumbs and the dialog it opens, which
    shows an <iframe> snippet (built in embed.js, since the full URL depends
    on where the site is hosted) for pasting into Canvas or similar."""
    return f"""<button class="embed-btn" type="button" title="Embed this page" aria-label="Embed this page"
        data-embed="{html.escape(embed_href)}" data-title="{html.escape(page_title)}">{EMBED_ICON}</button>
      <dialog class="embed-dialog">
        <form method="dialog">
          <h2>Embed this page</h2>
          <p>Paste this into the HTML editor of a Canvas page (or anywhere that accepts an iframe).</p>
          <textarea readonly rows="4"></textarea>
          <label>Height <input type="number" min="200" step="50" value="800"> px</label>
          <div class="embed-actions">
            <button type="button" class="embed-copy">Copy code</button>
            <button value="close">Close</button>
          </div>
        </form>
      </dialog>"""


def _page_shell(*, site_title: str, page_title: str, nav_html: str, body_html: str,
                 breadcrumb_html: str, base_path: str, embed_href: str | None = None) -> str:
    page_bar = ""
    if embed_href:
        page_bar = (f'<div class="page-bar">{breadcrumb_html or "<span></span>"}'
                    f"{_embed_controls(embed_href, page_title)}</div>")
        breadcrumb_html = ""
    script = f'<script src="{base_path}/static/embed.js" defer></script>' if embed_href else ""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page_title)} · {html.escape(site_title)}</title>
<link rel="stylesheet" href="{base_path}/static/style.css">
</head>
<body>
<header class="site-header">
  <a class="site-title" href="{base_path}/">
    <img class="site-logo" src="{base_path}/static/logo.png" alt="">
    <span>{html.escape(site_title)}</span>
  </a>
  <button class="menu-toggle" onclick="document.querySelector('.sidebar').classList.toggle('open')">Menu</button>
</header>
<div class="layout">
  <aside class="sidebar">
    <nav><ul>{nav_html}</ul></nav>
  </aside>
  <main class="content-wrap">
    <div class="content">
      {page_bar}
      {breadcrumb_html}
      {body_html}
    </div>
  </main>
</div>
{script}
</body>
</html>
"""


def _embed_shell(*, page_title: str, body_html: str, base_path: str) -> str:
    """Just the page content, for showing inside an iframe on another site.
    Links open in a new tab rather than inside the frame."""
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page_title)}</title>
<base target="_blank">
<link rel="stylesheet" href="{base_path}/static/style.css">
</head>
<body class="embed">
<div class="content">
{body_html}
</div>
</body>
</html>
"""


def _breadcrumb(path_titles: list[str]) -> str:
    if len(path_titles) <= 1:
        return ""
    crumbs = " / ".join(html.escape(t) for t in path_titles[:-1])
    return f'<p class="breadcrumb">{crumbs}</p>'


def _make_page_resolver(root: Node, slugs: dict[str, str], base_path: str) -> PageResolver:
    """Maps a Notion page id to (site href or None, title) so mentions and
    page links in the content point at the generated site."""
    by_id: dict[str, Node] = {}

    def index(node: Node) -> None:
        by_id[strip_dashes(node.id).lower()] = node
        for child in node.children:
            index(child)

    index(root)

    def resolve(page_id: str) -> tuple[str | None, str | None]:
        node = by_id.get(page_id)
        if node is None:
            return None, None
        if node.kind in ("page", "database_row") and node.is_published:
            return f"{base_path}/{slugs[node.id]}/", node.title
        return None, node.title

    return resolve


def _page_body_html(source: str, title: str, resolver: PageResolver, base_path: str) -> str:
    """Notion's markdown export omits the page title, but Notion shows it as
    the page's H1; add it unless the content already opens with that heading."""
    if not source.strip():
        body = "<p><em>(No content.)</em></p>"
    else:
        body = notion_markdown_to_html(source, resolver, base_path)
    opens_with_title = re.match(r"<h1[^>]*>(.*?)</h1>", body, re.S)
    if opens_with_title:
        heading_text = html.unescape(re.sub(r"<[^>]+>", "", opens_with_title.group(1)))
        if heading_text.strip().lower() == title.strip().lower():
            return body
    return f'<h1 class="page-title">{html.escape(title)}</h1>{body}'


def render_site(*, root: Node, markdown_by_id: dict[str, str], output_dir: Path,
                 site_title: str, project_root: Path, base_path: str = "",
                 slugs: dict[str, str] | None = None, home_id: str | None = None) -> list[str]:
    """
    Writes the static site into output_dir. Returns a list of human-readable
    log lines describing what was written / skipped, for --dry-run style
    reporting even in a real build.
    """
    base_path = "/" + base_path.strip("/") if base_path.strip("/") else ""

    output_dir = Path(output_dir)
    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)

    static_src = project_root / "static"
    static_dst = output_dir / "static"
    if static_src.exists():
        shutil.copytree(static_src, static_dst)

    slugs = assign_slugs(root, existing=slugs)
    resolver = _make_page_resolver(root, slugs, base_path)
    nav_root_html = "".join(
        _render_nav(c, slugs, current_id="", base_path=base_path) for c in root.children
    )

    log: list[str] = []
    written = 0

    def walk_and_write(node: Node, path_titles: list[str]) -> None:
        nonlocal written
        if not node.any_published():
            log.append(f"  (skip, unpublished, no published descendants) {node.title}")
            return

        if node.kind in ("page", "database_row") and node.is_published:
            page_dir = output_dir / slugs[node.id]
            page_dir.mkdir(parents=True, exist_ok=True)
            body_html = _page_body_html(markdown_by_id.get(node.id, ""), node.title, resolver, base_path)
            nav_html = "".join(
                _render_nav(c, slugs, current_id=node.id, base_path=base_path) for c in root.children
            )
            page_html = _page_shell(
                site_title=site_title,
                page_title=node.title,
                nav_html=nav_html,
                body_html=body_html,
                breadcrumb_html=_breadcrumb(path_titles + [node.title]),
                base_path=base_path,
                embed_href=f"{base_path}/{slugs[node.id]}/embed/",
            )
            (page_dir / "index.html").write_text(page_html, encoding="utf-8")
            (page_dir / "embed").mkdir()
            (page_dir / "embed" / "index.html").write_text(
                _embed_shell(page_title=node.title, body_html=body_html, base_path=base_path),
                encoding="utf-8",
            )
            written += 1
            log.append(f"  wrote /{slugs[node.id]}/  <-  {node.title}")
        else:
            log.append(f"  (section only, not itself published) {node.title}")

        for child in node.children:
            walk_and_write(child, path_titles + [node.sidebar_title])

    for child in root.children:
        walk_and_write(child, [site_title])

    # Home page: the chosen home page's content (also kept at its own URL),
    # else the root page's own content if it's published, otherwise a
    # generated landing page that just lists the top-level published sections.
    home = _find(root, home_id) if home_id else None
    home_nav_html = nav_root_html
    home_embed_href = None
    if home is not None:
        home_embed_href = f"{base_path}/{slugs[home.id]}/embed/"
        body_html = _page_body_html(markdown_by_id.get(home.id, ""), home.title, resolver, base_path)
        home_nav_html = "".join(
            _render_nav(c, slugs, current_id=home.id, base_path=base_path) for c in root.children
        )
    elif root.is_published:
        body_html = _page_body_html(markdown_by_id.get(root.id, ""), root.title, resolver, base_path)
    else:
        # Reuse the same nav-tree logic (not a flat list of direct children)
        # so a top-level page that isn't itself published, but has a
        # published descendant, shows as a label with its real children
        # nested underneath rather than a dead link.
        landing_list_html = "".join(
            _render_nav(c, slugs, current_id="", base_path=base_path, collapsible=False)
            for c in root.children
        )
        body_html = f"<h1>{html.escape(site_title)}</h1><ul>{landing_list_html}</ul>"

    home_html = _page_shell(
        site_title=site_title,
        page_title=site_title,
        nav_html=home_nav_html,
        body_html=body_html,
        breadcrumb_html="",
        base_path=base_path,
        embed_href=home_embed_href,
    )
    (output_dir / "index.html").write_text(home_html, encoding="utf-8")

    log.insert(0, f"Wrote {written} published page(s) to {output_dir}")
    return log
