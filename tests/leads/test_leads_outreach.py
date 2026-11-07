"""Aira Leads for services: finding people who need web, marketing, content or ads help, and reaching them.

Covers the intent source, the gaps read from Google Maps and from a website audit, the email outbox (drafts,
approval, the real SMTP send to a stand-in server, the daily cap, suppression, the region guard), the call
assist (talk track, calling hours, call log) and the HTTP routes. Nothing here dials a number: Aira has no
auto-dialer, and a test pins that down.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aira.leads import calls, enrich, intent, mail, scoring, service, sources, store
from smtp_stub import SmtpStub
from test_leads import (  # noqa: F401  (fixtures)
    FakeBridge,
    NOW,
    api,
    bridge,
    client,
    connected,
    db,
    env,
    site,
    stub,
)

SENDER = {
    "from_name": "Sam Seller",
    "from_email": "sam@seller.test",
    "smtp_host": "127.0.0.1",
    "smtp_security": "none",
    "postal_address": "12 Example Street, Austin, TX 78701, USA",
    "delay_seconds": 2,
    "offer": "I build websites and run ads for local businesses",
    "services": ["website", "ads"],
}


@pytest.fixture
def smtp():
    server = SmtpStub().start()
    yield server
    server.stop()


def configure(db, smtp, **extra):
    return mail.save_settings(db, {**SENDER, "smtp_port": smtp.port, **extra})


def add_lead(db, **fields):
    list_id = store.default_list(db)["id"]
    base = {
        "kind": "company",
        "name": "Acme Bakery",
        "company": "Acme Bakery",
        "email": "owner@acmebakery.test",
        "phone": "(512) 555-0100",
        "website": "https://acmebakery.test",
        "location": "Austin, TX 78704",
    }
    return store.insert_lead(db, {**base, **fields}, list_id=list_id)["lead"]


# ---------------------------------------------------------------------------
# finding people who ask for these services
# ---------------------------------------------------------------------------


def test_the_intent_search_is_built_from_the_services_you_offer():
    params, applied, warnings = intent.build_intent_params(
        {
            "services": ["website", "ads"],
            "where": ["reddit", "quora"],
            "freshness": "week",
            "location": "Austin",
        }
    )
    q = params["q"]
    assert "site:reddit.com" in q and "site:quora.com" in q and '"looking for"' in q
    assert '"web designer"' in q and '"Google Ads"' in q and '"Austin"' in q
    assert params["tbs"] == "qdr:w" and "start" not in params
    assert (
        any("Website design" in line for line in applied)
        and "never replies" in warnings[0]
    )
    anywhere = intent.build_intent_params({"services": "marketing", "where": ["web"]})[
        0
    ]["q"]
    assert "site:" not in anywhere
    for bad in (
        {},
        {"services": []},
        {"services": ["nonsense"]},
        {"services": ["website"], "where": ["myspace"]},
        {"services": ["website"], "freshness": "decade"},
        {"services": ["website"], "start": 7},
    ):
        with pytest.raises(ValueError):
            intent.build_intent_params(bad)
    assert {s["id"] for s in intent.catalog()} == set(intent.SERVICE_IDS)


def test_a_search_for_requests_returns_public_posts_with_what_they_say(
    db, client, stub, bridge
):
    out = service.search(
        db,
        client,
        {
            "source": "google_intent",
            "services": ["website", "ads", "content"],
            "where": ["reddit", "quora", "web"],
        },
        now=NOW,
    )
    assert out["count"] == 3 and out["credits"] == 1 and out["next_start"] is None
    by_name = {lead["name"]: lead for lead in out["leads"]}
    bakery = by_name["Looking for a web designer for my bakery"]
    assert bakery["kind"] == "request" and bakery["source"] == "google_intent"
    assert bakery["signals"][0]["type"] == "request" and bakery["signals"][0][
        "url"
    ].startswith("https://www.reddit.com/")
    details = next(
        s["text"] for s in bakery["signals"] if s["type"] == "request_detail"
    )
    assert "budget" in details and "urgent" in details and "2 days ago" in details
    assert "website" in bakery["tags"] and bakery["notes"] == "Found on reddit.com"
    sent = [c for c in stub.calls if c.engine == "google"][-1]
    assert sent.params["tbs"] == "qdr:m" and "looking for" in sent.params["q"]
    saved = service.search(
        db,
        client,
        {
            "source": "google_intent",
            "services": ["website", "ads", "content"],
            "where": ["reddit", "quora", "web"],
            "auto_add": True,
        },
        now=NOW,
    )
    assert saved["added"] == 3
    again = service.search(
        db,
        client,
        {
            "source": "google_intent",
            "services": ["website", "ads", "content"],
            "where": ["reddit", "quora", "web"],
            "auto_add": True,
        },
        now=NOW,
    )
    assert again["added"] == 0 and again["duplicate_count"] == 3, (
        "a post is recognised by its link"
    )
    assert store.query_leads(db, kind="request")["total"] == 3


def test_gaps_in_a_businesss_own_presence_are_read_from_its_maps_listing():
    leads = sources.normalize_maps(
        {
            "local_results": [
                {
                    "title": "No Site Cafe",
                    "place_id": "p1",
                    "reviews": 3,
                    "rating": 3.2,
                    "phone": "(512) 555-0111",
                },
                {
                    "title": "Solid Bakery",
                    "place_id": "p2",
                    "reviews": 480,
                    "rating": 4.8,
                    "website": "https://solid.test",
                },
            ]
        }
    )
    needy, solid = leads
    kinds = {s["type"] for s in needy["signals"]}
    assert {"no_website", "thin_reviews", "low_rating"} <= kinds
    assert not {"no_website", "thin_reviews", "low_rating"} & {
        s["type"] for s in solid["signals"]
    }
    assert (
        intent.maps_needs({"website": "https://x.test", "reviews": 1, "rating": 5})[0][
            "text"
        ]
        == "Only 1 Google review"
    )


def test_the_services_you_offer_raise_the_score_of_leads_that_need_them(db):
    icp = scoring.normalize_icp(
        {"services": ["website", "social"], "keywords": "bakery"}
    )
    needy = {
        "kind": "company",
        "name": "No Site Bakery",
        "company": "No Site Bakery",
        "phone": "(512) 555-0111",
        "signals": [
            {
                "type": "no_website",
                "text": "No website listed on Google Maps",
                "url": "",
            }
        ],
    }
    fine = {
        "kind": "company",
        "name": "Fine Bakery",
        "company": "Fine Bakery",
        "phone": "(512) 555-0112",
        "website": "https://fine.test",
        "signals": [],
    }
    needy_score, why = scoring.score_lead(needy, icp)
    fine_score, _ = scoring.score_lead(fine, icp)
    assert needy_score > fine_score and any(
        "Needs Website design" in line for line in why
    )
    post = {
        "kind": "request",
        "name": "Need someone for Facebook ads",
        "tags": ["ads"],
        "signals": [
            {
                "type": "request",
                "text": "need a PPC specialist",
                "url": "https://reddit.com/r/x/1",
            }
        ],
    }
    _, post_why = scoring.score_lead(post, scoring.normalize_icp({"services": ["ads"]}))
    assert any("Asked for it in a public post" in line for line in post_why)
    hiring = {
        "kind": "company",
        "name": "Hiring Co",
        "signals": [
            {"type": "hiring", "text": "Hiring: Content Writer (Austin)", "url": ""}
        ],
    }
    assert any(
        "Needs Content" in line
        for line in scoring.score_lead(
            hiring, scoring.normalize_icp({"services": ["content"]})
        )[1]
    )
    with pytest.raises(ValueError, match="Unknown service"):
        scoring.normalize_icp({"services": ["plumbing"]})


# ---------------------------------------------------------------------------
# the website audit
# ---------------------------------------------------------------------------

GOOD_PAGE = (
    "<html><head><title>Fine Bakery | Fresh bread in Austin</title><meta name='viewport' content='width=device-width'>"
    "<meta name='description' content='Fresh bread.'><script async src='https://www.googletagmanager.com/gtag/js?id=G-1'></script>"
    "<script>fbq('init','1')</script></head><body>"
    + "<p>"
    + "We bake bread every morning with local flour and honest ingredients. " * 12
    + "</p>"
    + "<a href='https://instagram.com/finebakery'>Instagram</a><footer>© 2026 Fine Bakery</footer></body></html>"
)


def test_an_audit_lists_only_what_is_visibly_missing():
    good = enrich.Page("https://fine.test/", 200, GOOD_PAGE, 0.3)
    audit = enrich.audit_home(good, enrich.parse_page(good), year=2026)
    assert audit["findings"] == [] and audit["summary"] == "Nothing obvious is missing"
    bad_html = "<html><head><title>Joe</title></head><body><p>Welcome</p><footer>Copyright 2016 Joe's</footer></body></html>"
    bad = enrich.Page("http://joes.test/", 200, bad_html, 4.2)
    audit = enrich.audit_home(bad, enrich.parse_page(bad), year=2026)
    found = {f["id"]: f for f in audit["findings"]}
    assert {
        "no_https",
        "no_viewport",
        "weak_title",
        "no_description",
        "no_analytics",
        "no_ad_pixels",
        "old_copyright",
        "thin_content",
        "no_social_links",
        "slow",
    } <= set(found)
    assert found["old_copyright"]["label"] == "The footer says © 2016"
    assert [f["severity"] for f in audit["findings"]][0] == "high", (
        "the most serious come first"
    )
    assert {f["service"] for f in audit["findings"]} <= set(intent.SERVICE_IDS)
    signals = enrich.audit_signals(audit)
    assert len(signals) == 3 and all(
        s["type"] == "weak_site" and s["text"].startswith("Site audit: ")
        for s in signals
    )


def test_enriching_a_lead_audits_its_site_and_keeps_the_findings_as_signals(db, site):
    scoring.save_icp(db, {"services": ["website", "marketing"]})
    lead = add_lead(db, website=site.url, email="", phone="")
    updated = service.enrich_lead(db, lead["id"])
    audit = updated["enrichment"]["audit"]
    assert {f["id"] for f in audit["findings"]} >= {"no_https", "no_viewport"}
    assert any(s["type"] == "weak_site" for s in updated["signals"])
    assert any("Needs" in line for line in updated["score_reasons"])


# ---------------------------------------------------------------------------
# the outbox: drafts, review, approval
# ---------------------------------------------------------------------------


def test_drafts_use_real_findings_and_skip_leads_that_cannot_be_emailed(
    db, smtp, bridge
):
    configure(db, smtp)
    fine = add_lead(
        db, name="Gap Bakery", company="Gap Bakery", email="hello@gapbakery.test"
    )
    store.add_signals(
        db,
        fine["id"],
        [
            {
                "type": "weak_site",
                "text": "Site audit: No mobile viewport tag",
                "url": "https://gapbakery.test/",
            }
        ],
    )
    no_email = add_lead(
        db, name="Mute Cafe", company="Mute Cafe", email="", website="https://mute.test"
    )
    post = store.insert_lead(
        db,
        {
            "kind": "request",
            "name": "Looking for a designer",
            "source": "google_intent",
            "source_ref": "https://reddit.com/r/x/9",
        },
        list_id=store.default_list(db)["id"],
    )["lead"]
    blocked = add_lead(
        db,
        name="Blocked Co",
        company="Blocked Co",
        email="x@blocked.test",
        website="https://blocked.test",
    )
    mail.add_suppression(db, "blocked.test", "asked us to stop")
    london = add_lead(
        db,
        name="London Co",
        company="London Co",
        email="hi@london.test",
        website="https://london.test",
        location="London, UK",
    )
    out = service.draft_emails(
        db,
        [fine["id"], no_email["id"], post["id"], blocked["id"], london["id"], "0" * 32],
        {"services": ["website"]},
    )
    assert out["created"] == 1 and out["messages"][0]["status"] == "draft"
    body = out["messages"][0]["body"]
    assert (
        "no mobile viewport tag" in body
        and "Gap Bakery" in body
        and "Sam Seller" in body
    )
    assert (
        out["messages"][0]["source"] == "template"
        and out["messages"][0]["to_address"] == "hello@gapbakery.test"
    )
    reasons = {s["name"] or s["lead_id"]: s["reason"] for s in out["skipped"]}
    assert (
        "Enrich" in reasons["Mute Cafe"]
        and "public post" in reasons["Looking for a designer"]
    )
    assert (
        "do-not-contact" in reasons["Blocked Co"]
        and "prior consent" in reasons["London Co"]
    )
    assert "no longer exists" in reasons["0" * 32]
    again = service.draft_emails(db, [fine["id"]], {})
    assert again["created"] == 0 and "already waiting" in again["skipped"][0]["reason"]
    settings = mail.save_settings(db, {"allow_regulated_regions": True})
    assert settings["allow_regulated_regions"] is True
    assert service.draft_emails(db, [london["id"]], {})["created"] == 1


def test_the_model_writes_the_draft_but_only_from_facts_it_is_given(db, smtp, bridge):
    configure(db, smtp)
    lead = add_lead(db)
    prompts: list[str] = []

    def writer(prompt):
        prompts.append(prompt)
        return json.dumps(
            {
                "subject": "A quick idea for Acme Bakery",
                "body": "Hi, I saw Acme Bakery has no website listed. Could I help?",
            }
        )

    bridge.model = writer
    out = service.draft_emails(db, [lead["id"]], {"offer": "Websites that sell"})
    assert (
        out["messages"][0]["source"] == "model"
        and out["messages"][0]["subject"] == "A quick idea for Acme Bakery"
    )
    assert "Never claim to have reviewed" in prompts[0] and "Acme Bakery" in prompts[0]
    edited = mail.update_message(
        db, out["messages"][0]["id"], None, "Hi there, a shorter note."
    )
    assert edited["source"] == "edited" and edited["status"] == "draft"


def test_a_message_must_be_approved_and_editing_withdraws_the_approval(
    db, smtp, bridge
):
    configure(db, smtp)
    lead = add_lead(db)
    message = service.draft_emails(db, [lead["id"]], {})["messages"][0]
    assert mail.approve(db, [message["id"]]) == {"approved": 1}
    assert mail.list_messages(db, status="approved")[0]["id"] == message["id"]
    changed = mail.update_message(db, message["id"], "New subject", None)
    assert changed["status"] == "draft" and changed["approved_at"] == ""
    assert mail.reject(db, [message["id"]]) == {"rejected": 1}
    with pytest.raises(ValueError, match="cannot be edited"):
        mail.update_message(db, message["id"], "x", "y")
    mail.delete_message(db, message["id"])
    with pytest.raises(LookupError):
        mail.delete_message(db, message["id"])


# ---------------------------------------------------------------------------
# sending: the real SMTP path, the cap, the guards
# ---------------------------------------------------------------------------


def approved(db, smtp, count=1, **lead_overrides):
    messages = []
    for index in range(count):
        lead = add_lead(
            db,
            name=f"Biz {index}",
            company=f"Biz {index}",
            email=f"owner{index}@biz{index}.test",
            website=f"https://biz{index}.test",
            phone=f"(512) 555-01{index:02d}",
            **lead_overrides,
        )
        messages.append(service.draft_emails(db, [lead["id"]], {})["messages"][0])
    mail.approve(db, [m["id"] for m in messages])
    return messages


def test_approved_messages_are_sent_through_the_senders_own_mail_server(
    db, smtp, bridge
):
    settings = configure(db, smtp)
    messages = approved(db, smtp, 3)
    pauses: list[float] = []
    out = mail.send_approved(db, settings, "", sleep=pauses.append)
    assert out["sent"] == 3 and out["failed"] == 0 and out["skipped"] == []
    assert pauses == [2, 2], "a pause between messages, none before the first"
    assert len(smtp.messages) == 3
    first = smtp.parsed(0)
    assert (
        first["From"] == "Sam Seller <sam@seller.test>"
        and first["To"] == "owner0@biz0.test"
    )
    assert first["List-Unsubscribe"] == "<mailto:sam@seller.test?subject=unsubscribe>"
    text = first.get_payload(decode=True).decode()
    assert (
        "12 Example Street, Austin, TX 78701" in text and 'reply "unsubscribe"' in text
    )
    assert messages[0]["body"].split("\n")[0] in text
    assert [m["status"] for m in mail.list_messages(db, status="sent")] == ["sent"] * 3
    assert all(
        store.get_lead(db, m["lead_id"])["status"] == "contacted" for m in messages
    )
    assert mail.send_approved(db, settings, "")["sent"] == 0, (
        "a sent message is never sent twice"
    )
    assert mail.counts(db)["sent_today"] == 3


def test_the_daily_cap_holds_back_the_rest(db, smtp, bridge):
    settings = configure(db, smtp, daily_cap=2)
    approved(db, smtp, 3)
    out = mail.send_approved(db, settings, "", sleep=lambda _s: None)
    assert out["sent"] == 2 and out["remaining_today"] == 0 and len(smtp.messages) == 2
    later = mail.send_approved(db, settings, "", sleep=lambda _s: None)
    assert later["sent"] == 0 and "daily cap of 2" in later["note"]
    assert len(mail.list_messages(db, status="approved")) == 1
    tomorrow = datetime(2099, 1, 2, tzinfo=timezone.utc)
    assert (
        mail.send_approved(db, settings, "", now=tomorrow, sleep=lambda _s: None)[
            "sent"
        ]
        == 1
    )


def test_guards_are_checked_again_at_send_time(db, smtp, bridge):
    settings = configure(db, smtp)
    a, b, c = approved(db, smtp, 3)
    mail.add_suppression(db, "owner1@biz1.test", "unsubscribed by reply")
    store.update_lead(db, c["lead_id"], {"status": "disqualified"})
    out = mail.send_approved(db, settings, "", sleep=lambda _s: None)
    assert out["sent"] == 1 and len(out["skipped"]) == 2
    assert (
        "do-not-contact" in out["skipped"][0]["reason"]
        and "disqualified" in out["skipped"][1]["reason"]
    )
    assert [
        m["status"]
        for m in (mail.list_messages(db, lead_id=x["lead_id"])[0] for x in (a, b, c))
    ] == ["sent", "skipped", "skipped"]
    assert [m["to"] for m in smtp.messages] == [["owner0@biz0.test"]]


def test_a_region_that_needs_consent_is_refused_even_after_approval(db, smtp, bridge):
    settings = configure(db, smtp, allow_regulated_regions=True)
    (message,) = approved(db, smtp, 1, location="Toronto, ON")
    settings = mail.save_settings(db, {"allow_regulated_regions": False})
    out = mail.send_approved(db, settings, "", sleep=lambda _s: None)
    assert (
        out["sent"] == 0
        and "prior consent" in out["skipped"][0]["reason"]
        and smtp.messages == []
    )
    assert (
        mail.regulated_region({"location": "Austin, TX"}) is False
        and mail.regulated_region({"location": "Berlin, Germany"}) is True
    )


def test_a_wrong_password_stops_the_batch_and_a_refused_recipient_does_not(db, bridge):
    locked = SmtpStub(
        require_login=("sam", "right-password"), refuse={"owner0@biz0.test"}
    ).start()
    try:
        settings = configure(db, locked, smtp_username="sam")
        approved(db, locked, 3)
        wrong = mail.send_approved(db, settings, "wrong", sleep=lambda _s: None)
        assert (
            wrong["sent"] == 0
            and wrong["failed"] == 1
            and "username or password" in wrong["details"][0]["error"]
        )
        assert len(mail.list_messages(db, status="approved")) == 2, (
            "the rest are untouched"
        )
        right = mail.send_approved(
            db, settings, "right-password", sleep=lambda _s: None
        )
        assert right["sent"] == 2 and len(locked.messages) == 2
        wrong_password = mail.list_messages(db, status="failed")[0]
        assert "username or password" in wrong_password["error"]
        mail.approve(db, [wrong_password["id"]])
        retry = mail.send_approved(
            db, settings, "right-password", sleep=lambda _s: None
        )
        assert retry["sent"] == 0 and retry["failed"] == 1, (
            "a refused recipient fails that message only"
        )
        refused = mail.list_messages(db, status="failed")[0]
        assert (
            "recipient was refused" in refused["error"]
            and "No such user" in refused["error"]
        )
        mail.approve(db, [refused["id"]])
        assert mail.list_messages(db, status="approved")[0]["id"] == refused["id"], (
            "a failed message can be approved again"
        )
    finally:
        locked.stop()
    with pytest.raises(ValueError, match="password"):
        mail.send_approved(db, settings, "", sleep=lambda _s: None)


def test_sending_needs_the_sender_identity_and_a_hosted_build_refuses_internal_mail_servers(
    db, smtp, bridge, monkeypatch
):
    with pytest.raises(ValueError, match="Finish the sender settings"):
        mail.send_approved(db, mail.get_settings(db), "")
    assert mail.settings_problems(
        mail.normalize_settings({"from_name": "Sam"}), False
    ) == [
        "a from address",
        "your mail server",
        "a postal address for the footer (commercial email rules require one)",
    ]
    settings = configure(db, smtp)
    assert (
        mail.send_test(settings, "")["sent_to"] == "sam@seller.test" and smtp.messages
    )
    monkeypatch.setenv("AIRA_LEADS_STRICT_SMTP", "1")
    with pytest.raises(mail.SendError, match="cannot be used here"):
        mail.send_test(settings, "")
    for bad in (
        {"smtp_port": 0},
        {"smtp_security": "carrier-pigeon"},
        {"daily_cap": 500},
        {"from_email": "nope"},
        {"services": ["plumbing"]},
    ):
        with pytest.raises(ValueError):
            mail.normalize_settings(bad)
    assert (
        mail.normalize_settings({"delay_seconds": 0})["delay_seconds"] == mail.MIN_DELAY
    )


def test_a_header_cannot_be_injected_through_a_subject(db, smtp):
    settings = configure(db, smtp)
    message = mail.build_message(
        settings, to="a@b.test", subject="Hello\r\nBcc: victim@evil.test", body="Hi"
    )
    assert "Bcc" not in message and "\n" not in message["Subject"]


def test_the_do_not_contact_list_takes_emails_domains_and_phone_numbers(db):
    for value, kind in (
        ("A@B.test", "email"),
        ("example-corp.com", "domain"),
        ("+1 (512) 555-0142", "phone"),
    ):
        assert mail.add_suppression(db, value, "asked")["kind"] == kind
    lead = {
        "email": "x@example-corp.com",
        "phone": "512-555-0142",
        "website": "https://other.test",
    }
    assert "domain" in mail.suppressed_reason(db, lead)
    assert "phone" in mail.suppressed_reason(db, {"phone": "(512) 555-0142"})
    assert (
        mail.suppressed_reason(
            db, {"email": "fine@gmail.test", "phone": "(512) 555-9999"}
        )
        == ""
    )
    for bad in ("gmail.com", "not a thing", "12"):
        with pytest.raises(ValueError):
            mail.add_suppression(db, bad)
    first = mail.list_suppressions(db)[0]["key"]
    mail.remove_suppression(db, first)
    with pytest.raises(LookupError):
        mail.remove_suppression(db, first)


# ---------------------------------------------------------------------------
# calls: prepared and recorded, never placed
# ---------------------------------------------------------------------------


def test_aira_has_no_way_to_place_a_call():
    import aira.leads.calls as module

    source = open(module.__file__, encoding="utf-8").read().lower()
    for forbidden in (
        "twilio",
        "voip",
        "sip:",
        "robocall",
        "autodial",
        "text-to-speech",
        "websocket",
        "subprocess",
        "requests.",
        "urlopen",
    ):
        assert forbidden not in source.replace("auto-dialer", "").replace(
            "autodialer", ""
        ), forbidden


def test_the_call_window_follows_the_leads_local_time():
    assert calls.lead_timezone({"location": "Austin, TX 78704"}) == "America/Chicago"
    assert (
        calls.lead_timezone({"location": "Seattle, Washington"})
        == "America/Los_Angeles"
    )
    assert calls.lead_timezone({"location": "Miami, FL"}) == "America/New_York"
    assert calls.lead_timezone({"location": "London, UK"}) == ""
    afternoon = datetime(2026, 10, 24, 15, 0, tzinfo=timezone.utc)  # 10:00 in Austin
    night = datetime(
        2026, 10, 25, 3, 0, tzinfo=timezone.utc
    )  # 22:00 the evening before in Austin
    austin = {"location": "Austin, TX"}
    assert calls.calling_window(austin, afternoon)["ok"] is True
    late = calls.calling_window(austin, night)
    assert late["ok"] is False and "8:00 to 21:00" in late["note"]
    unknown = calls.calling_window({"location": "Somewhere"}, afternoon)
    assert unknown["known"] is False and unknown["ok"] is None


def test_dial_info_gives_a_number_to_dial_and_warns_about_the_do_not_contact_list(
    db, smtp
):
    settings = configure(db, smtp, calling_number="+1 512 555 0199")
    lead = add_lead(db)
    info = calls.dial_info(db, lead, settings, NOW)
    assert info["e164"] == "+15125550100" and info["tel_url"] == "tel:+15125550100"
    assert (
        info["google_voice_url"]
        == "https://voice.google.com/u/0/calls?a=nc,%2B15125550100"
    )
    assert info["calling_from"] == "+1 512 555 0199" and info["blocked"] == ""
    assert any("Monosage never dials" in line for line in info["reminders"])
    mail.add_suppression(db, "(512) 555-0100", "asked not to be called")
    assert "do-not-contact" in calls.dial_info(db, lead, settings, NOW)["blocked"]
    assert calls.dial_info(db, {"phone": ""}, settings)["tel_url"] == ""


def test_the_call_script_uses_real_findings_and_falls_back_without_a_model():
    lead = {"kind": "company", "name": "Acme Bakery", "company": "Acme Bakery"}
    script = calls.call_script(
        lead,
        offer="I build websites",
        sender="Sam",
        findings=["No mobile viewport tag"],
        calling_from="+1 512 555 0199",
    )
    assert (
        script["source"] == "template"
        and "no mobile viewport tag" in script["opener"]
        and "Sam" in script["opener"]
    )
    assert (
        len(script["questions"]) == 3
        and script["objections"]
        and "+1 512 555 0199" in script["voicemail"]
    )
    assert "unsubscribe" not in script["opener"].lower()
    prompts: list[str] = []

    def model(prompt):
        prompts.append(prompt)
        return json.dumps(
            {
                "opener": "Hi, it's Sam.",
                "pitch": "Websites.",
                "questions": ["Q1?", "Q2?"],
                "objections": [{"objection": "No", "response": "Fine."}],
                "close": "Call?",
                "voicemail": "Hi.",
            }
        )

    modelled = calls.call_script(
        lead,
        offer="I build websites",
        sender="Sam",
        needs=[("website", "No website listed on Google Maps")],
        model=model,
    )
    assert modelled["source"] == "model" and modelled["opener"] == "Hi, it's Sam."
    assert (
        "Never claim to have reviewed" in prompts[0]
        and "No website listed" in prompts[0]
    )
    assert calls.call_script(lead, model=lambda _p: "not json")["source"] == "template"


def test_logging_a_call_moves_the_lead_and_honours_do_not_call(db):
    lead = add_lead(db)
    log = calls.log_call(db, lead["id"], outcome="no_answer", now=NOW)
    assert (
        log["follow_up_at"].startswith("2026-10-26")
        and store.get_lead(db, lead["id"])["status"] == "contacted"
    )
    calls.log_call(db, lead["id"], outcome="interested", notes="Wants a quote", now=NOW)
    assert store.get_lead(db, lead["id"])["status"] == "qualified"
    calls.log_call(db, lead["id"], outcome="voicemail", now=NOW)
    assert store.get_lead(db, lead["id"])["status"] == "qualified", (
        "a later no-answer never lowers the stage"
    )
    assert [c["outcome"] for c in calls.list_calls(db, lead["id"])][0] == "voicemail"
    other = add_lead(
        db,
        name="Wrong Co",
        company="Wrong Co",
        email="a@wrong.test",
        website="https://wrong.test",
        phone="(512) 555-0177",
    )
    calls.log_call(db, other["id"], outcome="wrong_number", now=NOW)
    assert store.get_lead(db, other["id"])["phone"] == ""
    stop = add_lead(
        db,
        name="Stop Co",
        company="Stop Co",
        email="a@stop.test",
        website="https://stop.test",
        phone="(512) 555-0188",
    )
    calls.log_call(db, stop["id"], outcome="do_not_call", now=NOW)
    assert store.get_lead(db, stop["id"])["status"] == "disqualified"
    assert "phone" in mail.suppressed_reason(
        db, {"phone": "(512) 555-0188"}
    ) and "email" in mail.suppressed_reason(db, {"email": "a@stop.test"})
    with pytest.raises(ValueError):
        calls.log_call(db, lead["id"], outcome="hung_up")
    with pytest.raises(ValueError):
        calls.log_call(db, lead["id"], outcome="callback", follow_up_days=999)


def test_follow_ups_come_due_for_the_latest_call_only(db):
    due = add_lead(
        db,
        name="Due Co",
        company="Due Co",
        email="a@due.test",
        website="https://due.test",
    )
    done = add_lead(
        db,
        name="Done Co",
        company="Done Co",
        email="a@done.test",
        website="https://done.test",
    )
    calls.log_call(db, due["id"], outcome="callback", now=NOW)
    calls.log_call(db, done["id"], outcome="callback", now=NOW)
    calls.log_call(
        db, done["id"], outcome="not_interested", now=NOW
    )  # the latest call asks for no follow-up
    later = datetime(2026, 10, 26, tzinfo=timezone.utc)
    assert [f["lead_name"] for f in calls.follow_ups_due(db, later)] == ["Due Co"]
    assert (
        calls.follow_ups_due(db, datetime(2026, 10, 24, 13, tzinfo=timezone.utc)) == []
    )


# ---------------------------------------------------------------------------
# HTTP routes, through the real server
# ---------------------------------------------------------------------------

OUTREACH_ROUTES = [
    ("GET", "/api/leads/services"),
    ("GET", "/api/leads/outreach/settings"),
    ("POST", "/api/leads/outreach/settings"),
    ("POST", "/api/leads/outreach/test"),
    ("GET", "/api/leads/outbox"),
    ("POST", "/api/leads/outbox/drafts"),
    ("PATCH", f"/api/leads/outbox/{'0' * 32}"),
    ("DELETE", f"/api/leads/outbox/{'0' * 32}"),
    ("POST", "/api/leads/outbox/approve"),
    ("POST", "/api/leads/outbox/reject"),
    ("POST", "/api/leads/outbox/send"),
    ("GET", "/api/leads/suppressions"),
    ("POST", "/api/leads/suppressions"),
    ("POST", "/api/leads/suppressions/remove"),
    ("GET", f"/api/leads/items/{'0' * 32}/dial"),
    ("POST", f"/api/leads/items/{'0' * 32}/call-script"),
    ("GET", f"/api/leads/items/{'0' * 32}/calls"),
    ("POST", f"/api/leads/items/{'0' * 32}/calls"),
    ("GET", "/api/leads/follow-ups"),
]


def test_every_outreach_route_needs_a_session(env):
    anonymous = env.api()
    for method, path in OUTREACH_ROUTES:
        status, body = anonymous.call(
            method, path, {} if method in {"POST", "PATCH"} else None
        )
        assert status == 401 and body == {"error": "Sign in to continue."}, (
            method,
            path,
        )


def test_the_outreach_journey_over_http(env, connected, smtp):
    api = connected
    services = api.get("/api/leads/services")["services"]
    assert {s["id"] for s in services} == set(intent.SERVICE_IDS)
    # sender settings: the password is accepted, stored encrypted and never shown again
    status, body = api.call("POST", "/api/leads/outbox/send", {})
    assert status == 400 and "Finish the sender settings" in body["error"]
    saved = api.post(
        "/api/leads/outreach/settings",
        {
            **SENDER,
            "smtp_port": smtp.port,
            "smtp_username": "",
            "smtp_password": "s3cret-pass",
        },
    )
    assert (
        saved["has_password"] is True
        and saved["problems"] == []
        and "s3cret-pass" not in json.dumps(saved)
    )
    assert "s3cret-pass" not in json.dumps(api.get("/api/leads/outreach/settings"))
    assert "s3cret-pass" not in env.database_bytes().decode("latin-1"), (
        "stored encrypted"
    )
    assert (
        api.post("/api/leads/outreach/test")["sent_to"] == "sam@seller.test"
        and len(smtp.messages) == 1
    )
    # find a need, save it, draft, review, approve, send
    found = api.post(
        "/api/leads/search",
        {
            "source": "google_intent",
            "services": ["website"],
            "where": ["reddit", "web"],
        },
    )
    assert found["count"] == 3 and found["leads"][0]["kind"] == "request"
    api.post(
        "/api/leads/items/import-csv",
        {
            "csv": 'Name,Company,Email,Phone,Website,Location\nAcme Bakery,Acme Bakery,owner@acmebakery.test,(512) 555-0100,acmebakery.test,"Austin, TX 78704"\n'
        },
        expect=201,
    )
    lead = api.get("/api/leads/items?q=Acme")["leads"][0]
    drafts = api.post(
        "/api/leads/outbox/drafts",
        {"lead_ids": [lead["id"]], "services": ["website"]},
        expect=201,
    )
    assert drafts["created"] == 1
    message = drafts["messages"][0]
    api.ok(
        "PATCH",
        f"/api/leads/outbox/{message['id']}",
        {
            "subject": "A website for Acme Bakery",
            "body": "Hi, I build websites for local bakeries. Could I send two ideas?",
        },
    )
    assert api.post("/api/leads/outbox/approve", {"ids": [message["id"]]}) == {
        "approved": 1
    }
    sent = api.post("/api/leads/outbox/send")
    assert sent["sent"] == 1 and sent["remaining_today"] == 19
    last = smtp.parsed(-1)
    assert (
        last["Subject"] == "A website for Acme Bakery"
        and last["To"] == "owner@acmebakery.test"
    )
    assert "12 Example Street" in last.get_payload(decode=True).decode()
    assert (
        api.get("/api/leads/outbox?status=sent")["messages"][0]["lead_name"]
        == "Acme Bakery"
    )
    assert api.get(f"/api/leads/items/{lead['id']}")["lead"]["status"] == "contacted"
    assert api.post("/api/leads/outbox/send")["sent"] == 0
    # do-not-contact
    api.post(
        "/api/leads/suppressions",
        {"value": "acmebakery.test", "reason": "asked to stop"},
        expect=201,
    )
    again = api.post("/api/leads/outbox/drafts", {"lead_ids": [lead["id"]]}, expect=201)
    assert again["created"] == 0 and "do-not-contact" in again["skipped"][0]["reason"]
    entry = api.get("/api/leads/suppressions")["suppressions"][0]
    api.post("/api/leads/suppressions/remove", {"key": entry["key"]})
    assert api.get("/api/leads/suppressions")["suppressions"] == []
    # calls: prepared and logged, never placed
    dial = api.get(f"/api/leads/items/{lead['id']}/dial")["dial"]
    assert (
        dial["tel_url"] == "tel:+15125550100"
        and "voice.google.com" in dial["google_voice_url"]
        and dial["window"]["known"] is True
    )
    script = api.post(
        f"/api/leads/items/{lead['id']}/call-script", {"offer": "Websites that sell"}
    )["script"]
    assert script["opener"] and len(script["questions"]) == 3
    logged = api.post(
        f"/api/leads/items/{lead['id']}/calls",
        {"outcome": "callback", "notes": "Call Friday", "follow_up_days": 0},
        expect=201,
    )
    assert (
        logged["call"]["outcome"] == "callback"
        and logged["lead"]["status"] == "contacted"
    )
    assert (
        api.get(f"/api/leads/items/{lead['id']}/calls")["calls"][0]["label"]
        == "Asked me to call back"
    )
    assert [f["lead_name"] for f in api.get("/api/leads/follow-ups")["follow_ups"]] == [
        "Acme Bakery"
    ]
    assert (
        api.call(
            "POST", f"/api/leads/items/{lead['id']}/calls", {"outcome": "hung_up"}
        )[0]
        == 400
    )
    assert api.call("POST", "/api/leads/outbox/drafts", {"lead_ids": []})[0] == 400
    assert (
        api.call("POST", "/api/leads/outreach/settings", {"daily_cap": 5000})[0] == 400
    )


def test_a_model_that_says_the_caller_is_from_the_business_being_called_is_corrected():
    lead = {"kind": "company", "name": "Acme Bakery", "company": "Acme Bakery"}
    wrong = {
        "opener": "Hello, this is Sam Seller calling from Acme Bakery in Austin.",
        "pitch": "I build websites.",
        "questions": ["Q1?", "Q2?", "Q3?"],
        "objections": [{"objection": "No", "response": "Fine."}],
        "close": "Sam here from Acme Bakery, call me.",
        "voicemail": "Hi, Sam from Acme Bakery.",
    }
    script = calls.call_script(
        lead, sender="Sam Seller", model=lambda _p: json.dumps(wrong)
    )
    assert script["source"] == "model" and script["pitch"] == "I build websites."
    for key in ("opener", "close", "voicemail"):
        assert "from Acme Bakery" not in script[key], key
    assert "Sam Seller" in script["opener"], "the template line names the real caller"
    assert outreach_claims("Hi, I'm Sam at Acme Bakery.", lead) and outreach_claims(
        "Sam here from Acme Bakery", lead
    )
    assert not outreach_claims(
        "I noticed Acme Bakery has no website. This is Sam.", lead
    )
    from aira.leads import outreach

    email = outreach.draft(
        {**lead, "email": "o@acme.test"},
        sender="Sam",
        model=lambda _p: json.dumps(
            {"subject": "Hi", "body": "Hi, this is Sam from Acme Bakery. Want a site?"}
        ),
    )
    assert email["source"] == "template"


def outreach_claims(text, lead):
    from aira.leads import outreach

    return outreach.claims_to_be_from(text, lead)


def test_a_business_is_greeted_politely_even_if_the_model_greets_it_by_name():
    from aira.leads import outreach

    lead = {
        "kind": "company",
        "name": "Acme Bakery",
        "company": "Acme Bakery",
        "email": "o@acme.test",
    }
    out = outreach.draft(
        lead,
        sender="Sam",
        model=lambda _p: json.dumps(
            {
                "subject": "Hi",
                "body": "Hi Acme Bakery,\n\nI noticed you have no website. Would you like a short proposal?",
            }
        ),
    )
    assert out["source"] == "model" and out["body"].startswith("Hi there,")
    person = {
        "kind": "person",
        "name": "Jane Doe",
        "first_name": "Jane",
        "company": "Acme",
    }
    kept = outreach.draft(
        person,
        model=lambda _p: json.dumps(
            {"subject": "Hi", "body": "Hi Jane,\n\nQuick idea."}
        ),
    )
    assert kept["body"].startswith("Hi Jane,")
    prompts: list[str] = []
    outreach.draft(lead, model=lambda p: prompts.append(p) or None)
    assert (
        "never a question about their prices" in prompts[0]
        and "NOT an employee" in prompts[0]
    )
