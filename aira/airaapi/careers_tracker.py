"""Careers: the money end of a job search and the records a job seeker keeps.

* **Offers.** One offer per job: base pay (a year, a month or an hour), bonus, equity a year, a sign-on
  payment and the yearly value of benefits. Every offer is turned into the same two numbers (first-year
  pay and pay a year after that) so offers can be compared side by side, against each other and against
  the salary the person said they want. Different currencies are never added together.
* **Negotiation.** A counter-offer email built only from facts the person entered (the offer, their
  expectation, another offer if they choose to mention it). The writing model may polish it; without one
  a clear template is used. Nothing is sent: the person copies it.
* **Job search log.** A CSV of every application (date, company, role, how it was sent, status, contact,
  link), the record many unemployment offices ask for as proof of work search.
* **Jobs added by hand.** A job heard about from a friend, a sign in a window or a phone call has no
  posting URL; it is kept under the ``manual`` source with a placeholder address that never resolves.

The routes live in ``auto_apply_assistant.py`` (job notes, job generate, jobs list and import), so the
hosted build's existing permissions cover them.
"""

from __future__ import annotations

import csv
import io
import re
import sqlite3
import uuid
from datetime import date, datetime, timezone
from typing import Any, Callable, Mapping

OFFER_STATUSES = ("considering", "negotiating", "accepted", "declined", "expired")
CLOSED_OFFER_STATUSES = (
    "declined",
    "expired",
)  # no longer on the table: never "best", never a lever
PAY_PERIODS = ("year", "month", "hour")
MANUAL_SOURCE = "manual"
MANUAL_URL_PREFIX = "https://manual.aira.invalid/jobs/"
CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
MAX_AMOUNT = 100_000_000
NEGOTIATION_RAISE = (
    0.08  # the default ask when nothing better is known: 8% above the offered base
)

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS job_offers (
        job_id TEXT PRIMARY KEY,
        currency TEXT NOT NULL DEFAULT 'USD',
        base_salary REAL NOT NULL DEFAULT 0,
        pay_period TEXT NOT NULL DEFAULT 'year',
        hours_per_week REAL NOT NULL DEFAULT 40,
        bonus REAL NOT NULL DEFAULT 0,
        equity_per_year REAL NOT NULL DEFAULT 0,
        sign_on REAL NOT NULL DEFAULT 0,
        benefits REAL NOT NULL DEFAULT 0,
        start_date TEXT NOT NULL DEFAULT '',
        deadline TEXT NOT NULL DEFAULT '',
        status TEXT NOT NULL DEFAULT 'considering',
        notes TEXT NOT NULL DEFAULT '',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
)


def ensure(connection: sqlite3.Connection) -> None:
    for statement in SCHEMA:
        connection.execute(statement)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# validation
# ---------------------------------------------------------------------------


def _amount(value: Any, label: str, *, required: bool = False) -> float:
    if value in (None, ""):
        if required:
            raise ValueError(f"Enter the {label}.")
        return 0.0
    text = str(value).strip().replace(",", "").replace(" ", "")
    text = re.sub(r"^[^\d.-]+", "", text)  # a currency sign typed in front: "$85000"
    try:
        number = float(text)
    except ValueError as error:
        raise ValueError(f"The {label} must be a number, like 85000.") from error
    if number != number or number < 0:  # NaN or negative
        raise ValueError(f"The {label} cannot be negative.")
    if number > MAX_AMOUNT:
        raise ValueError(f"The {label} is too large.")
    if required and number <= 0:
        raise ValueError(f"Enter the {label}.")
    return round(number, 2)


def _day(value: Any, label: str) -> str:
    text = str(value or "").strip()[:10]
    if not text:
        return ""
    try:
        return date.fromisoformat(text).isoformat()
    except ValueError as error:
        raise ValueError(f"The {label} must be a date (YYYY-MM-DD).") from error


def clean_offer(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate what a person typed into the offer form; raise ``ValueError`` with a sentence they can act on."""
    if not isinstance(payload, Mapping):
        raise ValueError("Send the offer as an object.")
    currency = str(payload.get("currency") or "USD").strip().upper()
    if not CURRENCY_RE.fullmatch(currency):
        raise ValueError("The currency must be a three-letter code, like USD or INR.")
    period = str(payload.get("pay_period") or "year").strip().lower()
    if period not in PAY_PERIODS:
        raise ValueError("Pay is a year, a month or an hour.")
    hours = _amount(payload.get("hours_per_week"), "hours a week") or 40.0
    if hours > 100:
        raise ValueError("Hours a week must be 100 or fewer.")
    status = str(payload.get("status") or "considering").strip().lower()
    if status not in OFFER_STATUSES:
        raise ValueError(
            "Choose a status: considering, negotiating, accepted, declined or expired."
        )
    return {
        "currency": currency,
        "base_salary": _amount(payload.get("base_salary"), "base pay", required=True),
        "pay_period": period,
        "hours_per_week": hours,
        "bonus": _amount(payload.get("bonus"), "bonus"),
        "equity_per_year": _amount(payload.get("equity_per_year"), "equity a year"),
        "sign_on": _amount(payload.get("sign_on"), "sign-on payment"),
        "benefits": _amount(payload.get("benefits"), "benefits value"),
        "start_date": _day(payload.get("start_date"), "start date"),
        "deadline": _day(payload.get("deadline"), "answer-by date"),
        "status": status,
        "notes": str(payload.get("notes") or "").strip()[:4000],
    }


def annual_base(offer: Mapping[str, Any]) -> float:
    base = float(offer.get("base_salary") or 0)
    period = offer.get("pay_period") or "year"
    if period == "month":
        return round(base * 12, 2)
    if period == "hour":
        return round(base * float(offer.get("hours_per_week") or 40) * 52, 2)
    return round(base, 2)


def with_totals(offer: Mapping[str, Any]) -> dict[str, Any]:
    """The offer plus the numbers people compare: base a year, pay a year, first-year pay."""
    out = dict(offer)
    base = annual_base(offer)
    yearly = (
        base
        + float(offer.get("bonus") or 0)
        + float(offer.get("equity_per_year") or 0)
        + float(offer.get("benefits") or 0)
    )
    out["annual_base"] = round(base, 2)
    out["yearly_total"] = round(yearly, 2)
    out["first_year_total"] = round(yearly + float(offer.get("sign_on") or 0), 2)
    return out


# ---------------------------------------------------------------------------
# store
# ---------------------------------------------------------------------------


def get_offer(connection: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    ensure(connection)
    row = connection.execute(
        """SELECT job_offers.*, jobs.title, jobs.company FROM job_offers
        LEFT JOIN jobs ON jobs.job_id = job_offers.job_id WHERE job_offers.job_id = ?""",
        (job_id,),
    ).fetchone()
    return with_totals(dict(row)) if row else None


def save_offer(
    connection: sqlite3.Connection, job_id: str, payload: Mapping[str, Any]
) -> dict[str, Any]:
    ensure(connection)
    values = clean_offer(payload)
    now = _now()
    columns = list(values)
    connection.execute(
        f"""INSERT INTO job_offers (job_id, {", ".join(columns)}, created_at, updated_at)
        VALUES (?, {", ".join("?" for _ in columns)}, ?, ?)
        ON CONFLICT(job_id) DO UPDATE SET {", ".join(f"{c} = excluded.{c}" for c in columns)}, updated_at = excluded.updated_at""",
        (job_id, *values.values(), now, now),
    )
    return get_offer(connection, job_id) or {}


def delete_offer(connection: sqlite3.Connection, job_id: str) -> bool:
    ensure(connection)
    return (
        connection.execute(
            "DELETE FROM job_offers WHERE job_id = ?", (job_id,)
        ).rowcount
        > 0
    )


def expectation(settings: Mapping[str, str]) -> dict[str, Any]:
    """The salary the person said they want (profile), as numbers."""

    def number(key: str) -> float | None:
        try:
            value = float(str(settings.get(key) or "").replace(",", ""))
        except ValueError:
            return None
        return value if value > 0 else None

    return {
        "min": number("application_salary_expectation_min"),
        "max": number("application_salary_expectation_max"),
        "currency": str(settings.get("application_salary_currency") or "").upper()
        or None,
    }


def compare(
    connection: sqlite3.Connection, settings: Mapping[str, str]
) -> dict[str, Any]:
    """Every offer with its job, best first, and how each compares with the best and with the expectation."""
    ensure(connection)
    rows = connection.execute(
        """SELECT job_offers.*, jobs.title, jobs.company, jobs.location, jobs.stage
        FROM job_offers JOIN jobs ON jobs.job_id = job_offers.job_id
        WHERE jobs.archived_at IS NULL"""
    ).fetchall()
    offers = [with_totals(dict(row)) for row in rows]
    offers.sort(key=lambda o: (-o["first_year_total"], o["company"] or ""))
    wanted = expectation(settings)
    currencies: dict[str, int] = {}
    for offer in offers:
        if offer["status"] not in CLOSED_OFFER_STATUSES:
            currencies[offer["currency"]] = currencies.get(offer["currency"], 0) + 1
    main_currency = (
        max(currencies, key=lambda c: (currencies[c], c)) if currencies else None
    )
    open_offers = [
        o
        for o in offers
        if o["currency"] == main_currency and o["status"] not in CLOSED_OFFER_STATUSES
    ]
    best = open_offers[0] if open_offers else None
    for offer in offers:
        offer["best"] = best is not None and offer["job_id"] == best["job_id"]
        offer["open"] = offer["status"] not in CLOSED_OFFER_STATUSES
        # A declined or expired offer is not compared with the best one: it is no longer there to take.
        offer["vs_best"] = (
            round(offer["first_year_total"] - best["first_year_total"], 2)
            if best and offer["open"] and offer["currency"] == best["currency"]
            else None
        )
        same_currency = wanted["currency"] in (None, offer["currency"])
        offer["vs_expectation"] = (
            round(offer["annual_base"] - wanted["min"], 2)
            if wanted["min"] and same_currency
            else None
        )
        offer["meets_expectation"] = (
            offer["annual_base"] >= wanted["min"]
            if wanted["min"] and same_currency
            else None
        )
    return {
        "offers": offers,
        "best_job_id": best["job_id"] if best else None,
        "currency": main_currency,
        "expectation": wanted,
    }


# ---------------------------------------------------------------------------
# negotiation
# ---------------------------------------------------------------------------


def _round_ask(value: float, period: str) -> float:
    if period == "hour":
        return round(value * 2) / 2  # half a unit an hour
    step = 100 if period == "month" else 1000
    return float(round(value / step) * step)


def money(value: float | None, currency: str) -> str:
    if value is None:
        return ""
    whole = abs(value - round(value)) < 0.005
    text = f"{value:,.0f}" if whole else f"{value:,.2f}"
    return f"{currency} {text}"


def _per(period: str) -> str:
    return {"year": "a year", "month": "a month", "hour": "an hour"}.get(period, "")


def suggest_ask(
    offer: Mapping[str, Any],
    wanted: Mapping[str, Any],
    others: list[Mapping[str, Any]],
) -> tuple[float, list[str]]:
    """The base pay to ask for (in the offer's own period) and why, from facts only."""
    period = offer.get("pay_period") or "year"
    base = float(offer.get("base_salary") or 0)
    factor = {
        "year": 1.0,
        "month": 12.0,
        "hour": float(offer.get("hours_per_week") or 40) * 52,
    }[period]
    reasons: list[str] = []
    candidates: list[float] = [base * (1 + NEGOTIATION_RAISE)]
    reasons.append(
        "Asking for about 8% more than the offer is a common, polite starting point."
    )
    currency = offer.get("currency")
    if wanted.get("max") and wanted.get("currency") in (None, currency):
        target = float(wanted["max"]) / factor
        if target > base:
            candidates.append(target)
            reasons.append(
                f"Your profile says you are looking for up to {money(float(wanted['max']), str(currency))} a year."
            )
    higher = [
        o
        for o in others
        if o.get("currency") == currency
        and o.get("job_id") != offer.get("job_id")
        and o.get("status") not in CLOSED_OFFER_STATUSES
        and annual_base(o) > annual_base(offer)
    ]
    if higher:
        top = max(higher, key=annual_base)
        candidates.append(annual_base(top) / factor)
        reasons.append(
            f"You have a higher base offer from {top.get('company') or 'another employer'} ({money(annual_base(top), str(currency))} a year)."
        )
    ask = _round_ask(max(candidates), period)
    if ask <= base:
        ask = _round_ask(base * (1 + NEGOTIATION_RAISE), period)
    return ask, reasons


def negotiation_draft(
    job: Mapping[str, Any],
    offer: Mapping[str, Any],
    *,
    name: str,
    wanted: Mapping[str, Any],
    others: list[Mapping[str, Any]],
    ask: Any = None,
    mention_other_offer: bool = False,
    writer: Callable[[str, Mapping[str, Any]], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """A counter-offer email the person edits and sends themselves."""
    period = offer.get("pay_period") or "year"
    currency = str(offer.get("currency") or "USD")
    suggested, reasons = suggest_ask(offer, wanted, others)
    asked = _amount(ask, "amount to ask for") if ask not in (None, "") else suggested
    if asked <= 0:
        asked = suggested
    role = str(job.get("title") or "the role")
    company = str(job.get("company") or "your team")
    other_line = ""
    if mention_other_offer:
        higher = [
            o
            for o in others
            if o.get("currency") == currency
            and o.get("job_id") != offer.get("job_id")
            and o.get("status") not in CLOSED_OFFER_STATUSES
            and annual_base(o) > annual_base(offer)
        ]
        if higher:
            other_line = " I have another offer with a higher base salary, but I would much rather join you."
    deadline = str(offer.get("deadline") or "")
    subject = f"{role} offer: a question about the salary"
    body = (
        f"Hello,\n\n"
        f"Thank you very much for the offer for the {role} position at {company}. "
        f"I am excited about the role and the team.\n\n"
        f"The offer is {money(float(offer.get('base_salary') or 0), currency)} {_per(period)}. "
        f"Based on the scope of the role and my experience, I would like to ask whether you can bring the base pay to "
        f"{money(asked, currency)} {_per(period)}.{other_line} "
        f"If we can agree on that, I am ready to accept{' before ' + deadline if deadline else ''}.\n\n"
        f"Thank you again for your time and for considering this.\n\n"
        f"Best regards,\n{name or ''}".rstrip()
    )
    fallback = {"subject": subject, "body": body}
    result: dict[str, Any] = {**fallback, "source": "template"}
    if writer is not None:
        prompt = (
            "Rewrite this salary negotiation email so it is warm, short and professional. "
            "Keep every number, name and date exactly as written; add no new facts, no new claims and no other offers. "
            'Answer with JSON: {"subject": "...", "body": "..."}.\n\n'
            f"Subject: {subject}\n\n{body}"
        )
        drafted = writer(prompt, fallback)
        if _keeps_facts(drafted, asked, currency):
            result = drafted
    return {
        **result,
        "ask": asked,
        "suggested_ask": suggested,
        "currency": currency,
        "pay_period": period,
        "reasons": reasons,
    }


def _keeps_facts(drafted: Mapping[str, Any], asked: float, currency: str) -> bool:
    """A model's version is used only if it still names the amount asked for."""
    body = str(drafted.get("body") or "")
    return bool(body.strip()) and money(asked, currency).split(" ", 1)[1] in body


# ---------------------------------------------------------------------------
# job search log (CSV)
# ---------------------------------------------------------------------------

LOG_COLUMNS = (
    "Date applied",
    "Company",
    "Job title",
    "Location",
    "Status",
    "How you applied",
    "Confirmation",
    "Source",
    "Link",
    "Contacts",
    "Interviews",
    "Offer (first-year pay)",
    "Last update",
    "Notes",
)
PRE_APPLICATION = ("discovered", "saved", "ready_to_apply", "needs_review")
OFFER_CLOSED_LABELS = {"declined": "Offer declined", "expired": "Offer expired"}


def _cell(value: Any) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # A spreadsheet must never run a cell as a formula.
    if text[:1] in ("=", "+", "-", "@", "\t"):
        text = "'" + text
    return text


def _label(stage: str) -> str:
    return stage.replace("_", " ").capitalize()


def job_search_log(
    connection: sqlite3.Connection, *, scope: str = "applications"
) -> str:
    """CSV text: one row per application (``scope="applications"``) or per tracked job (``"all"``)."""
    ensure(connection)
    if scope not in ("applications", "all"):
        raise ValueError("Export applications or all jobs.")
    where = "jobs.archived_at IS NULL"
    if scope == "applications":
        where += " AND jobs.stage NOT IN ('discovered', 'saved', 'ready_to_apply', 'needs_review')"
    rows = connection.execute(
        f"""SELECT jobs.job_id, jobs.title, jobs.company, jobs.location, jobs.stage, jobs.source, jobs.job_url,
            jobs.application_url, jobs.applied_at, jobs.updated_at, jobs.notes,
            s.submitted_at, s.method, s.confirmation_number, s.confirmation_text,
            (SELECT COUNT(*) FROM interviews i WHERE i.job_id = jobs.job_id AND i.status != 'cancelled') AS interviews,
            (SELECT GROUP_CONCAT(DISTINCT r.name) FROM recruiters r JOIN recruiter_interactions x ON x.recruiter_id = r.recruiter_id
                WHERE x.job_id = jobs.job_id AND r.deleted_at IS NULL) AS contacts
        FROM jobs LEFT JOIN application_submissions s ON s.job_id = jobs.job_id
        WHERE {where}
        ORDER BY COALESCE(s.submitted_at, jobs.applied_at, jobs.updated_at) DESC"""
    ).fetchall()
    out = io.StringIO()
    writer = csv.writer(out, lineterminator="\r\n")
    writer.writerow(LOG_COLUMNS)
    for row in rows:
        offer = get_offer(connection, row["job_id"])
        applied = str(row["submitted_at"] or row["applied_at"] or "")[:10]
        how = ""
        text = str(row["confirmation_text"] or "")
        if text.startswith("Applied "):
            how = text.split(".", 1)[0]
        elif row["method"] == "auto":
            how = "Monosage applied for you"
        elif row["method"]:
            how = "Online application"
        link = str(row["application_url"] or row["job_url"] or "")
        if link.startswith(MANUAL_URL_PREFIX):
            link = ""
        writer.writerow(
            [
                _cell(applied),
                _cell(row["company"]),
                _cell(row["title"]),
                _cell(row["location"]),
                _cell(
                    OFFER_CLOSED_LABELS[offer["status"]]
                    if offer and offer["status"] in OFFER_CLOSED_LABELS
                    else _label(row["stage"])
                ),
                _cell(how),
                _cell(row["confirmation_number"]),
                _cell(
                    "Added by you"
                    if row["source"] == MANUAL_SOURCE
                    else str(row["source"] or "").replace("_", " ")
                ),
                _cell(link),
                _cell((row["contacts"] or "").replace(",", ", ")),
                _cell(row["interviews"]),
                _cell(
                    money(offer["first_year_total"], offer["currency"]) if offer else ""
                ),
                _cell(str(row["updated_at"] or "")[:10]),
                _cell((row["notes"] or "")[:500]),
            ]
        )
    return "﻿" + out.getvalue()  # a byte-order mark so spreadsheet apps read UTF-8


# ---------------------------------------------------------------------------
# jobs added by hand
# ---------------------------------------------------------------------------


MANUAL_FIELDS = (
    "title",
    "company",
    "location",
    "url",
    "description",
    "workplace_type",
    "employment_type",
    "salary_min",
    "salary_max",
    "currency",
)


def _text(payload: Mapping[str, Any], key: str, label: str) -> str:
    """A text field: absent or null is empty; a list, an object or a number is refused (not stored as "['a']")."""
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"The {label} must be text.")
    return value


def _manual_values(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate the fields of a job typed in by hand (every field; absent ones are empty)."""
    if not isinstance(payload, Mapping):
        raise ValueError("Send the job as an object.")
    title = re.sub(r"\s+", " ", _text(payload, "title", "job title")).strip()
    company = re.sub(r"\s+", " ", _text(payload, "company", "company")).strip()
    if not title:
        raise ValueError("Enter the job title.")
    if not company:
        raise ValueError("Enter the company or employer.")
    if len(title) > 200 or len(company) > 160:
        raise ValueError("The job title or company name is too long.")
    location = _text(payload, "location", "location").strip()[:200]
    description = _text(payload, "description", "description").strip()[:50000]
    url = _text(payload, "url", "link").strip()
    if url and (len(url) > 1000 or not re.match(r"^https?://[^\s/]+", url)):
        raise ValueError("The link must start with http:// or https://.")
    if url.startswith(MANUAL_URL_PREFIX):
        url = ""  # the placeholder of a job without a link is not a link
    workplace = _text(payload, "workplace_type", "workplace").strip().lower()
    if workplace and workplace not in ("remote", "hybrid", "on_site"):
        raise ValueError("Workplace is remote, hybrid or on-site.")
    employment = _text(payload, "employment_type", "employment type").strip().lower()
    if employment and employment not in (
        "full_time",
        "part_time",
        "contract",
        "temporary",
        "internship",
    ):
        raise ValueError("Choose an employment type from the list.")
    salary_min = _amount(payload.get("salary_min"), "lowest pay") or None
    salary_max = _amount(payload.get("salary_max"), "highest pay") or None
    if salary_min and salary_max and salary_min > salary_max:
        raise ValueError("The lowest pay cannot be more than the highest pay.")
    currency = _text(payload, "currency", "currency").strip().upper() or None
    if currency and not CURRENCY_RE.fullmatch(currency):
        raise ValueError("The currency must be a three-letter code, like USD or INR.")
    return {
        "title": title,
        "company": company,
        "location": location,
        "url": url,
        "description": description,
        "workplace_type": workplace or None,
        "employment_type": employment or None,
        "salary_min": salary_min,
        "salary_max": salary_max,
        "currency": currency if (salary_min or salary_max) else None,
    }


def manual_job(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Validate a job typed in by hand; returns the keyword arguments for the job store."""
    values = _manual_values(payload)
    external_id = uuid.uuid4().hex
    source_url = values.pop("url") or MANUAL_URL_PREFIX + external_id
    return {
        "external_id": external_id,
        "source": MANUAL_SOURCE,
        "source_url": source_url,
        "application_url": source_url,
        **values,
        "application_method": "manual",
    }


def manual_edit(
    payload: Mapping[str, Any], current: Mapping[str, Any]
) -> dict[str, Any]:
    """The columns to change on a job added by hand: the fields sent replace the current ones.

    Returns ``title``, ``company``, ``location``, ``description``, ``workplace_type``, ``employment_type``,
    ``salary_min``, ``salary_max``, ``currency`` and ``url`` (the job's address: the link typed, or the
    placeholder of a job without one).
    """
    if not isinstance(payload, Mapping):
        raise ValueError("Send the job as an object.")
    unknown = sorted(set(payload) - set(MANUAL_FIELDS))
    if unknown:
        raise ValueError(f"These cannot be changed here: {', '.join(unknown)}.")
    link = str(current.get("job_url") or "")
    merged: dict[str, Any] = {
        "title": current.get("title") or "",
        "company": current.get("company") or "",
        "location": current.get("location") or "",
        "url": "" if link.startswith(MANUAL_URL_PREFIX) else link,
        "description": current.get("description") or "",
        "workplace_type": current.get("workplace_type") or None,
        "employment_type": current.get("employment_type") or None,
        "salary_min": current.get("salary_min"),
        "salary_max": current.get("salary_max"),
        "currency": current.get("currency") or None,
        **payload,
    }
    values = _manual_values(merged)
    values["url"] = values["url"] or MANUAL_URL_PREFIX + str(
        current.get("external_id") or uuid.uuid4().hex
    )
    return values


def has_posting(url: str | None) -> bool:
    """False for the placeholder address of a job added by hand without a link."""
    return bool(url) and not str(url).startswith(MANUAL_URL_PREFIX)
