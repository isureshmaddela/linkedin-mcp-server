"""A stand-in Google News / RSS server for News tests (never touches the network).

    stub = NewsStub().start()
    monkeypatch.setenv("AIRA_NEWS_GOOGLE_BASE", stub.base_url)
    monkeypatch.setenv("AIRA_NEWS_ALLOW_LOCAL", "1")   # http to the loopback address, tests only

Paths: ``/rss`` (top stories), ``/rss/headlines/section/topic/<TOPIC>``, ``/rss/search?q=``, ``/atom.xml``,
``/bomb.xml`` (entity declarations), ``/doctype.xml``, ``/huge.xml`` (over 2 MB), ``/redirect-private`` (to a
private address), ``/redirect-file``, ``/broken`` (500) and ``/notfeed`` (HTML). Titles are in the language of
``hl`` (en, te, hi). ``stub.extra`` adds that many fresh items to every RSS answer; ``stub.down = True`` makes
every feed answer 503. ``stub.calls`` lists the paths requested.
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit
from xml.sax.saxutils import escape

TITLES = {
    "en": [
        ("Monsoon rains bring relief to farmers", "The Daily Herald"),
        ("New metro line opens in Hyderabad", "City News"),
        ("Scientists find water on distant moon", "Science Today"),
        ("Cricket team wins the series 3-1", "Sports Desk"),
        ("Bank holiday announced for Friday", "Money Times"),
    ],
    "te": [
        ("రైతులకు వర్షాలతో ఊరట", "ఈనాడు"),
        ("హైదరాబాద్‌లో కొత్త మెట్రో లైన్ ప్రారంభం", "సాక్షి"),
        ("దూర చంద్రుడిపై నీటి జాడ", "విజ్ఞానం"),
        ("సిరీస్ గెలిచిన క్రికెట్ జట్టు", "క్రీడలు"),
        ("శుక్రవారం బ్యాంకు సెలవు", "వ్యాపారం"),
    ],
    "hi": [
        ("किसानों को बारिश से राहत", "दैनिक समाचार"),
        ("हैदराबाद में नई मेट्रो लाइन शुरू", "शहर समाचार"),
        ("दूर के चंद्रमा पर पानी मिला", "विज्ञान आज"),
        ("क्रिकेट टीम ने सीरीज जीती", "खेल डेस्क"),
        ("शुक्रवार को बैंक अवकाश", "मनी टाइम्स"),
    ],
}
TOPIC_TITLES = {
    "SCIENCE": [
        ("Telescope spots a new comet", "Space News"),
        ("Robots learn to fold laundry", "Tech Lab"),
    ],
    "TECHNOLOGY": [
        ("New phone has a week-long battery", "Gadget Daily"),
        ("Robots learn to fold laundry", "Tech Lab"),
    ],
    "SPORTS": [("Local club reaches the final", "Sports Desk")],
    "ENTERTAINMENT": [("Film festival opens with a comedy", "Screen")],
    "WORLD": [
        ("Leaders meet on climate plan", "World Wire"),
        ("Attack on border post leaves two dead", "World Wire"),
    ],
    "NATION": [("Election dates announced", "National Desk")],
    "BUSINESS": [("Markets close higher", "Money Times")],
    "HEALTH": [("Walking 20 minutes a day helps the heart", "Health Hub")],
}


def _rss(
    items: list[tuple[str, str, str]], title: str = "Top stories - Google News"
) -> bytes:
    """items: (title, source, link)."""
    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<rss version="2.0" xmlns:media="http://search.yahoo.com/mrss/"><channel>',
        f"<title>{escape(title)}</title>",
    ]
    for index, (headline, source, link) in enumerate(items):
        image = (
            f'<media:content url="https://img.example/{index}.jpg" type="image/jpeg"/>'
            if index == 0
            else '<media:content url="http://img.example/insecure.jpg" type="image/jpeg"/>'
            if index == 1
            else ""
        )
        description = escape(
            f'<a href="{link}">{headline}</a>&nbsp;&nbsp;<font>{source}</font><script>x()</script>'
        )
        parts.append(
            f"<item><title>{escape(headline)} - {escape(source)}</title><link>{escape(link)}</link>"
            f"<pubDate>Sun, 25 Oct 2026 {23 - index % 24:02d}:00:00 GMT</pubDate>"  # the first item is the newest
            f'<description>{description}</description><source url="https://src.example">{escape(source)}</source>'
            f"{image}</item>"
        )
    parts.append(
        "<item><title>Bad link - Nowhere</title><link>javascript:alert(1)</link><source>Nowhere</source></item>"
    )
    parts.append("</channel></rss>")
    return "".join(parts).encode("utf-8")


ATOM = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Village Blog</title>
  <entry>
    <title>Harvest festival this weekend</title>
    <link rel="alternate" href="https://blog.example/harvest"/>
    <published>2026-10-24T09:00:00Z</published>
    <author><name>Village Blog</name></author>
    <summary type="html">&lt;p&gt;Bring the &lt;b&gt;whole family&lt;/b&gt;.&lt;/p&gt;</summary>
  </entry>
  <entry>
    <title>Library opens on Sundays</title>
    <link href="https://blog.example/library"/>
    <updated>2026-10-23T09:00:00Z</updated>
  </entry>
</feed>
"""

BOMB = b"""<?xml version="1.0"?>
<!DOCTYPE lolz [<!ENTITY lol "lol"><!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">]>
<rss version="2.0"><channel><title>&lol2;</title></channel></rss>"""
DOCTYPE = b"""<?xml version="1.0"?><!DOCTYPE rss SYSTEM "http://evil.example/x.dtd"><rss version="2.0"><channel><title>x</title></channel></rss>"""


class NewsStub:
    def __init__(self) -> None:
        self.calls: list[str] = []
        self.extra = 0
        self.down = False
        self._server: ThreadingHTTPServer | None = None

    @property
    def base_url(self) -> str:
        assert self._server is not None
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def count(self, prefix: str) -> int:
        return sum(1 for call in self.calls if call.startswith(prefix))

    def start(self) -> NewsStub:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args: Any) -> None:
                pass

            def _send(
                self,
                status: int,
                body: bytes,
                kind: str = "application/rss+xml; charset=utf-8",
                extra: dict[str, str] | None = None,
            ) -> None:
                self.send_response(status)
                self.send_header("Content-Type", kind)
                self.send_header("Content-Length", str(len(body)))
                for name, value in (extra or {}).items():
                    self.send_header(name, value)
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self) -> None:  # noqa: N802
                parts = urlsplit(self.path)
                query = parse_qs(parts.query)
                stub.calls.append(self.path)
                lang = (query.get("hl", ["en"])[0] or "en").split("-")[0]
                lang = lang if lang in TITLES else "en"
                path = parts.path
                if stub.down and path.startswith("/rss"):
                    self._send(503, b"down", "text/plain")
                    return
                if path == "/rss":
                    items = [
                        (t, s, f"https://news.example/{lang}/{i}")
                        for i, (t, s) in enumerate(TITLES[lang])
                    ]
                    items = [
                        (
                            f"Fresh story {n}",
                            "Wire",
                            f"https://news.example/{lang}/fresh/{n}",
                        )
                        for n in range(stub.extra)
                    ] + items
                    self._send(200, _rss(items))
                elif path.startswith("/rss/headlines/section/topic/"):
                    topic = path.rsplit("/", 1)[-1]
                    titles = TOPIC_TITLES.get(topic, [])
                    items = [
                        (t, s, f"https://news.example/{topic.lower()}/{i}")
                        for i, (t, s) in enumerate(titles)
                    ]
                    self._send(200, _rss(items, f"{topic} - Google News"))
                elif path == "/rss/search":
                    q = query.get("q", [""])[0]
                    items = [
                        (
                            f"{q} makes the news today",
                            "Search Wire",
                            f"https://news.example/search/{q.replace(' ', '-')}/1",
                        ),
                        (
                            f"More about {q}",
                            "Search Wire",
                            f"https://news.example/search/{q.replace(' ', '-')}/2",
                        ),
                    ]
                    self._send(200, _rss(items, f'"{q}" - Google News'))
                elif path == "/atom.xml":
                    self._send(200, ATOM, "application/atom+xml")
                elif path == "/bomb.xml":
                    self._send(200, BOMB, "application/xml")
                elif path == "/doctype.xml":
                    self._send(200, DOCTYPE, "application/xml")
                elif path == "/huge.xml":
                    self._send(
                        200,
                        b"<rss><channel>"
                        + b"x" * (2 * 1024 * 1024 + 10)
                        + b"</channel></rss>",
                        "application/xml",
                    )
                elif path == "/redirect-private":
                    self._send(
                        302, b"", "text/plain", {"Location": "http://10.0.0.1/rss"}
                    )
                elif path == "/redirect-file":
                    self._send(
                        302, b"", "text/plain", {"Location": "file:///etc/passwd"}
                    )
                elif path == "/redirect-ok":
                    self._send(302, b"", "text/plain", {"Location": "/atom.xml"})
                elif path == "/broken":
                    self._send(500, b"oops", "text/plain")
                elif path == "/notfeed":
                    self._send(200, b"<html><body>Hello</body></html>", "text/html")
                else:
                    self._send(404, b"not found", "text/plain")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.05},
            daemon=True,
        ).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
