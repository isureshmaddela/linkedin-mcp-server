"""Small SQLite helpers shared by the apps' lazy migrations.

Apps add a column the first time a connection needs it. Two requests can both see the column missing and both
add it; the second then fails with "duplicate column name". These helpers treat that as done.
"""

from __future__ import annotations

import sqlite3


def is_duplicate_column(error: BaseException) -> bool:
    return (
        isinstance(error, sqlite3.OperationalError)
        and "duplicate column" in str(error).lower()
    )


def add_column(
    connection: sqlite3.Connection, table: str, column: str, definition: str
) -> bool:
    """``ALTER TABLE table ADD COLUMN column definition`` unless it is there; True when this call added it.

    ``table``, ``column`` and ``definition`` are the app's own fixed names, never user input.
    """
    have = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}  # noqa: S608 - fixed names
    if column in have:
        return False
    try:
        connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")  # noqa: S608 - fixed names
    except sqlite3.OperationalError as error:
        if is_duplicate_column(error):
            return False  # another connection added it first
        raise
    return True


def execute_migration(connection: sqlite3.Connection, statement: str) -> None:
    """Run one lazy-migration statement; an ``ADD COLUMN`` another connection already ran counts as done."""
    try:
        connection.execute(statement)
    except sqlite3.OperationalError as error:
        if " add column " in f" {statement.lower()} " and is_duplicate_column(error):
            return
        raise
