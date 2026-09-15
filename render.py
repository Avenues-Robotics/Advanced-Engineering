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

import markdown as md

from walker import Node

_MD_EXTENSIONS = ["extra", "sane_lists", "toc"]


def slugify(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-")
    return text or "page"


def assign_slugs(root: Node) -> dict[str, str]:
    """Map node id -> unique url slug (folder name), walking the whole tree."""
    used: set[str] = set()
    slugs: dict[str, str] = {}

    def visit(node: Node) -> None:
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


def _render_nav(node: Node, slugs: dict[str, str], current_id: str, base_path: str) -> str:
    if not node.any_published():
        return ""

    child_html = "".join(_render_nav(c, slugs, current_id, base_path) for c in node.children)
    title = html.escape(node.title)

    if node.kind in ("page", "database_row") and node.is_published:
        cls = " active" if node.id == current_id else ""
        label = f'<a class="nav-link{cls}" href="{base_path}/{slugs[node.id]}/">{title}</a>'
    else:
        label = f'<span class="nav-label">{title}</span>'

    if child_html:
        return f"<li>{label}<ul>{child_html}</ul></li>"
    return f"<li>{label}</li>"


def _page_shell(*, site_title: str, page_title: str, nav_html: str, body_html: str,
                 breadcrumb_html: str, source_public_url: str | None, base_path: str) -> str:
    footer = ""
    if source_public_url:
        footer = (
            '<p class="unpublished-note">Sourced from the published Notion page '
            f'(<a href="{html.escape(source_public_url)}">view in Notion</a>). '
            "Regenerate this site after publishing changes there.</p>"
        )
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(page_title)} · {html.escape(site_title)}</title>
<link rel="stylesheet" href="{base_path}/static/style.css">
</head>
<body>
<button class="menu-toggle" onclick="document.querySelector('.sidebar').classList.toggle('open')">Menu</button>
<div class="layout">
  <aside class="sidebar">
    <a class="site-title" href="{base_path}/">{html.escape(site_title)}</a>
    <nav><ul>{nav_html}</ul></nav>
  </aside>
  <main class="content-wrap">
    <div class="content">
      {breadcrumb_html}
      {body_html}
      {footer}
    </div>
  </main>
</div>
</body>
</html>
"""


def _breadcrumb(path_titles: list[str]) -> str:
    if len(path_titles) <= 1:
        return ""
    crumbs = " / ".join(html.escape(t) for t in path_titles[:-1])
    return f'<p class="breadcrumb">{crumbs}</p>'


def _markdown_to_html(source: str) -> str:
    return md.markdown(source, extensions=_MD_EXTENSIONS)


def render_site(*, root: Node, markdown_by_id: dict[str, str], output_dir: Path,
                 site_title: str, project_root: Path, base_path: str = "") -> list[str]:
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

    slugs = assign_slugs(root)
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
            body_md = markdown_by_id.get(node.id, "")
            body_html = _markdown_to_html(body_md) if body_md.strip() else "<p><em>(No content.)</em></p>"
            nav_html = "".join(
                _render_nav(c, slugs, current_id=node.id, base_path=base_path) for c in root.children
            )
            page_html = _page_shell(
                site_title=site_title,
                page_title=node.title,
                nav_html=nav_html,
                body_html=body_html,
                breadcrumb_html=_breadcrumb(path_titles + [node.title]),
                source_public_url=node.public_url,
                base_path=base_path,
            )
            (page_dir / "index.html").write_text(page_html, encoding="utf-8")
            written += 1
            log.append(f"  wrote /{slugs[node.id]}/  <-  {node.title}")
        else:
            log.append(f"  (section only, not itself published) {node.title}")

        for child in node.children:
            walk_and_write(child, path_titles + [node.title])

    for child in root.children:
        walk_and_write(child, [root.title])

    # Home page: the root page's own content if it's published, otherwise a
    # generated landing page that just lists the top-level published sections.
    if root.is_published:
        body_md = markdown_by_id.get(root.id, "")
        body_html = _markdown_to_html(body_md) if body_md.strip() else "<p><em>(No content.)</em></p>"
        source_url = root.public_url
    else:
        # Reuse the same nav-tree logic (not a flat list of direct children)
        # so a top-level page that isn't itself published, but has a
        # published descendant, shows as a label with its real children
        # nested underneath rather than a dead link.
        body_html = f"<h1>{html.escape(site_title)}</h1><ul>{nav_root_html}</ul>"
        source_url = None

    home_html = _page_shell(
        site_title=site_title,
        page_title=site_title,
        nav_html=nav_root_html,
        body_html=body_html,
        breadcrumb_html="",
        source_public_url=source_url,
        base_path=base_path,
    )
    (output_dir / "index.html").write_text(home_html, encoding="utf-8")

    log.insert(0, f"Wrote {written} published page(s) to {output_dir}")
    return log
