"""Careers: Google Jobs search, duplicate-safe import and job alerts.

This module turns the generic API layer (:mod:`aira.airaapi.service`) into
a job-search workspace. It owns three things:

* **Job normalization.** A SerpApi ``google_jobs`` response becomes a list of
  *Job* dictionaries (spec section 6) with every documented field kept: apply
  options, detected extensions, highlights, a parsed salary, a relative
  ``posted_at`` converted to a timestamp, and the dynamic ``filters``/``chips``
  Google returns for refining the search.
* **Duplicate detection and import.** Results are checked against the ``jobs``
  table with the same three rules ``_upsert_job`` uses (source record, canonical
  URL, content fingerprint) and imported through an injected
  :class:`JobGateway`, so a job is never inserted twice and imports go through
  the application's normal scoring path. The gateway is installed by the HTTP
  server module; this module never imports it.
* **Job alerts.** An alert is a saved ``google_jobs`` search plus an API
  monitor. A monitor hook (see :func:`aira.airaapi.service.register_monitor_hook`)
  lists only jobs the alert has not seen before, raises an in-app notification
  and, if the user asked for it, imports new jobs whose match score reaches a
  threshold. Nothing here ever applies to a job.

The Careers reports (company intelligence, salary, trends, interview prep,
travel, learning) live in :mod:`aira.airaapi.careers_intel`.

Honesty rules: Google has deprecated the ``chips`` and ``ltype`` filters, so a
search that uses them says so in ``warnings``; a salary is parsed only when the
posting text contains one; a match score is reported only when the profile has
enough to score with.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import re
import sqlite3
from collections.abc import Callable, Iterable, Mapping
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol

from linkedin_mcp_server.job_sources import (
    GoogleJobsAdapter,
    JobPosting,
    canonicalize_job_url,
)

from aira.airaapi import service, store

logger = logging.getLogger("aira.airaapi.careers")

JOBS_ENGINE = "google_jobs"
JOB_SOURCE = "google_jobs"
SEARCH_SOURCE = "careers"
ALERT_TAGS = ["careers", "alert"]
MAX_ALERTS = 10
MAX_ALERT_JOB_ROWS = 1500
MAX_QUERY_CHARS = 200
DEFAULT_ALERT_INTERVAL = 720
# Google Jobs does not change minute to minute, and every run costs a search.
ALERT_MIN_INTERVAL = 60
DEFAULT_MIN_SCORE = 75

POSTED_WITHIN = {
    "today": "date_posted:today",
    "3days": "date_posted:3days",
    "week": "date_posted:week",
    "month": "date_posted:month",
}
EMPLOYMENT_TYPES = {
    "full_time": "employment_type:FULLTIME",
    "part_time": "employment_type:PARTTIME",
    "contract": "employment_type:CONTRACTOR",
    "internship": "employment_type:INTERN",
}
_EMPLOYMENT_ALIASES = {
    "fulltime": "full_time",
    "full-time": "full_time",
    "full time": "full_time",
    "parttime": "part_time",
    "part-time": "part_time",
    "part time": "part_time",
    "contractor": "contract",
    "contract": "contract",
    "intern": "internship",
    "internship": "internship",
}
DEPRECATION_WARNING = (
    "Google has deprecated the chips and ltype filters, so results may ignore them. "
    "Use the filters offered with the results (they carry Google's own uds tokens) "
    "or check work_from_home on each job."
)

ANNUALIZE = {"hour": 2080, "day": 260, "week": 52, "month": 12, "year": 1}


# ---------------------------------------------------------------------------
# schema (migration 13)
# ---------------------------------------------------------------------------


def ensure_schema(connection: sqlite3.Connection) -> None:
    """Create the Careers tables. Additive and idempotent."""
    connection.execute(
        """CREATE TABLE IF NOT EXISTS careers_alerts (
            id TEXT PRIMARY KEY, saved_id TEXT NOT NULL,
            monitor_id TEXT NOT NULL DEFAULT '', name TEXT NOT NULL,
            auto_import INTEGER NOT NULL DEFAULT 0,
            min_score INTEGER NOT NULL DEFAULT 75,
            notify INTEGER NOT NULL DEFAULT 1,
            baselined INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            last_found INTEGER NOT NULL DEFAULT 0,
            last_imported INTEGER NOT NULL DEFAULT 0
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_careers_alerts_monitor ON careers_alerts(monitor_id)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS careers_alert_jobs (
            alert_id TEXT NOT NULL, job_key TEXT NOT NULL,
            first_seen_at TEXT NOT NULL,
            baseline INTEGER NOT NULL DEFAULT 0,
            dismissed INTEGER NOT NULL DEFAULT 0,
            record_id TEXT NOT NULL DEFAULT '',
            job_json TEXT NOT NULL DEFAULT '{}',
            match_score INTEGER, match_class TEXT NOT NULL DEFAULT '',
            imported_job_id TEXT NOT NULL DEFAULT '',
            PRIMARY KEY (alert_id, job_key)
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_careers_alert_jobs_new "
        "ON careers_alert_jobs(alert_id, baseline, dismissed, first_seen_at)"
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS careers_reports (
            id TEXT PRIMARY KEY, kind TEXT NOT NULL, title TEXT NOT NULL,
            created_at TEXT NOT NULL,
            credits INTEGER NOT NULL DEFAULT 0,
            cache_hits INTEGER NOT NULL DEFAULT 0,
            errors INTEGER NOT NULL DEFAULT 0,
            input_json TEXT NOT NULL DEFAULT '{}',
            body_json TEXT NOT NULL
        )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS idx_careers_reports_created ON careers_reports(created_at)"
    )


# ---------------------------------------------------------------------------
# the application's job store (installed by the HTTP server module)
# ---------------------------------------------------------------------------


class JobGateway(Protocol):
    """What Careers needs from the application's own job store and scorer."""

    def import_posting(self, posting: JobPosting) -> dict[str, Any]:
        """Save ``posting`` through the normal import path and return the Aira job row."""
        ...

    def preview_matches(
        self, postings: list[JobPosting]
    ) -> list[dict[str, Any] | None]:
        """Score each posting against the profile without saving anything."""
        ...


_gateway: JobGateway | None = None


def set_gateway(gateway: JobGateway | None) -> None:
    global _gateway
    _gateway = gateway


def get_gateway() -> JobGateway | None:
    return _gateway


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SPACE_RE = re.compile(r"\s+")
_GEO_RE = re.compile(r"^[A-Za-z]{2}$")
_LANG_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,4})?$")


def clean_text(value: Any, limit: int) -> str:
    """Collapse whitespace, drop control characters and cap the length."""
    text = _CONTROL_RE.sub("", str(value if value is not None else ""))
    return _SPACE_RE.sub(" ", text).strip()[:limit]


def text_field(
    payload: Mapping[str, Any],
    name: str,
    *,
    required: bool = False,
    limit: int = MAX_QUERY_CHARS,
    label: str = "",
) -> str:
    raw = payload.get(name)
    if raw is not None and not isinstance(raw, str | int | float):
        raise ValueError(f"{label or name} must be text.")
    text = clean_text(raw, limit + 1)
    if len(text) > limit:
        raise ValueError(f"{label or name} is limited to {limit} characters.")
    if required and not text:
        raise ValueError(f"Enter {label or name}.")
    return text


def flag(value: Any, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    raise ValueError(f"{name} must be true or false.")


def https_url(value: Any) -> str:
    """The URL when it is an absolute http(s) URL, else an empty string."""
    text = str(value or "").strip()
    if len(text) > 2000 or not re.match(r"^https?://[^\s/]+", text, re.IGNORECASE):
        return ""
    return text


def _https_only(value: Any) -> str:
    text = https_url(value)
    return text if text.lower().startswith("https://") else ""


def bound_connect(
    connection: sqlite3.Connection,
) -> Callable[[], sqlite3.Connection] | None:
    """A factory for extra connections to the same database, for worker threads.

    The application's own ``connect`` is bound to the request thread, so a
    worker thread cannot use it. The migrated database file is the same, so a
    plain connection with the same row factory is all a worker needs. Returns
    ``None`` for in-memory databases, which cannot be shared.
    """
    path = ""
    for row in connection.execute("PRAGMA database_list").fetchall():
        if row[1] == "main":
            path = str(row[2] or "")
    if not path:
        return None

    def open_worker() -> sqlite3.Connection:
        worker = sqlite3.connect(path, timeout=10)
        worker.row_factory = sqlite3.Row
        worker.execute("PRAGMA busy_timeout = 5000")
        return worker

    return open_worker


# ---------------------------------------------------------------------------
# salary and posted-at parsing
# ---------------------------------------------------------------------------

_CURRENCY_TOKENS: tuple[tuple[str, str], ...] = (
    ("US$", "USD"),
    ("CA$", "CAD"),
    ("CAD$", "CAD"),
    ("C$", "CAD"),
    ("AU$", "AUD"),
    ("AUD$", "AUD"),
    ("A$", "AUD"),
    ("NZ$", "NZD"),
    ("S$", "SGD"),
    ("HK$", "HKD"),
    ("R$", "BRL"),
    ("$", "USD"),
    ("£", "GBP"),
    ("€", "EUR"),
    ("₹", "INR"),
    ("¥", "JPY"),
    ("₩", "KRW"),
)
_CURRENCY_CODES = (
    "USD|CAD|AUD|NZD|EUR|GBP|INR|CHF|SGD|JPY|CNY|SEK|NOK|DKK|PLN|MXN|BRL|ZAR|AED"
)
_CUR = (
    r"US\$|CAD\$|CA\$|C\$|AUD\$|AU\$|A\$|NZ\$|S\$|HK\$|R\$|[$£€₹¥₩]|\b(?:"
    + _CURRENCY_CODES
    + r")\b"
)
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_AMOUNT_RE = re.compile(
    rf"(?<![\w.])(?P<cur>{_CUR})?\s?(?P<lo>{_NUM})\s?(?P<lok>[kKmM])?(?![\w])"
    rf"(?:\s*(?:-|to|and)\s*(?P<cur2>{_CUR})?\s?(?P<hi>{_NUM})\s?(?P<hik>[kKmM])?(?![\w]))?",
    re.IGNORECASE,
)
_PERIOD_RE = re.compile(
    r"\b(hour|hr|hourly|day|daily|week|wk|weekly|month|mo|monthly|year|yr|yearly|"
    r"annum|annual|annually)\b",
    re.IGNORECASE,
)
_PERIOD_NAMES = {
    "hour": "hour",
    "hr": "hour",
    "hourly": "hour",
    "day": "day",
    "daily": "day",
    "week": "week",
    "wk": "week",
    "weekly": "week",
    "month": "month",
    "mo": "month",
    "monthly": "month",
    "year": "year",
    "yr": "year",
    "yearly": "year",
    "annum": "year",
    "annual": "year",
    "annually": "year",
}
_UP_TO_RE = re.compile(r"(?:up\s+to|max(?:imum)?(?:\s+of)?|under)\s*$", re.IGNORECASE)
_FROM_RE = re.compile(
    r"(?:from|starting(?:\s+at)?|min(?:imum)?(?:\s+of)?|at\s+least|over|above)\s*$",
    re.IGNORECASE,
)
_SALARY_HINT_RE = re.compile(
    rf"(?:{_CUR})\s?\d|\d\s?[kK]\b|\b\d[\d,.]*\s*(?:-|to)\s*\$?\d[\d,.]*\s*"
    r"(?:an?|per|/)\s*(?:hour|hr|year|yr|month|week|day)",
    re.IGNORECASE,
)


def _currency_of(token: str | None) -> str | None:
    if not token:
        return None
    upper = token.upper()
    if re.fullmatch(_CURRENCY_CODES, upper):
        return upper
    for symbol, code in _CURRENCY_TOKENS:
        if upper == symbol.upper():
            return code
    return None


def _number(text: str, suffix: str | None) -> float:
    value = float(text.replace(",", ""))
    if suffix:
        value *= 1_000_000 if suffix.lower() == "m" else 1_000
    return value


def parse_salary(text: Any) -> dict[str, Any] | None:
    """Parse a salary string such as ``$60K-70K a year`` or ``$18-22 an hour``.

    Returns ``{text, min, max, currency, period, annual_min, annual_max}``.
    ``min``/``max`` are ``None`` when the text states no figure ("Competitive");
    ``period`` is ``None`` when the text does not say per what; the annualized
    values are only set when the period is known. Returns ``None`` for empty text.
    """
    original = clean_text(text, 200)
    if not original:
        return None
    salary: dict[str, Any] = {
        "text": original,
        "min": None,
        "max": None,
        "currency": None,
        "period": None,
        "annual_min": None,
        "annual_max": None,
    }
    original = re.sub("[\u2010-\u2015\u2212]", "-", original)
    salary["text"] = clean_text(text, 200)
    for match in _AMOUNT_RE.finditer(original):
        tail = original[match.end() : match.end() + 30]
        has_signal = bool(
            match.group("cur") or match.group("lok") or match.group("hik")
        ) or bool(_PERIOD_RE.search(tail))
        if not has_signal:
            continue
        low_text, high_text = match.group("lo"), match.group("hi")
        low_suffix, high_suffix = match.group("lok"), match.group("hik")
        if (
            high_suffix
            and not low_suffix
            and low_text
            and float(low_text.replace(",", "")) < 1000
        ):
            low_suffix = high_suffix
        low = _number(low_text, low_suffix)
        high = _number(high_text, high_suffix) if high_text else None
        before = original[: match.start()]
        if high is None:
            if _UP_TO_RE.search(before):
                low, high = None, low
            elif _FROM_RE.search(before):
                high = None
            else:
                high = low
        elif low is not None and low > high:
            low, high = high, low
        period_match = _PERIOD_RE.search(original[match.end() :]) or _PERIOD_RE.search(
            original
        )
        period = _PERIOD_NAMES[period_match.group(1).lower()] if period_match else None
        code = re.search(rf"\b({_CURRENCY_CODES})\b", original)
        currency = (
            _currency_of(match.group("cur"))
            or _currency_of(match.group("cur2"))
            or (code.group(1).upper() if code else None)
        )
        multiplier = ANNUALIZE.get(period or "")
        salary.update(
            {
                "min": round(low, 2) if low is not None else None,
                "max": round(high, 2) if high is not None else None,
                "currency": currency,
                "period": period,
                "annual_min": round(low * multiplier, 2)
                if low is not None and multiplier
                else None,
                "annual_max": round(high * multiplier, 2)
                if high is not None and multiplier
                else None,
            }
        )
        break
    return salary


_AGO_RE = re.compile(
    r"(?:(\d+)|\b(an?|one))\s*\+?\s*(minute|min|hour|hr|day|week|month|year)s?\s+ago",
    re.IGNORECASE,
)
_UNIT_DELTA = {
    "minute": timedelta(minutes=1),
    "min": timedelta(minutes=1),
    "hour": timedelta(hours=1),
    "hr": timedelta(hours=1),
    "day": timedelta(days=1),
    "week": timedelta(days=7),
    "month": timedelta(days=30),
    "year": timedelta(days=365),
}


def posted_timestamp(text: Any, base: datetime) -> str | None:
    """Turn ``3 days ago`` into an ISO timestamp relative to ``base`` (the search time).

    Google gives only a relative age, so day-and-older ages are rounded to the
    day, which keeps repeat imports of one posting stable. ``30+ days ago``
    means at least 30 days and is reported as 30.
    """
    label = clean_text(text, 80).lower()
    if not label:
        return None
    if label in {"today", "just posted", "just now", "new"}:
        return store.to_iso(base)
    found = _AGO_RE.search(label)
    if not found:
        return None
    count = int(found.group(1)) if found.group(1) else 1
    unit = found.group(3).lower()
    moment = base - _UNIT_DELTA[unit] * count
    if unit in {"day", "week", "month", "year"}:
        moment = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    return store.to_iso(moment)


# ---------------------------------------------------------------------------
# job normalization
# ---------------------------------------------------------------------------

# ``normalize`` and ``stable_job_id`` never use the key; the adapter only insists
# on a non-empty one at construction. Reusing it keeps one definition of a Google
# Jobs posting for both the Jobs tab and Careers.
_NORMALIZER = GoogleJobsAdapter("normalize-only")
_JOB_ID_CAP = 4000


def _search_time(result: Mapping[str, Any], fallback: datetime) -> datetime:
    """When Google served the results, from ``search_metadata`` (else ``fallback``)."""
    metadata = result.get("search_metadata")
    if isinstance(metadata, Mapping):
        for name in ("processed_at", "created_at"):
            raw = str(metadata.get(name) or "").replace(" UTC", "").strip()
            if raw:
                try:
                    parsed = datetime.fromisoformat(raw)
                except ValueError:
                    continue
                return (
                    parsed.replace(tzinfo=timezone.utc)
                    if parsed.tzinfo is None
                    else parsed.astimezone(timezone.utc)
                )
    return fallback


def _string_list(value: Any, limit: int, each: int = 300) -> list[str]:
    if not isinstance(value, list):
        return []
    return [clean_text(item, each) for item in value[:limit] if clean_text(item, each)]


def _highlights(value: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not isinstance(value, list):
        return out
    for block in value[:12]:
        if not isinstance(block, Mapping):
            continue
        items = _string_list(block.get("items"), 30, 500)
        title = clean_text(block.get("title"), 80)
        if items:
            out.append({"title": title or "Details", "items": items})
    return out


def _apply_options(value: Any) -> list[dict[str, str]]:
    out: list[dict[str, str]] = []
    if not isinstance(value, list):
        return out
    seen: set[str] = set()
    for option in value[:12]:
        if not isinstance(option, Mapping):
            continue
        link = https_url(option.get("link"))
        if not link or link in seen:
            continue
        seen.add(link)
        out.append(
            {"title": clean_text(option.get("title"), 80) or "Apply", "link": link}
        )
    return out


def job_key(raw: Mapping[str, Any]) -> str:
    """The stable id the Jobs-tab source gives this posting (so the two never diverge)."""
    raw_id = str(raw.get("job_id") or "")[:_JOB_ID_CAP]
    stable = GoogleJobsAdapter.stable_job_id(raw_id) if raw_id else ""
    if stable:
        return stable
    options = _apply_options(raw.get("apply_options"))
    url = https_url(raw.get("share_link")) or (options[0]["link"] if options else "")
    if url:
        try:
            return hashlib.sha256(
                canonicalize_job_url(url).encode("utf-8")
            ).hexdigest()[:32]
        except ValueError:
            pass
    identity = "|".join(
        clean_text(raw.get(name), 300).casefold()
        for name in ("title", "company_name", "location")
    )
    return "t-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:30]


def _salary_text(detected: Mapping[str, Any], extensions: list[str]) -> str:
    text = clean_text(detected.get("salary"), 200)
    if text:
        return text
    for extension in extensions:
        if _SALARY_HINT_RE.search(extension) and _PERIOD_RE.search(extension):
            return extension
    return ""


def normalize_job(raw: Any, base: datetime) -> dict[str, Any] | None:
    """One SerpApi ``jobs_results`` entry as a Job dictionary (``None`` if unusable)."""
    if not isinstance(raw, Mapping):
        return None
    title = clean_text(raw.get("title"), 500)
    if not title:
        return None
    detected_raw = raw.get("detected_extensions")
    detected: Mapping[str, Any] = (
        detected_raw if isinstance(detected_raw, Mapping) else {}
    )
    extensions = _string_list(raw.get("extensions"), 20, 120)
    options = _apply_options(raw.get("apply_options"))
    share_link = https_url(raw.get("share_link"))
    highlights = _highlights(raw.get("job_highlights"))
    posted_label = clean_text(detected.get("posted_at"), 80) or next(
        (item for item in extensions if _AGO_RE.search(item)), ""
    )
    qualifications: list[str] = []
    for block in highlights:
        if str(block["title"]).lower().startswith("qualification"):
            qualifications.extend(block["items"])
    description = str(raw.get("description") or "").strip()[:50000]
    key = job_key(raw)
    importable = bool(share_link or options) and not key.startswith("t-")
    return {
        "key": key,
        "title": title,
        "company": clean_text(raw.get("company_name"), 300),
        "location": clean_text(raw.get("location"), 300),
        "via": clean_text(raw.get("via"), 120),
        "posted_at": posted_label,
        "posted_at_iso": posted_timestamp(posted_label, base),
        "schedule_type": clean_text(detected.get("schedule_type"), 60),
        "work_from_home": detected.get("work_from_home") is True,
        "salary": parse_salary(_salary_text(detected, extensions)),
        "description": description,
        "highlights": highlights,
        "qualifications": qualifications[:30],
        "apply_options": options,
        "share_link": share_link,
        "thumbnail": _https_only(raw.get("thumbnail")),
        "google_job_id": str(raw.get("job_id") or "")[:_JOB_ID_CAP],
        "extensions": extensions,
        "benefits": {
            name: True
            for name in ("health_insurance", "dental_coverage", "paid_time_off")
            if detected.get(name) is True
        },
        "importable": importable,
        "already_in_aira": False,
        "aira_job_key": None,
        "match": None,
        "aira_source": clean_text(raw.get("aira_source"), 40) or None,
        "attribution": clean_text(raw.get("attribution"), 200),
        "posted_at_exact": clean_text(raw.get("posted_at_iso"), 40) or None,
    }


def normalize_results(
    result: Any, fallback_time: datetime | None = None
) -> list[dict[str, Any]]:
    """Every usable, de-duplicated job in a ``google_jobs`` response body."""
    if not isinstance(result, Mapping):
        return []
    base = _search_time(result, fallback_time or store.now_utc())
    records = result.get("jobs_results")
    jobs: list[dict[str, Any]] = []
    seen: set[str] = set()
    if not isinstance(records, list):
        return jobs
    for raw in records:
        job = normalize_job(raw, base)
        if job is None or job["key"] in seen:
            continue
        seen.add(job["key"])
        jobs.append(job)
    return jobs


def _filter_option(text: Any, value: Any, group: str = "") -> dict[str, str] | None:
    label = clean_text(text, 100)
    token = clean_text(value, 6000)
    if not label or not token:
        return None
    if group and ":" not in token and group != "uds":
        token = f"{group}:{token}"
    return {"text": label, "value": token}


def normalize_filters(result: Any) -> list[dict[str, Any]]:
    """Google's ``filters`` and ``chips`` as ``[{name, parameter, options:[{text, value}]}]``.

    ``parameter`` says which search parameter the option values go to: ``uds``
    for the filter strings Google issues for the current query, ``chips`` for
    the older ``name:value`` chips. Unknown shapes are skipped, never guessed.
    """
    if not isinstance(result, Mapping):
        return []
    groups: list[dict[str, Any]] = []
    for entry in result.get("filters") or []:
        if not isinstance(entry, Mapping):
            continue
        name = clean_text(
            entry.get("name") or entry.get("type") or entry.get("title"), 80
        )
        parameters = entry.get("parameters")
        parameters = parameters if isinstance(parameters, Mapping) else {}
        options: list[dict[str, str]] = []
        parameter = "uds"
        listed = entry.get("options")
        if isinstance(listed, list) and listed:
            for option in listed[:24]:
                if not isinstance(option, Mapping):
                    continue
                inner = option.get("parameters")
                inner = inner if isinstance(inner, Mapping) else {}
                value = inner.get("uds") or option.get("uds")
                if value:
                    built = _filter_option(
                        option.get("text") or option.get("name") or option.get("title"),
                        value,
                        "uds",
                    )
                else:
                    value = (
                        inner.get("chips") or option.get("chips") or option.get("value")
                    )
                    parameter = "chips"
                    built = _filter_option(
                        option.get("text") or option.get("name") or option.get("title"),
                        value,
                        str(entry.get("param") or ""),
                    )
                if built:
                    options.append(built)
        elif parameters.get("uds"):
            built = _filter_option(name, parameters["uds"], "uds")
            options = [built] if built else []
        elif parameters.get("chips"):
            parameter = "chips"
            built = _filter_option(name, parameters["chips"])
            options = [built] if built else []
        if name and options:
            groups.append({"name": name, "parameter": parameter, "options": options})
    for chip in result.get("chips") or []:
        if not isinstance(chip, Mapping):
            continue
        name = clean_text(chip.get("type") or chip.get("name") or chip.get("title"), 80)
        group = str(chip.get("param") or "")
        options = []
        for option in (chip.get("options") or [])[:24]:
            if not isinstance(option, Mapping):
                continue
            built = _filter_option(
                option.get("text") or option.get("name") or option.get("title"),
                option.get("value") or option.get("chips"),
                group,
            )
            if built:
                options.append(built)
        if name and options:
            groups.append({"name": name, "parameter": "chips", "options": options})
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        merged_group = merged.setdefault(
            (group["name"], group["parameter"]),
            {"name": group["name"], "parameter": group["parameter"], "options": []},
        )
        known = {option["value"] for option in merged_group["options"]}
        merged_group["options"].extend(
            option for option in group["options"] if option["value"] not in known
        )
    return list(merged.values())


def next_page_token(result: Any) -> str:
    if not isinstance(result, Mapping):
        return ""
    pagination = result.get("serpapi_pagination")
    if isinstance(pagination, Mapping):
        return clean_text(pagination.get("next_page_token"), 4000)
    return ""


# ---------------------------------------------------------------------------
# search parameters
# ---------------------------------------------------------------------------


def employment_type_key(value: Any) -> str:
    text = clean_text(value, 40).lower().replace("_", " ")
    if not text:
        return ""
    candidate = _EMPLOYMENT_ALIASES.get(text) or _EMPLOYMENT_ALIASES.get(
        text.replace(" ", "")
    )
    if candidate is None and text.replace(" ", "_") in EMPLOYMENT_TYPES:
        candidate = text.replace(" ", "_")
    if candidate is None:
        raise ValueError(
            "employment_type must be one of: full_time, part_time, contract, internship."
        )
    return candidate


def _chip_list(value: Any) -> list[str]:
    if value in (None, ""):
        return []
    items = value.split(",") if isinstance(value, str) else value
    if not isinstance(items, list):
        raise ValueError("chips must be a list of Google chip values.")
    chips: list[str] = []
    for item in items:
        text = clean_text(item, 300)
        if text and text not in chips:
            chips.append(text)
    if len(chips) > 12:
        raise ValueError("At most 12 chips can be combined in one search.")
    return chips


AIRA_JOBS_ENGINE = "aira_jobs"
PROVIDERS = ("google", "aira")
_AIRA_EMPLOYMENT = {
    "fulltime": "full_time",
    "full_time": "full_time",
    "parttime": "part_time",
    "part_time": "part_time",
    "contractor": "contract",
    "contract": "contract",
    "intern": "internship",
    "internship": "internship",
    "temporary": "temporary",
}


def build_aira_params(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Map the Careers search form onto ``aira_jobs`` parameters (Aira's own job boards)."""
    params: dict[str, Any] = {
        "q": text_field(payload, "q", required=True, label="a search query")
    }
    applied: list[dict[str, Any]] = []
    warnings: list[str] = []
    location = text_field(payload, "location", label="the location")
    if location:
        params["location"] = location
    if flag(payload.get("remote"), "remote"):
        params["remote_only"] = True
        applied.append({"filter": "Remote only", "param": "remote_only", "value": True})
    employment = str(payload.get("employment_type") or "").strip().lower()
    if employment:
        mapped = _AIRA_EMPLOYMENT.get(employment)
        if not mapped:
            raise ValueError("Choose a supported employment type.")
        params["employment_type"] = mapped
        applied.append(
            {"filter": "Employment type", "param": "employment_type", "value": mapped}
        )
    if any(
        payload.get(name)
        for name in ("posted_within", "chips", "uds", "lrad", "next_page_token")
    ):
        warnings.append(
            "Aira Jobs ignores the Google-only filters (posted within, radius, Google chips); results are newest first."
        )
    return params, applied, warnings


def build_job_params(
    payload: Mapping[str, Any], *, paginate: bool = True
) -> tuple[dict[str, Any], list[dict[str, Any]], list[str]]:
    """Map the Careers search form onto ``google_jobs`` parameters.

    Returns ``(params, applied, warnings)``. ``applied`` lists each friendly
    filter and the real parameter it became, so the UI can show exactly what
    was sent. Remote, posted-within and employment type use the documented
    ``ltype`` and ``chips`` parameters; both are deprecated by Google, which
    is reported in ``warnings`` rather than hidden.
    """
    params: dict[str, Any] = {
        "q": text_field(payload, "q", required=True, label="a search query")
    }
    applied: list[dict[str, Any]] = []
    location = text_field(payload, "location", label="the location")
    if location:
        params["location"] = location
    gl = text_field(payload, "gl", limit=20, label="gl")
    if gl:
        if not _GEO_RE.match(gl):
            raise ValueError("gl must be a two-letter country code such as us.")
        params["gl"] = gl.lower()
    hl = text_field(payload, "hl", limit=20, label="hl")
    if hl:
        if not _LANG_RE.match(hl):
            raise ValueError("hl must be a language code such as en or en-gb.")
        if (
            hl.lower() != "en"
        ):  # en is SerpApi's default; omitting it keeps one cache key with the Jobs tab
            params["hl"] = hl
    chips = _chip_list(payload.get("chips"))
    deprecated = False
    posted = clean_text(payload.get("posted_within"), 20).lower()
    if posted:
        if posted not in POSTED_WITHIN:
            raise ValueError("posted_within must be one of: today, 3days, week, month.")
        chip = POSTED_WITHIN[posted]
        chips = [item for item in chips if not item.startswith("date_posted:")] + [chip]
        applied.append(
            {
                "filter": "posted_within",
                "value": posted,
                "parameter": "chips",
                "sent": chip,
            }
        )
        deprecated = True
    employment = employment_type_key(payload.get("employment_type"))
    if employment:
        chip = EMPLOYMENT_TYPES[employment]
        chips = [item for item in chips if not item.startswith("employment_type:")] + [
            chip
        ]
        applied.append(
            {
                "filter": "employment_type",
                "value": employment,
                "parameter": "chips",
                "sent": chip,
            }
        )
        deprecated = True
    if chips:
        params["chips"] = ",".join(chips)
        if not posted and not employment:
            applied.append(
                {
                    "filter": "chips",
                    "value": params["chips"],
                    "parameter": "chips",
                    "sent": params["chips"],
                }
            )
    ltype = payload.get("ltype")
    if flag(payload.get("remote"), "remote") and ltype in (None, ""):
        ltype = 1
    if ltype not in (None, ""):
        if str(ltype).strip() not in {"1", "0"}:
            raise ValueError("ltype must be 1 (work from home) when set.")
        if str(ltype).strip() == "1":
            params["ltype"] = "1"
            applied.append(
                {"filter": "remote", "value": True, "parameter": "ltype", "sent": "1"}
            )
            deprecated = True
    if payload.get("lrad") not in (None, ""):
        try:
            radius = float(payload["lrad"])
        except (TypeError, ValueError):
            raise ValueError("lrad must be a number of kilometres.") from None
        if not 0 < radius <= 1000:
            raise ValueError("lrad must be between 1 and 1000 kilometres.")
        params["lrad"] = int(radius) if radius == int(radius) else radius
        applied.append(
            {
                "filter": "radius_km",
                "value": radius,
                "parameter": "lrad",
                "sent": str(params["lrad"]),
            }
        )
    uds = text_field(payload, "uds", limit=6000, label="uds")
    if uds:
        params["uds"] = uds
        applied.append(
            {"filter": "uds", "value": "filter", "parameter": "uds", "sent": "uds"}
        )
    if paginate:
        token = text_field(
            payload, "next_page_token", limit=4000, label="next_page_token"
        )
        if token:
            params["next_page_token"] = token
    return params, applied, [DEPRECATION_WARNING] if deprecated else []


# ---------------------------------------------------------------------------
# duplicate detection and import
# ---------------------------------------------------------------------------


def _job_urls(job: Mapping[str, Any]) -> list[str]:
    urls = [job.get("share_link"), *(o["link"] for o in job.get("apply_options") or [])]
    canonical: list[str] = []
    for url in urls:
        if not url:
            continue
        try:
            value = canonicalize_job_url(str(url))
        except ValueError:
            continue
        if value not in canonical:
            canonical.append(value)
    return canonical


def build_posting(job: Mapping[str, Any]) -> JobPosting:
    """Rebuild a :class:`JobPosting` from a Job dictionary.

    Everything is re-derived from the fields Google supplied (the key and the
    flags the client sends back are ignored), reusing the Jobs-tab normalizer,
    so a posting imported from here and one found by the Jobs tab are the same
    record. Salaries are stored annualized because the Aira scorer compares
    against annual expectations.
    """
    title = clean_text(job.get("title"), 500)
    if not title:
        raise ValueError("A job needs a title to be imported.")
    if job.get("aira_source"):
        return _aira_posting(job, title)
    highlights = _highlights(job.get("highlights"))
    description = str(job.get("description") or "").strip()[:50000]
    if not description and highlights:
        description = "\n\n".join(
            f"{block['title']}\n" + "\n".join(f"- {item}" for item in block["items"])
            for block in highlights
        )[:50000]
    record = {
        "job_id": str(job.get("google_job_id") or "")[:_JOB_ID_CAP],
        "share_link": https_url(job.get("share_link")),
        "apply_options": _apply_options(job.get("apply_options")),
        "title": title,
        "company_name": clean_text(job.get("company"), 300),
        "location": clean_text(job.get("location"), 300),
        "description": description,
        "detected_extensions": {
            "work_from_home": job.get("work_from_home") is True,
            "schedule_type": clean_text(job.get("schedule_type"), 60),
        },
    }
    posting = _NORMALIZER.normalize(record)
    changes: dict[str, Any] = {}
    salary = job.get("salary")
    if isinstance(salary, Mapping):
        figures = parse_salary(salary.get("text")) or {}
        low, high = figures.get("annual_min"), figures.get("annual_max")
        if low is not None or high is not None:
            changes["salary_min"] = low if low is not None else high
            changes["salary_max"] = high if high is not None else low
            changes["currency"] = figures.get("currency")
    posted = job.get("posted_at_iso")
    if isinstance(posted, str) and posted:
        try:
            changes["posted_at"] = store.from_iso(posted)
        except ValueError:
            pass
    return dataclasses.replace(posting, **changes) if changes else posting


def _aira_posting(job: Mapping[str, Any], title: str) -> JobPosting:
    """An Aira Jobs result (a free job board, not Google) as a posting with its own source."""
    from aira.airaapi import jobboards

    return jobboards.posting_from_item(
        {
            "title": title,
            "company_name": clean_text(job.get("company"), 300),
            "location": clean_text(job.get("location"), 300),
            "description": str(job.get("description") or "")[:50000],
            "job_id": str(job.get("google_job_id") or job.get("key") or "")[
                :_JOB_ID_CAP
            ],
            "share_link": https_url(job.get("share_link")),
            "apply_options": _apply_options(job.get("apply_options")),
            "detected_extensions": {
                "work_from_home": job.get("work_from_home") is True,
                "schedule_type": clean_text(job.get("schedule_type"), 60),
            },
            "posted_at_iso": job.get("posted_at_exact"),
            "attribution": clean_text(job.get("attribution"), 200),
        }
    )


def find_existing(
    connection: sqlite3.Connection,
    job: Mapping[str, Any],
    posting: JobPosting | None = None,
) -> dict[str, Any] | None:
    """The Aira job this posting already is, using the importer's own three rules.

    ``via`` says which rule matched: ``source`` (same Google record), ``url``
    (the share link or any apply link is a known posting URL, which is how a
    Greenhouse job imported earlier is recognised) or ``content`` (same title,
    company, location and description). For ``url`` matches ``source_url`` is
    the known URL, which the import passes on so the importer merges the two.
    """
    key = str(job.get("key") or "")
    row = connection.execute(
        "SELECT job_id FROM job_source_records WHERE source = ? AND external_id = ?",
        ("aira_jobs" if job.get("aira_source") else JOB_SOURCE, key),
    ).fetchone()
    if row:
        return {"job_id": row[0], "via": "source", "source_url": ""}
    for url in _job_urls(job):
        row = connection.execute(
            "SELECT job_id, source_url FROM job_source_records WHERE canonical_url = ? "
            "ORDER BY first_seen_at, job_id LIMIT 1",
            (url,),
        ).fetchone()
        if row:
            return {"job_id": row[0], "via": "url", "source_url": row[1]}
    if posting is not None and posting.content_fingerprint:
        title, company, location = posting.title, posting.company, posting.location
        if title and company and location:
            row = connection.execute(
                """SELECT job_id FROM jobs WHERE content_fingerprint = ?
                AND lower(trim(title)) = lower(trim(?))
                AND lower(trim(company)) = lower(trim(?))
                AND lower(trim(location)) = lower(trim(?))
                ORDER BY updated_at, job_id LIMIT 1""",
                (posting.content_fingerprint, title, company, location),
            ).fetchone()
            if row:
                return {"job_id": row[0], "via": "content", "source_url": ""}
    return None


def annotate_jobs(
    connection: sqlite3.Connection,
    jobs: list[dict[str, Any]],
    *,
    with_match: bool = True,
) -> list[dict[str, Any]]:
    """Fill ``already_in_aira``, ``aira_job_key`` and ``match`` on each job in place."""
    postings: list[tuple[int, JobPosting]] = []
    for index, job in enumerate(jobs):
        posting: JobPosting | None = None
        if job.get("importable"):
            try:
                posting = build_posting(job)
            except ValueError:
                job["importable"] = False
        found = find_existing(connection, job, posting)
        job["already_in_aira"] = found is not None
        job["aira_job_key"] = found["job_id"] if found else None
        if posting is not None:
            postings.append((index, posting))
    gateway = get_gateway()
    if with_match and gateway is not None and postings:
        try:
            matches = gateway.preview_matches([item for _, item in postings])
        except Exception:
            logger.debug("Match preview unavailable", exc_info=True)
            matches = [None] * len(postings)
        for (index, _), match in zip(postings, matches, strict=False):
            jobs[index]["match"] = match
    return jobs


def import_job(
    connection: sqlite3.Connection, job: Mapping[str, Any]
) -> dict[str, Any]:
    """Import one job through the application's importer; duplicate-safe.

    ``connection`` is only read from, so no write lock is held while the
    gateway writes through its own connection.
    """
    gateway = get_gateway()
    if gateway is None:
        raise RuntimeError("Job import is not available in this process.")
    if not isinstance(job, Mapping):
        raise ValueError("Send the job to import as job.")
    posting = build_posting(job)
    existing = find_existing(connection, {**job, "key": posting.external_id}, posting)
    if existing and existing["via"] == "url" and existing["source_url"]:
        posting = dataclasses.replace(posting, source_url=existing["source_url"])
    row = gateway.import_posting(posting)
    job_id = str(row.get("job_id") or "")
    if job_id:
        connection.execute(
            "UPDATE careers_alert_jobs SET imported_job_id = ? WHERE job_key = ? "
            "AND imported_job_id = ''",
            (job_id, posting.external_id),
        )
        connection.commit()
    return {
        "job": row,
        "created": existing is None,
        "duplicate_of": existing["job_id"] if existing else None,
        "matched_by": existing["via"] if existing else None,
    }


# ---------------------------------------------------------------------------
# job search
# ---------------------------------------------------------------------------


def _cache_would_hit(
    connection: sqlite3.Connection,
    engine_id: str,
    params: Mapping[str, Any],
    limits: service.Limits,
    now: datetime,
) -> bool:
    from aira.airaapi import catalog

    clean, problems = catalog.validate_params(engine_id, params)
    if problems:
        raise ValueError(" ".join(problems))
    ttl = limits.cache_ttl_minutes
    if ttl <= 0:
        return False
    digest = service.params_hash(engine_id, clean, service.normalize_options({}))
    since = store.to_iso(now - timedelta(minutes=ttl))
    return store.find_cached(connection, digest, since) is not None


def search_jobs(
    connection: sqlite3.Connection,
    client: Any,
    payload: Mapping[str, Any],
    *,
    limits: service.Limits | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run one jobs search (Google Jobs, or Aira's own job boards) and return normalized, annotated jobs.

    ``provider`` is ``google`` (SerpApi's Google Jobs, the default when a key is
    connected) or ``aira`` (Aira Jobs: free public job boards, no key, no credits).
    """
    moment = now or store.now_utc()
    limits = limits or service.Limits()
    provider = str(
        payload.get("provider") or ("google" if client is not None else "aira")
    )
    if provider not in PROVIDERS:
        raise ValueError("provider must be google or aira.")
    engine_id = JOBS_ENGINE if provider == "google" else AIRA_JOBS_ENGINE
    if provider == "google":
        params, applied, warnings = build_job_params(payload)
    else:
        params, applied, warnings = build_aira_params(payload)
    if flag(payload.get("dry_run"), "dry_run"):
        hit = _cache_would_hit(connection, engine_id, params, limits, moment)
        return {
            "dry_run": True,
            "engine": engine_id,
            "provider": provider,
            "params": params,
            "applied": applied,
            "warnings": warnings,
            "credits": 0 if hit or provider == "aira" else 1,
            "cache_hit": hit,
        }
    save_as = text_field(payload, "save_as", limit=120, label="the saved search name")
    record = service.run_search(
        connection,
        client,
        engine_id,
        params,
        source=SEARCH_SOURCE,
        label=f"Jobs: {params['q']}"[:200],
        now=moment,
        limits=limits,
    )
    result = record.get("result")
    jobs = normalize_results(result, store.from_iso(record["created_at"]))
    annotate_jobs(connection, jobs)
    information = (
        result.get("search_information") if isinstance(result, Mapping) else None
    )
    response: dict[str, Any] = {
        "provider": provider,
        "boards": (information or {}).get("boards", []) if provider == "aira" else [],
        "jobs": jobs,
        "next_page_token": next_page_token(result),
        "filters": normalize_filters(result),
        "record_id": record["id"],
        "cache_hit": record["cache_hit"],
        "credits": record["credits"],
        "result_count": len(jobs),
        "note": record.get("note") or "",
        "params": params,
        "applied": applied,
        "warnings": warnings,
    }
    if save_as:
        try:
            saved_params = {k: v for k, v in params.items() if k != "next_page_token"}
            response["saved"] = service.create_saved(
                connection,
                {
                    "name": save_as,
                    "engine": engine_id,
                    "params": saved_params,
                    "tags": ["careers"],
                },
                now=moment,
            )
        except ValueError as failure:
            response["save_error"] = str(failure)
    return response


def shared_search(
    connection: sqlite3.Connection,
    client: Any,
    parameters: Mapping[str, Any],
    *,
    limits: service.Limits | None = None,
) -> dict[str, Any]:
    """The Jobs-tab Google Jobs source, routed through API history and cache.

    ``GoogleJobsAdapter`` builds its own request parameters (including the API
    key); this runs the same search through :func:`service.run_search` so it is
    recorded, counted and cached like every other search, and hands back the
    response body in the shape the adapter expects. A "no results" note is not
    an error here, exactly as the adapter should treat it.
    """
    return shared_search_record(connection, client, parameters, limits=limits)[0]


def shared_search_record(
    connection: sqlite3.Connection,
    client: Any,
    parameters: Mapping[str, Any],
    *,
    limits: service.Limits | None = None,
) -> tuple[dict[str, Any], Mapping[str, Any]]:
    """:func:`shared_search`, also returning the search record (``cache_hit``, ``credits``)."""
    params = {k: v for k, v in parameters.items() if k not in {"engine", "api_key"}}
    # hl=en is SerpApi's default; sending it would give the Jobs tab a different cache key from Careers.
    if str(params.get("hl", "")).lower() == "en":
        params.pop("hl")
    record = service.run_search(
        connection,
        client,
        JOBS_ENGINE,
        params,
        source=SEARCH_SOURCE,
        label=f"Jobs tab: {str(params.get('q', ''))[:150]}",
        limits=limits,
    )
    body = dict(record.get("result") or {})
    if record.get("note"):
        body.pop("error", None)
    return body, record


# ---------------------------------------------------------------------------
# job alerts
# ---------------------------------------------------------------------------


def _alert_row(connection: sqlite3.Connection, alert_id: str) -> sqlite3.Row:
    row = connection.execute(
        "SELECT * FROM careers_alerts WHERE id = ?", (alert_id,)
    ).fetchone()
    if row is None:
        raise LookupError("Job alert not found.")
    return row


def _alert_view(connection: sqlite3.Connection, row: sqlite3.Row) -> dict[str, Any]:
    saved = store.get_saved(connection, row["saved_id"])
    monitor = (
        store.get_monitor(connection, row["monitor_id"]) if row["monitor_id"] else None
    )
    params: dict[str, Any] = {}
    if saved is not None:
        try:
            params = json.loads(saved["params_json"] or "{}")
        except json.JSONDecodeError:
            params = {}
    counts = connection.execute(
        "SELECT SUM(baseline = 0 AND dismissed = 0) AS fresh, COUNT(*) AS seen "
        "FROM careers_alert_jobs WHERE alert_id = ?",
        (row["id"],),
    ).fetchone()
    interval = monitor["interval_minutes"] if monitor else None
    return {
        "id": row["id"],
        "name": row["name"],
        "saved_id": row["saved_id"],
        "monitor_id": monitor["id"] if monitor else None,
        "params": params,
        "q": params.get("q", ""),
        "location": params.get("location", ""),
        "interval_minutes": interval,
        "estimated_monthly_searches": service.estimate_monthly_searches(interval)
        if interval
        else 0,
        "enabled": bool(monitor["enabled"]) if monitor else False,
        "auto_import": bool(row["auto_import"]),
        "min_score": row["min_score"],
        "notify": bool(row["notify"]),
        "baselined": bool(row["baselined"]),
        "created_at": row["created_at"],
        "last_run_at": monitor["last_run_at"] if monitor else None,
        "next_run_at": monitor["next_run_at"] if monitor else None,
        "last_search_id": monitor["last_search_id"] if monitor else None,
        "last_summary": (monitor["last_change_summary"] if monitor else "") or "",
        "last_found": row["last_found"],
        "last_imported": row["last_imported"],
        "new_count": int(counts["fresh"] or 0),
        "seen_count": int(counts["seen"] or 0),
    }


def list_alerts(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """All alerts, newest first. Alerts whose saved search was deleted are cleaned up."""
    alerts: list[dict[str, Any]] = []
    for row in connection.execute(
        "SELECT * FROM careers_alerts ORDER BY created_at DESC, rowid DESC"
    ).fetchall():
        if store.get_saved(connection, row["saved_id"]) is None:
            _drop_alert_rows(connection, row["id"])
            continue
        alerts.append(_alert_view(connection, row))
    connection.commit()
    return alerts


def _drop_alert_rows(connection: sqlite3.Connection, alert_id: str) -> None:
    connection.execute("DELETE FROM careers_alert_jobs WHERE alert_id = ?", (alert_id,))
    connection.execute("DELETE FROM careers_alerts WHERE id = ?", (alert_id,))


def _alert_interval(value: Any) -> int:
    try:
        minutes = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("interval_minutes must be a whole number.") from None
    if minutes < ALERT_MIN_INTERVAL:
        raise ValueError(
            f"A job alert cannot run more often than every {ALERT_MIN_INTERVAL} minutes; "
            "each run uses one search."
        )
    return minutes


def _min_score(value: Any) -> int:
    try:
        score = int(str(value).strip())
    except (TypeError, ValueError):
        raise ValueError("min_score must be a whole number from 0 to 100.") from None
    if not 0 <= score <= 100:
        raise ValueError("min_score must be a whole number from 0 to 100.")
    return score


def create_alert(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create a saved Google Jobs search, its monitor and the alert that ties them."""
    moment = now or store.now_utc()
    params, _applied, warnings = build_job_params(payload, paginate=False)
    count = connection.execute("SELECT COUNT(*) FROM careers_alerts").fetchone()[0]
    if count >= MAX_ALERTS:
        raise ValueError(
            f"You can keep up to {MAX_ALERTS} job alerts; delete one first."
        )
    default_name = params["q"] + (
        f" in {params['location']}" if params.get("location") else ""
    )
    name = (
        text_field(payload, "name", limit=120, label="the alert name")
        or default_name[:120]
    )
    interval = _alert_interval(payload.get("interval_minutes", DEFAULT_ALERT_INTERVAL))
    min_score = _min_score(payload.get("min_score", DEFAULT_MIN_SCORE))
    auto_import = flag(payload.get("auto_import"), "auto_import")
    notify_flag = (
        True if payload.get("notify") is None else flag(payload["notify"], "notify")
    )
    saved = service.create_saved(
        connection,
        {
            "name": f"Alert: {name}"[:120],
            "engine": JOBS_ENGINE,
            "params": params,
            "tags": ALERT_TAGS,
        },
        now=moment,
    )
    try:
        monitor = service.create_monitor(
            connection,
            {
                "saved_id": saved["id"],
                "interval_minutes": interval,
                "notify_on_change": False,
            },
            now=moment,
        )
    except (ValueError, LookupError):
        store.delete_saved(connection, saved["id"])
        connection.commit()
        raise
    alert_id = store.new_id()
    connection.execute(
        "INSERT INTO careers_alerts (id, saved_id, monitor_id, name, auto_import, min_score, "
        "notify, baselined, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)",
        (
            alert_id,
            saved["id"],
            monitor["id"],
            name,
            1 if auto_import else 0,
            min_score,
            1 if notify_flag else 0,
            store.to_iso(moment),
        ),
    )
    connection.commit()
    view = _alert_view(connection, _alert_row(connection, alert_id))
    view["warnings"] = warnings
    return view


def update_alert(
    connection: sqlite3.Connection,
    alert_id: str,
    payload: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or store.now_utc()
    row = _alert_row(connection, alert_id)
    fields: dict[str, Any] = {}
    if "name" in payload:
        fields["name"] = text_field(
            payload, "name", required=True, limit=120, label="the alert name"
        )
    if "auto_import" in payload:
        fields["auto_import"] = 1 if flag(payload["auto_import"], "auto_import") else 0
    if "notify" in payload:
        fields["notify"] = 1 if flag(payload["notify"], "notify") else 0
    if "min_score" in payload:
        fields["min_score"] = _min_score(payload["min_score"])
    monitor_changes: dict[str, Any] = {}
    if "interval_minutes" in payload:
        monitor_changes["interval_minutes"] = _alert_interval(
            payload["interval_minutes"]
        )
    if "enabled" in payload:
        monitor_changes["enabled"] = payload["enabled"]
    if not fields and not monitor_changes:
        raise ValueError(
            "Send name, interval_minutes, enabled, auto_import, min_score or notify to update."
        )
    if monitor_changes:
        monitor_id = row["monitor_id"]
        if monitor_id and store.get_monitor(connection, monitor_id) is not None:
            service.update_monitor(connection, monitor_id, monitor_changes, now=moment)
        else:
            created = service.create_monitor(
                connection,
                {
                    "saved_id": row["saved_id"],
                    "interval_minutes": monitor_changes.get(
                        "interval_minutes", DEFAULT_ALERT_INTERVAL
                    ),
                    "enabled": monitor_changes.get("enabled", True) is not False,
                    "notify_on_change": False,
                },
                now=moment,
            )
            fields["monitor_id"] = created["id"]
    if fields:
        assignments = ", ".join(f"{name} = ?" for name in fields)
        connection.execute(
            f"UPDATE careers_alerts SET {assignments} WHERE id = ?",
            (*fields.values(), alert_id),
        )
    connection.commit()
    return _alert_view(connection, _alert_row(connection, alert_id))


def delete_alert(connection: sqlite3.Connection, alert_id: str) -> None:
    """Delete an alert with its monitor, saved search and seen-jobs list."""
    row = _alert_row(connection, alert_id)
    store.delete_saved(connection, row["saved_id"])
    _drop_alert_rows(connection, alert_id)
    connection.commit()


def _hit_job(row: sqlite3.Row) -> dict[str, Any]:
    try:
        job = json.loads(row["job_json"] or "{}")
    except json.JSONDecodeError:
        job = {}
    job.setdefault("key", row["job_key"])
    job["found_at"] = row["first_seen_at"]
    job["record_id"] = row["record_id"]
    job["imported_job_id"] = row["imported_job_id"] or None
    job["match"] = (
        {"score": row["match_score"], "classification": row["match_class"]}
        if row["match_score"] is not None
        else None
    )
    return job


def alert_detail(
    connection: sqlite3.Connection, alert_id: str, *, include_dismissed: bool = False
) -> dict[str, Any]:
    """An alert with the jobs it found that were new when found."""
    row = _alert_row(connection, alert_id)
    view = _alert_view(connection, row)
    condition = "baseline = 0" + ("" if include_dismissed else " AND dismissed = 0")
    hits = connection.execute(
        f"SELECT * FROM careers_alert_jobs WHERE alert_id = ? AND {condition} "
        "ORDER BY first_seen_at DESC, rowid DESC LIMIT 100",
        (alert_id,),
    ).fetchall()
    jobs = [_hit_job(hit) for hit in hits]
    annotate_jobs(connection, jobs, with_match=False)
    for job, hit in zip(jobs, hits, strict=False):
        job["dismissed"] = bool(hit["dismissed"])
        if hit["imported_job_id"]:
            job["already_in_aira"] = True
            job["aira_job_key"] = hit["imported_job_id"]
    view["new_jobs"] = jobs
    return view


def dismiss_alert_jobs(
    connection: sqlite3.Connection, alert_id: str, keys: Iterable[str] | None
) -> dict[str, Any]:
    """Mark new jobs as reviewed (all of them when ``keys`` is ``None``)."""
    _alert_row(connection, alert_id)
    if keys is None:
        cursor = connection.execute(
            "UPDATE careers_alert_jobs SET dismissed = 1 WHERE alert_id = ? "
            "AND baseline = 0 AND dismissed = 0",
            (alert_id,),
        )
        changed = cursor.rowcount
    else:
        changed = 0
        for key in keys:
            changed += connection.execute(
                "UPDATE careers_alert_jobs SET dismissed = 1 WHERE alert_id = ? "
                "AND job_key = ? AND dismissed = 0",
                (alert_id, str(key)[:300]),
            ).rowcount
    connection.commit()
    return {
        "dismissed": changed,
        "alert": _alert_view(connection, _alert_row(connection, alert_id)),
    }


def _prune_alert_jobs(connection: sqlite3.Connection, alert_id: str) -> None:
    connection.execute(
        """DELETE FROM careers_alert_jobs WHERE alert_id = ? AND rowid NOT IN (
            SELECT rowid FROM careers_alert_jobs WHERE alert_id = ?
            ORDER BY first_seen_at DESC, rowid DESC LIMIT ?)""",
        (alert_id, alert_id, MAX_ALERT_JOB_ROWS),
    )


def process_monitor_run(
    connection: sqlite3.Connection,
    monitor: sqlite3.Row,
    record: dict[str, Any],
    notify: service.Notifier | None,
) -> str | None:
    """Monitor hook: list jobs this alert has not seen, notify and optionally import.

    The first run only records what is currently posted (the baseline), so an
    alert never floods the user with the whole backlog. Later runs treat any
    job key not seen before as new. Auto-import saves a new job only when its
    match score reaches the alert's threshold; a job that cannot be scored is
    listed but never imported. Returns ``None`` when the monitor is not an alert.
    """
    alert = connection.execute(
        "SELECT * FROM careers_alerts WHERE monitor_id = ?", (monitor["id"],)
    ).fetchone()
    if alert is None:
        return None
    moment = store.now_utc()
    fallback = (
        store.from_iso(record["created_at"]) if record.get("created_at") else moment
    )
    jobs = normalize_results(record.get("result"), fallback)
    alert_id = alert["id"]
    seen = {
        row[0]
        for row in connection.execute(
            "SELECT job_key FROM careers_alert_jobs WHERE alert_id = ?", (alert_id,)
        ).fetchall()
    }
    fresh = [job for job in jobs if job["key"] not in seen]
    baseline = not alert["baselined"]
    stamp = store.to_iso(moment)
    if baseline:
        for job in fresh:
            connection.execute(
                "INSERT OR IGNORE INTO careers_alert_jobs (alert_id, job_key, first_seen_at, "
                "baseline, record_id) VALUES (?, ?, ?, 1, ?)",
                (alert_id, job["key"], stamp, record["id"]),
            )
        connection.execute(
            "UPDATE careers_alerts SET baselined = 1, last_found = 0, last_imported = 0 WHERE id = ?",
            (alert_id,),
        )
        _prune_alert_jobs(connection, alert_id)
        connection.commit()
        return f"Baseline captured ({len(jobs)} current jobs); new postings will be listed from the next run"
    annotate_jobs(connection, fresh)
    for job in fresh:
        match = job.get("match") or {}
        connection.execute(
            "INSERT OR IGNORE INTO careers_alert_jobs (alert_id, job_key, first_seen_at, baseline, "
            "record_id, job_json, match_score, match_class) VALUES (?, ?, ?, 0, ?, ?, ?, ?)",
            (
                alert_id,
                job["key"],
                stamp,
                record["id"],
                json.dumps(_storable(job), ensure_ascii=False),
                match.get("score"),
                str(match.get("classification") or ""),
            ),
        )
    connection.commit()
    imported = _auto_import(connection, alert, fresh) if alert["auto_import"] else []
    connection.execute(
        "UPDATE careers_alerts SET last_found = ?, last_imported = ? WHERE id = ?",
        (len(fresh), len(imported), alert_id),
    )
    _prune_alert_jobs(connection, alert_id)
    connection.commit()
    summary = _alert_summary(len(fresh), len(imported), len(jobs))
    if fresh and alert["notify"] and notify is not None:
        notify(
            f"{len(fresh)} new job{'' if len(fresh) == 1 else 's'}: {alert['name']}",
            _notification_body(fresh, imported),
            f"#/careers?tab=alerts&alert={alert_id}",
        )
    return summary


def _storable(job: Mapping[str, Any]) -> dict[str, Any]:
    """A Job without the per-read annotations (they are recomputed when read)."""
    return {
        key: value
        for key, value in job.items()
        if key not in {"already_in_aira", "aira_job_key", "match"}
    }


def _alert_summary(found: int, imported: int, total: int) -> str:
    if not found:
        return f"No new jobs ({total} current)"
    text = f"{found} new job{'' if found == 1 else 's'} of {total}"
    return text + (f", {imported} imported" if imported else "")


def _notification_body(fresh: list[dict[str, Any]], imported: list[str]) -> str:
    names = [
        f"{job['title']} at {job['company']}" if job["company"] else job["title"]
        for job in fresh[:3]
    ]
    body = "; ".join(names)
    if len(fresh) > 3:
        body += f"; and {len(fresh) - 3} more"
    if imported:
        body += f". {len(imported)} imported into Jobs."
    return body


def _auto_import(
    connection: sqlite3.Connection, alert: sqlite3.Row, fresh: list[dict[str, Any]]
) -> list[str]:
    """Import new jobs scoring at least the alert's threshold. Never applies."""
    gateway = get_gateway()
    if gateway is None:
        return []
    imported: list[str] = []
    for job in fresh:
        match = job.get("match") or {}
        score = match.get("score")
        if (
            job["already_in_aira"]
            or not job["importable"]
            or score is None
            or score < alert["min_score"]
        ):
            continue
        try:
            outcome = import_job(connection, job)
        except (ValueError, RuntimeError, OSError):
            logger.warning(
                "Alert auto-import skipped %r", job["title"][:80], exc_info=True
            )
            continue
        job_id = str(outcome["job"].get("job_id") or "")
        if job_id:
            imported.append(job_id)
            connection.execute(
                "UPDATE careers_alert_jobs SET imported_job_id = ? WHERE alert_id = ? AND job_key = ?",
                (job_id, alert["id"], job["key"]),
            )
            # Release the write lock before the next import writes through its own connection.
            connection.commit()
    return imported


def run_alert(
    connection: sqlite3.Connection,
    client: Any,
    alert_id: str,
    *,
    notify: service.Notifier | None = None,
    limits: service.Limits | None = None,
) -> dict[str, Any]:
    """Run an alert's search now (costs one search; never served from cache)."""
    row = _alert_row(connection, alert_id)
    if (
        not row["monitor_id"]
        or store.get_monitor(connection, row["monitor_id"]) is None
    ):
        raise ValueError("This alert has no monitor. Turn it back on to recreate it.")
    outcome = service.run_monitor(
        connection, client, row["monitor_id"], notify=notify, limits=limits
    )
    detail = alert_detail(connection, alert_id)
    return {
        "alert": detail,
        "summary": outcome["summary"],
        "record_id": outcome["record"]["id"],
        "credits": outcome["record"]["credits"],
        "found": detail["last_found"],
        "imported": detail["last_imported"],
    }


service.register_monitor_hook(process_monitor_run)
