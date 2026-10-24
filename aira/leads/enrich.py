"""Enrich a lead from its company's own public website: contact emails, phones and social links.

Only what the site itself publishes is read: the home page and a few obvious contact, about and team pages
of the same site. The fetcher refuses anything that is not a public internet address (no loopback, private
or link-local ranges, checked on every redirect, and the connection is made to the address that was
checked), honours ``robots.txt``, identifies itself, and caps time and size. Each contact detail keeps the
page it came from. Nothing is guessed or pattern-built: an address that is not on the site is not reported.
"""

from __future__ import annotations

import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
from collections.abc import Mapping
from dataclasses import dataclass
from html.parser import HTMLParser
from typing import Any
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

from aira.leads import store

USER_AGENT = "AiraLeadsBot/1.0 (+contact-enrichment; honours robots.txt)"
ALLOW_LOCAL_ENV = "AIRA_LEADS_ALLOW_LOCAL"  # tests and demos only
MAX_BYTES = 400_000
MAX_REDIRECTS = 3
MAX_PAGES = 4
TIMEOUT = 8.0
GENERIC_ROLES = frozenset(
    "info contact hello hi sales support admin office team enquiries inquiries help careers jobs press media "
    "marketing billing service customerservice mail email general reception booking bookings reservations orders".split()
)
_BAD_TLDS = (
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".js",
    ".css",
    ".woff",
    ".ico",
)
_PLACEHOLDER_DOMAINS = {
    "example.com",
    "domain.com",
    "yourdomain.com",
    "email.com",
    "sentry.io",
    "wixpress.com",
}
_EMAIL = re.compile(
    r"[A-Za-z0-9._%+\-']{1,64}@[A-Za-z0-9](?:[A-Za-z0-9\-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9\-]{1,63})*\.[A-Za-z]{2,24}"
)
_OBFUSCATED = re.compile(
    r"([A-Za-z0-9._%+\-']{1,64})\s*(?:\[at\]|\(at\)|\{at\}| at )\s*([A-Za-z0-9\-]+(?:\s*(?:\[dot\]|\(dot\)|\{dot\}|\.)\s*[A-Za-z0-9\-]+)+)",
    re.I,
)
_PHONE = re.compile(r"(?<![\w/.-])(\+?\d[\d\s().\-]{7,17}\d)(?![\w/-])")
_SOCIAL = {
    "linkedin": re.compile(
        r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[^\s\"'<>?#]+", re.I
    ),
    "twitter": re.compile(
        r"https?://(?:www\.)?(?:twitter|x)\.com/[A-Za-z0-9_]{1,30}(?![A-Za-z0-9_/])",
        re.I,
    ),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[^\s\"'<>?#]+", re.I),
    "instagram": re.compile(
        r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_.]{1,30}", re.I
    ),
    "youtube": re.compile(
        r"https?://(?:www\.)?youtube\.com/(?:c|channel|user|@)[^\s\"'<>?#]*", re.I
    ),
}
_PAGE_HINT = re.compile(r"contact|about|team|leadership|people|our-story|company", re.I)


class FetchError(Exception):
    """A page could not be fetched safely (the reason is shown to the person)."""


@dataclass
class Page:
    url: str
    status: int
    text: str
    seconds: float = 0.0


def _allow_local() -> bool:
    return os.environ.get(ALLOW_LOCAL_ENV, "") == "1"


def _check_addresses(
    host: str, port: int, *, allow_local: bool | None = None
) -> list[str]:
    """Resolve ``host`` and refuse anything that is not a public address.

    ``allow_local`` overrides the ``AIRA_LEADS_ALLOW_LOCAL`` development switch (False: never relax).
    """
    permit = _allow_local() if allow_local is None else allow_local
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FetchError(f"{host} could not be found.") from None
    addresses = []
    for info in infos:
        address = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if not ip.is_global and not permit:
            raise FetchError("Only public websites can be read.")
        addresses.append(address)
    if not addresses:
        raise FetchError(f"{host} could not be found.")
    return addresses


def _open(url: str, timeout: float) -> tuple[http.client.HTTPConnection, str, str]:
    parts = urlsplit(url)
    if (
        parts.scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise FetchError("Only http and https websites can be read.")
    host = parts.hostname
    port = parts.port or (443 if parts.scheme == "https" else 80)
    address = _check_addresses(host, port)[0]
    sock = socket.create_connection(
        (address, port), timeout=timeout
    )  # the address that was checked
    if parts.scheme == "https":
        context = ssl.create_default_context()
        try:
            sock = context.wrap_socket(sock, server_hostname=host)
        except (ssl.SSLError, OSError) as error:
            sock.close()
            raise FetchError(
                f"{host} has no valid HTTPS certificate ({type(error).__name__})."
            ) from None
    connection = http.client.HTTPConnection(host, port, timeout=timeout)
    connection.sock = sock
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return connection, host, target


def fetch(url: str, *, timeout: float = TIMEOUT, max_bytes: int = MAX_BYTES) -> Page:
    """GET a public page (following up to three redirects, each one re-checked)."""
    current = url
    started = time.monotonic()
    deadline = time.monotonic() + timeout * 2
    for _ in range(MAX_REDIRECTS + 1):
        connection, host, target = _open(current, timeout)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Host": urlsplit(current).netloc,
                    "User-Agent": USER_AGENT,
                    "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location") or ""
                if not location:
                    raise FetchError("The site redirected without saying where.")
                current = urljoin(current, location)
                continue
            kind = (
                (response.getheader("Content-Type") or "")
                .split(";", 1)[0]
                .strip()
                .lower()
            )
            if kind and not (
                kind.startswith("text/") or kind in {"application/xhtml+xml"}
            ):
                raise FetchError("That address is not a web page.")
            body = response.read(max_bytes + 1)[:max_bytes]
            charset = "utf-8"
            found = re.search(
                r"charset=([\w-]+)", response.getheader("Content-Type") or "", re.I
            )
            if found:
                charset = found.group(1)
            try:
                text = body.decode(charset, "replace")
            except LookupError:
                text = body.decode("utf-8", "replace")
            return Page(current, response.status, text, time.monotonic() - started)
        except (OSError, http.client.HTTPException) as error:
            raise FetchError(
                f"{host} did not answer ({type(error).__name__})."
            ) from None
        finally:
            connection.close()
            if time.monotonic() > deadline:
                raise FetchError("The site took too long.")
    raise FetchError("The site redirected too many times.")


def allowed_by_robots(url: str) -> bool:
    """False only when robots.txt disallows this path for everyone or for our name."""
    parts = urlsplit(url)
    robots = f"{parts.scheme}://{parts.netloc}/robots.txt"
    try:
        page = fetch(robots, timeout=5, max_bytes=100_000)
    except FetchError:
        return True
    if page.status >= 400:
        return True
    parser = robotparser.RobotFileParser()
    parser.parse(page.text.splitlines())
    return parser.can_fetch(USER_AGENT, url) and parser.can_fetch("AiraLeadsBot", url)


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------


class _Collector(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[tuple[str, str]] = []
        self.text: list[str] = []
        self.title = ""
        self.description = ""
        self._in_title = False
        self._skip = 0
        self._anchor: list[Any] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        data = {k: v or "" for k, v in attrs}
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag == "meta" and data.get("name", "").lower() == "description":
            self.description = store.clean(data.get("content"), 300)
        elif tag == "a" and data.get("href"):
            self._anchor = [data["href"], []]

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style", "noscript", "svg"} and self._skip:
            self._skip -= 1
        elif tag == "title":
            self._in_title = False
        elif tag == "a" and self._anchor is not None:
            self.links.append((self._anchor[0], " ".join(self._anchor[1])))
            self._anchor = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        if self._skip:
            return
        if self._anchor is not None:
            self._anchor[1].append(data.strip())
        if data.strip():
            self.text.append(data.strip())


def _kind(address: str) -> str:
    local = address.split("@", 1)[0].lower()
    return "generic" if local in GENERIC_ROLES else "personal"


def _emails(text: str, links: list[tuple[str, str]]) -> list[str]:
    found: list[str] = []
    for href, _label in links:
        if href.lower().startswith("mailto:"):
            address = href[7:].split("?", 1)[0].strip()
            if address:
                found.append(address)
    found.extend(_EMAIL.findall(text))
    for local, domain in _OBFUSCATED.findall(text):
        cleaned = re.sub(r"\s*(?:\[dot\]|\(dot\)|\{dot\})\s*", ".", domain, flags=re.I)
        found.append(f"{local}@{re.sub(r'\s+', '', cleaned)}")
    out: list[str] = []
    for address in found:
        address = address.strip().strip(".,;:<>()[]\"'").lower()
        if not re.fullmatch(
            r"[a-z0-9._%+\-']{1,64}@[a-z0-9.\-]+\.[a-z]{2,24}", address
        ):
            continue
        domain = address.split("@", 1)[1]
        if (
            domain.endswith(_BAD_TLDS)
            or domain in _PLACEHOLDER_DOMAINS
            or address in out
        ):
            continue
        out.append(address)
    return out


def _phones(text: str, links: list[tuple[str, str]]) -> list[str]:
    found: list[str] = []
    for href, _label in links:
        if href.lower().startswith("tel:"):
            found.append(href[4:].strip())
    found.extend(m.group(1) for m in _PHONE.finditer(text))
    out: list[str] = []
    seen: set[str] = set()
    for item in found:
        digits = re.sub(r"\D", "", item)
        if not 10 <= len(digits) <= 15 or digits in seen or len(set(digits)) < 4:
            continue
        seen.add(digits)
        out.append(store.clean(item, 30))
    return out


def parse_page(page: Page) -> dict[str, Any]:
    collector = _Collector()
    try:
        collector.feed(page.text)
        collector.close()
    except Exception:  # noqa: BLE001 - broken markup is common; keep what was read
        pass
    text = " ".join(collector.text)
    socials: dict[str, str] = {}
    haystack = " ".join(href for href, _ in collector.links)
    for network, pattern in _SOCIAL.items():
        found = pattern.search(haystack)
        if found:
            socials[network] = found.group(0).rstrip("/")
    return {
        "title": store.clean(collector.title, 200),
        "description": collector.description,
        "emails": _emails(text, collector.links),
        "phones": _phones(text, collector.links),
        "socials": socials,
        "text_chars": len(text),
        "links": collector.links,
    }


def enrich_website(website: str, *, max_pages: int = MAX_PAGES) -> dict[str, Any]:
    """Read a company's public site and report the contact details it publishes."""
    start = store._http_url(website)
    if not start:
        raise ValueError("This lead has no website to read.")
    domain = store.domain_of(start)
    result: dict[str, Any] = {
        "domain": domain,
        "emails": [],
        "phones": [],
        "socials": {},
        "title": "",
        "description": "",
        "pages": [],
        "errors": [],
        "audit": {},
        "robots_blocked": False,
    }
    queue = [start]
    seen: set[str] = set()
    email_pages: dict[str, str] = {}
    phone_pages: dict[str, str] = {}
    while queue and len(result["pages"]) < max_pages:
        url = queue.pop(0)
        key = url.split("#", 1)[0].rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        try:
            if not allowed_by_robots(url):
                result["robots_blocked"] = True
                result["errors"].append(
                    f"{urlsplit(url).path or '/'} is disallowed by robots.txt"
                )
                continue
            page = fetch(url)
        except FetchError as error:
            result["errors"].append(str(error))
            if url == start:
                break
            continue
        if page.status >= 400:
            result["errors"].append(
                f"{urlsplit(url).path or '/'} answered {page.status}"
            )
            continue
        parsed = parse_page(page)
        if url == start:
            result["audit"] = audit_home(page, parsed)
        result["pages"].append(page.url)
        result["title"] = result["title"] or parsed["title"]
        result["description"] = result["description"] or parsed["description"]
        for address in parsed["emails"]:
            email_pages.setdefault(address, page.url)
        for number in parsed["phones"]:
            phone_pages.setdefault(number, page.url)
        for network, link in parsed["socials"].items():
            result["socials"].setdefault(network, link)
        if url == start:
            hints = []
            for href, label in parsed["links"]:
                target = urljoin(page.url, href)
                if store.domain_of(target) == store.domain_of(
                    page.url
                ) and _PAGE_HINT.search(f"{urlsplit(target).path} {label}"):
                    hints.append(target.split("#", 1)[0])
            fallback = [
                urljoin(page.url, p) for p in ("/contact", "/contact-us", "/about")
            ]
            queue.extend(list(dict.fromkeys(hints + fallback)))
    own = domain
    ranked = sorted(
        email_pages,
        key=lambda a: (
            not (a.split("@", 1)[1] == own or a.split("@", 1)[1].endswith("." + own)),
            _kind(a) == "personal",
            a,
        ),
    )
    result["emails"] = [
        {
            "address": address,
            "kind": _kind(address),
            "same_domain": address.split("@", 1)[1] == own
            or address.split("@", 1)[1].endswith("." + own),
            "source_url": email_pages[address],
        }
        for address in ranked[:8]
    ]
    result["phones"] = [
        {"number": n, "source_url": phone_pages[n]} for n in list(phone_pages)[:5]
    ]
    return result


# ---------------------------------------------------------------------------
# a plain audit of the home page: what is visibly missing, not a score
# ---------------------------------------------------------------------------

_VIEWPORT = re.compile(r"<meta[^>]+name=[\"']viewport[\"']", re.I)
_ANALYTICS = re.compile(
    r"googletagmanager\.com|google-analytics\.com|gtag\(|analytics\.js|plausible\.io|matomo|hotjar|clarity\.ms|fathom|segment\.(?:com|io)|umami",
    re.I,
)
_AD_PIXELS = re.compile(
    r"fbq\(|connect\.facebook\.net|googleadservices|doubleclick\.net|[\"']AW-\d{6,}|snap\.licdn\.com|ads\.linkedin|analytics\.tiktok|static\.ads-twitter",
    re.I,
)
_COPYRIGHT = re.compile(
    r"(?:©|&copy;|&#169;|copyright)\s*(?:\d{4}\s*[-–]\s*)?(\d{4})", re.I
)
_BUILDER = re.compile(
    r"wixstatic\.com|squarespace\.com|weebly\.com|godaddysites|sitebuilder", re.I
)
SEVERITY = {"high": 3, "medium": 2, "low": 1}


def audit_home(
    page: Page, parsed: Mapping[str, Any], *, year: int | None = None
) -> dict[str, Any]:
    """What the home page visibly lacks, each finding with the service it points to (website, marketing, content, ads).

    These are checks anyone can repeat by viewing the page's source: no HTTPS, no mobile viewport tag, no meta
    description, no analytics or ad tags, an old copyright year, very little text, no social links, slow answer.
    """
    from datetime import datetime, timezone

    now_year = year or datetime.now(timezone.utc).year
    html = page.text
    findings: list[dict[str, str]] = []

    def add(
        key: str, label: str, severity: str, service: str, detail: str = ""
    ) -> None:
        findings.append(
            {
                "id": key,
                "label": label,
                "severity": severity,
                "service": service,
                "detail": detail,
            }
        )

    if urlsplit(page.url).scheme != "https":
        add(
            "no_https",
            "The site loads over http, not https",
            "high",
            "website",
            "Browsers label such sites 'Not secure'.",
        )
    if not _VIEWPORT.search(html):
        add(
            "no_viewport",
            "No mobile viewport tag",
            "high",
            "website",
            "The page is probably not built for phones.",
        )
    title = str(parsed.get("title") or "").strip()
    if len(title) < 10:
        add(
            "weak_title",
            "The page title is missing or very short",
            "medium",
            "marketing",
            f"Title: {title or 'none'}",
        )
    if not str(parsed.get("description") or "").strip():
        add(
            "no_description",
            "No meta description",
            "medium",
            "marketing",
            "Search results show whatever text Google picks.",
        )
    if not _ANALYTICS.search(html):
        add(
            "no_analytics",
            "No analytics tag found",
            "medium",
            "marketing",
            "Visitors are probably not being measured.",
        )
    if not _AD_PIXELS.search(html):
        add(
            "no_ad_pixels",
            "No ad pixels (Meta, Google Ads, LinkedIn, TikTok) found",
            "low",
            "ads",
            "Ads cannot retarget visitors.",
        )
    years = [int(y) for y in _COPYRIGHT.findall(html) if 1990 <= int(y) <= now_year + 1]
    if years and max(years) <= now_year - 2:
        add(
            "old_copyright",
            f"The footer says © {max(years)}",
            "low",
            "website",
            "The site may not have been updated in years.",
        )
    if int(parsed.get("text_chars") or 0) < 400:
        add(
            "thin_content",
            "Very little text on the home page",
            "medium",
            "content",
            f"{int(parsed.get('text_chars') or 0)} characters of text.",
        )
    if not parsed.get("socials"):
        add("no_social_links", "No social media links", "low", "marketing", "")
    if page.seconds > 3.0:
        add(
            "slow",
            f"The home page took {page.seconds:.1f} seconds to answer",
            "medium",
            "website",
            "",
        )
    findings.sort(key=lambda f: -SEVERITY[f["severity"]])
    return {
        "url": page.url,
        "seconds": round(page.seconds, 2),
        "builder": "a website builder" if _BUILDER.search(html) else "",
        "findings": findings,
        "summary": f"{len(findings)} thing{'s' if len(findings) != 1 else ''} visibly missing"
        if findings
        else "Nothing obvious is missing",
    }


def audit_signals(audit: Mapping[str, Any], limit: int = 3) -> list[dict[str, str]]:
    """The few most serious findings as ``weak_site`` signals for the lead."""
    return [
        {
            "type": "weak_site",
            "text": f"Site audit: {f['label']}",
            "url": str(audit.get("url") or ""),
        }
        for f in (audit.get("findings") or [])[:limit]
    ]


def best_email(enrichment: Mapping[str, Any]) -> str:
    """The email to put on the lead: one on the company's own domain first, a role address before nothing."""
    for item in enrichment.get("emails") or []:
        if item.get("same_domain"):
            return str(item["address"])
    return ""


def best_phone(enrichment: Mapping[str, Any]) -> str:
    phones = enrichment.get("phones") or []
    return str(phones[0]["number"]) if phones else ""
