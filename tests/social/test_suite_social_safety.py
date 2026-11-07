"""Social safety: uploaded photos only, who may contact a child or a teen, the automatic check that holds writing
for review, and the evidence Dating keeps for moderators when a match ends or is reported."""

from __future__ import annotations

import base64

import pytest
from test_suite_social_dating import Person, _match
from test_suite_social_plus import png

from aira.suite.social import dating, db, moderation, photos, service, store


@pytest.fixture(autouse=True)
def _social_db(tmp_path):
    db.set_path(lambda: tmp_path / "social-safety.sqlite3")
    db.forget()
    photos.forget()
    yield
    db.reset_path()
    db.forget()
    photos.forget()
    moderation.set_image_scanner(None)


@pytest.fixture
def sc():
    connection = db.connect()
    yield connection
    connection.close()


def _join(sc, key, name, *, age="adult", home="home-1"):
    ident = {
        "key": key,
        "profile": {"id": key, "name": name, "emoji": "🙂", "age_group": age},
        "home": home,
        "moderator": False,
        "hosted": False,
    }
    service.create_account(sc, ident, {"handle": name.lower()})
    return service.me(sc, ident)


def _fresh(sc, acct):
    return service.account(sc, acct["id"])


def _upload(sc, owner):
    return photos.save(sc, owner, base64.b64encode(png()).decode())["ref"]


# ---------------------------------------------------------------------------
# photos: uploads only (P0-4)
# ---------------------------------------------------------------------------


def test_profile_and_dating_photos_must_be_uploads(sc):
    pat = _join(sc, "p:pat", "Pat")
    for field in ("avatar", "cover"):
        with pytest.raises(ValueError, match="Upload the photo"):
            service.update_account(sc, pat, {field: "https://tracker.example/me.jpg"})
    with pytest.raises(ValueError, match="Upload the photo"):
        service.create_group(
            sc, pat, {"name": "Walkers", "photo": "https://x.example/p.png"}
        )
    with pytest.raises(ValueError, match="Upload the photo"):
        service.create_post(
            sc, pat, {"text": "hi", "photos": ["https://x.example/a.jpg"]}
        )
    picture = _upload(sc, pat)
    assert service.update_account(sc, pat, {"avatar": picture})["avatar"] == picture
    assert service.update_account(sc, pat, {"avatar": "😀"})["avatar"] == "😀"
    # another person's upload cannot be borrowed
    sam = _join(sc, "p:sam", "Sam", home="home-2")
    with pytest.raises(ValueError, match="not found"):
        service.update_account(sc, sam, {"cover": picture})
    # a link saved before is never shown
    assert photos.display("https://tracker.example/me.jpg") == ""
    assert photos.display("😀") == "😀"
    assert photos.display_list(["https://x.example/a.jpg", picture]) == [
        photos.display(picture)
    ]


def test_old_picture_links_are_cleared_once(sc):
    pat = _join(sc, "p:pat", "Pat")
    ana = Person(sc, "p:ana", "Ana")
    ana.date(first_name="Ana")
    kept = ana.profile["photos"][0]
    sc.execute(
        "UPDATE so_accounts SET avatar = 'https://tracker.example/a.jpg', cover = 'http://t.example/c.jpg' WHERE id = ?",
        (pat["id"],),
    )
    sc.execute(
        "UPDATE so_dating_profiles SET photos = ? WHERE account_id = ?",
        (store.dumps(["https://tracker.example/d.jpg", kept]), ana.acct["id"]),
    )
    sc.execute("DELETE FROM so_settings WHERE key = 'external_pictures_cleared'")
    store.clear_external_pictures(sc)
    sc.commit()
    cleared = _fresh(sc, pat)
    assert cleared["avatar"] == "🙂" and cleared["cover"] == ""
    assert ana.profile["photos"] == [kept]
    # once only: a later row is left to the input checks
    sc.execute(
        "UPDATE so_accounts SET cover = 'https://late.example/c.jpg' WHERE id = ?",
        (pat["id"],),
    )
    store.clear_external_pictures(sc)
    assert _fresh(sc, pat)["cover"] == "https://late.example/c.jpg"


def test_an_image_scanner_plugs_in(sc):
    pat = _join(sc, "p:pat", "Pat")
    assert moderation.scan_image(b"x", "image/png").status == "unscanned"

    class Blocker:
        def scan(self, data, mime, *, young):
            return moderation.ImageVerdict("block", "matched a known hash")

    moderation.set_image_scanner(Blocker())
    with pytest.raises(ValueError, match="cannot be uploaded"):
        _upload(sc, pat)


# ---------------------------------------------------------------------------
# minors: who may befriend and message a child or a teen
# ---------------------------------------------------------------------------


def test_adult_strangers_cannot_befriend_or_message_a_teen(sc):
    mum = _join(sc, "p:mum", "Mum", home="home-1")
    teo = _join(sc, "p:teo", "Teo", age="teen", home="home-1")
    sam = _join(sc, "p:sam", "Sam", home="home-2")
    lia = _join(sc, "p:lia", "Lia", age="teen", home="home-3")
    service.update_account(sc, sam, {"open_messages": True})
    # a stranger adult: no request, no chat
    with pytest.raises(PermissionError, match="teen"):
        service.request_friend(sc, sam, teo["id"])
    with pytest.raises(PermissionError):
        service.open_direct(sc, sam, teo["id"])
    # teens befriend each other; family is family
    service.request_friend(sc, lia, teo["id"])
    assert service.request_friend(sc, teo, lia["id"]) == {"relation": "friends"}
    service.request_friend(sc, mum, teo["id"])
    assert service.request_friend(sc, teo, mum["id"]) == {"relation": "friends"}
    # a teen asking a stranger adult waits for a guardian; their yes makes Sam an approved contact
    assert service.request_friend(sc, teo, sam["id"]) == {"relation": "awaiting_parent"}
    [asking] = service.guardian_requests(sc, mum)
    assert asking["other"]["id"] == sam["id"]
    service.answer_guardian(sc, mum, teo["id"], sam["id"], True)
    assert service.contact_ok(sc, teo, sam)
    assert service.request_friend(sc, sam, teo["id"]) == {"relation": "friends"}
    chat = service.open_direct(sc, sam, teo["id"])
    assert service.send(sc, sam, chat["id"], {"text": "Good game today"})["message"]
    # the guardian takes Sam off the list: even as friends, no more messages
    contacts = service.approved_contacts(sc, mum, teo["id"])
    assert [c["id"] for c in contacts["contacts"]] == [sam["id"]] and contacts[
        "can_change"
    ]
    service.set_approved_contact(sc, mum, teo["id"], {"contact_id": sam["id"]}, False)
    with pytest.raises(PermissionError):
        service.send(sc, sam, chat["id"], {"text": "still there?"})
    # only a guardian keeps the list; by @name works too
    with pytest.raises(PermissionError):
        service.set_approved_contact(sc, sam, teo["id"], {"handle": "sam"}, True)
    with pytest.raises(PermissionError):
        service.set_approved_contact(sc, teo, teo["id"], {"handle": "sam"}, True)
    service.set_approved_contact(sc, mum, teo["id"], {"handle": "@sam"}, True)
    assert service.contact_ok(sc, teo, sam)


def test_an_approved_contact_can_find_and_ask_a_child(sc):
    mum = _join(sc, "p:mum", "Mum", home="home-1")
    mia = _join(sc, "p:mia", "Mia", age="child", home="home-1")
    gran = _join(sc, "p:gran", "Gran", home="home-2")
    with pytest.raises(LookupError):
        service.request_friend(sc, gran, mia["id"])
    service.set_approved_contact(sc, mum, mia["id"], {"contact_id": gran["id"]}, True)
    assert service.request_friend(sc, gran, mia["id"]) == {"relation": "request_sent"}


# ---------------------------------------------------------------------------
# the automatic check: held for review, never silently delivered
# ---------------------------------------------------------------------------


def test_the_moderation_module():
    assert moderation.check("hello there", young=True).action == "allow"
    for text in (
        "add me on snap",
        "call me on 98765 43210",
        "let's meet at the park",
        "mail me at teo@mail.example",
        "don't tell your parents",
        "send nudes",
        "I want to kill myself",
    ):
        assert moderation.check(text, young=True).action == "hold", text
        assert moderation.check(text).action in ("allow", "mask"), text
    assert moderation.check("on 2026-11-06 at 10:30", young=True).action == "allow"
    assert moderation.check("I love Sussex", young=True).action == "allow"
    assert moderation.mask("what a sh!t day, fuuuck") == "what a **** day, ******"
    assert moderation.mask("darn it", extra_words=["darn"]) == "**** it"
    assert moderation.self_harm_intent(
        "i wanna die"
    ) and not moderation.self_harm_intent("die hard is a film")
    assert (
        "14416" in moderation.crisis_reply()
        and "1-800-891-4416" in moderation.crisis_reply(True)
    )
    assert "emergency services" in moderation.crisis_reply()
    verdict = moderation.check("add me on whatsapp, I want to die", young=True)
    assert verdict.categories == ["contact", "self_harm"] and verdict.reason.startswith(
        "Held for review"
    )


def test_writing_with_a_teen_is_held_for_review(sc):
    mum = _join(sc, "p:mum", "Mum", home="home-1")
    teo = _join(sc, "p:teo", "Teo", age="teen", home="home-1")
    sam = _join(sc, "p:sam", "Sam", home="home-2")
    service.set_approved_contact(sc, mum, teo["id"], {"contact_id": sam["id"]}, True)
    service.request_friend(sc, sam, teo["id"])
    service.request_friend(sc, teo, sam["id"])
    chat = service.open_direct(sc, sam, teo["id"])
    sent = service.send(
        sc, sam, chat["id"], {"text": "add me on snap, my number is 98765 43210"}
    )
    assert sent["held"] and "safety check" in sent["notice"]
    # Teo never sees it; Sam sees it waiting
    assert service.messages(sc, teo, chat["id"])["messages"] == []
    [mine] = service.messages(sc, sam, chat["id"])["messages"]
    assert mine["held"] is True and service.unread_messages(sc, teo) == 0
    # Teo's household moderates it (though Sam is from another household), and Mum is told
    [queued] = service.reports(sc, home="home-1")
    assert queued["automatic"] and queued["held"] and queued["reporter"] is None
    assert "contact details" in queued["reason"]
    kinds = [n["kind"] for n in service.notifications(sc, mum)["notifications"]]
    assert "safety_hold" in kinds
    # released: delivered after all
    service.moderate(sc, "@mum", queued["id"], "release", home="home-1")
    [delivered] = service.messages(sc, teo, chat["id"])["messages"]
    assert delivered["text"].startswith("add me on snap") and delivered["held"] is False
    # removed: it stays hidden, from its author too
    again = service.send(
        sc, sam, chat["id"], {"text": "let's meet up, don't tell your parents"}
    )
    [second] = [
        r
        for r in service.reports(sc, home="home-1")
        if r["target_id"] == again["message"]["id"]
    ]
    service.moderate(sc, "@mum", second["id"], "hide", home="home-1")
    assert [m["id"] for m in service.messages(sc, sam, chat["id"])["messages"]] == [
        delivered["id"]
    ]
    # a teen who writes about hurting themselves gets help at once, and the message waits
    hurt = service.send(sc, teo, chat["id"], {"text": "I want to kill myself"})
    assert hurt["held"] and "14416" in hurt["support"]
    # grown-ups among themselves: masked as before, never held
    pat = _join(sc, "p:pat", "Pat", home="home-1")
    service.request_friend(sc, pat, mum["id"])
    service.request_friend(sc, mum, pat["id"])
    talk = service.open_direct(sc, pat, mum["id"])
    plain = service.send(sc, pat, talk["id"], {"text": "call me on 98765 43210, shit"})
    assert not plain.get("held") and plain["message"]["text"].endswith("****")


def test_a_teens_post_and_comments_on_it_are_checked(sc):
    teo = _join(sc, "p:teo", "Teo", age="teen", home="home-1")
    lia = _join(sc, "p:lia", "Lia", age="teen", home="home-1")
    service.request_friend(sc, teo, lia["id"])
    service.request_friend(sc, lia, teo["id"])
    held_post = service.create_post(sc, teo, {"text": "dm me on insta"})
    assert held_post["held"]
    assert service.feed(sc, lia)["posts"] == []
    post = service.create_post(sc, teo, {"text": "Match day!"})
    reply = service.comment(sc, lia, post["id"], {"text": "add me on discord"})
    assert reply["held"] and service.comments(sc, teo, post["id"]) == []
    fine = service.comment(sc, lia, post["id"], {"text": "Well played"})
    assert not fine.get("held")


# ---------------------------------------------------------------------------
# Dating: the evidence survives the end of a match (P0-3)
# ---------------------------------------------------------------------------


def test_ending_a_match_keeps_its_chat_for_moderators_only(sc):
    pat, ana = Person(sc, "p:pat", "Pat"), Person(sc, "p:ana", "Ana")
    pat.date(first_name="Pat")
    ana.date(first_name="Ana")
    match = _match(sc, pat, ana)
    dating.chat_send(sc, pat.ident, pat.acct, match["id"], {"text": "Hi Ana"})
    dating.chat_send(sc, ana.ident, ana.acct, match["id"], {"text": "Something nasty"})
    chat_id = store.row(
        sc, "SELECT chat_id FROM so_dating_matches WHERE id = ?", (match["id"],)
    )["chat_id"]
    dating.unmatch(sc, ana.ident, ana.acct, match["id"])
    # gone for both of them
    assert dating.matches(sc, pat.ident, pat.acct)["matches"] == []
    assert (
        store.rows(sc, "SELECT * FROM so_messages WHERE chat_id = ?", (chat_id,)) == []
    )
    # kept for moderators, with both profiles as they were
    [ended] = dating.ended_chats(sc)
    assert ended["reason"] == "unmatched"
    assert [m["text"] for m in ended["transcript"]] == ["Hi Ana", "Something nasty"]
    assert {p["dating_profile"]["first_name"] for p in ended["profiles"].values()} == {
        "Pat",
        "Ana",
    }
    # Pat reports Ana afterwards: the ended chat goes into the report
    dating.report(sc, pat.ident, pat.acct, ana.profile["id"], {"reason": "Abuse"})
    [queued] = service.reports(sc)
    [evidence] = queued["evidence"]
    assert evidence["kind"] == "dating"
    [kept] = evidence["data"]["ended_chats"]
    assert [m["text"] for m in kept["transcript"]] == ["Hi Ana", "Something nasty"]
    # a household moderator sees Dating evidence only of reports they filed themselves
    [local] = service.reports(sc, home=pat.ident["home"], viewer_id=ana.acct["id"])
    assert local["evidence"] == [
        {"kind": "dating", "withheld": True, "created_at": evidence["created_at"]}
    ]
    [own] = service.reports(sc, home=pat.ident["home"], viewer_id=pat.acct["id"])
    assert own["evidence"][0]["data"]["ended_chats"]


def test_report_and_block_copies_the_live_chat_first(sc):
    pat, lee = Person(sc, "p:pat", "Pat"), Person(sc, "p:lee", "Lee")
    pat.date(first_name="Pat")
    lee.date(first_name="Lee", bio="Not who I seem")
    match = _match(sc, pat, lee)
    dating.chat_send(sc, lee.ident, lee.acct, match["id"], {"text": "Send me money"})
    dating.report(
        sc, pat.ident, pat.acct, lee.profile["id"], {"reason": "Scam", "block": True}
    )
    [queued] = service.reports(sc)
    data = queued["evidence"][0]["data"]
    assert [m["text"] for m in data["live_chat"]] == ["Send me money"]
    reported = data["profiles"]["a" if data["reporter"] == "b" else "b"]
    assert reported["dating_profile"]["bio"] == "Not who I seem"
    assert dating.ended_chats(sc)[0]["reason"] == "blocked"


def test_kept_chats_and_evidence_are_purged_after_the_retention(sc):
    pat, ana = Person(sc, "p:pat", "Pat"), Person(sc, "p:ana", "Ana")
    pat.date(first_name="Pat")
    ana.date(first_name="Ana")
    match = _match(sc, pat, ana)
    dating.chat_send(sc, pat.ident, pat.acct, match["id"], {"text": "Hi"})
    dating.report(
        sc, pat.ident, pat.acct, ana.profile["id"], {"reason": "x", "block": True}
    )
    assert service.purge_kept(sc) == {"ended_chats": 0, "evidence": 0}
    past = store.later(-1)
    sc.execute("UPDATE so_dating_ended SET purge_after = ?", (past,))
    sc.execute("UPDATE so_evidence SET purge_after = ?", (past,))
    sc.commit()
    # evidence of an open report stays until it is closed
    assert service.purge_kept(sc) == {"ended_chats": 1, "evidence": 0}
    [queued] = service.reports(sc)
    service.moderate(sc, "@mod", queued["id"], "dismiss")
    assert service.purge_kept(sc) == {"ended_chats": 0, "evidence": 1}
    assert store.RETENTION_DAYS == 90
