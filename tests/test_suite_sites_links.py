"""Sites builds with the business's own pages (review section 4): its contact section goes to a Marketing contact
form, so messages arrive as leads, and "Book" and "Shop" buttons get the business's real public links."""

# ruff: noqa: F811
from __future__ import annotations

import re
import sqlite3

import pytest

import test_careers
from aira import suite
from aira.suite import kit, public
from aira.suite.marketing import service as marketing
from aira.suite.marketing import store as marketing_store
from aira.suite.sites import designer, generator, service

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    suite.load_apps()
    connection = sqlite3.connect(tmp_path / "sites.sqlite3")
    connection.row_factory = sqlite3.Row
    marketing_store.ensure(connection)
    yield connection
    connection.close()


def test_the_prompt_lists_the_business_pages_and_never_asks_for_a_fake_form():
    assert (
        "mailto:)" not in generator.SYSTEM and "Never fake sending" in generator.SYSTEM
    )
    prompt = generator.user_prompt(
        "A bakery website",
        files={},
        links=[
            {
                "kind": "form",
                "label": "Contact form (messages arrive as leads)",
                "title": "",
                "url": "https://glow.example/p/abc",
            },
            {
                "kind": "booking_page",
                "label": "Book an appointment",
                "title": "Haircuts",
                "url": "https://glow.example/p/def",
            },
            {
                "kind": "event",
                "label": "Event tickets",
                "title": "x",
                "url": "javascript:alert(1)",
            },
        ],
        contact={
            "name": "Glow",
            "email": "hello@glow.example",
            "phone": "",
            "address": "",
        },
    )
    assert (
        "- Contact form (messages arrive as leads): https://glow.example/p/abc"
        in prompt
    )
    assert "- Book an appointment: Haircuts: https://glow.example/p/def" in prompt
    assert "javascript:" not in prompt  # only web addresses are passed on
    assert "- Email: hello@glow.example" in prompt and "Phone" not in prompt
    assert prompt.index("https://glow.example/p/abc") < prompt.index(
        "Build this website:"
    )


def test_a_build_gets_a_contact_form_and_the_live_links_of_the_other_apps(db):
    kit.save_business(db, {"name": "Glow", "email": "hello@glow.example"})
    public.create_link(db, "shop", "storefront", "shop", title="Glow shop")
    public.create_link(db, "events", "event", "e1", title="Open day")
    revoked = public.create_link(db, "bookings", "booking_page", "b1", title="Old")
    public.revoke(db, "bookings", "booking_page", "b1")
    links = service.business_links(db, {"base_url": "http://127.0.0.1:8765"})
    by_kind = {link["kind"]: link for link in links}
    assert list(by_kind)[0] == "form"
    form = marketing.website_form(db)
    assert by_kind["form"]["url"] == f"http://127.0.0.1:8765{form['public']['path']}"
    assert by_kind["storefront"]["url"].endswith(
        public.link_for(db, "shop", "storefront", "shop")["path"]
    )
    assert by_kind["event"]["title"] == "Open day"
    assert "booking_page" not in by_kind and revoked["token"] not in str(links)
    # the same form is used for every build; a deleted one is replaced
    assert marketing.website_form(db)["id"] == form["id"]
    marketing.delete_form(db, form["id"])
    assert marketing.website_form(db)["id"] != form["id"]


def test_a_visitor_using_the_sites_contact_form_becomes_a_lead(db, monkeypatch):
    monkeypatch.setattr(kit, "FORM_MIN_SECONDS", 0)
    form = marketing.website_form(db)
    token = form["public"]["token"]
    page = public.view(db, token)["page"]
    status, sent = public.act(
        db,
        token,
        "submit",
        {
            "name": "Kim",
            "email": "kim@example.com",
            "message": "Do you deliver?",
            "started": page["started"],
        },
        {"client": "x", "base_url": "http://127.0.0.1:8765"},
    )
    assert status == 201 and sent["stored"] is True
    [lead] = marketing.list_submissions(db)
    assert lead["message"] == "Do you deliver?" and lead["form_id"] == form["id"]


def test_the_offline_designer_links_its_contact_section_to_the_form():
    spec = designer.spec_from_prompt("A website for Pulse gym with a contact form")
    spec["contact_url"] = "https://glow.example/p/abcdef"
    page = designer.render(spec)["index.html"]
    contact = re.search(r'<section id="contact".*?</section>', page, re.S).group(0)
    assert 'href="https://glow.example/p/abcdef"' in contact and "<form" not in contact
    plain = designer.render(
        designer.spec_from_prompt("A website for Pulse gym with a contact form")
    )
    assert '<form class="card contact-form' in plain["index.html"]


def test_the_local_server_builds_with_the_contact_form(env, api):
    kit.forget_schema()
    api.post("/api/suite/business", {"name": "Pulse", "email": "hi@pulse.example"})
    project = api.post(
        "/api/sites/projects",
        {"name": "Pulse", "prompt": "A website for Pulse gym with a contact form"},
        expect=201,
    )["project"]
    built = api.post(
        f"/api/sites/projects/{project['id']}/generate",
        {
            "prompt": "A website for Pulse gym with a contact form",
            "provider": "builtin",
        },
    )
    assert built["run"]["status"] == "done"
    forms = api.get("/api/marketing/forms")["forms"]
    [contact] = [f for f in forms if f["title"] == "Contact us"]
    version = api.get(
        f"/api/sites/projects/{project['id']}/versions/{built['version']}"
    )["version"]
    assert contact["public"]["path"] in version["files"]["index.html"]
