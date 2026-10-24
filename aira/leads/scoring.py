"""Ideal-customer-profile (ICP) scoring: a transparent 0-100 fit score with the reasons behind it.

The score is rules you can read, not a model: each rule that fires adds points and says so. With no ICP
saved, a lead has no score (``None``) rather than an invented one.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from typing import Any

from aira.leads import intent, store

WEIGHTS = {
    "title": 30,
    "keywords": 15,
    "industry": 10,
    "location": 15,
    "contact": 15,
    "signals": 10,
    "rating": 5,
    "need": 20,
}
MAX_VALUES = 20
REQUIRE = ("website", "phone", "email", "linkedin")
SETTINGS_TABLE = "lead_settings"


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        f"CREATE TABLE IF NOT EXISTS {SETTINGS_TABLE} (key TEXT PRIMARY KEY, value TEXT NOT NULL)"
    )


def _values(raw: Any, *, limit: int = 80) -> list[str]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        items = re.split(r"[\n,;]+", raw)
    elif isinstance(raw, Sequence):
        items = [str(i) for i in raw]
    else:
        raise ValueError("ICP lists must be lists of text.")
    out: list[str] = []
    for item in items:
        text = store.clean(item, limit)
        if text and text.casefold() not in {o.casefold() for o in out}:
            out.append(text)
    return out[:MAX_VALUES]


def normalize_icp(raw: Mapping[str, Any]) -> dict[str, Any]:
    require = [r for r in _values(raw.get("require")) if r in REQUIRE]
    try:
        min_rating = (
            float(raw["min_rating"])
            if raw.get("min_rating") not in (None, "")
            else None
        )
    except (TypeError, ValueError):
        raise ValueError("min_rating must be a number from 0 to 5.") from None
    if min_rating is not None and not 0 <= min_rating <= 5:
        raise ValueError("min_rating must be a number from 0 to 5.")
    services = [s.lower() for s in _values(raw.get("services"))]
    unknown = [s for s in services if s not in intent.SERVICES]
    if unknown:
        raise ValueError(
            f"Unknown service: {unknown[0]}. Choose from {', '.join(intent.SERVICE_IDS)}."
        )
    return {
        "services": services,
        "titles": _values(raw.get("titles")),
        "keywords": _values(raw.get("keywords")),
        "industries": _values(raw.get("industries")),
        "locations": _values(raw.get("locations")),
        "exclude_keywords": _values(raw.get("exclude_keywords")),
        "exclude_companies": _values(raw.get("exclude_companies")),
        "require": require,
        "min_rating": min_rating,
    }


def is_empty(icp: Mapping[str, Any]) -> bool:
    return not any(
        icp.get(key)
        for key in (
            "titles",
            "keywords",
            "industries",
            "locations",
            "require",
            "min_rating",
            "services",
        )
    )


def get_icp(connection: sqlite3.Connection) -> dict[str, Any]:
    row = connection.execute(
        f"SELECT value FROM {SETTINGS_TABLE} WHERE key = ?", (store.ICP_KEY,)
    ).fetchone()
    try:
        raw = json.loads(row[0]) if row else {}
    except ValueError:
        raw = {}
    return normalize_icp(raw if isinstance(raw, dict) else {})


def save_icp(connection: sqlite3.Connection, raw: Mapping[str, Any]) -> dict[str, Any]:
    icp = normalize_icp(raw)
    connection.execute(
        f"INSERT INTO {SETTINGS_TABLE} (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (store.ICP_KEY, json.dumps(icp, ensure_ascii=False)),
    )
    connection.commit()
    return icp


def _contains(text: str, needle: str) -> bool:
    return (
        re.search(
            rf"(?<![a-z0-9]){re.escape(needle.casefold())}(?![a-z0-9])", text.casefold()
        )
        is not None
    )


def score_lead(
    lead: Mapping[str, Any], icp: Mapping[str, Any]
) -> tuple[int | None, list[str]]:
    """``(score, reasons)``. ``score`` is None when there is no ICP to score against."""
    if is_empty(icp):
        return None, []
    reasons: list[str] = []
    points = 0
    possible = 0
    title = str(lead.get("title") or "")
    company = str(lead.get("company") or lead.get("name") or "")
    blob = " ".join(
        str(lead.get(k) or "")
        for k in ("title", "company", "industry", "name", "notes")
    )

    excluded_company = next(
        (
            c
            for c in icp.get("exclude_companies") or []
            if c.casefold() in company.casefold()
        ),
        "",
    )
    if excluded_company:
        return 0, [f"Excluded company: {excluded_company}"]
    excluded = next(
        (w for w in icp.get("exclude_keywords") or [] if _contains(blob, w)), ""
    )
    if excluded:
        return 0, [f"Mentions an excluded word: {excluded}"]

    if icp.get("titles") and lead.get("kind") == "person":
        possible += WEIGHTS["title"]
        hit = next((t for t in icp["titles"] if _contains(title, t)), "")
        if hit:
            points += WEIGHTS["title"]
            reasons.append(f"Title matches {hit} (+{WEIGHTS['title']})")
        else:
            reasons.append("Title does not match your target titles")
    if icp.get("keywords"):
        possible += WEIGHTS["keywords"]
        hits = [k for k in icp["keywords"] if _contains(blob, k)]
        if hits:
            earned = round(
                WEIGHTS["keywords"]
                * min(1.0, len(hits) / max(1, min(2, len(icp["keywords"]))))
            )
            points += earned
            reasons.append(f"Mentions {', '.join(hits[:3])} (+{earned})")
    if icp.get("industries"):
        possible += WEIGHTS["industry"]
        industry = str(lead.get("industry") or "") + " " + company
        hit = next((i for i in icp["industries"] if _contains(industry, i)), "")
        if hit:
            points += WEIGHTS["industry"]
            reasons.append(f"Industry matches {hit} (+{WEIGHTS['industry']})")
    if icp.get("locations"):
        possible += WEIGHTS["location"]
        place = str(lead.get("location") or "")
        hit = next((loc for loc in icp["locations"] if _contains(place, loc)), "")
        if hit:
            points += WEIGHTS["location"]
            reasons.append(f"Located in {hit} (+{WEIGHTS['location']})")
        elif place:
            reasons.append(f"Located in {place}, outside your target areas")
    # contactability: how many ways there are to reach them
    channels = {
        "website": bool(lead.get("website")),
        "phone": bool(lead.get("phone")),
        "email": bool(lead.get("email")),
        "linkedin": bool(lead.get("linkedin_url") or lead.get("sales_nav_url")),
        "post": any(
            isinstance(s, Mapping) and s.get("type") == "request" and s.get("url")
            for s in (lead.get("signals") or [])
        ),
    }
    possible += WEIGHTS["contact"]
    reachable = [name for name, ok in channels.items() if ok]
    if reachable:
        earned = round(WEIGHTS["contact"] * min(1.0, len(reachable) / 2))
        points += earned
        reasons.append(f"Reachable by {', '.join(reachable)} (+{earned})")
    missing = [r for r in icp.get("require") or [] if not channels.get(r)]
    if missing:
        reasons.append(f"Missing required: {', '.join(missing)}")
        points = int(points * 0.5)
    if icp.get("services"):
        possible += WEIGHTS["need"]
        needs = intent.needed_services(lead, icp["services"])
        if needs:
            points += WEIGHTS["need"]
            label = intent.SERVICES[needs[0][0]]["label"]
            reasons.append(f"Needs {label}: {needs[0][1][:70]} (+{WEIGHTS['need']})")
    signals = lead.get("signals") or []
    strong = [
        s
        for s in signals
        if isinstance(s, Mapping) and s.get("type") in {"hiring", "news"}
    ]
    possible += WEIGHTS["signals"]
    if strong:
        points += WEIGHTS["signals"]
        reasons.append(
            f"Buying signal: {strong[0].get('text', '')[:60]} (+{WEIGHTS['signals']})"
        )
    min_rating = icp.get("min_rating")
    if min_rating is not None and lead.get("rating") is not None:
        possible += WEIGHTS["rating"]
        if float(lead["rating"]) >= float(min_rating):
            points += WEIGHTS["rating"]
            reasons.append(f"Rated {lead['rating']} (+{WEIGHTS['rating']})")
        else:
            reasons.append(f"Rated {lead['rating']}, below your minimum {min_rating}")
    score = round(100 * points / possible) if possible else 0
    return max(0, min(100, score)), reasons
