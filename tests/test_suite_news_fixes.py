"""News: the problems an independent tester found in the newsletter, the search and the de-duplication, each fixed
and kept fixed here.

* A page the writer turned off stays off when they publish or confirm a payment.
* A price that cannot be read never makes a paid newsletter free (and never frees the waiting readers).
* Adding a price tells the free readers; they keep their place and stay in the numbers.
* In child-safe mode search, followed words and alerts look only in the safe sections.
* Hindi and Telugu headlines that differ only in a vowel sign are different stories.
* The writer's newsletter numbers show on their Today card (the Business hub lists only the business apps).
* Undoing a removed reader's payment keeps them removed; a reader waiting to pay can withdraw; a daily cap on new
  readers; the readers CSV follows the search.

Every feed comes from tests/news_stub.py: nothing here reaches the internet.
"""

from __future__ import annotations

import pytest
from life_support import Household

import test_careers
import test_suite_news_features as features
from aira.suite import kit, public
from aira.suite.news import letter, news_routes, service

env = test_careers.env
api = test_careers.api
stub = test_careers.stub
# The News fixtures (a database with a household, the feed stub, no writing model, the local link index).
db = features.db
people = features.people
news = features.news
local_index = features.local_index
_no_model = features._no_model
_pick_all = features._pick_all
_titles = features._titles


def _mail(db, kind, to=""):
    return [
        m
        for m in kit.outbox(db, app="news")
        if m["kind"] == kind and (not to or m["recipient"] == to)
    ]


# ---------------------------------------------------------------------------
# the page the writer turned off
# ---------------------------------------------------------------------------


def test_a_turned_off_page_stays_off_on_publish_and_on_payment(db, people, news):
    pat = people["pat"]
    letter.save(db, pat, {"name": "Mandi Prices", "price_cents": 4900})
    row = letter.mine(db, pat)
    first = letter.share(db, pat)
    asked = letter.subscribe(db, row, {"name": "Lakshmi", "email": "l@example.com"})
    assert asked["status"] == "pending"
    letter.unshare(db, pat)
    assert letter.overview(db, pat)["page_off"] is True
    _pick_all(db, pat, 2)
    issue = letter.publish(db, pat, {"title": "Prices this week"})
    assert issue["link"] is None
    assert public.link_for(db, "news", letter.KIND, row["id"]) is None
    reader = letter.subscribers(db, pat, status="pending")[0]
    paid = letter.mark_paid(db, pat, reader["id"])
    assert public.link_for(db, "news", letter.KIND, row["id"]) is None
    access = _mail(db, "newsletter_access", "l@example.com")[0]
    assert paid["code"] in access["body"] and "/p/" not in access["body"]
    assert "page is closed for now" in access["body"]
    # Issues still go by email, without a page address in them.
    _pick_all(db, pat, 1)
    assert letter.publish(db, pat, {})["emailed"] == 1
    mail = _mail(db, "newsletter_issue", "l@example.com")[0]
    assert "/p/" not in mail["body"] and "reply to this email" in mail["body"]
    with pytest.raises(LookupError):
        public.view(db, first["token"])
    # The writer opens it again on purpose: a new link, and later issues use it.
    again = letter.share(db, pat)
    assert again["token"] != first["token"]
    assert letter.overview(db, pat)["page_off"] is False
    _pick_all(db, pat, 1)
    assert letter.publish(db, pat, {})["link"]["token"] == again["token"]


def test_the_first_issue_still_makes_the_page(db, people, news):
    pat = people["pat"]
    letter.save(db, pat, {"name": "Town Weekly"})
    assert letter.overview(db, pat)["page_off"] is False
    _pick_all(db, pat, 1)
    assert letter.publish(db, pat, {})["link"]["path"].startswith("/p/")


# ---------------------------------------------------------------------------
# prices
# ---------------------------------------------------------------------------


def test_a_price_that_cannot_be_read_never_makes_it_free(db, people, news):
    pat = people["pat"]
    letter.save(db, pat, {"name": "Mandi Prices", "price_cents": 4900})
    row = letter.mine(db, pat)
    letter.subscribe(db, row, {"name": "Lakshmi", "email": "l@example.com"})
    for unreadable in (None, "", "Rs fifty", "fifty"):
        with pytest.raises(ValueError):
            letter.save(db, pat, {"name": "Mandi Prices", "price_cents": unreadable})
    assert letter.mine(db, pat)["price_cents"] == 4900
    assert letter.subscribers(db, pat)[0]["status"] == "pending"
    assert _mail(db, "newsletter_welcome") == []
    # Leaving the price out keeps it; 0 on purpose makes it free and lets the waiting reader in.
    assert letter.save(db, pat, {"name": "Mandi Daily"})["price_cents"] == 4900
    saved = letter.save(db, pat, {"price_cents": 0})
    assert saved["freed"] == 1 and saved["told"] == 0
    assert letter.subscribers(db, pat)[0]["status"] == "active"


def test_adding_a_price_tells_the_free_readers(db, people, news):
    pat = people["pat"]
    letter.save(db, pat, {"name": "Town Weekly"})
    letter.share(db, pat)
    row = letter.mine(db, pat)
    letter.subscribe(db, row, {"name": "Sam", "email": "sam@example.com"})
    saved = letter.save(db, pat, {"price_cents": 4900})
    assert saved["told"] == 1 and saved["freed"] == 0
    told = _mail(db, "newsletter_price", "sam@example.com")
    assert len(told) == 1 and "costs" in told[0]["subject"]
    assert "/p/" in told[0]["body"] and "Subscribe" in told[0]["body"]
    # They stay in the numbers, as free readers who have not paid.
    numbers = letter.overview(db, pat)["numbers"]
    assert numbers["free"] == 1 and numbers["paid"] == 0
    # Saving again (same price) tells nobody twice.
    assert letter.save(db, pat, {"name": "Town Weekly"})["told"] == 0
    assert len(_mail(db, "newsletter_price")) == 1
    # They ask to pay on the page; once paid they get the issues again.
    row = letter.mine(db, pat)
    assert (
        letter.subscribe(db, row, {"name": "Sam", "email": "sam@example.com"})["status"]
        == "pending"
    )
    reader = letter.subscribers(db, pat, q="sam")[0]
    letter.mark_paid(db, pat, reader["id"])
    _pick_all(db, pat, 1)
    assert letter.publish(db, pat, {})["emailed"] == 1


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------


def test_undoing_a_removed_readers_payment_keeps_them_removed(db, people, news):
    pat = people["pat"]
    letter.save(db, pat, {"name": "Mandi Prices", "price_cents": 4900})
    row = letter.mine(db, pat)
    letter.subscribe(db, row, {"name": "Lakshmi", "email": "l@example.com"})
    reader = letter.subscribers(db, pat)[0]
    letter.mark_paid(db, pat, reader["id"])
    letter.remove_subscriber(db, pat, reader["id"])
    undone = letter.undo_payment(db, pat, reader["id"])
    assert undone["status"] == "cancelled" and undone["paid_until"] is None
    assert letter.waiting_for(db, pat) == 0
    assert (
        kit.count(
            db,
            "SELECT COUNT(*) FROM suite_revenue WHERE app = 'news' AND voided_at IS NULL",
        )
        == 0
    )


def test_a_reader_waiting_to_pay_can_withdraw(db, people, news):
    pat = people["pat"]
    letter.save(db, pat, {"name": "Mandi Prices", "price_cents": 4900})
    row = letter.mine(db, pat)
    asked = letter.subscribe(
        db, row, {"name": "Lakshmi", "email": "lakshmi@example.com"}
    )
    with pytest.raises(PermissionError, match="payment reference"):
        letter.unsubscribe(
            db, row, {"email": "lakshmi@example.com", "code": "AAAAA-AAAAA"}
        )
    with pytest.raises(PermissionError):  # someone else's email with this reference
        letter.unsubscribe(
            db, row, {"email": "other@example.com", "code": asked["reference"]}
        )
    assert letter.unsubscribe(
        db,
        row,
        {"email": "LAKSHMI@example.com", "code": asked["reference"].lower()},
    ) == {"status": "cancelled"}
    assert letter.waiting_for(db, pat) == 0
    # A paid reader's reference is not their code.
    again = letter.subscribe(
        db, row, {"name": "Lakshmi", "email": "lakshmi@example.com"}
    )
    reader = letter.subscribers(db, pat, status="pending")[0]
    letter.mark_paid(db, pat, reader["id"])
    with pytest.raises(PermissionError):
        letter.unsubscribe(
            db, row, {"email": "lakshmi@example.com", "code": again["reference"]}
        )


def test_new_readers_a_day_are_capped(db, people, news, monkeypatch):
    pat = people["pat"]
    monkeypatch.setattr(letter, "DAILY_CAP", 3)
    letter.save(db, pat, {"name": "Town Weekly"})
    row = letter.mine(db, pat)
    for n in range(3):
        letter.subscribe(db, row, {"name": f"R{n}", "email": f"r{n}@example.com"})
    with pytest.raises(ValueError, match="tomorrow"):
        letter.subscribe(db, row, {"name": "R9", "email": "r9@example.com"})
    assert len(_mail(db, "newsletter_welcome")) == 3
    # Someone already subscribed is still answered.
    assert letter.subscribe(db, row, {"name": "R0", "email": "r0@example.com"})[
        "already"
    ]


def test_today_shows_the_writers_newsletter_numbers(db, people, news):
    pat = people["pat"]
    assert [c["title"] for c in news_routes._today(db, pat)] == ["Today's headlines"]
    letter.save(db, pat, {"name": "Mandi Prices", "price_cents": 4900})
    row = letter.mine(db, pat)
    letter.subscribe(db, row, {"name": "Lakshmi", "email": "l@example.com"})
    letter.subscribe(db, row, {"name": "Ravi", "email": "r@example.com"})
    first = letter.subscribers(db, pat, q="lakshmi")[0]
    letter.mark_paid(db, pat, first["id"])
    card = next(
        c for c in news_routes._today(db, pat) if c["title"] == "Your newsletter"
    )
    texts = [line["text"] for line in card["lines"]]
    assert texts[0] == "1 reader wants to subscribe" and card["tone"] == "warn"
    assert texts[1].startswith("1 paid reader · earned") and "49" in texts[1]
    assert card["link"] == "#/news?tab=newsletter"
    # Not for someone else in the household.
    assert all(
        c["title"] != "Your newsletter" for c in news_routes._today(db, people["ravi"])
    )


# ---------------------------------------------------------------------------
# child-safe search, the same story
# ---------------------------------------------------------------------------


def test_child_safe_search_and_followed_words_stay_in_the_safe_sections(
    db, people, news
):
    mia, pat = people["mia"], people["pat"]
    searches = news.count("/rss/search")
    found = service.search(db, mia, "election")
    assert found["child_safe"] is True and found["articles"] == []
    assert news.count("/rss/search") == searches  # never a search of all the news
    assert service.search(db, mia, "politics")["articles"] == []
    assert _titles(service.search(db, mia, "robots")) == [
        "Robots learn to fold laundry"
    ]
    assert _titles(service.search(db, mia, "COMET")) == ["Telescope spots a new comet"]
    # Following a word: For you, the badge feeds and alerts look in the safe sections too.
    service.add_keyword(db, mia, {"query": "election", "alert": True})
    assert service.articles(db, mia, topic="foryou")["articles"] == []
    assert all(f["kind"] != "search" for f in service.feeds_for(db, mia, "all"))
    service.add_keyword(db, mia, {"query": "film", "alert": True})
    assert _titles(service.articles(db, mia, topic="foryou")) == [
        "Film festival opens with a comedy"
    ]
    assert [a["body"] for a in service.keyword_alerts(db, mia)] == [
        "Film festival opens with a comedy"
    ]
    assert news.count("/rss/search") == searches
    # An adult still searches everything.
    assert _titles(service.search(db, pat, "election"))[0] == (
        "election makes the news today"
    )


@pytest.mark.parametrize(
    ("one", "other"),
    [
        ("दिल की बात", "दाल की बात"),
        ("पेट्रोल के दाम घटे", "पेट्रोल के दाम घटें"),
        ("మంత్రి రాజీనామా", "మంత్రం రాజీనామా"),
    ],
)
def test_hindi_and_telugu_headlines_keep_their_vowel_signs(one, other):
    assert service._story_key(one) != service._story_key(other)


def test_the_same_hindi_story_with_other_punctuation_is_still_one():
    assert service._story_key("दिल की बात!") == service._story_key("दिल  की बात")
    assert service._story_key("మంత్రి రాజీనామా - ఈనాడు") == service._story_key(
        "మంత్రి  రాజీనామా — ఈనాడు"
    )


def test_two_hindi_headlines_one_letter_apart_both_show(db, people, news):
    pat = people["pat"]
    feed = service.topic_feed(db, "NATION", "en", "IN")
    service.refresh(db, feed)
    for n, title in enumerate(("दिल की बात", "दाल की बात")):
        db.execute(
            """INSERT INTO nw_articles (id, feed_id, link, title, summary, source, image, published_at, fetched_at)
            VALUES (?, ?, ?, ?, '', 'Desk', '', '2026-10-26T10:00:00Z', ?)""",
            (kit.new_id(), feed["id"], f"https://news.example/h{n}", title, kit.now()),
        )
    db.commit()
    titles = _titles(service.articles(db, pat, topic="NATION"))
    assert "दिल की बात" in titles and "दाल की बात" in titles


# ---------------------------------------------------------------------------
# on the single-user server
# ---------------------------------------------------------------------------


def test_save_answers_and_the_readers_csv_on_the_local_build(
    env, api, news, local_index
):
    kit.forget_schema()
    home = Household(env, api)
    pat = home.owner
    made = pat.post("/api/news/newsletter", {"name": "Town Weekly"})
    assert made["told"] == 0 and made["freed"] == 0 and made["page_off"] is False
    link = pat.post("/api/news/newsletter/link")["link"]
    for name in ("Lakshmi", "Ravi"):
        status, _ = _public_post(
            env,
            f"/api/public/{link['token']}/subscribe",
            {"name": name, "email": f"{name.lower()}@example.com"},
        )
        assert status == 201
    status, sheet = pat.call(
        "GET", "/api/news/newsletter/subscribers?format=csv&status=&q=laks", raw=True
    )
    text = sheet.decode("utf-8")
    assert status == 200 and "lakshmi@example.com" in text and "ravi@" not in text
    # A price that cannot be read is refused; a real one tells the free readers.
    assert pat.call("POST", "/api/news/newsletter", {"price_cents": None})[0] == 400
    changed = pat.post("/api/news/newsletter", {"price_cents": 4900})
    assert changed["told"] == 2 and changed["numbers"]["free"] == 2
    pat.ok("DELETE", "/api/news/newsletter/link")
    assert pat.get("/api/news/newsletter")["page_off"] is True


def _public_post(env, path, body):
    return features._public(env, "POST", path, body)
