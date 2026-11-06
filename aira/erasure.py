"""Erase a person across every app: one job for a removed household profile, a deleted account, a hosted member who
leaves or deletes themselves, and a business erasing one of its customers.

Each app registers an eraser::

    from aira import erasure

    def erase_care(connection, person) -> dict[str, int]:   # {table: rows deleted or anonymised}
        ...

    erasure.register("care", erase_care)

``connection`` is the account's database (the hosted build: the space the person's apps run in). An app whose
data lives elsewhere opens its own (Social and Videos keep one database for the whole platform). An eraser deletes
what is the person's and anonymises what a business must keep for its books: an order keeps its number and amounts
but loses the name, email, phone and address (:data:`ERASED_NAME`). A count under a ``"held:<table>"`` key is kept
on purpose under a legal hold (evidence of an open moderation report) and is purged when the hold ends. An eraser
must be safe to run twice: a retry runs it again.

:func:`run_erasure` runs every eraser, each in its own savepoint: one that fails is rolled back and recorded, and
the others carry on. The job is kept in ``erasure_jobs`` with what each app erased and which apps failed; *who* it
is for is kept there only until every app has succeeded, so :func:`retry` can finish it. The receipt written to the
audit log has counts per app and nothing about the person (no ids, names, emails or phones).

The apps' own erasers are in :mod:`aira.erasers` (loaded on first use).
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from aira.suite import kit

logger = logging.getLogger("aira.erasure")

ERASED_NAME = (
    "Erased customer"  # what a kept accounting row shows in place of the person's name
)
REASONS = (
    "profile_erased",  # a household profile: "Erase everything" (aira.suite.life.erase)
    "account_deleted",  # the single-user build's account deleted by its owner
    "member_removed",  # hosted: an administrator removed the member
    "member_deleted",  # hosted: the member deleted their own account, or a data subject request
    "customer_request",  # a business erased one customer (the Business hub)
)
STATUSES = ("running", "done", "partial")
STALLED_MINUTES = 15  # a job still "running" this long after its last step was cut short: the next pass resumes it

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS erasure_jobs (
        id TEXT PRIMARY KEY,
        reason TEXT NOT NULL,
        actor TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'running',
        subject TEXT NOT NULL DEFAULT '',
        apps TEXT NOT NULL DEFAULT '[]',
        counts TEXT NOT NULL DEFAULT '{}',
        failed TEXT NOT NULL DEFAULT '[]',
        attempts INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        finished_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS erasure_jobs_status ON erasure_jobs (status, created_at)",
)


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure(connection, "erasure.v1", SCHEMA)


# ---------------------------------------------------------------------------
# who is erased
# ---------------------------------------------------------------------------


def phone_key(value: Any) -> str:
    """A phone number compared by its last ten digits ("+91 98765 43210" is "098765 43210"); '' if too short."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits[-10:] if len(digits) >= 7 else ""


@dataclass(frozen=True)
class Person:
    """Who to erase. ``profile_id``: their life profile in this database; ``member_id``: the hosted member; ``emails``
    and ``phones``: how businesses and other people's records name them; ``names``: only to take their name out of
    the business feed's labels (an order "from Zelda Quokka"). ``owner``: the household's owner (Careers'
    notifications are theirs). ``whole_account``: the database is this person's own (their Careers workspace, the
    single-user account they delete): its Careers data, leads and recruiter contacts go too."""

    profile_id: str = ""
    member_id: str = ""
    emails: tuple[str, ...] = ()
    phones: tuple[str, ...] = ()
    names: tuple[str, ...] = ()
    owner: bool = False
    whole_account: bool = False

    def __post_init__(self) -> None:
        clean = lambda values: tuple(dict.fromkeys(v for v in values if v))  # noqa: E731
        object.__setattr__(self, "profile_id", str(self.profile_id or "").strip())
        object.__setattr__(self, "member_id", str(self.member_id or "").strip())
        object.__setattr__(
            self, "emails", clean(str(e or "").strip().lower() for e in self.emails)
        )
        object.__setattr__(
            self, "phones", clean(str(p or "").strip() for p in self.phones)
        )
        object.__setattr__(
            self,
            "names",
            clean(
                str(n or "").strip()
                for n in self.names
                if len(str(n or "").strip()) >= 3
            ),
        )

    @property
    def keys(self) -> tuple[str, ...]:
        """Their person keys in Social, Videos and the notification centre (``p:<profile>``, ``m:<member>``)."""
        found = []
        if self.profile_id:
            found.append(f"p:{self.profile_id}")
        if self.member_id:
            found.append(f"m:{self.member_id}")
        return tuple(found)

    @property
    def phone_keys(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(k for k in (phone_key(p) for p in self.phones) if k))

    @property
    def empty(self) -> bool:
        return not (self.profile_id or self.member_id or self.emails or self.phone_keys)

    def to_dict(self) -> dict[str, Any]:
        return {
            "profile_id": self.profile_id,
            "member_id": self.member_id,
            "emails": list(self.emails),
            "phones": list(self.phones),
            "names": list(self.names),
            "owner": self.owner,
            "whole_account": self.whole_account,
        }

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Person:
        return cls(
            profile_id=str(data.get("profile_id") or ""),
            member_id=str(data.get("member_id") or ""),
            emails=tuple(data.get("emails") or ()),
            phones=tuple(data.get("phones") or ()),
            names=tuple(data.get("names") or ()),
            owner=bool(data.get("owner")),
            whole_account=bool(data.get("whole_account")),
        )


# ---------------------------------------------------------------------------
# the registry
# ---------------------------------------------------------------------------

Eraser = Callable[[sqlite3.Connection, Person], Mapping[str, int] | None]


@dataclass(frozen=True)
class _Entry:
    app: str
    fn: Eraser
    order: int
    customer: bool


_REGISTRY: dict[str, _Entry] = {}
_loaded = False


def register(
    app: str,
    fn: Eraser | None = None,
    *,
    order: int = 50,
    customer: bool = False,
) -> Any:
    """Register ``fn`` as ``app``'s eraser (also usable as ``@register("care")``). ``order``: lower runs first (the
    profile itself goes last). ``customer``: it also erases a business's customer by email or phone, so the
    Business hub runs it for a customer request."""

    def wrap(eraser: Eraser) -> Eraser:
        _REGISTRY[str(app)] = _Entry(str(app), eraser, int(order), bool(customer))
        return eraser

    return wrap(fn) if fn is not None else wrap


def unregister(app: str) -> None:
    _REGISTRY.pop(app, None)


def _load() -> None:
    global _loaded
    if not _loaded:
        _loaded = True
        import aira.erasers  # noqa: F401 - registers every app's eraser


def registered(*, customer: bool | None = None) -> list[str]:
    """The apps with an eraser, in the order they run (``customer=True``: those a customer request runs)."""
    _load()
    entries = sorted(_REGISTRY.values(), key=lambda e: (e.order, e.app))
    return [e.app for e in entries if customer is None or e.customer == customer]


# ---------------------------------------------------------------------------
# running a job
# ---------------------------------------------------------------------------


def _job(connection: sqlite3.Connection, job_id: str) -> dict[str, Any]:
    found = kit.fetch_one(
        connection, "SELECT * FROM erasure_jobs WHERE id = ?", (job_id,)
    )
    if not found:
        raise LookupError("That erasure was not found.")
    return found


def _counts(raw: Mapping[str, Any] | None) -> dict[str, int]:
    out: dict[str, int] = {}
    for table, value in dict(raw or {}).items():
        try:
            number = int(value)
        except (TypeError, ValueError):
            continue
        if number > 0:
            out[str(table)[:80]] = out.get(str(table)[:80], 0) + number
    return out


def run_erasure(
    connection: sqlite3.Connection,
    person: Person,
    *,
    reason: str,
    actor: str,
    apps: Iterable[str] | None = None,
    job_id: str = "",
) -> dict[str, Any]:
    """Run every eraser (or only ``apps``) for ``person`` and return the receipt. A failing eraser is rolled back,
    recorded, and the others still run; the job then stays ``partial`` and :func:`retry` runs what failed.

    ``actor`` names who asked, never the person erased when it is themselves ("self", "owner", "operator")."""
    if person.empty:
        raise ValueError(
            "Say who to erase: a profile, a member, an email or a phone number."
        )
    if reason not in REASONS:
        raise ValueError(f"The reason is one of {', '.join(REASONS)}.")
    _load()
    ensure(connection)
    wanted = None if apps is None else {str(a) for a in apps}
    entries = [
        _REGISTRY[app]
        for app in registered()
        if (wanted is None or app in wanted)
        and (reason != "customer_request" or _REGISTRY[app].customer)
    ]
    moment = kit.now()
    if job_id:
        job = _job(connection, job_id)
        counts: dict[str, dict[str, int]] = kit.loads(job["counts"], {}) or {}
        attempts = int(job["attempts"] or 0)
    else:
        job_id = kit.new_id()
        counts, attempts = {}, 0
        connection.execute(
            """INSERT INTO erasure_jobs (id, reason, actor, status, subject, apps, created_at, updated_at)
            VALUES (?, ?, ?, 'running', ?, ?, ?, ?)""",
            (
                job_id,
                reason,
                str(actor or "")[:120],
                json.dumps(person.to_dict()),
                json.dumps([e.app for e in entries]),
                moment,
                moment,
            ),
        )
        connection.commit()
    failed: list[str] = []
    for index, entry in enumerate(entries):
        point = f"erase_{index}"
        connection.execute(f"SAVEPOINT {point}")
        try:
            result = _counts(entry.fn(connection, person))
        except Exception as error:  # noqa: BLE001 - one app's failure never stops the others
            _undo(connection, point)
            failed.append(entry.app)
            # the class only: an error's words may carry the person's details (the traceback is for debugging)
            logger.warning(
                "Erasing in %s failed (%s); the job %s can be retried",
                entry.app,
                type(error).__name__,
                job_id,
            )
            logger.debug("Eraser %s failed", entry.app, exc_info=True)
            continue
        _release(connection, point)
        merged = counts.get(entry.app, {})
        for table, number in result.items():
            merged[table] = merged.get(table, 0) + number
        counts[entry.app] = merged
        # what is done is recorded at once: a job cut short (the server stopped) resumes after it
        connection.execute(
            "UPDATE erasure_jobs SET counts = ?, updated_at = ? WHERE id = ?",
            (json.dumps(counts), kit.now(), job_id),
        )
        connection.commit()
    attempts += 1
    done = not failed
    connection.execute(
        """UPDATE erasure_jobs SET status = ?, subject = ?, counts = ?, failed = ?, attempts = ?, updated_at = ?,
        finished_at = ? WHERE id = ?""",
        (
            "done" if done else "partial",
            ""
            if done
            else json.dumps(person.to_dict()),  # kept only while a retry still needs it
            json.dumps(counts),
            json.dumps(failed),
            attempts,
            kit.now(),
            kit.now() if done else None,
            job_id,
        ),
    )
    receipt = _receipt(job_id, reason, counts, failed, attempts)
    _write_receipt(connection, receipt, actor)
    connection.commit()
    return receipt


def _undo(connection: sqlite3.Connection, point: str) -> None:
    try:
        connection.execute(f"ROLLBACK TO {point}")
        connection.execute(f"RELEASE {point}")
    except sqlite3.Error:  # the eraser committed part of its work: a retry finishes it (erasers run twice safely)
        try:
            connection.rollback()
        except sqlite3.Error:
            logger.debug("Nothing to roll back after a failed eraser")


def _release(connection: sqlite3.Connection, point: str) -> None:
    try:
        connection.execute(f"RELEASE {point}")
    except sqlite3.Error:
        pass  # the eraser committed its own work, which ended the savepoint


def _receipt(
    job_id: str,
    reason: str,
    counts: Mapping[str, Mapping[str, int]],
    failed: Sequence[str],
    attempts: int,
) -> dict[str, Any]:
    """Counts per app (erased and kept under a legal hold): nothing that names the person."""
    erased: dict[str, int] = {}
    held: dict[str, int] = {}
    for app, tables in counts.items():
        for table, number in tables.items():
            if table.startswith("held:"):
                held[app] = held.get(app, 0) + int(number)
            else:
                erased[app] = erased.get(app, 0) + int(number)
    return {
        "job_id": job_id,
        "reason": reason,
        "status": "partial" if failed else "done",
        "erased": dict(sorted(erased.items())),
        "held": dict(sorted(held.items())),
        "failed": sorted(failed),
        "attempts": attempts,
    }


def _write_receipt(
    connection: sqlite3.Connection, receipt: Mapping[str, Any], actor: str
) -> None:
    if not table_exists(connection, "audit_logs"):
        return  # a database without the account's audit log (the job row is the record)
    from aira.audit import record_audit

    record_audit(
        connection,
        actor=str(actor or "system")[:120],
        action="person.erased"
        if receipt["status"] == "done"
        else "person.erasure_partial",
        target_type="erasure",
        target_id=str(receipt["job_id"]),
        detail=receipt,
        source="erasure",
    )


def retry(connection: sqlite3.Connection, job_id: str, *, actor: str) -> dict[str, Any]:
    """Run again the apps that failed in a partial job, or those a job cut short had not reached (a done job just
    returns its receipt)."""
    ensure(connection)
    job = _job(connection, job_id)
    counts = kit.loads(job["counts"], {}) or {}
    if job["status"] == "done" or not job["subject"]:
        return _receipt(job_id, job["reason"], counts, [], int(job["attempts"] or 0))
    person = Person.from_dict(kit.loads(job["subject"], {}) or {})
    if job["status"] == "partial":
        apps = kit.loads(job["failed"], []) or []
    else:  # still "running": the server stopped in the middle
        apps = [a for a in kit.loads(job["apps"], []) or [] if a not in counts]
    return run_erasure(
        connection,
        person,
        reason=job["reason"],
        actor=actor,
        apps=apps,
        job_id=job_id,
    )


def retry_partial(
    connection: sqlite3.Connection, *, actor: str = "system"
) -> list[dict[str, Any]]:
    """Retry every partial job of this database, and any cut short a while ago (the background pass)."""
    if not table_exists(connection, "erasure_jobs"):
        return []  # nothing was ever erased here
    from datetime import timedelta

    stalled = kit.iso(kit.utcnow() - timedelta(minutes=STALLED_MINUTES))
    out = []
    for job in kit.fetch_all(
        connection,
        "SELECT id FROM erasure_jobs WHERE status = 'partial' OR (status = 'running' AND updated_at < ?) ORDER BY created_at",
        (stalled,),
    ):
        out.append(retry(connection, job["id"], actor=actor))
    return out


def jobs(connection: sqlite3.Connection, *, limit: int = 50) -> list[dict[str, Any]]:
    """Erasures run in this database, newest first, as receipts (never who they were for)."""
    ensure(connection)
    out = []
    for job in kit.fetch_all(
        connection,
        "SELECT * FROM erasure_jobs ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (max(1, min(int(limit), 200)),),
    ):
        receipt = _receipt(
            job["id"],
            job["reason"],
            kit.loads(job["counts"], {}) or {},
            kit.loads(job["failed"], []) or [],
            int(job["attempts"] or 0),
        )
        receipt.update(
            {"created_at": job["created_at"], "finished_at": job["finished_at"]}
        )
        out.append(receipt)
    return out


def household(
    connection: sqlite3.Connection, *, whole_account: bool = False
) -> list[Person]:
    """Everyone of the household in this database (archived people too), as people to erase. ``whole_account``:
    the account itself goes, so its owner's Careers data, leads and recruiter contacts go with them."""
    if not table_exists(connection, "life_profiles"):
        return []
    return [
        Person(
            profile_id=row["id"],
            member_id=row["member_ref"] or "",
            emails=(row["email"] or "",),
            names=(row["name"] or "",),
            owner=bool(row["owner"]),
            whole_account=whole_account and bool(row["owner"]),
        )
        for row in kit.fetch_all(
            connection, "SELECT * FROM life_profiles ORDER BY owner, created_at"
        )
    ]


def erase_account_database(path: Any, *, actor: str = "self") -> list[dict[str, Any]]:
    """The single-user build deletes an account: erase every person of its household before the database file
    goes, so nothing of them stays in what lives outside it (Social and Videos keep one database for the computer).
    Never raises: the account is deleted whatever happens here (a failure is logged). Returns the receipts."""
    from pathlib import Path

    target = Path(str(path or ""))
    if not str(path or "") or not target.exists():
        return []
    receipts: list[dict[str, Any]] = []
    try:
        connection = sqlite3.connect(target, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            for person in household(connection, whole_account=True):
                receipts.append(
                    run_erasure(
                        connection, person, reason="account_deleted", actor=actor
                    )
                )
        finally:
            connection.close()
    except Exception:  # noqa: BLE001 - deleting the account presses on
        logger.exception("Erasing the people of a deleted account failed")
    # the account's own audit log goes with its file: the receipts (counts only) stay in the server's log
    for receipt in receipts:
        logger.info(
            "Erasure %s for a deleted account: %s",
            receipt["job_id"],
            json.dumps({k: receipt[k] for k in ("status", "erased", "held", "failed")}),
        )
    return receipts


def erase_customer(
    connection: sqlite3.Connection,
    *,
    emails: Iterable[str] = (),
    phones: Iterable[str] = (),
    actor: str,
) -> dict[str, Any]:
    """A business erases one customer by email or phone across its apps (Shop, Bookings, Marketing, Service,
    Events, Academy, Studio, Leads and the mail it keeps), keeping its accounting rows anonymised."""
    person = Person(emails=tuple(emails), phones=tuple(phones))
    if not (person.emails or person.phone_keys):
        raise ValueError("Type the customer's email address or phone number.")
    return run_erasure(connection, person, reason="customer_request", actor=actor)


def preview_customer(
    connection: sqlite3.Connection,
    *,
    emails: Iterable[str] = (),
    phones: Iterable[str] = (),
) -> dict[str, int]:
    """How many records in each table name this customer (what :func:`erase_customer` would change)."""
    person = Person(emails=tuple(emails), phones=tuple(phones))
    if not (person.emails or person.phone_keys):
        raise ValueError("Type the customer's email address or phone number.")
    found: dict[str, int] = {}
    for table, column, kind in contact_columns(connection):
        hits = len(matching(connection, table, person, **{kind: (column,)}))
        if hits:
            found[table] = found.get(table, 0) + hits
    return dict(sorted(found.items()))


# ---------------------------------------------------------------------------
# helpers for erasers: every one skips a table (or a column) this database does not have
# ---------------------------------------------------------------------------


def table_exists(connection: sqlite3.Connection, table: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        is not None
    )


def columns(connection: sqlite3.Connection, table: str) -> dict[str, Any]:
    """Column name -> its declared default (as SQL text, or None), for a table that exists ({} otherwise)."""
    if not _NAME.fullmatch(table) or not table_exists(connection, table):
        return {}
    return {
        row[1]: row[4] for row in connection.execute(f'PRAGMA table_info("{table}")')
    }


_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def _with_rowid(have: Mapping[str, Any]) -> dict[str, Any]:
    """A table's columns plus ``rowid`` (every ordinary table has one), or {} for a missing table."""
    return {**have, "rowid": None} if have else {}


def _default(definition: Any) -> Any:
    """The value a blanked column takes: its declared default ('' / '[]' / '{}' / 0), or ''."""
    if definition is None:
        return ""
    text = str(definition).strip()
    if text.upper() == "NULL":
        return None
    if len(text) >= 2 and text[0] == text[-1] == "'":
        return text[1:-1].replace("''", "'")
    try:
        return int(text)
    except ValueError:
        return ""


def placeholders(values: Sequence[Any]) -> str:
    return ", ".join("?" for _ in values)


def ids(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    values: Sequence[Any],
    *,
    key: str = "id",
) -> list[str]:
    """``key`` of the rows of ``table`` whose ``column`` is one of ``values``."""
    have = _with_rowid(columns(connection, table))
    values = [v for v in values if v not in (None, "")]
    if not values or column not in have or key not in have:
        return []
    return [
        str(row[0])
        for row in connection.execute(
            f'SELECT "{key}" FROM "{table}" WHERE "{column}" IN ({placeholders(values)})',  # noqa: S608 - checked names
            tuple(values),
        )
    ]


def delete(
    connection: sqlite3.Connection, table: str, column: str, values: Sequence[Any]
) -> int:
    """Delete the rows of ``table`` whose ``column`` is one of ``values``; the number deleted."""
    values = [v for v in values if v not in (None, "")]
    if not values or column not in _with_rowid(columns(connection, table)):
        return 0
    total = 0
    for start in range(0, len(values), 500):
        chunk = values[start : start + 500]
        total += max(
            connection.execute(
                f'DELETE FROM "{table}" WHERE "{column}" IN ({placeholders(chunk)})',  # noqa: S608 - checked names
                tuple(chunk),
            ).rowcount,
            0,
        )
    return total


def delete_all(connection: sqlite3.Connection, table: str) -> int:
    if not columns(connection, table):
        return 0
    return max(connection.execute(f'DELETE FROM "{table}"').rowcount, 0)  # noqa: S608 - checked name


def blank(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    values: Sequence[Any],
    changes: Mapping[str, Any],
) -> int:
    """Anonymise the rows of ``table`` whose ``column`` is one of ``values``: each column in ``changes`` takes the
    value given, or its declared default when the value is ``None`` ('' for text). Columns the table lacks are
    skipped. The number of rows changed."""
    have = columns(connection, table)
    values = [v for v in values if v not in (None, "")]
    if not values or column not in _with_rowid(have):
        return 0
    sets = {
        name: (_default(have[name]) if value is None else value)
        for name, value in changes.items()
        if name in have
    }
    if not sets:
        return 0
    assignments = ", ".join(f'"{name}" = ?' for name in sets)
    total = 0
    for start in range(0, len(values), 500):
        chunk = values[start : start + 500]
        total += max(
            connection.execute(
                f'UPDATE "{table}" SET {assignments} WHERE "{column}" IN ({placeholders(chunk)})',  # noqa: S608
                (*sets.values(), *chunk),
            ).rowcount,
            0,
        )
    return total


def matching(
    connection: sqlite3.Connection,
    table: str,
    person: Person,
    *,
    emails: Sequence[str] = (),
    phones: Sequence[str] = (),
    key: str = "rowid",
) -> list[Any]:
    """``key`` of the rows of ``table`` that name the person in one of the ``emails`` columns (any case) or the
    ``phones`` columns (by :func:`phone_key`)."""
    have = columns(connection, table)
    if not have:
        return []
    found: list[Any] = []
    email_cols = [c for c in emails if c in have]
    if person.emails and email_cols:
        where = " OR ".join(
            f'lower(trim("{c}")) IN ({placeholders(person.emails)})' for c in email_cols
        )
        found += [
            row[0]
            for row in connection.execute(
                f'SELECT {key} FROM "{table}" WHERE {where}',  # noqa: S608 - checked names
                tuple(person.emails) * len(email_cols),
            )
        ]
    phone_cols = [c for c in phones if c in have]
    wanted = set(person.phone_keys)
    if wanted and phone_cols:
        for column in phone_cols:
            for row in connection.execute(
                f'SELECT {key}, "{column}" FROM "{table}" WHERE "{column}" IS NOT NULL AND "{column}" != \'\''  # noqa: S608
            ):
                if phone_key(row[1]) in wanted:
                    found.append(row[0])
    return list(dict.fromkeys(found))


def column_values(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    where_column: str,
    values: Sequence[Any],
) -> list[str]:
    """The ``column`` values of the matching rows (to learn a customer's name before it is blanked)."""
    have = columns(connection, table)
    values = [v for v in values if v not in (None, "")]
    if not values or column not in have or where_column not in have:
        return []
    return [
        str(row[0])
        for row in connection.execute(
            f'SELECT "{column}" FROM "{table}" WHERE "{where_column}" IN ({placeholders(values)})',  # noqa: S608
            tuple(values),
        )
        if row[0]
    ]


def drop_from_lists(
    connection: sqlite3.Connection,
    table: str,
    column: str,
    value: str,
    *,
    key: str = "rowid",
) -> int:
    """Take ``value`` out of a column holding a JSON list (invitees, recipients); the rows changed."""
    have = columns(connection, table)
    if not value or column not in have:
        return 0
    changed = 0
    for row in connection.execute(
        f'SELECT {key}, "{column}" FROM "{table}" WHERE "{column}" LIKE ?',  # noqa: S608 - checked names
        (f"%{value}%",),
    ).fetchall():
        items = kit.loads(row[1], None)
        if not isinstance(items, list):
            continue
        kept = [
            item
            for item in items
            if not (isinstance(item, str) and item.strip().lower() == value.lower())
            and not (
                isinstance(item, dict)
                and value.lower() in {str(v).strip().lower() for v in item.values()}
            )
        ]
        if len(kept) != len(items):
            connection.execute(
                f'UPDATE "{table}" SET "{column}" = ? WHERE {key} = ?',  # noqa: S608 - checked names
                (json.dumps(kept), row[0]),
            )
            changed += 1
    return changed


def scrub_names(
    text: str, names: Iterable[str], replacement: str = "a customer"
) -> str:
    """``text`` with each of ``names`` (whole words, any case) replaced."""
    out = str(text or "")
    for name in sorted({n for n in names if len(n) >= 3}, key=len, reverse=True):
        out = re.sub(
            rf"(?<!\w){re.escape(name)}(?!\w)", replacement, out, flags=re.IGNORECASE
        )
    return out


def scrub_ledger(
    connection: sqlite3.Connection, refs: Sequence[str], names: Iterable[str]
) -> dict[str, int]:
    """The shared business records about ``refs`` (orders, bookings, invoices... that were anonymised): the revenue
    ledger keeps the amount without the customer, the activity feed keeps the line without their name, and mail
    still waiting about them loses their name."""
    names = [n for n in dict.fromkeys(names) if n and n != ERASED_NAME]
    refs = [r for r in refs if r]
    out: dict[str, int] = {}
    if not refs:
        return out
    if columns(connection, "suite_revenue"):
        changed = 0
        for start in range(0, len(refs), 500):
            chunk = refs[start : start + 500]
            for row in connection.execute(
                f"SELECT id, label, customer FROM suite_revenue WHERE source_id IN ({placeholders(chunk)})",  # noqa: S608
                tuple(chunk),
            ).fetchall():
                connection.execute(
                    "UPDATE suite_revenue SET customer = '', label = ? WHERE id = ?",
                    (scrub_names(row[1], names), row[0]),
                )
                changed += 1
        out["suite_revenue"] = changed
    for table, cols in (
        ("suite_activity", ("title", "detail")),
        ("suite_outbox", ("subject", "body")),
    ):
        have = columns(connection, table)
        if not have:
            continue
        changed = 0
        for start in range(0, len(refs), 500):
            chunk = refs[start : start + 500]
            for row in connection.execute(
                f'SELECT rowid, {", ".join(cols)} FROM "{table}" WHERE ref_id IN ({placeholders(chunk)})',  # noqa: S608
                tuple(chunk),
            ).fetchall():
                values = [scrub_names(row[i + 1], names) for i in range(len(cols))]
                extra = ", actor = ''" if "actor" in have else ""
                connection.execute(
                    f'UPDATE "{table}" SET {", ".join(f"{c} = ?" for c in cols)}{extra} WHERE rowid = ?',  # noqa: S608
                    (*values, row[0]),
                )
                changed += 1
        if changed:
            out[table] = changed
    return out


# Columns that hold an email address or a phone number, wherever they are (the last sweep blanks any left).
EMAIL_COLUMN = re.compile(
    r"(^|_)(e?mail|email_address|recipient|to_address|from_email)$"
)
PHONE_COLUMN = re.compile(r"(^|_)(phone|mobile|whatsapp)$")
SWEEP_SKIP = frozenset(
    {
        "audit_logs",
        "document_audit",
        "erasure_jobs",
        "schema_migrations",
        "settings",
        "suite_settings",
        "lead_suppressions",  # a do-not-contact list is how an opt-out is honoured
    }
)


def contact_columns(connection: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """(table, column, "emails" | "phones") for every column of an ordinary table that holds an email address or a
    phone number."""
    out = []
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL%' ORDER BY name"
    ).fetchall():
        if table in SWEEP_SKIP or not _NAME.fullmatch(table):
            continue
        for column in columns(connection, table):
            lowered = column.lower()
            if lowered == "contact":  # free text: an email address or a phone number
                out += [(table, column, "emails"), (table, column, "phones")]
            elif EMAIL_COLUMN.search(lowered):
                out.append((table, column, "emails"))
            elif PHONE_COLUMN.search(lowered):
                out.append((table, column, "phones"))
    return out


@dataclass
class Tally:
    """Counts an eraser returns: ``tally.add("cr_doses", n)``."""

    counts: dict[str, int] = field(default_factory=dict)

    def add(self, table: str, number: int) -> int:
        if number:
            self.counts[table] = self.counts.get(table, 0) + int(number)
        return number

    def merge(self, other: Mapping[str, int]) -> None:
        for table, number in other.items():
            self.add(table, number)
