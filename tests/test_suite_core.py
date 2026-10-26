"""The industry apps' shared core (aira/suite): the kit, public links, the Business hub routes, and both builds.

A test-only public page kind (``studio/test_page``) stands in for an app so the plumbing is exercised without
depending on any one app's code.
"""

from __future__ import annotations

import json
import sqlite3
import urllib.request

import pytest
from cloud_support import Browser, build_org, sign_in

import auto_apply_assistant as local
import test_careers
from aira.suite import kit, public

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


@public.page("studio", "test_page")
def _test_page(connection, link):
    return {"ref": link["ref_id"], "greeting": "hello"}


@public.action("studio", "test_page", "sign_up")
def _test_sign_up(connection, link, payload, visitor):
    name = kit.text(payload.get("name"), "your name", required=True)
    kit.log(connection, "studio", "signup", f"{name} signed up", public=True)
    kit.record_revenue(
        connection,
        "studio",
        source_type="test",
        source_id=name,
        amount_cents=1500,
        label="Test",
    )
    connection.commit()
    return 201, {"ok": True, "name": name}


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    public.set_index(public._MemoryIndex())
    connection = sqlite3.connect(tmp_path / "suite.sqlite3")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


@pytest.fixture
def local_index():
    """The single-user build's index (a hosted test in the same process installs its own)."""
    public.set_index(local._public_link_index)


# ---------------------------------------------------------------------------
# the kit
# ---------------------------------------------------------------------------


def test_money_is_integer_cents_and_formats_per_currency():
    assert kit.cents("12.50") == 1250
    assert kit.cents("1,200") == 120000
    assert kit.cents("$9.99") == 999
    assert kit.cents(7) == 700
    with pytest.raises(ValueError):
        kit.cents("-1")
    with pytest.raises(ValueError):
        kit.cents("abc")
    assert kit.fmt_money(123456, "USD") == "$1,234.56"
    assert kit.fmt_money(1250, "INR") == "₹12.50"
    assert kit.fmt_money(150000, "JPY") == "¥1,500"
    assert kit.percent_of(10000, 750) == 750


def test_validation_gives_sentences_a_person_can_act_on():
    assert kit.text("  a   b ", "a name") == "a b"
    with pytest.raises(ValueError, match="Enter a name"):
        kit.text("", "a name", required=True)
    with pytest.raises(ValueError, match="not an email"):
        kit.email("nope")
    assert kit.day("2026-10-25") == "2026-10-25"
    with pytest.raises(ValueError):
        kit.day("25/10/2026")
    assert kit.moment("2026-10-25T09:30") == "2026-10-25T09:30:00Z"
    assert kit.choice("Paid", "status", ("draft", "paid")) == "paid"
    assert kit.tags("VIP, vip, new") == ["vip", "new"]
    with pytest.raises(ValueError):
        kit.url("javascript:alert(1)")


def test_business_profile_and_csv_are_safe(db):
    saved = kit.save_business(
        db, {"name": "Glow Studio", "currency": "eur", "tax_rate_bp": 2000}
    )
    assert saved["currency"] == "EUR" and saved["tax_rate_bp"] == 2000
    assert kit.public_business(db)["name"] == "Glow Studio"
    assert "tax_rate_bp" not in kit.public_business(db)
    with pytest.raises(ValueError):
        kit.save_business(db, {"accent": "red"})
    data = kit.csv_bytes([{"a": "=HYPERLINK(1)", "b": "-12.5"}], ("a", "b")).decode()
    assert "'=HYPERLINK" in data and ",-12.5" in data


def test_revenue_is_counted_once_and_voids_stop_counting(db):
    kit.record_revenue(
        db, "studio", source_type="invoice", source_id="i1", amount_cents=5000
    )
    kit.record_revenue(
        db, "studio", source_type="invoice", source_id="i1", amount_cents=5000
    )
    kit.record_revenue(
        db, "shop", source_type="order", source_id="o1", amount_cents=2500
    )
    summary = kit.revenue(db)
    assert summary["total_cents"] == 7500 and summary["payments"] == 2
    assert summary["by_app"] == {"studio": 5000, "shop": 2500}
    assert summary["this_month_cents"] == 7500
    kit.void_revenue(db, "shop", "order", "o1")
    assert kit.revenue(db)["total_cents"] == 5000
    assert len(kit.revenue_lines(db)) == 2


def test_the_outbox_waits_for_approval_unless_the_kind_is_automatic(db):
    assert (
        kit.queue_email(db, "bookings", "reminder", to="", subject="x", body="y")
        is None
    )
    held = kit.queue_email(
        db,
        "bookings",
        "reminder",
        to="a@example.com",
        subject="Tomorrow",
        body="See you",
    )
    assert held["status"] == "pending"
    kit.save_email_settings(
        db,
        {
            "from_email": "me@biz.example",
            "smtp_host": "smtp.example.com",
            "auto_send": ["ticket"],
        },
    )
    automatic = kit.queue_email(
        db, "events", "ticket", to="b@example.com", subject="Ticket", body="Your ticket"
    )
    assert automatic["status"] == "approved"
    sent = []
    result = kit.send_outbox(
        db, "pw", sender=lambda settings, password, message: sent.append(message["To"])
    )
    assert result["sent"] == 1 and sent == ["b@example.com"]
    assert kit.set_outbox_status(db, [held["id"]], "approved") == 1

    def refuse(settings, password, message):
        raise RuntimeError("mailbox full")

    result = kit.send_outbox(db, "pw", sender=refuse)
    assert result["failed"] == 1
    assert kit.outbox_counts(db)["failed"] == 1
    edited = kit.edit_outbox(db, held["id"], {"subject": "Tomorrow at 10"})
    assert edited["subject"] == "Tomorrow at 10"


def test_the_writing_model_falls_back_to_the_template(db):
    from aira import suite

    class Model:
        def __init__(self, reply):
            self.reply = reply

        def model(self, prompt):
            return self.reply

    previous = suite.gateway()
    try:
        suite.set_gateway(Model(None))
        assert kit.draft("x", {"title": "T"})["source"] == "template"
        suite.set_gateway(Model(json.dumps({"title": "Written"})))
        assert kit.draft("x", {"title": "T"}) == {"title": "Written", "source": "model"}
        suite.set_gateway(Model(json.dumps({"title": 3})))
        assert kit.draft("x", {"title": "T"})["source"] == "template"
    finally:
        suite.set_gateway(previous)


def test_public_links_are_stable_revocable_and_count_use(db):
    first = public.create_link(db, "studio", "test_page", "ref1", title="Demo")
    assert first["path"].startswith("/p/") and len(first["token"]) >= 40
    assert (
        public.create_link(db, "studio", "test_page", "ref1")["token"] == first["token"]
    )
    with pytest.raises(ValueError):
        public.create_link(db, "studio", "no_such_kind", "ref1")
    page = public.view(db, first["token"])
    assert page["page"] == {"ref": "ref1", "greeting": "hello"} and page["actions"] == [
        "sign_up"
    ]
    status, data = public.act(
        db, first["token"], "sign_up", {"name": "Ana"}, {"client": "x"}
    )
    assert status == 201 and data["name"] == "Ana"
    with pytest.raises(LookupError):
        public.act(db, first["token"], "delete_everything", {}, {})
    link = public.link_for(db, "studio", "test_page", "ref1")
    assert link["views"] == 1 and link["actions"] == 1
    assert public.revoke(db, "studio", "test_page", "ref1") == 1
    with pytest.raises(LookupError):
        public.view(db, first["token"])
    assert (
        public.create_link(db, "studio", "test_page", "ref1")["token"] != first["token"]
    )


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def _anonymous(env, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    request.add_header("X-Requested-With", "aira")
    request.add_header("Origin", env.base)
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return response.status, response.read(), response.headers
    except urllib.error.HTTPError as error:
        return error.code, error.read(), error.headers


def _link_in_local(env, ref="ref1"):
    user = env.user()
    local._bind_user(user)
    try:
        with local._connect() as connection:
            return public.create_link(
                connection, "studio", "test_page", ref, title="Demo"
            )
    finally:
        local._unbind_user()


def test_the_local_build_serves_the_hub_and_public_links(env, api, local_index):
    kit.forget_schema()
    home = api.get("/api/suite/home")
    assert home["revenue"]["total_cents"] == 0 and home["profile_complete"] is False
    by_id = {app["id"]: app for app in home["apps"]}
    assert set(by_id) >= {"studio", "marketing", "service", "commerce", "experiences"}
    # Commerce is Shop and Bookings: the hub lists each section under the app
    assert [section["id"] for section in by_id["commerce"]["sections"]] == [
        "shop",
        "bookings",
    ]
    assert by_id["commerce"]["home"] == "shop"
    api.post(
        "/api/suite/business",
        {"name": "Glow Studio", "email": "hi@glow.example", "accent": "#aa3366"},
    )
    assert api.get("/api/suite/home")["profile_complete"] is True

    link = _link_in_local(env)
    status, body, headers = _anonymous(env, "GET", link["path"])
    assert (
        status == 200
        and b"/static/public.js" in body
        and "text/html" in headers["Content-Type"]
    )
    status, body, _ = _anonymous(env, "GET", f"/api/public/{link['token']}")
    page = json.loads(body)
    assert (
        status == 200
        and page["business"]["name"] == "Glow Studio"
        and page["page"]["greeting"] == "hello"
    )
    assert "tax_rate_bp" not in page["business"]
    status, body, _ = _anonymous(
        env, "POST", f"/api/public/{link['token']}/sign_up", {"name": "Ana"}
    )
    assert status == 201, body
    status, body, _ = _anonymous(
        env, "POST", f"/api/public/{link['token']}/sign_up", {}
    )
    assert status == 400 and b"Enter your name" in body
    home = api.get("/api/suite/home")
    assert home["revenue"]["total_cents"] == 1500
    assert home["activity"][0]["public"] is True
    # An unknown token is a 404, without a session or with one.
    assert _anonymous(env, "GET", "/api/public/" + "x" * 43)[0] == 404
    assert api.call("GET", "/api/public/" + "x" * 43)[0] == 404


def test_hub_routes_need_a_session_in_the_local_build(env):
    status, _, _ = _anonymous(env, "GET", "/api/suite/home")
    assert status == 401


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def _link_in_org(cloud, org_id, ref="ref1"):
    with cloud.workspaces.bind_org_api(org_id):
        with local._connect() as connection:
            return public.create_link(
                connection, "studio", "test_page", ref, title="Demo"
            )


def test_hosted_hub_is_for_members_who_may_use_the_apps(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    for who, expected in (
        ("owner", 200),
        ("admin", 200),
        ("coach", 200),
        ("auditor", 200),
        ("candidate", 403),
        ("billing", 403),
    ):
        status, _ = sign_in(cloud, getattr(org, who)).call("GET", "/api/suite/home")
        assert status == expected, who
    auditor = sign_in(cloud, org.auditor)
    assert auditor.call("POST", "/api/suite/business", {"name": "X"})[0] == 403
    owner = sign_in(cloud, org.owner)
    status, saved = owner.call("POST", "/api/suite/business", {"name": "Acme Agency"})
    assert status == 200 and saved["business"]["name"] == "Acme Agency"


def test_hosted_public_links_need_the_app_and_an_active_organization(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    owner = sign_in(cloud, org.owner)
    owner.call("POST", "/api/suite/business", {"name": "Acme Agency"})
    link = _link_in_org(cloud, org.org_id)
    visitor = Browser(cloud)
    # Team does not include Studio: the link is the same 404 as an unknown one until the app is turned on.
    assert visitor.call("GET", f"/api/public/{link['token']}")[0] == 404
    cloud.billing.subscribe_app(org.owner, "studio")
    status, page = visitor.call("GET", f"/api/public/{link['token']}")
    assert status == 200 and page["business"]["name"] == "Acme Agency"
    status, data = visitor.call(
        "POST", f"/api/public/{link['token']}/sign_up", {"name": "Ana"}
    )
    assert status == 201, data
    status, page_html = visitor.call("GET", link["path"], raw=True)
    assert status == 200
    status, home = owner.call("GET", "/api/suite/home")
    assert home["revenue"]["total_cents"] == 1500
    assert visitor.call("GET", "/api/public/" + "y" * 43)[0] == 404
    # Another organization's members never see this organization's links or numbers.
    other = build_org(cloud, "Other Org", slug_email="other.test")
    status, home = sign_in(cloud, other.owner).call("GET", "/api/suite/home")
    assert status == 200 and home["revenue"]["total_cents"] == 0


def test_the_monthly_goal_shows_progress(env, api, local_index):
    kit.forget_schema()
    assert api.get("/api/suite/home")["goal"] == {
        "monthly_cents": 0,
        "progress_percent": None,
        "remaining_cents": None,
    }
    goal = api.post("/api/suite/goal", {"monthly_cents": 6000})["goal"]
    assert goal["monthly_cents"] == 6000 and goal["progress_percent"] == 0
    link = _link_in_local(env, "goal")
    assert (
        _anonymous(env, "POST", f"/api/public/{link['token']}/sign_up", {"name": "Bo"})[
            0
        ]
        == 201
    )
    goal = api.get("/api/suite/home")["goal"]
    assert goal["progress_percent"] == 25 and goal["remaining_cents"] == 4500
    assert api.call("POST", "/api/suite/goal", {"monthly_cents": -5})[0] == 400


def test_email_settings_keep_the_password_secret(env, api, local_index):
    kit.forget_schema()
    view = api.post(
        "/api/suite/email",
        {
            "from_email": "me@glow.example",
            "smtp_host": "smtp.gmail.com",
            "smtp_port": 587,
            "smtp_password": "app-pass-1234",
        },
    )
    assert view["has_password"] is True and view["ready"] is True
    assert "app-pass-1234" not in json.dumps(view)
    assert "app-pass-1234" not in json.dumps(api.get("/api/suite/email"))
    assert (
        api.post("/api/suite/email", {"clear_password": True})["has_password"] is False
    )
