"""Workday candidate accounts: one per employer tenant, the password encrypted at rest.

Workday gives every employer its own tenant (``acme.wd5.myworkdayjobs.com``) and every tenant its own
candidate accounts. The person either saves the email and password of an account they already have, or
asks Monosage to create the account for them: the profile email and a generated strong password,
saved here before the account form is ever filled so it is never lost. A preference turns that on for
every new tenant automatically.

Passwords are encrypted with the same Fernet secret store as the SerpApi key
(:class:`aira.airaapi.store.SecretStore`, the installation's ``airaapi.key`` or the hosted key). They
are decrypted only for a run (:func:`credentials_for_run`), never returned by the API, never logged and
never part of an audit entry.

Routes (session auth, owner-only like the rest of auto-apply, same-origin checked by the server):

* ``GET    /api/autopilot/workday-accounts``          list (masked) and the preference
* ``POST   /api/autopilot/workday-accounts``          add ``{host, email, password}`` or ``{host, create_for_me: true}``
* ``PATCH  /api/autopilot/workday-accounts/<host>``   update the email and/or password, or regenerate
* ``DELETE /api/autopilot/workday-accounts/<host>``   remove
* ``POST   /api/autopilot/workday-settings``          ``{auto_create: bool}``
"""

from __future__ import annotations

import logging
import re
import secrets
import sqlite3
import string
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from typing import Any

from aira.airaapi.http import Request, aira_route
from aira.airaapi.store import SecretStore
from aira.workday import ACCOUNT_STATES, WorkdayAccount, is_workday_url, tenant_host

__all__ = [
    "credentials_for_run",
    "delete_account",
    "ensure_schema",
    "generate_password",
    "list_accounts",
    "load_credentials",
    "mask_email",
    "normalize_host",
    "preferences",
    "record_result",
    "save_account",
    "set_preferences",
]

logger = logging.getLogger(__name__)

ORIGINS = ("person", "generated")
STATE_LABELS = {
    "new": "Not used yet",
    "active": "Working",
    "verification_needed": "Verify the email Workday sent",
    "sign_in_failed": "Sign-in failed: update the password",
    "exists": "An account already exists: save its password",
}
_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
_HOST_RE = re.compile(
    r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
_WORKDAY_SUFFIXES = (".myworkdayjobs.com", ".myworkdaysite.com", ".myworkday.com")
_SPECIALS = "!@#$%^&*-_=+?"
MAX_PASSWORD = 256


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the Workday tables. Additive and idempotent."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS workday_accounts (
            host TEXT PRIMARY KEY,
            email TEXT NOT NULL,
            password_ciphertext BLOB NOT NULL,
            origin TEXT NOT NULL CHECK (origin IN ('person', 'generated')),
            state TEXT NOT NULL DEFAULT 'new',
            message TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            last_used_at TEXT
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS workday_preferences (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            auto_create INTEGER NOT NULL DEFAULT 0,
            updated_at TEXT NOT NULL
        )"""
    )


# --------------------------------------------------------------------------- #
# Values
# --------------------------------------------------------------------------- #


def normalize_host(value: Any) -> str:
    """A Workday tenant host from a host name or any URL on it; ``ValueError`` otherwise."""
    text = str(value or "").strip()
    if not text:
        raise ValueError(
            "Enter the Workday site, for example acme.wd5.myworkdayjobs.com."
        )
    host = tenant_host(text if "://" in text else f"https://{text}")
    if not _HOST_RE.fullmatch(host):
        raise ValueError("That is not a valid Workday site address.")
    if not host.endswith(_WORKDAY_SUFFIXES):
        raise ValueError(
            "Workday sites end in myworkdayjobs.com or myworkdaysite.com, for example "
            "acme.wd5.myworkdayjobs.com."
        )
    return host


def _clean_email(value: Any) -> str:
    email = str(value or "").strip()
    if not _EMAIL_RE.fullmatch(email) or len(email) > 254:
        raise ValueError("Enter a valid email address for the Workday account.")
    return email


def _clean_password(value: Any) -> str:
    password = str(value or "")
    if not password.strip():
        raise ValueError("Enter the Workday account's password.")
    if len(password) > MAX_PASSWORD:
        raise ValueError(f"The password must be {MAX_PASSWORD} characters or fewer.")
    return password


def generate_password(length: int = 20) -> str:
    """A strong random password that meets Workday's rules (upper, lower, digit, special)."""
    length = max(12, length)
    pools = (string.ascii_uppercase, string.ascii_lowercase, string.digits, _SPECIALS)
    chars = [secrets.choice(pool) for pool in pools]
    everything = "".join(pools)
    chars += [secrets.choice(everything) for _ in range(length - len(chars))]
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)


def mask_email(email: str) -> str:
    """``hy•••@example.com``: enough to tell accounts apart, not the whole address."""
    local, _, domain = str(email or "").partition("@")
    if not domain:
        return "•••"
    return f"{local[:2]}{'•' * max(3, len(local) - 2)}@{domain}"


def _public(row: sqlite3.Row) -> dict[str, Any]:
    state = str(row["state"])
    return {
        "host": row["host"],
        "email": mask_email(str(row["email"])),
        "origin": row["origin"],
        "has_password": True,
        "state": state,
        "state_label": STATE_LABELS.get(state, state),
        "message": row["message"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "last_used_at": row["last_used_at"],
    }


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


def _row(connection: sqlite3.Connection, host: str) -> sqlite3.Row | None:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    return connection.execute(
        "SELECT * FROM workday_accounts WHERE host = ?", (host,)
    ).fetchone()


def list_accounts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(connection)
    connection.row_factory = sqlite3.Row
    rows = connection.execute("SELECT * FROM workday_accounts ORDER BY host").fetchall()
    return [_public(row) for row in rows]


def preferences(connection: sqlite3.Connection) -> dict[str, Any]:
    ensure_schema(connection)
    row = connection.execute(
        "SELECT auto_create FROM workday_preferences WHERE id = 1"
    ).fetchone()
    return {"auto_create": bool(row[0]) if row else False}


def set_preferences(
    connection: sqlite3.Connection, *, auto_create: bool
) -> dict[str, Any]:
    ensure_schema(connection)
    connection.execute(
        "INSERT INTO workday_preferences (id, auto_create, updated_at) VALUES (1, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET auto_create = excluded.auto_create, "
        "updated_at = excluded.updated_at",
        (1 if auto_create else 0, _now()),
    )
    return preferences(connection)


def save_account(
    connection: sqlite3.Connection,
    store: SecretStore,
    *,
    host: str,
    email: str,
    password: str | None = None,
    create_for_me: bool = False,
) -> dict[str, Any]:
    """Add or replace the account for ``host``; ``create_for_me`` generates the password."""
    email = _clean_email(email)
    origin = "generated" if create_for_me else "person"
    secret = generate_password() if create_for_me else _clean_password(password)
    now = _now()
    ensure_schema(connection)
    connection.execute(
        "INSERT INTO workday_accounts (host, email, password_ciphertext, origin, state, message, "
        "created_at, updated_at) VALUES (?, ?, ?, ?, 'new', '', ?, ?) "
        "ON CONFLICT(host) DO UPDATE SET email = excluded.email, "
        "password_ciphertext = excluded.password_ciphertext, origin = excluded.origin, "
        "state = 'new', message = '', updated_at = excluded.updated_at",
        (host, email, store.encrypt(secret), origin, now, now),
    )
    row = _row(connection, host)
    assert row is not None
    return _public(row)


def update_account(
    connection: sqlite3.Connection,
    store: SecretStore,
    host: str,
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Change the email and/or password (``create_for_me`` generates a fresh one)."""
    row = _row(connection, host)
    if row is None:
        raise LookupError(f"No Workday account is saved for {host}.")
    email = (
        _clean_email(payload["email"]) if payload.get("email") else str(row["email"])
    )
    if payload.get("create_for_me"):
        return save_account(
            connection, store, host=host, email=email, create_for_me=True
        )
    if payload.get("password"):
        return save_account(
            connection, store, host=host, email=email, password=payload["password"]
        )
    connection.execute(
        "UPDATE workday_accounts SET email = ?, updated_at = ? WHERE host = ?",
        (email, _now(), host),
    )
    updated = _row(connection, host)
    assert updated is not None
    return _public(updated)


def delete_account(connection: sqlite3.Connection, host: str) -> None:
    ensure_schema(connection)
    cursor = connection.execute("DELETE FROM workday_accounts WHERE host = ?", (host,))
    if not cursor.rowcount:
        raise LookupError(f"No Workday account is saved for {host}.")


def load_credentials(
    connection: sqlite3.Connection, store: SecretStore, host: str
) -> dict[str, Any] | None:
    """The decrypted account for a run (internal: never send this to a page)."""
    row = _row(connection, host)
    if row is None:
        return None
    return {
        "host": row["host"],
        "email": row["email"],
        "password": store.decrypt(row["password_ciphertext"]),
        "origin": row["origin"],
        "state": row["state"],
    }


def record_result(
    connection: sqlite3.Connection, host: str, state: str, message: str = ""
) -> None:
    """Remember what the last run found out about the account."""
    if state not in ACCOUNT_STATES:
        raise ValueError(f"Unknown Workday account state {state!r}.")
    now = _now()
    ensure_schema(connection)
    connection.execute(
        "UPDATE workday_accounts SET state = ?, message = ?, last_used_at = ?, updated_at = ? "
        "WHERE host = ?",
        (state, str(message or "")[:300], now, now, host),
    )


def credentials_for_run(
    connect: Callable[[], sqlite3.Connection],
    store: SecretStore | Callable[[], SecretStore],
    url: str,
    *,
    source: str = "",
    profile_email: str = "",
) -> WorkdayAccount | None:
    """The account for the Workday tenant behind ``url`` (``None`` for other sites or no account).

    With the "create accounts for me" preference on and no account saved for the tenant, an account
    with the profile email and a generated password is saved first, so the password is never lost.
    """
    if not (is_workday_url(url) or str(source or "").strip().casefold() == "workday"):
        return None
    host = tenant_host(url)
    if not host:
        return None
    try:
        secret_store = store if isinstance(store, SecretStore) else store()
        with connect() as connection:
            saved = load_credentials(connection, secret_store, host)
            if saved is None and preferences(connection)["auto_create"]:
                email = str(profile_email or "").strip()
                if not _EMAIL_RE.fullmatch(email):
                    return None
                save_account(
                    connection, secret_store, host=host, email=email, create_for_me=True
                )
                saved = load_credentials(connection, secret_store, host)
                logger.info("Saved a generated Workday account for %s", host)
    except Exception:  # noqa: BLE001 - a broken store means "no account", never a crash
        logger.warning(
            "Could not load the Workday account for %s", host, exc_info=False
        )
        return None
    if saved is None:
        return None

    def on_result(state: str, message: str) -> None:
        with connect() as connection:
            record_result(connection, host, state, message)

    return WorkdayAccount(
        host=host,
        email=str(saved["email"]),
        password=str(saved["password"]),
        create=saved["origin"] == "generated" and saved["state"] == "new",
        on_result=on_result,
    )


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #

_HOST_PATH = r"([a-z0-9.-]{4,253})"


def _profile_email(connection: sqlite3.Connection) -> str:
    """The application profile's email (``settings.application_email``)."""
    try:
        row = connection.execute(
            "SELECT value FROM settings WHERE key = 'application_email'"
        ).fetchone()
    except sqlite3.Error:
        return ""
    return str(row[0] or "").strip() if row else ""


def _flag(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value in (0, 1):
        return bool(value)
    raise ValueError(f"{name} must be true or false.")


@aira_route("GET", r"/api/autopilot/workday-accounts")
def accounts_list(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, {
            "accounts": list_accounts(connection),
            **preferences(connection),
        }


@aira_route("POST", r"/api/autopilot/workday-accounts")
def accounts_add(request: Request) -> tuple[int, dict[str, Any]]:
    payload = request.payload
    host = normalize_host(payload.get("host"))
    create = _flag(payload.get("create_for_me", False), "create_for_me")
    email = payload.get("email")
    with request.connect() as connection:
        if _row(connection, host) is not None:
            raise ValueError(
                f"An account for {host} is saved already. Update or remove it."
            )
        if create and not email:
            email = payload.get("profile_email") or _profile_email(connection)
        account = save_account(
            connection,
            request.secret_store,
            host=host,
            email=str(email or ""),
            password=None if create else payload.get("password"),
            create_for_me=create,
        )
    request.audit(
        "autopilot.workday_account.add",
        "workday_account",
        host,
        {"origin": account["origin"]},
    )
    return 201, {"account": account}


@aira_route("PATCH", rf"/api/autopilot/workday-accounts/{_HOST_PATH}")
def accounts_update(request: Request) -> tuple[int, dict[str, Any]]:
    host = normalize_host(request.match.group(1))
    payload = request.payload
    if not any(payload.get(key) for key in ("email", "password", "create_for_me")):
        raise ValueError("Send a new email, a new password, or create_for_me.")
    if "create_for_me" in payload:
        _flag(payload["create_for_me"], "create_for_me")
    with request.connect() as connection:
        account = update_account(connection, request.secret_store, host, payload)
    request.audit(
        "autopilot.workday_account.update",
        "workday_account",
        host,
        {
            "fields": sorted(
                key
                for key in ("email", "password", "create_for_me")
                if payload.get(key)
            )
        },
    )
    return 200, {"account": account}


@aira_route("DELETE", rf"/api/autopilot/workday-accounts/{_HOST_PATH}")
def accounts_delete(request: Request) -> tuple[int, dict[str, Any]]:
    host = normalize_host(request.match.group(1))
    with request.connect() as connection:
        delete_account(connection, host)
    request.audit("autopilot.workday_account.delete", "workday_account", host, {})
    return 200, {"ok": True}


@aira_route("POST", r"/api/autopilot/workday-settings")
def accounts_settings(request: Request) -> tuple[int, dict[str, Any]]:
    if "auto_create" not in request.payload:
        raise ValueError("Send auto_create: true or false.")
    auto_create = _flag(request.payload["auto_create"], "auto_create")
    with request.connect() as connection:
        result = set_preferences(connection, auto_create=auto_create)
    request.audit("autopilot.workday_settings", "workday_settings", "", result)
    return 200, result
