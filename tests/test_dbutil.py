"""Lazy column migrations tolerate a second connection adding the same column first."""

from __future__ import annotations

import sqlite3

import pytest

from aira import dbutil
from aira.suite import kit


def test_add_column_adds_once_and_tolerates_a_racing_connection(tmp_path):
    path = tmp_path / "race.sqlite3"
    first = sqlite3.connect(path)
    second = sqlite3.connect(path)
    first.execute("CREATE TABLE t (id TEXT)")
    first.commit()
    # Both connections looked before either added it: the second ALTER is the one that used to fail.
    assert dbutil.add_column(first, "t", "kind", "TEXT NOT NULL DEFAULT ''") is True
    first.commit()
    second.execute(
        "ALTER TABLE t ADD COLUMN other TEXT"
    )  # unrelated work on the second connection
    second.commit()
    assert dbutil.add_column(second, "t", "kind", "TEXT NOT NULL DEFAULT ''") is False
    dbutil.execute_migration(
        second, "ALTER TABLE t ADD COLUMN kind TEXT"
    )  # a plain statement: also done
    with pytest.raises(sqlite3.OperationalError):
        dbutil.execute_migration(second, "ALTER TABLE missing ADD COLUMN kind TEXT")
    first.close()
    second.close()


def test_the_ledger_survives_a_column_another_connection_added(tmp_path):
    kit.forget_schema()
    path = tmp_path / "suite.sqlite3"
    one = sqlite3.connect(path)
    kit.ensure_core(one)
    # another request adds one of the ledger's later columns first
    other = sqlite3.connect(path)
    dbutil.add_column(other, "suite_revenue", "kind", "TEXT NOT NULL DEFAULT 'payment'")
    other.commit()
    kit.forget_schema()
    assert isinstance(kit.revenue(one), dict)  # no "duplicate column name: kind"
    one.close()
    other.close()
