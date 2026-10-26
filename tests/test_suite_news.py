"""News (aira/suite/news): safe feed reading, each person's language and topics, child-safe mode, saving, Today,
pulse and background refresh, on its own, through the single-user server and in the hosted build.

Every feed comes from tests/news_stub.py: nothing here reaches the internet.
"""

from __future__ import annotations

import sqlite3
from datetime import timedelta

import pytest
from cloud_support import build_org, sign_in
from life_support import Household
from news_stub import ATOM, BOMB, DOCTYPE, NewsStub, _rss

import test_careers
from aira import suite
from aira.suite import kit, life
from aira.suite.news import feeds, news_routes, service, store

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class _NoModel:
    def model(self, prompt):
        return None


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
    def __init__(self, header: str = "") -> None:
        self._header = header
        self.user = {"email": "pat@home.example"}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "news.sqlite3")
    connection.row_factory = sqlite3.Row
    store.ensure(connection)
    yield connection
    connection.close()


@pytest.fixture
def people(db):
    pat = life.current(Req(), db)
    mia = life.add(db, pat, {"name": "Mia", "age_group": "child"})
    ravi = life.add(db, pat, {"name": "Ravi", "age_group": "senior"})
    return {"pat": pat, "mia": mia, "ravi": ravi}


# ---------------------------------------------------------------------------
# reading feeds safely
# ---------------------------------------------------------------------------


def test_rss_and_atom_become_plain_safe_articles():
    parsed = feeds.parse(
        _rss(
            [
                ("Rains bring relief", "The Herald", "https://news.example/1"),
                ("Metro opens", "City News", "https://news.example/2"),
            ]
        )
    )
    assert [a.title for a in parsed.items] == [
        "Rains bring relief",
        "Metro opens",
    ]  # javascript: link dropped
    first, second = parsed.items
    assert first.source == "The Herald" and first.link == "https://news.example/1"
    assert (
        first.image == "https://img.example/0.jpg" and second.image == ""
    )  # only https images
    assert "<" not in first.summary and "x()" not in first.summary
    assert first.published_at == "2026-10-25T23:00:00Z"
    atom = feeds.parse(ATOM)
    assert atom.title == "Village Blog"
    assert [a.title for a in atom.items] == [
        "Harvest festival this weekend",
        "Library opens on Sundays",
    ]
    assert (
        atom.items[0].summary == "Bring the whole family."
        and atom.items[1].link == "https://blog.example/library"
    )
    assert atom.items[1].published_at == "2026-10-23T09:00:00Z"
    assert feeds.plain_text("<p>a &amp; b</p><script>evil()</script>") == "a & b"


def test_unsafe_or_wrong_documents_are_refused(news):
    for body in (BOMB, DOCTYPE):
        with pytest.raises(feeds.FeedError, match="document type"):
            feeds.parse(body)
    with pytest.raises(feeds.FeedError, match="too large"):
        feeds.read(f"{news.base_url}/huge.xml")
    with pytest.raises(feeds.FeedError, match="not a news feed"):
        feeds.read(f"{news.base_url}/notfeed")
    with pytest.raises(feeds.FeedError, match="answered 500"):
        feeds.read(f"{news.base_url}/broken")


def test_only_public_https_addresses_are_fetched(news, monkeypatch):
    # Redirects are checked again: to a private address or another scheme is refused, even in tests.
    with pytest.raises(feeds.FeedError, match="public"):
        feeds.read(f"{news.base_url}/redirect-private")
    with pytest.raises(feeds.FeedError, match="https"):
        feeds.read(f"{news.base_url}/redirect-file")
    assert feeds.read(f"{news.base_url}/redirect-ok").title == "Village Blog"
    monkeypatch.delenv(feeds.ALLOW_LOCAL_ENV)
    with pytest.raises(feeds.FeedError, match="https"):
        feeds.read(f"{news.base_url}/rss")
    with pytest.raises(feeds.FeedError, match="public"):
        feeds._check_addresses("127.0.0.1", 443)
    assert feeds.top_url("te", "IN").endswith("/rss?hl=te&gl=IN&ceid=IN:te")
    assert "hl=en-GB&gl=GB&ceid=GB:en" in feeds.topic_url("WORLD", "en", "GB")
    assert "q=new+metro" in feeds.search_url("new metro", "hi", "IN")


# ---------------------------------------------------------------------------
# people, cache and refresh
# ---------------------------------------------------------------------------


def test_each_person_reads_in_their_language_and_feeds_are_cached(db, people, news):
    pat, ravi = people["pat"], people["ravi"]
    life.save_prefs(
        db, ravi, {}
    )  # prefs exist; a UI language (when set) picks the news language
    db.execute(
        "UPDATE life_profiles SET prefs = ? WHERE id = ?",
        (kit.dumps({**ravi["prefs"], "language": "te"}), ravi["id"]),
    )
    db.commit()
    ravi = life.get(db, ravi["id"])
    english = service.articles(db, pat, topic="top")
    telugu = service.articles(db, ravi, topic="top")
    assert (
        english["lang"] == "en"
        and english["articles"][0]["title"] == "Monsoon rains bring relief to farmers"
    )
    assert (
        telugu["lang"] == "te" and telugu["articles"][0]["title"] == "రైతులకు వర్షాలతో ఊరట"
    )
    assert telugu["articles"][0]["source"] == "ఈనాడు" and telugu["articles"][0]["ago"]
    calls = news.count("/rss?")
    service.articles(db, pat, topic="top")
    assert news.count("/rss?") == calls  # within 30 minutes the cache answers
    db.execute(
        "UPDATE nw_feeds SET last_fetched = ?",
        (kit.iso(kit.utcnow() - timedelta(minutes=31)),),
    )
    db.commit()
    service.articles(db, pat, topic="top")
    assert (
        news.count("/rss?") == calls + 1
    )  # stale again: Pat's front page is fetched once more
    # A failed refresh keeps the earlier news and says so.
    db.execute(
        "UPDATE nw_feeds SET last_fetched = ?",
        (kit.iso(kit.utcnow() - timedelta(minutes=31)),),
    )
    db.commit()
    news.down = True
    kept = service.articles(db, pat, topic="top")
    assert kept["error"] == service.REFRESH_ERROR and len(kept["articles"]) == 5
    # Settings: Hindi; a region a language does not offer falls back to its first one.
    view = service.save_settings(
        db, pat, {"lang": "hi", "region": "US", "topics": ["top", "business", "WORLD"]}
    )
    assert (
        view["lang"] == "hi"
        and view["region"] == "IN"
        and view["topics"] == ["top", "BUSINESS", "WORLD"]
    )
    news.down = False
    assert (
        service.articles(db, pat, topic="top")["articles"][0]["title"]
        == "किसानों को बारिश से राहत"
    )
    with pytest.raises(ValueError):
        service.save_settings(db, pat, {"lang": "xx"})
    with pytest.raises(ValueError):
        service.save_settings(db, pat, {"topics": ["GOSSIP"]})
    with pytest.raises(ValueError):
        service.save_settings(db, pat, {"topics": []})


def test_keywords_sources_saving_and_since(db, people, news):
    pat = people["pat"]
    keyword = service.add_keyword(db, pat, {"query": "metro", "alert": True})
    assert keyword["alert"] is True
    with pytest.raises(ValueError, match="already follow"):
        service.add_keyword(db, pat, {"query": "Metro"})
    for n in range(9):
        service.add_keyword(db, pat, {"query": f"word{n}"})
    with pytest.raises(ValueError, match="up to 10"):
        service.add_keyword(db, pat, {"query": "one more"})
    for k in service.keywords(db, pat)[1:]:
        service.remove_keyword(db, pat, k["id"])
    foryou = service.articles(db, pat, topic="foryou")["articles"]
    assert [a["title"] for a in foryou] == [
        "metro makes the news today",
        "More about metro",
    ]
    with pytest.raises(LookupError):
        service.remove_keyword(db, people["ravi"], keyword["id"])  # not theirs
    # Own sources (RSS or Atom).
    source = service.add_source(db, pat, {"url": f"{news.base_url}/atom.xml"})
    assert source["title"] == "Village Blog"
    assert [
        a["title"]
        for a in service.articles(db, pat, topic=f"source:{source['id']}")["articles"]
    ][0] == ("Harvest festival this weekend")
    with pytest.raises(ValueError, match="Could not read"):
        service.add_source(db, pat, {"url": f"{news.base_url}/notfeed"})
    with pytest.raises(LookupError):
        service.articles(db, people["ravi"], topic=f"source:{source['id']}")
    # Save, read, saved list and since.
    first = service.articles(db, pat, topic="top")["articles"][0]
    assert service.save(db, pat, first["id"])["saved"] is True
    assert [a["id"] for a in service.saved(db, pat)] == [first["id"]]
    assert service.saved(db, people["ravi"]) == []
    assert service.mark_read(db, pat, first["id"])["read"] is True
    assert service.unsave(db, pat, first["id"])["saved"] is False
    with pytest.raises(LookupError):
        service.save(db, pat, "0" * 32)
    mark = kit.now()
    news.extra = 2
    db.execute(
        "UPDATE nw_feeds SET last_fetched = ?",
        (kit.iso(kit.utcnow() - timedelta(minutes=31)),),
    )
    db.execute(
        "UPDATE nw_articles SET fetched_at = ?",
        (kit.iso(kit.utcnow() - timedelta(minutes=40)),),
    )
    db.commit()
    fresh = service.articles(
        db, pat, topic="top", since=kit.iso(kit.parse_time(mark) - timedelta(seconds=1))
    )
    assert [a["title"] for a in fresh["articles"]] == [
        "Fresh story 0",
        "Fresh story 1",
    ] or sorted(a["title"] for a in fresh["articles"]) == [
        "Fresh story 0",
        "Fresh story 1",
    ]
    service.remove_source(db, pat, source["id"])
    assert service.sources(db, pat) == []


def test_children_get_safe_news_and_cannot_turn_it_off(db, people, news):
    mia, pat = people["mia"], people["pat"]
    mine = service.settings(db, mia)
    assert mine["child_safe"] is True and set(mine["topics"]) <= set(feeds.CHILD_TOPICS)
    view = service.save_settings(db, mia, {"child_safe": False})
    assert view["child_safe"] is True and view["can_turn_off_child_safe"] is False
    with pytest.raises(ValueError, match="child-safe"):
        service.save_settings(db, mia, {"topics": ["WORLD"]})
    # Asking for the world page gives a child their safe topics instead.
    world = service.articles(db, mia, topic="WORLD")["articles"]
    assert world and not any("Attack" in a["title"] for a in world)
    assert {a["title"] for a in world} >= {"Telescope spots a new comet"}
    # An adult reads everything, unless they turn child-safe mode on.
    titles = [a["title"] for a in service.articles(db, pat, topic="WORLD")["articles"]]
    assert "Attack on border post leaves two dead" in titles
    service.save_settings(db, pat, {"child_safe": True, "topics": ["SCIENCE"]})
    assert service.settings(db, pat)["child_safe"] is True
    assert feeds.child_safe_title("Rains bring relief") and not feeds.child_safe_title(
        "Man killed in crash"
    )
    assert not feeds.child_safe_title("హైదరాబాద్‌లో హత్య") and not feeds.child_safe_title(
        "शहर में हत्या"
    )


# ---------------------------------------------------------------------------
# Today, pulse, briefing and background
# ---------------------------------------------------------------------------


def test_today_pulse_briefing_and_background(db, people, news):
    pat, ravi = people["pat"], people["ravi"]
    cards = news_routes._today(db, pat)
    assert cards[0]["title"] == "Today's headlines" and len(cards[0]["lines"]) == 5
    assert "Monsoon rains bring relief to farmers" in [
        line["text"] for line in cards[0]["lines"]
    ]
    assert "Open News to listen" in cards[0]["lines"][-1]["sub"]
    assert news_routes._pulse(db, pat, "")["badge"] > 0
    service.mark_opened(db, pat)
    assert news_routes._pulse(db, pat, "")["badge"] == 0
    # A keyword marked "alert me" in a new headline raises one alert per article, until it is read.
    service.add_keyword(db, pat, {"query": "metro", "alert": True})
    service.articles(db, pat, topic="foryou")
    db.execute(
        "UPDATE nw_settings SET last_opened = ? WHERE profile_id = ?",
        (kit.iso(kit.utcnow() - timedelta(hours=1)), pat["id"]),
    )
    db.commit()
    alerts = news_routes._pulse(db, pat, "")["alerts"]
    assert (
        len(alerts) == 2
        and alerts[0]["id"].startswith("news:")
        and alerts[0]["title"] == "News about metro"
    )
    assert (
        news_routes._pulse(db, pat, "")["alerts"][0]["id"] == alerts[0]["id"]
    )  # stable
    service.mark_read(db, pat, alerts[0]["id"].split(":", 1)[1])
    assert len(news_routes._pulse(db, pat, "")["alerts"]) == 1
    # The briefing lists the headlines (a template without a model).
    brief = service.briefing(db, pat)
    assert (
        brief["source"] == "template"
        and len(brief["headlines"]) == 5
        and "1. " in brief["summary"]
    )
    # Background: every followed feed that is stale is fetched again; old articles nobody saved are dropped.
    service.settings(db, ravi)
    db.execute(
        "UPDATE nw_feeds SET last_fetched = ?",
        (kit.iso(kit.utcnow() - timedelta(minutes=45)),),
    )
    keep = kit.fetch_one(db, "SELECT id FROM nw_articles LIMIT 1")["id"]
    service.save(db, pat, keep)
    db.execute(
        "UPDATE nw_articles SET fetched_at = ?",
        (kit.iso(kit.utcnow() - timedelta(days=20)),),
    )
    db.commit()
    news.down = True
    result = service.background_refresh(db)
    assert result["refreshed"] >= 4 and result["failed"] == result["refreshed"]
    assert result["pruned"] > 0 and kit.fetch_one(
        db, "SELECT id FROM nw_articles WHERE id = ?", (keep,)
    )
    assert service.background_refresh(db)["refreshed"] == 0  # fresh within 30 minutes


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def test_a_telugu_reader_and_an_english_reader_on_the_local_build(env, api, news):
    kit.forget_schema()
    household = Household(env, api)
    english = household.owner
    amma = household.add("Amma", age_group="senior")
    amma.post("/api/news/settings", {"lang": "te"})
    mine = english.get("/api/news/feed?topic=top")
    hers = amma.get("/api/news/feed?topic=top")
    assert mine["articles"][0]["title"] == "Monsoon rains bring relief to farmers"
    assert hers["articles"][0]["title"] == "రైతులకు వర్షాలతో ఊరట" and hers["lang"] == "te"
    settings = amma.get("/api/news/settings")
    assert settings["lang"] == "te" and {
        lang["code"] for lang in settings["languages"]
    } == {"en", "te", "hi"}
    keyword = amma.post("/api/news/keywords", {"query": "metro"}, expect=201)["keyword"]
    assert amma.get("/api/news/feed?topic=foryou")["articles"]
    article = hers["articles"][0]
    amma.post(f"/api/news/articles/{article['id']}/save")
    assert [a["id"] for a in amma.get("/api/news/saved")["articles"]] == [article["id"]]
    assert english.get("/api/news/saved")["articles"] == []
    later = english.get(f"/api/news/feed?topic=top&since={mine['now']}")
    assert later["articles"] == []
    assert amma.call("DELETE", f"/api/news/keywords/{keyword['id']}")[0] == 200
    assert english.call("DELETE", f"/api/news/keywords/{keyword['id']}")[0] == 404
    assert (
        english.call("POST", "/api/news/sources", {"url": "javascript:alert(1)"})[0]
        == 400
    )
    assert english.post("/api/news/refresh")["feeds"] >= 1
    assert english.get("/api/news/briefing")["headlines"]
    today = english.get("/api/life/today")
    card = next(c for c in today["cards"] if c["app"] == "news")
    assert card["title"] == "Today's headlines" and card["lines"]
    assert english.get("/api/life/pulse")["apps"]["news"]["badge"] >= 0


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_news_needs_the_app_and_keeps_saved_articles_private(cloud, news):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    status, body = owner.call("GET", "/api/news/feed")
    assert status == 402 and body["code"] == "app_required"
    cloud.billing.subscribe_app(org.owner, "news")
    for who in ("owner", "admin", "coach", "candidate", "billing", "auditor"):
        status, data = sign_in(cloud, getattr(org, who)).call(
            "GET", "/api/news/feed?topic=top"
        )
        assert status == 200 and data["articles"], (who, data)
    candidate = sign_in(cloud, org.candidate)
    first = candidate.call("GET", "/api/news/feed")[1]["articles"][0]
    assert (
        candidate.call("POST", f"/api/news/articles/{first['id']}/save", {})[0] == 200
    )
    assert [
        a["id"] for a in candidate.call("GET", "/api/news/saved")[1]["articles"]
    ] == [first["id"]]
    assert owner.call("GET", "/api/news/saved")[1]["articles"] == []
    status, keyword = candidate.call("POST", "/api/news/keywords", {"query": "metro"})
    assert status == 201
    assert (
        owner.call("DELETE", f"/api/news/keywords/{keyword['keyword']['id']}")[0] == 404
    )
    other = build_org(cloud, "Other Org", slug_email="other.test")
    cloud.billing.subscribe_app(other.owner, "news")
    other_owner = sign_in(cloud, other.owner)
    assert other_owner.call("GET", "/api/news/saved")[1]["articles"] == []
    assert (
        other_owner.call("POST", f"/api/news/articles/{first['id']}/save", {})[0] == 404
    )
