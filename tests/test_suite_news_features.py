"""News: fixes found by using the app (http only to this computer in test mode, the same story once, child-safe
summaries, children and sources, Today's error), and the features added: search, hiding a source, saved articles
as CSV and the newsletter a person earns from (picks, issues, free and paid readers, payments into the revenue
ledger, emails through the outbox, its public page), on their own, on the single-user server and hosted.

Every feed comes from tests/news_stub.py: nothing here reaches the internet.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.error
import urllib.request
from datetime import timedelta

import pytest
from cloud_support import Browser, build_org, sign_in
from life_support import Household
from news_stub import NewsStub

import auto_apply_assistant as local
import test_careers
from aira import suite
from aira.suite import kit, life, public
from aira.suite.news import feeds, letter, news_routes, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


class _Model:
    """A stand-in writing model that answers with fixed JSON."""

    def __init__(self, answer: dict) -> None:
        self.answer = answer
        self.prompts: list[str] = []

    def model(self, prompt):
        self.prompts.append(prompt)
        return json.dumps(self.answer)


@pytest.fixture(autouse=True)
def _no_model():
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


@pytest.fixture
def news(monkeypatch):
    server = NewsStub().start()
    monkeypatch.setenv(feeds.GOOGLE_BASE_ENV, server.base_url)
    monkeypatch.setenv(feeds.ALLOW_LOCAL_ENV, "1")
    yield server
    server.stop()


class Req:
    def __init__(self) -> None:
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "news.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    tara = life.add(db, pat, {"name": "Tara", "age_group": "teen"})
    ravi = life.add(db, pat, {"name": "Ravi", "age_group": "senior"})
    return {"pat": pat, "mia": mia, "tara": tara, "ravi": ravi}


def _titles(data):
    return [a["title"] for a in data["articles"]]


# ---------------------------------------------------------------------------
# fixes
# ---------------------------------------------------------------------------


def test_the_test_switch_allows_plain_http_only_to_this_computer(news):
    # Found in the browser: with AIRA_NEWS_ALLOW_LOCAL=1 a plain-http site on the internet was fetched.
    with pytest.raises(feeds.FeedError, match="https"):
        feeds.fetch(
            "http://8.8.8.8/rss"
        )  # a public address: refused before any connection
    assert feeds.read(f"{news.base_url}/atom.xml").title == "Village Blog"


def test_the_same_story_through_two_feeds_shows_once(db, people, news):
    # Found in the browser: a child's front page (Science + Technology) showed "Robots learn to fold laundry" twice.
    mine = service.articles(db, people["mia"], topic="top")
    assert _titles(mine).count("Robots learn to fold laundry") == 1
    assert service._story_key("Robots learn: to fold LAUNDRY!") == service._story_key(
        "Robots learn to fold laundry"
    )


def test_child_safe_mode_also_reads_the_summary(db, people, news):
    mia, pat = people["mia"], people["pat"]
    feed = service.topic_feed(db, "SCIENCE", "en", "IN")
    service.refresh(db, feed)
    db.execute(
        """INSERT INTO nw_articles (id, feed_id, link, title, summary, source, image, published_at, fetched_at)
        VALUES (?, ?, 'https://news.example/x', 'Town hall meeting tonight', 'Two people were killed nearby.', 'Desk', '',
        '2026-10-26T10:00:00Z', ?)""",
        (kit.new_id(), feed["id"], kit.now()),
    )
    db.commit()
    assert "Town hall meeting tonight" not in _titles(
        service.articles(db, mia, topic="SCIENCE")
    )
    assert "Town hall meeting tonight" in _titles(
        service.articles(db, pat, topic="SCIENCE")
    )


def test_children_cannot_add_their_own_sources(db, people, news):
    mia, tara = people["mia"], people["tara"]
    with pytest.raises(PermissionError, match="adult"):
        service.add_source(db, mia, {"url": f"{news.base_url}/atom.xml"})
    assert service.settings_view(db, mia)["can_add_sources"] is False
    # A teenager may add one (child-safe mode is for children).
    assert service.add_source(db, tara, {"url": f"{news.base_url}/atom.xml"})["title"]


def test_today_says_the_news_could_not_load_only_when_it_could_not(db, people, news):
    news.down = True
    assert news_routes._today(db, people["ravi"])[0]["empty"] == service.NO_NEWS_ERROR
    news.down = False
    db.execute("UPDATE nw_feeds SET last_fetched = NULL")
    db.commit()
    card = news_routes._today(db, people["ravi"])[0]
    assert card["lines"] and not service.today_lines(db, people["ravi"])["error"]


# ---------------------------------------------------------------------------
# search, hiding a source
# ---------------------------------------------------------------------------


def test_search_any_news_and_follow_it(db, people, news):
    pat = people["pat"]
    found = service.search(db, pat, "  solar   power ")
    assert found["query"] == "solar power" and found["following"] is False
    assert _titles(found)[:2] == [
        "solar power makes the news today",
        "More about solar power",
    ]
    calls = news.count("/rss/search")
    service.search(db, pat, "Solar power")
    assert (
        news.count("/rss/search") == calls
    )  # the same search within 30 minutes: no new fetch
    service.add_keyword(db, pat, {"query": "solar power"})
    assert service.search(db, pat, "solar power")["following"] is True
    with pytest.raises(ValueError):
        service.search(db, pat, "   ")
    with pytest.raises(ValueError):
        service.search(db, pat, "x" * 61)
    # A search nobody follows is dropped after a day; one someone follows stays.
    service.search(db, pat, "monsoon")
    db.execute(
        "UPDATE nw_feeds SET created_at = ? WHERE kind = 'search'",
        (kit.iso(kit.utcnow() - timedelta(days=2)),),
    )
    db.commit()
    service.background_refresh(db)
    left = {
        r["title"]
        for r in kit.fetch_all(db, "SELECT title FROM nw_feeds WHERE kind = 'search'")
    }
    assert left == {"solar power"}
    # A failed search says so.
    news.down = True
    assert service.search(db, pat, "floods")["error"] == service.NO_NEWS_ERROR


def test_hide_a_source_and_show_it_again(db, people, news):
    pat, ravi = people["pat"], people["ravi"]
    assert "Cricket team wins the series 3-1" in _titles(service.articles(db, pat))
    hidden = service.mute(db, pat, {"source": "sports desk"})
    assert service.mute(db, pat, {"source": "Sports Desk"})["id"] == hidden["id"]
    assert "Cricket team wins the series 3-1" not in _titles(service.articles(db, pat))
    assert "Cricket team wins the series 3-1" in _titles(service.articles(db, ravi))
    assert [m["source"] for m in service.settings_view(db, pat)["muted"]] == [
        "sports desk"
    ]
    with pytest.raises(LookupError):
        service.unmute(db, ravi, hidden["id"])  # not Ravi's
    service.unmute(db, pat, hidden["id"])
    assert "Cricket team wins the series 3-1" in _titles(service.articles(db, pat))
    with pytest.raises(ValueError):
        service.mute(db, pat, {"source": ""})


# ---------------------------------------------------------------------------
# the newsletter
# ---------------------------------------------------------------------------


def _pick_all(db, who, n=3):
    articles = service.articles(db, who, topic="top")["articles"][:n]
    return [letter.add_pick(db, who, {"article_id": a["id"]}) for a in articles]


def test_a_free_newsletter_from_picks_to_readers(db, people, news):
    pat = people["pat"]
    with pytest.raises(ValueError, match="Start your newsletter"):
        letter.add_pick(db, pat, {"article_id": "0" * 32})
    assert letter.overview(db, pat)["newsletter"] is None
    letter.save(
        db, pat, {"name": "Hyderabad This Week", "about": "Our city, in five minutes."}
    )
    picks = _pick_all(db, pat)
    first = picks[0]
    # The same article twice stays one pick (a note given the second time is kept).
    again = letter.add_pick(
        db,
        pat,
        {
            "article_id": service.articles(db, pat)["articles"][0]["id"],
            "note": "Good news for farmers.",
        },
    )
    assert again["id"] == first["id"] and again["note"] == "Good news for farmers."
    letter.update_pick(db, pat, picks[2]["id"], {"move": "up"})
    assert [p["id"] for p in letter.picks(db, letter.mine(db, pat))] == [
        picks[0]["id"],
        picks[2]["id"],
        picks[1]["id"],
    ]
    with pytest.raises(ValueError):
        letter.update_pick(db, pat, picks[0]["id"], {"move": "sideways"})
    # The opening is a template without a model, and the model's own words with one.
    suggested = letter.suggest_intro(db, pat)
    assert (
        suggested["source"] == "template"
        and suggested["title"] == "Hyderabad This Week, issue 1"
    )
    model = _Model({"title": "Rain, rails and a win", "intro": "A good week."})
    suite.set_gateway(model)
    assert letter.suggest_intro(db, pat)["title"] == "Rain, rails and a win"
    assert "Good news for farmers." in model.prompts[0]
    suite.set_gateway(_NoModel())
    # Nobody subscribed yet: the issue is on the page, nobody is emailed.
    issue = letter.publish(db, pat, {"title": "", "intro": "Hello all."})
    assert issue["number"] == 1 and issue["stories"] == 3 and issue["emailed"] == 0
    assert letter.picks(db, letter.mine(db, pat)) == []
    with pytest.raises(ValueError, match="at least one story"):
        letter.publish(db, pat, {})
    row = letter.mine(db, pat)
    page = letter.page_data(db, row)
    assert page["paid"] is False and page["writer"] == pat["name"]
    assert len(page["issues"][0]["stories"]) == 3 and page["issues"][0]["more"] == 0
    assert page["issues"][0]["stories"][0]["note"] == "Good news for farmers."
    # A reader subscribes: a welcome email with their code; subscribing again changes nothing.
    assert letter.subscribe(db, row, {"name": "Sam", "email": "sam@example.com"}) == {
        "status": "subscribed",
        "already": False,
    }
    assert (
        letter.subscribe(db, row, {"name": "Sam", "email": "SAM@example.com"})[
            "already"
        ]
        is True
    )
    with pytest.raises(ValueError):
        letter.subscribe(db, row, {"name": "Sam", "email": "not an email"})
    welcome = kit.outbox(db, app="news")[0]
    assert welcome["kind"] == "newsletter_welcome" and welcome["status"] == "pending"
    code = welcome["body"].split("Your code is ")[1].split(".")[0]
    _pick_all(db, pat, 1)
    second = letter.publish(db, pat, {"title": "Issue two"})
    assert second["emailed"] == 1
    mail = next(
        m for m in kit.outbox(db, app="news") if m["kind"] == "newsletter_issue"
    )
    assert mail["recipient"] == "sam@example.com" and "Issue two" in mail["subject"]
    assert "/p/" in mail["body"] and "https://news.example/" in mail["body"]
    # The reader stops with their email and code; a wrong code does nothing.
    with pytest.raises(PermissionError):
        letter.unsubscribe(db, row, {"email": "sam@example.com", "code": "AAAAA-AAAAA"})
    assert letter.unsubscribe(db, row, {"email": "sam@example.com", "code": code}) == {
        "status": "cancelled"
    }
    _pick_all(db, pat, 1)
    assert letter.publish(db, pat, {})["emailed"] == 0
    # An issue can be taken off the page.
    letter.delete_issue(db, pat, issue["id"])
    assert [i["number"] for i in letter.page_data(db, row)["issues"]] == [3, 2]
    with pytest.raises(LookupError):
        letter.delete_issue(db, pat, issue["id"])


def test_a_paid_newsletter_takes_payments_into_the_ledger(db, people, news):
    pat = people["pat"]
    with pytest.raises(ValueError):
        letter.save(
            db, pat, {"name": "Mandi Prices", "price_cents": ""}
        )  # a price that could not be read
    letter.save(db, pat, {"name": "Mandi Prices", "price_cents": 250})
    _pick_all(db, pat, 3)
    letter.publish(db, pat, {"title": "Tomato prices up"})
    row = letter.mine(db, pat)
    page = letter.page_data(db, row)
    assert page["paid"] and page["price_cents"] == 250
    assert len(page["issues"][0]["stories"]) == 1 and page["issues"][0]["more"] == 2
    asked = letter.subscribe(
        db, row, {"name": "Lakshmi", "email": "lakshmi@example.com"}
    )
    assert asked["status"] == "pending" and asked["amount_cents"] == 250
    assert asked["reference"].startswith("NW-")
    assert (
        letter.subscribe(db, row, {"name": "L", "email": "lakshmi@example.com"})[
            "reference"
        ]
        == asked["reference"]
    )
    assert letter.waiting_for(db, pat) == 1
    today = news_routes._today(db, pat)
    assert (
        today[1]["title"] == "Your newsletter"
        and "1 reader wants" in today[1]["lines"][0]["text"]
    )
    reader = letter.subscribers(db, pat, status="pending")[0]
    with pytest.raises(PermissionError):
        letter.unlock(db, row, "AAAAA-AAAAA")
    paid = letter.mark_paid(db, pat, reader["id"])
    assert paid["subscriber"]["paid_up"] and paid["email"] == "queued"
    ledger = kit.fetch_all(
        db, "SELECT * FROM suite_revenue WHERE app = 'news' AND voided_at IS NULL"
    )
    assert [r["amount_cents"] for r in ledger] == [250] and ledger[0][
        "customer"
    ] == "Lakshmi"
    access = next(
        m for m in kit.outbox(db, app="news") if m["kind"] == "newsletter_access"
    )
    assert (
        paid["code"] in access["body"] and access["recipient"] == "lakshmi@example.com"
    )
    full = letter.unlock(db, row, paid["code"])
    assert len(full[0]["stories"]) == 3
    # A second month: 30 more days, a second ledger line, the old code stops working.
    first_until = kit.parse_time(paid["subscriber"]["paid_until"])
    second = letter.mark_paid(db, pat, reader["id"])
    assert kit.parse_time(
        second["subscriber"]["paid_until"]
    ) - first_until == timedelta(days=30)
    with pytest.raises(PermissionError):
        letter.unlock(db, row, paid["code"])
    assert letter.overview(db, pat)["numbers"]["earned_cents"] == 500
    # Issues go to paid-up readers only.
    letter.subscribe(db, row, {"name": "Unpaid", "email": "unpaid@example.com"})
    _pick_all(db, pat, 1)
    assert letter.publish(db, pat, {})["emailed"] == 1
    # Undo: one payment out of the ledger, 30 days less; the last one undone makes them wait again.
    undone = letter.undo_payment(db, pat, reader["id"])
    assert kit.parse_time(undone["paid_until"]) == first_until and undone["paid_up"]
    letter.undo_payment(db, pat, reader["id"])
    assert letter.subscribers(db, pat, q="lakshmi")[0]["status"] == "pending"
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM suite_revenue WHERE app = 'news' AND voided_at IS NULL",
        )
        == 0
    )
    with pytest.raises(ValueError, match="no payment"):
        letter.undo_payment(db, pat, reader["id"])
    with pytest.raises(PermissionError):
        letter.unlock(db, row, second["code"])
    # A lapsed reader asks again with the same reference; their code says the subscription ended.
    again = letter.mark_paid(db, pat, reader["id"])
    db.execute(
        "UPDATE nw_subscribers SET paid_until = ? WHERE id = ?",
        (kit.iso(kit.utcnow() - timedelta(days=1)), reader["id"]),
    )
    db.commit()
    with pytest.raises(PermissionError, match="ended"):
        letter.unlock(db, row, again["code"])
    assert (
        letter.subscribe(db, row, {"name": "Lakshmi", "email": "lakshmi@example.com"})[
            "reference"
        ]
        == asked["reference"]
    )
    # Removing a reader keeps their payments on record.
    assert letter.remove_subscriber(db, pat, reader["id"])["status"] == "cancelled"
    with pytest.raises(ValueError, match="stopped"):
        letter.mark_paid(db, pat, reader["id"])
    summary = news_routes._summary(db)
    assert any(
        c["label"] == "Earned from newsletters this month" for c in summary["cards"]
    )
    # Turning the price off makes a waiting reader a free one at once, with a welcome email and a code.
    letter.save(db, pat, {"price_cents": 0})
    row = letter.mine(db, pat)
    assert letter.subscribers(db, pat, q="unpaid")[0]["status"] == "active"
    welcome = next(
        m
        for m in kit.outbox(db, app="news")
        if m["kind"] == "newsletter_welcome" and m["recipient"] == "unpaid@example.com"
    )
    freed_code = welcome["body"].split("Your code is ")[1].split(".")[0]
    assert letter.subscribe(db, row, {"name": "U", "email": "unpaid@example.com"}) == {
        "status": "subscribed",
        "already": True,
    }
    _pick_all(db, pat, 1)
    assert letter.publish(db, pat, {})["emailed"] >= 1
    with pytest.raises(ValueError, match="free"):
        letter.mark_paid(db, pat, letter.subscribers(db, pat, q="unpaid")[0]["id"])
    assert letter.unsubscribe(
        db, row, {"email": "unpaid@example.com", "code": freed_code}
    ) == {"status": "cancelled"}


def test_only_adults_run_a_newsletter_and_each_their_own(db, people, news):
    pat, mia, tara, ravi = people["pat"], people["mia"], people["tara"], people["ravi"]
    for young in (mia, tara):
        with pytest.raises(PermissionError, match="adults"):
            letter.save(db, young, {"name": "My news"})
        with pytest.raises(PermissionError):
            letter.overview(db, young)
        assert service.settings_view(db, young)["can_publish"] is False
    letter.save(db, pat, {"name": "Pat's picks"})
    pick = _pick_all(db, pat, 1)[0]
    assert service.settings_view(db, pat)["has_newsletter"] is True
    assert service.settings_view(db, ravi)["has_newsletter"] is False
    letter.save(db, ravi, {"name": "Ravi's garden news"})
    with pytest.raises(LookupError):
        letter.remove_pick(db, ravi, pick["id"])
    letter.subscribe(
        db, letter.mine(db, pat), {"name": "Sam", "email": "sam@example.com"}
    )
    sam = letter.subscribers(db, pat)[0]
    with pytest.raises(LookupError):
        letter.remove_subscriber(db, ravi, sam["id"])
    assert letter.subscribers(db, ravi) == []
    with pytest.raises(ValueError):
        letter.save(db, pat, {"name": "x", "price_cents": 10_000_01})


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


@pytest.fixture
def local_index():
    public.set_index(local._public_link_index)


def _public(env, method, path, body=None, headers=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    for name, value in {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        **(headers or {}),
    }.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def test_search_hide_export_and_a_paid_newsletter_on_the_local_build(
    env, api, news, local_index
):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    mia = home.add("Mia", age_group="child")
    # Search, hide a source, export saved articles.
    found = pat.get("/api/news/search?q=metro")
    assert found["articles"][0]["title"] == "metro makes the news today"
    assert pat.call("GET", "/api/news/search?q=")[0] == 400
    top = pat.get("/api/news/feed?topic=top")["articles"]
    hidden = pat.post("/api/news/muted", {"source": top[1]["source"]}, expect=201)[
        "muted"
    ]
    assert top[1]["title"] not in _titles(pat.get("/api/news/feed?topic=top"))
    assert mia.call("DELETE", f"/api/news/muted/{hidden['id']}")[0] == 404
    pat.ok("DELETE", f"/api/news/muted/{hidden['id']}")
    pat.post(f"/api/news/articles/{top[0]['id']}/save")
    status, sheet = pat.call("GET", "/api/news/saved?format=csv", raw=True)
    text = sheet.decode("utf-8")
    assert (
        status == 200
        and text.startswith("title,source,link")
        and top[0]["title"] in text
    )
    assert (
        mia.call("GET", "/api/news/saved?format=csv", raw=True)[1].decode().count("\n")
        == 1
    )
    assert (
        mia.call("POST", "/api/news/sources", {"url": f"{news.base_url}/atom.xml"})[0]
        == 403
    )
    # The newsletter: a child cannot; an adult starts one with a price and shares its page.
    assert mia.call("GET", "/api/news/newsletter")[0] == 403
    assert mia.call("POST", "/api/news/newsletter", {"name": "Mine"})[0] == 403
    assert pat.get("/api/news/newsletter")["newsletter"] is None
    assert pat.call("POST", "/api/news/newsletter", {"name": ""})[0] == 400
    made = pat.post(
        "/api/news/newsletter", {"name": "Farm Prices Daily", "price_cents": 300}
    )
    assert made["newsletter"]["price_cents"] == 300 and made["link"] is None
    for article in top[:3]:
        pat.post(
            "/api/news/newsletter/picks",
            {"article_id": article["id"], "note": "Why it matters."},
            expect=201,
        )
    assert (
        pat.call("POST", "/api/news/newsletter/picks", {"article_id": "0" * 32})[0]
        == 404
    )
    pick = pat.get("/api/news/newsletter")["picks"][0]
    pat.ok(
        "PATCH",
        f"/api/news/newsletter/picks/{pick['id']}",
        {"note": "Read this first."},
    )
    suggested = pat.post("/api/news/newsletter/suggest")
    issue = pat.post(
        "/api/news/newsletter/issues",
        {"title": suggested["title"], "intro": suggested["intro"]},
        expect=201,
    )["issue"]
    token = issue["link"]["token"]
    assert pat.get("/api/news/newsletter")["link"]["token"] == token
    # A visitor reads the preview and asks to subscribe.
    status, page = _public(env, "GET", f"/api/public/{token}")
    assert (
        status == 200
        and page["page"]["paid"]
        and page["page"]["issues"][0]["more"] == 2
    )
    assert page["page"]["issues"][0]["stories"][0]["note"] == "Read this first."
    status, asked = _public(
        env,
        "POST",
        f"/api/public/{token}/subscribe",
        {"name": "Sam", "email": "sam@example.com"},
    )
    assert status == 201 and asked["subscription"]["status"] == "pending"
    assert (
        _public(
            env, "POST", f"/api/public/{token}/subscribe", {"name": "", "email": "x"}
        )[0]
        == 400
    )
    assert (
        _public(
            env,
            "GET",
            f"/api/public/{token}/issues",
            headers={"Aira-Secret": "AAAAA-AAAAA"},
        )[0]
        == 403
    )
    today = pat.get("/api/life/today")
    assert any(
        c["app"] == "news" and c["title"] == "Your newsletter" for c in today["cards"]
    )
    reader = pat.get("/api/news/newsletter/subscribers?status=pending")["subscribers"][
        0
    ]
    assert (
        mia.call("POST", f"/api/news/newsletter/subscribers/{reader['id']}/paid", {})[0]
        == 403
    )
    paid = pat.post(f"/api/news/newsletter/subscribers/{reader['id']}/paid")
    status, full = _public(
        env, "GET", f"/api/public/{token}/issues", headers={"Aira-Secret": paid["code"]}
    )
    assert status == 200 and len(full["issues"][0]["stories"]) == 3
    status, sheet = pat.call(
        "GET", "/api/news/newsletter/subscribers?format=csv", raw=True
    )
    assert (
        status == 200
        and "sam@example.com" in sheet.decode()
        and "paid" in sheet.decode()
    )
    assert pat.get("/api/news/newsletter")["numbers"]["earned_cents"] == 300
    user = env.user()
    local._bind_user(user)
    try:
        with local._connect() as connection:
            ledger = kit.fetch_all(
                connection,
                "SELECT * FROM suite_revenue WHERE app = 'news' AND voided_at IS NULL",
            )
            mail = kit.outbox(connection, app="news")
    finally:
        local._unbind_user()
    assert [r["amount_cents"] for r in ledger] == [300]
    assert any(
        m["kind"] == "newsletter_access" and paid["code"] in m["body"] for m in mail
    )
    # The reader stops; undo a payment; take the page down.
    status, stopped = _public(
        env,
        "POST",
        f"/api/public/{token}/unsubscribe",
        {"email": "sam@example.com", "code": paid["code"]},
    )
    assert status == 200 and stopped["status"] == "cancelled"
    pat.post(f"/api/news/newsletter/subscribers/{reader['id']}/undo")
    pat.ok("DELETE", f"/api/news/newsletter/issues/{issue['id']}")
    pat.ok("DELETE", "/api/news/newsletter/link")
    assert _public(env, "GET", f"/api/public/{token}")[0] == 404


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_each_run_their_own_newsletter(cloud, news):
    kit.forget_schema()
    org = build_org(cloud)
    coach = sign_in(cloud, org.coach)
    assert coach.call("GET", "/api/news/newsletter")[0] == 402
    cloud.billing.subscribe_app(org.owner, "news")
    candidate = sign_in(cloud, org.candidate)
    status, made = coach.call(
        "POST",
        "/api/news/newsletter",
        {"name": "Job market weekly", "price_cents": 500},
    )
    assert status == 200, made
    first = coach.call("GET", "/api/news/feed?topic=top")[1]["articles"][0]
    assert (
        coach.call("POST", "/api/news/newsletter/picks", {"article_id": first["id"]})[0]
        == 201
    )
    status, issue = coach.call(
        "POST", "/api/news/newsletter/issues", {"title": "Week one"}
    )
    assert status == 201, issue
    token = issue["issue"]["link"]["token"]
    # Every member searches and hides sources for themselves.
    for who in ("owner", "admin", "billing", "auditor"):
        assert (
            sign_in(cloud, getattr(org, who)).call("GET", "/api/news/search?q=metro")[0]
            == 200
        )
    assert candidate.call("POST", "/api/news/muted", {"source": "City News"})[0] == 201
    assert "New metro line opens in Hyderabad" in _titles(
        coach.call("GET", "/api/news/feed")[1]
    )
    visitor = Browser(cloud)
    status, page = visitor.call("GET", f"/api/public/{token}")
    assert status == 200 and page["page"]["name"] == "Job market weekly", page
    status, asked = visitor.call(
        "POST",
        f"/api/public/{token}/subscribe",
        {"name": "Sam", "email": "sam@example.com"},
    )
    assert status == 201 and asked["subscription"]["status"] == "pending", asked
    # Another member has no newsletter of their own yet and cannot see or confirm the coach's readers.
    assert candidate.call("GET", "/api/news/newsletter")[1]["newsletter"] is None
    reader = coach.call("GET", "/api/news/newsletter/subscribers")[1]["subscribers"][0]
    assert (
        candidate.call(
            "POST", f"/api/news/newsletter/subscribers/{reader['id']}/paid", {}
        )[0]
        == 400
    )
    candidate.call("POST", "/api/news/newsletter", {"name": "Candidate digest"})
    assert (
        candidate.call(
            "POST", f"/api/news/newsletter/subscribers/{reader['id']}/paid", {}
        )[0]
        == 404
    )
    status, paid = coach.call(
        "POST", f"/api/news/newsletter/subscribers/{reader['id']}/paid", {}
    )
    assert status == 200, paid
    status, full = visitor.call(
        "GET", f"/api/public/{token}/issues", headers={"Aira-Secret": paid["code"]}
    )
    assert status == 200 and full["issues"][0]["stories"], full
    # Another organization sees nothing of this one.
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "news")
    theirs = sign_in(cloud, other.owner)
    assert theirs.call("GET", "/api/news/newsletter")[1]["newsletter"] is None
    theirs.call("POST", "/api/news/newsletter", {"name": "Theirs"})
    assert (
        theirs.call(
            "POST", f"/api/news/newsletter/subscribers/{reader['id']}/paid", {}
        )[0]
        == 404
    )
    assert (
        theirs.call("GET", "/api/news/newsletter/subscribers")[1]["subscribers"] == []
    )
