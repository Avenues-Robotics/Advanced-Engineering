"""
Convert Notion "enhanced markdown" (GET /v1/pages/{id}/markdown) into HTML.

This is *not* CommonMark, so a stock markdown library mangles it. The
differences that matter:
  - Every line is its own block (no blank lines between paragraphs/lists).
  - Children are nested with tab indentation.
  - Block-level XML tags: <callout>, <columns>/<column>, <details>/<summary>,
    <table>, <tabs>, <page>, <video>, <empty-block/>, ...
  - Inline run styling is emitted per run (e.g. `*a *<span>***b***</span>*, c*`),
    so `*`/`**`/`~~` must be treated as toggles, not matched pairs.
  - Special characters are backslash-escaped (\\~ \\: \\< ...).

Spec: notion://docs/enhanced-markdown-spec (Notion MCP).
"""
from __future__ import annotations

import html
import re
import string
from typing import Callable
from urllib.parse import urlparse

# notion page id (32 hex, no dashes, lowercase) -> (site href or None, title or None)
PageResolver = Callable[[str], "tuple[str | None, str | None]"]

_ATTR_RE = re.compile(r'([\w-]+)\s*=\s*"([^"]*)"')
_TAG_RE = re.compile(r'<(/?)([A-Za-z][\w-]*)((?:\s+[\w-]+\s*=\s*"[^"]*")*)\s*(/?)>')
_OPEN_RE = re.compile(r'^<([A-Za-z][\w-]*)((?:\s+[\w-]+\s*=\s*"[^"]*")*)\s*(/?)>')
_ATTRS_TAIL = re.compile(r'\s*\{((?:\s*[\w-]+="[^"]*")+)\s*\}\s*$')
_NOTION_ID = re.compile(
    r"([0-9a-f]{8}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{4}-?[0-9a-f]{12})$", re.I
)
_SPECIAL = re.compile(r"[\\`*~$\[<]")
_PUNCT = set(string.punctuation)

_BLOCK_TAGS = {
    "callout", "columns", "column", "details", "table", "tabs", "tab",
    "synced_block", "synced_block_reference", "meeting-notes", "page",
    "database", "folder", "video", "audio", "file", "pdf", "embed",
    "table_of_contents", "empty-block", "unknown",
}
_COLORS = {
    "gray", "brown", "orange", "yellow", "green", "blue", "purple", "pink", "red",
}


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

def _esc(s: str) -> str:
    return html.escape(s, quote=True)


def _safe_url(url: str) -> str:
    url = url.strip()
    if re.match(r"^(https?://|mailto:|/|#)", url, re.I):
        return url
    return "#"


def _color_class(color: str | None) -> str:
    if not color:
        return ""
    base = color[:-3] if color.endswith("_bg") else color
    if base not in _COLORS:
        return ""
    return f"c-{color}"


def _class_attr(*names: str) -> str:
    joined = " ".join(n for n in names if n)
    return f' class="{joined}"' if joined else ""


def _plain_text(fragment: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", fragment)).strip()


def _slug(text: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "section"


def _notion_id_from_url(url: str) -> str | None:
    path = url.split("?")[0].split("#")[0].rstrip("/")
    m = _NOTION_ID.search(path)
    return m.group(1).replace("-", "").lower() if m else None


def _is_notion_host(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in ("notion.so", "www.notion.so", "app.notion.com") or host.endswith(".notion.site")


class _Ctx:
    def __init__(self, resolve_page: PageResolver | None, base_path: str = ""):
        self._resolve_page = resolve_page
        self.base_path = base_path
        self.heading_ids: set[str] = set()

    def asset_url(self, url: str) -> str:
        """`media/<file>` refers to a file saved alongside the site by media.py."""
        url = url.strip()
        if re.match(r"^media/[\w.-]+$", url):
            return f"{self.base_path}/{url}"
        return _safe_url(url)

    def resolve_page(self, page_id: str) -> tuple[str | None, str | None]:
        if self._resolve_page is None:
            return None, None
        return self._resolve_page(page_id)

    def link_target(self, url: str) -> str | None:
        """Site-internal href for Notion page links, the URL itself for normal
        links, or None when the target is a private Notion page (so we don't
        publish a dead/private link)."""
        url = url.strip()
        if url.startswith("media/"):
            return self.asset_url(url)
        relative_notion = re.match(r"^/(?:p/)?[0-9a-f]{32}(?:[?#]|$)", url, re.I)
        if not relative_notion and not _is_notion_host(url):
            return _safe_url(url)
        page_id = _notion_id_from_url(url)
        if page_id:
            href, _ = self.resolve_page(page_id)
            if href:
                return href
        host = (urlparse(url).hostname or "").lower()
        if host.endswith(".notion.site"):
            return _safe_url(url)
        return None

    def heading_id(self, text: str) -> str:
        base = _slug(text)
        candidate, n = base, 2
        while candidate in self.heading_ids:
            candidate = f"{base}-{n}"
            n += 1
        self.heading_ids.add(candidate)
        return candidate


# --------------------------------------------------------------------------
# inline
# --------------------------------------------------------------------------

_EMPH = {"b": ("<strong>", "</strong>"), "i": ("<em>", "</em>"), "s": ("<del>", "</del>")}


class _Out:
    """Output buffer that keeps HTML well-nested even when the source toggles
    styles in an interleaved order."""

    def __init__(self) -> None:
        self.parts: list[str] = []
        self.stack: list[tuple[str, str, str]] = []  # (key, open_html, close_html)

    def text(self, s: str) -> None:
        self.parts.append(html.escape(s, quote=False))

    def raw(self, s: str) -> None:
        self.parts.append(s)

    def is_open(self, key: str) -> bool:
        return any(k == key for k, _, _ in self.stack)

    def open(self, key: str, open_html: str, close_html: str) -> None:
        self.parts.append(open_html)
        self.stack.append((key, open_html, close_html))

    def close(self, key: str) -> None:
        idx = max(i for i, (k, _, _) in enumerate(self.stack) if k == key)
        tail = self.stack[idx + 1:]
        for _, _, close_html in reversed(tail):
            self.parts.append(close_html)
        self.parts.append(self.stack[idx][2])
        del self.stack[idx:]
        for k, open_html, close_html in tail:
            self.parts.append(open_html)
            self.stack.append((k, open_html, close_html))

    def toggle(self, key: str) -> None:
        if self.is_open(key):
            self.close(key)
        else:
            self.open(key, *_EMPH[key])

    def toggle_all(self, keys: list[str]) -> None:
        # Close whatever is open innermost-first, then open the rest, so
        # `***x***` produces <strong><em>x</em></strong> without stray tags.
        open_now = [k for k, _, _ in reversed(self.stack) if k in keys]
        for k in open_now:
            self.toggle(k)
        for k in keys:
            if k not in open_now:
                self.toggle(k)

    def finish(self) -> str:
        while self.stack:
            self.close(self.stack[-1][0])
        out = "".join(self.parts)
        out = out.replace("</strong><strong>", "").replace("</em><em>", "")
        return re.sub(r"<(strong|em|del)></\1>", "", out)


def _find_matching(s: str, start: int, opener: str, closer: str) -> int:
    depth, i = 0, start
    while i < len(s):
        ch = s[i]
        if ch == "\\":
            i += 2
            continue
        if ch == opener:
            depth += 1
        elif ch == closer:
            depth -= 1
            if depth == 0:
                return i
        i += 1
    return -1


def _mention(tag: str, attrs: dict[str, str], inner: str, ctx: _Ctx) -> str:
    if tag == "mention-date":
        start, end = attrs.get("start", ""), attrs.get("end", "")
        label = f"{start} → {end}" if end else start
        return f'<time class="mention">{_esc(label)}</time>' if label else ""
    if tag in ("mention-user", "mention-agent"):
        return f'<span class="mention">@{_esc(inner)}</span>' if inner else ""

    url = attrs.get("url", "")
    title = inner
    href = None
    page_id = _notion_id_from_url(url)
    if page_id:
        href, resolved_title = ctx.resolve_page(page_id)
        title = title or resolved_title or ""
    if href is None and _is_notion_host(url) and (urlparse(url).hostname or "").endswith(".notion.site"):
        href = _safe_url(url)
    if not title:
        return ""
    if href:
        return f'<a class="mention" href="{_esc(href)}">{_esc(title)}</a>'
    return f'<span class="mention">{_esc(title)}</span>'


def render_inline(text: str, ctx: _Ctx) -> str:
    out = _Out()
    i, n = 0, len(text)
    while i < n:
        m = _SPECIAL.search(text, i)
        if not m:
            out.text(text[i:])
            break
        out.text(text[i:m.start()])
        i = m.start()
        ch = text[i]

        if ch == "\\":
            if i + 1 < n and text[i + 1] in _PUNCT:
                out.text(text[i + 1])
                i += 2
            else:
                out.text("\\")
                i += 1

        elif ch == "`":
            run = len(text[i:]) - len(text[i:].lstrip("`"))
            fence = "`" * run
            end = text.find(fence, i + run)
            if end == -1:
                out.text(fence)
                i += run
                continue
            code = text[i + run:end]
            body = "<br>".join(html.escape(part, quote=False) for part in code.split("<br>"))
            out.raw(f"<code>{body}</code>")
            i = end + run

        elif ch == "$":
            end = text.find("`$", i + 2) if text.startswith("$`", i) else -1
            if end == -1:
                out.text("$")
                i += 1
            else:
                out.raw(f'<span class="math">{_esc(text[i + 2:end])}</span>')
                i = end + 2

        elif ch == "*":
            j = i
            while j < n and text[j] == "*":
                j += 1
            run = j - i
            if run == 3:
                out.toggle_all(["b", "i"])
            else:
                for _ in range(run // 2):
                    out.toggle("b")
                if run % 2:
                    out.toggle("i")
            i = j

        elif ch == "~":
            if text.startswith("~~", i):
                out.toggle("s")
                i += 2
            else:
                out.text("~")
                i += 1

        elif ch == "[":
            cite = re.match(r"\[\^[^\]]*\]", text[i:])
            if cite:
                i += cite.end()
                continue
            close = _find_matching(text, i, "[", "]")
            if close != -1 and close + 1 < n and text[close + 1] == "(":
                pclose = _find_matching(text, close + 1, "(", ")")
                if pclose != -1:
                    label = render_inline(text[i + 1:close], ctx)
                    href = ctx.link_target(text[close + 2:pclose])
                    if href:
                        out.raw(f'<a href="{_esc(href)}">{label}</a>')
                    else:
                        out.raw(label)
                    i = pclose + 1
                    continue
            out.text("[")
            i += 1

        elif ch == "<":
            tm = _TAG_RE.match(text, i)
            if not tm:
                out.text("<")
                i += 1
                continue
            closing, tag, attr_str, selfclose = tm.groups()
            tag = tag.lower()
            attrs = dict(_ATTR_RE.findall(attr_str))
            i = tm.end()

            if tag == "br":
                out.raw("<br>")
            elif tag == "span":
                if closing:
                    if out.is_open("span"):
                        out.close("span")
                elif not selfclose:
                    classes = [_color_class(attrs.get("color"))]
                    if attrs.get("underline") == "true":
                        classes.append("u")
                    out.open("span", f"<span{_class_attr(*classes)}>", "</span>")
            elif tag.startswith("mention-") and not closing:
                inner = ""
                if not selfclose:
                    end = text.find(f"</{tag}>", i)
                    if end != -1:
                        inner = text[i:end]
                        i = end + len(f"</{tag}>")
                out.raw(_mention(tag, attrs, html.unescape(inner), ctx))
            elif tag.startswith("mention-"):
                pass
            else:
                out.text(tm.group(0))

    return out.finish()


# --------------------------------------------------------------------------
# blocks
# --------------------------------------------------------------------------

def _split_attrs(line: str) -> tuple[str, dict[str, str]]:
    m = _ATTRS_TAIL.search(line)
    if not m:
        return line, {}
    return line[:m.start()], dict(_ATTR_RE.findall(m.group(1)))


def _dedent(lines: list[str]) -> list[str]:
    if all(l.startswith("\t") for l in lines if l.strip()):
        return [l[1:] if l.startswith("\t") else l for l in lines]
    return lines


def _take_children(lines: list[str], i: int) -> tuple[list[str], int]:
    """Lines after `i` that are indented one level deeper, dedented by one tab."""
    n = len(lines)
    j, buf = i, []
    while j < n:
        l = lines[j]
        if l.startswith("\t"):
            buf.append(l[1:])
            j += 1
        elif not l.strip():
            k = j
            while k < n and not lines[k].strip():
                k += 1
            if k < n and lines[k].startswith("\t"):
                buf.extend([""] * (k - j))
                j = k
            else:
                break
        else:
            break
    return buf, j


def _children_html(children: list[str], ctx: _Ctx) -> str:
    if not any(c.strip() for c in children):
        return ""
    return f'<div class="block-children">{render_blocks(children, ctx)}</div>'


def _find_close(lines: list[str], i: int, name: str) -> int | None:
    open_re = re.compile(rf"^\s*<{re.escape(name)}(?=[\s/>])")
    close_re = re.compile(rf"^\s*</{re.escape(name)}>\s*$")
    depth = 1
    for j in range(i + 1, len(lines)):
        line = lines[j]
        if close_re.match(line):
            depth -= 1
            if depth == 0:
                return j
        elif open_re.match(line) and f"</{name}>" not in line and not line.rstrip().endswith("/>"):
            depth += 1
    return None


def _youtube_id(url: str) -> str | None:
    m = re.search(r"(?:youtube\.com/watch\?(?:[^#]*&)?v=|youtu\.be/|youtube\.com/embed/)([\w-]{11})", url)
    return m.group(1) if m else None


def _render_media(name: str, attrs: dict[str, str], caption_html: str, ctx: _Ctx) -> str:
    src = ctx.asset_url(attrs.get("src", ""))
    cap = f"<figcaption>{caption_html}</figcaption>" if caption_html else ""
    if src == "#":
        return ""
    if name == "video":
        yt = _youtube_id(src)
        if yt:
            return (
                '<figure class="media"><div class="embed-16x9"><iframe '
                f'src="https://www.youtube-nocookie.com/embed/{yt}" loading="lazy" '
                'title="Embedded video" allowfullscreen></iframe></div>'
                f"{cap}</figure>"
            )
        return f'<figure class="media"><video controls src="{_esc(src)}"></video>{cap}</figure>'
    if name == "audio":
        return f'<figure class="media"><audio controls src="{_esc(src)}"></audio>{cap}</figure>'
    if name == "embed":
        return (
            '<figure class="media"><div class="embed-16x9"><iframe sandbox="allow-scripts" '
            f'src="{_esc(src)}" loading="lazy" title="Embedded content"></iframe></div>{cap}</figure>'
        )
    label = caption_html or _esc(src.rsplit("/", 1)[-1].split("?")[0] or "Download")
    return f'<p class="file-link"><a href="{_esc(src)}">{label}</a></p>'


def _render_table(attrs: dict[str, str], body: list[str], ctx: _Ctx) -> str:
    joined = "\n".join(body)
    header_row = attrs.get("header-row") == "true"
    header_col = attrs.get("header-column") == "true"
    rows_html = []
    for r_idx, row in enumerate(re.finditer(r"<tr((?:\s[^>]*)?)>(.*?)</tr>", joined, re.S)):
        row_attrs = dict(_ATTR_RE.findall(row.group(1)))
        cells = []
        for c_idx, cell in enumerate(re.finditer(r"<td((?:\s[^>]*)?)>(.*?)</td>", row.group(2), re.S)):
            cell_attrs = dict(_ATTR_RE.findall(cell.group(1)))
            is_header = (header_row and r_idx == 0) or (header_col and c_idx == 0)
            tag = "th" if is_header else "td"
            scope = ""
            if is_header:
                scope = ' scope="col"' if (header_row and r_idx == 0) else ' scope="row"'
            cls = _class_attr(_color_class(cell_attrs.get("color") or row_attrs.get("color")))
            cells.append(f"<{tag}{scope}{cls}>{render_inline(cell.group(2).strip(), ctx)}</{tag}>")
        rows_html.append(f"<tr>{''.join(cells)}</tr>")
    fit = " fit" if attrs.get("fit-page-width") == "true" else ""
    return f'<div class="table-wrap{fit}"><table>{"".join(rows_html)}</table></div>'


def _render_tag(name: str, attrs: dict[str, str], inner: str | None,
                body: list[str], ctx: _Ctx) -> str:
    color = _color_class(attrs.get("color"))

    if name == "empty-block":
        return '<div class="empty-block" aria-hidden="true"></div>'

    if name == "callout":
        icon = attrs.get("icon", "")
        icon_html = ""
        if icon and not re.match(r"^[\w-]+/", icon):
            icon_html = f'<div class="callout-icon">{_esc(icon)}</div>'
        if inner is not None:
            content = f"<p>{render_inline(inner.strip(), ctx)}</p>"
        else:
            content = render_blocks(_dedent(body), ctx)
        return (
            f'<div{_class_attr("callout", color)}>{icon_html}'
            f'<div class="callout-body">{content}</div></div>'
        )

    if name == "columns":
        return f'<div class="columns">{render_blocks(_dedent(body), ctx)}</div>'

    if name == "column":
        ratio = attrs.get("ratio", "")
        style = f' style="flex: {float(ratio):g} 1 0"' if re.fullmatch(r"\d+(\.\d+)?", ratio) else ""
        return f'<div class="column"{style}>{render_blocks(_dedent(body), ctx)}</div>'

    if name == "details":
        lines = list(body)
        summary = ""
        for idx, l in enumerate(lines):
            if not l.strip():
                continue
            sm = re.match(r"\s*<summary>(.*)</summary>\s*$", l)
            if sm:
                summary = render_inline(sm.group(1), ctx)
                lines = lines[idx + 1:]
            break
        return (
            f'<details{_class_attr("toggle", color)}><summary>{summary}</summary>'
            f'{_children_html(_dedent(lines), ctx)}</details>'
        )

    if name == "table":
        return _render_table(attrs, body, ctx)

    if name == "tabs":
        return f'<div class="tabs">{render_blocks(_dedent(body), ctx)}</div>'

    if name == "tab":
        lines = _dedent(body)
        first = next((k for k, l in enumerate(lines) if l.strip()), None)
        if first is None:
            return ""
        title = render_inline(lines[first].strip(), ctx)
        return (
            f'<section class="tab"><h4 class="tab-title">{title}</h4>'
            f"{render_blocks(lines[first + 1:], ctx)}</section>"
        )

    if name in ("synced_block", "synced_block_reference"):
        return render_blocks(_dedent(body), ctx)

    if name == "page":
        title = _plain_text(inner or "")
        href, resolved = None, None
        page_id = _notion_id_from_url(attrs.get("url", ""))
        if page_id:
            href, resolved = ctx.resolve_page(page_id)
        title = title or resolved or ""
        if not title:
            return ""
        label = _esc(title)
        if href:
            label = f'<a href="{_esc(href)}">{label}</a>'
        return f'<p class="page-link">{label}</p>'

    if name in ("video", "audio", "file", "pdf", "embed"):
        return _render_media(name, attrs, render_inline((inner or "").strip(), ctx), ctx)

    return ""  # database, folder, table_of_contents, meeting-notes, unknown


def _match_list_item(line: str) -> tuple[str, str, str, int] | None:
    """Returns (kind, text, checked, number) for a list-item line at indent 0."""
    m = re.match(r"^-\s+\[([ xX])\](?:\s+(.*))?$", line)
    if m:
        return "todo", m.group(2) or "", "x" if m.group(1) in "xX" else "", 0
    m = re.match(r"^-(?:\s+(.*))?$", line)
    if m:
        return "ul", m.group(1) or "", "", 0
    m = re.match(r"^(\d+)\.(?:\s+(.*))?$", line)
    if m:
        return "ol", m.group(2) or "", "", int(m.group(1))
    return None


def _render_list(lines: list[str], i: int, ctx: _Ctx) -> tuple[str, int]:
    first = _match_list_item(lines[i])
    assert first is not None
    kind, start = first[0], first[3]
    items: list[str] = []
    n = len(lines)
    while i < n:
        item = _match_list_item(lines[i]) if not lines[i].startswith("\t") else None
        if item is None or item[0] != kind:
            break
        _, raw_text, checked, _num = item
        text, attrs = _split_attrs(raw_text)
        children, i = _take_children(lines, i + 1)
        cls = _class_attr("task" if kind == "todo" else "", _color_class(attrs.get("color")))
        box = ""
        if kind == "todo":
            box = f'<input type="checkbox" disabled{" checked" if checked else ""}> '
        items.append(f"<li{cls}>{box}{render_inline(text.strip(), ctx)}{_children_html(children, ctx)}</li>")
    if kind == "ol":
        start_attr = f' start="{start}"' if start > 1 else ""
        return f"<ol{start_attr}>{''.join(items)}</ol>", i
    cls = ' class="task-list"' if kind == "todo" else ""
    return f"<ul{cls}>{''.join(items)}</ul>", i


def render_blocks(lines: list[str], ctx: _Ctx) -> str:
    out: list[str] = []
    i, n = 0, len(lines)
    while i < n:
        raw = lines[i]
        if not raw.strip():
            i += 1
            continue
        line = raw.lstrip("\t").rstrip()  # stray indentation with no parent: treat as top level
        lines[i] = line
        stripped = line.strip()

        if stripped.startswith("```"):
            lang = stripped[3:].strip()
            j, buf = i + 1, []
            while j < n and lines[j].strip() != "```":
                buf.append(lines[j])
                j += 1
            i = j + 1
            cls = f' class="language-{_esc(lang)}"' if lang else ""
            out.append(f"<pre><code{cls}>{html.escape(chr(10).join(buf), quote=False)}</code></pre>")
            continue

        if stripped == "$$":
            j, buf = i + 1, []
            while j < n and lines[j].strip() != "$$":
                buf.append(lines[j].strip())
                j += 1
            i = j + 1
            out.append(f'<pre class="math">{html.escape(chr(10).join(buf), quote=False)}</pre>')
            continue

        tag = _OPEN_RE.match(stripped)
        if tag and tag.group(1) in _BLOCK_TAGS:
            name = tag.group(1)
            attrs = dict(_ATTR_RE.findall(tag.group(2)))
            rest = stripped[tag.end():]
            inner: str | None = None
            body: list[str] = []
            if tag.group(3):
                i += 1
            elif f"</{name}>" in rest:
                inner = rest[:rest.rindex(f"</{name}>")]
                i += 1
            else:
                close = _find_close(lines, i, name)
                if close is None:
                    body, i = lines[i + 1:], n
                else:
                    body, i = lines[i + 1:close], close + 1
            out.append(_render_tag(name, attrs, inner, body, ctx))
            continue

        if re.fullmatch(r"-{3,}", stripped):
            out.append("<hr>")
            i += 1
            continue

        if _match_list_item(line):
            list_html, i = _render_list(lines, i, ctx)
            out.append(list_html)
            continue

        heading = re.match(r"^(#{1,6})\s+(.*)$", line)
        if heading:
            level = min(len(heading.group(1)), 4)
            text, attrs = _split_attrs(heading.group(2))
            inline_html = render_inline(text.strip(), ctx)
            hid = ctx.heading_id(_plain_text(inline_html))
            children, i = _take_children(lines, i + 1)
            cls = _class_attr(_color_class(attrs.get("color")))
            h = f'<h{level} id="{hid}"{cls}>{inline_html}</h{level}>'
            if attrs.get("toggle") == "true":
                out.append(f'<details class="toggle-heading"><summary>{h}</summary>{_children_html(children, ctx)}</details>')
            else:
                out.append(h + _children_html(children, ctx))
            continue

        if line.startswith(">"):
            text, attrs = _split_attrs(line[1:].lstrip())
            children, i = _take_children(lines, i + 1)
            cls = _class_attr(_color_class(attrs.get("color")))
            out.append(f"<blockquote{cls}><p>{render_inline(text.strip(), ctx)}</p>{_children_html(children, ctx)}</blockquote>")
            continue

        text, attrs = _split_attrs(line)
        image = re.fullmatch(r"!\[((?:\\.|[^\]])*)\]\((\S+?)\)", text.strip())
        children, i = _take_children(lines, i + 1)
        if image:
            src = ctx.asset_url(image.group(2))
            caption = render_inline(image.group(1), ctx)
            alt = _esc(_plain_text(caption))
            cap = f"<figcaption>{caption}</figcaption>" if caption else ""
            out.append(f'<figure class="media"><img src="{_esc(src)}" alt="{alt}" loading="lazy">{cap}</figure>')
        else:
            cls = _class_attr(_color_class(attrs.get("color")))
            out.append(f"<p{cls}>{render_inline(text.strip(), ctx)}</p>")
        out.append(_children_html(children, ctx))

    return "".join(out)


def notion_markdown_to_html(source: str, resolve_page: PageResolver | None = None,
                            base_path: str = "") -> str:
    ctx = _Ctx(resolve_page, base_path)
    lines = source.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return render_blocks(lines, ctx)
