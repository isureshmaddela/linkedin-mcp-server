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
    "tax_id": "",  # the business's own tax or registration number (VAT, GSTIN, EIN), printed on business invoices
    "payment_link": "",  # the business's own payment page (Stripe, PayPal, Razorpay, UPI), shown to customers
    "payment_instructions": "",  # bank transfer details or "pay at the venue"
    # Who the business sells to: consumers (B2C), other businesses (B2B) or both. The apps read it: the shop offers
    # trade accounts, wholesale prices and payment terms to businesses; Sites writes for the right buyer.
    "sell_to": "consumers",
    # Tax: "flat" adds tax_rate_bp to every invoice and order; "gst" (an Indian business with a GSTIN in tax_id,
    # selling in INR) splits it into CGST + SGST inside its own state and IGST to other states (tax_breakdown).
    "tax_mode": "flat",
    "prices_include_tax": False,  # True: prices already include the tax (shown as "incl."), nothing is added
    "state_code": "",  # the business's GST state code ("29" Karnataka); read from the GSTIN when empty
}
SELL_TO = ("consumers", "businesses", "both")
TAX_MODES = ("flat", "gst")


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
        "tax_id": text(merged["tax_id"], "your tax or registration number", max_len=40),
        "payment_link": url(merged["payment_link"], "the payment link"),
        "payment_instructions": text(
            merged["payment_instructions"],
            "payment instructions",
            max_len=600,
            multiline=True,
        ),
        "sell_to": choice(
            merged["sell_to"], "who you sell to", SELL_TO, default="consumers"
        ),
        "tax_mode": choice(merged["tax_mode"], "the tax mode", TAX_MODES, default="flat"),
        "prices_include_tax": flag(merged["prices_include_tax"]),
        "state_code": text(merged["state_code"], "the state code", max_len=2),
    }
    if clean["tax_mode"] == "gst":
        clean["tax_id"] = clean["tax_id"].upper().replace(" ", "")
        if clean["currency"] != "INR":
            raise ValueError("GST is for prices in Indian rupees: set the currency to INR first.")
        if not valid_gstin(clean["tax_id"]):
            raise ValueError(
                "Enter your 15-character GSTIN as your tax number (for example 29ABCDE1234F1Z5) to charge GST."
            )
        clean["state_code"] = clean["state_code"] or clean["tax_id"][:2]
    if clean["state_code"] and clean["state_code"] not in GST_STATES:
        raise ValueError("Pick your state from the list (its GST state code).")
    set_setting(connection, "core", "business", clean)
    connection.commit()
    return clean


def public_business(connection: sqlite3.Connection) -> dict[str, Any]:
    """What customers see on a public page (never the internal settings, never a key or a secret)."""
    profile = business(connection)
    shown = {
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
            "tax_id",
            "prices_include_tax",
        )
    }
    pay = payment_settings(connection)
    provider = pay["provider"]
    if provider != "manual":
        shown["payment_link"] = ""  # "none" and Razorpay do not send customers to a fixed page
    shown["payment_provider"] = provider
    shown["online_payments"] = online_ready(pay)
    shown["upi_id"] = pay["upi_id"] if profile["currency"] == "INR" else ""
    shown["upi_name"] = (pay["upi_name"] or profile["name"]) if shown["upi_id"] else ""
    return shown


# ---------------------------------------------------------------------------
# taxes: a flat rate, or GST (CGST + SGST inside the state, IGST between states)
# ---------------------------------------------------------------------------

# GST state and union territory codes (the first two digits of a GSTIN).
GST_STATES: dict[str, str] = {
    "01": "Jammu and Kashmir",
    "02": "Himachal Pradesh",
    "03": "Punjab",
    "04": "Chandigarh",
    "05": "Uttarakhand",
    "06": "Haryana",
    "07": "Delhi",
    "08": "Rajasthan",
    "09": "Uttar Pradesh",
    "10": "Bihar",
    "11": "Sikkim",
    "12": "Arunachal Pradesh",
    "13": "Nagaland",
    "14": "Manipur",
    "15": "Mizoram",
    "16": "Tripura",
    "17": "Meghalaya",
    "18": "Assam",
    "19": "West Bengal",
    "20": "Jharkhand",
    "21": "Odisha",
    "22": "Chhattisgarh",
    "23": "Madhya Pradesh",
    "24": "Gujarat",
    "26": "Dadra and Nagar Haveli and Daman and Diu",
    "27": "Maharashtra",
    "29": "Karnataka",
    "30": "Goa",
    "31": "Lakshadweep",
    "32": "Kerala",
    "33": "Tamil Nadu",
    "34": "Puducherry",
    "35": "Andaman and Nicobar Islands",
    "36": "Telangana",
    "37": "Andhra Pradesh",
    "38": "Ladakh",
    "97": "Other Territory",
}
_GSTIN = re.compile(r"^[0-9]{2}[A-Z]{5}[0-9]{4}[A-Z][1-9A-Z]Z[0-9A-Z]$")
_GSTIN_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
_HSN = re.compile(r"^[0-9]{4,8}$")


def valid_gstin(value: Any) -> bool:
    """A 15-character GSTIN with a known state code and a correct check character."""
    code = str(value or "").strip().upper()
    if not _GSTIN.match(code) or code[:2] not in GST_STATES:
        return False
    total = 0
    for index, character in enumerate(code[:14]):
        product = _GSTIN_CHARS.index(character) * (1 if index % 2 == 0 else 2)
        total += product // 36 + product % 36
    return _GSTIN_CHARS[(36 - total % 36) % 36] == code[14]


def hsn_code(value: Any) -> str:
    """An HSN (goods) or SAC (services) code: 4 to 8 digits, or empty."""
    cleaned = text(value, "the HSN/SAC code", max_len=8).replace(" ", "")
    if cleaned and not _HSN.match(cleaned):
        raise ValueError("An HSN or SAC code is 4 to 8 digits.")
    return cleaned


def state_code(value: Any) -> str:
    """A customer's state as a GST state code: the code itself ("29"), or the state's name."""
    cleaned = text(value, "the state", max_len=60)
    if not cleaned:
        return ""
    if cleaned.zfill(2) in GST_STATES and cleaned.isdigit():
        return cleaned.zfill(2)
    for code, name in GST_STATES.items():
        if name.lower() == cleaned.lower():
            return code
    raise ValueError(f"{cleaned} is not an Indian state or union territory.")


def gst_applies(profile: Mapping[str, Any]) -> bool:
    return (
        profile.get("tax_mode") == "gst"
        and profile.get("currency") == "INR"
        and valid_gstin(profile.get("tax_id"))
    )


def tax_breakdown(
    profile: Mapping[str, Any],
    taxable_cents: int,
    *,
    customer_state: str = "",
    rate_bp: int | None = None,
    exempt: bool = False,
) -> dict[str, Any]:
    """The tax on ``taxable_cents`` (goods after discounts) under the business's settings.

    ``added_cents`` is what goes on top of the price (0 when prices include the tax); ``tax_cents`` is the tax
    either way; ``lines`` are the lines an invoice prints: one flat line, or CGST + SGST (half the rate each) when
    the customer is in the business's own state, or IGST when they are in another one. A customer whose state is
    not known is taken to be in the business's state."""
    rate = int(profile.get("tax_rate_bp") or 0) if rate_bp is None else int(rate_bp)
    inclusive = bool(profile.get("prices_include_tax"))
    amount = max(0, int(taxable_cents))
    if exempt or rate <= 0 or amount <= 0:
        tax = 0
    elif inclusive:
        tax = int(
            (Decimal(amount) * Decimal(rate) / Decimal(10000 + rate)).quantize(
                Decimal("1"), rounding=ROUND_HALF_UP
            )
        )
    else:
        tax = percent_of(amount, rate)
    result: dict[str, Any] = {
        "rate_bp": rate,
        "inclusive": inclusive,
        "tax_cents": tax,
        "added_cents": 0 if inclusive else tax,
        "net_cents": amount - tax if inclusive else amount,
        "gst": False,
        "supply": "",
        "place_of_supply": "",
        "lines": [],
    }
    if gst_applies(profile):
        home = str(profile.get("state_code") or str(profile.get("tax_id"))[:2])
        place = customer_state or home
        result.update(gst=True, place_of_supply=place)
        if place == home:
            central = tax // 2 + (tax % 2)
            result["supply"] = "intra"
            result["lines"] = [
                {"label": "CGST", "rate_bp": rate / 2, "cents": central},
                {"label": "SGST", "rate_bp": rate / 2, "cents": tax - central},
            ] if tax else []
        else:
            result["supply"] = "inter"
            result["lines"] = [{"label": "IGST", "rate_bp": rate, "cents": tax}] if tax else []
    elif tax:
        result["lines"] = [
            {"label": str(profile.get("tax_label") or "Tax"), "rate_bp": rate, "cents": tax}
        ]
    return result


# ---------------------------------------------------------------------------
# how customers pay online (Business hub > Business profile > Payments)
# ---------------------------------------------------------------------------

PAYMENT_PROVIDERS = ("none", "manual", "razorpay")
PAYMENT_DEFAULTS: dict[str, Any] = {
    # "none": customers see the payment instructions only; "manual": the payment_link page; "razorpay": a payment
    # link for the exact amount of each order, invoice or booking. '' (never saved) reads as "manual" when the
    # business has a payment_link, else "none".
    "provider": "",
    "razorpay_key_id": "",  # rzp_test_... or rzp_live_...: test or live mode
    "key_secret_saved": False,  # the key secret is in the secret store (never here)
    "webhook_secret_saved": False,
    "notify_customer": False,  # Razorpay also emails and texts the link to the customer
    "link_expiry_hours": 72,  # how long a payment link for an invoice or a booking stays open
    "upi_id": "",  # the business's own UPI ID (VPA): a "Pay by UPI app" button the business confirms by hand
    "upi_name": "",  # the payee name in the UPI app (the business name when empty)
}
RAZORPAY_KEY_SECRET = "suite_razorpay_key_secret"
RAZORPAY_WEBHOOK_SECRET = "suite_razorpay_webhook_secret"
_VPA = re.compile(r"^[A-Za-z0-9._-]{2,256}@[A-Za-z][A-Za-z0-9.-]{1,63}$")


def payment_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = get_setting(connection, "core", "payments", {}) or {}
    merged = {
        **PAYMENT_DEFAULTS,
        **{k: v for k, v in stored.items() if k in PAYMENT_DEFAULTS},
    }
    if not merged["provider"]:
        merged["provider"] = "manual" if business(connection)["payment_link"] else "none"
    return merged


def save_payment_settings(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """Save the plain settings (the key id, the UPI ID...). Secrets go to the secret store through the route;
    ``key_secret_saved`` and ``webhook_secret_saved`` only record that they are there."""
    merged = {
        **payment_settings(connection),
        **{k: v for k, v in raw.items() if k in PAYMENT_DEFAULTS},
    }
    clean = {
        "provider": choice(merged["provider"], "the payment provider", PAYMENT_PROVIDERS, default="none"),
        "razorpay_key_id": text(merged["razorpay_key_id"], "the Razorpay key id", max_len=60),
        "key_secret_saved": flag(merged["key_secret_saved"]),
        "webhook_secret_saved": flag(merged["webhook_secret_saved"]),
        "notify_customer": flag(merged["notify_customer"]),
        "link_expiry_hours": integer(
            merged["link_expiry_hours"], "how long a payment link stays open (hours)",
            default=72, minimum=1, maximum=24 * 180,
        ),
        "upi_id": text(merged["upi_id"], "the UPI ID", max_len=300).replace(" ", ""),
        "upi_name": text(merged["upi_name"], "the UPI payee name", max_len=60),
    }
    key_id = clean["razorpay_key_id"]
    if key_id and not re.match(r"^rzp_(test|live)_[A-Za-z0-9]{6,40}$", key_id):
        raise ValueError(
            "The Razorpay key id starts with rzp_test_ or rzp_live_ (Razorpay dashboard > Account & Settings > API keys)."
        )
    if clean["upi_id"] and not _VPA.match(clean["upi_id"]):
        raise ValueError("A UPI ID looks like yourname@okbank.")
    if clean["provider"] == "manual" and not business(connection)["payment_link"]:
        raise ValueError("Add your payment link to the business profile first, or pick another way to be paid.")
    if clean["provider"] == "razorpay" and not key_id:
        raise ValueError("Enter your Razorpay key id to take payments with Razorpay.")
    set_setting(connection, "core", "payments", clean)
    connection.commit()
    return clean


def payment_mode(settings: Mapping[str, Any]) -> str:
    """``test`` or ``live`` from the Razorpay key id, '' when there is none."""
    found = re.match(r"^rzp_(test|live)_", str(settings.get("razorpay_key_id") or ""))
    return found.group(1) if found else ""


def online_ready(settings: Mapping[str, Any]) -> bool:
    """Customers can pay online: Razorpay is picked and its key id and key secret are saved."""
    return bool(
        settings.get("provider") == "razorpay"
        and payment_mode(settings)
        and settings.get("key_secret_saved")
    )


# ---------------------------------------------------------------------------
# time zones (months in the ledger are the business's months)
# ---------------------------------------------------------------------------

# Zones without daylight saving that should work even where the system has no time-zone database (Windows without
# the tzdata package): India's businesses bucket their months correctly either way.
_FIXED_ZONES = {
    "Asia/Kolkata": 330,
    "Asia/Calcutta": 330,
    "Asia/Dubai": 240,
    "Asia/Singapore": 480,
    "Asia/Tokyo": 540,
    "Asia/Kathmandu": 345,
    "Asia/Dhaka": 360,
    "Asia/Karachi": 300,
    "Asia/Shanghai": 480,
    "Asia/Hong_Kong": 480,
    "UTC": 0,
    "Etc/UTC": 0,
}
_OFFSET = re.compile(r"^(?:UTC|GMT)?\s*([+-])(\d{1,2})(?::?(\d{2}))?$", re.IGNORECASE)


def zone_info(name: str) -> Any:
    """A tzinfo for an IANA name ("Asia/Kolkata") or an offset ("UTC+05:30"); UTC when it is not known."""
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    label = str(name or "").strip() or "UTC"
    offset = _OFFSET.match(label)
    if offset:
        minutes = int(offset.group(2)) * 60 + int(offset.group(3) or 0)
        if minutes <= 14 * 60:
            sign = -1 if offset.group(1) == "-" else 1
            return timezone(timedelta(minutes=sign * minutes))
    try:
        return ZoneInfo(label)
    except (ZoneInfoNotFoundError, ValueError):
        if label in _FIXED_ZONES:
            return timezone(timedelta(minutes=_FIXED_ZONES[label]))
        return timezone.utc


def business_zone(connection: sqlite3.Connection) -> Any:
    return zone_info(business(connection)["timezone"])


# ---------------------------------------------------------------------------
# the public address (single-user build: the business's own tunnel to this computer)
# ---------------------------------------------------------------------------

# () -> the public address of the account bound to this request ('' for none); the single-user server installs it.
_address_source: Callable[[], str] | None = None
# (address) -> None: tell the server this account answers customers at ``address`` ('' when it no longer does),
# so its Host check lets that address reach the public pages.
_public_host_hook: Callable[[str], None] | None = None


def set_address_source(source: Callable[[], str] | None) -> None:
    global _address_source
    _address_source = source


def set_public_host_hook(hook: Callable[[str], None] | None) -> None:
    global _public_host_hook
    _public_host_hook = hook


def public_address(connection: sqlite3.Connection) -> str:
    """The https address customers use to reach this computer (a Cloudflare Tunnel or ngrok address), or ''."""
    return str(get_setting(connection, "core", "public_address", "") or "")


def save_public_address(connection: sqlite3.Connection, value: Any) -> str:
    """Save the public address: ``https://`` and a host name only (no path, no IP address, not this computer)."""
    from urllib.parse import urlsplit

    cleaned = text(value, "the public address", max_len=200).rstrip("/")
    if cleaned:
        parts = urlsplit(cleaned)
        host = (parts.hostname or "").lower()
        if parts.scheme != "https" or not host:
            raise ValueError("The public address starts with https:// (your tunnel's address).")
        if parts.path not in ("", "/") or parts.query or parts.fragment or parts.username:
            raise ValueError("Enter only the address, like https://shop.example.com (no path).")
        if (
            host in ("localhost",)
            or host.endswith(".localhost")
            or re.fullmatch(r"[0-9.]+|\[?[0-9a-f:]+\]?", host)
            or "." not in host
        ):
            raise ValueError("The public address is a name on the internet, not this computer or an IP address.")
        cleaned = f"https://{host}" + (f":{parts.port}" if parts.port else "")
    set_setting(connection, "core", "public_address", cleaned)
    connection.commit()
    if _public_host_hook is not None:
        try:
            _public_host_hook(cleaned)
        except Exception:  # noqa: BLE001 - the address is saved; the server learns it on its next reload
            suite.logger.exception("Could not register the public address")
    return cleaned


def is_loopback_url(link: str) -> bool:
    """Whether a link points at this computer (127.x, localhost, ::1): customers elsewhere cannot open it."""
    from urllib.parse import urlsplit

    host = (urlsplit(str(link or "")).hostname or "").lower()
    return (
        host in ("localhost", "::1", "0.0.0.0")
        or host.endswith(".localhost")
        or host.startswith("127.")
    )


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
    covers: str = "",
) -> None:
    """Money the business received (an invoice paid, an order paid, a ticket sold). Recording the same source
    again updates it, so a payment is never counted twice.

    ``covers`` names another app's line for the same sale (:func:`revenue_key`), such as the Marketing lead a Sales
    deal came from: while this line counts, that one does not (:func:`_cover`), whichever was recorded first."""
    _ensure_ledger(connection)
    code = currency_code or business(connection)["currency"]
    covers = str(covers or "")[:200]
    connection.execute(
        """INSERT INTO suite_revenue (id, app, source_type, source_id, label, customer, amount_cents, currency, at, voided_at, covers, covered_by)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, '')
        ON CONFLICT(app, source_type, source_id) DO UPDATE SET amount_cents = excluded.amount_cents,
        label = excluded.label, customer = excluded.customer, currency = excluded.currency,
        at = CASE WHEN ? <> '' OR suite_revenue.voided_at IS NOT NULL THEN excluded.at ELSE suite_revenue.at END,
        voided_at = NULL, covers = excluded.covers, covered_by = ''""",
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
            covers,
            at,  # a payment recorded again with its own date, or after a void, moves to that date
        ),
    )
    key = revenue_key(app, source_type, source_id)
    if covers and covers != key:
        _cover(connection, covers, by=key)
    _cover_if_covered(connection, key)


def revenue_key(app: str, source_type: str, source_id: str) -> str:
    """How one ledger line is named in ``covers`` / ``covered_by``: ``app:source_type:source_id``."""
    return f"{app}:{source_type[:40]}:{source_id[:64]}"


def _split_key(key: str) -> tuple[str, str, str] | None:
    parts = str(key or "").split(":", 2)
    return (parts[0], parts[1], parts[2]) if len(parts) == 3 and all(parts) else None


def _cover(connection: sqlite3.Connection, target: str, *, by: str) -> int:
    """The same sale counted elsewhere (``by``): the payment ``target`` and its refunds stop counting, marked
    ``covered_by`` so they count again when ``by`` is voided (:func:`_uncover`)."""
    parts = _split_key(target)
    if parts is None or target == by:
        return 0
    app, source_type, source_id = parts
    return int(
        connection.execute(
            """UPDATE suite_revenue SET voided_at = ?, covered_by = ? WHERE app = ? AND source_type = ? AND voided_at IS NULL
            AND ((kind = 'payment' AND source_id = ?) OR (kind = 'refund' AND refund_of = ?))""",
            (now(), by[:200], app, source_type, source_id, source_id),
        ).rowcount
        or 0
    )


def _cover_if_covered(connection: sqlite3.Connection, key: str) -> None:
    """A line recorded (again) while another app's counting line covers it does not count."""
    found = connection.execute(
        "SELECT app, source_type, source_id FROM suite_revenue WHERE covers = ? AND voided_at IS NULL AND kind = 'payment' LIMIT 1",
        (key,),
    ).fetchone()
    if found is not None:
        _cover(connection, key, by=revenue_key(str(found[0]), str(found[1]), str(found[2])))


def _uncover(connection: sqlite3.Connection, by: str) -> None:
    """``by`` stopped counting: what it covered counts again, unless another counting line covers it too."""
    rows = connection.execute(
        "SELECT app, source_type, source_id, kind, refund_of FROM suite_revenue WHERE covered_by = ?",
        (by,),
    ).fetchall()
    if not rows:
        return
    connection.execute(
        "UPDATE suite_revenue SET voided_at = NULL, covered_by = '' WHERE covered_by = ?",
        (by,),
    )
    targets = {
        revenue_key(str(row[0]), str(row[1]), str(row[4] if row[3] == "refund" else row[2]))
        for row in rows
    }
    for target in targets:
        _cover_if_covered(connection, target)


def void_revenue(
    connection: sqlite3.Connection,
    app: str,
    source_type: str,
    source_id: str,
    *,
    refund_cents: int | None = None,
    refund_id: str = "",
    at: str = "",
) -> None:
    """A payment marked by mistake: the line stays for the record and stops counting.

    With ``refund_cents`` it is a refund instead: the payment keeps counting in its own month and a negative line
    for the refunded amount is dated now (or ``at``), in the refund's month (:func:`record_refund`)."""
    _ensure_ledger(connection)
    if refund_cents is not None:
        line = fetch_one(
            connection,
            "SELECT * FROM suite_revenue WHERE app = ? AND source_type = ? AND source_id = ? AND kind = 'payment'",
            (app, source_type, source_id),
        )
        if line is None or line["voided_at"]:
            return
        record_refund(
            connection,
            app,
            source_type=source_type,
            source_id=source_id,
            amount_cents=int(refund_cents),
            currency_code=str(line["currency"]),
            label=str(line["label"]),
            customer=str(line["customer"]),
            refund_id=refund_id,
            at=at,
        )
        return
    # voided for its own reason now (not only covered by another app's line), so un-covering leaves it voided
    connection.execute(
        "UPDATE suite_revenue SET voided_at = COALESCE(voided_at, ?), covered_by = '' WHERE app = ? AND source_type = ? AND source_id = ?",
        (now(), app, source_type, source_id),
    )
    _uncover(connection, revenue_key(app, source_type, source_id))


def record_refund(
    connection: sqlite3.Connection,
    app: str,
    *,
    source_type: str,
    source_id: str,
    amount_cents: int,
    currency_code: str = "",
    label: str = "",
    customer: str = "",
    refund_id: str = "",
    at: str = "",
) -> str:
    """Money given back: a negative line dated in the refund's month, next to the payment (which keeps counting
    in its own month). ``refund_id`` names this refund, so recording it again changes nothing; without one, each
    call is a further refund of the same source. Returns the line's id."""
    _ensure_ledger(connection)
    amount = abs(int(amount_cents))
    if amount <= 0:
        raise ValueError("A refund needs an amount.")
    code = currency_code or business(connection)["currency"]
    key = f"{source_id}~refund~{refund_id or new_id()}"[:96]
    line_id = new_id()
    connection.execute(
        """INSERT INTO suite_revenue (id, app, source_type, source_id, label, customer, amount_cents, currency, at, voided_at, kind, refund_of)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, 'refund', ?)
        ON CONFLICT(app, source_type, source_id) DO NOTHING""",
        (
            line_id,
            app,
            source_type[:40],
            key,
            f"Refund: {label}"[:200] if label else "Refund",
            customer[:200],
            -amount,
            code,
            at or now(),
            source_id[:64],
        ),
    )
    # a refund of a payment another app's line covers does not count either
    payment = connection.execute(
        "SELECT covered_by FROM suite_revenue WHERE app = ? AND source_type = ? AND source_id = ? AND kind = 'payment'",
        (app, source_type[:40], source_id[:64]),
    ).fetchone()
    if payment is not None and payment[0]:
        connection.execute(
            "UPDATE suite_revenue SET voided_at = ?, covered_by = ? WHERE id = ?",
            (now(), payment[0], line_id),
        )
    return line_id


def refunded_cents(
    connection: sqlite3.Connection, app: str, source_type: str, source_id: str
) -> int:
    """How much of this source was refunded so far (a positive number)."""
    _ensure_ledger(connection)
    row = connection.execute(
        "SELECT COALESCE(SUM(amount_cents), 0) FROM suite_revenue WHERE app = ? AND source_type = ? AND refund_of = ? AND kind = 'refund' AND voided_at IS NULL",
        (app, source_type, source_id),
    ).fetchone()
    return -int(row[0] or 0)


def _ensure_ledger(connection: sqlite3.Connection) -> None:
    """The ledger's later columns: ``kind`` ('payment' or 'refund'), ``refund_of`` (the refunded source), and
    ``covers`` / ``covered_by`` (one sale recorded by two apps counts once: :func:`record_revenue`)."""
    ensure_core(connection)
    marker = (_db_file(connection), "suite.ledger.v3")
    if marker in _ensured:
        return
    columns = {row[1] for row in connection.execute("PRAGMA table_info(suite_revenue)")}
    if "kind" not in columns:
        connection.execute(
            "ALTER TABLE suite_revenue ADD COLUMN kind TEXT NOT NULL DEFAULT 'payment'"
        )
    if "refund_of" not in columns:
        connection.execute(
            "ALTER TABLE suite_revenue ADD COLUMN refund_of TEXT NOT NULL DEFAULT ''"
        )
    for column in ("covers", "covered_by"):
        if column not in columns:
            connection.execute(
                f"ALTER TABLE suite_revenue ADD COLUMN {column} TEXT NOT NULL DEFAULT ''"
            )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS suite_revenue_covers ON suite_revenue (covers)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS suite_revenue_refund ON suite_revenue (app, source_type, refund_of)"
    )
    connection.commit()
    _ensured.add(marker)


@contextmanager
def earnings_source(connection: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """The database that holds what the person earns. Here it is the bound one; in the hosted build, when each
    member has their own business apps, it is their own space (the life apps that show earnings run elsewhere)."""
    from aira.hosting import HOOKS

    other = HOOKS.earnings_connection() if HOOKS.earnings_connection else None
    if other is None:
        yield connection
        return
    try:
        yield other
    finally:
        other.close()


def revenue(
    connection: sqlite3.Connection,
    *,
    app: str = "",
    months: int = 6,
    moment: datetime | None = None,
) -> dict[str, Any]:
    """Totals for the Business hub: this month, last month, all time, by app and by month.

    Money in different currencies is never added together: the top-level numbers are the business's own currency,
    and ``by_currency`` has the same numbers for every currency that has a line (``currencies`` lists them, the
    business's first). Months are the business's months (its time zone), and refunds count, as negative lines, in
    the month they were made."""
    _ensure_ledger(connection)
    profile = business(connection)
    zone = zone_info(profile["timezone"])
    current = (moment or utcnow()).astimezone(zone).date().replace(day=1)
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
    rows = connection.execute(
        f"SELECT app, at, amount_cents, currency, kind FROM suite_revenue {where}",  # noqa: S608
        params,
    ).fetchall()
    sums: dict[str, dict[str, Any]] = {}

    def bucket(code: str) -> dict[str, Any]:
        if code not in sums:
            sums[code] = {
                "by_month": {key: 0 for key in keys},
                "by_app": {},
                "total": 0,
                "count": 0,
                "refunds": 0,
            }
        return sums[code]

    home = profile["currency"]
    bucket(home)
    for row in rows:
        code = str(row[3] or home)
        entry = bucket(code)
        amount = int(row[2] or 0)
        entry["total"] += amount
        if str(row[4] or "payment") == "refund":
            entry["refunds"] += -amount
        else:
            entry["count"] += 1
        entry["by_app"][row[0]] = entry["by_app"].get(row[0], 0) + amount
        try:
            month = parse_time(row[1]).astimezone(zone).strftime("%Y-%m")
        except ValueError:
            month = str(row[1] or "")[:7]
        if month in entry["by_month"]:
            entry["by_month"][month] += amount

    def view(entry: dict[str, Any]) -> dict[str, Any]:
        this_month = entry["by_month"].get(keys[-1], 0)
        last_month = entry["by_month"].get(keys[-2], 0) if len(keys) > 1 else 0
        return {
            "this_month_cents": this_month,
            "last_month_cents": last_month,
            "change_percent": (
                round((this_month - last_month) * 100 / last_month)
                if last_month
                else None
            ),
            "total_cents": entry["total"],
            "refunded_cents": entry["refunds"],
            "payments": entry["count"],
            "by_app": entry["by_app"],
            "by_month": [
                {"month": key, "cents": entry["by_month"][key]} for key in keys
            ],
        }

    by_currency = {code: view(entry) for code, entry in sums.items()}
    others = sorted(
        code for code in by_currency if code != home and sums[code]["count"] + abs(sums[code]["total"])
    )
    return {
        "currency": home,
        **by_currency[home],
        "timezone": profile["timezone"],
        "currencies": [home, *others],
        "by_currency": {code: by_currency[code] for code in (home, *others)},
    }


def revenue_lines(
    connection: sqlite3.Connection, *, app: str = "", limit: int = 500
) -> list[dict[str, Any]]:
    _ensure_ledger(connection)
    where, params = "", []
    if app:
        where, params = "WHERE app = ?", [app]
    return fetch_all(
        connection,
        f"SELECT * FROM suite_revenue {where} ORDER BY at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 5000))),
    )


# ---------------------------------------------------------------------------
# one customer record: the suite's contacts
# ---------------------------------------------------------------------------
#
# Every app that meets a customer (an order, a booking, a form, a case, a lead, tickets, a course) points its row at
# one contact with a nullable ``contact_id``: NULL means "not linked yet" (the backfill links it), '' means the row
# has no email or phone to link by. A contact is found by its normalized email or phone (``suite_contact_keys``,
# ``e:<email>`` and ``p:<last 10 digits>``), so the same person ordering in the Shop and booking in Bookings is one
# record with one timeline (aira.suite.customers).

CONTACTS_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS suite_contacts (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        phone TEXT NOT NULL DEFAULT '',
        company TEXT NOT NULL DEFAULT '',
        tags TEXT NOT NULL DEFAULT '[]',
        email_consent INTEGER,
        sms_consent INTEGER,
        consent_at TEXT,
        source_app TEXT NOT NULL DEFAULT '',
        apps TEXT NOT NULL DEFAULT '[]',
        notes TEXT NOT NULL DEFAULT '',
        first_seen TEXT NOT NULL,
        last_seen TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS suite_contacts_seen ON suite_contacts (last_seen)",
    """CREATE TABLE IF NOT EXISTS suite_contact_keys (
        key TEXT PRIMARY KEY,
        contact_id TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS suite_contact_keys_contact ON suite_contact_keys (contact_id)",
)


class ContactSource:
    """Where an app keeps a customer's details in one of its tables (column names; '' when it has none)."""

    __slots__ = ("app", "email", "phone", "name", "company", "at")

    def __init__(
        self,
        app: str,
        *,
        email: str = "",
        phone: str = "",
        name: str = "",
        company: str = "",
        at: str = "created_at",
    ) -> None:
        self.app, self.email, self.phone = app, email, phone
        self.name, self.company, self.at = name, company, at


# The tables that point at a contact. Their apps call contact_columns() in their own ensure().
CONTACT_SOURCES: dict[str, ContactSource] = {
    "sh_orders": ContactSource(
        "shop",
        email="customer_email",
        phone="customer_phone",
        name="customer_name",
        company="company",
    ),
    "bk_customers": ContactSource(
        "bookings", email="email", phone="phone", name="name", at="updated_at"
    ),
    "mk_submissions": ContactSource(
        "marketing", email="email", phone="phone", name="name", company="company"
    ),
    "mk_subscribers": ContactSource("marketing", email="email", name="name"),
    "sv_cases": ContactSource(
        "service",
        email="customer_email",
        phone="customer_phone",
        name="customer_name",
    ),
    "leads": ContactSource(
        "leads", email="email", phone="phone", name="name", company="company"
    ),
    "ev_orders": ContactSource(
        "events", email="buyer_email", phone="buyer_phone", name="buyer_name"
    ),
    "ac_enrolments": ContactSource("academy", email="email", name="name"),
}


def email_key(value: Any) -> str:
    """An email address as the contacts compare it (trimmed, lower case), or '' when it is not one."""
    text = str(value or "").strip().lower()
    return text if len(text) <= 254 and _EMAIL.match(text) else ""


def phone_key(value: Any) -> str:
    """A phone number as the contacts compare it: its digits, the last ten of a longer number, so +91 98765 43210,
    098765 43210 and 9876543210 are one person. '' for fewer than seven digits."""
    digits = re.sub(r"\D", "", str(value or ""))
    if len(digits) < 7 or len(digits) > 15:
        return ""
    return digits[-10:]


def ensure_contacts(connection: sqlite3.Connection) -> None:
    ensure_core(connection)
    ensure(connection, "suite.contacts.v1", CONTACTS_SCHEMA)


def contact_columns(connection: sqlite3.Connection, *tables: str) -> None:
    """Give these app tables their nullable ``contact_id`` (added safely to an older database) and link their rows
    that are not linked yet: the backfill, which each row goes through once. Apps call it from ``ensure()``."""
    ensure_contacts(connection)
    for table in tables:
        if table not in CONTACT_SOURCES:
            raise ValueError(f"{table} does not hold customers.")
        marker = (_db_file(connection), f"suite.contacts.{table}")
        if marker in _ensured:
            continue
        columns = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
        if not columns:
            continue  # the app has not made the table yet
        if "contact_id" not in columns:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN contact_id TEXT")
        connection.execute(
            f"CREATE INDEX IF NOT EXISTS {table}_contact ON {table} (contact_id)"
        )
        connection.commit()
        link_pending(connection, table)
        _ensured.add(marker)


def _has_contact_column(connection: sqlite3.Connection, table: str) -> bool:
    marker = (_db_file(connection), f"suite.contacts.{table}")
    if marker in _ensured:
        return True
    return any(
        row[1] == "contact_id"
        for row in connection.execute(f"PRAGMA table_info({table})")  # noqa: S608 - a CONTACT_SOURCES name
    )


def _contact_values(table: str, row: Mapping[str, Any]) -> dict[str, Any]:
    spec = CONTACT_SOURCES[table]
    out: dict[str, Any] = {
        "email": row.get(spec.email) if spec.email else "",
        "phone": row.get(spec.phone) if spec.phone else "",
        "name": row.get(spec.name) if spec.name else "",
        "company": row.get(spec.company) if spec.company else "",
        "source": spec.app,
        "at": str(row.get(spec.at) or ""),
    }
    if table == "leads" and not out["name"]:
        out["name"] = " ".join(
            str(part) for part in (row.get("first_name"), row.get("last_name")) if part
        )
    if table == "mk_subscribers":
        # double opt-in: consent is a confirmed subscription; an unsubscribe withdraws it; pending says nothing yet
        status = str(row.get("status") or "")
        out["email_consent"] = (
            True if status == "subscribed" else False if status == "unsubscribed" else None
        )
    return out


def link_row(connection: sqlite3.Connection, table: str, row_id: str) -> str:
    """Point one row of an app's table at its contact (made or updated from the row) and return the contact's id
    ('' when the row has no email or phone). Apps call it after they insert a customer's row."""
    if table not in CONTACT_SOURCES:
        raise ValueError(f"{table} does not hold customers.")
    if not _has_contact_column(connection, table):
        return ""
    row = fetch_one(connection, f"SELECT * FROM {table} WHERE id = ?", (row_id,))  # noqa: S608 - a known table
    if not row:
        return ""
    contact_id = contact_for(connection, **_contact_values(table, row))
    connection.execute(
        f"UPDATE {table} SET contact_id = ? WHERE id = ?",  # noqa: S608 - a known table
        (contact_id, row_id),
    )
    return contact_id


def link_pending(connection: sqlite3.Connection, table: str = "") -> int:
    """Link every row not linked yet (``contact_id IS NULL``), in one table or in all of them. Returns how many."""
    ensure_contacts(connection)
    linked = 0
    for name in [table] if table else list(CONTACT_SOURCES):
        if not _has_contact_column(connection, name):
            continue
        while True:
            rows = fetch_all(
                connection,
                f"SELECT * FROM {name} WHERE contact_id IS NULL LIMIT 500",  # noqa: S608 - a known table
            )
            if not rows:
                break
            for row in rows:
                contact_id = contact_for(connection, **_contact_values(name, row))
                connection.execute(
                    f"UPDATE {name} SET contact_id = ? WHERE id = ?",  # noqa: S608
                    (contact_id, row["id"]),
                )
                linked += 1
            connection.commit()
    return linked


def contact_for(
    connection: sqlite3.Connection,
    *,
    email: Any = "",
    phone: Any = "",
    name: Any = "",
    source: str = "",
    company: Any = "",
    tags: Iterable[str] | None = None,
    email_consent: bool | None = None,
    at: str = "",
) -> str:
    """The contact for this email or phone, made on first sight and updated after (blank details filled in, tags
    and apps added, last seen moved on). Returns its id, or '' when there is neither a valid email nor a phone.

    The email decides first. A phone alone matches a contact that has no other email, so two people who share a
    phone but give different emails stay two people."""
    ensure_contacts(connection)
    address, number = email_key(email), phone_key(phone)
    if not address and not number:
        return ""
    stamp = str(at or "") or now()
    person = _CONTROL.sub("", str(name or "")).strip()[:120]
    firm = _CONTROL.sub("", str(company or "")).strip()[:120]
    found: dict[str, Any] | None = None
    if address:
        found = fetch_one(
            connection,
            "SELECT c.* FROM suite_contact_keys k JOIN suite_contacts c ON c.id = k.contact_id WHERE k.key = ?",
            (f"e:{address}",),
        )
    if found is None and number:
        by_phone = fetch_one(
            connection,
            "SELECT c.* FROM suite_contact_keys k JOIN suite_contacts c ON c.id = k.contact_id WHERE k.key = ?",
            (f"p:{number}",),
        )
        if by_phone and (not address or not by_phone["email"]):
            found = by_phone
    extra_tags = [str(tag) for tag in (tags or []) if str(tag).strip()]
    if found is None:
        contact_id = new_id()
        connection.execute(
            """INSERT INTO suite_contacts (id, name, email, phone, company, tags, email_consent, consent_at, source_app,
            apps, first_seen, last_seen, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                contact_id,
                person,
                address,
                str(phone or "").strip()[:30] if number else "",
                firm,
                dumps(sorted(set(extra_tags))[:50]),
                None if email_consent is None else int(bool(email_consent)),
                stamp if email_consent is not None else None,
                source[:40],
                dumps([source] if source else []),
                stamp,
                stamp,
                now(),
                now(),
            ),
        )
    else:
        contact_id = str(found["id"])
        apps = loads(found["apps"], []) or []
        if source and source not in apps:
            apps.append(source)
        values: dict[str, Any] = {
            "name": found["name"] or person,
            "email": found["email"] or address,
            "phone": found["phone"] or (str(phone or "").strip()[:30] if number else ""),
            "company": found["company"] or firm,
            "tags": dumps(sorted(set(loads(found["tags"], []) or []) | set(extra_tags))[:50]),
            "apps": dumps(apps[:20]),
            "first_seen": min(str(found["first_seen"]), stamp),
            "last_seen": max(str(found["last_seen"]), stamp),
            "source_app": found["source_app"] or source[:40],
            "updated_at": now(),
        }
        if email_consent is not None and (
            found["email_consent"] is None or int(found["email_consent"]) != int(email_consent)
        ):
            values["email_consent"] = int(bool(email_consent))
            values["consent_at"] = stamp
        update_row(connection, "suite_contacts", contact_id, values)
    for key in ([f"e:{address}"] if address else []) + ([f"p:{number}"] if number else []):
        # a key another contact already has stays theirs (merge the two to join them)
        connection.execute(
            "INSERT OR IGNORE INTO suite_contact_keys (key, contact_id) VALUES (?, ?)",
            (key, contact_id),
        )
    return contact_id


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
    # Kinds sent without approval. Until the business picks them itself (``auto_send_chosen``), the transactional
    # kinds a customer is waiting for (AUTO_SEND_DEFAULT: an order confirmation, a ticket) are sent automatically too.
    "auto_send": [],
    "auto_send_chosen": False,
}
SMTP_PASSWORD_SECRET = "suite_smtp_password"
# Safety notices a person set up for themselves (a caregiver told about a missed dose) and notices to the business's
# own team (a new case, an overdue one): sent without waiting for approval, once the business has set up email.
ALWAYS_SEND = frozenset(
    {"missed_dose", "staff_new_case", "staff_new_chat", "staff_sla_breach"}
)
MAX_BODY = 20000  # the longest message the outbox keeps, footer included
# The kinds of message the apps prepare, as the Business hub names them (Email > Send without approval):
# (label, group, sent automatically unless the business turns it off).
MESSAGE_KINDS: dict[str, tuple[str, str, bool]] = {
    "order_confirmation": ("Shop: order confirmations", "transactional", True),
    "order_shipped": ("Shop: order shipped", "transactional", True),
    "return_update": ("Shop: returns and refunds", "transactional", True),
    "payment_receipt": ("Payment receipts", "transactional", True),
    "ticket": ("Events: tickets", "transactional", True),
    "booking_confirmation": ("Bookings: confirmations", "transactional", True),
    "booking_rescheduled": ("Bookings: a booking moved", "transactional", True),
    "booking_cancelled": ("Bookings: cancellations", "transactional", True),
    "enrolment": ("Courses: enrolments", "transactional", True),
    "case_received": ("Service: we received your request", "transactional", True),
    "newsletter_confirm": (
        "Marketing: confirm a newsletter sign-up",
        "transactional",
        True,
    ),
    "form_submission": ("Marketing: new enquiries (to you)", "transactional", True),
    "case_reply": ("Service: your replies to customers", "replies", False),
    "csat_request": ("Service: how did we do?", "replies", False),
    "invoice": ("Studio: invoices", "replies", False),
    "invoice_reminder": ("Studio: invoice reminders", "replies", False),
    "proposal": ("Studio: proposals", "replies", False),
    "payment_request": ("Payment requests", "replies", False),
    "lead_reply": ("Marketing: replies to leads", "replies", False),
    "booking_reminder": ("Bookings: reminders the day before", "reminders", False),
    "event_reminder": ("Events: reminders the day before", "reminders", False),
    "waitlist_offer": ("Events: tickets available again", "reminders", False),
    "waitlist_opening": ("Bookings: a time opened up", "reminders", False),
    "review_request": ("Bookings: review requests", "reminders", False),
    "post_reminder": ("Marketing: a post is due", "reminders", False),
    "newsletter": ("Marketing: newsletters", "marketing", False),
    "announcement": ("Courses: announcements", "marketing", False),
    "back_in_stock": ("Shop: back in stock", "marketing", False),
}
AUTO_SEND_DEFAULT = frozenset(
    kind for kind, (_label, _group, on) in MESSAGE_KINDS.items() if on
)
MARKETING_KINDS = frozenset(
    kind for kind, (_label, group, _on) in MESSAGE_KINDS.items() if group == "marketing"
)
# Messages that only make sense for a while: dropped when still unsent this long after they were due
# (a reminder for a booking that has started, a waitlist offer for seats that are gone).
EXPIRE_AFTER: dict[str, timedelta] = {
    "booking_reminder": timedelta(hours=23),
    "event_reminder": timedelta(hours=23),
    "waitlist_offer": timedelta(days=2),
    "waitlist_opening": timedelta(days=1),
    "review_request": timedelta(days=7),
    "post_reminder": timedelta(days=1),
    "newsletter_confirm": timedelta(days=7),
    "staff_new_chat": timedelta(hours=4),
    "staff_sla_breach": timedelta(days=1),
}
# A send that failed for a passing reason (the mail server did not answer) is tried again after these minutes,
# then marked failed.
RETRY_MINUTES = (5, 15, 60, 240)
MAX_ATTEMPTS = len(RETRY_MINUTES) + 1
OUTBOX_COLUMNS = (
    ("expires_at", "TEXT"),
    ("attempts", "INTEGER NOT NULL DEFAULT 0"),
    ("message_id", "TEXT NOT NULL DEFAULT ''"),  # the Message-ID it went out with (replies thread on it)
    ("unsubscribe_url", "TEXT NOT NULL DEFAULT ''"),  # bulk mail: the one-click unsubscribe address
)


# Files a message carries (a booking's calendar invite). Kept beside the outbox until the message is sent, then
# dropped: what was sent is in the recipient's mailbox, and the business keeps no second copy of it.
OUTBOX_FILES_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS suite_outbox_files (
        id TEXT PRIMARY KEY,
        message_id TEXT NOT NULL,
        filename TEXT NOT NULL,
        content_type TEXT NOT NULL,
        data BLOB NOT NULL,
        created_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS suite_outbox_files_message ON suite_outbox_files (message_id)",
)
MAX_ATTACHMENTS = 5
MAX_ATTACHMENT_BYTES = 1_000_000  # every file of one message together
_CONTENT_TYPE = re.compile(
    r"^[a-z]+/[a-z0-9.+-]{1,60}(?:; ?[a-z-]{1,30}=[A-Za-z0-9._-]{1,40}){0,4}$"
)
_FILENAME_BAD = re.compile(r"[^A-Za-z0-9._ -]")


def clean_attachments(attachments: Iterable[Any]) -> list[dict[str, Any]]:
    """``[{"filename", "content_type", "data"}]`` (or ``(filename, content_type, data)`` tuples) checked: at most
    :data:`MAX_ATTACHMENTS`, :data:`MAX_ATTACHMENT_BYTES` together, a plain file name and a MIME type."""
    out: list[dict[str, Any]] = []
    total = 0
    for item in attachments or ():
        if isinstance(item, Mapping):
            filename, content_type, data = (
                item.get("filename"),
                item.get("content_type"),
                item.get("data"),
            )
        else:
            filename, content_type, data = item
        if isinstance(data, str):
            data = data.encode("utf-8")
        if not isinstance(data, (bytes, bytearray)) or not data:
            raise ValueError("An attachment needs its contents.")
        name = _FILENAME_BAD.sub("_", str(filename or "").strip())[:100].strip(" .")
        if not name:
            raise ValueError("An attachment needs a file name.")
        main, sep, rest = str(content_type or "").strip().partition(";")
        kind = main.strip().lower() + (sep + rest if sep else "")
        if not _CONTENT_TYPE.match(kind):
            raise ValueError(f"{name} does not have a usable type.")
        total += len(data)
        out.append({"filename": name, "content_type": kind, "data": bytes(data)})
    if len(out) > MAX_ATTACHMENTS:
        raise ValueError(f"A message can carry at most {MAX_ATTACHMENTS} files.")
    if total > MAX_ATTACHMENT_BYTES:
        raise ValueError("The files are too large to send by email.")
    return out


def outbox_files(
    connection: sqlite3.Connection, message_id: str
) -> list[dict[str, Any]]:
    """A message's files, in the order they were added (with their bytes)."""
    ensure_outbox(connection)
    return fetch_all(
        connection,
        "SELECT filename, content_type, data FROM suite_outbox_files WHERE message_id = ? ORDER BY rowid",
        (message_id,),
    )


def _file_names(
    connection: sqlite3.Connection, message_ids: Sequence[str]
) -> dict[str, list[dict[str, Any]]]:
    names: dict[str, list[dict[str, Any]]] = {}
    for start in range(0, len(message_ids), 500):
        chunk = list(message_ids[start : start + 500])
        if not chunk:
            continue
        for row in connection.execute(
            f"SELECT message_id, filename, content_type, length(data) FROM suite_outbox_files WHERE message_id IN ({', '.join('?' for _ in chunk)}) ORDER BY rowid",  # noqa: S608
            chunk,
        ):
            names.setdefault(row[0], []).append(
                {"filename": row[1], "content_type": row[2], "size": int(row[3] or 0)}
            )
    return names


def drop_orphan_files(connection: sqlite3.Connection) -> int:
    """Files whose message is gone (an erasure removed it)."""
    if not connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'suite_outbox_files'"
    ).fetchone():
        return 0
    return int(
        connection.execute(
            "DELETE FROM suite_outbox_files WHERE message_id NOT IN (SELECT id FROM suite_outbox)"
        ).rowcount
        or 0
    )


def ensure_outbox(connection: sqlite3.Connection) -> None:
    """The shared tables plus the outbox columns added later (safe on any database, old or new)."""
    ensure_core(connection)
    ensure(connection, "suite.outbox.files.v1", OUTBOX_FILES_SCHEMA)
    marker = (_db_file(connection), "suite.outbox.v2")
    if marker in _ensured:
        return
    have = {row[1] for row in connection.execute("PRAGMA table_info(suite_outbox)")}
    for column, definition in OUTBOX_COLUMNS:
        if column in have:
            continue
        try:
            connection.execute(
                f"ALTER TABLE suite_outbox ADD COLUMN {column} {definition}"
            )
        except sqlite3.OperationalError as error:  # another connection added it first
            if "duplicate column" not in str(error).lower():
                raise
    connection.execute(
        "CREATE INDEX IF NOT EXISTS suite_outbox_message ON suite_outbox (message_id)"
    )
    connection.commit()
    _ensured.add(marker)


def email_settings(connection: sqlite3.Connection) -> dict[str, Any]:
    stored = get_setting(connection, "core", "email", {}) or {}
    return {
        **EMAIL_SETTINGS_DEFAULTS,
        **{k: v for k, v in stored.items() if k in EMAIL_SETTINGS_DEFAULTS},
    }


def auto_send_kinds(settings: Mapping[str, Any]) -> set[str]:
    """The kinds that go out without approval: the business's own choice, or (until it chose) its list plus the
    transactional defaults."""
    chosen = {str(kind) for kind in (settings.get("auto_send") or [])}
    return chosen if settings.get("auto_send_chosen") else chosen | AUTO_SEND_DEFAULT


def message_kinds(settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Every kind the hub offers as a checkbox (with whether it is on now), plus any other kind already chosen."""
    on = auto_send_kinds(settings)
    rows = [
        {"id": kind, "label": label, "group": group, "default": default, "on": kind in on}
        for kind, (label, group, default) in MESSAGE_KINDS.items()
    ]
    rows.extend(
        {"id": kind, "label": kind.replace("_", " ").capitalize(), "group": "other", "default": False, "on": True}
        for kind in sorted(on - set(MESSAGE_KINDS))
    )
    return rows


def _mail_ready(settings: Mapping[str, Any]) -> bool:
    return bool(settings.get("smtp_host") and settings.get("from_email"))


def lent_mail() -> Any:
    """The mail server lent to this database when it has none of its own (hosted: a member's own space sends
    through the organization's), or None. Here, in the single-user build, there is none."""
    from aira.hosting import HOOKS

    if HOOKS.business_mail is None:
        return None
    lent = HOOKS.business_mail()
    if lent is None or lent.unavailable or not _mail_ready(lent.settings):
        return None
    return lent


def outgoing_mail(
    connection: sqlite3.Connection, secret_store: Any
) -> tuple[dict[str, Any], str, bool]:
    """``(settings, password, lent)``: how customer email leaves. Business hub > Email of this database when it is
    set up (a sender address and a mail server); otherwise the lent server (``lent`` True); otherwise these
    incomplete settings, which :func:`send_outbox` turns into "Set up email first"."""
    settings = email_settings(connection)
    if _mail_ready(settings):
        password = (
            secret_store.get(connection, SMTP_PASSWORD_SECRET) if secret_store else ""
        )
        return settings, password or "", False
    lent = lent_mail()
    if lent is not None:
        return dict(lent.settings), lent.password, True
    return settings, "", False


def clean_email_settings(
    connection: sqlite3.Connection, raw: Mapping[str, Any]
) -> dict[str, Any]:
    """The settings as they would be saved (the stored ones with ``raw`` over them), checked; nothing is written."""
    merged = {
        **email_settings(connection),
        **{k: v for k, v in raw.items() if k in EMAIL_SETTINGS_DEFAULTS},
    }
    given = merged.get("auto_send") or []
    if isinstance(given, str):
        given = [part for part in given.split(",") if part.strip()]
    kinds: list[str] = []
    for kind in given if isinstance(given, list) else []:
        cleaned = text(kind, "a message kind", max_len=40)
        if cleaned and cleaned not in kinds:
            kinds.append(cleaned)
    return {
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
        "auto_send": kinds[:60],
        # sending "auto_send" is the business choosing; until then the transactional defaults apply
        "auto_send_chosen": bool(merged.get("auto_send_chosen")) or "auto_send" in raw,
    }


def save_email_settings(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    check_host: Callable[[str, int], Any] | None = None,
) -> dict[str, Any]:
    """Check, then save. ``check_host(host, port)`` (the hosted build refuses internal mail servers) runs before
    anything is written, so refused settings are never stored."""
    clean = clean_email_settings(connection, raw)
    if check_host is not None and clean["smtp_host"]:
        check_host(clean["smtp_host"], int(clean["smtp_port"]))
    set_setting(connection, "core", "email", clean)
    connection.commit()
    return clean


def expiry_of(message: Mapping[str, Any]) -> str | None:
    """When a message is dropped if still unsent: its own ``expires_at``, else (a kind in :data:`EXPIRE_AFTER`) that
    long after it is due. The default follows ``send_after``, so a reminder an app moves (an event rescheduled)
    expires with its new time."""
    if message.get("expires_at"):
        return str(message["expires_at"])
    lifetime = EXPIRE_AFTER.get(str(message.get("kind") or ""))
    if lifetime is None:
        return None
    start = message.get("send_after") or message.get("created_at") or now()
    return iso(parse_time(str(start)) + lifetime)


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
    expires_at: str = "",
    unsubscribe_url: str = "",
    attachments: Iterable[Any] = (),
) -> dict[str, Any] | None:
    """Prepare a message to a customer. It waits in the outbox for approval unless the business sends this kind
    automatically. Returns None when there is no address to send to.

    ``expires_at``: a time-bound message (a reminder) is dropped if it is still unsent then; kinds in
    :data:`EXPIRE_AFTER` get one by default. ``unsubscribe_url``: bulk mail (a newsletter) carries the RFC 8058
    one-click unsubscribe headers, and is checked against unsubscribes again when it is sent. ``attachments``:
    files it carries (:func:`clean_attachments`: a booking's ``.ics`` invite), kept until it is sent."""
    ensure_outbox(connection)
    recipient = email(to) if to else ""
    if not recipient:
        return None
    files = clean_attachments(attachments)
    automatic = kind in ALWAYS_SEND or kind in auto_send_kinds(
        email_settings(connection)
    )
    message: dict[str, Any] = {
        "id": new_id(),
        "app": app,
        "kind": kind[:40],
        "recipient": recipient,
        "subject": text(subject, "a subject", max_len=200) or "Message",
        "body": text(body, "a message", max_len=MAX_BODY, multiline=True),
        "ref_type": ref_type[:40],
        "ref_id": ref_id[:64],
        "status": "approved" if automatic else "pending",
        "send_after": send_after or None,
        "expires_at": moment(expires_at, "when it expires") or None,
        "unsubscribe_url": str(unsubscribe_url or "")[:500],
        "created_at": now(),
        "approved_at": now() if automatic else None,
    }
    connection.execute(
        """INSERT INTO suite_outbox (id, app, kind, recipient, subject, body, ref_type, ref_id, status, send_after,
        expires_at, unsubscribe_url, created_at, approved_at)
        VALUES (:id, :app, :kind, :recipient, :subject, :body, :ref_type, :ref_id, :status, :send_after,
        :expires_at, :unsubscribe_url, :created_at, :approved_at)""",
        message,
    )
    for file in files:
        connection.execute(
            "INSERT INTO suite_outbox_files (id, message_id, filename, content_type, data, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (
                new_id(),
                message["id"],
                file["filename"],
                file["content_type"],
                file["data"],
                message["created_at"],
            ),
        )
    if files:
        message["attachments"] = [
            {
                "filename": f["filename"],
                "content_type": f["content_type"],
                "size": len(f["data"]),
            }
            for f in files
        ]
    return message


def cancel_queued(
    connection: sqlite3.Connection,
    recipient: str,
    *,
    reason: str,
    app: str = "",
    kinds: Iterable[str] | None = None,
    bulk_only: bool = False,
) -> int:
    """Withdraw unsent messages to ``recipient`` (an unsubscribe, a bounce): the kinds given, or bulk mail only, or
    everything of ``app``. Returns how many were withdrawn."""
    ensure_outbox(connection)
    clauses = ["recipient = ?", "status IN ('pending', 'approved', 'failed')"]
    params: list[Any] = [str(recipient or "").strip().lower()]
    if app:
        clauses.append("app = ?")
        params.append(app)
    wanted = list(kinds or [])
    if wanted:
        clauses.append(f"kind IN ({', '.join('?' for _ in wanted)})")
        params.extend(wanted)
    if bulk_only:
        marks = ", ".join("?" for _ in MARKETING_KINDS)
        clauses.append(f"(unsubscribe_url <> '' OR kind IN ({marks}))")
        params.extend(sorted(MARKETING_KINDS))
    changed = connection.execute(
        f"UPDATE suite_outbox SET status = 'cancelled', error = ? WHERE {' AND '.join(clauses)}",  # noqa: S608
        (str(reason)[:300], *params),
    ).rowcount
    return int(changed or 0)


def expire_outbox(connection: sqlite3.Connection, at: str = "") -> int:
    """Drop time-bound messages still unsent after their expiry. Returns how many were dropped."""
    ensure_outbox(connection)
    stamp = at or now()
    drop = "UPDATE suite_outbox SET status = 'cancelled', error = 'Expired: it was not sent in time.' WHERE status IN ('pending', 'approved', 'failed') AND "
    changed = connection.execute(
        drop + "expires_at IS NOT NULL AND expires_at <= ?", (stamp,)
    ).rowcount
    for kind, lifetime in EXPIRE_AFTER.items():  # the defaults, counted from when each message is due
        changed += connection.execute(
            drop + "expires_at IS NULL AND kind = ? AND COALESCE(send_after, created_at) <= ?",
            (kind, iso(parse_time(stamp) - lifetime)),
        ).rowcount
    # the files of a message that went, was withdrawn or is gone are not needed any more
    dropped = connection.execute(
        """DELETE FROM suite_outbox_files WHERE NOT EXISTS (SELECT 1 FROM suite_outbox o
        WHERE o.id = suite_outbox_files.message_id AND o.status NOT IN ('sent', 'cancelled'))"""
    ).rowcount
    if changed or dropped:
        connection.commit()
    return int(changed or 0)


# Send-time checks an app adds (Marketing: the address unsubscribed): fn(connection, message) -> why it must not
# go, or "" when it may.
SuppressionFn = Callable[[sqlite3.Connection, Mapping[str, Any]], str]
SUPPRESSIONS: dict[str, SuppressionFn] = {}


def suppression(app: str) -> Callable[[SuppressionFn], SuppressionFn]:
    def wrap(fn: SuppressionFn) -> SuppressionFn:
        SUPPRESSIONS[app] = fn
        return fn

    return wrap


def bulk_mail(message: Mapping[str, Any]) -> bool:
    """Marketing mail (a newsletter), as opposed to a message a customer is waiting for."""
    return bool(message.get("unsubscribe_url")) or message.get("kind") in MARKETING_KINDS


def suppression_reason(connection: sqlite3.Connection, message: Mapping[str, Any]) -> str:
    """Why this message must not go now ('' when it may): the address is on the do-not-contact list (any entry for
    bulk mail; a bounce for any mail), or an app's own check (an unsubscribe) refuses it."""
    from aira.leads import mail as transport

    is_bulk = bulk_mail(message)
    recipient = str(message.get("recipient") or "")
    try:
        why = transport.do_not_contact(connection, recipient, bounces_only=not is_bulk)
    except sqlite3.Error:
        suite.logger.exception("The do-not-contact list could not be read")
        why = "The do-not-contact list could not be checked." if is_bulk else ""
    if why:
        return why
    for app, check in SUPPRESSIONS.items():
        try:
            why = check(connection, message)
        except Exception:  # noqa: BLE001 - bulk mail is held back when its check fails; a receipt still goes
            suite.logger.exception("The send-time check of %s failed", app)
            why = "The unsubscribe list could not be checked." if is_bulk else ""
        if why:
            return why
    return ""


# ---------------------------------------------------------------------------
# public forms: a bot check (a hidden field and a minimum time to fill the form in)
# ---------------------------------------------------------------------------

FORM_MIN_SECONDS = 3
FORM_TICKET_SECONDS = 24 * 3600


def _form_secret(connection: sqlite3.Connection) -> bytes:
    import secrets

    secret = get_setting(connection, "core", "form_secret", "")
    if not secret:
        secret = secrets.token_hex(32)
        set_setting(connection, "core", "form_secret", secret)
        connection.commit()
    return str(secret).encode()


def form_ticket(connection: sqlite3.Connection, at: datetime | None = None) -> str:
    """A signed stamp of when the page was shown, sent back with the form (``started``)."""
    import hashlib
    import hmac

    issued = str(int((at or utcnow()).timestamp()))
    signature = hmac.new(_form_secret(connection), issued.encode(), hashlib.sha256)
    return f"{issued}.{signature.hexdigest()[:32]}"


def check_form(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    at: datetime | None = None,
) -> bool:
    """Whether a public form was sent by a person. False for a bot (the hidden ``website`` field filled in, or no
    ticket from the page): the caller thanks it and keeps nothing. A person who was faster than
    :data:`FORM_MIN_SECONDS`, or whose page is a day old, gets a ValueError asking them to send again."""
    import hashlib
    import hmac

    if str(payload.get("website") or "").strip():
        return False
    issued, _, signature = str(payload.get("started") or "").partition(".")
    if not issued.isdigit() or not signature:
        return False
    expected = hmac.new(_form_secret(connection), issued.encode(), hashlib.sha256)
    if not hmac.compare_digest(expected.hexdigest()[:32], signature):
        return False
    age = (at or utcnow()).timestamp() - int(issued)
    if age > FORM_TICKET_SECONDS:
        raise ValueError("This page has been open a long time. Reload it and send again.")
    if age < FORM_MIN_SECONDS:
        raise ValueError("That was quick. Check your details, then send again.")
    return True


def outbox(
    connection: sqlite3.Connection, *, status: str = "", app: str = "", limit: int = 200
) -> list[dict[str, Any]]:
    ensure_outbox(connection)
    clauses, params = [], []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if app:
        clauses.append("app = ?")
        params.append(app)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = fetch_all(
        connection,
        f"SELECT * FROM suite_outbox {where} ORDER BY created_at DESC LIMIT ?",  # noqa: S608
        (*params, max(1, min(limit, 1000))),
    )
    files = _file_names(connection, [row["id"] for row in rows])
    for row in rows:
        row["attachments"] = files.get(row["id"], [])
    return rows


def outbox_counts(connection: sqlite3.Connection) -> dict[str, int]:
    ensure_outbox(connection)
    counts = {status: 0 for status in OUTBOX_STATUSES}
    for row in connection.execute(
        "SELECT status, COUNT(*) FROM suite_outbox GROUP BY status"
    ):
        counts[row[0]] = int(row[1])
    return counts


def set_outbox_status(
    connection: sqlite3.Connection, ids: Sequence[str], status: str
) -> int:
    ensure_outbox(connection)
    if status not in ("approved", "cancelled"):
        raise ValueError("A message can be approved or cancelled.")
    expire_outbox(connection)  # an expired reminder is not approved any more
    changed = 0
    for message_id in ids[:500]:
        changed += connection.execute(
            "UPDATE suite_outbox SET status = ?, approved_at = CASE WHEN ? = 'approved' THEN ? ELSE approved_at END, "
            "attempts = CASE WHEN ? = 'approved' THEN 0 ELSE attempts END WHERE id = ? AND status IN ('pending', 'failed', 'approved')",
            (status, status, now(), status, str(message_id)),
        ).rowcount
    connection.commit()
    return changed


def edit_outbox(
    connection: sqlite3.Connection, message_id: str, raw: Mapping[str, Any]
) -> dict[str, Any]:
    ensure_outbox(connection)
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
            raw["body"], "a message", max_len=MAX_BODY, required=True, multiline=True
        )
    if "recipient" in raw:
        values["recipient"] = email(raw["recipient"], required=True)
    update_row(connection, "suite_outbox", message_id, values)
    connection.commit()
    return require_row(connection, "suite_outbox", message_id, "message")


def _transactional(
    settings: Mapping[str, Any],
    profile: Mapping[str, Any],
    message: Mapping[str, Any],
    files: Sequence[Mapping[str, Any]] = (),
) -> Any:
    from email.message import EmailMessage
    from email.utils import formataddr, formatdate, make_msgid

    sender = str(settings["from_email"])
    reply_to = str(settings.get("reply_to") or sender)
    mail = EmailMessage()
    mail["From"] = formataddr(
        (str(settings.get("from_name") or profile.get("name") or ""), sender)
    )
    mail["To"] = str(message["recipient"])
    mail["Subject"] = str(message["subject"])
    mail["Reply-To"] = reply_to
    mail["Date"] = formatdate(localtime=False)
    mail["Message-ID"] = make_msgid(domain=sender.split("@")[-1] or "localhost")
    mail["X-Mailer"] = "Monosage"
    unsubscribe = str(message.get("unsubscribe_url") or "")
    if unsubscribe:
        # RFC 2369 and RFC 8058: a mail client offers "Unsubscribe", and with an https address it unsubscribes with
        # one POST (List-Unsubscribe=One-Click), without opening a page. The mailto: also works when replies are
        # read (Business hub > Email > Incoming mail).
        mailto = f"<mailto:{reply_to}?subject=unsubscribe>"
        if unsubscribe.lower().startswith("https://"):
            mail["List-Unsubscribe"] = f"<{unsubscribe}>, {mailto}"
            mail["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
        else:  # a local address no mail provider can reach: the mailto only
            mail["List-Unsubscribe"] = mailto
    footer_bits = [
        str(profile.get(k) or "")
        for k in ("name", "address", "phone")
        if profile.get(k)
    ]
    footer = ("\n\n--\n" + "\n".join(footer_bits)) if footer_bits else ""
    mail.set_content(str(message["body"]).replace("\r\n", "\n").rstrip() + footer)
    for file in files:
        kind, _sep, rest = str(file["content_type"]).partition(";")
        maintype, _slash, subtype = kind.strip().partition("/")
        params = {}
        for part in rest.split(";"):
            key, eq, value = part.strip().partition("=")
            if eq and key:
                params[key.strip()] = value.strip()
        mail.add_attachment(
            bytes(file["data"]),
            maintype=maintype,
            subtype=subtype,
            filename=str(file["filename"]),
            params=params or None,
        )
    return mail


def _temporary(error: BaseException) -> bool:
    """A failure worth trying again later (the mail server did not answer, or said "try later")."""
    import smtplib

    if getattr(error, "temporary", False):
        return True
    if isinstance(error, smtplib.SMTPResponseException):
        return 400 <= int(error.smtp_code) < 500
    return isinstance(error, (smtplib.SMTPServerDisconnected, ConnectionError, TimeoutError))


def _stops_the_pass(error: BaseException) -> bool:
    """The same failure would repeat for every message (a wrong password, a refused mail server)."""
    words = str(error)
    return "refused the username or password" in words or "cannot be used here" in words


def send_outbox(
    connection: sqlite3.Connection,
    password: str,
    *,
    limit: int = 25,
    sender: Callable[..., None] | None = None,
    ids: Sequence[str] | None = None,
    mail_settings: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Send approved messages whose time has come, through the business's own mail server.

    Before each message: an expired one is dropped, and one whose address unsubscribed or is on the do-not-contact
    list since it was queued is cancelled (``skipped``). A send that fails for a passing reason is tried again
    later (``retrying``, after :data:`RETRY_MINUTES`), up to :data:`MAX_ATTEMPTS`; then it is ``failed``.

    ``ids`` limits the pass to those messages (an app sending what the person just approved). ``mail_settings``
    replaces this database's Business hub > Email (a hosted member sending through the organization's)."""
    from aira.leads import mail as transport

    ensure_outbox(connection)
    settings = (
        dict(mail_settings) if mail_settings is not None else email_settings(connection)
    )
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
    expired = expire_outbox(connection)
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
    result: dict[str, Any] = {
        "sent": 0,
        "failed": 0,
        "retrying": 0,
        "skipped": 0,
        "expired": expired,
        "details": [],
    }
    for message in due:
        why = suppression_reason(connection, message)
        if why:
            connection.execute(
                "UPDATE suite_outbox SET status = 'cancelled', error = ? WHERE id = ?",
                (why[:300], message["id"]),
            )
            connection.commit()
            result["skipped"] += 1
            result["details"].append(
                {"id": message["id"], "status": "cancelled", "error": why[:300]}
            )
            continue
        try:
            built = _transactional(
                settings, profile, message, outbox_files(connection, message["id"])
            )
            send(settings, password, built)
        except Exception as error:  # noqa: BLE001 - one failure must not stop the rest
            attempts = int(message.get("attempts") or 0) + 1
            reason = str(error)[:300]
            if _temporary(error) and attempts < MAX_ATTEMPTS:
                retry_at = iso(utcnow() + timedelta(minutes=RETRY_MINUTES[attempts - 1]))
                connection.execute(
                    "UPDATE suite_outbox SET attempts = ?, send_after = ?, error = ? WHERE id = ?",
                    (attempts, retry_at, reason, message["id"]),
                )
                result["retrying"] += 1
                result["details"].append(
                    {"id": message["id"], "status": "retrying", "error": reason, "retry_at": retry_at}
                )
            else:
                if getattr(error, "bounced", False):
                    # refused for good: on the do-not-contact list, and the rest queued for it is withdrawn
                    transport.record_bounce(connection, message["recipient"], reason)
                connection.execute(
                    "UPDATE suite_outbox SET status = 'failed', attempts = ?, error = ? WHERE id = ?",
                    (attempts, reason, message["id"]),
                )
                result["failed"] += 1
                result["details"].append(
                    {"id": message["id"], "status": "failed", "error": reason}
                )
            connection.commit()
            if _stops_the_pass(error):
                break
        else:
            connection.execute(
                "UPDATE suite_outbox SET status = 'sent', sent_at = ?, error = '', attempts = ?, message_id = ? WHERE id = ?",
                (
                    now(),
                    int(message.get("attempts") or 0) + 1,
                    str(built["Message-ID"] or "")[:300],
                    message["id"],
                ),
            )
            connection.execute(
                "DELETE FROM suite_outbox_files WHERE message_id = ?", (message["id"],)
            )
            result["sent"] += 1
            result["details"].append({"id": message["id"], "status": "sent"})
            connection.commit()
    return result


def sent_message(connection: sqlite3.Connection, message_ids: Iterable[str]) -> dict[str, Any] | None:
    """The outbox message one of these Message-IDs belongs to (a reply's In-Reply-To or References), or None."""
    ensure_outbox(connection)
    for message_id in message_ids:
        cleaned = str(message_id or "").strip()
        if not cleaned:
            continue
        found = fetch_one(
            connection,
            "SELECT * FROM suite_outbox WHERE message_id = ? AND status = 'sent' ORDER BY sent_at DESC",
            (cleaned,),
        )
        if found:
            return found
    return None


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
    build's configured public address wins over both (it is https behind a proxy), and so does the single-user
    build's public address (Business hub > Business profile: the business's own tunnel to this computer)."""
    origin = suite.public_origin()
    if not origin and _address_source is not None:
        try:
            origin = str(_address_source() or "").rstrip("/")
        except Exception:  # noqa: BLE001 - a link falls back to the request's address
            suite.logger.debug("No public address for this account", exc_info=True)
            origin = ""
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
