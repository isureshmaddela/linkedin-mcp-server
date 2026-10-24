"""Storage for Aira Leads: lists, leads, de-duplication keys, saved searches, run history and the ICP."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

STATUSES = ("new", "contacted", "replied", "qualified", "disqualified", "customer")
KINDS = (
    "person",
    "company",
    "request",
)  # a request is a public post asking for a service
SOURCES = (
    "google_maps",
    "google_web",
    "google_jobs",
    "google_intent",
    "sales_navigator",
    "import",
    "manual",
)
MAX_LEADS_PER_LIST = 20_000
MAX_LISTS = 100
MAX_SEARCHES = 50
MAX_RUNS_KEPT = 300
DEFAULT_LIST_NAME = "My leads"
ICP_KEY = "leads_icp"

_SPACE = re.compile(r"\s+")
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def new_id() -> str:
    return uuid4().hex


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def clean(value: Any, limit: int = 300) -> str:
    """Collapse whitespace, drop control characters and cap the length."""
    text = _CONTROL.sub("", str(value if value is not None else ""))
    return _SPACE.sub(" ", text).strip()[:limit]


# ---------------------------------------------------------------------------
# schema
# ---------------------------------------------------------------------------


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the Leads tables. Additive and idempotent."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS lead_lists (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, description TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS leads (
            id TEXT PRIMARY KEY, list_id TEXT NOT NULL,
            kind TEXT NOT NULL DEFAULT 'company',
            name TEXT NOT NULL DEFAULT '', first_name TEXT NOT NULL DEFAULT '', last_name TEXT NOT NULL DEFAULT '',
            title TEXT NOT NULL DEFAULT '', company TEXT NOT NULL DEFAULT '',
            company_domain TEXT NOT NULL DEFAULT '', industry TEXT NOT NULL DEFAULT '',
            location TEXT NOT NULL DEFAULT '', website TEXT NOT NULL DEFAULT '',
            email TEXT NOT NULL DEFAULT '', phone TEXT NOT NULL DEFAULT '',
            linkedin_url TEXT NOT NULL DEFAULT '', sales_nav_url TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'manual', source_ref TEXT NOT NULL DEFAULT '',
            rating REAL, reviews INTEGER,
            signals TEXT NOT NULL DEFAULT '[]',
            score INTEGER, score_reasons TEXT NOT NULL DEFAULT '[]',
            status TEXT NOT NULL DEFAULT 'new', notes TEXT NOT NULL DEFAULT '',
            tags TEXT NOT NULL DEFAULT '[]',
            enrichment TEXT NOT NULL DEFAULT '{}', enriched_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_list ON leads(list_id, updated_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_leads_status ON leads(status, updated_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS lead_keys (
            key TEXT PRIMARY KEY, lead_id TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_keys_lead ON lead_keys(lead_id)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS lead_searches (
            id TEXT PRIMARY KEY, name TEXT NOT NULL, source TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}', list_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, last_run_at TEXT NOT NULL DEFAULT '',
            last_found INTEGER NOT NULL DEFAULT 0, last_added INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS lead_runs (
            id TEXT PRIMARY KEY, search_id TEXT NOT NULL DEFAULT '', source TEXT NOT NULL,
            params TEXT NOT NULL DEFAULT '{}', found INTEGER NOT NULL DEFAULT 0,
            added INTEGER NOT NULL DEFAULT 0, duplicates INTEGER NOT NULL DEFAULT 0,
            credits INTEGER NOT NULL DEFAULT 0, cache_hit INTEGER NOT NULL DEFAULT 0,
            record_id TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'ok',
            error TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_runs_created ON lead_runs(created_at)"
    )
    connection.execute(
        "CREATE TABLE IF NOT EXISTS lead_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS lead_messages (
            id TEXT PRIMARY KEY, lead_id TEXT NOT NULL, channel TEXT NOT NULL DEFAULT 'email',
            to_address TEXT NOT NULL DEFAULT '', subject TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'draft', error TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT 'template', campaign TEXT NOT NULL DEFAULT '',
            message_id TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL, approved_at TEXT NOT NULL DEFAULT '', sent_at TEXT NOT NULL DEFAULT ''
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_messages_status ON lead_messages(status, created_at)"
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_messages_lead ON lead_messages(lead_id, created_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS lead_suppressions (
            key TEXT PRIMARY KEY, kind TEXT NOT NULL, value TEXT NOT NULL,
            reason TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS lead_calls (
            id TEXT PRIMARY KEY, lead_id TEXT NOT NULL, outcome TEXT NOT NULL,
            notes TEXT NOT NULL DEFAULT '', follow_up_at TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_lead_calls_lead ON lead_calls(lead_id, created_at)"
    )


# ---------------------------------------------------------------------------
# normalisation and de-duplication keys
# ---------------------------------------------------------------------------

_LI_PROFILE = re.compile(r"linkedin\.com/(?:[a-z]{2,3}/)?in/([^/?#\s]+)", re.I)
_LI_COMPANY = re.compile(r"linkedin\.com/company/([^/?#\s]+)", re.I)
_SALES_LEAD = re.compile(r"linkedin\.com/sales/(?:lead|people)/([^,/?#\s]+)", re.I)
_WWW = re.compile(r"^www\d*\.", re.I)


def domain_of(url: str) -> str:
    """The registrable-looking host of a URL ('' when it has none), without ``www.``."""
    text = str(url or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    try:
        host = (urlsplit(text).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""
    return _WWW.sub("", host)


def canonical_linkedin(url: str) -> str:
    """``https://www.linkedin.com/in/<slug>`` or ``/company/<slug>``; anything else is ''."""
    text = str(url or "")
    found = _LI_PROFILE.search(text)
    if found:
        return f"https://www.linkedin.com/in/{found.group(1).strip('/').lower()}"
    found = _LI_COMPANY.search(text)
    if found:
        return f"https://www.linkedin.com/company/{found.group(1).strip('/').lower()}"
    return ""


def _plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.casefold()).strip()


def dedupe_keys(lead: Mapping[str, Any]) -> list[str]:
    """Every identity a lead can be recognised by, strongest first."""
    keys: list[str] = []
    linkedin = canonical_linkedin(str(lead.get("linkedin_url") or ""))
    if linkedin:
        keys.append(f"li:{linkedin}")
    sales = _SALES_LEAD.search(str(lead.get("sales_nav_url") or ""))
    if sales:
        keys.append(f"sn:{sales.group(1).lower()}")
    if lead.get("source_ref") and lead.get("source") in {"google_maps"}:
        keys.append(f"place:{str(lead['source_ref']).lower()}")
    kind = str(lead.get("kind") or "company")
    if kind == "request":
        ref = str(lead.get("source_ref") or "").strip().lower()
        return [f"req:{ref}"] if ref else [f"req:{_plain(str(lead.get('name') or ''))}"]
    domain = domain_of(str(lead.get("website") or lead.get("company_domain") or ""))
    # Locations of one business (and chains) share a website, so a Maps lead is identified by its place instead.
    if kind == "company" and domain and lead.get("source") != "google_maps":
        keys.append(f"domain:{domain}")
    name = _plain(str(lead.get("name") or ""))
    company = _plain(str(lead.get("company") or ""))
    if kind == "person" and name:
        keys.append(f"person:{name}|{company}")
    elif kind == "company" and name:
        keys.append(f"company:{name}|{_plain(str(lead.get('location') or ''))[:40]}")
    return keys


def _json(value: Any, default: Any) -> str:
    return json.dumps(value if value is not None else default, ensure_ascii=False)


def _loads(text: Any, default: Any) -> Any:
    try:
        value = json.loads(text) if text else default
    except (TypeError, ValueError):
        return default
    return value if isinstance(value, type(default)) else default


def _row(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    data = dict(row)
    data["signals"] = _loads(data.get("signals"), [])
    data["score_reasons"] = _loads(data.get("score_reasons"), [])
    data["tags"] = _loads(data.get("tags"), [])
    data["enrichment"] = _loads(data.get("enrichment"), {})
    return data


# ---------------------------------------------------------------------------
# lists
# ---------------------------------------------------------------------------


def _count(connection: sqlite3.Connection, sql: str, args: Sequence[Any] = ()) -> int:
    return int(connection.execute(sql, args).fetchone()[0])


def list_lists(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """SELECT l.*, (SELECT COUNT(*) FROM leads WHERE list_id = l.id) AS lead_count
           FROM lead_lists l ORDER BY l.created_at, l.name"""
    ).fetchall()
    return [dict(row) for row in rows]


def get_list(connection: sqlite3.Connection, list_id: str) -> dict[str, Any]:
    row = connection.execute(
        """SELECT l.*, (SELECT COUNT(*) FROM leads WHERE list_id = l.id) AS lead_count
           FROM lead_lists l WHERE l.id = ?""",
        (list_id,),
    ).fetchone()
    if row is None:
        raise LookupError("List not found.")
    return dict(row)


def create_list(
    connection: sqlite3.Connection, name: str, description: str = ""
) -> dict[str, Any]:
    name = clean(name, 80)
    if not name:
        raise ValueError("Give the list a name.")
    if _count(connection, "SELECT COUNT(*) FROM lead_lists") >= MAX_LISTS:
        raise ValueError(f"You can keep up to {MAX_LISTS} lists; delete one first.")
    if connection.execute(
        "SELECT 1 FROM lead_lists WHERE lower(name) = lower(?)", (name,)
    ).fetchone():
        raise ValueError("A list with that name already exists.")
    list_id = new_id()
    connection.execute(
        "INSERT INTO lead_lists (id, name, description, created_at) VALUES (?, ?, ?, ?)",
        (list_id, name, clean(description, 300), now()),
    )
    connection.commit()
    return get_list(connection, list_id)


def default_list(connection: sqlite3.Connection) -> dict[str, Any]:
    """The list new leads land in when none is named; created on first use."""
    row = connection.execute(
        "SELECT id FROM lead_lists ORDER BY created_at, name LIMIT 1"
    ).fetchone()
    if row is not None:
        return get_list(connection, row["id"])
    return create_list(connection, DEFAULT_LIST_NAME)


def rename_list(
    connection: sqlite3.Connection,
    list_id: str,
    name: str | None,
    description: str | None,
) -> dict[str, Any]:
    current = get_list(connection, list_id)
    new_name = clean(name, 80) if name is not None else current["name"]
    if not new_name:
        raise ValueError("Give the list a name.")
    if connection.execute(
        "SELECT 1 FROM lead_lists WHERE lower(name) = lower(?) AND id != ?",
        (new_name, list_id),
    ).fetchone():
        raise ValueError("A list with that name already exists.")
    connection.execute(
        "UPDATE lead_lists SET name = ?, description = ? WHERE id = ?",
        (
            new_name,
            clean(description, 300)
            if description is not None
            else current["description"],
            list_id,
        ),
    )
    connection.commit()
    return get_list(connection, list_id)


def delete_list(connection: sqlite3.Connection, list_id: str) -> int:
    """Delete a list and every lead in it; returns how many leads went."""
    get_list(connection, list_id)
    ids = [
        r[0]
        for r in connection.execute(
            "SELECT id FROM leads WHERE list_id = ?", (list_id,)
        )
    ]
    _forget_keys(connection, ids)
    connection.execute("DELETE FROM leads WHERE list_id = ?", (list_id,))
    connection.execute("DELETE FROM lead_lists WHERE id = ?", (list_id,))
    connection.execute(
        "UPDATE lead_searches SET list_id = '' WHERE list_id = ?", (list_id,)
    )
    connection.commit()
    return len(ids)


# ---------------------------------------------------------------------------
# leads
# ---------------------------------------------------------------------------

_TEXT_FIELDS = {
    "name": 160,
    "first_name": 80,
    "last_name": 80,
    "title": 200,
    "company": 200,
    "industry": 120,
    "location": 200,
    "website": 300,
    "email": 200,
    "phone": 60,
    "linkedin_url": 300,
    "sales_nav_url": 500,
    "source_ref": 300,
}
EDITABLE = (
    "name",
    "first_name",
    "last_name",
    "title",
    "company",
    "industry",
    "location",
    "website",
    "email",
    "phone",
    "linkedin_url",
    "status",
    "notes",
    "tags",
)
_EMAIL = re.compile(r"^[A-Za-z0-9._%+\-']+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}$")


def _http_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = "https://" + text
    parts = urlsplit(text)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return ""
    return text[:300]


def normalize_lead(raw: Mapping[str, Any]) -> dict[str, Any]:
    """A clean, bounded lead dictionary from whatever a source or the form sent."""
    lead: dict[str, Any] = {
        key: clean(raw.get(key), limit) for key, limit in _TEXT_FIELDS.items()
    }
    kind = str(raw.get("kind") or "").strip().lower()
    lead["kind"] = (
        kind if kind in KINDS else ("person" if raw.get("title") else "company")
    )
    source = str(raw.get("source") or "manual").strip().lower()
    lead["source"] = source if source in SOURCES else "manual"
    lead["website"] = _http_url(lead["website"])
    lead["linkedin_url"] = canonical_linkedin(lead["linkedin_url"]) or (
        lead["linkedin_url"] if lead["linkedin_url"].startswith("https://") else ""
    )
    sales = lead["sales_nav_url"]
    lead["sales_nav_url"] = (
        sales if re.match(r"https://www\.linkedin\.com/sales/", sales) else ""
    )
    email = lead["email"].lower()
    lead["email"] = email if _EMAIL.match(email) else ""
    if not lead["name"]:
        lead["name"] = clean(
            f"{lead['first_name']} {lead['last_name']}".strip() or lead["company"], 160
        )
    if lead["kind"] == "person" and not (lead["first_name"] or lead["last_name"]):
        first, _, last = lead["name"].partition(" ")
        lead["first_name"], lead["last_name"] = clean(first, 80), clean(last, 80)
    lead["company_domain"] = domain_of(lead["website"])
    for key in ("rating",):
        value = raw.get(key)
        try:
            lead[key] = round(float(value), 2) if value not in (None, "") else None
        except (TypeError, ValueError):
            lead[key] = None
    try:
        reviews = raw.get("reviews")
        lead["reviews"] = int(reviews) if reviews not in (None, "") else None
    except (TypeError, ValueError):
        lead["reviews"] = None
    raw_signals = raw.get("signals")
    signals: list[Any] = list(raw_signals) if isinstance(raw_signals, list) else []
    lead["signals"] = [
        {
            "type": clean(item.get("type"), 30),
            "text": clean(item.get("text"), 200),
            "url": _http_url(str(item.get("url") or "")),
        }
        for item in signals[:10]
        if isinstance(item, Mapping) and clean(item.get("text"), 200)
    ]
    raw_tags = raw.get("tags")
    tags: list[Any] = list(raw_tags) if isinstance(raw_tags, list) else []
    lead["tags"] = list(dict.fromkeys(clean(t, 40) for t in tags if clean(t, 40)))[:20]
    status = str(raw.get("status") or "new").strip().lower()
    lead["status"] = status if status in STATUSES else "new"
    lead["notes"] = clean(raw.get("notes"), 4000)
    if not lead["name"]:
        raise ValueError("A lead needs a name.")
    return lead


def find_duplicate(
    connection: sqlite3.Connection, lead: Mapping[str, Any]
) -> dict[str, Any] | None:
    for key in dedupe_keys(lead):
        row = connection.execute(
            "SELECT l.* FROM lead_keys k JOIN leads l ON l.id = k.lead_id WHERE k.key = ?",
            (key,),
        ).fetchone()
        if row is not None:
            return _row(row)
    return None


def _remember_keys(
    connection: sqlite3.Connection, lead_id: str, lead: Mapping[str, Any]
) -> None:
    for key in dedupe_keys(lead):
        connection.execute(
            "INSERT OR IGNORE INTO lead_keys (key, lead_id) VALUES (?, ?)",
            (key, lead_id),
        )


def _forget_keys(connection: sqlite3.Connection, lead_ids: Iterable[str]) -> None:
    ids = list(lead_ids)
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ", ".join("?" for _ in chunk)
        connection.execute(f"DELETE FROM lead_keys WHERE lead_id IN ({marks})", chunk)


def insert_lead(
    connection: sqlite3.Connection,
    raw: Mapping[str, Any],
    *,
    list_id: str,
    score: int | None = None,
    score_reasons: Sequence[str] = (),
) -> dict[str, Any]:
    """Add one lead. A lead already known (by LinkedIn, place, domain or name) is not added twice."""
    lead = normalize_lead(raw)
    duplicate = find_duplicate(connection, lead)
    if duplicate is not None:
        return {"lead": duplicate, "created": False}
    if (
        _count(connection, "SELECT COUNT(*) FROM leads WHERE list_id = ?", (list_id,))
        >= MAX_LEADS_PER_LIST
    ):
        raise ValueError(f"A list holds up to {MAX_LEADS_PER_LIST} leads.")
    lead_id, stamp = new_id(), now()
    connection.execute(
        """INSERT INTO leads (id, list_id, kind, name, first_name, last_name, title, company, company_domain,
            industry, location, website, email, phone, linkedin_url, sales_nav_url, source, source_ref, rating, reviews,
            signals, score, score_reasons, status, notes, tags, created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            lead_id,
            list_id,
            lead["kind"],
            lead["name"],
            lead["first_name"],
            lead["last_name"],
            lead["title"],
            lead["company"],
            lead["company_domain"],
            lead["industry"],
            lead["location"],
            lead["website"],
            lead["email"],
            lead["phone"],
            lead["linkedin_url"],
            lead["sales_nav_url"],
            lead["source"],
            lead["source_ref"],
            lead["rating"],
            lead["reviews"],
            _json(lead["signals"], []),
            score,
            _json(list(score_reasons), []),
            lead["status"],
            lead["notes"],
            _json(lead["tags"], []),
            stamp,
            stamp,
        ),
    )
    _remember_keys(connection, lead_id, lead)
    connection.commit()
    return {"lead": get_lead(connection, lead_id), "created": True}


def get_lead(connection: sqlite3.Connection, lead_id: str) -> dict[str, Any]:
    row = connection.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        raise LookupError("Lead not found.")
    return _row(row)


SORTS = {
    "updated": "updated_at DESC",
    "score": "COALESCE(score, -1) DESC, updated_at DESC",
    "name": "lower(name) ASC",
    "company": "lower(company) ASC, lower(name) ASC",
    "created": "created_at DESC",
}


def query_leads(
    connection: sqlite3.Connection,
    *,
    list_id: str = "",
    status: str = "",
    source: str = "",
    kind: str = "",
    text: str = "",
    min_score: int | None = None,
    has_email: bool | None = None,
    tag: str = "",
    sort: str = "updated",
    limit: int = 50,
    offset: int = 0,
) -> dict[str, Any]:
    clauses: list[str] = []
    args: list[Any] = []
    if list_id:
        clauses.append("list_id = ?")
        args.append(list_id)
    if status:
        if status not in STATUSES:
            raise ValueError("Unknown status.")
        clauses.append("status = ?")
        args.append(status)
    if source:
        clauses.append("source = ?")
        args.append(source)
    if kind:
        clauses.append("kind = ?")
        args.append(kind)
    if min_score is not None:
        clauses.append("score >= ?")
        args.append(min_score)
    if has_email is True:
        clauses.append("email != ''")
    elif has_email is False:
        clauses.append("email = ''")
    if tag:
        clauses.append("tags LIKE ?")
        args.append("%" + json.dumps(clean(tag, 40))[1:-1] + "%")
    if text:
        like = "%" + clean(text, 100).replace("%", "").replace("_", "") + "%"
        clauses.append(
            "(name LIKE ? OR company LIKE ? OR title LIKE ? OR location LIKE ? OR email LIKE ? OR notes LIKE ?)"
        )
        args.extend([like] * 6)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    order = SORTS.get(sort)
    if order is None:
        raise ValueError(f"sort must be one of: {', '.join(SORTS)}.")
    total = _count(connection, f"SELECT COUNT(*) FROM leads {where}", args)
    rows = connection.execute(
        f"SELECT * FROM leads {where} ORDER BY {order} LIMIT ? OFFSET ?",
        [*args, max(1, min(limit, 500)), max(0, offset)],
    ).fetchall()
    return {"leads": [_row(row) for row in rows], "total": total}


def update_lead(
    connection: sqlite3.Connection, lead_id: str, changes: Mapping[str, Any]
) -> dict[str, Any]:
    current = get_lead(connection, lead_id)
    merged = {**current}
    for key in EDITABLE:
        if key in changes:
            merged[key] = changes[key]
    if "status" in changes and str(changes["status"]) not in STATUSES:
        raise ValueError(f"status must be one of: {', '.join(STATUSES)}.")
    lead = normalize_lead(
        {**merged, "source": current["source"], "kind": current["kind"]}
    )
    other = find_duplicate(connection, lead)
    if other is not None and other["id"] != lead_id:
        raise ValueError(f"That would duplicate the lead {other['name']}.")
    connection.execute(
        """UPDATE leads SET name = ?, first_name = ?, last_name = ?, title = ?, company = ?, company_domain = ?,
            industry = ?, location = ?, website = ?, email = ?, phone = ?, linkedin_url = ?, status = ?,
            notes = ?, tags = ?, updated_at = ? WHERE id = ?""",
        (
            lead["name"],
            lead["first_name"],
            lead["last_name"],
            lead["title"],
            lead["company"],
            lead["company_domain"],
            lead["industry"],
            lead["location"],
            lead["website"],
            lead["email"],
            lead["phone"],
            lead["linkedin_url"],
            lead["status"],
            lead["notes"],
            _json(lead["tags"], []),
            now(),
            lead_id,
        ),
    )
    _forget_keys(connection, [lead_id])
    _remember_keys(
        connection,
        lead_id,
        {
            **lead,
            "sales_nav_url": current["sales_nav_url"],
            "source_ref": current["source_ref"],
        },
    )
    connection.commit()
    return get_lead(connection, lead_id)


def save_scoring(
    connection: sqlite3.Connection,
    lead_id: str,
    score: int | None,
    reasons: Sequence[str],
) -> None:
    connection.execute(
        "UPDATE leads SET score = ?, score_reasons = ? WHERE id = ?",
        (score, _json(list(reasons), []), lead_id),
    )


def save_enrichment(
    connection: sqlite3.Connection,
    lead_id: str,
    enrichment: Mapping[str, Any],
    *,
    email: str = "",
    phone: str = "",
) -> dict[str, Any]:
    current = get_lead(connection, lead_id)
    connection.execute(
        "UPDATE leads SET enrichment = ?, enriched_at = ?, email = ?, phone = ?, updated_at = ? WHERE id = ?",
        (
            _json(dict(enrichment), {}),
            now(),
            current["email"] or email,
            current["phone"] or phone,
            now(),
            lead_id,
        ),
    )
    connection.commit()
    return get_lead(connection, lead_id)


def add_signals(
    connection: sqlite3.Connection, lead_id: str, signals: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Merge signals into a lead (a signal with a link already on the lead is not added twice); keeps 10."""
    current = get_lead(connection, lead_id)
    merged = list(current["signals"])
    known = {s.get("url") for s in merged if s.get("url")} | {
        s.get("text") for s in merged
    }
    for signal in signals:
        text = clean(signal.get("text"), 200)
        url = _http_url(str(signal.get("url") or ""))
        if not text or (url and url in known) or text in known:
            continue
        merged.append({"type": clean(signal.get("type"), 30), "text": text, "url": url})
        known.update({url, text})
    connection.execute(
        "UPDATE leads SET signals = ?, updated_at = ? WHERE id = ?",
        (_json(merged[-10:], []), now(), lead_id),
    )
    connection.commit()
    return get_lead(connection, lead_id)


def delete_leads(connection: sqlite3.Connection, ids: Sequence[str]) -> int:
    ids = list(dict.fromkeys(ids))
    if not ids:
        return 0
    _forget_keys(connection, ids)
    removed = 0
    for start in range(0, len(ids), 400):
        chunk = ids[start : start + 400]
        marks = ", ".join("?" for _ in chunk)
        removed += connection.execute(
            f"DELETE FROM leads WHERE id IN ({marks})", chunk
        ).rowcount
    connection.commit()
    return removed


def bulk_update(
    connection: sqlite3.Connection,
    ids: Sequence[str],
    action: str,
    value: Any = "",
) -> dict[str, Any]:
    ids = list(dict.fromkeys(str(i) for i in ids))[:2000]
    if not ids:
        raise ValueError("Choose at least one lead.")
    marks = ", ".join("?" for _ in ids)
    existing = [
        r[0]
        for r in connection.execute(f"SELECT id FROM leads WHERE id IN ({marks})", ids)
    ]
    if action == "delete":
        return {"changed": delete_leads(connection, existing)}
    if action == "status":
        if value not in STATUSES:
            raise ValueError(f"status must be one of: {', '.join(STATUSES)}.")
        for lead_id in existing:
            connection.execute(
                "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
                (value, now(), lead_id),
            )
        connection.commit()
        return {"changed": len(existing)}
    if action == "move":
        get_list(connection, str(value))
        for lead_id in existing:
            connection.execute(
                "UPDATE leads SET list_id = ?, updated_at = ? WHERE id = ?",
                (value, now(), lead_id),
            )
        connection.commit()
        return {"changed": len(existing)}
    if action in {"tag", "untag"}:
        tag = clean(value, 40)
        if not tag:
            raise ValueError("Enter a tag.")
        for lead_id in existing:
            current = get_lead(connection, lead_id)["tags"]
            updated = (
                list(dict.fromkeys([*current, tag]))[:20]
                if action == "tag"
                else [t for t in current if t != tag]
            )
            connection.execute(
                "UPDATE leads SET tags = ?, updated_at = ? WHERE id = ?",
                (_json(updated, []), now(), lead_id),
            )
        connection.commit()
        return {"changed": len(existing)}
    raise ValueError("action must be delete, status, move, tag or untag.")


# ---------------------------------------------------------------------------
# searches and runs
# ---------------------------------------------------------------------------


def create_search(
    connection: sqlite3.Connection,
    name: str,
    source: str,
    params: Mapping[str, Any],
    list_id: str = "",
) -> dict[str, Any]:
    name = clean(name, 100)
    if not name:
        raise ValueError("Give the saved search a name.")
    if source not in SOURCES:
        raise ValueError("Unknown source.")
    if _count(connection, "SELECT COUNT(*) FROM lead_searches") >= MAX_SEARCHES:
        raise ValueError(f"You can keep up to {MAX_SEARCHES} saved searches.")
    if list_id:
        get_list(connection, list_id)
    search_id = new_id()
    connection.execute(
        "INSERT INTO lead_searches (id, name, source, params, list_id, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (search_id, name, source, _json(dict(params), {}), list_id, now()),
    )
    connection.commit()
    return get_search(connection, search_id)


def _search_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    data["params"] = _loads(data.get("params"), {})
    return data


def get_search(connection: sqlite3.Connection, search_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT * FROM lead_searches WHERE id = ?", (search_id,)
    ).fetchone()
    if row is None:
        raise LookupError("Saved search not found.")
    return _search_row(row)


def list_searches(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    return [
        _search_row(r)
        for r in connection.execute(
            "SELECT * FROM lead_searches ORDER BY created_at DESC"
        )
    ]


def delete_search(connection: sqlite3.Connection, search_id: str) -> None:
    get_search(connection, search_id)
    connection.execute("DELETE FROM lead_searches WHERE id = ?", (search_id,))
    connection.commit()


def record_run(
    connection: sqlite3.Connection,
    *,
    source: str,
    params: Mapping[str, Any],
    found: int = 0,
    added: int = 0,
    duplicates: int = 0,
    credits: int = 0,
    cache_hit: bool = False,
    record_id: str = "",
    status: str = "ok",
    error: str = "",
    search_id: str = "",
) -> dict[str, Any]:
    run_id = new_id()
    connection.execute(
        """INSERT INTO lead_runs (id, search_id, source, params, found, added, duplicates, credits, cache_hit,
            record_id, status, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            run_id,
            search_id,
            source,
            _json(dict(params), {}),
            found,
            added,
            duplicates,
            credits,
            1 if cache_hit else 0,
            record_id,
            status,
            clean(error, 300),
            now(),
        ),
    )
    if search_id:
        connection.execute(
            "UPDATE lead_searches SET last_run_at = ?, last_found = ?, last_added = ? WHERE id = ?",
            (now(), found, added, search_id),
        )
    connection.execute(
        "DELETE FROM lead_runs WHERE id NOT IN (SELECT id FROM lead_runs ORDER BY created_at DESC, id LIMIT ?)",
        (MAX_RUNS_KEPT,),
    )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM lead_runs WHERE id = ?", (run_id,)
    ).fetchone()
    data = dict(row)
    data["params"] = _loads(data.get("params"), {})
    return data


def recent_runs(
    connection: sqlite3.Connection, limit: int = 15
) -> list[dict[str, Any]]:
    out = []
    for row in connection.execute(
        "SELECT * FROM lead_runs ORDER BY created_at DESC, rowid DESC LIMIT ?", (limit,)
    ):
        data = dict(row)
        data["params"] = _loads(data.get("params"), {})
        out.append(data)
    return out


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def counts(connection: sqlite3.Connection) -> dict[str, Any]:
    by_status = {s: 0 for s in STATUSES}
    for row in connection.execute("SELECT status, COUNT(*) FROM leads GROUP BY status"):
        by_status[row[0]] = row[1]
    by_source: dict[str, int] = {}
    for row in connection.execute("SELECT source, COUNT(*) FROM leads GROUP BY source"):
        by_source[row[0]] = row[1]
    return {
        "total": sum(by_status.values()),
        "by_status": by_status,
        "by_source": by_source,
        "with_email": _count(
            connection, "SELECT COUNT(*) FROM leads WHERE email != ''"
        ),
        "with_phone": _count(
            connection, "SELECT COUNT(*) FROM leads WHERE phone != ''"
        ),
        "enriched": _count(
            connection, "SELECT COUNT(*) FROM leads WHERE enriched_at != ''"
        ),
    }
