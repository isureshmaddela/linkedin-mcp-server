"""Work the apps need done when nobody is looking: each app's periodic checks, and sending messages that are due.

The hosting server calls :func:`run` for every account that uses the apps, every few minutes: the single-user
build from its scheduler thread (each person's database), the hosted build as a queued ``suite.tick`` job (each
organization's database). An app registers a task::

    @background.task("care")
    def _tick(connection):            # e.g. find missed doses and tell caregivers
        ...

Then approved messages whose time has come (a booking reminder the day before, a caregiver's notice) go out
through the business's own mail server, if it set one up (Business hub > Email). Nothing is sent that a person
did not approve, except the kinds the business chose to send automatically and safety notices
(``kit.ALWAYS_SEND``).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable
from typing import Any

from aira import suite
from aira.suite import kit

TaskFn = Callable[[sqlite3.Connection], Any]
TASKS: dict[str, TaskFn] = {}
INTERVAL_SECONDS = 300


def task(app: str) -> Callable[[TaskFn], TaskFn]:
    def wrap(fn: TaskFn) -> TaskFn:
        TASKS[app] = fn
        return fn

    return wrap


def uses_suite(connection: sqlite3.Connection) -> bool:
    """Has this account ever used an industry or life app (so there is something to do)?"""
    row = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'suite_settings'"
    ).fetchone()
    return row is not None


def run(connection: sqlite3.Connection, secret_store: Any) -> dict[str, Any]:
    """One pass for one account. Never raises: a failing app is logged and the others still run."""
    if not uses_suite(connection):
        return {"ran": "nothing"}
    suite.load_apps()
    result: dict[str, Any] = {"tasks": {}}
    for app_id, fn in TASKS.items():
        if not suite.app_enabled(app_id):
            continue
        try:
            outcome = fn(connection)
            connection.commit()
            result["tasks"][app_id] = (
                outcome if isinstance(outcome, (int, str, dict, list)) else "ok"
            )
        except Exception:  # noqa: BLE001 - one app must not stop the others or the mail
            suite.logger.exception("Background task for %s failed", app_id)
            result["tasks"][app_id] = "failed"
    settings = kit.email_settings(connection)
    if settings.get("smtp_host") and settings.get("from_email"):
        due = connection.execute(
            "SELECT 1 FROM suite_outbox WHERE status = 'approved' AND (send_after IS NULL OR send_after <= ?) LIMIT 1",
            (kit.now(),),
        ).fetchone()
        if due:
            try:
                password = secret_store.get(connection, kit.SMTP_PASSWORD_SECRET) or ""
                sent = kit.send_outbox(connection, password)
                result["mail"] = {"sent": sent["sent"], "failed": sent["failed"]}
            except Exception:  # noqa: BLE001
                suite.logger.exception("Sending due messages failed")
                result["mail"] = "failed"
    return result
