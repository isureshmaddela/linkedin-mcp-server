"""Public links: pages a business shares with its customers, used without signing in.

A link is ``/p/<token>``. The token (32 random bytes) is the only key: it names the account's database
through an index the hosting server keeps (the account's user id locally, the organization hosted), and
inside that database it names one thing of one app (a booking page, an invoice, a product catalogue).

An app registers what a link shows and what a visitor may do there::

    @public.page("bookings", "booking_page")
    def booking_page(connection, link):            # -> the page data (JSON)
        ...

    @public.action("bookings", "booking_page", "book")
    def book(connection, link, payload, visitor):  # -> (status, data)
        ...

``visitor`` is ``{"client": <rate-limit key>, "user_agent": ...}``. Everything a page returns is public:
put only what the business means to show. A revoked link answers 404, and so does a link whose app the
organization no longer has (the hosted gateway checks the subscription before the handler runs).
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol

from aira.suite import kit

TOKEN_BYTES = 32
TOKEN_PATTERN = r"[A-Za-z0-9_-]{40,64}"

PageFn = Callable[[sqlite3.Connection, dict[str, Any]], dict[str, Any]]
ActionFn = Callable[
    [sqlite3.Connection, dict[str, Any], dict[str, Any], dict[str, Any]],
    tuple[int, dict[str, Any]],
]
QueryFn = Callable[
    [sqlite3.Connection, dict[str, Any], dict[str, Any]], tuple[int, dict[str, Any]]
]


@dataclass
class PublicKind:
    app: str
    kind: str
    render: PageFn | None = None
    actions: dict[str, ActionFn] = field(default_factory=dict)
    queries: dict[str, QueryFn] = field(default_factory=dict)


KINDS: dict[tuple[str, str], PublicKind] = {}


def _kind(app: str, kind: str) -> PublicKind:
    return KINDS.setdefault((app, kind), PublicKind(app, kind))


def page(app: str, kind: str) -> Callable[[PageFn], PageFn]:
    def wrap(fn: PageFn) -> PageFn:
        _kind(app, kind).render = fn
        return fn

    return wrap


def action(app: str, kind: str, name: str) -> Callable[[ActionFn], ActionFn]:
    def wrap(fn: ActionFn) -> ActionFn:
        _kind(app, kind).actions[name] = fn
        return fn

    return wrap


def query(app: str, kind: str, name: str) -> Callable[[QueryFn], QueryFn]:
    """A read-only question a page asks often (a chat's new messages): ``GET /api/public/<token>/<name>``.

    It counts against the page-view limit, not the action limit, and must not change anything.
    """

    def wrap(fn: QueryFn) -> QueryFn:
        _kind(app, kind).queries[name] = fn
        return fn

    return wrap


# ---------------------------------------------------------------------------
# the index (token hash -> account), kept by the hosting server
# ---------------------------------------------------------------------------


class Index(Protocol):
    def add(self, token_hash: str, app: str) -> None:
        """Index a new link for the account bound to this request."""
        ...

    def remove(self, token_hash: str) -> None: ...


class _MemoryIndex:
    """For unit tests of an app's own code: links work inside one database without a server."""

    def __init__(self) -> None:
        self.entries: dict[str, str] = {}

    def add(self, token_hash: str, app: str) -> None:
        self.entries[token_hash] = app

    def remove(self, token_hash: str) -> None:
        self.entries.pop(token_hash, None)


_default_index: Index = _MemoryIndex()
_index: Index = _default_index


def set_default_index(index: Index) -> None:
    """The process's own index (the single-user build's accounts database); also made current."""
    global _default_index, _index
    _default_index = _index = index


def set_index(index: Index) -> None:
    """Use ``index`` from now on (the hosted build installs its organization index over the default)."""
    global _index
    _index = index


def reset_index() -> None:
    """Back to the default index (tests that run both builds in one process call this between tests)."""
    global _index
    _index = _default_index


def hash_token(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def path_for(token: str) -> str:
    return f"/p/{token}"


def _public(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "token": row["token"],
        "path": path_for(row["token"]),
        "app": row["app"],
        "kind": row["kind"],
        "ref_id": row["ref_id"],
        "title": row["title"],
        "views": int(row["views"] or 0),
        "actions": int(row["actions"] or 0),
        "created_at": row["created_at"],
        "last_seen_at": row["last_seen_at"],
    }


def link_for(
    connection: sqlite3.Connection, app: str, kind: str, ref_id: str
) -> dict[str, Any] | None:
    """The live link for this thing, if one was made."""
    kit.ensure_core(connection)
    row = kit.fetch_one(
        connection,
        "SELECT * FROM suite_public_links WHERE app = ? AND kind = ? AND ref_id = ? AND revoked_at IS NULL ORDER BY created_at DESC",
        (app, kind, ref_id),
    )
    return _public(row) if row else None


def create_link(
    connection: sqlite3.Connection, app: str, kind: str, ref_id: str, *, title: str = ""
) -> dict[str, Any]:
    """The live link for this thing, made on first use (the same link is returned while it is live)."""
    if (app, kind) not in KINDS:
        raise ValueError(f"{app} has no public page of kind {kind}.")
    existing = link_for(connection, app, kind, ref_id)
    if existing:
        if title and title != existing["title"]:
            connection.execute(
                "UPDATE suite_public_links SET title = ? WHERE token = ?",
                (title[:200], existing["token"]),
            )
            connection.commit()
            existing["title"] = title[:200]
        return existing
    token = secrets.token_urlsafe(TOKEN_BYTES)
    token_hash = hash_token(token)
    connection.execute(
        """INSERT INTO suite_public_links (token, token_hash, app, kind, ref_id, title, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (token, token_hash, app, kind, ref_id, title[:200], kit.now()),
    )
    connection.commit()
    _index.add(token_hash, app)
    return link_for(connection, app, kind, ref_id) or {}


def revoke(connection: sqlite3.Connection, app: str, kind: str, ref_id: str) -> int:
    """Turn off every link to this thing (a new one can be made later)."""
    kit.ensure_core(connection)
    rows = kit.fetch_all(
        connection,
        "SELECT token_hash FROM suite_public_links WHERE app = ? AND kind = ? AND ref_id = ? AND revoked_at IS NULL",
        (app, kind, ref_id),
    )
    connection.execute(
        "UPDATE suite_public_links SET revoked_at = ? WHERE app = ? AND kind = ? AND ref_id = ? AND revoked_at IS NULL",
        (kit.now(), app, kind, ref_id),
    )
    connection.commit()
    for row in rows:
        _index.remove(row["token_hash"])
    return len(rows)


def resolve(connection: sqlite3.Connection, token: str) -> dict[str, Any]:
    """The link row for a token presented on a public page, or LookupError."""
    kit.ensure_core(connection)
    row = kit.fetch_one(
        connection,
        "SELECT * FROM suite_public_links WHERE token_hash = ? AND revoked_at IS NULL",
        (hash_token(token),),
    )
    if not row or (row["app"], row["kind"]) not in KINDS:
        raise LookupError("This link is no longer available.")
    return row


def view(connection: sqlite3.Connection, token: str) -> dict[str, Any]:
    """What ``GET /api/public/<token>`` answers: the app's page plus the business's public profile."""
    link = resolve(connection, token)
    spec = KINDS[(link["app"], link["kind"])]
    if spec.render is None:
        raise LookupError("This link is no longer available.")
    data = spec.render(connection, link)
    connection.execute(
        "UPDATE suite_public_links SET views = views + 1, last_seen_at = ? WHERE token = ?",
        (kit.now(), link["token"]),
    )
    connection.commit()
    return {
        "app": link["app"],
        "kind": link["kind"],
        "title": link["title"],
        "business": kit.public_business(connection),
        "actions": sorted(spec.actions),
        "queries": sorted(spec.queries),
        "page": data,
    }


def ask(
    connection: sqlite3.Connection, token: str, name: str, params: Mapping[str, Any]
) -> tuple[int, dict[str, Any]]:
    """What ``GET /api/public/<token>/<query>`` answers (read only: no counters, no commit)."""
    link = resolve(connection, token)
    handler = KINDS[(link["app"], link["kind"])].queries.get(name)
    if handler is None:
        raise LookupError("That cannot be asked on this page.")
    return handler(connection, link, dict(params))


def act(
    connection: sqlite3.Connection,
    token: str,
    name: str,
    payload: Mapping[str, Any],
    visitor: Mapping[str, Any],
) -> tuple[int, dict[str, Any]]:
    """What ``POST /api/public/<token>/<action>`` answers."""
    link = resolve(connection, token)
    spec = KINDS[(link["app"], link["kind"])]
    handler = spec.actions.get(name)
    if handler is None:
        raise LookupError("That cannot be done on this page.")
    status, data = handler(connection, link, dict(payload), dict(visitor))
    if status < 400:
        connection.execute(
            "UPDATE suite_public_links SET actions = actions + 1, last_seen_at = ? WHERE token = ?",
            (kit.now(), link["token"]),
        )
        connection.commit()
    return status, data
