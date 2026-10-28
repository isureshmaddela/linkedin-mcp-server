"""Who may open Courses & Events in a household: the business's money and its customers are for adults.

The single-user build lets a household share one Aira, each person with a life profile the browser names in
``X-Aira-Profile``. The everyday apps adapt to a child's profile; a business app has customer names, payments and
partner payouts, so a request made from a child's profile is refused (403). An adult's or teen's profile, a
request without a profile, and the hosted build (members are adults with roles) are unchanged.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from aira.suite import kit

REFUSED = "Courses & Events is for the adults who run the business. Ask one of them to open it."


def refuse_child(request: Any, connection: sqlite3.Connection) -> None:
    header = request.header("X-Aira-Profile") if hasattr(request, "header") else ""
    profile_id = str(header or "").strip().partition(".")[0]
    if not profile_id:
        return
    try:
        row = kit.fetch_one(
            connection,
            "SELECT age_group FROM life_profiles WHERE id = ? AND archived_at IS NULL",
            (profile_id,),
        )
    except sqlite3.Error:  # no household profiles in this database
        return
    if row and row["age_group"] == "child":
        raise PermissionError(REFUSED)


def connect(request: Any) -> sqlite3.Connection:
    """``request.connect()`` for the staff routes of Courses & Events, refusing a child's profile."""
    connection = request.connect()
    try:
        refuse_child(request, connection)
    except BaseException:
        connection.close()
        raise
    return connection
