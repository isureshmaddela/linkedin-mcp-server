"""Who may use the business side of Marketing and Leads in the single-user build.

The account is a household: the browser says which person is using Aira (``X-Aira-Profile``). Marketing and Leads
hold the business's customers (names, emails, messages), its money (paid leads count as revenue) and a paid search
key, so a child or teen profile is refused, as the hosted build refuses a member without a business role. With no
profile header the request is the account itself (the owner), as before. The hosted build's roles are checked by
the gateway, so a request with a signed-in member is left to it.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from aira import suite
from aira.suite import kit, life


def require_grown_up(request: Any, connection: sqlite3.Connection) -> None:
    """Raise ``PermissionError`` (403) when the household profile using Aira is a child or a teen."""
    member = suite.actor()
    if member and member.get("member_id"):
        return
    header = (
        str(request.header("X-Aira-Profile") or "").strip()
        if hasattr(request, "header")
        else ""
    )
    if not header:
        return
    profile_id = header.partition(".")[0]
    life.ensure(connection)
    row = kit.fetch_one(
        connection,
        "SELECT age_group FROM life_profiles WHERE id = ? AND archived_at IS NULL",
        (profile_id,),
    )
    if row and not life.is_adult(dict(row)):
        raise PermissionError(
            "Marketing and Leads hold the business's customers and money. Ask a grown-up in your household to open it."
        )
