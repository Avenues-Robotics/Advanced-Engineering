#!/usr/bin/env python3
"""
Offline tests for notion_md.py, using snippets copied from real Notion
enhanced-markdown exports of the published pages.

Run with:  python3 tests/test_notion_md.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from notion_md import notion_markdown_to_html as convert  # noqa: E402

failures: list[str] = []


def check(label: str, condition: bool, html: str = "") -> None:
    print(f"[{'PASS' if condition else 'FAIL'}] {label}")
    if not condition:
        failures.append(label)
        if html:
            print("    output was:\n    " + html.replace("\n", "\n    "))


def main() -> int:
    # -- paragraphs / lists / escapes (Program Overview) -----------------------
    md = (
        "Importantly, participation is not mutually dependent:\n"
        "- Students may participate without enrolling.\n"
        "- Students work alongside teammates.\n"
        "This model preserves the inclusive nature.\n"
        "## **Instructional Model**\n"
        "- Robotics meets 2–3 times per week for \\~90 minutes from 4\\:00-5\\:30\n"
        "- limited at-home work (typically \\<1 hour per week)\n"
    )
    out = convert(md)
    check("each source line is its own paragraph", "<p>This model preserves the inclusive nature.</p>" in out, out)
    check("list directly after a paragraph becomes a real <ul>", out.count("<ul>") == 2 and "<li>Students may participate" in out, out)
    check("heading with bold text renders", '<h2 id="instructional-model"><strong>Instructional Model</strong></h2>' in out, out)
    check("\\~ \\: escapes are unescaped", "~90 minutes from 4:00-5:30" in out and "\\" not in out, out)
    check("\\< becomes a literal <, HTML-escaped", "&lt;1 hour" in out and "\\&lt;" not in out, out)

    # -- table (raw HTML table with markdown cells) --------------------------------
    md = (
        "<table>\n<colgroup>\n<col>\n<col width=\"489\">\n</colgroup>\n"
        "<tr>\n<td>**Course**</td>\n<td>**Primary Goals**</td>\n</tr>\n"
        "<tr>\n<td>**Robotics Engineering: Mechanical 1**</td>\n"
        "<td>Introduces the process.**  **ME2** **advances rigor.</td>\n</tr>\n</table>\n"
    )
    out = convert(md)
    check("table cells render bold, not literal asterisks", "**" not in out and "<strong>Course</strong>" in out, out)
    check("table has 2 rows x 2 cells", out.count("<tr>") == 2 and out.count("<td") == 4, out)
    out = convert('<table header-row="true">\n<tr>\n<td>Term</td>\n<td>Definition</td>\n</tr>\n</table>')
    check("header-row=true makes <th>", "<th" in out and "<td" not in out, out)

    # -- callout with nested children, details toggle, task list ----------------------
    md = (
        "<callout color=\"gray_bg\">\n"
        "\t**Competition Robotics Example**\n"
        "\tHere is how one team might describe it.\n"
        "\t- **Close Shooting:** The shooter shoots.\n"
        "\t- **Far Shooting:** From the far zone.\n"
        "</callout>\n"
        "<empty-block/>\n"
        "after"
    )
    out = convert(md)
    check("callout becomes a div with its color class", '<div class="callout c-gray_bg">' in out, out)
    check("callout contents parsed as blocks (list inside)", "<ul><li><strong>Close Shooting:</strong>" in out, out)
    check("no raw <callout> tag leaks", "<callout" not in out and "</callout>" not in out, out)
    check("<empty-block/> becomes a spacer", 'class="empty-block"' in out and "empty-block/>" not in out, out)

    md = (
        "<callout color=\"gray_bg\">\n"
        "\t<details>\n"
        "\t<summary>🔵 <span color=\"blue\">**Advanced Functional Requirements**</span></summary>\n"
        "\t\tAt the basic level, a requirement is a shall statement.\n"
        "\t\t**Completeness** \n"
        "\t\t\tCompleteness means every scenario generates a requirement.\n"
        "\t\t<callout color=\"gray_bg\">\n"
        "\t\t\t> **Rationale**: Derived from the jam scenario.\n"
        "\t\t</callout>\n"
        "\t\t- [ ] **Correctly written**: single shall statement\n"
        "\t\t- [x] **Feasible**: achievable\n"
        "\t</details>\n"
        "</callout>\n"
    )
    out = convert(md)
    check("toggle renders as <details> with a summary", "<details" in out and "<summary>" in out, out)
    check("summary keeps colored bold text",
          '<span class="c-blue"><strong>Advanced Functional Requirements</strong></span>' in out, out)
    check("nested callout inside toggle inside callout", out.count('<div class="callout ') == 2, out)
    check("indented lines become children of the preceding block",
          '<p><strong>Completeness</strong></p><div class="block-children"><p>Completeness means' in out, out)
    check("blockquote inside nested callout", "<blockquote><p><strong>Rationale</strong>: Derived" in out, out)
    check("task list checkboxes, one checked",
          out.count('type="checkbox"') == 2 and out.count(" checked>") == 1, out)
    check("no raw <details>/<summary> text leaks", "&lt;details" not in out and "&lt;summary" not in out, out)

    # -- run-styled emphasis (Notion emits each styled run separately) --------------------------
    md = "*Notice that the last is an *<span color=\"green_bg\">***edge case***</span>*, not the primary use.*"
    out = convert(md)
    check("italic run / bold-italic span / italic run nests correctly",
          '<em>Notice that the last is an </em><span class="c-green_bg"><strong><em>edge case</em></strong></span>'
          "<em>, not the primary use.</em>" in out, out)
    check("no stray asterisks", "*" not in out, out)

    out = convert("A <span color=\"red\">**`functional requirement`**</span> answers *what*.")
    check("colored bold inline code", '<span class="c-red"><strong><code>functional requirement</code></strong></span>' in out, out)
    out = convert("The shooter <span underline=\"true\">shall</span> score.")
    check("underline span", '<span class="u">shall</span>' in out, out)

    # -- columns ----------------------------------------------------------------------------------
    md = (
        "<columns>\n"
        "\t<column ratio=\"62\">\n\t\tLeft text\n\t</column>\n"
        "\t<column ratio=\"38\">\n\t\tRight text\n\t</column>\n"
        "</columns>"
    )
    out = convert(md)
    check("columns render as flex columns with ratios",
          '<div class="columns">' in out and "flex: 62 1 0" in out and "flex: 38 1 0" in out, out)

    # -- media, links, mentions, dividers, numbered list ------------------------------------------
    out = convert('<video src="https://www.youtube.com/watch?v=wMYfWO8DYgQ"></video>')
    check("YouTube video becomes an embed", "youtube-nocookie.com/embed/wMYfWO8DYgQ" in out, out)

    out = convert("[NASA handbook](https://www.nasa.gov/x.pdf) (chapter 4.2)\n---\n1. one\n2. two\n3. three")
    check("external link + divider + ordered list",
          '<a href="https://www.nasa.gov/x.pdf">NASA handbook</a>' in out and "<hr>" in out
          and out.count("<li>") == 3 and "<ol>" in out, out)

    def resolver(page_id: str):
        return {
            "3d0ec788f84080ff83d4c617cf7b6d1c": ("/Advanced-Engineering/scs-course-outcomes/", "SCS Course Outcomes"),
            "375ec788f840818b8c69e9075d261be8": (None, "Unpublished Thing"),
        }.get(page_id, (None, None))

    md = (
        '<mention-page url="https://app.notion.com/p/3d0ec788f84080ff83d4c617cf7b6d1c"/>\n'
        '<mention-page url="https://app.notion.com/p/375ec788f840818b8c69e9075d261be8"/>\n'
        '<page url="https://app.notion.com/p/3d0ec788f84080ff83d4c617cf7b6d1c">SCS Course Outcomes</page>\n'
        "[secret](https://app.notion.com/p/aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa)"
    )
    out = convert(md, resolver)
    check("mention of a published page links to the site page with its title",
          '<a class="mention" href="/Advanced-Engineering/scs-course-outcomes/">SCS Course Outcomes</a>' in out, out)
    check("mention of an unpublished page is plain text, not a link",
          '<span class="mention">Unpublished Thing</span>' in out, out)
    check("<page> block links to the published site page",
          '<p class="page-link"><a href="/Advanced-Engineering/scs-course-outcomes/">' in out, out)
    check("link to a private Notion page is dropped to plain text", "notion.com" not in out and "secret" in out, out)

    # -- code, headings, safety ---------------------------------------------------------------------
    out = convert("```java\nif (a < b) { x[0] = 1; }\n```\n### Sub *heading* {color=\"blue\"}")
    check("code block is literal and escaped", "if (a &lt; b) { x[0] = 1; }" in out and 'class="language-java"' in out, out)
    check("heading attribute list becomes a color class", 'class="c-blue"' in out and "{color" not in out, out)

    out = convert("<script>alert(1)</script> and [x](javascript:alert(1))")
    check("raw script tags are escaped", "<script>" not in out and "&lt;script&gt;" in out, out)
    check("javascript: links are neutralised", "javascript:" not in out.replace("&lt;", ""), out)

    print()
    if failures:
        print(f"{len(failures)} check(s) FAILED:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("All checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
