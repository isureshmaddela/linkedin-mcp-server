"""Lead sources: how each search is built, and how its answer becomes leads.

Every ``build_*`` function validates the form and returns ``(params, applied, warnings)`` for the Aira API
engine it runs on; every ``normalize_*`` function turns the engine's stored answer into lead dictionaries
(see :func:`aira.leads.store.normalize_lead`). A field a source does not provide stays empty: nothing is
guessed, and each lead keeps ``source`` and ``source_ref`` so you can see where it came from.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Mapping, Sequence
from typing import Any
from urllib.parse import urlsplit

from aira.leads import intent, store

NEWS_ENGINE = "google_news"
MAPS_ENGINE = "google_maps"
WEB_ENGINE = "google"
JOBS_ENGINE = "google_jobs"
MAX_QUERY = 200
MAX_TITLES = 6
MAX_CSV_ROWS = 5000
MAX_CSV_CHARS = 3_000_000

WEB_MODES = ("people", "companies", "custom")
_LL = re.compile(r"^@-?\d{1,2}(\.\d+)?,-?\d{1,3}(\.\d+)?,\d{1,2}(\.\d+)?z$")


def _text(
    payload: Mapping[str, Any],
    name: str,
    *,
    limit: int = MAX_QUERY,
    required: bool = False,
    label: str = "",
) -> str:
    raw = payload.get(name)
    if raw is not None and not isinstance(raw, str | int | float):
        raise ValueError(f"{label or name} must be text.")
    text = store.clean(raw, limit + 1)
    if len(text) > limit:
        raise ValueError(f"{label or name} is limited to {limit} characters.")
    if required and not text:
        raise ValueError(f"Enter {label or name}.")
    return text


def _list(payload: Mapping[str, Any], name: str, *, limit: int, cap: int) -> list[str]:
    raw = payload.get(name)
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        items = re.split(r"[\n,;]+", raw)
    elif isinstance(raw, Sequence):
        items = [str(item) for item in raw]
    else:
        raise ValueError(f"{name} must be a list.")
    out: list[str] = []
    for item in items:
        text = store.clean(item, limit)
        if text and text.casefold() not in {o.casefold() for o in out}:
            out.append(text)
    if len(out) > cap:
        raise ValueError(f"Give at most {cap} values for {name}.")
    return out


def _quote(text: str) -> str:
    text = text.replace('"', "")
    return f'"{text}"' if " " in text else text


def _offset(payload: Mapping[str, Any], step: int, maximum: int) -> int:
    raw = payload.get("start", 0)
    try:
        value = int(raw or 0)
    except (TypeError, ValueError):
        raise ValueError("start must be a whole number.") from None
    if value < 0 or value > maximum or value % step:
        raise ValueError(f"start must be a multiple of {step} up to {maximum}.")
    return value


# ---------------------------------------------------------------------------
# Google Maps: local businesses
# ---------------------------------------------------------------------------


def build_maps_params(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    query = _text(
        payload,
        "query",
        required=True,
        label="what to look for (for example 'dentists')",
    )
    location = _text(payload, "location", limit=120)
    ll = _text(payload, "ll", limit=60)
    if ll and not _LL.match(ll):
        raise ValueError("ll must look like @40.7455096,-74.0083012,14z.")
    q = query
    applied = [f"query: {query}"]
    if location and f" in {location}".casefold() not in query.casefold():
        q = f"{query} in {location}"
    if location:
        applied.append(f"location: {location}")
    params: dict[str, Any] = {"q": q, "type": "search", "hl": "en"}
    if ll:
        params["ll"] = ll
        applied.append("map area")
    start = _offset(payload, 20, 100)
    if start:
        params["start"] = start
    warnings = []
    if not location and not ll:
        warnings.append(
            "No location: Google Maps picks one from the search itself. Name a city for better results."
        )
    return params, applied, warnings


def _first(*values: Any) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def normalize_maps(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, Mapping):
        return []
    items = result.get("local_results")
    if isinstance(
        items, Mapping
    ):  # google (web) nests places under local_results.places
        items = items.get("places")
    places: list[Any] = list(items) if isinstance(items, list) else []
    single = result.get("place_results")
    if isinstance(single, Mapping) and not places:
        places = [single]
    leads: list[dict[str, Any]] = []
    for item in places:
        if not isinstance(item, Mapping):
            continue
        name = _first(item.get("title"), item.get("name"))
        if not name:
            continue
        rating = item.get("rating")
        reviews = item.get("reviews")
        signal = []
        if rating:
            suffix = f" ({reviews:,} reviews)" if isinstance(reviews, int) else ""
            signal.append(
                {
                    "type": "google_maps",
                    "text": f"{rating}★{suffix} on Google Maps",
                    "url": "",
                }
            )
        signal.extend(intent.maps_needs(item))
        types = item.get("types") if isinstance(item.get("types"), list) else []
        leads.append(
            {
                "kind": "company",
                "name": name,
                "company": name,
                "industry": _first(item.get("type"), *(str(t) for t in types)),
                "location": _first(item.get("address")),
                "website": _first(item.get("website")),
                "phone": _first(item.get("phone")),
                "rating": rating,
                "reviews": reviews if isinstance(reviews, int) else None,
                "source": "google_maps",
                "source_ref": _first(
                    item.get("place_id"),
                    item.get("data_id"),
                    str(item.get("data_cid") or ""),
                ),
                "signals": signal,
            }
        )
    return leads


# ---------------------------------------------------------------------------
# Google web: people and companies (site:linkedin.com ...)
# ---------------------------------------------------------------------------


def build_web_params(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    mode = str(payload.get("mode") or "people").strip().lower()
    if mode not in WEB_MODES:
        raise ValueError(f"mode must be one of: {', '.join(WEB_MODES)}.")
    applied: list[str] = []
    warnings: list[str] = []
    if mode == "custom":
        query = _text(payload, "query", limit=300, required=True, label="the search")
        site = _text(payload, "site", limit=120)
        q = f"site:{site} {query}" if site else query
        applied.append(f"query: {query}")
    else:
        titles = _list(payload, "titles", limit=60, cap=MAX_TITLES)
        keywords = _text(payload, "keywords", limit=120)
        company = _text(payload, "company", limit=100)
        location = _text(payload, "location", limit=100)
        if mode == "people" and not (titles or keywords or company):
            raise ValueError("Give at least a job title, a keyword or a company.")
        if mode == "companies" and not (keywords or company or location):
            raise ValueError("Give at least a keyword, a company or a location.")
        parts = [
            "site:linkedin.com/in" if mode == "people" else "site:linkedin.com/company"
        ]
        if titles:
            group = " OR ".join(_quote(t) for t in titles)
            parts.append(f"({group})" if len(titles) > 1 else group)
            applied.append("titles: " + ", ".join(titles))
        if keywords:
            parts.append(_quote(keywords))
            applied.append(f"keywords: {keywords}")
        if company:
            parts.append(_quote(company))
            applied.append(f"company: {company}")
        if location:
            parts.append(_quote(location))
            applied.append(f"location: {location}")
        q = " ".join(parts)
    if len(q) > 500:
        raise ValueError("That search is too long; use fewer titles or keywords.")
    params: dict[str, Any] = {"q": q, "hl": "en"}
    start = _offset(payload, 10, 90)
    if start:
        params["start"] = start
    warnings.append(
        "Google shows a public snippet of each LinkedIn page: names and titles can be out of date."
    )
    return params, applied, warnings


_LI_SUFFIX = re.compile(r"\s*[|\-–—]\s*LinkedIn\s*$", re.I)
_DASH = re.compile(r"\s+[-–—]\s+")
_ELLIPSIS = re.compile(r"\s*(\.{3}|…)\s*$")
_AT_COMPANY = re.compile(r"\s(?:at|@)\s+([^|,·\-–—]+?)\s*(?:[|·\-–—]|$)", re.I)
_EXPERIENCE = re.compile(r"Experience:\s*([^·|]+?)\s*(?:·|\||$|Education:)")
_ROLE_COMPANY = re.compile(
    r"\.\s+([^.]{2,60}?)\.\s+(?:[A-Z][a-z]{2,8}\s+)?\d{4}\s*[-–—]\s*(?:Present|(?:[A-Z][a-z]{2,8}\s+)?\d{4})"
)
_LABELLED_PLACE = re.compile(r"(?:Location|Based in)\s*[:\-]\s*([^·|\n.]{3,80})", re.I)
_AREA = re.compile(
    r"((?:Greater\s+)?[A-Z][A-Za-z.'\- ]{1,40}(?:,\s*[A-Z][A-Za-z.'\- ]{1,40}){0,2}"
    r"\s+(?:Metropolitan\s+)?Area)"
)


def parse_profile_title(title: str, snippet: str = "") -> tuple[str, str, str]:
    """``(name, job title, company)`` from a LinkedIn result's title and snippet.

    Google titles a profile ``Name - Headline``; the headline is free text the person wrote, so a company is
    only reported when something says so: ``... at Company`` or ``... @ Company`` in the headline, a short
    ``Title - Company`` pair, ``Experience: Company`` in the snippet, or the snippet's
    ``Role. Company. 2024 - Present`` line. Otherwise it stays empty rather than guessed.
    """
    text = _LI_SUFFIX.sub("", store.clean(title, 300))
    parts = _DASH.split(text, maxsplit=1)
    name = parts[0].strip()
    if not name:
        return "", "", ""
    headline = _ELLIPSIS.sub("", parts[1]).strip() if len(parts) == 2 else ""
    company = ""
    found = _AT_COMPANY.search(headline)
    if found:
        company = found.group(1).strip()
        headline = headline[: found.start()].strip(" -–—|")
        # "Head of Sales, US - Repair AI at Partly": the team before the company stays in the title
    elif _DASH.search(headline):
        segments = _DASH.split(headline)
        last = segments[-1]
        if (
            len(segments) == 2
            and len(last.split()) <= 5
            and "|" not in last
            and "," not in last
        ):
            headline, company = segments[0].strip(), last.strip()
    if not company:
        snippet_text = store.clean(snippet, 500)
        experience = _EXPERIENCE.search(snippet_text)
        role_line = _ROLE_COMPANY.search(snippet_text)
        company = (
            experience.group(1).strip()
            if experience
            else role_line.group(1).strip()
            if role_line
            else ""
        )
    return name, store.clean(headline, 200), store.clean(company, 120)


def _location_of(snippet: str) -> str:
    found = _LABELLED_PLACE.search(snippet)
    if found:
        return store.clean(found.group(1), 120)
    area = _AREA.search(snippet)
    if area:
        return store.clean(area.group(1), 120)
    head = snippet.split("·", 1)[0].strip()
    if (
        "·" in snippet
        and "," in head
        and len(head.split()) <= 6
        and not re.search(r"\d", head)
    ):
        return store.clean(head, 120)
    return ""


def normalize_web(
    result: Any, mode: str = "people"
) -> tuple[list[dict[str, Any]], int]:
    """``(leads, skipped)``: results that are not a person or company page are skipped, not guessed."""
    if not isinstance(result, Mapping):
        return [], 0
    leads: list[dict[str, Any]] = []
    skipped = 0
    for item in result.get("organic_results") or []:
        if not isinstance(item, Mapping):
            continue
        link = str(item.get("link") or "")
        title = str(item.get("title") or "")
        snippet = store.clean(item.get("snippet"), 400)
        linkedin = store.canonical_linkedin(link)
        signal = (
            [{"type": "web", "text": snippet[:180], "url": link}] if snippet else []
        )
        if "/in/" in linkedin and mode in {"people", "custom"}:
            name, job, company = parse_profile_title(title, snippet)
            if not name:
                skipped += 1
                continue
            leads.append(
                {
                    "kind": "person",
                    "name": name,
                    "title": job,
                    "company": company,
                    "location": _location_of(snippet),
                    "linkedin_url": linkedin,
                    "source": "google_web",
                    "source_ref": link[:300],
                    "signals": signal,
                }
            )
        elif "/company/" in linkedin and mode in {"companies", "custom"}:
            name = _LI_SUFFIX.sub("", store.clean(title, 200))
            name = re.split(r"\s+[-–—|]\s+", name)[0].strip()
            if not name:
                skipped += 1
                continue
            leads.append(
                {
                    "kind": "company",
                    "name": name,
                    "company": name,
                    "linkedin_url": linkedin,
                    "location": _location_of(snippet),
                    "source": "google_web",
                    "source_ref": link[:300],
                    "signals": signal,
                }
            )
        elif mode == "custom" and link:
            host = store.domain_of(link)
            if not host:
                skipped += 1
                continue
            name = re.split(r"\s+[-–—|]\s+", store.clean(title, 200))[0].strip() or host
            leads.append(
                {
                    "kind": "company",
                    "name": name,
                    "company": name,
                    "website": f"https://{host}",
                    "source": "google_web",
                    "source_ref": link[:300],
                    "signals": signal,
                }
            )
        else:
            skipped += 1
    return leads, skipped


# ---------------------------------------------------------------------------
# Google Jobs: companies hiring for a role you sell into
# ---------------------------------------------------------------------------


def build_hiring_params(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    query = _text(
        payload, "query", required=True, label="the role companies are hiring for"
    )
    location = _text(payload, "location", limit=120)
    params: dict[str, Any] = {"q": query, "hl": "en"}
    applied = [f"role: {query}"]
    if location:
        params["location"] = location
        applied.append(f"location: {location}")
    token = _text(payload, "next_page_token", limit=2000)
    if token:
        params["next_page_token"] = token
    return (
        params,
        applied,
        ["Each company found is a hiring signal: they are spending on this role now."],
    )


def normalize_hiring(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, Mapping):
        return []
    by_company: dict[str, dict[str, Any]] = {}
    for job in result.get("jobs_results") or []:
        if not isinstance(job, Mapping):
            continue
        company = store.clean(job.get("company_name"), 200)
        if not company:
            continue
        key = company.casefold()
        options = job.get("apply_options")
        link = ""
        if isinstance(options, list) and options and isinstance(options[0], Mapping):
            link = str(options[0].get("link") or "")
        link = link or str(job.get("share_link") or "")
        title = store.clean(job.get("title"), 120)
        place = store.clean(job.get("location"), 100)
        signal = {
            "type": "hiring",
            "text": f"Hiring: {title}{f' ({place})' if place else ''}"[:200],
            "url": link,
        }
        entry = by_company.get(key)
        if entry is None:
            by_company[key] = {
                "kind": "company",
                "name": company,
                "company": company,
                "location": place,
                "source": "google_jobs",
                "source_ref": store.clean(job.get("job_id"), 300),
                "signals": [signal],
            }
        elif len(entry["signals"]) < 5:
            entry["signals"].append(signal)
    return list(by_company.values())


# ---------------------------------------------------------------------------
# Google News: recent news about a lead's company, kept as buying signals
# ---------------------------------------------------------------------------


def build_news_params(
    company: str, topic: str = ""
) -> tuple[dict[str, Any], list[str]]:
    name = store.clean(company, 100)
    if not name:
        raise ValueError("This lead has no company to look up.")
    extra = store.clean(topic, 80)
    q = f'"{name.replace(chr(34), "")}"' + (f" {extra}" if extra else "")
    return {"q": q, "hl": "en"}, [f"company: {name}"] + (
        [f"topic: {extra}"] if extra else []
    )


def normalize_news(result: Any, limit: int = 3) -> list[dict[str, str]]:
    """The newest few stories as ``{type: news, text, url}`` signals (the source and date are in the text)."""
    if not isinstance(result, Mapping):
        return []
    signals: list[dict[str, str]] = []
    for item in result.get("news_results") or []:
        if not isinstance(item, Mapping):
            continue
        title = store.clean(item.get("title"), 140)
        link = str(item.get("link") or "")
        if not title or not link.startswith(("http://", "https://")):
            continue
        source = item.get("source")
        outlet = store.clean(
            source.get("name") if isinstance(source, Mapping) else source, 40
        )
        when = store.clean(item.get("date"), 30)
        tail = ", ".join(part for part in (outlet, when) if part)
        signals.append(
            {
                "type": "news",
                "text": f"{title} ({tail})" if tail else title,
                "url": link,
            }
        )
        if len(signals) >= limit:
            break
    return signals


# ---------------------------------------------------------------------------
# LinkedIn Sales Navigator (read by the LinkedIn tool in the person's own browser)
# ---------------------------------------------------------------------------

_SALES_URL = re.compile(
    r"^https://www\.linkedin\.com/sales/(search/people|lists/people)\b", re.I
)


def build_sales_navigator_request(payload: Mapping[str, Any]) -> dict[str, Any]:
    url = _text(payload, "url", limit=2000)
    keywords = _text(payload, "keywords", limit=200)
    location = _text(payload, "location", limit=120)
    if url:
        if not _SALES_URL.match(url):
            raise ValueError(
                "Paste a Sales Navigator people search or lead list address (it starts with https://www.linkedin.com/sales/search/people or /sales/lists/people)."
            )
        parts = urlsplit(url)
        if parts.username or parts.password:
            raise ValueError("That address is not a Sales Navigator search.")
    elif not keywords:
        raise ValueError(
            "Paste the address of a Sales Navigator search you built, or enter keywords."
        )
    try:
        pages = int(payload.get("max_pages") or 2)
    except (TypeError, ValueError):
        raise ValueError("max_pages must be a whole number.") from None
    if not 1 <= pages <= 5:
        raise ValueError("max_pages must be from 1 to 5.")
    request: dict[str, Any] = {"max_pages": pages}
    if url:
        request["url"] = url
    else:
        request["keywords"] = keywords
        if location:
            request["location"] = location
    return request


def normalize_sales_navigator(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, Mapping):
        return []
    leads: list[dict[str, Any]] = []
    for item in result.get("leads") or []:
        if not isinstance(item, Mapping):
            continue
        name = store.clean(item.get("name"), 160)
        if not name:
            continue
        signals = []
        degree = store.clean(item.get("degree"), 10)
        if degree:
            signals.append(
                {"type": "connection", "text": f"{degree}-degree connection", "url": ""}
            )
        for key, label in (
            ("tenure_in_role", "in role"),
            ("tenure_in_company", "at company"),
        ):
            value = store.clean(item.get(key), 60)
            if value:
                signals.append(
                    {"type": "tenure", "text": f"{value} {label}", "url": ""}
                )
        leads.append(
            {
                "kind": "person",
                "name": name,
                "first_name": item.get("first_name") or "",
                "last_name": item.get("last_name") or "",
                "title": item.get("title") or "",
                "company": item.get("company") or "",
                "location": item.get("location") or "",
                "linkedin_url": item.get("linkedin_url") or "",
                "sales_nav_url": item.get("profile_url") or "",
                "website": "",
                "source": "sales_navigator",
                "source_ref": store.clean(item.get("profile_url"), 300),
                "signals": signals,
            }
        )
    return leads


# ---------------------------------------------------------------------------
# CSV import
# ---------------------------------------------------------------------------

_HEADERS = {
    "name": {
        "name",
        "full name",
        "fullname",
        "contact",
        "contact name",
        "lead",
        "lead name",
    },
    "first_name": {"first name", "firstname", "given name", "first"},
    "last_name": {"last name", "lastname", "surname", "family name", "last"},
    "title": {"title", "job title", "position", "role", "headline"},
    "company": {
        "company",
        "company name",
        "organization",
        "organisation",
        "account",
        "employer",
    },
    "website": {"website", "web site", "url", "domain", "company website", "site"},
    "email": {"email", "e-mail", "email address", "work email"},
    "phone": {"phone", "phone number", "mobile", "telephone", "tel", "work phone"},
    "linkedin_url": {
        "linkedin",
        "linkedin url",
        "linkedin profile",
        "profile url",
        "linkedin profile url",
        "person linkedin url",
    },
    "location": {"location", "city", "region", "country", "address", "geography"},
    "industry": {"industry", "sector"},
    "notes": {"notes", "note", "comments", "comment"},
    "tags": {"tags", "tag", "labels"},
    "status": {"status", "stage"},
}


def parse_csv(text: str) -> tuple[list[dict[str, Any]], list[str]]:
    """``(leads, skipped_reasons)`` from CSV text with a header row; unknown columns are ignored."""
    if len(text) > MAX_CSV_CHARS:
        raise ValueError("That file is too large to import at once (3 MB).")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # type: ignore[assignment]
    reader = csv.reader(io.StringIO(text.lstrip("﻿")), dialect)
    try:
        header = next(reader)
    except StopIteration:
        raise ValueError("The file is empty.") from None
    mapping: dict[int, str] = {}
    for index, column in enumerate(header):
        key = re.sub(r"\s+", " ", column.strip().lower().replace("_", " "))
        for field, names in _HEADERS.items():
            if key in names and field not in mapping.values():
                mapping[index] = field
                break
    if not mapping:
        raise ValueError(
            "No known columns found. Use a header row with names such as Name, Title, Company, Email, Website, LinkedIn."
        )
    leads: list[dict[str, Any]] = []
    skipped: list[str] = []
    for number, row in enumerate(reader, start=2):
        if number - 1 > MAX_CSV_ROWS:
            skipped.append(f"Only the first {MAX_CSV_ROWS} rows are imported.")
            break
        if not any(cell.strip() for cell in row):
            continue
        record: dict[str, Any] = {"source": "import"}
        for index, field in mapping.items():
            if index < len(row):
                record[field] = row[index].strip()
        if isinstance(record.get("tags"), str):
            record["tags"] = [
                t for t in re.split(r"[;,|]", record["tags"]) if t.strip()
            ]
        record["kind"] = (
            "person"
            if (
                record.get("title")
                or record.get("first_name")
                or record.get("last_name")
            )
            else "company"
        )
        if not (
            record.get("name")
            or record.get("first_name")
            or record.get("last_name")
            or record.get("company")
        ):
            skipped.append(f"Row {number} has no name or company.")
            continue
        leads.append(record)
    return leads, skipped
