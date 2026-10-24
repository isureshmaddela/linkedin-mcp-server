"""Aira Jobs: Aira API's own job search over free, public job-board APIs.

No SerpApi key, no credits. Five boards publish open JSON APIs for exactly
this kind of use, each with its own terms, which this module follows:

===============  ===========================================  =====================================
Board            How Aira searches it                         Terms Aira follows
===============  ===========================================  =====================================
Himalayas        ``/jobs/api/search?q=&country=``              data refreshes daily: cached 12 h per
                                                             query; apply through ``applicationLink``;
                                                             credited on every result
The Muse         ``/api/public/jobs`` by category + location  credited; 3 pages at most per search
Remotive         the whole feed, filtered locally             at most 4 fetches a day (cached 6 h);
                                                             credited and linked back
Jobicy           ``?tag=`` keyword feed                       a sync at most hourly (cached 2 h);
                                                             credited, apply links go to Jobicy
Arbeitnow        the whole feed (page 1), filtered locally    "do not abuse": cached 3 h; linked back
===============  ===========================================  =====================================

Results come back in the ``google_jobs`` response shape (``jobs_results`` with
``title``, ``company_name``, ``location``, ``via``, ``description``,
``share_link``, ``apply_options``, ``detected_extensions``), so everything built
on Google Jobs (Careers, the Jobs tab importer, monitors and alerts) works on
them unchanged. Every item also carries ``aira_source`` and the board's
attribution. Nothing is sent anywhere: the listings are shown only to the person
using Aira, with links back to the board.

``LocalJobsClient`` plugs this into :func:`aira.airaapi.service.run_search` as
the client for the ``aira_jobs`` engine, so a search is recorded, cached and
counted (at zero credits) exactly like a SerpApi search.
"""

from __future__ import annotations

import hashlib
import html
import json
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from aira.airaapi import store
from aira.airaapi.client import RawResponse, SerpApiError

ENGINE_ID = "aira_jobs"
USER_AGENT = "Aira/1.0 (personal job search assistant; local, single user)"
FETCH_TIMEOUT_SECONDS = 20
MAX_BODY_BYTES = 8 * 1024 * 1024
DEFAULT_RESULTS = 40
MAX_RESULTS = 100
EMPLOYMENT_TYPES = {
    "full_time": "Full-time",
    "part_time": "Part-time",
    "contract": "Contractor",
    "internship": "Internship",
    "temporary": "Temporary",
}
JsonGetter = Callable[[str], Any]


@dataclass(frozen=True, slots=True)
class Board:
    id: str
    label: str
    homepage: str
    attribution: str
    cache_seconds: int
    # Board pages Aira may open to find the employer's own application form. A
    # board that refuses scripted visits (Himalayas answers 403) is never opened.
    follow_apply_page: bool


BOARDS: dict[str, Board] = {
    board.id: board
    for board in (
        Board(
            "himalayas",
            "Himalayas",
            "https://himalayas.app",
            "Jobs from Himalayas (himalayas.app)",
            12 * 3600,
            False,
        ),
        Board(
            "themuse",
            "The Muse",
            "https://www.themuse.com",
            "Jobs from The Muse (themuse.com)",
            3 * 3600,
            True,
        ),
        Board(
            "remotive",
            "Remotive",
            "https://remotive.com",
            "Jobs from Remotive (remotive.com)",
            6 * 3600,
            True,
        ),
        Board(
            "jobicy",
            "Jobicy",
            "https://jobicy.com",
            "Jobs from Jobicy (jobicy.com)",
            2 * 3600,
            True,
        ),
        Board(
            "arbeitnow",
            "Arbeitnow",
            "https://www.arbeitnow.com",
            "Jobs from Arbeitnow (arbeitnow.com)",
            3 * 3600,
            True,
        ),
    )
}
BOARD_HOSTS = {
    "himalayas.app": "himalayas",
    "www.themuse.com": "themuse",
    "themuse.com": "themuse",
    "remotive.com": "remotive",
    "jobicy.com": "jobicy",
    "www.arbeitnow.com": "arbeitnow",
    "arbeitnow.com": "arbeitnow",
}
# The Muse has no keyword search; a query is mapped onto its categories.
_MUSE_CATEGORIES = (
    (
        (
            "architect",
            "cloud",
            "devops",
            "sre",
            "infrastructure",
            "platform",
            "network",
            "security",
            "it ",
        ),
        ("Software Engineering", "IT"),
    ),
    (
        (
            "engineer",
            "developer",
            "software",
            "programmer",
            "frontend",
            "backend",
            "full stack",
            "mobile",
        ),
        ("Software Engineering",),
    ),
    (
        ("data", "analyst", "analytics", "scientist", "machine learning", "ml ", "ai "),
        ("Data and Analytics", "Data Science"),
    ),
    (("product manager", "product owner"), ("Product Management",)),
    (("project manager", "program manager", "scrum"), ("Project Management",)),
    (("designer", "ux", "ui "), ("Design and UX",)),
    (("marketing", "seo", "content", "growth"), ("Marketing",)),
    (("sales", "account executive", "business development"), ("Sales",)),
    (("recruiter", "talent", "hr ", "people"), ("Human Resources and Recruitment",)),
    (("finance", "accountant", "accounting"), ("Accounting and Finance",)),
    (("support", "customer success"), ("Customer Service",)),
)
_US_STATES = {
    "al",
    "ak",
    "az",
    "ar",
    "ca",
    "co",
    "ct",
    "de",
    "fl",
    "ga",
    "hi",
    "id",
    "il",
    "in",
    "ia",
    "ks",
    "ky",
    "la",
    "me",
    "md",
    "ma",
    "mi",
    "mn",
    "ms",
    "mo",
    "mt",
    "ne",
    "nv",
    "nh",
    "nj",
    "nm",
    "ny",
    "nc",
    "nd",
    "oh",
    "ok",
    "or",
    "pa",
    "ri",
    "sc",
    "sd",
    "tn",
    "tx",
    "ut",
    "vt",
    "va",
    "wa",
    "wv",
    "wi",
    "wy",
    "dc",
}
_COUNTRY_WORDS = {
    "united states": "US",
    "usa": "US",
    "us": "US",
    "america": "US",
    "canada": "CA",
    "united kingdom": "GB",
    "uk": "GB",
    "england": "GB",
    "germany": "DE",
    "deutschland": "DE",
    "india": "IN",
    "australia": "AU",
    "ireland": "IE",
    "netherlands": "NL",
    "france": "FR",
    "spain": "ES",
    "poland": "PL",
    "singapore": "SG",
}
_TAG_RE = re.compile(r"<[^>]+>")
_SPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANKS_RE = re.compile(r"\n{3,}")
_WORD_RE = re.compile(r"[a-z0-9+#]+")
_STOP = frozenset(
    {
        "and",
        "or",
        "the",
        "a",
        "an",
        "of",
        "for",
        "in",
        "to",
        "with",
        "remote",
        "job",
        "jobs",
        "role",
    }
)
_ATS_LINK_RE = re.compile(
    r"https://[A-Za-z0-9.-]*(?:greenhouse\.io|lever\.co|ashbyhq\.com|smartrecruiters\.com)/[^\"'\s<>\\]+",
    re.IGNORECASE,
)
_fetch_locks: dict[str, threading.Lock] = {board: threading.Lock() for board in BOARDS}


# --------------------------------------------------------------------------- #
# small helpers
# --------------------------------------------------------------------------- #


def plain_text(markup: Any, limit: int = 20_000) -> str:
    """HTML from a board as readable text (paragraphs and list items kept as lines)."""
    text = str(markup or "")
    text = re.sub(r"(?i)<\s*(br|/p|/li|/h[1-6]|/div|/tr)\s*/?>", "\n", text)
    text = re.sub(r"(?i)<\s*li[^>]*>", "\n- ", text)
    text = html.unescape(_TAG_RE.sub("", text))
    text = "\n".join(_SPACE_RE.sub(" ", line).strip() for line in text.splitlines())
    return _BLANKS_RE.sub("\n\n", text).strip()[:limit]


def _https(value: Any) -> str:
    text = str(value or "").strip()
    return text if text.startswith("https://") and len(text) <= 2000 else ""


def _words(text: str) -> list[str]:
    return [
        word
        for word in _WORD_RE.findall(str(text or "").casefold())
        if word not in _STOP
    ]


def matches_query(query: str, *texts: str) -> bool:
    """Every key word of ``query`` appears (as a word start) in the title or the other texts."""
    words = _words(query)
    if not words:
        return True
    haystack = " ".join(str(text or "") for text in texts).casefold()
    return all(
        re.search(rf"(?<![a-z0-9]){re.escape(word[:6])}", haystack) for word in words
    )


def country_code(location: str) -> str:
    """A two-letter country for a free-text location ('' when it cannot tell)."""
    text = " ".join(str(location or "").casefold().replace(",", " , ").split())
    if not text or text in {"remote", "anywhere", "worldwide"}:
        return ""
    for word, code in _COUNTRY_WORDS.items():
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", text):
            return code
    parts = [part.strip() for part in text.split(",") if part.strip()]
    if parts and parts[-1] in _US_STATES:
        return "US"
    return ""


def _ago(moment: datetime | None, now: datetime) -> str:
    if moment is None:
        return ""
    seconds = max(0, (now - moment).total_seconds())
    if seconds < 3600:
        return "just posted"
    if seconds < 86400:
        hours = int(seconds // 3600)
        return f"{hours} hour{'s' if hours != 1 else ''} ago"
    days = int(seconds // 86400)
    if days < 31:
        return f"{days} day{'s' if days != 1 else ''} ago"
    months = days // 30
    return f"{months} month{'s' if months != 1 else ''} ago"


def _when(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        if isinstance(value, (int, float)) or str(value).isdigit():
            return datetime.fromtimestamp(int(value), tz=timezone.utc)
        text = str(value).strip().replace("Z", "+00:00")
        moment = datetime.fromisoformat(text)
        return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)
    except (ValueError, OverflowError, OSError):
        return None


def _salary(low: Any, high: Any, currency: Any, period: Any) -> str:
    def number(value: Any) -> float | None:
        try:
            amount = float(value)
        except (TypeError, ValueError):
            return None
        return amount if amount > 0 else None

    lo, hi = number(low), number(high)
    if lo is None and hi is None:
        return ""
    symbol = {
        "USD": "$",
        "EUR": "€",
        "GBP": "£",
        "CAD": "CA$",
        "AUD": "A$",
        "INR": "₹",
    }.get(str(currency or "USD").upper(), f"{currency} ")
    unit = {
        "year": "a year",
        "yearly": "a year",
        "annual": "a year",
        "month": "a month",
        "monthly": "a month",
        "hour": "an hour",
        "hourly": "an hour",
    }.get(str(period or "year").casefold(), "a year")

    def short(amount: float) -> str:
        return (
            f"{symbol}{amount / 1000:.0f}K"
            if amount >= 1000
            else f"{symbol}{amount:.0f}"
        )

    if lo is not None and hi is not None and lo != hi:
        return f"{short(lo)}–{short(hi)} {unit}"
    single = lo if lo is not None else hi
    return f"{short(single)} {unit}" if single is not None else ""


def _job_id(board: str, raw: Any) -> str:
    """A short, storage-safe id that stays the same across searches."""
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(raw or "")).strip("-")
    if not text or len(text) > 90:
        text = hashlib.sha256(str(raw).encode("utf-8")).hexdigest()[:24]
    return f"{board}-{text}"


def _item(
    *,
    board: str,
    raw_id: Any,
    title: Any,
    company: Any,
    location: Any,
    description: Any,
    link: Any,
    apply_link: Any = None,
    posted: datetime | None,
    now: datetime,
    remote: bool,
    schedule: str = "",
    salary: str = "",
    logo: Any = None,
) -> dict[str, Any] | None:
    info = BOARDS[board]
    share = _https(link)
    apply = _https(apply_link) or share
    name = " ".join(str(title or "").split())[:300]
    if not name or not apply:
        return None
    detected: dict[str, Any] = {}
    posted_label = _ago(posted, now)
    if posted_label:
        detected["posted_at"] = posted_label
    if schedule:
        detected["schedule_type"] = schedule
    if remote:
        detected["work_from_home"] = True
    if salary:
        detected["salary"] = salary
    extensions = [
        value
        for value in (
            posted_label,
            schedule,
            "Work from home" if remote else "",
            salary,
        )
        if value
    ]
    return {
        "title": name,
        "company_name": " ".join(str(company or "").split())[:200],
        "location": " ".join(str(location or "").split())[:200]
        or ("Remote" if remote else ""),
        "via": f"via {info.label}",
        "description": plain_text(description),
        "job_id": _job_id(board, raw_id),
        "share_link": share,
        "apply_options": [{"title": f"Apply on {info.label}", "link": apply}],
        "detected_extensions": detected,
        "extensions": extensions,
        "thumbnail": _https(logo),
        "aira_source": board,
        "attribution": info.attribution,
        "posted_at_iso": posted.isoformat(timespec="seconds") if posted else None,
    }


def _employment(value: Any) -> str:
    text = str(value or "").casefold().replace("-", " ").replace("_", " ")
    if "full" in text:
        return "Full-time"
    if "part" in text:
        return "Part-time"
    if "contract" in text or "freelance" in text:
        return "Contractor"
    if "intern" in text:
        return "Internship"
    if "temp" in text:
        return "Temporary"
    return ""


# --------------------------------------------------------------------------- #
# fetching (polite, cached)
# --------------------------------------------------------------------------- #


def http_json(url: str) -> Any:
    """GET ``url`` and parse JSON, with Aira's user agent, a timeout and a size cap."""
    request = urllib.request.Request(
        url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
    )
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310 - fixed https hosts
            data = response.read(MAX_BODY_BYTES + 1)
    except urllib.error.HTTPError as error:
        code = "rate_limited" if error.code == 429 else "upstream_error"
        raise SerpApiError(
            code, f"The job board answered HTTP {error.code}.", error.code
        ) from None
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise SerpApiError(
            "unreachable", f"The job board could not be reached: {error}."
        ) from None
    if len(data) > MAX_BODY_BYTES:
        raise SerpApiError(
            "upstream_error", "The job board sent more data than Aira accepts."
        )
    try:
        return json.loads(data.decode("utf-8", "replace"))
    except json.JSONDecodeError:
        raise SerpApiError(
            "upstream_error", "The job board sent something that is not JSON."
        ) from None


class _Fetcher:
    """Board requests through the Aira API cache, one at a time per board."""

    def __init__(
        self, connection: sqlite3.Connection | None, getter: JsonGetter, now: datetime
    ) -> None:
        self.connection = connection
        self.getter = getter
        self.now = now
        self.calls = 0

    def get(self, board: str, url: str) -> Any:
        key = "jobboard:" + hashlib.sha256(url.encode("utf-8")).hexdigest()
        if self.connection is not None:
            cached = store.cache_get(self.connection, key, self.now)
            if cached is not None:
                return cached
        with _fetch_locks[board]:
            if self.connection is not None:
                cached = store.cache_get(self.connection, key, self.now)
                if cached is not None:
                    return cached
            data = self.getter(url)
            self.calls += 1
            if self.connection is not None:
                store.cache_set(
                    self.connection, key, data, BOARDS[board].cache_seconds, self.now
                )
                self.connection.commit()
        return data


# --------------------------------------------------------------------------- #
# boards
# --------------------------------------------------------------------------- #


def _himalayas(
    fetch: _Fetcher, query: Mapping[str, Any], now: datetime
) -> list[dict[str, Any]]:
    params = {"q": query["q"]}
    if query["country"]:
        params["country"] = query["country"]
    if (
        query["employment_type"] in EMPLOYMENT_TYPES
        and query["employment_type"] != "temporary"
    ):
        params["employment_type"] = {
            "full_time": "Full Time",
            "part_time": "Part Time",
            "contract": "Contractor",
            "internship": "Intern",
        }[query["employment_type"]]
    data = fetch.get(
        "himalayas",
        "https://himalayas.app/jobs/api/search?" + urllib.parse.urlencode(params),
    )
    out = []
    for raw in (data or {}).get("jobs") or []:
        if not isinstance(raw, Mapping):
            continue
        places = raw.get("locationRestrictions") or []
        item = _item(
            board="himalayas",
            raw_id=raw.get("guid") or raw.get("applicationLink"),
            title=raw.get("title"),
            company=raw.get("companyName"),
            location=", ".join(str(place) for place in places[:3])
            if places
            else "Remote (worldwide)",
            description=raw.get("description") or raw.get("excerpt"),
            link=raw.get("applicationLink"),
            posted=_when(raw.get("pubDate")),
            now=now,
            remote=True,
            schedule=_employment(raw.get("employmentType")),
            salary=_salary(
                raw.get("minSalary"),
                raw.get("maxSalary"),
                raw.get("currency"),
                raw.get("salaryPeriod"),
            ),
            logo=raw.get("companyLogo"),
        )
        if item:
            out.append(item)
    return out


def _muse_categories(text: str) -> list[str]:
    query = f" {text.casefold()} "
    for words, categories in _MUSE_CATEGORIES:
        if any(word in query for word in words):
            return list(categories)
    return []


def _themuse(
    fetch: _Fetcher, query: Mapping[str, Any], now: datetime
) -> list[dict[str, Any]]:
    categories = _muse_categories(query["q"])
    if not categories:
        return []  # a query The Muse cannot categorise would only return noise
    base: list[tuple[str, str]] = [("category", name) for name in categories]
    location = query["location"]
    if query["remote_only"]:
        base.append(("location", "Flexible / Remote"))
    elif "," in location:
        # The Muse knows "City, ST" places only; a country is filtered locally instead.
        base.append(("location", location))
    out = []
    for page in range(3):
        data = fetch.get(
            "themuse",
            "https://www.themuse.com/api/public/jobs?"
            + urllib.parse.urlencode([*base, ("page", str(page))]),
        )
        results = (data or {}).get("results") or []
        for raw in results:
            if not isinstance(raw, Mapping):
                continue
            places = [
                str(place.get("name"))
                for place in raw.get("locations") or []
                if isinstance(place, Mapping)
            ]
            remote = any("remote" in place.casefold() for place in places)
            item = _item(
                board="themuse",
                raw_id=raw.get("id"),
                title=raw.get("name"),
                company=(raw.get("company") or {}).get("name"),
                location=", ".join(places[:3]),
                description=raw.get("contents"),
                link=(raw.get("refs") or {}).get("landing_page"),
                posted=_when(raw.get("publication_date")),
                now=now,
                remote=remote,
                schedule="",
            )
            if item:
                out.append(item)
        if len(results) < 20:
            break
    return out


def _remotive(
    fetch: _Fetcher, query: Mapping[str, Any], now: datetime
) -> list[dict[str, Any]]:
    data = fetch.get("remotive", "https://remotive.com/api/remote-jobs")
    out = []
    for raw in (data or {}).get("jobs") or []:
        if not isinstance(raw, Mapping):
            continue
        item = _item(
            board="remotive",
            raw_id=raw.get("id"),
            title=raw.get("title"),
            company=raw.get("company_name"),
            location=raw.get("candidate_required_location") or "Remote",
            description=raw.get("description"),
            link=raw.get("url"),
            posted=_when(raw.get("publication_date")),
            now=now,
            remote=True,
            schedule=_employment(raw.get("job_type")),
            salary=str(raw.get("salary") or "")[:80],
            logo=raw.get("company_logo"),
        )
        if item:
            out.append(item)
    return out


def _jobicy(
    fetch: _Fetcher, query: Mapping[str, Any], now: datetime
) -> list[dict[str, Any]]:
    words = _words(query["q"])
    tag = sorted(words, key=lambda word: -len(word))[0] if words else ""
    params = {"count": "100"}
    if tag:
        params["tag"] = tag
    data = fetch.get(
        "jobicy",
        "https://jobicy.com/api/v2/remote-jobs?" + urllib.parse.urlencode(params),
    )
    out = []
    for raw in (data or {}).get("jobs") or []:
        if not isinstance(raw, Mapping):
            continue
        item = _item(
            board="jobicy",
            raw_id=raw.get("id"),
            title=html.unescape(str(raw.get("jobTitle") or "")),
            company=raw.get("companyName"),
            location=raw.get("jobGeo") or "Remote",
            description=raw.get("jobDescription") or raw.get("jobExcerpt"),
            link=raw.get("url"),
            posted=_when(raw.get("pubDate")),
            now=now,
            remote=True,
            schedule=_employment(
                raw.get("jobType")
                if not isinstance(raw.get("jobType"), list)
                else (raw.get("jobType") or [""])[0]
            ),
            salary=_salary(
                raw.get("salaryMin"),
                raw.get("salaryMax"),
                raw.get("salaryCurrency"),
                raw.get("salaryPeriod"),
            ),
            logo=raw.get("companyLogo"),
        )
        if item:
            out.append(item)
    return out


def _arbeitnow(
    fetch: _Fetcher, query: Mapping[str, Any], now: datetime
) -> list[dict[str, Any]]:
    data = fetch.get("arbeitnow", "https://www.arbeitnow.com/api/job-board-api")
    out = []
    for raw in (data or {}).get("data") or []:
        if not isinstance(raw, Mapping):
            continue
        types = raw.get("job_types") or []
        item = _item(
            board="arbeitnow",
            raw_id=raw.get("slug"),
            title=raw.get("title"),
            company=raw.get("company_name"),
            location=raw.get("location") or ("Remote" if raw.get("remote") else ""),
            description=raw.get("description"),
            link=raw.get("url"),
            posted=_when(raw.get("created_at")),
            now=now,
            remote=bool(raw.get("remote")),
            schedule=_employment(types[0] if types else ""),
        )
        if item:
            out.append(item)
    return out


_BOARD_SEARCH: dict[
    str, Callable[[_Fetcher, Mapping[str, Any], datetime], list[dict[str, Any]]]
] = {
    "himalayas": _himalayas,
    "themuse": _themuse,
    "remotive": _remotive,
    "jobicy": _jobicy,
    "arbeitnow": _arbeitnow,
}


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def _flag(value: Any) -> bool:
    return value in (True, 1, "1", "true", "yes", "on")


def parse_query(params: Mapping[str, Any]) -> dict[str, Any]:
    q = " ".join(str(params.get("q") or "").split())[:200]
    if not q:
        raise ValueError("Enter what to search for (q), such as cloud architect.")
    location = " ".join(str(params.get("location") or "").split())[:200]
    boards_raw = str(params.get("boards") or "").strip()
    boards = (
        [b.strip() for b in boards_raw.split(",") if b.strip()]
        if boards_raw
        else list(BOARDS)
    )
    unknown = [b for b in boards if b not in BOARDS]
    if unknown:
        raise ValueError(f"Unknown board: {unknown[0]}. Use {', '.join(BOARDS)}.")
    employment = str(params.get("employment_type") or "").strip()
    if employment and employment not in EMPLOYMENT_TYPES:
        raise ValueError(
            f"employment_type must be one of: {', '.join(EMPLOYMENT_TYPES)}."
        )
    try:
        num = int(str(params.get("num") or DEFAULT_RESULTS))
    except ValueError:
        raise ValueError("num must be a whole number.") from None
    remote_only = _flag(params.get("remote_only")) or location.casefold() in {
        "remote",
        "anywhere",
        "worldwide",
    }
    return {
        "q": q,
        "location": location,
        "country": str(params.get("country") or "").upper()[:2]
        or country_code(location),
        "remote_only": remote_only,
        "employment_type": employment,
        "boards": boards,
        "num": max(1, min(num, MAX_RESULTS)),
    }


def _keep(job: Mapping[str, Any], query: Mapping[str, Any]) -> bool:
    if not matches_query(query["q"], job["title"]):
        return False
    detected = job.get("detected_extensions") or {}
    if query["remote_only"] and not detected.get("work_from_home"):
        return False
    wanted = EMPLOYMENT_TYPES.get(query["employment_type"], "")
    if wanted and detected.get("schedule_type") and detected["schedule_type"] != wanted:
        return False
    location = query["location"]
    if location and not query["remote_only"]:
        text = str(job.get("location") or "").casefold()
        if detected.get("work_from_home"):
            # a remote job fits a location search when it is open to that country (or worldwide)
            country = query["country"]
            if country and text and "worldwide" not in text and "anywhere" not in text:
                names = [
                    name for name, code in _COUNTRY_WORDS.items() if code == country
                ]
                if not any(
                    name in text for name in names
                ) and country.casefold() not in _words(text):
                    return False
        else:
            city = location.split(",")[0].strip().casefold()
            if (
                city
                and city not in text
                and not (query["country"] and query["country"] == country_code(text))
            ):
                return False
    return True


def search(
    params: Mapping[str, Any],
    *,
    connection: sqlite3.Connection | None = None,
    getter: JsonGetter = http_json,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Search the boards and return a ``google_jobs``-shaped body."""
    moment = now or datetime.now(timezone.utc)
    query = parse_query(params)
    fetcher = _Fetcher(connection, getter, moment)
    jobs: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    counts: dict[str, int] = {}
    for board in query["boards"]:
        try:
            found = [
                job
                for job in _BOARD_SEARCH[board](fetcher, query, moment)
                if _keep(job, query)
            ]
        except SerpApiError as failure:
            errors[board] = failure.message
            continue
        except (ValueError, TypeError, KeyError, AttributeError) as failure:
            errors[board] = f"Unexpected data from {BOARDS[board].label}: {failure}"
            continue
        counts[board] = len(found)
        jobs.extend(found)
    if errors and not counts:
        raise SerpApiError(
            "upstream_error",
            "No job board could be searched: "
            + "; ".join(f"{BOARDS[b].label}: {m}" for b, m in errors.items()),
        )
    # Same posting on two boards: keep the first (boards are in preference order).
    seen: set[str] = set()
    unique = []
    for job in sorted(
        jobs, key=lambda item: item.get("posted_at_iso") or "", reverse=True
    ):
        identity = "|".join(_words(job["title"]) + ["@"] + _words(job["company_name"]))
        if identity in seen:
            continue
        seen.add(identity)
        unique.append(job)
    unique = unique[: query["num"]]
    for position, job in enumerate(unique, start=1):
        job["position"] = position
    body: dict[str, Any] = {
        "search_metadata": {
            "id": "",
            "status": "Success",
            "engine": ENGINE_ID,
            "created_at": moment.isoformat(timespec="seconds"),
            "provider": "aira",
            "board_requests": fetcher.calls,
        },
        "search_parameters": {
            "engine": ENGINE_ID,
            **{k: v for k, v in params.items() if v not in (None, "")},
        },
        "search_information": {
            "boards": [
                {
                    "id": board,
                    "name": BOARDS[board].label,
                    "results": counts.get(board, 0),
                    "error": errors.get(board, ""),
                    "homepage": BOARDS[board].homepage,
                }
                for board in query["boards"]
            ],
            "attribution": [
                BOARDS[board].attribution
                for board in query["boards"]
                if board in counts
            ],
        },
        "jobs_results": unique,
    }
    if not unique:
        body["error"] = "Aira Jobs hasn't returned any results for this query."
    return body


class LocalJobsClient:
    """The ``client`` :func:`service.run_search` uses for the ``aira_jobs`` engine."""

    def __init__(
        self,
        connection: sqlite3.Connection | None = None,
        getter: JsonGetter | None = None,
    ) -> None:
        self.connection = connection
        self.getter = getter or http_json

    def search(
        self, engine_id: str, params: Mapping[str, Any], options: Mapping[str, Any]
    ) -> RawResponse:
        if engine_id != ENGINE_ID:
            raise SerpApiError(
                "bad_request", f"Aira serves {ENGINE_ID} itself, not {engine_id}."
            )
        if options.get("output", "json") != "json" or options.get("async"):
            raise SerpApiError(
                "bad_request",
                "Aira Jobs answers JSON only, right away (no html, md or async).",
            )
        started = time.monotonic()
        try:
            body = search(params, connection=self.connection, getter=self.getter)
        except ValueError as error:
            raise SerpApiError("bad_request", str(error), 400) from None
        duration = int((time.monotonic() - started) * 1000)
        note = str(body.get("error") or "")
        return RawResponse(200, body, None, "", duration, note=note)


def resolve_application_url(
    url: str, *, page_getter: Callable[[str], str] | None = None
) -> str:
    """The employer's own application form behind a board's job page ('' when there is none).

    Only boards that allow ordinary visits are opened, and only a link to a
    known application system (Greenhouse, Lever, Ashby, SmartRecruiters) is
    accepted, which is what pressing the board's Apply button leads to.
    """
    host = (urllib.parse.urlsplit(url).hostname or "").casefold()
    board = BOARD_HOSTS.get(host)
    if board is None:
        return url
    if not BOARDS[board].follow_apply_page:
        return ""
    getter = page_getter or _http_text
    try:
        page = getter(url)
    except (SerpApiError, OSError, ValueError):
        return ""
    for match in _ATS_LINK_RE.finditer(html.unescape(page)):
        link = match.group(0).rstrip("\\.,);")
        if (
            "/jobs/" in link
            or "/apply" in link
            or re.search(r"lever\.co/[^/]+/[0-9a-f-]{36}", link)
            or "ashbyhq.com/" in link
        ):
            return link
    return ""


def _http_text(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:  # noqa: S310 - board hosts only
            return response.read(2 * 1024 * 1024).decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        raise SerpApiError(
            "upstream_error", f"The job board answered HTTP {error.code}.", error.code
        ) from None
    except (urllib.error.URLError, TimeoutError) as error:
        raise SerpApiError(
            "unreachable", f"The job board could not be reached: {error}."
        ) from None


JOB_SOURCE = "aira_jobs"
_SCHEDULE_TO_EMPLOYMENT = {
    "Full-time": "full_time",
    "Part-time": "part_time",
    "Contractor": "contract",
    "Internship": "internship",
    "Temporary": "temporary",
}


def posting_from_item(item: Mapping[str, Any]) -> Any:
    """A ``jobs_results`` item from this engine as a :class:`JobPosting` for Aira's pipeline."""
    from linkedin_mcp_server.job_sources import JobPosting

    detected = item.get("detected_extensions") or {}
    options = item.get("apply_options") or []
    apply_link = (
        _https(options[0].get("link"))
        if options and isinstance(options[0], Mapping)
        else ""
    )
    share = _https(item.get("share_link")) or apply_link
    posted = _when(item.get("posted_at_iso"))
    attribution = str(item.get("attribution") or "")
    description = str(item.get("description") or "")
    if attribution and attribution not in description:
        description = f"{description}\n\n{attribution}: {share}".strip()
    return JobPosting(
        source=JOB_SOURCE,
        external_id=str(item.get("job_id") or "")[:180],
        source_url=share,
        application_url=apply_link or share,
        title=str(item.get("title") or "")[:300],
        company=str(item.get("company_name") or "")[:200],
        location=str(item.get("location") or "")[:200],
        workplace_type="remote" if detected.get("work_from_home") else None,
        employment_type=_SCHEDULE_TO_EMPLOYMENT.get(
            str(detected.get("schedule_type") or "")
        ),
        description=description[:50000],
        posted_at=posted,
        application_method="external",
    )


def board_of(url: str) -> str:
    return BOARD_HOSTS.get(
        (urllib.parse.urlsplit(str(url or "")).hostname or "").casefold(), ""
    )


def board_labels(ids: Sequence[str] | None = None) -> list[str]:
    return [BOARDS[board].label for board in (ids or BOARDS)]
