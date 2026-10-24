"""Read-only helpers for LinkedIn Sales Navigator people searches.

Sales Navigator's class names are obfuscated and change often, so nothing here keys on them. The in-page probe
works from structure: a *lead* is an anchor whose ``href`` contains ``/sales/lead/``; its *row* is the smallest
ancestor (preferring the nearest ``li`` / ``tr`` / ``role=listitem``) that holds that lead and no other; the
company is the row's ``/sales/company/`` anchor. Title and location are the row's remaining text lines, told
apart by simple patterns. Those patterns are English; they are the one locale-dependent piece and are kept in
the tables below (``_DEGREE_RE``, ``_TENURE_RE``, ``_NOISE_RE``, ``_TITLE_WORDS``) so another locale can add its
own.

Nothing here clicks. The extractor navigates, scrolls and reads; connect, message, InMail and save controls are
never touched.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote, urlsplit

# ---------------------------------------------------------------------------
# URLs
# ---------------------------------------------------------------------------

SEARCH_PATH = "/sales/search/people"
LIST_PATH_RE = re.compile(r"^/sales/lists/people/[A-Za-z0-9_-]+/?$")
MAX_PAGES = 5

_HOSTS_FOR_PUBLIC_ORIGIN = {"linkedin.com", "www.linkedin.com"}
_PAGE_PARAM_RE = re.compile(r"(^|&)page=[^&]*")


def _origin_parts(origin: str) -> tuple[str, str]:
    parts = urlsplit(origin)
    return parts.scheme, parts.netloc.lower()


def validate_url(origin: str, url: str, *, lead_list: bool = False) -> tuple[str, bool]:
    """Check ``url`` is a Sales Navigator people search or lead list and return ``(clean_url, is_list)``.

    The page number is dropped (the reader sets it); every other parameter is kept byte for byte, because
    the ``query=(...)`` filter expression is the person's own and LinkedIn is picky about its encoding.
    """
    text = str(url or "").strip()
    scheme, netloc = _origin_parts(origin)
    parts = urlsplit(text)
    allowed_hosts = (
        _HOSTS_FOR_PUBLIC_ORIGIN if netloc in _HOSTS_FOR_PUBLIC_ORIGIN else {netloc}
    )
    if parts.scheme != scheme or parts.netloc.lower() not in allowed_hosts:
        raise ValueError(
            "url must be a Sales Navigator address on linkedin.com "
            "(https://www.linkedin.com/sales/search/people?... or a /sales/lists/people/<id> lead list)."
        )
    path = parts.path or "/"
    is_list = bool(LIST_PATH_RE.match(path))
    is_search = path.rstrip("/") == SEARCH_PATH
    if lead_list and not is_list:
        raise ValueError(
            "lead_list needs a lead list address (https://www.linkedin.com/sales/lists/people/<id>)."
        )
    if not (is_list or is_search):
        raise ValueError(
            "url must be a Sales Navigator people search (/sales/search/people) or a "
            "people lead list (/sales/lists/people/<id>)."
        )
    query = _PAGE_PARAM_RE.sub("", parts.query).lstrip("&")
    clean = f"{scheme}://{parts.netloc}{path}" + (f"?{query}" if query else "")
    return clean, is_list


def build_search_url(
    origin: str,
    *,
    url: str | None,
    keywords: str | None,
    location: str | None,
    lead_list: bool,
) -> tuple[str, bool, list[str]]:
    """The URL to read, whether it is a lead list, and any warnings about how it was built.

    The URL form is the reliable one: Sales Navigator filters (location, seniority, company size, ...) live in
    an expression the person builds in the product and copies. ``keywords`` builds a plain keyword search;
    a ``location`` is added to the keywords because Sales Navigator filters locations by geo id, which cannot
    be looked up from a name here.
    """
    warnings: list[str] = []
    if url and str(url).strip():
        clean, is_list = validate_url(origin, str(url), lead_list=lead_list)
        return clean, is_list, warnings
    if lead_list:
        raise ValueError("lead_list needs the lead list url.")
    terms = " ".join(
        part.strip()
        for part in (str(keywords or ""), str(location or ""))
        if part.strip()
    )
    if not terms:
        raise ValueError(
            "Give a Sales Navigator search url, or keywords (with an optional location)."
        )
    if len(terms) > 200:
        raise ValueError("keywords and location are limited to 200 characters.")
    if location and str(location).strip():
        warnings.append(
            "The location was added to the keywords: Sales Navigator filters locations by id. "
            "For exact filters, build the search in Sales Navigator and pass its url."
        )
    scheme, netloc = _origin_parts(origin)
    return (
        f"{scheme}://{netloc}{SEARCH_PATH}?query=(keywords:{quote(terms, safe='')})",
        False,
        warnings,
    )


def with_page(url: str, number: int) -> str:
    """``url`` addressed to page ``number`` (page 1 is the URL as given)."""
    if number <= 1:
        return url
    base, _, fragment = url.partition("#")
    path, _, query = base.partition("?")
    query = _PAGE_PARAM_RE.sub("", query).lstrip("&")
    joined = f"{query}&page={number}" if query else f"page={number}"
    return f"{path}?{joined}" + (f"#{fragment}" if fragment else "")


def is_results_path(path: str) -> bool:
    """Whether ``path`` is where a people search or lead list lives."""
    return path.rstrip("/") == SEARCH_PATH or bool(LIST_PATH_RE.match(path))


# ---------------------------------------------------------------------------
# In-page probes (structure only; no class names, no clicks)
# ---------------------------------------------------------------------------

_JS_PROLOGUE = r"""
    const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
    const isShown = el => {
        if (!el || el.hidden) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 || rect.height > 0;
    };
    const leadKey = href => {
        const found = (href || '').match(/\/sales\/lead\/([^,/?#]+)/);
        return found ? found[1] : '';
    };
    const cleanUrl = href => {
        try {
            const url = new URL(href, location.href);
            return url.origin + url.pathname;
        } catch (error) {
            return '';
        }
    };
    const leadAnchors = () => Array.from(document.querySelectorAll('a[href*="/sales/lead/"]'))
        .filter(a => leadKey(a.getAttribute('href')));
"""

# What kind of page is this: lead rows, search controls, and the words of an upsell.
STATE_JS = (
    "() => {"
    + _JS_PROLOGUE
    + r"""
    const keys = new Set(leadAnchors().map(a => leadKey(a.getAttribute('href'))));
    const inputs = Array.from(document.querySelectorAll(
        'input:not([type="hidden"]):not([type="checkbox"]):not([type="radio"]), [role="search"], [role="combobox"]'
    )).filter(isShown);
    const body = normalize((document.body && document.body.innerText) || '');
    return {
        path: location.pathname,
        host: location.host,
        lead_count: keys.size,
        search_controls: inputs.length,
        body: body.slice(0, 1500),
    };
}"""
)

# One scroll step inside the results container (or the window), reporting how many distinct leads exist.
SCROLL_JS = (
    "({ fraction }) => {"
    + _JS_PROLOGUE
    + r"""
    const anchors = leadAnchors();
    const count = new Set(anchors.map(a => leadKey(a.getAttribute('href')))).size;
    let container = anchors.length ? anchors[anchors.length - 1].parentElement : null;
    while (container && container !== document.body && container !== document.documentElement) {
        const style = window.getComputedStyle(container);
        const scrolls = style.overflowY === 'auto' || style.overflowY === 'scroll';
        if (scrolls && container.scrollHeight > container.clientHeight + 4) break;
        container = container.parentElement;
    }
    const scroller = container && container !== document.body && container !== document.documentElement
        ? container
        : (document.scrollingElement || document.documentElement);
    const before = scroller.scrollTop;
    scroller.scrollTop = Math.min(scroller.scrollHeight, before + scroller.clientHeight * fraction);
    const atBottom = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 4;
    return { count, atBottom, moved: scroller.scrollTop !== before };
}"""
)

# The rows currently rendered: structural facts only. Classification happens in Python (classify_row).
ROWS_JS = (
    "() => {"
    + _JS_PROLOGUE
    + r"""
    const main = document.querySelector('main') || document.body;
    const groups = new Map();
    leadAnchors().forEach(a => {
        const key = leadKey(a.getAttribute('href'));
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key).push(a);
    });
    const keysIn = el => new Set(
        Array.from(el.querySelectorAll('a[href*="/sales/lead/"]'))
            .map(a => leadKey(a.getAttribute('href'))).filter(Boolean)
    );
    const rowLike = el => el.tagName === 'LI' || el.tagName === 'TR'
        || el.getAttribute('role') === 'listitem' || el.getAttribute('role') === 'row';
    const rowOf = (group, key) => {
        let node = group[0];
        while (node && node !== main && !group.every(a => node.contains(a))) node = node.parentElement;
        if (!node) return group[0];
        let best = node;
        let probe = node;
        for (let depth = 0; probe && probe !== main && probe !== document.body && depth < 14; depth += 1) {
            const keys = keysIn(probe);
            if (keys.size > 1 || (keys.size === 1 && !keys.has(key))) break;
            best = probe;
            if (rowLike(probe)) break;
            probe = probe.parentElement;
        }
        return best;
    };
    const rows = [];
    groups.forEach((group, key) => {
        const row = rowOf(group, key);
        const textOf = el => normalize(el ? (el.innerText || el.textContent) : '');
        const named = group.find(a => textOf(a)) || group[0];
        const nameLine = ((named.innerText || named.textContent || '').split('\n')
            .map(normalize).filter(Boolean)[0]) || normalize(named.getAttribute('aria-label'));
        const companyAnchors = Array.from(row.querySelectorAll('a[href*="/sales/company/"]'));
        const companyAnchor = companyAnchors.find(a => textOf(a)) || companyAnchors[0] || null;
        const publicCompany = Array.from(row.querySelectorAll('a[href*="/company/"]'))
            .find(a => !/\/sales\//.test(a.getAttribute('href') || ''));
        const publicProfile = Array.from(row.querySelectorAll('a[href]'))
            .find(a => /\/in\/[^/?#]+/.test(a.getAttribute('href') || '')
                && !/\/sales\//.test(a.getAttribute('href') || ''));
        const buttonTexts = new Set();
        row.querySelectorAll('button, [role="button"]').forEach(el => {
            [textOf(el), normalize(el.getAttribute('aria-label'))].forEach(t => { if (t) buttonTexts.add(t); });
        });
        if (publicProfile && textOf(publicProfile)) buttonTexts.add(textOf(publicProfile));
        const lines = ((row.innerText || row.textContent || '').split('\n'))
            .map(normalize).filter(Boolean).filter(line => !buttonTexts.has(line));
        rows.push({
            key,
            profile_url: cleanUrl(group[0].getAttribute('href')),
            name: nameLine,
            company: companyAnchor ? textOf(companyAnchor) : '',
            company_sales_url: companyAnchor ? cleanUrl(companyAnchor.getAttribute('href')) : '',
            company_url: publicCompany ? cleanUrl(publicCompany.getAttribute('href')) : '',
            linkedin_url: publicProfile ? cleanUrl(publicProfile.getAttribute('href')) : '',
            lines,
        });
    });
    return rows;
}"""
)

# ---------------------------------------------------------------------------
# Row classification (English patterns; one table per concern)
# ---------------------------------------------------------------------------

_DEGREE_RE = re.compile(
    r"(?:^|[\s·•|\-–—])(1st|2nd|3rd)\+?(?=$|[\s·•|\-–—])", re.IGNORECASE
)
_TENURE_RE = re.compile(
    r"((?:\d+\s*(?:years?|yrs?)(?:\s*\d+\s*(?:months?|mos?))?)|(?:\d+\s*(?:months?|mos?))"
    r"|less than a (?:year|month))\s+in\s+(role|position|company)",
    re.IGNORECASE,
)
_NOISE_RE = re.compile(
    r"^(?:message|save|save to list|saved|connect|pending|view profile|more actions?|inmail|follow|"
    r"select|selected|open profile|\d+\s+shared connections?|shared connections?|"
    r"posted on linkedin|has posted on linkedin|recent activity|changed jobs|new in role|"
    r"premium|sales navigator|[\d\s·•|+\-]+)$",
    re.IGNORECASE,
)
_LOCATION_HINT_RE = re.compile(
    r"\b(area|region|metropolitan|metro|county|province|state of|greater)\b",
    re.IGNORECASE,
)
_LOCATION_SHAPE_RE = re.compile(r"^[^\d@|]{2,60}(?:,\s*[^\d@|,]{2,40}){1,3}$")
_TITLE_WORDS = re.compile(
    r"\b(manager|director|engineer|head|vp|vice president|chief|founder|co-founder|president|owner|lead|"
    r"officer|specialist|consultant|analyst|architect|developer|executive|partner|recruiter|"
    r"coordinator|supervisor|administrator|associate|principal|strategist|advisor|adviser|"
    r"representative|designer|scientist|ceo|cto|cfo|coo|cio|cmo|chro|svp|evp|sdr|bdr)\b",
    re.IGNORECASE,
)
_CREDENTIALS = {
    "phd",
    "mba",
    "pmp",
    "cpa",
    "md",
    "msc",
    "ms",
    "ma",
    "bsc",
    "cissp",
    "cfa",
    "pe",
    "esq",
    "jr",
    "sr",
}


def _is_noise(line: str) -> bool:
    return bool(_NOISE_RE.match(line))


def _looks_like_location(line: str) -> bool:
    if _TITLE_WORDS.search(line):
        return False
    return bool(_LOCATION_HINT_RE.search(line) or _LOCATION_SHAPE_RE.match(line))


def split_name(name: str) -> tuple[str, str, str]:
    """``(clean_name, first, last)``; credentials after a comma ("Jane Doe, MBA") are not part of the name."""
    text = re.sub(r"\s+", " ", name or "").strip()
    head, _, tail = text.partition(",")
    if tail and all(
        part.strip(" .").lower() in _CREDENTIALS
        for part in re.split(r"[,\s]+", tail)
        if part.strip(" .")
    ):
        text = head.strip()
    tokens = text.split(" ")
    first = tokens[0] if tokens and tokens[0] else ""
    last = " ".join(tokens[1:]) if len(tokens) > 1 else ""
    return text, first, last


def classify_row(raw: dict[str, Any]) -> dict[str, Any]:
    """Turn one probed row into a lead. Anything it cannot tell stays empty; nothing is invented."""
    raw_name = str(raw.get("name") or "")
    name, first, last = split_name(raw_name)
    company = str(raw.get("company") or "").strip()
    lines = [str(line).strip() for line in raw.get("lines") or [] if str(line).strip()]
    original_lines = list(lines)

    degree = ""
    tenure = {"role": "", "company": ""}
    remaining: list[str] = []
    name_fold = raw_name.casefold()
    for line in lines:
        text = line
        # the name, possibly followed by the connection degree on the same line
        if name_fold and text.casefold().startswith(name_fold):
            text = text[len(raw_name) :].strip(" ,·•|-–—")
        found = _DEGREE_RE.search(text)
        if found:
            degree = degree or found.group(1).lower()
            text = (text[: found.start()] + " " + text[found.end() :]).strip(" ·•|-–—")
        for match in _TENURE_RE.finditer(text):
            kind = "company" if match.group(2).lower() == "company" else "role"
            tenure[kind] = tenure[kind] or re.sub(r"\s+", " ", match.group(1)).strip()
        text = _TENURE_RE.sub(" ", text)
        text = re.sub(r"\s+", " ", text).strip(" ·•|-–—")
        if not text or _is_noise(text):
            continue
        if text.casefold() == name_fold or (
            company and text.casefold() == company.casefold()
        ):
            continue
        remaining.append(text)

    title = ""
    location = ""
    title_candidates = [
        line for line in remaining if _TITLE_WORDS.search(line) or " at " in line
    ]
    if title_candidates:
        title = title_candidates[0]
    location_candidates = [
        line for line in remaining if line != title and _looks_like_location(line)
    ]
    if location_candidates:
        location = location_candidates[-1]
    if not title:
        spare = [line for line in remaining if line != location]
        title = spare[0] if spare else ""

    if title and " at " in title:
        before, _, after = title.rpartition(" at ")
        if not company:
            company = after.strip()
            title = before.strip()
        elif after.strip().casefold() == company.casefold():
            title = before.strip()
    if company and title.casefold() == company.casefold():
        title = ""

    return {
        "name": name,
        "first_name": first,
        "last_name": last,
        "title": title,
        "company": company,
        "company_url": str(raw.get("company_url") or ""),
        "company_sales_url": str(raw.get("company_sales_url") or ""),
        "location": location,
        "profile_url": str(raw.get("profile_url") or ""),
        "linkedin_url": str(raw.get("linkedin_url") or ""),
        "degree": degree,
        "tenure_in_role": tenure["role"],
        "tenure_in_company": tenure["company"],
        "raw_lines": original_lines,
    }


# ---------------------------------------------------------------------------
# Access detection (English wording of an upsell; the URL check is the locale-free signal)
# ---------------------------------------------------------------------------

_UPSELL_RE = re.compile(
    r"(free trial|start (?:your )?free|upgrade|subscribe|get started|contact sales|request a demo|"
    r"try sales navigator|unlock|learn more about sales navigator)",
    re.IGNORECASE,
)


def looks_like_upsell(state: dict[str, Any]) -> bool:
    """A page about Sales Navigator rather than one inside it: no leads, no search controls, an offer to buy."""
    if state.get("lead_count") or state.get("search_controls"):
        return False
    body = str(state.get("body") or "")
    return "sales navigator" in body.lower() and bool(_UPSELL_RE.search(body))
