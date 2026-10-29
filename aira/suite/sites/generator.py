"""What the builder asks a model, and how its streamed answer becomes files.

The model answers in a small tagged format that streams well and survives any file content::

    <plan>What I will build.</plan>
    <file path="index.html">
    ...the whole file...
    </file>
    <delete path="old.html"/>
    <summary>What changed.</summary>

:class:`Reader` follows the answer as it streams (which file is being written, how much of it, which are done);
:func:`parse` reads the finished answer, falling back to Markdown code fences or a bare HTML document when a small
model ignores the format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from aira.suite.sites import store

STACKS: dict[str, dict[str, str]] = {
    "html": {
        "label": "HTML, CSS and JavaScript",
        "rules": "Use plain HTML5, one stylesheet (styles.css) with CSS custom properties, and vanilla JavaScript "
        "(script.js) only where it helps. No frameworks and no build step.",
    },
    "tailwind": {
        "label": "Tailwind CSS",
        "rules": 'Use Tailwind CSS from its CDN (<script src="https://cdn.tailwindcss.com"></script> in the head, '
        "with a small tailwind.config in an inline script for brand colors) and vanilla JavaScript. No build step.",
    },
    "react": {
        "label": "React (no build step)",
        "rules": "Use React 18 from unpkg (react.production.min.js and react-dom.production.min.js UMD builds) and "
        "@babel/standalone, with the components in app.jsx loaded by "
        '<script type="text/babel" data-presets="react" src="app.jsx"></script>. Plain CSS in styles.css. '
        "No imports, no bundler: use React and ReactDOM globals.",
    },
}
DEFAULT_STACK = "html"

SYSTEM = """You are Monosage Sites, a senior web designer and front-end engineer. You build complete, beautiful,
production-quality websites from a person's description, and you change them when asked.

Design bar: a clear visual hierarchy, generous spacing, a modern type scale (system font stack or one Google Font),
a coherent color palette with good contrast, tasteful gradients or imagery, subtle motion, and a layout that works
from a 360px phone to a wide desktop (mobile first, a menu that collapses on phones). Write real, specific copy for
the business or idea described: names, headlines, prices, opening hours, testimonials. Never lorem ipsum.

Answer in exactly this format and nothing else:
<plan>One to three short sentences: what you will build or change.</plan>
<file path="index.html">
the complete file
</file>
(one <file> block per file you write)
<summary>One or two sentences for the person: what you built or changed, and an idea for what to do next.</summary>

Rules:
- Every <file> block holds the WHOLE file. Never write "...", "rest unchanged" or partial snippets.
- When changing an existing site, write only the files that change. To remove a file write <delete path="name"/>.
- index.html is the home page. Other pages are separate .html files linked from the navigation.
- Paths are relative and plain (index.html, styles.css, script.js, about.html, assets/logo.svg). Link and load files
  with relative paths (href="styles.css"), never starting with "/". Do not use <base>.
- Images: use the person's uploaded files when listed (assets/<name>), otherwise
  photos that match the topic from https://loremflickr.com/1200/800/<keyword>?lock=<number> (e.g. coffee, bakery,
  gym, office; a different lock number for each picture), inline SVG, or CSS gradients. Give every image alt text.
- Accessibility: semantic landmarks, labels on form fields, visible focus, alt text, sufficient contrast.
- There is no server: a contact form shows a friendly thank-you message with JavaScript (or uses mailto:).
- Wrap any use of localStorage in try/catch: the preview runs in a sandbox where it may be unavailable.
- Do not put code fences inside <file> blocks. Do not explain outside the tags."""


def system_prompt(stack: str) -> str:
    spec = STACKS.get(stack) or STACKS[DEFAULT_STACK]
    return f"{SYSTEM}\n\nStack: {spec['label']}. {spec['rules']}"


MAX_CONTEXT_CHARS = 90_000


def user_prompt(
    request: str,
    *,
    files: dict[str, str],
    history: list[dict[str, Any]] | None = None,
    assets: list[dict[str, Any]] | None = None,
    project_name: str = "",
) -> str:
    """The message for one run: the request, and for a change the site's current files and the recent turns."""
    lines: list[str] = []
    if assets:
        lines.append("Files the person uploaded (use them where they fit):")
        lines.extend(f"- assets/{a['name']} ({a['mime']})" for a in assets)
        lines.append("")
    if files:
        lines.append(f'This is the current website "{project_name}". Its files:')
        budget = MAX_CONTEXT_CHARS
        ordered = sorted(
            files, key=lambda p: (p != "index.html", not p.endswith(".html"), p)
        )
        for path in ordered:
            content = files[path]
            if len(content) > budget:
                lines.append(
                    f'<file path="{path}" note="too long to show; rewrite it whole only if needed"/>'
                )
                continue
            budget -= len(content)
            lines.append(f'<file path="{path}">\n{content}\n</file>')
        lines.append("")
        turns = [m for m in (history or []) if m.get("role") in ("user", "assistant")][
            -6:
        ]
        if turns:
            lines.append("Recent conversation:")
            for turn in turns:
                who = "Person" if turn["role"] == "user" else "You"
                lines.append(f"{who}: {str(turn.get('content') or '')[:600]}")
            lines.append("")
        lines.append(
            "Change request (write only the files that change, each one whole):"
        )
    else:
        lines.append("Build this website:")
    lines.append(request.strip())
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# reading the answer
# ---------------------------------------------------------------------------

_OPEN_RE = re.compile(r"<file\s+path\s*=\s*[\"']([^\"'>]{1,200})[\"']\s*>")
_CLOSE = "</file>"
_DELETE_RE = re.compile(r"<delete\s+path\s*=\s*[\"']([^\"'>]{1,200})[\"']\s*/?>")
_PLAN_RE = re.compile(r"<plan>(.*?)(?:</plan>|$)", re.S)
_SUMMARY_RE = re.compile(r"<summary>(.*?)(?:</summary>|$)", re.S)
_FENCE_RE = re.compile(r"```([A-Za-z0-9_+-]*)[ \t]*([^\n`]*)\n(.*?)```", re.S)
_FENCE_NAMES = {
    "html": "index.html",
    "htm": "index.html",
    "css": "styles.css",
    "js": "script.js",
    "javascript": "script.js",
    "jsx": "app.jsx",
    "json": "data.json",
    "svg": "image.svg",
}


@dataclass
class Result:
    plan: str = ""
    summary: str = ""
    files: dict[str, str] = field(default_factory=dict)
    deleted: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # paths that were not acceptable
    format: str = "tags"  # tags | fences | document | none


def _strip_fence(content: str) -> str:
    text = content.strip("\n")
    match = re.fullmatch(r"\s*```[A-Za-z0-9_+-]*[^\n]*\n(.*?)\n?```\s*", text, re.S)
    return match.group(1) if match else text


def _accept(result: Result, path: str, content: str) -> None:
    try:
        clean = store.clean_path(path)
    except ValueError:
        result.skipped.append(path[:120])
        return
    result.files[clean] = _strip_fence(content).rstrip() + "\n"


_BLOCK_RE = re.compile(
    r"<file\s+path\s*=\s*[\"'][^\"'>]{1,200}[\"']\s*>.*?(?:</file>|$)", re.S
)


def outside_files(text: str) -> str:
    """The answer without its file contents: a page's own <summary> (a FAQ's <details>) is not the model's summary."""
    return _BLOCK_RE.sub(" ", text)


def parse(text: str) -> Result:
    """Files, deletions, plan and summary from a finished answer."""
    result = Result()
    talk = outside_files(text)
    plan = _PLAN_RE.search(talk)
    summary = _SUMMARY_RE.search(talk)
    result.plan = plan.group(1).strip()[:1000] if plan else ""
    result.summary = summary.group(1).strip()[:2000] if summary else ""
    position = 0
    while True:
        match = _OPEN_RE.search(text, position)
        if not match:
            break
        end = text.find(_CLOSE, match.end())
        if end < 0:
            # Cut off mid-file: keep it only if the model got far enough to end the document.
            tail = text[match.end() :]
            if "</html>" in tail.lower():
                _accept(
                    result, match.group(1), tail[: tail.lower().rfind("</html>") + 7]
                )
            break
        _accept(result, match.group(1), text[match.end() : end])
        position = end + len(_CLOSE)
    for match in _DELETE_RE.finditer(text):
        try:
            result.deleted.append(store.clean_path(match.group(1)))
        except ValueError:
            result.skipped.append(match.group(1)[:120])
    if result.files or result.deleted:
        return result
    # A model that ignored the format: Markdown fences (```html, ```css ...), named or not.
    used: set[str] = set()
    for match in _FENCE_RE.finditer(text):
        language, label, content = (
            match.group(1).lower(),
            match.group(2).strip(),
            match.group(3),
        )
        name = re.search(r"[\w\-./]+\.\w+", label)
        path = name.group(0) if name else _FENCE_NAMES.get(language, "")
        if not path or path in used:
            continue
        used.add(path)
        _accept(result, path, content)
    if result.files:
        result.format = "fences"
        return result
    lowered = text.lower()
    start = lowered.find("<!doctype html")
    if start < 0:
        start = lowered.find("<html")
    if start >= 0:
        end = lowered.rfind("</html>")
        _accept(
            result, "index.html", text[start : end + 7] if end > start else text[start:]
        )
        result.format = "document"
        return result
    result.format = "none"
    return result


class Reader:
    """Follows a streaming answer: the file being written now, its partial text, and the files finished."""

    def __init__(self) -> None:
        self.text = ""
        self.current = ""
        self.done: list[str] = []
        self.phase = "thinking"  # thinking | planning | writing | finishing

    def feed(self, piece: str) -> None:
        self.text += piece
        opens = list(_OPEN_RE.finditer(self.text))
        closed = self.text.count(_CLOSE)
        self.done = [m.group(1) for m in opens[:closed]]
        if len(opens) > closed:
            self.current = opens[-1].group(1)
            self.phase = "writing"
        else:
            self.current = ""
            if "<summary>" in outside_files(self.text):
                self.phase = "finishing"
            elif opens:
                self.phase = "writing"
            elif "<plan>" in self.text or self.text.strip():
                self.phase = "planning"

    @property
    def plan(self) -> str:
        match = _PLAN_RE.search(outside_files(self.text))
        return match.group(1).strip()[:1000] if match else ""
