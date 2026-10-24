"""A stand-in LinkedIn Sales Navigator for tests (never talks to linkedin.com).

It serves Sales-Navigator-shaped pages so the real extractor and a real Chromium can be exercised::

    stub = SalesNavStub().start()
    monkeypatch.setattr(extractor_module, "_LINKEDIN_ORIGIN", stub.base_url)
    ...
    assert stub.writes == []          # nothing was ever clicked
    stub.stop()

Pages (all under ``/sales/search/people``; the ``query=(...)`` parameter is free-form, the stub's own
switches are separate parameters):

* ``layout=a`` (default): ``<ul><li>`` rows with obfuscated class names, rows rendered lazily inside a scrolling
  results container (8 at first, 8 more whenever the container is scrolled near its end).
* ``layout=b``: plain ``div`` cards with different obfuscated classes, an avatar-only anchor next to the name
  anchor, a public ``/in/`` link, rows rendered lazily on *window* scroll.
* ``total=N`` leads (default 60), 25 per ``page=``; ``mode=`` switches: ``empty`` (search controls, no leads),
  ``upsell`` (a page selling Sales Navigator at the same path), ``redirect`` (302 to the Premium page),
  ``login`` (302 to /login), ``checkpoint`` (302 to /checkpoint), ``ratelimit`` (a bare "Too many requests"
  page), ``late`` (rows appear after a delay), ``repeat`` (every page repeats page 1), ``fail2`` (page 2 hits the
  login wall).
* ``/sales/lists/people/<id>``: a lead list, same rows.

Every row carries Message and Save buttons that POST to ``/sales/api/...`` when clicked; ``stub.writes`` records
any such request (and any other non-GET), so a test can prove the reader never clicked anything.
"""

from __future__ import annotations

import html
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

PAGE_SIZE = 25
FIRST = [
    "Jane",
    "Marcus",
    "Priya",
    "Liam",
    "Sofia",
    "Chen",
    "Aisha",
    "Tomas",
    "Nora",
    "Ravi",
]
LAST = [
    "Doe",
    "Hale",
    "Nair",
    "Okafor",
    "Silva",
    "Wang",
    "Khan",
    "Novak",
    "Berg",
    "Iyer",
]
TITLES = [
    "VP of Engineering",
    "Director, Data Platform",
    "Head of Growth",
    "Senior Account Executive",
    "Chief Technology Officer",
    "Founder and CEO",
]
COMPANIES = [
    "Acme Corp",
    "Globex",
    "Initech",
    "Umbrella Labs",
    "Hooli",
    "Stark Industries",
]
LOCATIONS = [
    "Austin, Texas, United States",
    "Greater Seattle Area",
    "London, England, United Kingdom",
    "Toronto, Ontario, Canada",
]
DEGREES = ["1st", "2nd", "3rd"]


def make_leads(total: int) -> list[dict[str, str]]:
    """Deterministic leads: lead ``i`` is always the same person."""
    leads = []
    for i in range(total):
        first, last = FIRST[i % len(FIRST)], LAST[(i * 3) % len(LAST)]
        leads.append(
            {
                "i": str(i),
                "key": f"ACwAAB{i:05d}xYz",
                "name": f"{first} {last}",
                "title": TITLES[i % len(TITLES)],
                "company": COMPANIES[i % len(COMPANIES)],
                "company_id": str(1000 + i % len(COMPANIES)),
                "location": LOCATIONS[i % len(LOCATIONS)],
                "degree": DEGREES[i % 3],
                "role": f"{1 + i % 4} years {1 + i % 11} months",
                "tenure": f"{2 + i % 6} years",
                "slug": f"{first}-{last}-{i}".lower(),
            }
        )
    return leads


def _lead_href(lead: dict[str, str]) -> str:
    return (
        f"/sales/lead/{lead['key']},NAME_SEARCH,Ab{lead['i']}?_ntb=tracking-{lead['i']}"
    )


def row_a(lead: dict[str, str]) -> str:
    """Layout A: list items, one block per fact."""
    return (
        f'<li class="_q1 _7zk"><div class="_c9 _a1">'
        f'<div class="_av"><a href="{_lead_href(lead)}"><img alt="" width="40" height="40"></a></div>'
        f'<div class="_bd"><div class="_hd"><a class="_nm" href="{_lead_href(lead)}"><span>{html.escape(lead["name"])}</span></a>'
        f'<span class="_dg"> · {lead["degree"]}</span></div>'
        f'<div class="_t1">{html.escape(lead["title"])}</div>'
        f'<div class="_cm"><a href="/sales/company/{lead["company_id"]}?_ntb=t"><span>{html.escape(lead["company"])}</span></a></div>'
        f'<div class="_lc">{html.escape(lead["location"])}</div>'
        f'<div class="_tn">{lead["role"]} in role</div><div class="_tn">{lead["tenure"]} in company</div>'
        f'<div class="_ac"><button type="button" data-act="save">Save</button>'
        f'<button type="button" data-act="message">Message</button></div></div></div></li>'
    )


def row_b(lead: dict[str, str]) -> str:
    """Layout B: plain divs, avatar-only anchor, public profile link, different structure."""
    return (
        f'<div class="s-9a" data-r="{lead["i"]}">'
        f'<a class="s-av" href="{_lead_href(lead)}"><img alt="" width="40" height="40"></a>'
        f'<div class="s-bx"><a class="s-n" href="{_lead_href(lead)}">{html.escape(lead["name"])}</a>'
        f"<span>{lead['degree']}</span>"
        f"<span>{html.escape(lead['title'])}</span>"
        f'<a class="s-co" href="/sales/company/{lead["company_id"]}">{html.escape(lead["company"])}</a>'
        f"<span>{html.escape(lead['location'])}</span>"
        f"<span>{lead['role']} in role</span>"
        f'<a class="s-pub" href="https://www.linkedin.com/in/{lead["slug"]}/">Public profile</a></div>'
        f'<div class="s-actions"><button type="button" data-act="message">Message</button>'
        f'<button type="button" data-act="save">Save to list</button></div></div>'
    )


_BUTTON_JS = """
document.addEventListener('click', e => {
  const b = e.target.closest('button[data-act]');
  if (b) fetch('/sales/api/' + b.dataset.act, {method: 'POST', body: '{}'});
});
"""

_CSS_A = """
body{margin:0;font-family:sans-serif}
header{padding:8px;border-bottom:1px solid #ccc}
#results{height:520px;overflow:auto;border:1px solid #ddd}
ul{list-style:none;margin:0;padding:0}
li{border-bottom:1px solid #eee;padding:10px}
._bd>div{display:block}
"""
_CSS_B = """
body{margin:0;font-family:sans-serif}
header{padding:8px;border-bottom:1px solid #ccc}
.s-9a{border-bottom:1px solid #eee;padding:12px;min-height:90px}
.s-bx>*{display:block}
"""


def _results_script(rows: list[str], layout: str, delay_ms: int) -> str:
    """Render ``rows`` lazily: 8 first, 8 more whenever the scroller nears its end."""
    data = json.dumps(rows)
    target = "document.getElementById('results')" if layout == "a" else "window"
    host = "document.getElementById('list')"
    near_end = (
        "(r.scrollTop + r.clientHeight >= r.scrollHeight - 80)"
        if layout == "a"
        else "(window.scrollY + window.innerHeight >= document.documentElement.scrollHeight - 80)"
    )
    return f"""
<script>
const ROWS = {data};
let shown = 0;
const host = {host};
function more() {{
  const slice = ROWS.slice(shown, shown + 8);
  if (!slice.length) return;
  host.insertAdjacentHTML('beforeend', slice.join(''));
  shown += slice.length;
}}
function start() {{ more(); }}
const r = {target};
const reach = () => {near_end};
let busy = false;
(r === window ? window : r).addEventListener('scroll', () => {{
  if (busy || !reach()) return;
  busy = true;
  setTimeout(() => {{ more(); busy = false; }}, 120);
}});
setTimeout(start, {delay_ms});
{_BUTTON_JS}
</script>"""


def results_page(
    rows: list[str], layout: str, *, delay_ms: int = 0, lead_list: bool = False
) -> str:
    title = "Lead list" if lead_list else "Lead search"
    controls = (
        '<header role="search"><input type="text" placeholder="Search leads" value="">'
        "<span>Lead filters</span></header>"
    )
    if layout == "b":
        body = f'{controls}<main><div id="list"></div></main>'
        css = _CSS_B
    else:
        body = f'{controls}<main><div id="results"><ul id="list"></ul></div></main>'
        css = _CSS_A
    return (
        f'<!doctype html><html><head><meta charset="utf-8"><title>{title} | Sales Navigator</title>'
        f"<style>{css}</style></head><body>{body}{_results_script(rows, layout, delay_ms)}</body></html>"
    )


EMPTY_PAGE = (
    "<!doctype html><html><head><title>Lead search | Sales Navigator</title></head><body>"
    '<header role="search"><input type="text" placeholder="Search leads"></header>'
    "<main><p>No leads match your search. Try removing a filter.</p></main></body></html>"
)
UPSELL_PAGE = (
    "<!doctype html><html><head><title>Sales Navigator</title></head><body><main>"
    "<h1>Find the right people with Sales Navigator</h1>"
    "<p>Get started with a free trial of Sales Navigator and unlock advanced lead search.</p>"
    '<a href="/premium/products/?family=sales">Start your free trial</a></main></body></html>'
)
PREMIUM_PAGE = (
    "<!doctype html><html><head><title>Premium products</title></head><body><main>"
    "<h1>Sales Navigator</h1><p>Start a free trial. Upgrade your plan to unlock lead search.</p>"
    "</main></body></html>"
)
LOGIN_PAGE = (
    "<!doctype html><html><head><title>Sign in | LinkedIn</title></head><body>"
    "<form><label>Email</label><input name=session_key><label>Password</label><input type=password></form>"
    "</body></html>"
)
CHECKPOINT_PAGE = (
    "<!doctype html><html><head><title>Security Verification | LinkedIn</title></head><body>"
    "<main><h1>Let's do a quick security check</h1></main></body></html>"
)
RATE_PAGE = "<!doctype html><html><head><title>LinkedIn</title></head><body><p>Too many requests. Slow down.</p></body></html>"


class SalesNavStub:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str]] = []
        self.writes: list[tuple[str, str]] = []
        self.lead_pages: list[str] = []
        self._lock = threading.Lock()
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format: str, *args: object) -> None:  # noqa: A002
                return

            def do_GET(self) -> None:  # noqa: N802
                stub._handle(self, "GET")

            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers.get("Content-Length", "0") or 0)
                if length:
                    self.rfile.read(length)
                stub._handle(self, "POST")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------------------

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def start(self) -> SalesNavStub:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    # -- URLs for tests ------------------------------------------------------------------

    def search_url(self, **switches: object) -> str:
        """A people-search URL like the ones a person copies out of Sales Navigator, plus stub switches."""
        extra = "".join(f"&{key}={value}" for key, value in switches.items())
        return f"{self.base_url}/sales/search/people?query=(spellCorrectionEnabled:true,keywords:cloud%20architect){extra}"

    def list_url(self, list_id: str = "123456", **switches: object) -> str:
        extra = "".join(f"&{key}={value}" for key, value in switches.items())
        return f"{self.base_url}/sales/lists/people/{list_id}?sortCriteria=CREATED_TIME{extra}"

    def page_hits(self) -> list[str]:
        """Every GET path (with query) the stub answered, in order."""
        with self._lock:
            return [path for method, path in self.requests if method == "GET"]

    # -- request handling ----------------------------------------------------------------

    def _handle(self, handler: BaseHTTPRequestHandler, method: str) -> None:
        parts = urlsplit(handler.path)
        query = {
            k: v[0] for k, v in parse_qs(parts.query, keep_blank_values=True).items()
        }
        with self._lock:
            self.requests.append((method, handler.path))
            if method != "GET" or parts.path.startswith("/sales/api/"):
                self.writes.append((method, handler.path))
        if method != "GET":
            return self._send(handler, 200, "{}", "application/json")
        path = parts.path
        mode = query.get("mode", "")
        if path == "/login":
            return self._send(handler, 200, LOGIN_PAGE)
        if path.startswith("/checkpoint"):
            return self._send(handler, 200, CHECKPOINT_PAGE)
        if path.startswith("/premium"):
            return self._send(handler, 200, PREMIUM_PAGE)
        if path.startswith("/sales/lead/"):
            with self._lock:
                self.lead_pages.append(path)
            return self._send(
                handler, 200, "<html><body><main>Lead profile</main></body></html>"
            )
        is_search = path.rstrip("/") == "/sales/search/people"
        is_list = path.startswith("/sales/lists/people/")
        if not (is_search or is_list):
            return self._send(handler, 404, "<html><body>Not found</body></html>")
        number = int(query.get("page", "1") or 1)
        if mode == "login" or (mode == "fail2" and number >= 2):
            return self._redirect(handler, "/login?session_redirect=sales")
        if mode == "checkpoint":
            return self._redirect(handler, "/checkpoint/challenge/abc")
        if mode == "redirect":
            return self._redirect(handler, "/premium/products/?family=sales")
        if mode == "upsell":
            return self._send(handler, 200, UPSELL_PAGE)
        if mode == "ratelimit":
            return self._send(handler, 200, RATE_PAGE)
        if mode == "empty":
            return self._send(handler, 200, EMPTY_PAGE)
        total = int(query.get("total", "60") or 60)
        layout = query.get("layout", "a")
        everything = make_leads(total)
        window_number = 1 if mode == "repeat" else number
        window = everything[(window_number - 1) * PAGE_SIZE : window_number * PAGE_SIZE]
        if not window:
            return self._send(handler, 200, EMPTY_PAGE)
        builder = row_b if layout == "b" else row_a
        delay = 1200 if mode == "late" else 0
        page = results_page(
            [builder(lead) for lead in window],
            layout,
            delay_ms=delay,
            lead_list=is_list,
        )
        return self._send(handler, 200, page)

    @staticmethod
    def _send(
        handler: BaseHTTPRequestHandler,
        status: int,
        body: str,
        kind: str = "text/html; charset=utf-8",
    ) -> None:
        data = body.encode("utf-8")
        handler.send_response(status)
        handler.send_header("Content-Type", kind)
        handler.send_header("Content-Length", str(len(data)))
        handler.end_headers()
        handler.wfile.write(data)

    @staticmethod
    def _redirect(handler: BaseHTTPRequestHandler, location: str) -> None:
        handler.send_response(302)
        handler.send_header("Location", location)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
