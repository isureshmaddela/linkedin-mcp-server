"""Aira Leads: sources, scoring, de-duplication, enrichment, the service and the HTTP routes.

Everything runs against the stand-in SerpApi server (``tests/serpapi_stub.py``), a stand-in company website
and fake gateways: never the network, a real database or a real SerpApi key.
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
import threading
import zipfile
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from aira.airaapi import client as client_module
from aira.airaapi.client import SerpApiClient, SerpApiError
from aira.leads import enrich, outreach, scoring, service, sources, store
from aira.schema import migrate
from serpapi_stub import DEFAULT_KEY, SerpApiStub
import test_careers
from test_careers import PASSWORD

NOW = datetime(2026, 10, 24, 12, 0, 0, tzinfo=timezone.utc)
env = (
    test_careers.env
)  # the real-server fixture (a stub SerpApi, a scratch account database)


@pytest.fixture
def stub(monkeypatch):
    server = SerpApiStub().start()
    monkeypatch.setenv("AIRA_SERPAPI_BASE_URL", server.base_url)
    monkeypatch.setattr(client_module, "_sleep", lambda _seconds: None)
    yield server
    server.stop()


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "user.sqlite3")
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA journal_mode = WAL")
    migrate(connection)
    connection.commit()
    yield connection
    connection.close()


@pytest.fixture
def client(stub):
    return SerpApiClient(DEFAULT_KEY)


class FakeBridge:
    """Stands in for the server's LinkedIn tools and writing model."""

    def __init__(self, state="connected", leads=None, error=None, reply=None):
        self.state = state
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.leads = leads if leads is not None else []
        self.error = error
        self.reply = reply

    def linkedin_state(self):
        return self.state

    def linkedin_call(self, name, arguments):
        self.calls.append((name, arguments))
        if self.error:
            raise self.error
        return {
            "url": "https://www.linkedin.com/sales/search/people?x=1",
            "pages_read": 2,
            "leads": self.leads,
            "warnings": [],
        }

    def sender_name(self):
        return "Sam Seller"

    def model(self, prompt):
        return self.reply


@pytest.fixture
def bridge():
    previous = service.gateway()
    fake = FakeBridge()
    service.set_gateway(fake)
    yield fake
    service.set_gateway(previous)


SALES_NAV_LEADS = [
    {
        "name": "Maria Lopez",
        "first_name": "Maria",
        "last_name": "Lopez",
        "title": "Director of Sales",
        "company": "Northwind Cloud",
        "location": "Austin, Texas",
        "profile_url": "https://www.linkedin.com/sales/lead/ACwAAA111,NAME_SEARCH",
        "linkedin_url": "https://www.linkedin.com/in/maria-lopez-77",
        "degree": "2nd",
        "tenure_in_role": "2 years 1 month",
    },
    {
        "name": "Chen Wei",
        "title": "VP Sales",
        "company": "Contoso Data",
        "location": "Dallas, Texas",
        "profile_url": "https://www.linkedin.com/sales/lead/ACwAAA222,NAME_SEARCH",
        "degree": "3rd",
    },
]


# ---------------------------------------------------------------------------
# storage and de-duplication
# ---------------------------------------------------------------------------


def test_lists_and_leads_are_stored_and_normalized(db):
    first = store.default_list(db)
    assert (
        first["name"] == store.DEFAULT_LIST_NAME
        and store.default_list(db)["id"] == first["id"]
    )
    other = store.create_list(db, "Austin SaaS", "Series B")
    with pytest.raises(ValueError):
        store.create_list(db, "austin saas")
    outcome = store.insert_lead(
        db,
        {
            "name": "  Jane   Doe ",
            "title": "VP Sales",
            "company": "Acme",
            "email": "JANE@ACME.COM",
            "website": "acme.com",
            "phone": "+1 512 555 0100",
            "tags": ["vip", "vip", ""],
        },
        list_id=other["id"],
    )
    lead = outcome["lead"]
    assert (
        outcome["created"] and lead["name"] == "Jane Doe" and lead["kind"] == "person"
    )
    assert (
        lead["email"] == "jane@acme.com"
        and lead["first_name"] == "Jane"
        and lead["tags"] == ["vip"]
    )
    assert lead["website"] == "https://acme.com"
    assert store.get_list(db, other["id"])["lead_count"] == 1


def test_a_lead_already_known_is_never_added_twice(db):
    list_id = store.default_list(db)["id"]
    created = store.insert_lead(
        db,
        {
            "name": "Jane Doe",
            "title": "VP",
            "company": "Acme",
            "linkedin_url": "https://uk.linkedin.com/in/Jane-Doe-1/?trk=x",
        },
        list_id=list_id,
    )["lead"]
    for same in (
        {
            "name": "J. Doe",
            "title": "VP",
            "linkedin_url": "https://www.linkedin.com/in/jane-doe-1",
        },  # same profile
        {
            "name": "Jane Doe",
            "title": "VP",
            "company": "ACME!",
        },  # same name and company
    ):
        again = store.insert_lead(db, same, list_id=list_id)
        assert not again["created"] and again["lead"]["id"] == created["id"], same
    place = {
        "kind": "company",
        "name": "Joe's Coffee",
        "source": "google_maps",
        "source_ref": "ChIJabc",
        "website": "https://www.joes.example/menu",
    }
    one = store.insert_lead(db, place, list_id=list_id)
    assert one["created"]
    assert not store.insert_lead(
        db, {**place, "name": "Joe's Coffee Co"}, list_id=list_id
    )["created"]  # same place
    # another location of the same business (same website, another place) is its own lead
    assert store.insert_lead(
        db,
        {**place, "name": "Joe's Coffee Downtown", "source_ref": "ChIJdef"},
        list_id=list_id,
    )["created"]
    # outside Maps the website is the identity
    site = {
        "kind": "company",
        "name": "Acme",
        "website": "https://www.acme.example/about",
        "source": "import",
    }
    assert store.insert_lead(db, site, list_id=list_id)["created"]
    assert not store.insert_lead(
        db, {**site, "name": "Acme Inc", "website": "acme.example"}, list_id=list_id
    )["created"]  # same domain
    assert store.query_leads(db)["total"] == 4


def test_leads_are_filtered_sorted_edited_and_bulk_changed(db):
    a = store.default_list(db)["id"]
    b = store.create_list(db, "Second")["id"]
    ids = []
    for n, (name, score) in enumerate([("Ann A", 90), ("Bob B", 40), ("Cy C", 70)]):
        lead = store.insert_lead(
            db,
            {
                "name": name,
                "title": "CEO",
                "company": f"Co{n}",
                "email": f"{name[0].lower()}@co{n}.test",
            },
            list_id=a,
            score=score,
        )["lead"]
        ids.append(lead["id"])
    assert [x["name"] for x in store.query_leads(db, sort="score")["leads"]] == [
        "Ann A",
        "Cy C",
        "Bob B",
    ]
    assert store.query_leads(db, min_score=60)["total"] == 2
    assert store.query_leads(db, text="co1")["leads"][0]["name"] == "Bob B"
    assert store.query_leads(db, has_email=False)["total"] == 0
    edited = store.update_lead(
        db,
        ids[0],
        {
            "status": "qualified",
            "notes": "Met at event",
            "tags": ["hot"],
            "title": "CTO",
        },
    )
    assert (
        edited["status"] == "qualified"
        and edited["title"] == "CTO"
        and edited["tags"] == ["hot"]
    )
    with pytest.raises(ValueError):
        store.update_lead(db, ids[0], {"status": "nonsense"})
    with pytest.raises(
        ValueError
    ):  # would collide with another lead's email-less identity
        store.update_lead(db, ids[1], {"name": "Ann A", "company": "Co0"})
    assert store.bulk_update(db, ids[:2], "move", b)["changed"] == 2
    assert store.query_leads(db, list_id=b)["total"] == 2
    assert store.bulk_update(db, ids, "tag", "q4")["changed"] == 3
    assert store.query_leads(db, tag="q4")["total"] == 3
    assert store.bulk_update(db, ids, "untag", "q4")["changed"] == 3
    assert store.bulk_update(db, ids[:1], "status", "contacted")["changed"] == 1
    assert store.bulk_update(db, ids, "delete")["changed"] == 3
    assert store.query_leads(db)["total"] == 0
    # deleting forgets the identity: the same person can be added again
    assert store.insert_lead(
        db, {"name": "Ann A", "title": "CEO", "company": "Co0"}, list_id=a
    )["created"]


def test_deleting_a_list_removes_its_leads_and_their_identities(db):
    keep = store.default_list(db)["id"]
    drop = store.create_list(db, "Drop me")["id"]
    store.insert_lead(
        db, {"name": "Zed Z", "title": "CEO", "company": "Zco"}, list_id=drop
    )
    assert store.delete_list(db, drop) == 1
    with pytest.raises(LookupError):
        store.get_list(db, drop)
    assert store.insert_lead(
        db, {"name": "Zed Z", "title": "CEO", "company": "Zco"}, list_id=keep
    )["created"]


# ---------------------------------------------------------------------------
# sources
# ---------------------------------------------------------------------------


def test_maps_parameters_are_validated_and_built():
    params, applied, warnings = sources.build_maps_params(
        {"query": "dentists", "location": "Austin, TX"}
    )
    assert (
        params["q"] == "dentists in Austin, TX"
        and params["type"] == "search"
        and not warnings
    )
    assert "start" not in params
    assert (
        sources.build_maps_params(
            {"query": "dentists in Austin", "location": "Austin"}
        )[0]["q"]
        == "dentists in Austin"
    )
    assert (
        sources.build_maps_params(
            {"query": "plumbers", "ll": "@30.27,-97.74,12z", "start": 20}
        )[0]["start"]
        == 20
    )
    assert sources.build_maps_params({"query": "plumbers"})[2], "no location warns"
    for bad in (
        {},
        {"query": "x", "start": 7},
        {"query": "x", "start": 200},
        {"query": "x", "ll": "nope"},
        {"query": "x" * 300},
    ):
        with pytest.raises(ValueError):
            sources.build_maps_params(bad)


def test_web_search_builds_xray_queries_and_normalizes_linkedin_results():
    params, applied, _ = sources.build_web_params(
        {
            "mode": "people",
            "titles": "VP Sales, Head of Sales",
            "keywords": "SaaS",
            "location": "Austin",
        }
    )
    assert (
        params["q"]
        == 'site:linkedin.com/in ("VP Sales" OR "Head of Sales") SaaS Austin'
    )
    assert sources.build_web_params(
        {"mode": "companies", "keywords": "cloud software", "location": "Austin"}
    )[0]["q"].startswith('site:linkedin.com/company "cloud software"')
    assert (
        sources.build_web_params(
            {"mode": "custom", "query": "coffee roaster", "site": "yelp.com"}
        )[0]["q"]
        == "site:yelp.com coffee roaster"
    )
    for bad in (
        {"mode": "people"},
        {"mode": "companies"},
        {"mode": "weird"},
        {"mode": "custom"},
    ):
        with pytest.raises(ValueError):
            sources.build_web_params(bad)
    result = {
        "organic_results": [
            {
                "title": "Jane Doe - VP Sales - Northwind Cloud | LinkedIn",
                "link": "https://www.linkedin.com/in/jane-doe-1/?trk=a",
                "snippet": "Austin, Texas, United States · 500+ connections on LinkedIn.",
            },
            {
                "title": "Ana Silva - Sales Director at Fabrikam | LinkedIn",
                "link": "https://br.linkedin.com/in/ana-silva-2",
                "snippet": "Location: Remote · Experience: Fabrikam",
            },
            {
                "title": "Sales trends | LinkedIn",
                "link": "https://www.linkedin.com/pulse/sales-trends",
                "snippet": "an article",
            },
            {"title": "Random", "link": "https://example.com/x", "snippet": ""},
        ]
    }
    leads, skipped = sources.normalize_web(result, "people")
    assert skipped == 2 and [lead["name"] for lead in leads] == [
        "Jane Doe",
        "Ana Silva",
    ]
    jane, ana = leads
    assert (jane["title"], jane["company"], jane["location"]) == (
        "VP Sales",
        "Northwind Cloud",
        "Austin, Texas, United States",
    )
    assert jane["linkedin_url"] == "https://www.linkedin.com/in/jane-doe-1"
    assert (ana["title"], ana["company"], ana["location"]) == (
        "Sales Director",
        "Fabrikam",
        "Remote",
    )
    companies, _ = sources.normalize_web(
        {
            "organic_results": [
                {
                    "title": "Northwind Cloud | LinkedIn",
                    "link": "https://www.linkedin.com/company/northwind-cloud",
                    "snippet": "Austin, Texas · 51-200 employees",
                }
            ]
        },
        "companies",
    )
    assert (
        companies[0]["kind"] == "company" and companies[0]["name"] == "Northwind Cloud"
    )
    assert sources.parse_profile_title("Only Name | LinkedIn") == ("Only Name", "", "")


def test_maps_hiring_and_sales_navigator_results_become_leads(stub):
    from test_careers import fixture_body

    maps = sources.normalize_maps(fixture_body("google_maps"))
    assert len(maps) == 4 and maps[0]["name"] == "Fixture Coffee Roasters"
    assert (
        maps[0]["phone"] == "(212) 555-0100"
        and maps[0]["website"] == "https://example.com/place-0"
    )
    assert (
        maps[0]["industry"] == "Coffee shop"
        and maps[0]["rating"] == 4.7
        and maps[0]["source_ref"] == "ChIJfixture0"
    )
    hiring = sources.normalize_hiring(fixture_body("google_jobs"))
    assert hiring and all(
        lead["kind"] == "company" and lead["signals"][0]["type"] == "hiring"
        for lead in hiring
    )
    nav = sources.normalize_sales_navigator({"leads": SALES_NAV_LEADS})
    assert (
        nav[0]["sales_nav_url"].startswith("https://www.linkedin.com/sales/lead/")
        and nav[0]["kind"] == "person"
    )
    assert {s["type"] for s in nav[0]["signals"]} == {"connection", "tenure"}
    assert sources.normalize_maps(None) == [] and sources.normalize_hiring("x") == []


def test_sales_navigator_requests_only_accept_sales_navigator_addresses():
    url = (
        "https://www.linkedin.com/sales/search/people?query=(recentSearchParam:(id:1))"
    )
    assert sources.build_sales_navigator_request({"url": url, "max_pages": 3}) == {
        "max_pages": 3,
        "url": url,
    }
    assert sources.build_sales_navigator_request(
        {"keywords": "cto", "location": "Austin"}
    ) == {"max_pages": 2, "keywords": "cto", "location": "Austin"}
    assert sources.build_sales_navigator_request(
        {"url": "https://www.linkedin.com/sales/lists/people/123"}
    )["url"].endswith("/123")
    for bad in (
        {},
        {"url": "https://www.linkedin.com/feed/"},
        {"url": "https://evil.example/sales/search/people"},
        {"url": "https://www.linkedin.com.evil.example/sales/search/people"},
        {"url": url, "max_pages": 9},
        {"url": url, "max_pages": "x"},
    ):
        with pytest.raises(ValueError):
            sources.build_sales_navigator_request(bad)


def test_csv_import_maps_common_headers_and_skips_empty_rows():
    text = (
        "Full Name,Job Title,Company Name,Work Email,Phone Number,LinkedIn Profile URL,Tags,Unknown\n"
        'Jane Doe,VP Sales,Acme,jane@acme.com,+1 512 555 0100,https://www.linkedin.com/in/jane-doe,"a;b",zzz\n'
        ",,,,,,,\n"
        "Acme Corp,,,,,,,\n"
        ",,,,,,,\n"
    )
    leads, skipped = sources.parse_csv(text)
    assert (
        len(leads) == 2
        and leads[0]["name"] == "Jane Doe"
        and leads[0]["tags"] == ["a", "b"]
    )
    assert leads[0]["kind"] == "person" and leads[1]["kind"] == "company"
    with pytest.raises(ValueError):
        sources.parse_csv("foo,bar\n1,2\n")
    with pytest.raises(ValueError):
        sources.parse_csv("")
    semi, _ = sources.parse_csv("name;company\nBob;Beta\n")
    assert semi[0]["company"] == "Beta"


# ---------------------------------------------------------------------------
# scoring
# ---------------------------------------------------------------------------


def test_scoring_is_explained_and_absent_without_an_icp():
    lead = {
        "kind": "person",
        "name": "Jane",
        "title": "VP of Sales",
        "company": "Northwind SaaS",
        "location": "Austin, TX",
        "email": "j@n.test",
        "linkedin_url": "https://www.linkedin.com/in/j",
    }
    assert scoring.score_lead(lead, scoring.normalize_icp({})) == (None, [])
    icp = scoring.normalize_icp(
        {"titles": "VP Sales, VP of Sales", "keywords": "SaaS", "locations": "Austin"}
    )
    score, reasons = scoring.score_lead(lead, icp)
    assert (
        score >= 80
        and any("Title matches" in r for r in reasons)
        and any("Austin" in r for r in reasons)
    )
    low, why = scoring.score_lead(
        {
            **lead,
            "title": "Intern",
            "location": "Oslo",
            "company": "Acme",
            "email": "",
            "linkedin_url": "",
        },
        icp,
    )
    assert low < 30 and any("does not match" in r for r in why)
    excluded, why = scoring.score_lead(
        lead, scoring.normalize_icp({"titles": "VP", "exclude_companies": "northwind"})
    )
    assert excluded == 0 and "Excluded company" in why[0]
    hiring = {
        "kind": "company",
        "name": "Acme",
        "company": "Acme",
        "signals": [{"type": "hiring", "text": "Hiring: SDR"}],
        "website": "https://acme.test",
    }
    with_signal, why = scoring.score_lead(
        hiring, scoring.normalize_icp({"keywords": "Acme"})
    )
    assert any("Buying signal" in r for r in why)
    required, why = scoring.score_lead(
        {"kind": "company", "name": "NoSite", "company": "NoSite", "signals": []},
        scoring.normalize_icp({"keywords": "NoSite", "require": "website,phone"}),
    )
    assert any("Missing required" in r for r in why)
    with pytest.raises(ValueError):
        scoring.normalize_icp({"min_rating": 9})


# ---------------------------------------------------------------------------
# enrichment: a stand-in company website
# ---------------------------------------------------------------------------

HOME = """<html><head><title>Acme Roasters</title><meta name="description" content="Small-batch coffee."></head><body>
<a href="/contact">Contact us</a><a href="/about">About</a><a href="https://www.linkedin.com/company/acme-roasters">LinkedIn</a>
<a href="https://twitter.com/acmeroasters">Twitter</a><p>Call (512) 555-0142</p>
<script>var fake = "tracker@sentry.io";</script></body></html>"""
CONTACT = """<html><body><h1>Contact</h1>
<a href="mailto:hello@acme.test?subject=Hi">Email us</a> <p>Sales: sales [at] acme [dot] test</p>
<p>Press: press@partner-example.org  logo@2x.png</p><a href="tel:+15125550143">Call</a></body></html>"""
PRIVATE = "<html><body>secret internal page: internal@acme.test</body></html>"


class _Site(BaseHTTPRequestHandler):
    def log_message(self, *_args):  # noqa: D401
        return

    def do_GET(self):  # noqa: N802
        site = self.server.site  # type: ignore[attr-defined]
        site["hits"].append(self.path)
        pages = {
            "/": HOME,
            "/contact": CONTACT,
            "/about": "<html><body>About: team@acme.test</body></html>",
            "/private/page": PRIVATE,
        }
        if self.path == "/robots.txt":
            body, status = "User-agent: *\nDisallow: /private/\n", 200
        elif self.path == "/go":
            self.send_response(302)
            self.send_header("Location", "/contact")
            self.end_headers()
            return
        elif self.path in pages:
            body, status = pages[self.path], 200
        else:
            body, status = "not found", 404
        data = body.encode()
        self.send_response(status)
        self.send_header(
            "Content-Type",
            "text/html; charset=utf-8" if self.path != "/robots.txt" else "text/plain",
        )
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@pytest.fixture
def site(monkeypatch):
    monkeypatch.setenv(
        enrich.ALLOW_LOCAL_ENV, "1"
    )  # the stand-in site listens on loopback
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Site)
    server.site = {"hits": []}  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    server.url = f"http://127.0.0.1:{server.server_address[1]}"  # type: ignore[attr-defined]
    yield server
    server.shutdown()
    server.server_close()


def test_enrichment_reads_only_what_the_site_publishes(site):
    result = enrich.enrich_website(site.url)
    addresses = {e["address"]: e for e in result["emails"]}
    assert (
        "hello@acme.test" in addresses
        and addresses["hello@acme.test"]["kind"] == "generic"
    )
    assert "sales@acme.test" in addresses, (
        "an obfuscated address the site publishes is read"
    )
    assert "tracker@sentry.io" not in addresses and not any(
        a.endswith(".png") for a in addresses
    )
    assert addresses["press@partner-example.org"]["same_domain"] is False
    assert all(e["source_url"].startswith(site.url) for e in result["emails"])
    assert {p["number"] for p in result["phones"]} >= {"+15125550143"}
    assert (
        result["socials"]["linkedin"].endswith("acme-roasters")
        and "twitter" in result["socials"]
    )
    assert (
        result["title"] == "Acme Roasters"
        and result["description"] == "Small-batch coffee."
    )
    assert "/robots.txt" in site.site["hits"], "robots.txt is consulted"


def test_enrichment_honours_robots_and_refuses_non_public_addresses(site, monkeypatch):
    assert enrich.allowed_by_robots(site.url + "/contact") is True
    assert enrich.allowed_by_robots(site.url + "/private/page") is False
    result = enrich.enrich_website(site.url + "/private/page")
    assert result["robots_blocked"] and not result["emails"] and not result["pages"]
    monkeypatch.delenv(enrich.ALLOW_LOCAL_ENV)
    with pytest.raises(enrich.FetchError, match="public"):
        enrich.fetch(site.url)
    refused = enrich.enrich_website(site.url)
    assert (
        not refused["pages"] and refused["errors"] and site.site["hits"].count("/") == 0
    )
    for address in ("10.0.0.5", "192.168.1.1", "169.254.169.254", "::1", "fd00::1"):
        monkeypatch.setattr(
            "socket.getaddrinfo", lambda *a, _x=address, **k: [(0, 0, 0, "", (_x, 80))]
        )
        with pytest.raises(enrich.FetchError):
            enrich._check_addresses("anything.example", 80)
    with pytest.raises(enrich.FetchError):
        enrich.fetch("file:///etc/passwd")
    with pytest.raises(enrich.FetchError):
        enrich.fetch("ftp://example.com/")


def test_enrichment_follows_a_redirect_on_the_same_site(site):
    page = enrich.fetch(site.url + "/go")
    assert "Contact" in page.text and page.url.endswith("/contact")


# ---------------------------------------------------------------------------
# outreach drafts
# ---------------------------------------------------------------------------


def test_drafts_use_only_the_leads_facts_and_fall_back_without_a_model():
    lead = {
        "name": "Jane Doe",
        "first_name": "Jane",
        "title": "VP Sales",
        "company": "Northwind",
        "signals": [{"type": "hiring", "text": "Hiring: SDR (Austin)"}],
    }
    email = outreach.draft(
        lead, offer="We cut onboarding time in half", sender="Sam", tone="direct"
    )
    assert (
        email["source"] == "template"
        and "Northwind" in email["body"]
        and "Sam" in email["body"]
        and email["subject"]
    )
    assert "Hiring" not in email["body"] or "SDR" in email["body"]
    note = outreach.draft(
        lead, offer="We cut onboarding time in half", sender="Sam", channel="linkedin"
    )
    assert len(note["body"]) <= outreach.LINKEDIN_NOTE_LIMIT and note["subject"] == ""
    modelled = outreach.draft(
        lead,
        model=lambda prompt: json.dumps(
            {"subject": "Hi Jane", "body": "Hello Jane " + "x" * 400}
        ),
        channel="linkedin",
    )
    assert (
        modelled["source"] == "model"
        and len(modelled["body"]) <= outreach.LINKEDIN_NOTE_LIMIT
    )
    prompts: list[str] = []
    outreach.draft(lead, model=lambda prompt: prompts.append(prompt) or None)
    assert "Jane Doe" in prompts[0] and "do not invent" in prompts[0].lower()
    assert outreach.draft(lead, model=lambda prompt: "not json")["source"] == "template"

    def broken(_prompt):
        raise RuntimeError("model down")

    assert outreach.draft(lead, model=broken)["source"] == "template"
    with pytest.raises(ValueError):
        outreach.draft(lead, tone="rude")


# ---------------------------------------------------------------------------
# the service
# ---------------------------------------------------------------------------


def test_a_maps_search_finds_businesses_scores_them_and_never_saves_unasked(
    db, client, stub, bridge
):
    store.default_list(db)
    scoring.save_icp(db, {"keywords": "coffee", "require": "website"})
    out = service.search(
        db,
        client,
        {"source": "google_maps", "query": "coffee roasters", "location": "New York"},
        now=NOW,
    )
    assert (
        out["count"] == 4
        and out["new_count"] == 4
        and out["added"] == 0
        and out["credits"] == 1
    )
    assert (
        out["leads"][0]["phone"]
        and out["leads"][0]["website"]
        and out["leads"][0]["score"] is not None
    )
    assert store.query_leads(db)["total"] == 0, "a search only previews"
    assert out["next_start"] in (None, 20)
    again = service.search(
        db,
        client,
        {"source": "google_maps", "query": "coffee roasters", "location": "New York"},
        now=NOW,
    )
    assert (
        again["cache_hit"]
        and again["credits"] == 0
        and stub.count("/search.json", "google_maps") == 1
    )
    plan = service.search(
        db,
        client,
        {"source": "google_maps", "query": "bakeries", "dry_run": True},
        now=NOW,
    )
    assert (
        plan["dry_run"]
        and plan["credits"] == 1
        and stub.count("/search.json", "google_maps") == 1
    )


def test_auto_add_saves_new_leads_and_the_second_run_adds_none(
    db, client, stub, bridge
):
    body = {
        "source": "google_maps",
        "query": "coffee roasters",
        "location": "New York",
        "auto_add": True,
    }
    first = service.search(db, client, body, now=NOW)
    assert first["added"] == 4 and store.query_leads(db)["total"] == 4
    second = service.search(db, client, body, now=NOW)
    assert (
        second["added"] == 0
        and second["duplicate_count"] == 4
        and all(lead["duplicate_of"] for lead in second["leads"])
    )
    runs = store.recent_runs(db)
    assert [r["added"] for r in runs] == [0, 4] and runs[0]["cache_hit"] == 1


def test_web_search_finds_people_and_companies_from_linkedin_pages(
    db, client, stub, bridge
):
    out = service.search(
        db,
        client,
        {
            "source": "google_web",
            "mode": "people",
            "titles": "VP Sales, Head of Sales",
            "location": "Texas",
        },
        now=NOW,
    )
    assert [lead["name"] for lead in out["leads"]] == [
        "Jane Doe",
        "Raj Patel",
        "Ana Silva",
    ] or {lead["name"] for lead in out["leads"]} == {
        "Jane Doe",
        "Raj Patel",
        "Ana Silva",
    }
    assert out["skipped"] == 1 and all(lead["linkedin_url"] for lead in out["leads"])
    sent = [c for c in stub.calls if c.engine == "google"][-1]
    assert sent.params["q"].startswith("site:linkedin.com/in")
    companies = service.search(
        db,
        client,
        {"source": "google_web", "mode": "companies", "keywords": "cloud software"},
        now=NOW,
    )
    assert {lead["name"] for lead in companies["leads"]} == {
        "Northwind Cloud",
        "Contoso Data",
    }


def test_hiring_signals_become_company_leads(db, client, stub, bridge):
    out = service.search(
        db,
        client,
        {"source": "google_jobs", "query": "barista", "location": "New York"},
        now=NOW,
    )
    assert out["count"] >= 1 and all(lead["kind"] == "company" for lead in out["leads"])
    assert out["leads"][0]["signals"][0]["type"] == "hiring"


def test_without_a_key_google_sources_explain_setup(db, bridge):
    with pytest.raises(SerpApiError) as raised:
        service.search(db, None, {"source": "google_maps", "query": "coffee"}, now=NOW)
    assert raised.value.code == "not_configured"
    plan = service.search(
        db, None, {"source": "google_maps", "query": "coffee", "dry_run": True}, now=NOW
    )
    assert plan["dry_run"] is True


def test_sales_navigator_reads_the_persons_own_linkedin_and_reports_failures(
    db, bridge
):
    bridge.leads = SALES_NAV_LEADS
    url = "https://www.linkedin.com/sales/search/people?query=x"
    out = service.search(
        db,
        None,
        {"source": "sales_navigator", "url": url, "max_pages": 2, "auto_add": True},
        now=NOW,
    )
    assert bridge.calls == [(service.SALES_NAV_TOOL, {"max_pages": 2, "url": url})]
    assert (
        out["count"] == 2
        and out["added"] == 2
        and out["credits"] == 0
        and out["pages_read"] == 2
    )
    saved = store.query_leads(db, source="sales_navigator")["leads"]
    assert {lead["name"] for lead in saved} == {"Maria Lopez", "Chen Wei"} and saved[0][
        "sales_nav_url"
    ]
    again = service.search(db, None, {"source": "sales_navigator", "url": url}, now=NOW)
    assert again["duplicate_count"] == 2 and again["added"] == 0
    plan = service.search(
        db, None, {"source": "sales_navigator", "url": url, "dry_run": True}, now=NOW
    )
    assert plan["dry_run"] and plan["credits"] == 0 and len(bridge.calls) == 2, (
        "a dry run reads nothing"
    )
    bridge.state = "disconnected"
    with pytest.raises(PermissionError, match="Connect your LinkedIn"):
        service.search(db, None, {"source": "sales_navigator", "url": url}, now=NOW)
    bridge.state = "connected"
    bridge.error = RuntimeError("This LinkedIn account has no Sales Navigator access.")
    with pytest.raises(RuntimeError, match="no Sales Navigator"):
        service.search(db, None, {"source": "sales_navigator", "url": url}, now=NOW)
    assert store.recent_runs(db)[0]["status"] == "error"
    with pytest.raises(ValueError):
        service.search(
            db,
            None,
            {"source": "sales_navigator", "url": "https://evil.example/"},
            now=NOW,
        )
    with pytest.raises(ValueError):
        service.search(db, None, {"source": "nope"}, now=NOW)


def test_import_rescoring_enrichment_and_export(db, bridge, monkeypatch):
    list_id = store.default_list(db)["id"]
    imported = service.import_csv(
        db,
        "Name,Title,Company,Website,Email\nJane Doe,VP Sales,Acme,acme.test,\n=cmd|' /C calc'!A0,CEO,Evil,,\n",
        list_id,
    )
    assert imported["created"] == 2 and imported["rows"] == 2
    assert all(lead["score"] is None for lead in imported["leads"]), "no ICP, no score"
    saved = service.save_icp(db, {"titles": "VP Sales", "keywords": "Acme"})
    assert saved["rescored"] == 2
    jane = next(
        lead for lead in store.query_leads(db)["leads"] if lead["name"] == "Jane Doe"
    )
    assert jane["score"] and any("Title matches" in r for r in jane["score_reasons"])
    calls: list[str] = []

    def fetcher(website):
        calls.append(website)
        return {
            "emails": [
                {
                    "address": "hello@acme.test",
                    "kind": "generic",
                    "same_domain": True,
                    "source_url": website,
                }
            ],
            "phones": [{"number": "+15125550100", "source_url": website}],
            "pages": [website],
        }

    enriched = service.enrich_lead(db, jane["id"], fetcher=fetcher)
    assert (
        enriched["email"] == "hello@acme.test"
        and enriched["phone"] == "+15125550100"
        and enriched["enriched_at"]
    )
    assert calls == ["https://acme.test"]
    evil = next(
        lead for lead in store.query_leads(db)["leads"] if lead["company"] == "Evil"
    )
    with pytest.raises(ValueError, match="no website"):
        service.enrich_lead(db, evil["id"], fetcher=fetcher)

    def refused(_url):
        raise enrich.FetchError("Only public websites can be read.")

    with pytest.raises(RuntimeError, match="public"):
        service.enrich_lead(db, jane["id"], fetcher=refused)
    body, kind, name = service.export(db, fmt="csv")
    rows = list(csv.reader(io.StringIO(body.decode("utf-8-sig"))))
    assert (
        kind.startswith("text/csv") and rows[0][0] == "Name" and name.endswith(".csv")
    )
    assert any(row[0].startswith("'=") for row in rows[1:]), (
        "a cell that looks like a formula is neutralised"
    )
    xbody, xkind, xname = service.export(db, fmt="xlsx", min_score=1)
    assert xname.endswith(".xlsx") and "spreadsheetml" in xkind
    with zipfile.ZipFile(io.BytesIO(xbody)) as archive:
        assert "xl/worksheets/sheet1.xml" in archive.namelist()
        assert b"Jane Doe" in archive.read("xl/worksheets/sheet1.xml")
    with pytest.raises(ValueError):
        service.export(db, fmt="pdf")


def test_drafts_come_from_the_gateway_model_and_sender(db, bridge):
    list_id = store.default_list(db)["id"]
    lead = store.insert_lead(
        db,
        {"name": "Jane Doe", "title": "VP Sales", "company": "Acme"},
        list_id=list_id,
    )["lead"]
    plain = service.draft_message(db, lead["id"], {"offer": "Faster onboarding"})
    assert plain["source"] == "template" and "Sam Seller" in plain["body"]
    bridge.reply = json.dumps(
        {"subject": "Quick idea", "body": "Hi Jane, about onboarding at Acme."}
    )
    modelled = service.draft_message(db, lead["id"], {"offer": "Faster onboarding"})
    assert modelled["source"] == "model" and modelled["subject"] == "Quick idea"
    assert (
        service.draft_message(db, lead["id"], {"use_model": False})["source"]
        == "template"
    )


# ---------------------------------------------------------------------------
# HTTP routes, through the real server
# ---------------------------------------------------------------------------


@pytest.fixture
def api(env):  # noqa: F811
    api = env.api()
    status, body = api.call(
        "POST",
        "/api/auth/register",
        {"email": "leads@example.com", "password": PASSWORD},
    )
    assert status == 201, body
    return api


@pytest.fixture
def connected(env, api):  # noqa: F811
    api.post("/api/airaapi/key", {"api_key": DEFAULT_KEY})
    return api


LEAD_ROUTES = [
    ("GET", "/api/leads/overview"),
    ("GET", "/api/leads/icp"),
    ("POST", "/api/leads/icp"),
    ("GET", "/api/leads/lists"),
    ("POST", "/api/leads/lists"),
    ("PATCH", f"/api/leads/lists/{'0' * 32}"),
    ("DELETE", f"/api/leads/lists/{'0' * 32}"),
    ("POST", "/api/leads/search"),
    ("GET", "/api/leads/searches"),
    ("POST", "/api/leads/searches"),
    ("DELETE", f"/api/leads/searches/{'0' * 32}"),
    ("POST", f"/api/leads/searches/{'0' * 32}/run"),
    ("GET", "/api/leads/items"),
    ("POST", "/api/leads/items"),
    ("POST", "/api/leads/items/import-csv"),
    ("POST", "/api/leads/items/bulk"),
    ("GET", f"/api/leads/items/{'0' * 32}"),
    ("PATCH", f"/api/leads/items/{'0' * 32}"),
    ("DELETE", f"/api/leads/items/{'0' * 32}"),
    ("POST", f"/api/leads/items/{'0' * 32}/enrich"),
    ("POST", f"/api/leads/items/{'0' * 32}/draft"),
    ("GET", "/api/leads/export"),
]


def test_every_leads_route_needs_a_session(env):
    anonymous = env.api()
    for method, path in LEAD_ROUTES:
        status, body = anonymous.call(
            method, path, {} if method in {"POST", "PATCH"} else None
        )
        assert status == 401 and body == {"error": "Sign in to continue."}, (
            method,
            path,
        )
    assert env.stub.search_calls == 0


def test_the_leads_journey_search_save_enrich_draft_and_export(
    env, connected, site, monkeypatch
):
    api = connected
    overview = api.get("/api/leads/overview")
    assert overview["lists"] == [] and overview["icp_empty"] is True
    sources_by_id = {s["id"]: s for s in overview["sources"]}
    assert (
        sources_by_id["google_maps"]["available"] is True
        and sources_by_id["sales_navigator"]["available"] is False
    )
    api.post(
        "/api/leads/icp",
        {"keywords": "coffee", "titles": "Owner", "require": ["website"]},
    )
    plan = api.post(
        "/api/leads/search",
        {
            "source": "google_maps",
            "query": "coffee roasters",
            "location": "New York",
            "dry_run": True,
        },
    )
    assert plan["dry_run"] and plan["credits"] == 1 and env.stub.search_calls == 0
    found = api.post(
        "/api/leads/search",
        {"source": "google_maps", "query": "coffee roasters", "location": "New York"},
    )
    assert (
        found["count"] == 4
        and found["added"] == 0
        and found["leads"][0]["score"] is not None
    )
    saved = api.post("/api/leads/items", {"leads": found["leads"][:3]}, expect=201)
    assert saved["created"] == 3 and saved["list_id"]
    again = api.post("/api/leads/items", {"leads": found["leads"][:3]}, expect=201)
    assert again["created"] == 0 and again["duplicates"] == 3
    listing = api.get("/api/leads/items?sort=score&limit=2")
    assert listing["total"] == 3 and len(listing["leads"]) == 2
    lead = listing["leads"][0]
    changed = api.ok(
        "PATCH",
        f"/api/leads/items/{lead['id']}",
        {"status": "contacted", "notes": "Called Tuesday", "tags": ["warm"]},
    )["lead"]
    assert changed["status"] == "contacted" and changed["tags"] == ["warm"]
    assert api.get("/api/leads/items?status=contacted")["total"] == 1
    # point one lead at the stand-in website and enrich it
    api.ok("PATCH", f"/api/leads/items/{lead['id']}", {"website": site.url})
    enriched = api.post(f"/api/leads/items/{lead['id']}/enrich")["lead"]
    found_emails = {e["address"] for e in enriched["enrichment"]["emails"]}
    assert "hello@acme.test" in found_emails and enriched["enrichment"]["pages"]
    # the stand-in site is 127.0.0.1 and these addresses are at acme.test, so none is promoted to the lead on its own;
    # the person picks one (a lead's own domain would have been used automatically)
    assert enriched["email"] == ""
    chosen = api.ok(
        "PATCH", f"/api/leads/items/{lead['id']}", {"email": "hello@acme.test"}
    )["lead"]
    assert chosen["email"] == "hello@acme.test"
    draft = api.post(
        f"/api/leads/items/{lead['id']}/draft",
        {"offer": "Faster roasting logistics", "channel": "email"},
    )["draft"]
    assert draft["body"] and draft["source"] == "template"
    status, body = api.call(
        "POST", f"/api/leads/items/{lead['id']}/draft", {"tone": "rude"}
    )
    assert status == 400
    # saved searches and lists
    created = api.post(
        "/api/leads/searches",
        {
            "name": "NYC roasters",
            "source": "google_maps",
            "params": {"query": "coffee roasters", "location": "New York"},
        },
        expect=201,
    )["search"]
    run = api.post(f"/api/leads/searches/{created['id']}/run", {})
    assert run["cache_hit"] and run["credits"] == 0 and run["duplicate_count"] == 3
    assert run["added"] == 1, (
        "a saved search adds the one result that was not saved yet"
    )
    new_list = api.post("/api/leads/lists", {"name": "Hot"}, expect=201)["list"]
    moved = api.post(
        "/api/leads/items/bulk",
        {"ids": [lead["id"]], "action": "move", "value": new_list["id"]},
    )
    assert (
        moved["changed"] == 1
        and api.get(f"/api/leads/items?list_id={new_list['id']}")["total"] == 1
    )
    # export
    request = api.opener.open(api.base + "/api/leads/export?format=csv", timeout=30)
    text = request.read().decode("utf-8-sig")
    assert (
        request.headers["Content-Type"].startswith("text/csv")
        and "attachment" in request.headers["Content-Disposition"]
    )
    assert "Fixture Coffee Roasters" in text and "hello@acme.test" in text
    assert (
        api.opener.open(api.base + "/api/leads/export?format=xlsx", timeout=30).read()[
            :2
        ]
        == b"PK"
    )
    overview = api.get("/api/leads/overview")
    assert (
        overview["counts"]["total"] == 4
        and overview["counts"]["with_email"] >= 1
        and overview["runs"]
    )
    api.ok("DELETE", f"/api/leads/items/{lead['id']}")
    assert api.get("/api/leads/overview")["counts"]["total"] == 3


def test_the_routes_explain_missing_setup_and_reject_bad_input(env, api):
    status, body = api.call(
        "POST", "/api/leads/search", {"source": "google_maps", "query": "coffee"}
    )
    assert status == 409 and body["code"] == "not_configured"
    status, body = api.call(
        "POST",
        "/api/leads/search",
        {
            "source": "sales_navigator",
            "url": "https://www.linkedin.com/sales/search/people?q=1",
        },
    )
    assert status in (403, 502), (
        status,
        body,
    )  # LinkedIn is not connected in this build
    for payload in ({"source": "google_maps"}, {"source": "bogus"}, {}):
        assert api.call("POST", "/api/leads/search", payload)[0] == 400
    assert api.call("POST", "/api/leads/items", {"leads": []})[0] == 400
    assert api.call("POST", "/api/leads/items/import-csv", {"csv": "x"})[0] == 400
    assert api.call("GET", f"/api/leads/items/{'1' * 32}")[0] == 404
    assert api.call("GET", "/api/leads/items?status=weird")[0] == 400
    csv_result = api.post(
        "/api/leads/items/import-csv",
        {"csv": "Name,Company,Email\nJane Doe,Acme,jane@acme.test\n"},
        expect=201,
    )
    assert csv_result["created"] == 1
    assert api.get("/api/leads/items?q=jane")["total"] == 1


def test_leads_are_private_to_each_account(env, api):
    api.post(
        "/api/leads/items/import-csv",
        {"csv": "Name,Company\nSecret Person,Hidden Co\n"},
        expect=201,
    )
    other = env.api()
    status, body = other.call(
        "POST",
        "/api/auth/register",
        {"email": "other@example.com", "password": PASSWORD},
    )
    assert status == 201, body
    assert other.get("/api/leads/items")["total"] == 0
    assert other.get("/api/leads/overview")["counts"]["total"] == 0


def test_news_signals_are_looked_up_once_kept_and_never_duplicated(
    db, client, stub, bridge
):
    list_id = store.default_list(db)["id"]
    scoring.save_icp(db, {"keywords": "coffee"})
    lead = store.insert_lead(
        db,
        {
            "kind": "company",
            "name": "Fixture Coffee Roasters",
            "company": "Fixture Coffee Roasters",
            "website": "https://example.com",
        },
        list_id=list_id,
    )["lead"]
    plan = service.news_signals(db, client, lead["id"], {"dry_run": True}, now=NOW)
    assert (
        plan["dry_run"]
        and plan["credits"] == 1
        and stub.count("/search.json", "google_news") == 0
    )
    out = service.news_signals(db, client, lead["id"], {"topic": "funding"}, now=NOW)
    assert out["found"] == 3 and out["credits"] == 1
    signals = out["lead"]["signals"]
    assert [s["type"] for s in signals] == ["news"] * 3 and all(
        s["url"].startswith("https://") for s in signals
    )
    assert "Reuters" in signals[0]["text"]
    sent = [c for c in stub.calls if c.engine == "google_news"][-1]
    assert sent.params["q"] == '"Fixture Coffee Roasters" funding'
    again = service.news_signals(db, client, lead["id"], {"topic": "funding"}, now=NOW)
    assert (
        again["cache_hit"]
        and again["credits"] == 0
        and len(again["lead"]["signals"]) == 3
    )
    assert "Buying signal" in " ".join(again["lead"]["score_reasons"])
    person = store.insert_lead(
        db, {"name": "No Company", "title": "CEO"}, list_id=list_id
    )["lead"]
    with pytest.raises(ValueError, match="no company"):
        service.news_signals(db, client, person["id"], {}, now=NOW)
    with pytest.raises(SerpApiError):
        service.news_signals(db, None, lead["id"], {}, now=NOW)
    assert (
        sources.normalize_news(
            {"news_results": [{"title": "x", "link": "javascript:alert(1)"}]}
        )
        == []
    )


def test_a_company_is_greeted_without_a_first_name():
    company = {
        "kind": "company",
        "name": "Fixture Coffee Roasters",
        "company": "Fixture Coffee Roasters",
    }
    body = outreach.draft(company, offer="Faster roasting logistics", sender="Sam")[
        "body"
    ]
    assert body.startswith("Hi,") and "Fixture," not in body
    person = {"kind": "person", "name": "Jane Doe", "first_name": "Jane"}
    assert outreach.draft(person)["body"].startswith("Hi Jane,")


def test_an_older_linkedin_connection_is_told_to_restart(monkeypatch):
    import auto_apply_assistant as app

    def old_tool(_name, _arguments):
        raise RuntimeError("Error calling tool: Unknown tool: search_sales_navigator")

    monkeypatch.setattr(app, "_call_mcp", old_tool)
    with pytest.raises(RuntimeError, match="Restart LinkedIn in Settings"):
        app._LeadsGateway().linkedin_call("search_sales_navigator", {"max_pages": 1})

    def other_failure(_name, _arguments):
        raise RuntimeError("LinkedIn asked for a security check")

    monkeypatch.setattr(app, "_call_mcp", other_failure)
    with pytest.raises(RuntimeError, match="security check"):
        app._LeadsGateway().linkedin_call("search_sales_navigator", {"max_pages": 1})


def test_real_linkedin_search_titles_never_invent_a_company():
    """Shapes seen in live Google results: the headline is free text, so a company needs evidence."""
    cases = [
        (  # a headline made of tags: no company at all
            "Tim Example - Chief Revenue Officer | VP Sales | SaaS | PEO",
            "Chief Revenue Officer | VP Sales | SaaS | PEO | HR Tech · As a Vice President of Sales ...",
            ("Tim Example", "Chief Revenue Officer | VP Sales | SaaS | PEO", ""),
            "",
        ),
        (  # "at Company" in the headline, plus the labelled location
            "Guy Sample - Head of Sales, US - Repair AI at Partly",
            "Head of Sales, US - Repair AI at Partly · Experience: Partly · Education: Some University · Location: Austin ...",
            ("Guy Sample", "Head of Sales, US - Repair AI", "Partly"),
            "Austin",
        ),
        (  # "@ Company" followed by a tagline that is cut off
            "Ryan Demo - Head of Sales @ Tebra | Driving Revenue ...",
            "Head of Sales @ Tebra | Driving Revenue Growth · Experience: Tebra · Location: Austin · 500+ connections on LinkedIn",
            ("Ryan Demo", "Head of Sales", "Tebra"),
            "Austin",
        ),
        (  # the company only appears in the snippet's "Role. Company. dates" line, with a metro-area location
            "John Placeholder - VP/SVP of Sales · B2B SaaS revenue leader",
            "Chief Revenue Officer / First Head of Sales. eSource Solutions. Feb 2026 - Present 9 months. Austin, Texas Metropolitan Area. Own Sales",
            (
                "John Placeholder",
                "VP/SVP of Sales · B2B SaaS revenue leader",
                "eSource Solutions",
            ),
            "Austin, Texas Metropolitan Area",
        ),
        (  # a short "Title - Company" pair
            "Jane Doe - VP Sales - Northwind Cloud | LinkedIn",
            "Austin, Texas, United States · 500+ connections on LinkedIn.",
            ("Jane Doe", "VP Sales", "Northwind Cloud"),
            "Austin, Texas, United States",
        ),
    ]
    for title, snippet, expected, place in cases:
        assert sources.parse_profile_title(title, snippet) == expected, title
        assert sources._location_of(snippet) == place, title
