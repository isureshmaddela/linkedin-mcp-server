"""Aira Leads for services in a real browser: finding people who need web, marketing, content or ads help, and
reaching them.

The journeys cover the Find tab (the services you offer, people asking for them, the gap filter), the Leads tab and
its drawer (what a lead needs, the website check, the call panel, the email block, follow-ups) and the Outreach tab
(sender settings, writing, editing, approving and really sending an email to a stand-in mail server, the
do-not-contact list). They fail on any console error. Screenshots go to ``LEADS_SHOTS`` for a human to look at.

What the screen must never do is also pinned here: there is no dial button, only a link that opens Google Voice for
a person to press Call, and nothing is sent until a person approves that message.
"""

# ruff: noqa: F811
from __future__ import annotations

import json
import re

import pytest

pytest.importorskip("patchright.sync_api")

from smtp_stub import SmtpStub  # noqa: E402
from test_careers import PASSWORD, env, stub  # noqa: E402, F401
from test_leads import site  # noqa: E402, F401
from test_leads_ui import browser, go, shot, signed_in_page, until  # noqa: E402, F401

HEADERS = lambda env: {  # noqa: E731
    "Content-Type": "application/json",
    "X-Requested-With": "aira",
    "Origin": env.base,
}

NASTY = "<b>Bold</b> & Co"

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


def post(context, env, path, body=None, *, expect=200):
    response = context.request.post(
        env.base + path, data=json.dumps(body or {}), headers=HEADERS(env)
    )
    assert response.status == expect, (path, response.status, response.text())
    return response.json()


def get(context, env, path):
    response = context.request.get(env.base + path)
    assert response.status == 200, (path, response.status, response.text())
    return response.json()


def seed_leads(context, env, site):
    """Five leads: one with a site to check, one with no website, one with markup in its name, one in the EU, one with no email."""
    leads = [
        {
            "kind": "company",
            "name": "Acme Bakery",
            "company": "Acme Bakery",
            "email": "owner@acmebakery.test",
            "phone": "(512) 555-0100",
            "website": site.url,
            "location": "Austin, TX 78704",
            "signals": [{"type": "thin_reviews", "text": "Only 3 Google reviews"}],
        },
        {
            "kind": "company",
            "name": "Bright Dental",
            "company": "Bright Dental",
            "email": "hello@brightdental.test",
            "phone": "(512) 555-0111",
            "location": "Austin, TX 78701",
            "signals": [
                {"type": "no_website", "text": "No website on its Google listing"},
                {"type": "thin_reviews", "text": "Only 4 Google reviews"},
            ],
        },
        {
            "kind": "company",
            "name": NASTY,
            "company": NASTY,
            "email": "info@boldco.test",
            "location": "Dallas, TX",
        },
        {
            "kind": "company",
            "name": "Berlin Cafe",
            "company": "Berlin Cafe",
            "email": "kaffee@berlincafe.test",
            "location": "Berlin, Germany",
        },
        {
            "kind": "company",
            "name": "No Email Plumbing",
            "company": "No Email Plumbing",
            "phone": "(512) 555-0122",
            "location": "Austin, TX",
        },
    ]
    return post(context, env, "/api/leads/items", {"leads": leads}, expect=201)


def sender_settings(context, env, smtp, **extra):
    return post(
        context,
        env,
        "/api/leads/outreach/settings",
        {**SENDER, "smtp_port": smtp.port, "smtp_password": "pw-12345", **extra},
    )


def row(page, text):
    return page.locator(".ld-leads tbody tr", has_text=text)


def open_lead(page, env, name):
    go(page, env, "leads?tab=leads")
    page.wait_for_selector(".ld-leads tbody tr")
    row(page, name).locator(".ld-open").click()
    page.wait_for_selector(".ld-drawer")


def test_find_services_people_asking_and_gaps(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "leads?tab=find")
    page.wait_for_selector("#ldOfferedTitle")
    labels = page.locator("#ldOfferedTitle").inner_text()
    assert labels == "Services I offer"
    boxes = page.locator(".ld-offer input")
    assert boxes.count() == 5
    page.locator("#ldOffer-website").check()
    page.locator("#ldOffer-ads").check()
    until(
        page,
        lambda: "Saved" in page.locator("#ldOfferedSaved").inner_text(),
        what="the services to be saved",
    )
    assert get(context, env, "/api/leads/icp")["icp"]["services"] == ["website", "ads"]

    # ---- people asking for what you offer ----
    page.click("#ldSource-requests")
    page.wait_for_selector("#ldOfferedLine")
    line = page.locator("#ldOfferedLine").inner_text()
    assert "Website design" in line and "Paid ads" in line
    assert page.locator("#ldWhere-reddit").is_checked()
    assert page.locator("#ldWhere-web").is_checked()
    assert not page.locator("#ldWhere-quora").is_checked()
    assert "Aira never replies for you" in page.locator("#ldRequestNote").inner_text()
    shot(page, "o-01-find-requests-form")
    page.click("#ldSearchBtn")
    dialog = page.locator(".modal")
    dialog.wait_for()
    assert "SerpApi search" in dialog.inner_text()
    assert env.stub.search_calls == 0, "the dry run spends nothing"
    dialog.get_by_role("button", name="Search").click()
    page.wait_for_selector("#ldResults .ld-requests tbody tr")
    assert page.locator("#ldResults .ld-requests tbody tr").count() == 3
    table = page.locator("#ldResults .ld-requests").inner_text()
    assert "Budget mentioned" in table and "Urgent" in table
    assert "Looking for a web designer for my bakery" in table
    assert (
        "Aira never posts or messages for you"
        in page.locator("#ldResults").inner_text()
    )
    link = page.locator("#ldResults .ld-requests a[href*='reddit.com']").first
    assert link.get_attribute("rel") == "noopener noreferrer"
    assert link.get_attribute("target") == "_blank"
    shot(page, "o-02-find-requests-results")
    page.get_by_role("button", name=re.compile(r"Save all new")).click()
    until(
        page,
        lambda: page.locator("#ldResults .ld-dup").count() == 3,
        what="the three posts to show as saved",
    )

    # ---- they show up under Leads as public requests, and open without the contact panels ----
    page.click(".ld-subnav a:has-text('Leads')")
    page.wait_for_selector(".ld-leads tbody tr")
    page.select_option("#ldFilterKind", "request")
    until(
        page,
        lambda: page.locator(".ld-leads tbody tr").count() == 3,
        what="the request filter",
    )
    page.locator(".ld-open").first.click()
    page.wait_for_selector(".ld-drawer")
    drawer = page.locator(".ld-drawer").inner_text()
    assert "The post" in drawer and "Aira has no way to post, email or call" in drawer
    assert page.locator("#ldCall").count() == 0, "a post has no phone to call"
    assert page.locator("#ldEmail").count() == 0
    shot(page, "o-03-request-drawer")
    page.keyboard.press("Escape")
    page.wait_for_selector(".ld-drawer", state="detached")

    # ---- the services also live in Settings, next to the ideal customer profile ----
    page.click(".ld-subnav a:has-text('Settings')")
    page.wait_for_selector("#ldIcpServices")
    assert page.locator("#ldicp-service-website").is_checked()
    assert page.locator("#ldicp-service-ads").is_checked()
    assert not page.locator("#ldicp-service-content").is_checked()
    assert not problems, problems
    context.close()


def test_a_maps_search_can_be_narrowed_to_businesses_with_a_gap(browser, env):
    context, page, problems = signed_in_page(browser, env)
    go(page, env, "leads?tab=find&source=maps&query=coffee+roasters&location=New+York")
    page.wait_for_selector("#ldSearchForm")
    page.click("#ldSearchBtn")
    page.locator(".modal").get_by_role("button", name="Search").click()
    page.wait_for_selector("#ldResults .ld-table tbody tr")
    everything = page.locator("#ldResults .ld-table tbody tr").count()
    assert everything == 4
    toggle = page.locator(".ld-gaptoggle input")
    assert toggle.count() == 1
    assert "gap" in page.locator(".ld-gaptoggle").inner_text()
    toggle.check()
    until(
        page,
        lambda: (
            page.locator("#ldResults .ld-table tbody tr").count() != everything
            or page.locator(".ld-nogap").count() == 1
        ),
        what="the gap filter to change the list",
    )
    narrowed = page.locator("#ldResults .ld-table tbody tr").count()
    assert narrowed < everything
    if narrowed == 0:
        assert (
            "None of these businesses shows a gap"
            in page.locator(".ld-nogap").inner_text()
        )
    page.locator(".ld-gaptoggle input").uncheck()
    until(
        page,
        lambda: page.locator("#ldResults .ld-table tbody tr").count() == everything,
        what="the filter to clear",
    )
    assert not problems, problems
    context.close()


def test_the_outreach_journey_from_sender_settings_to_a_sent_email(
    browser, env, site, smtp
):
    context, page, problems = signed_in_page(browser, env)
    post(context, env, "/api/leads/icp", {"services": ["website", "ads"]})
    seed_leads(context, env, site)

    # ---- nothing can be sent until the sender is set up ----
    go(page, env, "leads?tab=outreach")
    page.wait_for_selector("#ldOutbox")
    assert "Setup" in page.locator("#ldStatSetup").inner_text()
    assert page.locator("#ldProblems").inner_text().strip() != ""
    assert page.get_by_role("button", name=re.compile(r"Send approved")).is_disabled()
    assert (
        "Nothing is sent until you approve it"
        in page.locator("#ldOutboxRule").inner_text()
    )
    assert "The outbox is empty" in page.locator("#ldOutbox").inner_text()
    page.click("#ldSettingsTest")
    until(
        page,
        lambda: page.locator("#ldSettingsResult").inner_text().strip() != "",
        what="the test email to explain what is missing",
    )
    assert "Still needed" in page.locator("#ldSettingsResult").inner_text()
    assert len(smtp.messages) == 0
    shot(page, "o-10-outreach-empty")

    # ---- fill in the sender settings and send a test email ----
    page.fill("#ldset-from_name", SENDER["from_name"])
    page.fill("#ldset-from_email", SENDER["from_email"])
    page.fill("#ldset-smtp_host", "127.0.0.1")
    page.fill("#ldset-smtp_port", str(smtp.port))
    page.select_option("#ldset-smtp_security", "none")
    page.fill("#ldset-smtp_password", "pw-12345")
    page.fill("#ldset-postal_address", SENDER["postal_address"])
    page.fill("#ldset-offer", SENDER["offer"])
    page.fill("#ldset-delay_seconds", "2")
    page.click("#ldSettingsTest")
    until(
        page,
        lambda: (
            "Test email sent to sam@seller.test"
            in page.locator("#ldSettingsResult").inner_text()
        ),
        what="the test email",
        timeout=20,
    )
    assert len(smtp.messages) == 1
    assert page.locator("#ldset-smtp_password").input_value() == "", (
        "the password is never shown again"
    )
    assert "Saved" in (
        page.locator("#ldset-smtp_password").get_attribute("placeholder") or ""
    )
    page.click("#ldSettingsSave")
    until(
        page,
        lambda: page.locator("#ldSettingsResult").inner_text().strip() == "Saved.",
        what="the settings to save",
    )
    assert "Ready" in page.locator("#ldStatSetup").inner_text()
    assert "complete" in page.locator("#ldProblems").inner_text().lower()
    assert "pw-12345" not in page.content()

    # ---- write emails from the Leads tab ----
    go(page, env, "leads?tab=leads")
    page.wait_for_selector(".ld-leads tbody tr")
    for name in (
        "Acme Bakery",
        "Bright Dental",
        NASTY,
        "Berlin Cafe",
        "No Email Plumbing",
    ):
        row(page, name).locator("input[type=checkbox]").check()
    page.wait_for_selector(".ld-bulk:not([hidden])")
    assert "5 selected" in page.locator(".ld-bulk").inner_text()
    page.get_by_role("button", name="Write emails").click()
    page.wait_for_selector("#ldDraftResult")
    result = page.locator("#ldDraftResult").inner_text()
    assert "3 drafts written, 2 skipped." in result
    assert "Nothing is sent yet" in result
    skipped = page.locator("#ldDraftResult .ld-skipped").inner_text()
    assert "Berlin Cafe" in skipped and "No Email Plumbing" in skipped
    assert len(smtp.messages) == 1, "writing is not sending"
    shot(page, "o-11-drafts-written")
    page.get_by_role("button", name="Open the Outbox").click()
    page.wait_for_selector("#ldMessages")

    # ---- the outbox: three drafts, rendered as text ----
    cards = page.locator("#ldMessages .ld-msg")
    assert cards.count() == 3
    assert page.locator("#ldStatDrafts strong").inner_text() == "3"
    assert page.locator("#ldMessages b").count() == 0, (
        "a name with markup is shown as text"
    )
    assert NASTY in page.locator("#ldMessages").inner_text()
    acme = page.locator("#ldMessages .ld-msg", has_text="Acme Bakery")
    body = acme.locator(".ld-msg-body").inner_text()
    assert "Acme Bakery" in acme.inner_text() and body.strip()
    assert acme.locator(".ld-chip", has_text="Draft").count() == 1
    assert "Template" in acme.inner_text(), (
        "Ollama is off in tests, so the author is the template"
    )
    shot(page, "o-12-outbox-drafts")

    # edit one: the subject changes and it stays a draft
    acme.get_by_role("button", name="Edit").click()
    page.fill(
        f"#ldMsgSubject-{acme.get_attribute('data-id')}", "A website for Acme Bakery"
    )
    page.fill(
        f"#ldMsgBody-{acme.get_attribute('data-id')}",
        "Hi, I build websites for local bakeries. Could I send two ideas?",
    )
    acme.get_by_role("button", name="Save").click()
    until(
        page,
        lambda: "A website for Acme Bakery" in page.locator("#ldMessages").inner_text(),
        what="the edited subject",
    )
    acme = page.locator("#ldMessages .ld-msg", has_text="Acme Bakery")
    assert acme.locator(".ld-chip", has_text="Draft").count() == 1, (
        "editing keeps it a draft"
    )

    # approve one: it waits for Send, which does not send anything else
    acme.get_by_role("button", name="Approve", exact=True).click()
    until(
        page,
        lambda: page.locator("#ldStatApproved strong").inner_text() == "1",
        what="the approved count",
    )
    assert "1 message approved and waiting" in page.locator("#ldSendState").inner_text()
    assert len(smtp.messages) == 1
    page.get_by_role("button", name=re.compile(r"Send approved")).click()
    page.wait_for_selector("#ldSendResult")
    until(
        page,
        lambda: "Sent 1" in page.locator("#ldSendResult").inner_text(),
        what="the send result",
        timeout=30,
    )
    assert len(smtp.messages) == 2
    sent = smtp.parsed(-1)
    assert (
        sent["Subject"] == "A website for Acme Bakery"
        and sent["To"] == "owner@acmebakery.test"
    )
    text = sent.get_payload(decode=True).decode()
    assert "12 Example Street" in text, "the sender's postal address is in the footer"
    assert (
        "unsubscribe" in text.lower()
        or "opt out" in text.lower()
        or "not interested" in text.lower()
    )
    assert page.locator("#ldStatSent strong").inner_text() == "1"
    assert "19" in page.locator("#ldStatLeft strong").inner_text()
    shot(page, "o-13-sent")

    # the two drafts nobody approved stay drafts: reject one, delete the other
    others = page.locator("#ldMessages .ld-msg.draft")
    assert others.count() == 2
    others.first.get_by_role("button", name="Reject").click()
    until(
        page,
        lambda: page.locator("#ldMessages .ld-msg.rejected").count() == 1,
        what="the rejection",
    )
    page.locator("#ldMessages .ld-msg.draft").first.get_by_role(
        "button", name=re.compile("Delete")
    ).click()
    until(
        page,
        lambda: page.locator("#ldMessages .ld-msg.draft").count() == 0,
        what="the deletion",
    )
    page.select_option("#ldOutboxStatus", "sent")
    until(
        page,
        lambda: page.locator("#ldMessages .ld-msg").count() == 1,
        what="the sent filter",
    )

    # a person who asks to stop goes on the do-not-contact list from the sent message
    page.locator("#ldMessages .ld-msg").first.get_by_role(
        "button", name="Do not contact"
    ).click()
    until(
        page,
        lambda: page.locator("#ldDncTable").count() == 1,
        what="the do-not-contact table",
    )
    assert "owner@acmebakery.test" in page.locator("#ldDncTable").inner_text()

    # the list takes a domain, rejects an empty entry and lets an entry go
    page.click("#ldDncAdd")
    until(
        page,
        lambda: page.locator("#ldDncError").inner_text().strip() != "",
        what="an error for the empty entry",
    )
    page.fill("#ldDncValue", "spammy-domain.test")
    page.fill("#ldDncReason", "asked us to stop")
    page.click("#ldDncAdd")
    until(
        page,
        lambda: page.locator("#ldDncTable tbody tr").count() == 2,
        what="the second entry",
    )
    assert "spammy-domain.test" in page.locator("#ldDncTable").inner_text()
    shot(page, "o-14-dnc")
    page.locator("#ldDncTable tbody tr", has_text="spammy-domain.test").get_by_role(
        "button", name="Remove"
    ).click()
    until(
        page,
        lambda: page.locator("#ldDncTable tbody tr").count() == 1,
        what="the removal",
    )
    assert "Not legal advice" in page.locator("#ldCompliance").inner_text()
    # the one expected console error is the browser noting the 400 for the empty do-not-contact entry
    unexpected = [p for p in problems if "400 (Bad Request)" not in p]
    assert not unexpected and len(problems) <= 1, problems
    context.close()


def test_the_drawer_prepares_a_call_but_never_places_one(browser, env, site, smtp):
    context, page, problems = signed_in_page(browser, env)
    post(context, env, "/api/leads/icp", {"services": ["website", "ads"]})
    seed_leads(context, env, site)
    sender_settings(context, env, smtp)

    # ---- a business with no website: what it needs, nothing to check, a number to call ----
    open_lead(page, env, "Bright Dental")
    needs = page.locator("#ldNeeds").inner_text()
    assert (
        "Website design and development" in needs
        and "No website on its Google listing" in needs
    )
    assert "Paid ads" not in needs, "only services the lead's own signals call for"
    assert "no website to check" in page.locator("#ldAudit").inner_text()
    assert (
        page.locator("#ldAudit")
        .get_by_role("button", name="Check the website")
        .is_disabled()
    )
    call = page.locator("#ldCall")
    assert "Aira never dials. You place the call." in call.inner_text()
    page.wait_for_selector("#ldCallNumber")
    assert "555-0111" in page.locator("#ldCallNumber").inner_text()
    voice = call.get_by_role("link", name=re.compile("Open in Google Voice"))
    assert voice.get_attribute("href").startswith("https://voice.google.com")
    assert voice.get_attribute(
        "target"
    ) == "_blank" and "noopener" in voice.get_attribute("rel")
    tel = call.get_by_role("link", name=re.compile("Call with your phone"))
    assert tel.get_attribute("href").startswith("tel:")
    assert page.locator("#ldCallWindow").inner_text().strip() != ""
    assert page.locator("#ldCall li").count() >= 3, "reminders before calling"
    buttons = call.locator("button").all_inner_texts()
    assert not any(re.search(r"\b(dial|auto|robo)", b, re.I) for b in buttons), buttons
    shot(page, "o-20-drawer-call")

    # the talk track is written from the lead's facts and can be copied
    page.get_by_role("button", name="Make a talk track").click()
    page.wait_for_selector("#ldScript")
    script = page.locator("#ldScript").inner_text()
    assert (
        "Opener" in script and "Questions to ask" in script and "If they say" in script
    )
    assert "Bright Dental" in script
    assert page.locator("#ldScript .ld-script-part").count() >= 5
    shot(page, "o-21-talk-track")

    # log the call, with a follow-up due today
    page.select_option("#ldCallOutcome", "callback")
    page.fill("#ldCallNotes", "Call back Friday afternoon.")
    page.fill("#ldCallDays", "0")
    page.click("#ldCallLog")
    page.wait_for_selector(".toast.success >> text=Call logged")
    until(
        page,
        lambda: "Asked me to call back" in page.locator("#ldCall").inner_text(),
        what="the call history",
    )
    assert "Call back Friday afternoon." in page.locator("#ldCall").inner_text()
    assert len(smtp.messages) == 0
    page.keyboard.press("Escape")
    page.wait_for_selector(".ld-drawer", state="detached")

    # ---- the follow-up shows on the Leads tab and opens the lead ----
    go(page, env, "leads?tab=leads")
    page.wait_for_selector("#ldFollowBadge")
    assert "Follow-ups due (1)" in page.locator("#ldFollowBadge").inner_text()
    assert page.locator("#ldFollowList").is_hidden()
    page.click("#ldFollowBadge")
    assert page.locator("#ldFollowList").is_visible()
    assert "Bright Dental" in page.locator("#ldFollowList").inner_text()
    shot(page, "o-22-follow-ups")
    page.locator("#ldFollowList").get_by_role("button", name=re.compile("Open")).click()
    page.wait_for_selector(".ld-drawer")
    assert "Bright Dental" in page.locator(".ld-drawer").inner_text()

    # ---- someone who asks not to be called goes on the list, and the number is then blocked ----
    page.select_option("#ldCallOutcome", "do_not_call")
    page.click("#ldCallLog")
    confirm = page.locator(".modal")
    confirm.wait_for()
    assert "do-not-contact list" in confirm.inner_text()
    confirm.get_by_role("button", name="Add and log").click()
    page.wait_for_selector("#ldCallBlocked")
    assert "do-not-contact" in page.locator("#ldCallBlocked").inner_text().lower()
    assert (
        page.locator("#ldCall")
        .get_by_role("link", name=re.compile("Open in Google Voice"))
        .count()
        == 0
    )
    assert page.locator("#ldCall button:has-text('Open in Google Voice')").is_disabled()
    shot(page, "o-23-call-blocked")
    page.keyboard.press("Escape")
    page.wait_for_selector(".ld-drawer", state="detached")

    # ---- a lead with a website: the check lists what is visibly missing ----
    open_lead(page, env, "Acme Bakery")
    page.get_by_role("button", name="Check the website").click()
    page.wait_for_selector("#ldAudit .ld-audit-summary")
    audit = page.locator("#ldAudit").inner_text()
    assert "observations, not a score" in audit
    assert "answered in" in audit
    assert page.locator("#ldAudit .ld-findings li").count() >= 1
    shot(page, "o-24-website-check")

    # ---- the email block: write a draft with the model (the template here), it lands in the outbox ----
    page.get_by_role("button", name="Write email with Ollama").click()
    page.wait_for_selector("#ldDraftResult")
    assert "1 draft written" in page.locator("#ldDraftResult").inner_text()
    page.locator(".modal").get_by_role("button", name="Close").click()
    until(
        page,
        lambda: page.locator("#ldEmail .ld-lead-messages li").count() == 1,
        what="the lead's message list",
    )
    assert "Draft" in page.locator("#ldEmail .ld-lead-messages").inner_text()
    assert len(smtp.messages) == 0, "writing is not sending"
    shot(page, "o-25-email-block")
    assert not problems, problems
    context.close()


@pytest.mark.parametrize(
    ("width", "dark"), [(1366, False), (1366, True), (390, False), (390, True)]
)
def test_outreach_and_the_drawer_fit_every_screen(
    browser, env, site, smtp, width, dark
):
    context, page, problems = signed_in_page(browser, env, width=width, dark=dark)
    post(context, env, "/api/leads/icp", {"services": ["website", "ads"]})
    seeded = seed_leads(context, env, site)
    assert seeded["created"] == 5, seeded
    sender_settings(context, env, smtp)
    ids = [lead["id"] for lead in get(context, env, "/api/leads/items")["leads"]]
    drafts = post(
        context, env, "/api/leads/outbox/drafts", {"lead_ids": ids}, expect=201
    )
    assert drafts["created"] == 3
    post(
        context,
        env,
        "/api/leads/outbox/approve",
        {"ids": [drafts["messages"][0]["id"]]},
    )
    tag = f"{'dark' if dark else 'light'}-{width}"

    go(page, env, "leads?tab=outreach")
    page.wait_for_selector("#ldMessages")
    page.wait_for_timeout(250)
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the Outreach tab overflows by {overflow}px at {width}px"
    shot(page, f"o-v-{tag}-outreach")

    open_lead(page, env, "Bright Dental")
    page.wait_for_selector("#ldCallNumber")
    page.get_by_role("button", name="Make a talk track").click()
    page.wait_for_selector("#ldScript")
    overflow = page.evaluate(
        "document.documentElement.scrollWidth - document.documentElement.clientWidth"
    )
    assert overflow <= 1, f"the drawer overflows by {overflow}px at {width}px"
    inner = page.evaluate(
        "(() => { const d = document.querySelector('.ld-drawer'); return d ? d.scrollWidth - d.clientWidth : 0; })()"
    )
    assert inner <= 1, f"the drawer content overflows its panel by {inner}px"
    shot(page, f"o-v-{tag}-drawer")
    assert not problems, problems
    context.close()


def test_a_screen_reader_can_find_the_parts_of_the_outreach_tab(
    browser, env, site, smtp
):
    context, page, problems = signed_in_page(browser, env)
    seed_leads(context, env, site)
    sender_settings(context, env, smtp)
    go(page, env, "leads?tab=outreach")
    page.wait_for_selector("#ldOutbox")
    for region in ("Outbox", "Sender settings", "Do-not-contact list"):
        assert page.get_by_role("heading", name=region).count() == 1, region
    assert page.get_by_role("navigation", name="On this page").count() == 1
    # every field in the sender form has a visible label or an accessible name
    unnamed = page.evaluate(
        """(() => [...document.querySelectorAll('#ldSenderForm input, #ldSenderForm select, #ldSenderForm textarea')]
            .filter((el) => el.type !== 'checkbox')
            .filter((el) => !(el.labels && el.labels.length) && !el.getAttribute('aria-label'))
            .map((el) => el.id))()"""
    )
    assert unnamed == [], unnamed
    # keyboard: the whole tab can be reached with Tab, and focus is visible on the first button
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement !== document.body")
    assert not problems, problems
    context.close()
