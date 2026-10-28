"""Who may open Commerce (Shop and Bookings) in the single-user build.

A household shares one Aira and the browser says which person is using it (``X-Aira-Profile``). Commerce holds
the business's customers (names, emails, phone numbers), its money (payments, refunds) and its staff's pay, so a
child's or a teen's profile is refused (403 ``adult_only``), as the hosted build refuses a member without a
business role. A request without a profile header is the account itself (the owner), as before. In the hosted
build the organization's roles decide (the gateway checks them), so nothing is checked here.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from aira import suite
from aira.suite import kit

REFUSED = (
    "Shop and Bookings hold the business's customers and money. "
    "Ask a grown-up in your household to open them."
)


class AdultOnly(PermissionError):
    """A child's or a teen's household profile opened Commerce (403 ``adult_only``)."""

    code = "adult_only"


def refuse_young(request: Any, connection: sqlite3.Connection) -> None:
    """Raise :class:`AdultOnly` when the household profile using Aira is a child or a teen."""
    member = suite.actor()
    if member and member.get("member_id"):
        return
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
    if row and row["age_group"] not in ("adult", "senior"):
        raise AdultOnly(REFUSED)


def connect(request: Any) -> sqlite3.Connection:
    """``request.connect()`` for the business's own Commerce routes, refusing a child's or a teen's profile."""
    connection = request.connect()
    try:
        refuse_young(request, connection)
    except BaseException:
        connection.close()
        raise
    return connection
