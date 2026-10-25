"""Careers reports: company intelligence, salary, trends, interview prep, travel, learning.

A *report* is a small, fixed plan of SerpApi engine calls. Each section is
exactly one recorded engine call (history source ``intel``), the sections run
concurrently with at most four in flight, a section that fails does not stop the
others, and the report states what it cost: live credits and cache hits (a
section answered from the local cache is free and says so). A dry run returns
the plan and its cost without calling SerpApi at all.

Section items are a deliberately thin view of the engine response (title,
subtitle, link, thumbnail and a few ``meta`` scalars) so a report stays small;
the full response is one click away through ``record_id`` (API history) or
the section's ``params`` (open it in the Playground).

Nothing here fabricates data: every item comes from a field of the stored
response, derived numbers (trend averages, salary medians) are computed from
those fields and labelled as computed, and an engine that returns nothing
produces an ``empty`` section, not a filler.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import statistics
import threading
from collections.abc import Callable, Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

from aira.airaapi import careers, catalog, service, store
from aira.airaapi.client import SerpApiError

logger = logging.getLogger("aira.airaapi.careers_intel")

KINDS = ("company-intel", "salary", "trends", "interview-prep", "travel", "learn")
MAX_WORKERS = 4
MAX_REPORTS = 100
ITEM_LIMIT = 8
TREND_WINDOWS = {
    "7d": "now 7-d",
    "30d": "today 1-m",
    "90d": "today 3-m",
    "12m": "today 12-m",
    "5y": "today 5-y",
    "all": "all",
}
LEVELS = ("beginner", "intermediate", "advanced")
CONTROL_FIELDS = frozenset({"kind", "dry_run", "sections"})
# Failures that mean the whole report cannot work, so they are raised, not reported per section.
FATAL_CODES = frozenset(
    {
        "not_configured",
        "invalid_api_key",
        "out_of_searches",
        "rate_limited",
        "local_rate_limit",
        "busy",
    }
)


@dataclass(frozen=True)
class SectionPlan:
    id: str
    title: str
    engine: str
    params: dict[str, Any]
    extractor: str
    why: str = ""


@dataclass(frozen=True)
class ReportSpec:
    kind: str
    title: str
    description: str
    input: dict[str, Any]
    plans: list[SectionPlan] = field(default_factory=list)


# ---------------------------------------------------------------------------
# plans
# ---------------------------------------------------------------------------


def _join(*parts: str) -> str:
    return " ".join(part for part in parts if part).strip()


def _company_plans(company: str, role: str, location: str) -> list[SectionPlan]:
    maps_params: dict[str, Any] = {"q": f"{company} office", "type": "search"}
    if location:
        maps_params["location"] = location
    jobs_params: dict[str, Any] = {"q": _join(company, role)}
    if location:
        jobs_params["location"] = location
    return [
        SectionPlan(
            "overview",
            "Company overview",
            "google",
            {"q": company},
            "web",
            "Knowledge panel and top web results.",
        ),
        SectionPlan(
            "news",
            "Latest news",
            "google_news",
            {"q": company},
            "news",
            "Recent coverage to mention in an interview or to spot risk.",
        ),
        SectionPlan(
            "jobs",
            "Open roles",
            "google_jobs",
            jobs_params,
            "jobs",
            "What the company is hiring for right now.",
        ),
        SectionPlan(
            "employees",
            "What employees say",
            "google_forums",
            {"q": f"{company} working at culture reviews"},
            "forums",
            "Forum and community threads about working there.",
        ),
        SectionPlan(
            "offices",
            "Offices nearby",
            "google_maps",
            maps_params,
            "places",
            "Where the company's offices are.",
        ),
    ]


def _salary_plans(role: str, location: str, company: str) -> list[SectionPlan]:
    market_jobs: dict[str, Any] = {"q": role}
    if location:
        market_jobs["location"] = location
    plans = [
        SectionPlan(
            "estimates",
            "Salary estimates",
            "google",
            {"q": _join(role, "salary", location)},
            "web",
            "Salary snippets and answer boxes from the web.",
        ),
        SectionPlan(
            "market",
            "Pay in current listings",
            "google_jobs",
            market_jobs,
            "jobs",
            "Pay ranges that employers published in open listings.",
        ),
    ]
    if company:
        plans.append(
            SectionPlan(
                "company_pay",
                f"Pay at {company}",
                "google_jobs",
                {"q": _join(role, company)},
                "jobs",
                "Listings for the same role at this company.",
            )
        )
    plans.append(
        SectionPlan(
            "community",
            "What people report",
            "google_forums",
            {"q": _join(role, "salary compensation", company or location)},
            "forums",
            "Community threads where people share offers and pay.",
        )
    )
    return plans


def _trend_plans(terms: list[str], geo: str, window: str) -> list[SectionPlan]:
    base: dict[str, Any] = {"date": TREND_WINDOWS[window]}
    if geo:
        base["geo"] = geo
    first = terms[0]
    return [
        SectionPlan(
            "interest",
            "Interest over time",
            "google_trends",
            {**base, "q": ",".join(terms), "data_type": "TIMESERIES"},
            "trend_series",
            "Relative search interest (0-100) for each term.",
        ),
        SectionPlan(
            "related_queries",
            f"Related searches for {first}",
            "google_trends",
            {**base, "q": first, "data_type": "RELATED_QUERIES"},
            "trend_related",
            "Top and rising related queries (first term only).",
        ),
        SectionPlan(
            "related_topics",
            f"Related topics for {first}",
            "google_trends",
            {**base, "q": first, "data_type": "RELATED_TOPICS"},
            "trend_related",
            "Top and rising related topics (first term only).",
        ),
        SectionPlan(
            "news",
            f"News about {first}",
            "google_news",
            {"q": first},
            "news",
            "Recent coverage of the first term.",
        ),
    ]


def _interview_plans(company: str, role: str) -> list[SectionPlan]:
    return [
        SectionPlan(
            "questions",
            "Interview questions",
            "google",
            {"q": f"{company} {role} interview questions"},
            "web",
            "Question lists and the questions people also ask.",
        ),
        SectionPlan(
            "experiences",
            "Candidate experiences",
            "google_forums",
            {"q": f"{company} {role} interview experience"},
            "forums",
            "First-hand accounts of the process.",
        ),
        SectionPlan(
            "videos",
            "Prep videos",
            "youtube",
            {"search_query": f"{company} {role} interview"},
            "videos",
            "Walkthroughs and mock interviews.",
        ),
        SectionPlan(
            "news",
            "Latest news",
            "google_news",
            {"q": company},
            "news",
            "Recent news to have a view on. Shared with company intelligence, so the second run is free.",
        ),
        SectionPlan(
            "posting",
            "What the job posts ask for",
            "google_jobs",
            {"q": _join(role, company)},
            "jobs",
            "Open postings for this role at this company.",
        ),
    ]


def _learn_plans(skill: str, level: str) -> list[SectionPlan]:
    return [
        SectionPlan(
            "courses",
            "Courses and guides",
            "google",
            {"q": _join("best", level, skill, "course tutorial")},
            "web",
            "Where to learn it.",
        ),
        SectionPlan(
            "videos",
            "Video tutorials",
            "youtube",
            {"search_query": _join(skill, level, "tutorial")},
            "videos",
            "Free video courses.",
        ),
        SectionPlan(
            "demand",
            "Jobs asking for it",
            "google_jobs",
            {"q": skill},
            "jobs",
            "Open listings and the pay they state.",
        ),
        SectionPlan(
            "advice",
            "Community advice",
            "google_forums",
            {"q": f"how to learn {skill} roadmap"},
            "forums",
            "Roadmaps and advice from people who learned it.",
        ),
    ]


def _name(
    payload: Mapping[str, Any], field_name: str, label: str, *, required: bool = True
) -> str:
    return careers.text_field(
        payload, field_name, required=required, limit=120, label=label
    )


def _terms(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("terms")
    if isinstance(raw, str):
        raw = raw.split(",")
    if not isinstance(raw, list):
        raise ValueError("Send terms as a list of up to 5 search terms.")
    terms: list[str] = []
    for item in raw:
        text = careers.clean_text(item, 81)
        if not text:
            continue
        if len(text) > 80 or "," in text:
            raise ValueError("Each term must be up to 80 characters with no commas.")
        if text.lower() not in {existing.lower() for existing in terms}:
            terms.append(text)
    if not terms:
        raise ValueError("Enter at least one term to compare.")
    if len(terms) > 5:
        raise ValueError("Google Trends compares at most 5 terms.")
    return terms


def _travel_spec(payload: Mapping[str, Any]) -> ReportSpec:
    travel_kind = careers.clean_text(payload.get("kind"), 20).lower()
    if travel_kind not in {"flights", "hotels"}:
        raise ValueError("kind must be flights or hotels.")
    params = {
        name: value
        for name, value in payload.items()
        if name not in CONTROL_FIELDS and value not in (None, "")
    }
    if travel_kind == "flights":
        for name in ("departure_id", "arrival_id", "outbound_date"):
            if not params.get(name):
                raise ValueError(f"Flights need {name}.")
        params.setdefault("type", "1" if params.get("return_date") else "2")
        title = f"Flights {params['departure_id']} to {params['arrival_id']}"
        plan = SectionPlan(
            "flights",
            "Flights",
            "google_flights",
            params,
            "flights",
            "Best and other itineraries with Google's price insight.",
        )
    else:
        for name in ("q", "check_in_date", "check_out_date"):
            if not params.get(name):
                raise ValueError(f"Hotels need {name}.")
        title = f"Hotels in {careers.clean_text(params['q'], 80)}"
        plan = SectionPlan(
            "hotels",
            "Hotels",
            "google_hotels",
            params,
            "hotels",
            "Stays with nightly and total rates.",
        )
    return ReportSpec(
        "travel",
        title,
        "Onsite interview travel: fares or stays for the dates you give.",
        {"kind": travel_kind},
        [plan],
    )


def build_spec(kind: str, payload: Mapping[str, Any]) -> ReportSpec:
    """Validate the form for ``kind`` and return the report plan (no network)."""
    if kind not in KINDS:
        raise LookupError(f"Unknown report kind {kind!r}.")
    spec: ReportSpec
    if kind == "company-intel":
        company = _name(payload, "company", "a company")
        role = _name(payload, "role", "the role", required=False)
        location = _name(payload, "location", "the location", required=False)
        spec = ReportSpec(
            kind,
            f"Company intelligence: {company}",
            "What a company is doing, hiring for and how people describe working there.",
            {"company": company, "role": role, "location": location},
            _company_plans(company, role, location),
        )
    elif kind == "salary":
        role = _name(payload, "role", "a role")
        location = _name(payload, "location", "the location", required=False)
        company = _name(payload, "company", "the company", required=False)
        spec = ReportSpec(
            kind,
            f"Salary and market: {role}",
            "Published pay ranges, web estimates and what people report.",
            {"role": role, "location": location, "company": company},
            _salary_plans(role, location, company),
        )
    elif kind == "trends":
        terms = _terms(payload)
        geo = careers.clean_text(payload.get("geo"), 8).upper()
        if geo and not careers._GEO_RE.match(geo.split("-")[0]):
            raise ValueError(
                "geo must be a country code such as US or a region such as US-CA."
            )
        window = careers.clean_text(payload.get("window"), 8).lower() or "12m"
        if window not in TREND_WINDOWS:
            raise ValueError(f"window must be one of: {', '.join(TREND_WINDOWS)}.")
        spec = ReportSpec(
            kind,
            f"Market trends: {', '.join(terms)}",
            "How interest in skills or titles is moving, with related searches and news.",
            {"terms": terms, "geo": geo, "window": window},
            _trend_plans(terms, geo, window),
        )
    elif kind == "interview-prep":
        company = _name(payload, "company", "a company")
        role = _name(payload, "role", "the role")
        spec = ReportSpec(
            kind,
            f"Interview prep: {role} at {company}",
            "Questions, candidate accounts, videos, news and the postings themselves.",
            {"company": company, "role": role},
            _interview_plans(company, role),
        )
    elif kind == "travel":
        spec = _travel_spec(payload)
    else:
        skill = _name(payload, "skill", "a skill")
        level = careers.clean_text(payload.get("level"), 20).lower()
        if level and level not in LEVELS:
            raise ValueError(f"level must be one of: {', '.join(LEVELS)}.")
        spec = ReportSpec(
            kind,
            f"Learn {skill}",
            "Courses, videos, community advice and how much employers want it.",
            {"skill": skill, "level": level},
            _learn_plans(skill, level),
        )
    return _select_sections(spec, payload.get("sections"))


def _select_sections(spec: ReportSpec, wanted: Any) -> ReportSpec:
    if wanted in (None, ""):
        plans = spec.plans
    else:
        if not isinstance(wanted, list) or not all(
            isinstance(item, str) for item in wanted
        ):
            raise ValueError("sections must be a list of section ids.")
        known = {plan.id for plan in spec.plans}
        unknown = [item for item in wanted if item not in known]
        if unknown:
            raise ValueError(
                f"Unknown section {unknown[0]!r}. Sections: {', '.join(sorted(known))}."
            )
        plans = [plan for plan in spec.plans if plan.id in set(wanted)]
        if not plans:
            raise ValueError("Choose at least one section.")
    checked: list[SectionPlan] = []
    for plan in plans:
        clean, problems = catalog.validate_params(plan.engine, plan.params)
        if problems:
            raise ValueError(" ".join(problems))
        checked.append(
            SectionPlan(
                plan.id, plan.title, plan.engine, clean, plan.extractor, plan.why
            )
        )
    return ReportSpec(spec.kind, spec.title, spec.description, spec.input, checked)


PLAYBOOK_TITLES = {
    "company-intel": "Company intelligence",
    "salary": "Salary and market",
    "trends": "Market trends",
    "interview-prep": "Interview prep",
    "travel": "Onsite travel",
    "learn": "Learning",
}
PLAYBOOK_DEMO: dict[str, dict[str, Any]] = {
    "company-intel": {"company": "Example Co", "role": "Engineer"},
    "salary": {"role": "Engineer", "company": "Example Co"},
    "trends": {"terms": ["python"]},
    "interview-prep": {"company": "Example Co", "role": "Engineer"},
    "travel": {
        "kind": "flights",
        "departure_id": "AUS",
        "arrival_id": "JFK",
        "outbound_date": "2026-12-01",
    },
    "learn": {"skill": "python"},
}


def playbooks() -> list[dict[str, Any]]:
    """The report catalogue for the Careers overview, built from the real plans."""
    out: list[dict[str, Any]] = []
    for kind in KINDS:
        spec = build_spec(kind, PLAYBOOK_DEMO[kind])
        out.append(
            {
                "id": kind,
                "title": PLAYBOOK_TITLES[kind],
                "description": spec.description,
                "engines": list(dict.fromkeys(plan.engine for plan in spec.plans)),
                "sections": [
                    {
                        "id": plan.id,
                        "title": plan.title,
                        "engine": plan.engine,
                        "why": plan.why,
                    }
                    for plan in spec.plans
                ],
                "max_credits": sum(
                    catalog.credit_cost(plan.engine, None, plan.params)
                    for plan in spec.plans
                ),
            }
        )
    return out


# ---------------------------------------------------------------------------
# extraction: engine response -> thin items
# ---------------------------------------------------------------------------


def _scalar(value: Any) -> Any:
    if isinstance(value, bool | int | float):
        return value
    if isinstance(value, str):
        return careers.clean_text(value, 200)
    return None


def _item(
    title: Any,
    subtitle: Any = "",
    url: Any = "",
    thumbnail: Any = "",
    **meta: Any,
) -> dict[str, Any] | None:
    text = careers.clean_text(title, 200)
    if not text:
        return None
    return {
        "title": text,
        "subtitle": careers.clean_text(subtitle, 300),
        "url": careers.https_url(url),
        "thumbnail": careers._https_only(thumbnail),
        "meta": {
            key: scalar
            for key, value in meta.items()
            if (scalar := _scalar(value)) not in (None, "")
        },
    }


def _compact(
    items: list[dict[str, Any] | None], limit: int = ITEM_LIMIT
) -> list[dict[str, Any]]:
    return [item for item in items if item][:limit]


def _name_of(value: Any) -> str:
    if isinstance(value, Mapping):
        return careers.clean_text(value.get("name") or value.get("title"), 120)
    return careers.clean_text(value, 120)


def _first_thumb(value: Any) -> Any:
    if isinstance(value, Mapping):
        return (
            value.get("static")
            or value.get("rich")
            or value.get("thumbnail")
            or value.get("url")
            or value.get("original_image")
        )
    if isinstance(value, list) and value:
        return _first_thumb(value[0])
    return value


def _x_web(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any] | None] = []
    graph = result.get("knowledge_graph")
    if isinstance(graph, Mapping) and graph.get("title"):
        facts = {
            key: value
            for key, value in graph.items()
            if isinstance(value, str)
            and key
            not in {"title", "type", "description", "website", "kgmid", "entity_type"}
            and not value.startswith("http")
            and len(value) <= 120
        }
        source = graph.get("source")
        items.append(
            _item(
                graph["title"],
                graph.get("description") or graph.get("type"),
                graph.get("website")
                or (source.get("link") if isinstance(source, Mapping) else ""),
                _first_thumb(graph.get("header_images")) or graph.get("thumbnail"),
                **{
                    "kind": "knowledge",
                    "type": graph.get("type"),
                    **{f"fact_{key}": value for key, value in list(facts.items())[:8]},
                },
            )
        )
    box = result.get("answer_box")
    if isinstance(box, Mapping):
        headline = (
            box.get("answer")
            or box.get("result")
            or box.get("snippet")
            or box.get("title")
        )
        items.append(
            _item(
                headline,
                box.get("title")
                if headline != box.get("title")
                else box.get("snippet"),
                box.get("link"),
                kind="answer",
            )
        )
    for entry in (result.get("organic_results") or [])[:6]:
        if isinstance(entry, Mapping):
            items.append(
                _item(
                    entry.get("title"),
                    entry.get("snippet"),
                    entry.get("link"),
                    entry.get("thumbnail") or entry.get("favicon"),
                    kind="result",
                    source=_name_of(entry.get("source")) or entry.get("displayed_link"),
                    date=entry.get("date"),
                )
            )
    for entry in (result.get("related_questions") or [])[:4]:
        if isinstance(entry, Mapping):
            items.append(
                _item(
                    entry.get("question"),
                    entry.get("snippet"),
                    entry.get("link"),
                    kind="question",
                )
            )
    chosen = _compact(items, 14)
    kinds = {item["meta"].get("kind") for item in chosen}
    parts = [
        f"{sum(1 for i in chosen if i['meta'].get('kind') == 'result')} web results"
    ]
    if "knowledge" in kinds:
        parts.append("a knowledge panel")
    if "answer" in kinds:
        parts.append("an answer box")
    if "question" in kinds:
        parts.append(
            f"{sum(1 for i in chosen if i['meta'].get('kind') == 'question')} related questions"
        )
    return (", ".join(parts) if chosen else ""), chosen, {}


def _x_news(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    entries: list[Any] = []
    for entry in result.get("news_results") or result.get("top_stories") or []:
        if (
            isinstance(entry, Mapping)
            and not entry.get("title")
            and entry.get("stories")
        ):
            entries.extend(entry["stories"])
        else:
            entries.append(entry)
    items = _compact(
        [
            _item(
                entry.get("title"),
                _join_dot(_name_of(entry.get("source")), entry.get("date")),
                entry.get("link"),
                entry.get("thumbnail"),
                source=_name_of(entry.get("source")),
                date=entry.get("date"),
                snippet=entry.get("snippet"),
            )
            for entry in entries
            if isinstance(entry, Mapping)
        ]
    )
    sources = {
        item["meta"].get("source") for item in items if item["meta"].get("source")
    }
    summary = f"{len(items)} stories from {len(sources)} sources" if items else ""
    return summary, items, {}


def _join_dot(*parts: Any) -> str:
    return " · ".join(careers.clean_text(part, 120) for part in parts if part)


def _x_jobs(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    jobs = careers.normalize_results(result)
    items = _compact(
        [
            _item(
                job["title"],
                _join_dot(job["company"], job["location"]),
                (
                    job["apply_options"][0]["link"]
                    if job["apply_options"]
                    else job["share_link"]
                ),
                job["thumbnail"],
                company=job["company"],
                via=job["via"],
                posted=job["posted_at"],
                schedule=job["schedule_type"],
                salary=(job["salary"] or {}).get("text"),
                remote=job["work_from_home"] or None,
            )
            for job in jobs
        ],
        10,
    )
    stats = salary_stats(jobs)
    summary = ""
    if jobs:
        summary = f"{len(jobs)} listings"
        if stats["listings_with_pay"]:
            summary += f", {stats['listings_with_pay']} state pay"
        if stats.get("median_annual") is not None:
            summary += (
                f" (median {stats['median_annual']:,.0f} {stats['currency'] or ''} a year, computed)"
            ).replace("  ", " ")
    return summary, items, {"stats": stats}


def salary_stats(jobs: list[dict[str, Any]]) -> dict[str, Any]:
    """Annualized pay statistics computed from the listings that state a period.

    The median is of range midpoints and only covers listings in the most
    common currency; ``currency`` says which. Nothing is estimated for listings
    without a stated figure.
    """
    stated = [job["salary"] for job in jobs if job.get("salary")]
    usable = [
        salary
        for salary in stated
        if salary.get("annual_min") is not None or salary.get("annual_max") is not None
    ]
    stats: dict[str, Any] = {
        "listings": len(jobs),
        "listings_with_pay": len(stated),
        "annualized": len(usable),
        "currency": None,
        "min_annual": None,
        "max_annual": None,
        "median_annual": None,
    }
    if not usable:
        return stats
    by_currency: dict[str | None, list[Mapping[str, Any]]] = {}
    for salary in usable:
        by_currency.setdefault(salary.get("currency"), []).append(salary)
    currency, group = max(by_currency.items(), key=lambda pair: len(pair[1]))
    lows = [
        s["annual_min"] if s["annual_min"] is not None else s["annual_max"]
        for s in group
    ]
    highs = [
        s["annual_max"] if s["annual_max"] is not None else s["annual_min"]
        for s in group
    ]
    mids = [(low + high) / 2 for low, high in zip(lows, highs, strict=True)]
    stats.update(
        {
            "currency": currency,
            "min_annual": round(min(lows), 2),
            "max_annual": round(max(highs), 2),
            "median_annual": round(statistics.median(mids), 2),
            "annualized": len(group),
        }
    )
    return stats


def _x_forums(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    entries = (
        result.get("organic_results")
        or result.get("forum_results")
        or result.get("discussions_and_forums")
        or []
    )
    items = _compact(
        [
            _item(
                entry.get("title"),
                entry.get("snippet"),
                entry.get("link"),
                entry.get("thumbnail") or entry.get("favicon"),
                source=_name_of(entry.get("source")) or entry.get("displayed_link"),
                date=entry.get("date"),
                answers=entry.get("answers") or entry.get("comments"),
            )
            for entry in entries
            if isinstance(entry, Mapping)
        ]
    )
    return (f"{len(items)} threads" if items else ""), items, {}


def _x_videos(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    items = _compact(
        [
            _item(
                entry.get("title"),
                _join_dot(
                    _name_of(entry.get("channel")),
                    f"{entry['views']:,} views"
                    if isinstance(entry.get("views"), int)
                    else "",
                    entry.get("length"),
                ),
                entry.get("link"),
                _first_thumb(entry.get("thumbnail")),
                channel=_name_of(entry.get("channel")),
                views=entry.get("views"),
                length=entry.get("length"),
                published=entry.get("published_date"),
            )
            for entry in (result.get("video_results") or [])
            if isinstance(entry, Mapping)
        ]
    )
    return (f"{len(items)} videos" if items else ""), items, {}


def _numbers(entry: Mapping[str, Any]) -> list[tuple[str, float]]:
    values = []
    for value in entry.get("values") or []:
        if not isinstance(value, Mapping):
            continue
        raw = value.get("extracted_value")
        if raw is None:
            try:
                raw = float(str(value.get("value", "")).replace("<", "").strip())
            except ValueError:
                continue
        values.append((str(value.get("query") or ""), float(raw)))
    return values


def _x_trend_series(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    over_time = result.get("interest_over_time")
    timeline = (
        over_time.get("timeline_data") if isinstance(over_time, Mapping) else None
    )
    if not isinstance(timeline, list):
        return "", [], {}
    series: dict[str, list[tuple[str, float]]] = {}
    for point in timeline:
        if not isinstance(point, Mapping):
            continue
        for query, number in _numbers(point):
            series.setdefault(query, []).append((str(point.get("date") or ""), number))
    items: list[dict[str, Any] | None] = []
    for query, points in series.items():
        values = [number for _, number in points]
        peak_date, peak = max(points, key=lambda pair: pair[1])
        quarter = max(1, len(values) // 4)
        early, late = (
            statistics.fmean(values[:quarter]),
            statistics.fmean(values[-quarter:]),
        )
        change = round((late - early) / early * 100) if early > 0 else None
        average = round(statistics.fmean(values))
        step = max(1, len(values) // 60)
        subtitle = f"average {average} · peak {peak:.0f} ({peak_date}) · latest {values[-1]:.0f}"
        if change is not None:
            subtitle += (
                f" · {change:+d}% (last quarter of the window vs first, computed)"
            )
        item = _item(
            query,
            subtitle,
            average=average,
            peak=peak,
            peak_date=peak_date,
            latest=values[-1],
            change_pct=change,
        )
        if item:
            item["meta"]["points"] = [round(v) for v in values[::step]]
            items.append(item)
    ranked = sorted(
        (item for item in items if item), key=lambda i: -i["meta"].get("average", 0)
    )
    summary = ""
    if ranked:
        summary = f"{ranked[0]['title']} leads with an average of {ranked[0]['meta']['average']}"
        if len(ranked) > 1:
            summary += f" across {len(ranked)} terms"
    return summary, ranked, {}


def _x_trend_related(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    block = result.get("related_queries") or result.get("related_topics")
    if not isinstance(block, Mapping):
        return "", [], {}
    items: list[dict[str, Any] | None] = []
    for group in ("rising", "top"):
        for entry in (block.get(group) or [])[:6]:
            if not isinstance(entry, Mapping):
                continue
            topic = entry.get("topic")
            title = entry.get("query") or (
                topic.get("title") if isinstance(topic, Mapping) else ""
            )
            kind = topic.get("type") if isinstance(topic, Mapping) else ""
            items.append(
                _item(
                    title,
                    _join_dot(kind, entry.get("value")),
                    entry.get("link"),
                    group=group,
                    value=entry.get("value"),
                )
            )
    chosen = _compact(items, 12)
    rising = sum(1 for item in chosen if item["meta"].get("group") == "rising")
    return (
        (f"{rising} rising, {len(chosen) - rising} top" if chosen else ""),
        chosen,
        {},
    )


def _minutes(value: Any) -> str:
    if not isinstance(value, int | float) or value <= 0:
        return ""
    hours, minutes = divmod(int(value), 60)
    return f"{hours}h {minutes:02d}m" if hours else f"{minutes}m"


def _hhmm(value: Any) -> str:
    text = str(value or "")
    return text.split(" ")[-1] if " " in text else text


def _x_flights(
    result: Mapping[str, Any], currency: str = "USD"
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any] | None] = []
    for group, label in (("best_flights", "best"), ("other_flights", "other")):
        for trip in result.get(group) or []:
            if not isinstance(trip, Mapping):
                continue
            legs = [
                leg for leg in (trip.get("flights") or []) if isinstance(leg, Mapping)
            ]
            if not legs:
                continue
            first, last = legs[0], legs[-1]
            airlines = list(
                dict.fromkeys(
                    str(leg.get("airline") or "") for leg in legs if leg.get("airline")
                )
            )
            origin = (first.get("departure_airport") or {}).get("id", "")
            target = (last.get("arrival_airport") or {}).get("id", "")
            stops = len(legs) - 1
            price = trip.get("price")
            duration = trip.get("total_duration") or sum(
                int(leg.get("duration") or 0) for leg in legs
            )
            items.append(
                _item(
                    f"{' + '.join(airlines) or 'Flight'} · {origin} to {target}",
                    _join_dot(
                        f"{price:,} {currency}"
                        if isinstance(price, int | float)
                        else "",
                        _minutes(duration),
                        "nonstop"
                        if stops == 0
                        else f"{stops} stop{'s' if stops > 1 else ''}",
                        f"{_hhmm((first.get('departure_airport') or {}).get('time'))} to "
                        f"{_hhmm((last.get('arrival_airport') or {}).get('time'))}",
                    ),
                    "",
                    trip.get("airline_logo") or first.get("airline_logo"),
                    group=label,
                    price=price,
                    currency=currency,
                    duration_min=duration,
                    stops=stops,
                    travel_class=first.get("travel_class"),
                    flight_number=first.get("flight_number"),
                )
            )
    chosen = _compact(items, 10)
    prices = [
        i["meta"]["price"]
        for i in chosen
        if isinstance(i["meta"].get("price"), int | float)
    ]
    insights = result.get("price_insights")
    extra: dict[str, Any] = {}
    summary = ""
    if chosen:
        summary = f"{len(chosen)} itineraries"
        if prices:
            summary += f" from {min(prices):,.0f} {currency}"
    if isinstance(insights, Mapping):
        extra["price_insights"] = {
            key: insights.get(key)
            for key in ("lowest_price", "price_level", "typical_price_range")
            if insights.get(key) is not None
        }
        if insights.get("price_level"):
            summary += f"; Google rates these prices {insights['price_level']}"
    return summary, chosen, extra


def _x_hotels(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    items: list[dict[str, Any] | None] = []
    for prop in result.get("properties") or []:
        if not isinstance(prop, Mapping):
            continue
        rate = (
            prop.get("rate_per_night")
            if isinstance(prop.get("rate_per_night"), Mapping)
            else {}
        )
        total = (
            prop.get("total_rate")
            if isinstance(prop.get("total_rate"), Mapping)
            else {}
        )
        rating = prop.get("overall_rating")
        items.append(
            _item(
                prop.get("name"),
                _join_dot(
                    f"{rate.get('lowest')} per night" if rate.get("lowest") else "",
                    f"{total.get('lowest')} total" if total.get("lowest") else "",
                    prop.get("hotel_class"),
                    f"{rating} rating ({prop.get('reviews')} reviews)"
                    if rating and prop.get("reviews")
                    else (f"{rating} rating" if rating else ""),
                ),
                prop.get("link"),
                _first_thumb(prop.get("images")),
                price=rate.get("extracted_lowest"),
                total=total.get("extracted_lowest"),
                rating=rating,
                reviews=prop.get("reviews"),
                hotel_class=prop.get("hotel_class"),
                property_token=prop.get("property_token"),
            )
        )
    chosen = _compact(items, 10)
    prices = [
        i["meta"]["price"]
        for i in chosen
        if isinstance(i["meta"].get("price"), int | float)
    ]
    summary = ""
    if chosen:
        summary = f"{len(chosen)} stays"
        if prices:
            summary += f", nightly rates from {min(prices):,.0f}"
    return summary, chosen, {}


def _x_places(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    entries = list(result.get("local_results") or [])
    place = result.get("place_results")
    if isinstance(place, Mapping) and place.get("title"):
        entries.insert(0, place)
    items = _compact(
        [
            _item(
                entry.get("title"),
                _join_dot(entry.get("type"), entry.get("address")),
                entry.get("website") or "",
                entry.get("thumbnail"),
                rating=entry.get("rating"),
                reviews=entry.get("reviews"),
                phone=entry.get("phone"),
                hours=entry.get("open_state") or entry.get("hours"),
            )
            for entry in entries
            if isinstance(entry, Mapping)
        ],
        6,
    )
    return (f"{len(items)} places" if items else ""), items, {}


def _x_generic(
    result: Mapping[str, Any],
) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    key = service.main_result_key("", dict(result))
    entries = result.get(key) if key else []
    items = _compact(
        [
            _item(
                entry.get("title") or entry.get("name"),
                entry.get("snippet") or entry.get("description"),
                entry.get("link") or entry.get("url"),
                entry.get("thumbnail"),
            )
            for entry in (entries or [])
            if isinstance(entry, Mapping)
        ]
    )
    return (f"{len(items)} results" if items else ""), items, {}


EXTRACTORS: dict[
    str, Callable[..., tuple[str, list[dict[str, Any]], dict[str, Any]]]
] = {
    "web": _x_web,
    "news": _x_news,
    "jobs": _x_jobs,
    "forums": _x_forums,
    "videos": _x_videos,
    "trend_series": _x_trend_series,
    "trend_related": _x_trend_related,
    "flights": _x_flights,
    "hotels": _x_hotels,
    "places": _x_places,
    "generic": _x_generic,
}


# ---------------------------------------------------------------------------
# running a report
# ---------------------------------------------------------------------------


@dataclass
class _Outcome:
    record: dict[str, Any] | None = None
    failure: Exception | None = None


def _section_label(spec: ReportSpec, plan: SectionPlan) -> str:
    return f"{spec.title}: {plan.title}"[:200]


def _would_hit_cache(
    connection: sqlite3.Connection,
    plan: SectionPlan,
    limits: service.Limits,
    now: datetime,
) -> bool:
    ttl = limits.cache_ttl_minutes
    if ttl <= 0:
        return False
    digest = service.params_hash(
        plan.engine, plan.params, service.normalize_options({})
    )
    return (
        store.find_cached(
            connection, digest, store.to_iso(now - timedelta(minutes=ttl))
        )
        is not None
    )


def estimate(
    connection: sqlite3.Connection,
    spec: ReportSpec,
    limits: service.Limits,
    now: datetime,
) -> dict[str, Any]:
    """The plan and its cost, without calling SerpApi."""
    rows = []
    for plan in spec.plans:
        hit = _would_hit_cache(connection, plan, limits, now)
        rows.append(
            {
                "id": plan.id,
                "title": plan.title,
                "engine": plan.engine,
                "params": plan.params,
                "why": plan.why,
                "cache_hit": hit,
                "credits": 0
                if hit
                else catalog.credit_cost(plan.engine, None, plan.params),
            }
        )
    return {
        "dry_run": True,
        "kind": spec.kind,
        "title": spec.title,
        "input": spec.input,
        "plan": rows,
        "credits": sum(row["credits"] for row in rows),
        "max_credits": sum(
            catalog.credit_cost(plan.engine, None, plan.params) for plan in spec.plans
        ),
        "cache_hits": sum(1 for row in rows if row["cache_hit"]),
    }


def _run_one(
    plan: SectionPlan,
    spec: ReportSpec,
    client: Any,
    limits: service.Limits,
    now: datetime,
    connect: Callable[[], sqlite3.Connection] | None,
    shared: sqlite3.Connection,
    lock: threading.Lock,
) -> _Outcome:
    try:
        if connect is None:
            with lock:
                record = service.run_search(
                    shared,
                    client,
                    plan.engine,
                    plan.params,
                    source="intel",
                    label=_section_label(spec, plan),
                    now=now,
                    limits=limits,
                )
        else:
            with closing(connect()) as connection:
                record = service.run_search(
                    connection,
                    client,
                    plan.engine,
                    plan.params,
                    source="intel",
                    label=_section_label(spec, plan),
                    now=now,
                    limits=limits,
                )
        return _Outcome(record=record)
    except (SerpApiError, ValueError, LookupError, sqlite3.Error) as failure:
        return _Outcome(failure=failure)


def _section(plan: SectionPlan, outcome: _Outcome) -> dict[str, Any]:
    base: dict[str, Any] = {
        "id": plan.id,
        "title": plan.title,
        "engine": plan.engine,
        "params": plan.params,
        "why": plan.why,
        "record_id": None,
        "summary": "",
        "items": [],
        "error": "",
        "code": "",
        "cache_hit": False,
        "credits": 0,
        "stats": None,
        "price_insights": None,
    }
    if outcome.failure is not None:
        failure = outcome.failure
        base["status"] = "error"
        base["error"] = (
            failure.message if isinstance(failure, SerpApiError) else str(failure)
        )
        base["code"] = (
            failure.code if isinstance(failure, SerpApiError) else "bad_request"
        )
        base["record_id"] = getattr(failure, "record_id", None)
        return base
    record = outcome.record or {}
    result = record.get("result")
    base["record_id"] = record.get("id")
    base["cache_hit"] = bool(record.get("cache_hit"))
    base["credits"] = int(record.get("credits") or 0)
    extractor = EXTRACTORS.get(plan.extractor, _x_generic)
    summary, items, extra = "", [], {}
    if isinstance(result, Mapping):
        if plan.extractor == "flights":
            currency = str(
                (result.get("search_parameters") or {}).get("currency")
                or plan.params.get("currency")
                or "USD"
            )
            summary, items, extra = extractor(result, currency)
        else:
            summary, items, extra = extractor(result)
    base["items"] = items
    base["stats"] = extra.get("stats")
    base["price_insights"] = extra.get("price_insights")
    base["status"] = "ok" if items else "empty"
    base["summary"] = summary or (
        careers.clean_text(record.get("note"), 200) or "No results for this search."
    )
    return base


def run_report(
    connection: sqlite3.Connection,
    client: Any,
    spec: ReportSpec,
    *,
    limits: service.Limits | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Run every section (at most four at once) and return the stored report.

    A section that fails is reported with its error and does not stop the
    others. If nothing succeeded and the first failure means the whole report
    cannot work (no key, a rejected key, no searches left), that failure is
    raised instead so the caller can show the right setup message.
    """
    moment = now or store.now_utc()
    limits = limits or service.Limits()
    connect = careers.bound_connect(connection)
    lock = threading.Lock()
    workers = max(1, min(MAX_WORKERS, len(spec.plans)))
    if connect is None:
        workers = 1
    with ThreadPoolExecutor(
        max_workers=workers, thread_name_prefix="careers-intel"
    ) as pool:
        futures = [
            pool.submit(
                _run_one, plan, spec, client, limits, moment, connect, connection, lock
            )
            for plan in spec.plans
        ]
        outcomes = [future.result() for future in futures]
    sections = [
        _section(plan, outcome)
        for plan, outcome in zip(spec.plans, outcomes, strict=True)
    ]
    succeeded = [s for s in sections if s["status"] != "error"]
    if not succeeded:
        first = next((o.failure for o in outcomes if o.failure is not None), None)
        if isinstance(first, SerpApiError) and first.code in FATAL_CODES:
            raise first
    report = {
        "id": store.new_id(),
        "kind": spec.kind,
        "title": spec.title,
        "description": spec.description,
        "generated_at": store.to_iso(moment),
        "input": spec.input,
        "sections": sections,
        "credits": sum(s["credits"] for s in sections),
        "cache_hits": sum(1 for s in sections if s["cache_hit"]),
        "errors": sum(1 for s in sections if s["status"] == "error"),
    }
    if succeeded:
        save_report(connection, report)
    return report


# ---------------------------------------------------------------------------
# stored reports
# ---------------------------------------------------------------------------


def save_report(connection: sqlite3.Connection, report: Mapping[str, Any]) -> None:
    connection.execute(
        "INSERT INTO careers_reports (id, kind, title, created_at, credits, cache_hits, errors, "
        "input_json, body_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            report["id"],
            report["kind"],
            report["title"][:200],
            report["generated_at"],
            report["credits"],
            report["cache_hits"],
            report["errors"],
            json.dumps(report["input"], ensure_ascii=False),
            json.dumps(report, ensure_ascii=False),
        ),
    )
    connection.execute(
        """DELETE FROM careers_reports WHERE id NOT IN (
            SELECT id FROM careers_reports ORDER BY created_at DESC, rowid DESC LIMIT ?)""",
        (MAX_REPORTS,),
    )
    connection.commit()


def _report_summary(row: sqlite3.Row) -> dict[str, Any]:
    try:
        body = json.loads(row["body_json"])
        sections = body.get("sections", [])
    except json.JSONDecodeError:
        sections = []
    try:
        report_input = json.loads(row["input_json"] or "{}")
    except json.JSONDecodeError:
        report_input = {}
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "generated_at": row["created_at"],
        "credits": row["credits"],
        "cache_hits": row["cache_hits"],
        "errors": row["errors"],
        "input": report_input,
        "sections": len(sections),
    }


def list_reports(
    connection: sqlite3.Connection, *, kind: str = "", limit: int = 20
) -> list[dict[str, Any]]:
    if kind and kind not in KINDS:
        raise ValueError(f"kind must be one of: {', '.join(KINDS)}.")
    rows = connection.execute(
        "SELECT * FROM careers_reports WHERE (? = '' OR kind = ?) "
        "ORDER BY created_at DESC, rowid DESC LIMIT ?",
        (kind, kind, limit),
    ).fetchall()
    return [_report_summary(row) for row in rows]


def get_report(connection: sqlite3.Connection, report_id: str) -> dict[str, Any]:
    row = connection.execute(
        "SELECT body_json FROM careers_reports WHERE id = ?", (report_id,)
    ).fetchone()
    if row is None:
        raise LookupError("Report not found.")
    body: dict[str, Any] = json.loads(row["body_json"])
    return body


def delete_report(connection: sqlite3.Connection, report_id: str) -> None:
    deleted = connection.execute(
        "DELETE FROM careers_reports WHERE id = ?", (report_id,)
    ).rowcount
    if not deleted:
        raise LookupError("Report not found.")
    connection.commit()


# ---------------------------------------------------------------------------
# overview
# ---------------------------------------------------------------------------


def overview(
    connection: sqlite3.Connection,
    *,
    configured: bool,
    key: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Everything the Careers home needs from the local database."""
    moment = now or store.now_utc()
    since = store.to_iso(moment - timedelta(days=30))
    usage = connection.execute(
        "SELECT source, COUNT(*) AS searches, SUM(cache_hit) AS cached, SUM(credits) AS credits "
        "FROM airaapi_searches WHERE source IN ('careers', 'intel') AND created_at >= ? "
        "GROUP BY source",
        (since,),
    ).fetchall()
    by_source = {
        row["source"]: {
            "searches": row["searches"],
            "cached": int(row["cached"] or 0),
            "credits": int(row["credits"] or 0),
        }
        for row in usage
    }
    alerts = careers.list_alerts(connection)
    saved = [
        item
        for item in service.list_saved(connection)
        if item["engine"] in (careers.JOBS_ENGINE, careers.AIRA_JOBS_ENGINE)
        and "alert" not in item["tags"]
    ]
    return {
        "configured": configured,
        "key": dict(key),
        "saved_searches": saved,
        "alerts": alerts,
        "recent_intel": list_reports(connection, limit=8),
        "playbooks": playbooks(),
        "usage_30d": {
            "jobs": by_source.get(
                "careers", {"searches": 0, "cached": 0, "credits": 0}
            ),
            "reports": by_source.get(
                "intel", {"searches": 0, "cached": 0, "credits": 0}
            ),
        },
    }
