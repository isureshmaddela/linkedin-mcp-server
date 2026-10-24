"""Legal pages (terms, privacy, data processing, security, automation) served from ``docs/legal``.

Public, GET only, and only the whitelisted file names. The markdown is rendered by a small renderer
that escapes everything first and then recognises only headings, paragraphs, lists, tables, quotes,
code, emphasis and links (https links and links between the legal pages). Raw HTML in a document is
shown as text, and any other link is shown as its text.
"""

from __future__ import annotations

import html
import re
from pathlib import Path

from aira_cloud.webio import Reply, Req, cloud_route

DOC_DIR = Path(__file__).resolve().parents[2] / "docs" / "legal"
PAGES = {
    "terms": "Terms of Service",
    "privacy": "Privacy notice",
    "dpa": "Data Processing Agreement",
    "security": "Security and vulnerability disclosure",
    "automation-and-linkedin": "Automation rules and the LinkedIn notice",
}

LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s]+)\)")
CODE_RE = re.compile(r"`([^`]+)`")
BOLD_RE = re.compile(r"\*\*([^*]+)\*\*")
ITALIC_RE = re.compile(r"(?<![\w*])\*([^*\n]+)\*(?![\w*])")
SAFE_PLACEHOLDER = "\x00"


def _link(match: re.Match[str]) -> str:
    text, target = match.group(1), html.unescape(match.group(2))
    if target.startswith("https://"):
        return f'<a href="{html.escape(target, quote=True)}" rel="noopener noreferrer">{text}</a>'
    name = target.split("#", 1)[0]
    page = name.rsplit("/", 1)[-1].removesuffix(".md")
    if (
        name.endswith(".md")
        and page in PAGES
        and name in {f"{page}.md", f"legal/{page}.md", f"../legal/{page}.md"}
    ):
        return f'<a href="/legal/{page}">{text}</a>'
    return text  # not served by the application (other docs, relative paths, other schemes)


def inline(text: str) -> str:
    """Inline formatting on text that is escaped here, so nothing from a document becomes markup."""
    out = html.escape(text, quote=False)
    stash: list[str] = []

    def keep(match: re.Match[str]) -> str:
        stash.append(f"<code>{match.group(1)}</code>")
        return f"{SAFE_PLACEHOLDER}{len(stash) - 1}{SAFE_PLACEHOLDER}"

    out = CODE_RE.sub(keep, out)
    out = LINK_RE.sub(_link, out)
    out = BOLD_RE.sub(r"<strong>\1</strong>", out)
    out = ITALIC_RE.sub(r"<em>\1</em>", out)
    return re.sub(
        f"{SAFE_PLACEHOLDER}(\\d+){SAFE_PLACEHOLDER}",
        lambda m: stash[int(m.group(1))],
        out,
    )


def render(markdown: str) -> str:
    lines = markdown.splitlines()
    out: list[str] = []
    index = 0

    def table_cells(line: str) -> list[str]:
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    while index < len(lines):
        line = lines[index]
        stripped = line.strip()
        if not stripped:
            index += 1
        elif stripped.startswith("```"):
            index += 1
            block: list[str] = []
            while index < len(lines) and not lines[index].strip().startswith("```"):
                block.append(lines[index])
                index += 1
            index += 1
            out.append(f"<pre><code>{html.escape(chr(10).join(block))}</code></pre>")
        elif re.match(r"#{1,4}\s", stripped):
            level = len(stripped) - len(stripped.lstrip("#"))
            out.append(f"<h{level}>{inline(stripped[level:].strip())}</h{level}>")
            index += 1
        elif stripped.startswith(">"):
            quote: list[str] = []
            while index < len(lines) and lines[index].strip().startswith(">"):
                quote.append(lines[index].strip().lstrip(">").strip())
                index += 1
            out.append(f"<blockquote><p>{inline(' '.join(quote))}</p></blockquote>")
        elif (
            stripped.startswith("|")
            and index + 1 < len(lines)
            and re.match(r"^\|?[\s:|-]+\|[\s:|-]*$", lines[index + 1].strip())
        ):
            head = table_cells(stripped)
            index += 2
            rows: list[list[str]] = []
            while index < len(lines) and lines[index].strip().startswith("|"):
                rows.append(table_cells(lines[index]))
                index += 1
            body = "".join(
                "<tr>" + "".join(f"<td>{inline(cell)}</td>" for cell in row) + "</tr>"
                for row in rows
            )
            out.append(
                "<table class='table'><thead><tr>"
                + "".join(f"<th>{inline(cell)}</th>" for cell in head)
                + f"</tr></thead><tbody>{body}</tbody></table>"
            )
        elif re.match(r"([-*]|\d+\.)\s", stripped):
            ordered = bool(re.match(r"\d+\.\s", stripped))
            items: list[str] = []
            while index < len(lines) and re.match(r"\s*([-*]|\d+\.)\s", lines[index]):
                items.append(re.sub(r"^\s*([-*]|\d+\.)\s+", "", lines[index]))
                index += 1
            tag = "ol" if ordered else "ul"
            out.append(
                f"<{tag}>"
                + "".join(f"<li>{inline(item)}</li>" for item in items)
                + f"</{tag}>"
            )
        else:
            paragraph = [stripped]
            index += 1
            while (
                index < len(lines)
                and lines[index].strip()
                and not re.match(
                    r"(#{1,4}\s|>|\||```|([-*]|\d+\.)\s)", lines[index].strip()
                )
            ):
                paragraph.append(lines[index].strip())
                index += 1
            out.append(f"<p>{inline(' '.join(paragraph))}</p>")
    return "\n".join(out)


PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{title} · Aira</title><link rel="stylesheet" href="/static/app.css">
<style>body{{margin:0;background:var(--bg,#fff);color:var(--text,#111)}}main.legal{{max-width:820px;margin:0 auto;padding:24px 16px 64px;line-height:1.6}}
main.legal table{{width:100%;border-collapse:collapse;display:block;overflow-x:auto}}main.legal td,main.legal th{{border:1px solid var(--border,#ccc);padding:6px 8px;vertical-align:top;text-align:left}}
main.legal blockquote{{border-left:4px solid var(--border,#ccc);margin:16px 0;padding:4px 16px;background:var(--surface,#f6f6f6)}}main.legal pre{{overflow-x:auto}}
nav.legal-nav a{{margin-right:12px}}</style></head><body><main class="legal"><nav class="legal-nav" aria-label="Legal pages"><a href="/">Aira</a>{nav}</nav>{body}</main></body></html>"""


def _nav(current: str) -> str:
    links = []
    for name, title in PAGES.items():
        marker = ' aria-current="page"' if name == current else ""
        links.append(f'<a href="/legal/{name}"{marker}>{html.escape(title)}</a>')
    return "".join(links)


def _html(status: int, title: str, body: str, current: str = "") -> Reply:
    page = PAGE.format(title=html.escape(title), nav=_nav(current), body=body)
    return Reply(
        status,
        page.encode("utf-8"),
        content_type="text/html; charset=utf-8",
        headers=[("Cache-Control", "no-cache")],
    )


@cloud_route("GET", r"/legal", auth="public")
def legal_index(req: Req):  # noqa: ANN201
    items = "".join(
        f'<li><a href="/legal/{name}">{html.escape(title)}</a></li>'
        for name, title in PAGES.items()
    )
    return _html(
        200,
        "Legal",
        f"<h1>Legal</h1><p>Templates. They have not been reviewed by counsel.</p><ul>{items}</ul>",
    )


@cloud_route("GET", r"/legal/([a-z-]{3,40})", auth="public")
def legal_page(req: Req):  # noqa: ANN201
    name = req.group()
    if name not in PAGES:
        return Reply(404, {"error": "Not found.", "code": "not_found"})
    path = DOC_DIR / f"{name}.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return Reply(
            404, {"error": "This document is not installed.", "code": "not_found"}
        )
    return _html(200, PAGES[name], render(text), name)
