"""Finding people and businesses that need web, marketing, content or advertising services.

Three kinds of evidence, each a fact you can open and check:

* **A public request**: a post (Reddit, Quora, LinkedIn posts or anywhere Google indexes) in which someone says
  they are looking for a web designer, a marketer, a writer or an ads specialist. Aira never replies for you;
  the lead links to the post.
* **A gap in a business's own presence**: a Google Maps business with no website listed, few reviews or a low
  rating, and (after "Enrich") a website audit: no HTTPS, no mobile viewport, no meta description, no analytics
  or ad tags, an old copyright year, very little text.
* **A hiring signal**: a company posting a job for the role you could do as a service (marketing manager,
  content writer, web developer, PPC specialist).

Every finding is stored as a signal with its type and, where there is one, its link. Nothing is inferred beyond
what the source shows.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

from aira.leads import store

SERVICES: dict[str, dict[str, Any]] = {
    "website": {
        "label": "Website design and development",
        "requests": [
            '"web designer"',
            '"website designer"',
            '"web developer"',
            '"website developer"',
            '"build a website"',
            '"new website"',
            '"redesign my website"',
            '"shopify developer"',
            '"wordpress developer"',
        ],
        "roles": [
            "web developer",
            "website designer",
            "wordpress developer",
            "front end developer",
        ],
        "needs": ["no_website", "weak_site"],
    },
    "marketing": {
        "label": "Digital marketing and SEO",
        "requests": [
            '"digital marketing"',
            '"marketing agency"',
            '"local SEO"',
            '"SEO"',
            '"grow my business online"',
            '"marketing help"',
        ],
        "roles": [
            "digital marketing manager",
            "SEO specialist",
            "marketing manager",
            "growth marketer",
        ],
        "needs": ["thin_reviews", "low_rating", "weak_site"],
    },
    "content": {
        "label": "Content creation and copywriting",
        "requests": [
            '"content writer"',
            '"copywriter"',
            '"blog writer"',
            '"content creator"',
            '"video editor"',
            '"ghostwriter"',
        ],
        "roles": [
            "content writer",
            "copywriter",
            "content marketing manager",
            "content creator",
            "video editor",
        ],
        "needs": ["weak_site"],
    },
    "ads": {
        "label": "Paid ads (Google and Meta)",
        "requests": [
            '"PPC"',
            '"Google Ads"',
            '"Facebook ads"',
            '"Meta ads"',
            '"paid media"',
            '"run ads"',
        ],
        "roles": [
            "PPC specialist",
            "paid media manager",
            "performance marketing manager",
        ],
        "needs": ["weak_site"],
    },
    "social": {
        "label": "Social media management",
        "requests": [
            '"social media manager"',
            '"social media management"',
            '"manage my social media"',
            '"instagram marketing"',
        ],
        "roles": ["social media manager", "community manager"],
        "needs": ["thin_reviews", "weak_site"],
    },
}
SERVICE_IDS = tuple(SERVICES)

ASKING = [
    '"looking for"',
    '"need a"',
    '"need someone"',
    '"anyone recommend"',
    '"can anyone recommend"',
    '"hiring a"',
    '"seeking"',
]
PLACES = {
    "reddit": "site:reddit.com",
    "quora": "site:quora.com",
    "linkedin": "site:linkedin.com/posts",
    "web": "",
}
FRESHNESS = {"week": "qdr:w", "month": "qdr:m", "year": "qdr:y"}
NEED_SIGNALS = frozenset(
    {"request", "no_website", "thin_reviews", "low_rating", "weak_site"}
)
BUDGET = re.compile(
    r"(\$\s?\d|\bbudget\b|\bquote\b|\bper hour\b|\bhourly rate\b|\bretainer\b|\bpay(?:ing)? \d)",
    re.I,
)
URGENT = re.compile(
    r"\b(asap|urgent|urgently|this week|immediately|right away|deadline)\b", re.I
)
_TITLE_TAIL = re.compile(r"\s*[:|\-–—]\s*(r/\w+|Reddit|Quora|LinkedIn).*$", re.I)


def catalog() -> list[dict[str, Any]]:
    """The service presets the Find tab offers."""
    return [
        {
            "id": key,
            "label": value["label"],
            "requests": value["requests"],
            "roles": value["roles"],
        }
        for key, value in SERVICES.items()
    ]


def _services(payload: Mapping[str, Any]) -> list[str]:
    raw = payload.get("services")
    if isinstance(raw, str):
        raw = [s for s in re.split(r"[,\s]+", raw) if s]
    if not isinstance(raw, Sequence) or isinstance(raw, str) or not raw:
        raise ValueError("Choose at least one service you offer.")
    chosen = list(dict.fromkeys(str(s).strip().lower() for s in raw))
    unknown = [s for s in chosen if s not in SERVICES]
    if unknown:
        raise ValueError(
            f"Unknown service: {unknown[0]}. Choose from {', '.join(SERVICE_IDS)}."
        )
    return chosen


def build_intent_params(
    payload: Mapping[str, Any],
) -> tuple[dict[str, Any], list[str], list[str]]:
    """Google web search for public posts where someone asks for the services you offer."""
    chosen = _services(payload)
    where = payload.get("where") or ["reddit", "web"]
    if isinstance(where, str):
        where = [where]
    places = [str(w).lower() for w in where]
    bad = [w for w in places if w not in PLACES]
    if bad:
        raise ValueError(f"where must be a list of: {', '.join(PLACES)}.")
    freshness = str(payload.get("freshness") or "month").lower()
    if freshness not in FRESHNESS:
        raise ValueError("freshness must be week, month or year.")
    location = store.clean(payload.get("location"), 100)
    terms: list[str] = []
    for key in chosen:
        terms.extend(SERVICES[key]["requests"][:4])
    asking = " OR ".join(ASKING[:5])
    wanted = " OR ".join(dict.fromkeys(terms))
    sites = [PLACES[p] for p in places if PLACES[p]]
    parts = []
    if sites and "web" not in places:
        parts.append("(" + " OR ".join(sites) + ")" if len(sites) > 1 else sites[0])
    parts.append(f"({asking})")
    parts.append(f"({wanted})")
    if location:
        parts.append(f'"{location}"')
    q = " ".join(parts)
    if len(q) > 480:
        raise ValueError("That search is too long; choose fewer services.")
    params: dict[str, Any] = {"q": q, "hl": "en", "tbs": FRESHNESS[freshness]}
    try:
        start = int(payload.get("start") or 0)
    except (TypeError, ValueError):
        raise ValueError("start must be a whole number.") from None
    if start < 0 or start > 90 or start % 10:
        raise ValueError("start must be a multiple of 10 up to 90.")
    if start:
        params["start"] = start
    applied = [
        "services: " + ", ".join(SERVICES[k]["label"] for k in chosen),
        "posted in the past " + freshness,
        "where: " + ", ".join(places),
    ]
    if location:
        applied.append(f"place: {location}")
    return (
        params,
        applied,
        [
            "These are public posts. Monosage never replies for you: open the post and answer in your own name, following that community's rules.",
        ],
    )


def _matched_services(text: str, chosen: Sequence[str]) -> list[str]:
    haystack = text.casefold()
    hits = []
    for key in chosen:
        for term in SERVICES[key]["requests"]:
            if term.strip('"').casefold() in haystack:
                hits.append(key)
                break
    return hits


def normalize_intent(result: Any, chosen: Sequence[str]) -> list[dict[str, Any]]:
    """Public posts that ask for a service: one ``request`` lead per post, with what the post says as the signal."""
    if not isinstance(result, Mapping):
        return []
    leads: list[dict[str, Any]] = []
    for item in result.get("organic_results") or []:
        if not isinstance(item, Mapping):
            continue
        link = str(item.get("link") or "")
        if not link.startswith(("http://", "https://")):
            continue
        title = _TITLE_TAIL.sub("", store.clean(item.get("title"), 200)) or store.clean(
            item.get("title"), 200
        )
        snippet = store.clean(item.get("snippet"), 400)
        text = f"{title} {snippet}"
        services = _matched_services(text, chosen) or list(chosen)
        signals: list[dict[str, str]] = [
            {"type": "request", "text": (snippet or title)[:200], "url": link}
        ]
        when = store.clean(item.get("date"), 40)
        notes = []
        if BUDGET.search(text):
            notes.append("mentions a budget or pay")
        if URGENT.search(text):
            notes.append("says it is urgent")
        if when:
            notes.append(f"posted {when}")
        if notes:
            signals.append(
                {
                    "type": "request_detail",
                    "text": "The post " + ", ".join(notes),
                    "url": "",
                }
            )
        host = store.domain_of(link)
        leads.append(
            {
                "kind": "request",
                "name": title or host,
                "company": "",
                "industry": ", ".join(SERVICES[s]["label"] for s in services),
                "location": "",
                "source": "google_intent",
                "source_ref": link[:300],
                "signals": signals,
                "tags": services,
                "notes": f"Found on {host}",
            }
        )
    return leads


# ---------------------------------------------------------------------------
# gaps in a business's own presence (from a Google Maps listing)
# ---------------------------------------------------------------------------

THIN_REVIEWS = 15
LOW_RATING = 4.0


def maps_needs(item: Mapping[str, Any]) -> list[dict[str, str]]:
    """Need signals read straight from a Google Maps listing: no website, few reviews, a low rating."""
    signals: list[dict[str, str]] = []
    if not str(item.get("website") or "").strip():
        signals.append(
            {
                "type": "no_website",
                "text": "No website listed on Google Maps",
                "url": "",
            }
        )
    reviews = item.get("reviews")
    if isinstance(reviews, int) and reviews < THIN_REVIEWS:
        signals.append(
            {
                "type": "thin_reviews",
                "text": f"Only {reviews} Google review{'s' if reviews != 1 else ''}",
                "url": "",
            }
        )
    rating = item.get("rating")
    if isinstance(rating, int | float) and rating and float(rating) < LOW_RATING:
        signals.append(
            {"type": "low_rating", "text": f"Rated {rating} on Google Maps", "url": ""}
        )
    return signals


# ---------------------------------------------------------------------------
# which service a lead's signals point to
# ---------------------------------------------------------------------------


def needed_services(
    lead: Mapping[str, Any], offered: Sequence[str]
) -> list[tuple[str, str]]:
    """``[(service, reason)]`` for the services you offer that this lead's own signals call for."""
    found: list[tuple[str, str]] = []
    signals = [s for s in (lead.get("signals") or []) if isinstance(s, Mapping)]
    kinds = {str(s.get("type")): str(s.get("text")) for s in signals}
    for service in offered:
        spec = SERVICES.get(service)
        if not spec:
            continue
        reason = ""
        for need in spec["needs"]:
            if need in kinds:
                reason = kinds[need]
                break
        if not reason and "request" in kinds and service in (lead.get("tags") or []):
            reason = "Asked for it in a public post"
        if not reason:
            for signal in signals:
                if signal.get("type") == "hiring" and any(
                    role.casefold() in str(signal.get("text", "")).casefold()
                    for role in spec["roles"]
                ):
                    reason = str(signal.get("text"))
                    break
        if reason:
            found.append((service, reason))
    return found
