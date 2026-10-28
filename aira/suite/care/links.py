"""Health's public links (the emergency card, the doctor's report, a client's statement), through aira.suite.public.

They are made, read and turned off here so they also open in the hosted build whatever order the server started
in. ``public.set_default_index`` (called when ``auto_apply_assistant`` is first imported, which the hosted gateway
does after the hosted shell put its organization index in place) also makes the single-user index the current
one, so a link made in an organization would be indexed for the single-user server and answer 404 on the hosted
one. Until the shared code keeps an index already installed (see the care report's shared request), each link
made or shown inside an organization is also put in that organization's index; the index ignores a link it
already has.
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from aira.suite import kit, public
from aira.suite.care.store import APP

logger = logging.getLogger(__name__)

# token hashes this process already put in the hosted index
_indexed: set[str] = set()


def _hosted_index() -> Any:
    """The organization index of the hosted build, when this request runs inside an organization; else None."""
    try:
        from aira_cloud import context
    except ImportError:  # the single-user build without the hosted shell
        return None
    bound = context.current()
    if bound is None or not bound.org_id:
        return None
    from aira.hosting import HOOKS

    make = HOOKS.key_index  # the hosted shell's hook holds its CloudApp
    if make is None:
        return None
    try:
        cloud_app = getattr(make(f"org-{bound.org_id}"), "app", None)
    except Exception:  # noqa: BLE001 - without it the shared index is all there is
        logger.debug("No hosted public index", exc_info=True)
        return None
    index = getattr(cloud_app, "public_index", None)
    return index if index is not None and hasattr(index, "add") else None


def _index_hosted(link: dict[str, Any] | None) -> None:
    if not link:
        return
    token_hash = public.hash_token(link["token"])
    if token_hash in _indexed:
        return
    index = _hosted_index()
    if index is None:
        return
    index.add(token_hash, APP)
    _indexed.add(token_hash)


def current(
    connection: sqlite3.Connection, kind: str, ref_id: str
) -> dict[str, Any] | None:
    """The live link to this thing, if one was made."""
    link = public.link_for(connection, APP, kind, ref_id)
    _index_hosted(link)
    return link


def create(
    connection: sqlite3.Connection, kind: str, ref_id: str, *, title: str = ""
) -> dict[str, Any]:
    """The live link to this thing, made on first use."""
    link = public.create_link(connection, APP, kind, ref_id, title=title)
    _index_hosted(link)
    return link


def revoke(connection: sqlite3.Connection, kind: str, ref_id: str) -> int:
    """Turn off every link to this thing, in the hosted index too."""
    kit.ensure_core(connection)
    hashes = [
        row["token_hash"]
        for row in kit.fetch_all(
            connection,
            "SELECT token_hash FROM suite_public_links WHERE app = ? AND kind = ? AND ref_id = ? AND revoked_at IS NULL",
            (APP, kind, ref_id),
        )
    ]
    removed = public.revoke(connection, APP, kind, ref_id)
    index = _hosted_index() if hashes else None
    for token_hash in hashes:
        _indexed.discard(token_hash)
        if index is not None:
            index.remove(token_hash)
    return removed
