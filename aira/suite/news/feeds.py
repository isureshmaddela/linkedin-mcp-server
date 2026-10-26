"""Languages, topics and feed addresses for News, and fetching and parsing RSS 2.0 and Atom safely.

Fetching follows the same rules as the Leads website reader (aira/leads/enrich.py): only public internet
addresses (checked on every redirect, and the connection goes to the address that was checked), https only,
at most three redirects, a 10 second timeout and a 2 MB cap. ``AIRA_NEWS_ALLOW_LOCAL=1`` (tests only) also allows
plain http to the loopback address. Parsing refuses documents with a DTD or entity declarations, keeps only text
(HTML in summaries is stripped), only http(s) links and only https images.
"""

from __future__ import annotations

import html
import http.client
import ipaddress
import os
import re
import socket
import ssl
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from typing import Any
from urllib.parse import quote_plus, urljoin, urlsplit

USER_AGENT = "AiraNews/1.0 (+headlines reader)"
GOOGLE_BASE_ENV = "AIRA_NEWS_GOOGLE_BASE"
ALLOW_LOCAL_ENV = (
    "AIRA_NEWS_ALLOW_LOCAL"  # tests only: plain http to the loopback address
)
DEFAULT_GOOGLE_BASE = "https://news.google.com"
TIMEOUT = 10.0
MAX_BYTES = 2 * 1024 * 1024
MAX_REDIRECTS = 3
MAX_ITEMS = 60

# Languages News reads, each with the regions (countries) it offers. Add a language here to support it.
LANGUAGES: dict[str, dict[str, Any]] = {
    "en": {"name": "English", "native": "English", "regions": ("IN", "US", "GB")},
    "te": {"name": "Telugu", "native": "తెలుగు", "regions": ("IN",)},
    "hi": {"name": "Hindi", "native": "हिन्दी", "regions": ("IN",)},
}
REGIONS = {"IN": "India", "US": "United States", "GB": "United Kingdom"}
DEFAULT_LANGUAGE = "en"
DEFAULT_REGION = "IN"

# "top" is the front page; the others are Google News sections.
TOPICS: dict[str, str] = {
    "top": "Top stories",
    "NATION": "Your country",
    "WORLD": "World",
    "BUSINESS": "Business",
    "TECHNOLOGY": "Technology",
    "ENTERTAINMENT": "Entertainment",
    "SPORTS": "Sports",
    "SCIENCE": "Science",
    "HEALTH": "Health",
}
CHILD_TOPICS = ("SCIENCE", "TECHNOLOGY", "SPORTS", "ENTERTAINMENT")
DEFAULT_TOPICS = ("top", "NATION", "TECHNOLOGY")
DEFAULT_CHILD_TOPICS = ("SCIENCE", "TECHNOLOGY", "SPORTS")
# Headlines a child should not see in child-safe mode (English, Hindi, Telugu); matched as words or stems.
CHILD_BLOCKLIST = (
    "murder",
    "kill",
    "killed",
    "killing",
    "attack",
    "war",
    "terror",
    "rape",
    "shooting",
    "shot dead",
    "bomb",
    "blast",
    "dead",
    "death",
    "dies",
    "died",
    "crash",
    "suicide",
    "drug",
    "sex",
    "porn",
    "violence",
    "hostage",
    "massacre",
    "abuse",
    "हत्या",
    "बलात्कार",
    "आतंक",
    "मौत",
    "हमला",
    "धमाका",
    "आत्महत्या",
    "హత్య",
    "అత్యాచారం",
    "ఉగ్ర",
    "మృతి",
    "దాడి",
    "పేలుడు",
    "ఆత్మహత్య",
)
_BLOCK = re.compile(
    "|".join(
        rf"\b{re.escape(word)}" if word.isascii() else re.escape(word)
        for word in CHILD_BLOCKLIST
    ),
    re.IGNORECASE,
)


class FeedError(Exception):
    """A feed could not be fetched or read safely (the reason is shown to the person)."""


@dataclass
class Article:
    title: str
    link: str
    summary: str = ""
    source: str = ""
    image: str = ""
    published_at: str = ""


@dataclass
class Feed:
    title: str = ""
    items: list[Article] = field(default_factory=list)


# ---------------------------------------------------------------------------
# addresses
# ---------------------------------------------------------------------------


def google_base() -> str:
    return (os.environ.get(GOOGLE_BASE_ENV) or DEFAULT_GOOGLE_BASE).rstrip("/")


def _locale(lang: str, region: str) -> str:
    hl = f"{lang}-{region}" if lang == "en" else lang
    return f"hl={hl}&gl={region}&ceid={region}:{lang}"


def top_url(lang: str, region: str) -> str:
    return f"{google_base()}/rss?{_locale(lang, region)}"


def topic_url(topic: str, lang: str, region: str) -> str:
    if topic == "top":
        return top_url(lang, region)
    if topic not in TOPICS:
        raise ValueError("Choose a topic from the list.")
    return (
        f"{google_base()}/rss/headlines/section/topic/{topic}?{_locale(lang, region)}"
    )


def search_url(query: str, lang: str, region: str) -> str:
    return f"{google_base()}/rss/search?q={quote_plus(query)}&{_locale(lang, region)}"


def child_safe_title(title: str) -> bool:
    """True when a headline is fine for a child (no blocklisted word)."""
    return not _BLOCK.search(title or "")


# ---------------------------------------------------------------------------
# fetching
# ---------------------------------------------------------------------------


def _allow_local() -> bool:
    return os.environ.get(ALLOW_LOCAL_ENV, "") == "1"


def _check_addresses(host: str, port: int) -> str:
    """Resolve ``host``; refuse anything that is not a public address (loopback only with the test switch)."""
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise FeedError(f"{host} could not be found.") from None
    allowed: list[str] = []
    for info in infos:
        address = str(info[4][0]).split("%")[0]
        try:
            ip = ipaddress.ip_address(address)
        except ValueError:
            continue
        if ip.is_global or (ip.is_loopback and _allow_local()):
            allowed.append(address)
        else:
            raise FeedError("Only public news sites can be read.")
    if not allowed:
        raise FeedError(f"{host} could not be found.")
    return allowed[0]


def _open(url: str) -> tuple[http.client.HTTPConnection, str, str]:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    if (
        scheme not in {"http", "https"}
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise FeedError("A news feed address starts with https://.")
    if scheme == "http" and not _allow_local():
        raise FeedError("A news feed address starts with https://.")
    host = parts.hostname
    port = parts.port or (443 if scheme == "https" else 80)
    address = _check_addresses(host, port)
    sock = socket.create_connection(
        (address, port), timeout=TIMEOUT
    )  # the address that was checked
    if scheme == "https":
        context = ssl.create_default_context()
        try:
            sock = context.wrap_socket(sock, server_hostname=host)
        except (ssl.SSLError, OSError) as error:
            sock.close()
            raise FeedError(
                f"{host} has no valid HTTPS certificate ({type(error).__name__})."
            ) from None
    connection = http.client.HTTPConnection(host, port, timeout=TIMEOUT)
    connection.sock = sock
    target = parts.path or "/"
    if parts.query:
        target += "?" + parts.query
    return connection, host, target


def fetch(url: str) -> bytes:
    """GET a feed (following up to three redirects, each one re-checked); the body, at most 2 MB."""
    current = url
    deadline = time.monotonic() + TIMEOUT * 2
    for _ in range(MAX_REDIRECTS + 1):
        connection, host, target = _open(current)
        try:
            connection.request(
                "GET",
                target,
                headers={
                    "Host": urlsplit(current).netloc,
                    "User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml;q=0.9, */*;q=0.5",
                    "Accept-Encoding": "identity",
                    "Connection": "close",
                },
            )
            response = connection.getresponse()
            if response.status in {301, 302, 303, 307, 308}:
                location = response.getheader("Location") or ""
                if not location:
                    raise FeedError("The news site redirected without saying where.")
                current = urljoin(current, location)
                continue
            if response.status >= 400:
                raise FeedError(f"{host} answered {response.status}.")
            body = response.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise FeedError("That feed is too large to read.")
            return body
        except (OSError, http.client.HTTPException) as error:
            raise FeedError(
                f"{host} did not answer ({type(error).__name__})."
            ) from None
        finally:
            connection.close()
            if time.monotonic() > deadline:
                raise FeedError("The news site took too long.")
    raise FeedError("The news site redirected too many times.")


# ---------------------------------------------------------------------------
# parsing
# ---------------------------------------------------------------------------

ATOM = "{http://www.w3.org/2005/Atom}"
MEDIA = "{http://search.yahoo.com/mrss/}"
_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)


class _Text(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.skip = 0

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag in {"script", "style"}:
            self.skip += 1
        elif tag in {"br", "p", "li", "div"}:
            self.parts.append(" ")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self.skip:
            self.skip -= 1

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.parts.append(data)


def plain_text(value: str, *, max_len: int = 600) -> str:
    """HTML (or text with entities) to plain text, whitespace collapsed."""
    if not value:
        return ""
    parser = _Text()
    try:
        parser.feed(value)
        parser.close()
        text = "".join(parser.parts)
    except Exception:  # noqa: BLE001 - badly formed HTML still gives its text
        text = re.sub(r"<[^>]+>", " ", value)
    text = " ".join(html.unescape(text).split())
    if len(text) > max_len:
        text = text[: max_len - 1].rsplit(" ", 1)[0] + "…"
    return text


def _safe_link(value: str) -> str:
    link = (value or "").strip()
    parts = urlsplit(link)
    if (
        parts.scheme.lower() in {"http", "https"}
        and parts.hostname
        and len(link) <= 2000
    ):
        return link
    return ""


def _safe_image(value: str) -> str:
    link = _safe_link(value)
    return link if link.lower().startswith("https://") else ""


def _when(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    moment: datetime | None = None
    try:
        moment = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        try:
            moment = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return ""
    if moment is None:
        return ""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _child_text(element: ET.Element, name: str) -> str:
    found = element.find(name)
    return (found.text or "") if found is not None and found.text else ""


def _image(item: ET.Element) -> str:
    for media in (*item.findall(f"{MEDIA}content"), *item.findall(f"{MEDIA}thumbnail")):
        url = _safe_image(media.get("url", ""))
        kind = media.get("type", "") or media.get("medium", "")
        if url and (not kind or kind.startswith("image")):
            return url
    for enclosure in item.findall("enclosure"):
        if (enclosure.get("type") or "").startswith("image"):
            url = _safe_image(enclosure.get("url", ""))
            if url:
                return url
    return ""


def _split_source(title: str, source: str) -> tuple[str, str]:
    """Google News titles end with " - Source"; keep the headline and the source apart."""
    if source and title.endswith(f" - {source}"):
        return title[: -len(source) - 3].strip(), source
    if not source and " - " in title:
        head, _, tail = title.rpartition(" - ")
        if head and 0 < len(tail) <= 60:
            return head.strip(), tail.strip()
    return title, source


def parse(body: bytes) -> Feed:
    """RSS 2.0 or Atom to a Feed; FeedError for anything else or anything unsafe."""
    if _DECLARATION.search(body):
        raise FeedError(
            "That feed uses a document type declaration, which Aira does not read."
        )
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        raise FeedError("That address is not a news feed (RSS or Atom).") from None
    feed = Feed()
    if root.tag == "rss" or root.find("channel") is not None:
        channel = root.find("channel")
        if channel is None:
            raise FeedError("That address is not a news feed (RSS or Atom).")
        feed.title = plain_text(_child_text(channel, "title"), max_len=120)
        for item in channel.findall("item")[:MAX_ITEMS]:
            title = plain_text(_child_text(item, "title"), max_len=300)
            link = _safe_link(_child_text(item, "link"))
            if not title or not link:
                continue
            source = plain_text(_child_text(item, "source"), max_len=80)
            title, source = _split_source(title, source)
            summary = plain_text(_child_text(item, "description"))
            if summary.startswith(title):
                summary = summary[len(title) :].strip(" -–") if summary != title else ""
            feed.items.append(
                Article(
                    title=title,
                    link=link,
                    summary=summary if summary != source else "",
                    source=source or feed.title,
                    image=_image(item),
                    published_at=_when(_child_text(item, "pubDate")),
                )
            )
        return feed
    if root.tag == f"{ATOM}feed":
        feed.title = plain_text(_child_text(root, f"{ATOM}title"), max_len=120)
        for entry in root.findall(f"{ATOM}entry")[:MAX_ITEMS]:
            title = plain_text(_child_text(entry, f"{ATOM}title"), max_len=300)
            link = ""
            for candidate in entry.findall(f"{ATOM}link"):
                if candidate.get("rel", "alternate") == "alternate":
                    link = _safe_link(candidate.get("href", ""))
                    if link:
                        break
            if not title or not link:
                continue
            author = entry.find(f"{ATOM}author")
            source = (
                plain_text(_child_text(author, f"{ATOM}name"), max_len=80)
                if author is not None
                else ""
            )
            summary = plain_text(
                _child_text(entry, f"{ATOM}summary")
                or _child_text(entry, f"{ATOM}content")
            )
            feed.items.append(
                Article(
                    title=title,
                    link=link,
                    summary=summary,
                    source=source or feed.title,
                    image=_image(entry),
                    published_at=_when(
                        _child_text(entry, f"{ATOM}published")
                        or _child_text(entry, f"{ATOM}updated")
                    ),
                )
            )
        return feed
    raise FeedError("That address is not a news feed (RSS or Atom).")


def read(url: str) -> Feed:
    """Fetch and parse one feed."""
    return parse(fetch(url))
