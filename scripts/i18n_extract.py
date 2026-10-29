"""Collect every English string a person can see into auto_apply_assistant/i18n/catalog.json.

Sources:
* the browser screens (``auto_apply_assistant/*.js``): string and template literals that read as text;
  ``${...}`` in a template becomes ``{0}``, ``{1}``...
* the server (``aira/``, ``aira_cloud/``, ``auto_apply_assistant.py``): error messages people read, and the
  values of keys and arguments that are shown on screen (``title``, ``text``, ``label``, ``empty`` ...);
  an f-string's ``{expression}`` becomes ``{0}``...
* the app catalog (names, taglines, descriptions, highlights) and common status words.

The translator in the browser (``i18n.js``) matches text exactly, or against these patterns. Run it after
changing visible text: ``uv run python scripts/i18n_extract.py``; ``--check`` exits 1 when the catalog is stale.
"""

from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "auto_apply_assistant"
CATALOG = STATIC / "i18n" / "catalog.json"

CSS_HINTS = {
    "btn",
    "sm",
    "ghost",
    "secondary",
    "primary",
    "danger",
    "muted",
    "small",
    "card",
    "hint",
    "row",
    "active",
    "disabled",
    "hidden",
    "icon",
    "good",
    "bad",
    "warn",
    "info",
    "done",
    "open",
    "selected",
    "account",
    "menu",
    "item",
    "grid",
    "list",
    "badge",
    "pill",
    "num",
    "table",
    "wrap",
    "head",
    "title",
    "label",
    "field",
    "check",
    "error",
    "success",
    "warning",
    "public",
    "dot",
    "count",
    "stat",
    "value",
    "sub",
    "tile",
    "close",
    "ok",
}
STATUS_WORDS = (
    "paid",
    "unpaid",
    "pending",
    "draft",
    "sent",
    "accepted",
    "declined",
    "expired",
    "overdue",
    "void",
    "active",
    "archived",
    "cancelled",
    "confirmed",
    "completed",
    "delivered",
    "shipped",
    "packed",
    "placed",
    "refunded",
    "failed",
    "approved",
    "published",
    "scheduled",
    "idea",
    "live",
    "ended",
    "booked",
    "no show",
    "checked in",
    "valid",
    "waiting",
    "open",
    "closed",
    "new",
    "contacted",
    "replied",
    "qualified",
    "disqualified",
    "customer",
    "won",
    "lost",
    "included",
    "granted",
    "off",
    "unavailable",
    "cancelling",
    "partially paid",
    "trialing",
    "past due",
    "read only",
    "on hold",
    "planned",
    "doing",
    "todo",
    "taken",
    "skipped",
    "missed",
    "due",
    "upcoming",
    "snoozed",
    "going",
    "maybe",
    "not going",
    "subscribed",
    "unsubscribed",
    "ready",
    "not set up",
    "healthy",
)
SERVER_KEYS = {
    "title",
    "text",
    "sub",
    "label",
    "empty",
    "body",
    "hint",
    "error",
    "message",
    "tagline",
    "description",
    "reason",
    "greeting",
    "note",
    "placeholder",
    "summary",
    "headline",
    "subtitle",
    "plan_hint",
}
CALL_ARGS = {"card": (1,), "step": (1, 3), "action": (0,), "log": (3,)}
EXCEPTION_HINT = re.compile(
    r"(Error|Exception|Failed|Required|Forbidden|NotFound|Conflict|Gone|Unauthorized|Inactive|Locked|Required|Disabled)$"
)
SQL = re.compile(
    r"^\s*(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|PRAGMA|WITH|BEGIN|ORDER BY|GROUP BY|WHERE|AND|OR|LIMIT|JOIN|LEFT JOIN|ON CONFLICT|VALUES|SET|FROM)\b"
)


def looks_like_text(value: str) -> bool:
    text = value.strip()
    if (
        len(text) < 2
        or len(text) > 700
        or not re.search(r"[A-Za-z]", text)
        or "�" in text
    ):
        return False
    literal = re.sub(r"\{\d+\}", " ", text).strip()
    if "{" in text and (
        len(re.findall(r"[A-Za-z]", literal)) < 3 or not _plain(literal)
    ):
        return False  # a pattern needs real words around its placeholders
    if " " not in literal and re.search(r"[/?=&]", text):
        return False  # a path or a query string
    if text.startswith(
        ("/", "#", ".", "http", "data:", "aria-", "<", "--", "@", "&", "?")
    ) or SQL.match(text):
        return False
    if any(
        marker in text
        for marker in (
            "=>",
            "function(",
            "querySelector",
            "var(--",
            "rgba(",
            "px;",
            "\\u",
            "${",
        )
    ):
        return False
    if re.search(r"[{}]", re.sub(r"\{\d+\}", "", text)):
        return False
    if re.fullmatch(r"[A-Za-z0-9_.:\-/]+", text) and not re.match(r"^[A-Z][a-z]", text):
        return False  # identifiers, keys, class names, paths
    if " " not in text and re.fullmatch(r"[A-Z0-9_]+", text):
        return False  # CONSTANTS
    tokens = text.split()
    if all(re.fullmatch(r"[a-z0-9-]+", token) for token in tokens) and len(tokens) <= 5:
        if any("-" in token or token in CSS_HINTS for token in tokens):
            return False  # a class list
    if re.fullmatch(r"[a-z]+(\.[a-z_]+)+", text):
        return False  # dotted names (permissions, modules)
    if re.search(r"\.(js|css|py|json|png|svg|html)$", text):
        return False
    return True


def _plain(literal: str) -> bool:
    """The words around placeholders read as text (not a class list or an identifier)."""
    tokens = [token for token in re.split(r"\s+", literal) if token]
    if not tokens:
        return False
    if (
        all(re.fullmatch(r"[a-z0-9_.:-]+", token) for token in tokens)
        and len(tokens) <= 3
    ):
        return any(
            token.isalpha() and token not in CSS_HINTS and len(token) > 3
            for token in tokens
        ) and not any("-" in token or "_" in token or "." in token for token in tokens)
    return True


# ---------------------------------------------------------------------------
# JavaScript
# ---------------------------------------------------------------------------

REGEX_BEFORE = set("(,=:[!&|?{};+-*%<>~^")


def _read_string(src: str, i: int, quote: str) -> tuple[str, int]:
    out = []
    i += 1
    while i < len(src):
        char = src[i]
        if char == "\\":
            nxt = src[i + 1] if i + 1 < len(src) else ""
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        if char == quote:
            return "".join(out), i + 1
        if char == "\n":
            return "".join(out), i + 1  # unterminated: give up on this one
        out.append(char)
        i += 1
    return "".join(out), i


def _read_template(src: str, i: int, found: list[str]) -> tuple[str, int]:
    """A template literal from the backtick at ``i``: its text with {0}.. and the end index."""
    out = []
    holes = 0
    i += 1
    while i < len(src):
        char = src[i]
        if char == "\\":
            nxt = src[i + 1] if i + 1 < len(src) else ""
            out.append({"n": "\n", "t": "\t"}.get(nxt, nxt))
            i += 2
            continue
        if char == "`":
            return "".join(out), i + 1
        if char == "$" and i + 1 < len(src) and src[i + 1] == "{":
            i = _scan_code(src, i + 2, found, until="}")
            out.append("{%d}" % holes)
            holes += 1
            continue
        out.append(char)
        i += 1
    return "".join(out), i


def _scan_code(src: str, i: int, found: list[str], until: str = "") -> int:
    depth = 0
    last = "("
    while i < len(src):
        char = src[i]
        if until and char == "}" and depth == 0:
            return i + 1
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        if char == "/" and i + 1 < len(src) and src[i + 1] == "/":
            i = src.find("\n", i)
            i = len(src) if i < 0 else i
            continue
        if char == "/" and i + 1 < len(src) and src[i + 1] == "*":
            end = src.find("*/", i + 2)
            i = len(src) if end < 0 else end + 2
            continue
        if char in "\"'":
            text, i = _read_string(src, i, char)
            found.append(text)
            last = "a"
            continue
        if char == "`":
            text, i = _read_template(src, i, found)
            found.append(text)
            last = "a"
            continue
        if char == "/" and last in REGEX_BEFORE:
            # a regular expression literal
            j = i + 1
            in_class = False
            while j < len(src) and src[j] != "\n":
                if src[j] == "\\":
                    j += 2
                    continue
                if src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                elif src[j] == "/" and not in_class:
                    break
                j += 1
            i = j + 1
            last = "a"
            continue
        if not char.isspace():
            last = char
        i += 1
    return i


def from_js(path: Path) -> list[str]:
    found: list[str] = []
    _scan_code(path.read_text(encoding="utf-8"), 0, found)
    return found


# ---------------------------------------------------------------------------
# Python
# ---------------------------------------------------------------------------


def _text_of(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts, holes = [], 0
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
            else:
                parts.append("{%d}" % holes)
                holes += 1
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = _text_of(node.left), _text_of(node.right)
        if left is not None and right is not None:
            return left + right
    if isinstance(node, ast.IfExp):
        return None
    return None


def from_python(path: Path, *, broad: bool = False) -> list[str]:
    """``broad``: every string that reads as text (the apps' server code, which is mostly what people see:
    constants, data tables of labels); otherwise only messages and values in known places."""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    found: list[str] = []
    if broad:
        docstrings = set()
        for node in ast.walk(tree):
            if (
                isinstance(
                    node,
                    (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef),
                )
                and node.body
            ):
                first = node.body[0]
                if isinstance(first, ast.Expr) and isinstance(
                    first.value, ast.Constant
                ):
                    docstrings.add(id(first.value))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                text = node.value.strip()
                starts = text[:1].isupper() or (text[:1].isdigit() and " " in text)
                if starts and looks_like_text(text):
                    found.append(text)
            elif isinstance(node, ast.JoinedStr):
                text = _text_of(node) or ""
                if text[:1].isupper() and looks_like_text(text):
                    found.append(text)

    def add(node: ast.AST | None) -> None:
        if node is None:
            return
        if isinstance(node, ast.IfExp):
            add(node.body)
            add(node.orelse)
            return
        text = _text_of(node)
        if text is not None:
            found.append(text)

    for node in ast.walk(tree):
        if isinstance(node, ast.Raise) and isinstance(node.exc, ast.Call):
            func = node.exc.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            if EXCEPTION_HINT.search(name or "") and node.exc.args:
                add(node.exc.args[0])
        elif isinstance(node, ast.Dict):
            for key, value in zip(node.keys, node.values, strict=False):
                if isinstance(key, ast.Constant) and key.value in SERVER_KEYS:
                    add(value)
        elif isinstance(node, ast.Call):
            func = node.func
            name = (
                func.attr
                if isinstance(func, ast.Attribute)
                else getattr(func, "id", "")
            )
            for index in CALL_ARGS.get(name or "", ()):
                if index < len(node.args):
                    add(node.args[index])
            for keyword in node.keywords:
                if keyword.arg in SERVER_KEYS:
                    add(keyword.value)
    return found


def from_html(path: Path) -> list[str]:
    from html.parser import HTMLParser

    found: list[str] = []

    class Collector(HTMLParser):
        skip = 0

        def handle_starttag(self, tag, attrs):  # noqa: ANN001
            if tag in ("script", "style"):
                self.skip += 1
            for name, value in attrs:
                if name in ("aria-label", "title", "placeholder", "alt") and value:
                    found.append(value)

        def handle_endtag(self, tag):  # noqa: ANN001
            if tag in ("script", "style") and self.skip:
                self.skip -= 1

        def handle_data(self, data):  # noqa: ANN001
            if not self.skip and data.strip():
                found.append(" ".join(data.split()))

    Collector().feed(path.read_text(encoding="utf-8"))
    return found


def stage_labels() -> list[str]:
    """Job stages are shown from their keys ("ready_to_apply" -> "Ready To Apply", "ready to apply")."""
    source = (STATIC / "views-jobs.js").read_text(encoding="utf-8")
    found = re.search(r"const STAGES = \[([^\]]*)\]", source)
    keys = re.findall(r'"([a-z_]+)"', found.group(1)) if found else []
    out = []
    for key in keys:
        words = key.replace("_", " ")
        out += [words, words.title(), words[:1].upper() + words[1:]]
    return out


# ---------------------------------------------------------------------------


# Text written into a person's own website (Sites' offline designer), never shown on Monosage's screens.
NOT_SCREEN_TEXT = {"aira/suite/sites/designer.py"}


def collect() -> list[str]:
    strings: set[str] = set()
    for path in sorted(STATIC.glob("*.js")):
        for text in from_js(path):
            if looks_like_text(text):
                strings.add(text.strip())
    python_files = [
        ROOT / "auto_apply_assistant.py",
        *sorted((ROOT / "aira").rglob("*.py")),
        *sorted((ROOT / "aira_cloud").rglob("*.py")),
    ]
    for path in python_files:
        if (
            "tests" in path.parts
            or path.relative_to(ROOT).as_posix() in NOT_SCREEN_TEXT
        ):
            continue
        broad = "suite" in path.parts
        for text in from_python(path, broad=broad):
            if looks_like_text(text) or (
                text.strip() and text.strip()[0].isupper() and " " in text.strip()
            ):
                if looks_like_text(text):
                    strings.add(text.strip())
    sys.path.insert(0, str(ROOT))
    from aira import apps

    for app in apps.APPS:
        for text in (
            app.name,
            app.tagline,
            app.description,
            app.industry,
            *app.highlights,
        ):
            if text:
                strings.add(text.strip())
    for path in (STATIC / "index.html", STATIC / "public.html"):
        for text in from_html(path):
            if looks_like_text(text):
                strings.add(text.strip())
    for text in stage_labels():
        strings.add(text)
    for word in STATUS_WORDS:
        strings.add(word)
        strings.add(word[:1].upper() + word[1:])
    return sorted(strings)


def main() -> int:
    strings = collect()
    data = {
        "note": "English source strings (scripts/i18n_extract.py). {0}, {1} are numbers or names.",
        "strings": strings,
    }
    text = json.dumps(data, ensure_ascii=False, indent=0) + "\n"
    if "--check" in sys.argv:
        current = CATALOG.read_text(encoding="utf-8") if CATALOG.exists() else ""
        if current != text:
            print(
                "The i18n catalog is stale: run uv run python scripts/i18n_extract.py"
            )
            return 1
        return 0
    CATALOG.parent.mkdir(parents=True, exist_ok=True)
    CATALOG.write_text(text, encoding="utf-8")
    print(f"{len(strings)} strings -> {CATALOG.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
