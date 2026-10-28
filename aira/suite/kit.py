"""Building blocks every industry app uses, so each app is only its own domain.

* Validation that raises ``ValueError`` with a sentence a person can act on (the route layer answers 400).
* Money in integer cents, formatted per currency.
* Tables created on first use (``ensure``), and small row helpers.
* The business profile (name, contact, currency, brand colour) shown on every public page.
* The activity feed and the revenue ledger that the Business hub adds up across apps.
* Setup checklists: each app says what a new business should do first, the hub shows progress.
* An email outbox: messages an app prepares (a reminder, an invoice, a ticket) wait for approval unless the
  business turned on automatic sending for that kind, and go out through the business's own mail server.
* The writing model (Ollama locally, the organization's model hosted), with a template fallback.
"""

from __future__ import annotations

import csv
import io
import json
import re
import sqlite3
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from aira import suite

# ---------------------------------------------------------------------------
# ids and time
# ---------------------------------------------------------------------------


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def now() -> str:
    """The current time as ISO 8601 UTC (``2026-10-25T09:30:00Z``)."""
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def today() -> str:
    return utcnow().date().isoformat()


def iso(moment: datetime) -> str:
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_time(value: str) -> datetime:
    text = str(value or "").strip().replace("Z", "+00:00")
    moment = datetime.fromisoformat(text)
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)


def month_of(value: str) -> str:
    return str(value or "")[:7]


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------

_EMAIL = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_PHONE = re.compile(r"^[0-9+()\-. ]{5,30}$")
_SLUG = re.compile(r"[^a-z0-9]+")
_COLOR = re.compile(r"^#[0-9a-fA-F]{6}$")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def text(
    value: Any,
    name: str,
    *,
    max_len: int = 200,
    required: bool = False,
    multiline: bool = False,
) -> str:
    """Trimmed text without control characters; ``required`` refuses an empty value."""
    cleaned = _CONTROL.sub("", str(value if value is not None else "")).strip()
    if not multiline:
        cleaned = " ".join(cleaned.split())
    if required and not cleaned:
        raise ValueError(f"Enter {name}.")
    if len(cleaned) > max_len:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} is too long (at most {max_len} characters)."
        )
    return cleaned


def email(value: Any, name: str = "an email address", *, required: bool = False) -> str:
    cleaned = text(value, name, max_len=254, required=required).lower()
    if cleaned and not _EMAIL.match(cleaned):
        raise ValueError(f"{cleaned} is not an email address.")
    return cleaned


def phone(value: Any, name: str = "a phone number", *, required: bool = False) -> str:
    cleaned = text(value, name, max_len=30, required=required)
    if cleaned and not _PHONE.match(cleaned):
        raise ValueError(f"{cleaned} is not a phone number.")
    return cleaned


def url(value: Any, name: str = "a link", *, required: bool = False) -> str:
    cleaned = text(value, name, max_len=500, required=required)
    if cleaned and not re.match(
        r"^https?://[^\s/$.?#].[^\s]*$", cleaned, re.IGNORECASE
    ):
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} must start with http:// or https://."
        )
    return cleaned


def integer(
    value: Any,
    name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if value is None or value == "":
        if default is None:
            raise ValueError(f"Enter {name}.")
        return default
    try:
        number = int(str(value).strip())
    except ValueError:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} must be a whole number."
        ) from None
    if minimum is not None and number < minimum:
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be at least {minimum}.")
    if maximum is not None and number > maximum:
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be at most {maximum}.")
    return number


def flag(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def choice(
    value: Any, name: str, options: Sequence[str], *, default: str | None = None
) -> str:
    cleaned = str(value or "").strip().lower()
    if not cleaned and default is not None:
        return default
    if cleaned not in options:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} must be one of: {', '.join(options)}."
        )
    return cleaned


def day(value: Any, name: str = "a date", *, required: bool = False) -> str:
    """A calendar date as ``YYYY-MM-DD``."""
    cleaned = text(value, name, max_len=10, required=required)
    if not cleaned:
        return ""
    try:
        return date.fromisoformat(cleaned).isoformat()
    except ValueError:
        raise ValueError(f"{cleaned} is not a date (use YYYY-MM-DD).") from None


def moment(value: Any, name: str = "a time", *, required: bool = False) -> str:
    """A point in time as ISO 8601 UTC; a value without a zone is read as UTC."""
    cleaned = text(value, name, max_len=40, required=required)
    if not cleaned:
        return ""
    try:
        return iso(parse_time(cleaned))
    except ValueError:
        raise ValueError(f"{cleaned} is not a date and time.") from None


def color(value: Any, *, default: str = "#2563eb") -> str:
    cleaned = str(value or "").strip()
    if not cleaned:
        return default
    if not _COLOR.match(cleaned):
        raise ValueError("Use a colour like #2563eb.")
    return cleaned.lower()


def slug(value: Any, *, max_len: int = 60) -> str:
    return _SLUG.sub("-", str(value or "").lower()).strip("-")[:max_len] or "item"


def tags(value: Any, *, limit: int = 20) -> list[str]:
    items = value if isinstance(value, list) else str(value or "").split(",")
    out: list[str] = []
    for item in items:
        tag = text(item, "a tag", max_len=40).lower()
        if tag and tag not in out:
            out.append(tag)
    return out[:limit]


# ---------------------------------------------------------------------------
# money (integer cents)
# ---------------------------------------------------------------------------

CURRENCIES = {
    "USD": "$",
    "EUR": "€",
    "GBP": "£",
    "INR": "₹",
    "CAD": "CA$",
    "AUD": "A$",
    "SGD": "S$",
    "AED": "AED ",
    "JPY": "¥",
}
ZERO_DECIMAL = {"JPY"}


def cents(
    value: Any, name: str = "an amount", *, default: int | None = None, minimum: int = 0
) -> int:
    """``"12.50"``, ``12.5``, ``"1,200"`` -> cents. Negative amounts are refused unless ``minimum`` allows."""
    if value is None or value == "":
        if default is None:
            raise ValueError(f"Enter {name}.")
        return default
    raw: str = str(value).strip().replace(",", "")
    for symbol in sorted(set(CURRENCIES.values()), key=len, reverse=True):
        raw = raw.replace(str(symbol).strip(), "")
    try:
        amount = Decimal(raw.strip())
    except InvalidOperation:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} must be a number like 12.50."
        ) from None
    if not amount.is_finite():  # "Infinity", "NaN", "sNaN"
        raise ValueError(f"{name[:1].upper()}{name[1:]} must be a number like 12.50.")
    try:
        result = int((amount * 100).quantize(Decimal("1")))
    except ArithmeticError:  # "1e999999"
        raise ValueError(f"{name[:1].upper()}{name[1:]} is too large.") from None
    if result < minimum:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} cannot be below {minimum / 100:.2f}."
        )
    if result > 10_000_000_00:
        raise ValueError(f"{name[:1].upper()}{name[1:]} is too large.")
    return result


def cents_value(
    value: Any, name: str = "an amount", *, default: int | None = None, minimum: int = 0
) -> int:
    """A value that is already in cents (an ``*_cents`` field from the UI kit's money inputs): an integer."""
    return integer(value, name, default=default, minimum=minimum, maximum=10_000_000_00)


def currency(value: Any, *, default: str = "USD") -> str:
    code = str(value or "").strip().upper() or default
    if code not in CURRENCIES:
        raise ValueError(f"Currency must be one of: {', '.join(CURRENCIES)}.")
    return code


def fmt_money(amount_cents: int, code: str = "USD") -> str:
    symbol = CURRENCIES.get(code, f"{code} ")
    if code in ZERO_DECIMAL:
        return f"{symbol}{round(amount_cents / 100):,}"
    sign = "-" if amount_cents < 0 else ""
    return f"{sign}{symbol}{abs(amount_cents) / 100:,.2f}"


def percent_of(amount_cents: int, rate_basis_points: int) -> int:
    """``rate_basis_points`` is hundredths of a percent (750 = 7.5%), rounded half up."""
    return int(
        (Decimal(amount_cents) * Decimal(rate_basis_points) / Decimal(10000)).quantize(
            Decimal("1"), rounding=ROUND_HALF_UP
        )
    )


# ---------------------------------------------------------------------------
# tables and rows
# ---------------------------------------------------------------------------

_ensured: set[tuple[str, str]] = set()


def _db_file(connection: sqlite3.Connection) -> str:
    try:
        rows = connection.execute("PRAGMA database_list").fetchall()
        return (
            str(rows[0][2] or ":memory:")
            + f"#{id(connection) if not rows[0][2] else ''}"
        )
    except sqlite3.Error:
        return f":memory:#{id(connection)}"


def ensure(connection: sqlite3.Connection, key: str, statements: Iterable[str]) -> None:
    """Run an app's ``CREATE TABLE IF NOT EXISTS`` statements once per database file."""
    marker = (_db_file(connection), key)
    if marker in _ensured:
        return
    for statement in statements:
        connection.execute(statement)
    connection.commit()
    _ensured.add(marker)


def forget_schema() -> None:
    """Tests that delete a database between runs call this so tables are created again."""
    _ensured.clear()


def as_dict(row: Any) -> dict[str, Any]:
    if row is None:
        return {}
    if isinstance(row, dict):
        return dict(row)
    return {key: row[key] for key in row.keys()}


def _params(params: Sequence[Any] | Mapping[str, Any]) -> Any:
    """Positional parameters as a tuple; named parameters (a mapping) as they are."""
    return params if isinstance(params, Mapping) else tuple(params)


def fetch_all(
    connection: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
) -> list[dict[str, Any]]:
    return [as_dict(row) for row in connection.execute(sql, _params(params)).fetchall()]


def fetch_one(
    connection: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
) -> dict[str, Any] | None:
    row = connection.execute(sql, _params(params)).fetchone()
    return as_dict(row) if row is not None else None


def require_row(
    connection: sqlite3.Connection, table: str, row_id: str, label: str = "item"
) -> dict[str, Any]:
    """The row with this id, or LookupError (404)."""
    if not re.fullmatch(r"[a-z_]{1,40}", table):
        raise ValueError("Bad table name.")
    found = fetch_one(connection, f"SELECT * FROM {table} WHERE id = ?", (row_id,))  # noqa: S608 - table is checked
    if not found:
        raise LookupError(f"That {label} was not found.")
    return found


def update_row(
    connection: sqlite3.Connection, table: str, row_id: str, values: Mapping[str, Any]
) -> None:
    if not re.fullmatch(r"[a-z_]{1,40}", table) or not values:
        return
    columns = [column for column in values if re.fullmatch(r"[a-z_]{1,40}", column)]
    assignments = ", ".join(f"{column} = ?" for column in columns)
    connection.execute(
        f"UPDATE {table} SET {assignments} WHERE id = ?",  # noqa: S608 - names are checked
        (*[values[column] for column in columns], row_id),
    )


def loads(value: Any, default: Any = None) -> Any:
    try:
        return json.loads(value) if value else default
    except (TypeError, ValueError):
        return default


def dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def csv_bytes(rows: Iterable[Mapping[str, Any]], columns: Sequence[str]) -> bytes:
    """A spreadsheet-safe CSV (formula-looking cells are prefixed with an apostrophe)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(columns)
    for row in rows:
        cells = []
        for column in columns:
            value = row.get(column, "")
            cell = "" if value is None else str(value)
            if cell[:1] in ("=", "+", "-", "@") and not re.fullmatch(
                r"-?\d+(\.\d+)?", cell
            ):
                cell = "'" + cell
            cells.append(cell)
        writer.writerow(cells)
    return buffer.getvalue().encode("utf-8")


# ---------------------------------------------------------------------------
# the shared tables
# ---------------------------------------------------------------------------

CORE_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS suite_settings (
        app TEXT NOT NULL,
        key TEXT NOT NULL,
        value TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        PRIMARY KEY (app, key)
    )""",
    """CREATE TABLE IF NOT EXISTS suite_activity (
        id TEXT PRIMARY KEY,
        app TEXT NOT NULL,
        kind TEXT NOT NULL,
        title TEXT NOT NULL,
        detail TEXT NOT NULL DEFAULT '',
        ref_type TEXT NOT NULL DEFAULT '',
        ref_id TEXT NOT NULL DEFAULT '',
        amount_cents INTEGER NOT NULL DEFAULT 0,
        actor TEXT NOT NULL DEFAULT '',
        public INTEGER NOT NULL DEFAULT 0,
        at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS suite_activity_at ON suite_activity (at)",
    "CREATE INDEX IF NOT EXISTS suite_activity_app ON suite_activity (app, at)",
    """CREATE TABLE IF NOT EXISTS suite_revenue (
        id TEXT PRIMARY KEY,
        app TEXT NOT NULL,
        source_type TEXT NOT NULL,
        source_id TEXT NOT NULL,
        label TEXT NOT NULL DEFAULT '',
        customer TEXT NOT NULL DEFAULT '',
        amount_cents INTEGER NOT NULL,
        currency TEXT NOT NULL DEFAULT 'USD',
        at TEXT NOT NULL,
        voided_at TEXT,
        UNIQUE (app, source_type, source_id)
    )""",
    "CREATE INDEX IF NOT EXISTS suite_revenue_at ON suite_revenue (at)",
    """CREATE TABLE IF NOT EXISTS suite_public_links (
        token TEXT PRIMARY KEY,
        token_hash TEXT NOT NULL UNIQUE,
        app TEXT NOT NULL,
        kind TEXT NOT NULL,
        ref_id TEXT NOT NULL,
        title TEXT NOT NULL DEFAULT '',
        views INTEGER NOT NULL DEFAULT 0,
        actions INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        last_seen_at TEXT,
        revoked_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS suite_public_links_ref ON suite_public_links (app, kind, ref_id)",
    """CREATE TABLE IF NOT EXISTS suite_outbox (
        id TEXT PRIMARY KEY,
        app TEXT NOT NULL,
        kind TEXT NOT NULL,
        recipient TEXT NOT NULL,
        subject TEXT NOT NULL,
        body TEXT NOT NULL,
        ref_type TEXT NOT NULL DEFAULT '',
        ref_id TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'pending',
        error TEXT NOT NULL DEFAULT '',
        send_after TEXT,
        created_at TEXT NOT NULL,
        approved_at TEXT,
        sent_at TEXT
    )""",
    "CREATE INDEX IF NOT EXISTS suite_outbox_status ON suite_outbox (status, created_at)",
)


def ensure_core(connection: sqlite3.Connection) -> None:
    ensure(connection, "suite.core", CORE_SCHEMA)


# ---------------------------------------------------------------------------
# settings and the business profile
# ---------------------------------------------------------------------------


def get_setting(
    connection: sqlite3.Connection, app: str, key: str, default: Any = None
) -> Any:
    ensure_core(connection)
    row = connection.execute(
        "SELECT value FROM suite_settings WHERE app = ? AND key = ?", (app, key)
    ).fetchone()
    return loads(row[0], default) if row else default


def set_setting(connection: sqlite3.Connection, app: str, key: str, value: Any) -> None:
    ensure_core(connection)
    connection.execute(
        """INSERT INTO suite_settings (app, key, value, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(app, key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at""",
        (app, key, dumps(value), now()),
    )


BUSINESS_DEFAULTS: dict[str, Any] = {
    "name": "",
    "tagline": "",
    "email": "",
    "phone": "",
    "address": "",
    "website": "",
    "currency": "USD",
    "timezone": "UTC",
    "accent": "#2563eb",
    "logo_url": "",
    "tax_rate_bp": 0,  # hundredths of a percent added to invoices and orders (750 = 7.5%)
    "tax_label": "Tax",
    "payment_link": "",  # the business's own payment page (Stripe, PayPal, Razorpay, UPI), shown to customers
    "payment_instructions": "",  # bank transfer details or "pay at the venue"
}


def business(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = get_setting(connection, "core", "business", {}) or {}
    return {
        **BUSINESS_DEFAULTS,
        **{k: v for k, v in stored.items() if k in BUSINESS_DEFAULTS},
    }


def save_business(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    current = business(connection)
    merged = {**current, **{k: v for k, v in raw.items() if k in BUSINESS_DEFAULTS}}
    clean = {
        "name": text(merged["name"], "the business name", max_len=120),
        "tagline": text(merged["tagline"], "a tagline", max_len=160),
        "email": email(merged["email"]),
        "phone": phone(merged["phone"]),
        "address": text(merged["address"], "an address", max_len=300, multiline=True),
        "website": url(merged["website"], "the website"),
        "currency": currency(merged["currency"]),
        "timezone": text(merged["timezone"], "a time zone", max_len=60) or "UTC",
        "accent": color(merged["accent"]),
        "logo_url": url(merged["logo_url"], "the logo link"),
        "tax_rate_bp": integer(
            merged["tax_rate_bp"], "the tax rate", default=0, minimum=0, maximum=5000
        ),
        "tax_label": text(merged["tax_label"], "the tax label", max_len=30) or "Tax",
        "payment_link": url(merged["payment_link"], "the payment link"),
        "payment_instructions": text(
            merged["payment_instructions"],
            "payment instructions",
            max_len=600,
            multiline=True,
        ),
    }
    set_setting(connection, "core", "business", clean)
    connection.commit()
    return clean


def public_business(connection: sqlite3.Connection) -> dict[str, Any]:
    """What customers see on a public page (never the internal settings)."""
    profile = business(connection)
    return {
        key: profile[key]
        for key in (
            "name",
            "tagline",
            "email",
            "phone",
            "address",
            "website",
            "currency",
            "accent",
            "logo_url",
            "payment_link",
            "payment_instructions",
        )
    }


# ---------------------------------------------------------------------------
# activity and revenue
# ---------------------------------------------------------------------------


# Something outside the apps that hears every activity line (the hosted build turns them into webhook events).
_event_listener: Callable[[str, str, dict[str, Any]], None] | None = None


def set_event_listener(
    listener: Callable[[str, str, dict[str, Any]], None] | None,
) -> None:
    global _event_listener
    _event_listener = listener


def log(
    connection: sqlite3.Connection,
    app: str,
    kind: str,
    title: str,
    *,
    detail: str = "",
    ref_type: str = "",
    ref_id: str = "",
    amount_cents: int = 0,
    actor: str = "",
    public: bool = False,
) -> None:
    """One line in the activity feed (``public`` = a customer did it on a public page)."""
    ensure_core(connection)
    connection.execute(
        """INSERT INTO suite_activity (id, app, kind, title, detail, ref_type, ref_id, amount_cents, actor, public, at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id(),
            app,
            kind[:40],
            title[:200],
            detail[:500],
            ref_type[:40],
            ref_id[:64],
            int(amount_cents),
            actor[:120],
            1 if public else 0,
            now(),
        ),
    )
    if _event_listener is not None:
        try:
            _event_listener(
                app,
                kind[:40],
                {
                    "title": title[:200],
                    "detail": detail[:500],
                    "ref_type": ref_type[:40],
                    "ref_id": ref_id[:64],
                    "amount_cents": int(amount_cents),
                    "public": public,
                },
            )
        except Exception:  # noqa: BLE001 - a listener (webhooks) never fails the app's own work
            suite.logger.exception("The activity listener failed")


def activity(
    connection: sqlite3.Connection, *, app: str = "", limit: int = 50, before: str = ""
) -> list[dict[str, Any]]:
    ensure_core(connection)
    clauses, params = [], []
    if app:
        clauses.append("app = ?")
        params.append(app)
    if before:
        clauses.append("at < ?")
        params.append(before)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = fetch_all(
        connection,
        f"SELECT * FROM suite_activity {where} ORDER BY at DESC, rowid DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(int(limit), 500))),
    )
    for row in rows:
        row["public"] = bool(row["public"])
    return rows


def record_revenue(
    connection: sqlite3.Connection,
    app: str,
    *,
    source_type: str,
    source_id: str,
    amount_cents: int,
    currency_code: str = "",
    label: str = "",
    customer: str = "",
    at: str = "",
) -> None:
    """Money the business received (an invoice paid, an order paid, a ticket sold). Recording the same source
    again updates it, so a payment is never counted twice."""
    ensure_core(connection)
    code = currency_code or business(connection)["currency"]
    connection.execute(
        """INSERT INTO suite_revenue (id, app, source_type, source_id, label, customer, amount_cents, currency, at, voided_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(app, source_type, source_id) DO UPDATE SET amount_cents = excluded.amount_cents,
        label = excluded.label, customer = excluded.customer, currency = excluded.currency,
        at = CASE WHEN ? <> '' OR suite_revenue.voided_at IS NOT NULL THEN excluded.at ELSE suite_revenue.at END,
        voided_at = NULL""",
        (
            new_id(),
            app,
            source_type[:40],
            source_id[:64],
            label[:200],
            customer[:200],
            int(amount_cents),
            code,
            at or now(),
            at,  # a payment recorded again with its own date, or after a void, moves to that date
        ),
    )


def void_revenue(
    connection: sqlite3.Connection, app: str, source_type: str, source_id: str
) -> None:
    """A refund or a payment marked by mistake: the line stays for the record and stops counting."""
    ensure_core(connection)
    connection.execute(
        "UPDATE suite_revenue SET voided_at = ? WHERE app = ? AND source_type = ? AND source_id = ? AND voided_at IS NULL",
        (now(), app, source_type, source_id),
    )


def revenue(
    connection: sqlite3.Connection,
    *,
    app: str = "",
    months: int = 6,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Totals for the Business hub: this month, last month, all time, by app and by month."""
    ensure_core(connection)
    current = (moment or utcnow()).date().replace(day=1)
    keys = []
    cursor = current
    for _ in range(max(1, min(months, 24))):
        keys.append(cursor.strftime("%Y-%m"))
        cursor = (cursor - timedelta(days=1)).replace(day=1)
    keys.reverse()
    where, params = "WHERE voided_at IS NULL", []
    if app:
        where += " AND app = ?"
        params.append(app)
    rows = fetch_all(
        connection,
        f"SELECT app, substr(at, 1, 7) AS month, SUM(amount_cents) AS total, COUNT(*) AS n FROM suite_revenue {where} GROUP BY app, month",  # noqa: S608
        params,
    )
    by_month = {key: 0 for key in keys}
    by_app: dict[str, int] = {}
    total = count = 0
    for row in rows:
        amount = int(row["total"] or 0)
        total += amount
        count += int(row["n"] or 0)
        by_app[row["app"]] = by_app.get(row["app"], 0) + amount
        if row["month"] in by_month:
            by_month[row["month"]] += amount
    this_month = by_month.get(keys[-1], 0)
    last_month = by_month.get(keys[-2], 0) if len(keys) > 1 else 0
    return {
        "currency": business(connection)["currency"],
        "this_month_cents": this_month,
        "last_month_cents": last_month,
        "change_percent": (
            round((this_month - last_month) * 100 / last_month) if last_month else None
        ),
        "total_cents": total,
        "payments": count,
        "by_app": by_app,
        "by_month": [{"month": key, "cents": by_month[key]} for key in keys],
    }


def revenue_lines(
    connection: sqlite3.Connection, *, app: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    ensure_core(connection)
    where, params = "", []
    if app:
        where, params = "WHERE app = ?", [app]
    return fetch_all(
        connection,
        f"SELECT * FROM suite_revenue {where} ORDER BY at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 5000))),
    )


# ---------------------------------------------------------------------------
# setup checklists and summaries (the hub asks every app)
# ---------------------------------------------------------------------------

ChecklistFn = Callable[[sqlite3.Connection], list[dict[str, Any]]]
SummaryFn = Callable[[sqlite3.Connection], dict[str, Any]]
CHECKLISTS: dict[str, ChecklistFn] = {}
SUMMARIES: dict[str, SummaryFn] = {}


def checklist(app: str) -> Callable[[ChecklistFn], ChecklistFn]:
    """Register an app's setup steps: ``fn(connection) -> [{"id", "label", "done", "hint"}]``."""

    def wrap(fn: ChecklistFn) -> ChecklistFn:
        CHECKLISTS[app] = fn
        return fn

    return wrap


def summary(app: str) -> Callable[[SummaryFn], SummaryFn]:
    """Register an app's headline numbers for the hub: ``fn(connection) -> {"cards": [{"label","value","hint"}]}``."""

    def wrap(fn: SummaryFn) -> SummaryFn:
        SUMMARIES[app] = fn
        return fn

    return wrap


def step(step_id: str, label: str, done: bool, hint: str = "") -> dict[str, Any]:
    return {"id": step_id, "label": label, "done": bool(done), "hint": hint}


def count(
    connection: sqlite3.Connection,
    sql: str,
    params: Sequence[Any] | Mapping[str, Any] = (),
) -> int:
    row = connection.execute(sql, _params(params)).fetchone()
    return int(row[0] or 0) if row else 0


# ---------------------------------------------------------------------------
# the email outbox
# ---------------------------------------------------------------------------

OUTBOX_STATUSES = ("pending", "approved", "sent", "failed", "cancelled")
EMAIL_SETTINGS_DEFAULTS: dict[str, Any] = {
    "from_name": "",
    "from_email": "",
    "reply_to": "",
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_security": "starttls",
    "smtp_username": "",
    "auto_send": [],  # kinds sent without approval, for example ["booking_confirmation", "ticket"]
}
SMTP_PASSWORD_SECRET = "suite_smtp_password"
# Safety notices a person set up for themselves (a caregiver told about a missed dose): sent without waiting for
# approval, once the business has set up email.
ALWAYS_SEND = frozenset({"missed_dose"})


def email_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = get_setting(connection, "core", "email", {}) or {}
    return {
        **EMAIL_SETTINGS_DEFAULTS,
        **{k: v for k, v in stored.items() if k in EMAIL_SETTINGS_DEFAULTS},
    }


def save_email_settings(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    merged = {
        **email_settings(connection),
        **{k: v for k, v in raw.items() if k in EMAIL_SETTINGS_DEFAULTS},
    }
    clean = {
        "from_name": text(merged["from_name"], "the sender name", max_len=100),
        "from_email": email(merged["from_email"], "the sender address"),
        "reply_to": email(merged["reply_to"], "the reply-to address"),
        "smtp_host": text(merged["smtp_host"], "the mail server", max_len=200).lower(),
        "smtp_port": integer(
            merged["smtp_port"],
            "the mail server port",
            default=587,
            minimum=1,
            maximum=65535,
        ),
        "smtp_security": choice(
            merged["smtp_security"],
            "security",
            ("starttls", "ssl", "none"),
            default="starttls",
        ),
        "smtp_username": text(
            merged["smtp_username"], "the mail username", max_len=200
        ),
        "auto_send": [
            text(kind, "a message kind", max_len=40)
            for kind in (merged.get("auto_send") or [])
        ][:30],
    }
    set_setting(connection, "core", "email", clean)
    connection.commit()
    return clean


def queue_email(
    connection: sqlite3.Connection,
    app: str,
    kind: str,
    *,
    to: str,
    subject: str,
    body: str,
    ref_type: str = "",
    ref_id: str = "",
    send_after: str = "",
) -> dict[str, Any] | None:
    """Prepare a message to a customer. It waits in the outbox for approval unless the business turned on
    automatic sending for this kind. Returns None when there is no address to send to."""
    ensure_core(connection)
    recipient = email(to) if to else ""
    if not recipient:
        return None
    automatic = kind in ALWAYS_SEND or kind in (
        email_settings(connection).get("auto_send") or []
    )
    message = {
        "id": new_id(),
        "app": app,
        "kind": kind[:40],
        "recipient": recipient,
        "subject": text(subject, "a subject", max_len=200) or "Message",
        "body": text(body, "a message", max_len=20000, multiline=True),
        "ref_type": ref_type[:40],
        "ref_id": ref_id[:64],
        "status": "approved" if automatic else "pending",
        "send_after": send_after or None,
        "created_at": now(),
        "approved_at": now() if automatic else None,
    }
    connection.execute(
        """INSERT INTO suite_outbox (id, app, kind, recipient, subject, body, ref_type, ref_id, status, send_after, created_at, approved_at)
        VALUES (:id, :app, :kind, :recipient, :subject, :body, :ref_type, :ref_id, :status, :send_after, :created_at, :approved_at)""",
        message,
    )
    return message


def outbox(
    connection: sqlite3.Connection, *, status: str = "", app: str = "", limit: int = 200
) -> list[dict[str, Any]]:
    ensure_core(connection)
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if app:
        clauses.append("app = ?")
        params.append(app)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    return fetch_all(
        connection,
        f"SELECT * FROM suite_outbox {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 1000))),
    )


def outbox_counts(connection: sqlite3.Connection) -> dict[str, int]:
    ensure_core(connection)
    counts = {status: 0 for status in OUTBOX_STATUSES}
    for row in connection.execute(
        "SELECT status, COUNT(*) FROM suite_outbox GROUP BY status"
    ):
        counts[row[0]] = int(row[1])
    return counts


def set_outbox_status(
    connection: sqlite3.Connection, ids: Sequence[str], status: str
) -> int:
    ensure_core(connection)
    if status not in ("approved", "cancelled"):
        raise ValueError("A message can be approved or cancelled.")
    changed = 0
    for message_id in ids[:500]:
        changed += connection.execute(
            "UPDATE suite_outbox SET status = ?, approved_at = CASE WHEN ? = 'approved' THEN ? ELSE approved_at END WHERE id = ? AND status IN ('pending', 'failed', 'approved')",
            (status, status, now(), str(message_id)),
        ).rowcount
    connection.commit()
    return changed


def edit_outbox(
    connection: sqlite3.Connection, message_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    ensure_core(connection)
    message = require_row(connection, "suite_outbox", message_id, "message")
    if message["status"] not in ("pending", "failed"):
        raise ValueError("Only a message that has not been sent can be edited.")
    values = {}
    if "subject" in raw:
        values["subject"] = text(
            raw["subject"], "a subject", max_len=200, required=True
        )
    if "body" in raw:
        values["body"] = text(
            raw["body"], "a message", max_len=20000, required=True, multiline=True
        )
    if "recipient" in raw:
        values["recipient"] = email(raw["recipient"], required=True)
    update_row(connection, "suite_outbox", message_id, values)
    connection.commit()
    return require_row(connection, "suite_outbox", message_id, "message")


def _transactional(
    settings: Mapping[str, Any], profile: Mapping[str, Any], message: Mapping[str, Any]
) -> Any:
    from email.message import EmailMessage
    from email.utils import formataddr, formatdate, make_msgid

    sender = str(settings["from_email"])
    mail = EmailMessage()
    mail["From"] = formataddr(
        (str(settings.get("from_name") or profile.get("name") or ""), sender)
    )
    mail["To"] = str(message["recipient"])
    mail["Subject"] = str(message["subject"])
    mail["Reply-To"] = str(settings.get("reply_to") or sender)
    mail["Date"] = formatdate(localtime=False)
    mail["Message-ID"] = make_msgid(domain=sender.split("@")[-1] or "localhost")
    mail["X-Mailer"] = "Monosage"
    footer_bits = [
        str(profile.get(k) or "")
        for k in ("name", "address", "phone")
        if profile.get(k)
    ]
    footer = ("\n\n--\n" + "\n".join(footer_bits)) if footer_bits else ""
    mail.set_content(str(message["body"]).replace("\r\n", "\n").rstrip() + footer)
    return mail


def send_outbox(
    connection: sqlite3.Connection,
    password: str,
    *,
    limit: int = 25,
    sender: Callable[..., None] | None = None,
    ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Send approved messages whose time has come, through the business's own mail server.

    ``ids`` limits the pass to those messages (an app sending what the person just approved)."""
    from aira.leads import mail as transport

    ensure_core(connection)
    settings = email_settings(connection)
    missing = [
        label
        for label, key in (
            ("sender address", "from_email"),
            ("mail server", "smtp_host"),
        )
        if not settings.get(key)
    ]
    if missing:
        raise ValueError(
            "Set up email first (Business hub > Email): " + ", ".join(missing) + "."
        )
    send = sender or transport.smtp_send
    profile = business(connection)
    due = fetch_all(
        connection,
        "SELECT * FROM suite_outbox WHERE status = 'approved' AND (send_after IS NULL OR send_after <= ?) ORDER BY approved_at LIMIT ?",
        (now(), max(1, min(limit, 100)) if ids is None else 1000),
    )
    if ids is not None:
        wanted = {str(item) for item in ids}
        due = [message for message in due if message["id"] in wanted][
            : max(1, min(limit, 100))
        ]
    result = {"sent": 0, "failed": 0, "details": []}
    for message in due:
        try:
            send(settings, password, _transactional(settings, profile, message))
        except Exception as error:  # noqa: BLE001 - one failure must not stop the rest
            connection.execute(
                "UPDATE suite_outbox SET status = 'failed', error = ? WHERE id = ?",
                (str(error)[:300], message["id"]),
            )
            result["failed"] += 1
            result["details"].append(
                {"id": message["id"], "status": "failed", "error": str(error)[:300]}
            )
        else:
            connection.execute(
                "UPDATE suite_outbox SET status = 'sent', sent_at = ?, error = '' WHERE id = ?",
                (now(), message["id"]),
            )
            result["sent"] += 1
            result["details"].append({"id": message["id"], "status": "sent"})
        connection.commit()
    return result


# ---------------------------------------------------------------------------
# links customers open
# ---------------------------------------------------------------------------


_ORIGIN: ContextVar[str] = ContextVar("aira_suite_origin", default="")


@contextmanager
def origin_of(request: Any) -> Iterator[None]:
    """Within this block, kit.public_url without a source uses the address this request came to."""
    token = _ORIGIN.set(str(request.base_url()) if hasattr(request, "base_url") else "")
    try:
        yield
    finally:
        _ORIGIN.reset(token)


def public_url(path: str, source: Any = None) -> str:
    """A full address for ``path`` (for example a public link's ``/p/<token>``) to put in an email.

    ``source`` is the route's ``request`` or a public action's ``visitor``: the address they used. The hosted
    build's configured public address wins over both (it is https behind a proxy)."""
    origin = suite.public_origin()
    if not origin and isinstance(source, Mapping):
        origin = str(source.get("base_url") or "")
    elif not origin and source is not None and hasattr(source, "base_url"):
        origin = str(source.base_url())
    if not origin:
        origin = _ORIGIN.get()
    return (origin or "http://127.0.0.1:8765").rstrip("/") + path


# ---------------------------------------------------------------------------
# the writing model
# ---------------------------------------------------------------------------


def draft(prompt: str, fallback: Mapping[str, Any]) -> dict[str, Any]:
    """Ask the writing model for a JSON object; fall back to ``fallback`` (marked ``"source": "template"``)
    when no model is available or its answer is not usable. The model's keys must match the fallback's."""
    reply = None
    try:
        reply = suite.gateway().model(prompt)
    except Exception:  # noqa: BLE001 - writing help is optional
        suite.logger.info("The writing model was not available", exc_info=True)
    data = loads(reply, None) if reply else None
    if isinstance(data, dict) and all(key in data for key in fallback):
        merged = {key: data[key] for key in fallback}
        if all(isinstance(merged[key], type(value)) for key, value in fallback.items()):
            return {**merged, "source": "model"}
    return {**dict(fallback), "source": "template"}
