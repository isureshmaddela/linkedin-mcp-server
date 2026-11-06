"""One erasure job (aira/erasure.py, aira/erasers.py): a person erased across every app, accounting rows kept
anonymised, a legal hold on open moderation reports, one failing eraser that does not stop the rest, and a receipt
with nothing about the person."""

from __future__ import annotations

import importlib
import json
import sqlite3
import uuid
from contextlib import closing

import pytest
from test_suite_social_dating import Person as SocialPerson
from test_suite_social_dating import _match

from aira import erasure
from aira.schema import migrate
from aira.suite import kit, life
from aira.suite.social import db as social_db
from aira.suite.social import dating
from aira.suite.social import service as social
from aira.suite.videos import db as videos_db

NAME = "Zelda Quokka"
EMAIL = "zelda.quokka@example.test"
PHONE = "+1 (555) 010-4477"
SECRETS = ("bury me at sea", "my secret worry", "Zelda's diary entry", "zelda@upi")
STORES = (
    "aira.suite.care.store",
    "aira.suite.planner.store",
    "aira.suite.ai.store",
    "aira.suite.learn.store",
    "aira.suite.play.store",
    "aira.suite.news.store",
    "aira.suite.stocks.store",
    "aira.suite.journey.store",
    "aira.suite.family.store",
    "aira.suite.shop.store",
    "aira.suite.bookings.store",
    "aira.suite.marketing.store",
    "aira.suite.service.store",
    "aira.suite.events.store",
    "aira.suite.academy.store",
    "aira.suite.academy.partners",
    "aira.suite.studio.store",
    "aira.notify.store",
    "aira.leads.store",
    "aira.leads.sequences",
    "aira.recruiter_contacts",
    "aira.payments.service",
    "aira.inbound_mail",
    "aira.suite.sites.store",
    "aira.airaapi.careers_tracker",
    "aira.workday_accounts",
    "aira.email_apply",
)


class Req:
    """Just enough of a request for life.current()."""

    def __init__(self, header: str = "", email: str = "olive@home.example") -> None:
        self._header = header
        self.user = {"email": email}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    social_db.forget()
    videos_db.forget()
    connection = sqlite3.connect(tmp_path / "account.sqlite3")
    connection.row_factory = sqlite3.Row
    migrate(connection)  # the account's audit log, Careers, leads and recruiter tables
    life.ensure(connection)
    kit.ensure_outbox(connection)
    for name in STORES:
        module = importlib.import_module(name)
        (getattr(module, "ensure", None) or module.ensure_schema)(connection)
    yield connection
    connection.close()


def put(connection: sqlite3.Connection, table: str, **values) -> str:
    """Insert a row: the values given, '' or 0 for every other column that needs one, a new id."""
    columns = connection.execute(f"PRAGMA table_info({table})").fetchall()
    assert columns, f"no table {table}"
    row = dict(values)
    for _cid, name, kind, notnull, default, pk in columns:
        if name in row:
            continue
        if name == "id" or (pk and "INT" not in (kind or "").upper()):
            row[name] = uuid.uuid4().hex
        elif notnull and default is None:
            row[name] = (
                0 if any(t in (kind or "").upper() for t in ("INT", "REAL")) else ""
            )
    for key, value in list(row.items()):
        if isinstance(value, (list, dict)):
            row[key] = json.dumps(value)
    connection.execute(
        f"INSERT INTO {table} ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
        tuple(row.values()),
    )
    connection.commit()
    return str(row.get("id", ""))


def mentions(
    connection: sqlite3.Connection, needles, *, skip=()
) -> list[tuple[str, str, str]]:
    """Every (table, column, value) whose text contains one of ``needles`` (any case)."""
    lowered = [n.lower() for n in needles if n]
    hits = []
    for (table,) in connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' AND sql NOT LIKE 'CREATE VIRTUAL%'"
    ).fetchall():
        if table in skip or table.startswith("airaapi_search_text"):
            continue
        for row in connection.execute(f'SELECT * FROM "{table}"').fetchall():
            for column in row.keys():
                value = row[column]
                if isinstance(value, str) and any(n in value.lower() for n in lowered):
                    hits.append((table, column, value[:80]))
    return hits


def household(db):
    owner = life.current(Req(), db)
    life.set_pin(db, owner, owner["id"], "2468")
    owner = life.get(db, owner["id"])
    zelda = life.add(db, owner, {"name": NAME, "email": EMAIL, "age_group": "adult"})
    return owner, zelda


def seed_life(db, owner, zelda) -> dict[str, str]:
    """Zelda's rows in every life app, a few of Olive's beside them, and the household's shared things."""
    p, o = zelda["id"], owner["id"]
    out: dict[str, str] = {}
    # Health: medicines, doses, readings, diary, card, appointments; Olive made Zelda her caregiver
    medicine = put(
        db, "cr_medicines", owner_id=p, name="Metformin", times=["08:00"], days=[]
    )
    put(
        db,
        "cr_doses",
        owner_id=p,
        medicine_id=medicine,
        slot_date="2026-11-01",
        slot_time="08:00",
        status="taken",
    )
    put(db, "cr_readings", owner_id=p, kind="glucose", value1=6.1)
    put(db, "cr_diary", owner_id=p, day="2026-11-01", note="Zelda's diary entry")
    put(
        db,
        "cr_cards",
        owner_id=p,
        allergies="penicillin",
        contacts=[{"name": NAME, "phone": PHONE}],
    )
    put(db, "cr_appointments", owner_id=p, title="Check-up", at="2026-12-01T09:00")
    out["olive_medicine"] = put(
        db, "cr_medicines", owner_id=o, name="Aspirin", times=["09:00"], days=[]
    )
    put(db, "cr_caregivers", owner_id=o, caregiver_id=p, email=EMAIL, name=NAME)
    put(
        db,
        "suite_outbox",
        app="care",
        kind="missed_dose",
        recipient="carer@family.example",
        subject=f"{NAME} has not taken a scheduled medicine",
        body="...",
        ref_type="medicine",
        ref_id=medicine,
        status="pending",
    )
    put(
        db,
        "nt_items",
        person=f"p:{o}",
        category="medicine",
        title=f"{NAME} missed a dose",
        dedupe=f"missed:{medicine}:2026-11-01:08:00:{o}",
    )
    # her own care work: a client, a visit and a statement (an invoice) the books keep
    client = put(
        db,
        "cr_clients",
        owner_id=p,
        name="Carl Client",
        email="carl@clients.example",
        phone="+44 20 7946 0000",
    )
    statement = put(
        db,
        "cr_statements",
        owner_id=p,
        client_id=client,
        number="S-1",
        total_cents=12000,
        currency="GBP",
        status="paid",
        note="paid by Zelda's bank",
    )
    put(
        db,
        "cr_visits",
        owner_id=p,
        client_id=client,
        statement_id=statement,
        day="2026-10-01",
        minutes=120,
        notes="Zelda helped with lunch",
    )
    out["statement"] = statement
    # the life book, wishes and goals
    life_id = put(
        db,
        "jr_lives",
        owner_id=p,
        relation="self",
        name=NAME,
        wishes={"funeral": "bury me at sea"},
    )
    put(db, "jr_records", owner_id=p, life_id=life_id, kind="vaccine", title="Tetanus")
    goal = put(db, "jr_goals", owner_id=p, title="Run 5k")
    put(db, "jr_checkins", goal_id=goal, progress=10, note="first run")
    put(db, "jr_journal", owner_id=p, day="2026-11-02", body="my secret worry")
    # Family
    post = put(db, "fm_posts", author_id=p, author_name=NAME, text="Hello from Zelda")
    put(db, "fm_comments", post_id=post, author_id=o, author_name="Olive", text="Hi!")
    out["olive_post"] = olive_post = put(
        db, "fm_posts", author_id=o, author_name="Olive", text="Dinner at 7"
    )
    put(db, "fm_reactions", post_id=olive_post, who=p, emoji="❤")
    room = put(db, "fm_rooms", kind="direct", member_a=o, member_b=p)
    put(db, "fm_messages", room_id=room, author_id=p, text="see you soon")
    put(db, "fm_wishes", owner_id=p, title="A bicycle", created_by=p)
    out["event"] = put(
        db,
        "fm_events",
        title="Picnic",
        starts_at="2026-12-01",
        invitees=[p, o],
        created_by=o,
    )
    put(db, "fm_dates", name=NAME, date="1990-05-01", kind="birthday", created_by=o)
    # Planner: her notes and points go; the household's list and budget stay
    put(db, "pn_notes", owner_id=p, title="Passwords hint", body="my secret worry")
    put(db, "pn_points", profile_id=p, delta=5, reason="chores")
    out["task"] = put(
        db, "pn_tasks", title="Bins", assignee=p, created_by=o, status="open"
    )
    out["list"] = put(db, "pn_lists", name="Groceries", created_by=p)
    out["entry"] = put(
        db,
        "pn_entries",
        kind="expense",
        amount_cents=4500,
        day="2026-11-01",
        profile_id=p,
        note="Zelda's shoes",
    )
    # Learn, Play, News, Stocks, AI
    deck = put(db, "ln_decks", owner_id=p, title="Spanish")
    put(db, "ln_cards", deck_id=deck, front="hola", back="hello")
    put(db, "ln_progress", profile_id=p, card_id="c1", deck_id=deck)
    out["sale"] = put(
        db,
        "ln_sales",
        deck_id=deck,
        owner_id=p,
        buyer_name="Bob Buyer",
        buyer_email="bob@buyers.example",
        amount_cents=300,
        currency="USD",
    )
    put(db, "pl_sessions", profile_id=p, game="sudoku")
    out["watch"] = put(db, "pl_watch", kind="film", title="Up", added_by=p)
    put(
        db,
        "pl_watch_plans",
        item_id=out["watch"],
        planned_for="2026-12-24",
        who=[p, o],
        created_by=o,
    )
    put(db, "nw_saved", profile_id=p, article_id="a1", saved_at="2026-11-01")
    put(db, "nw_settings", profile_id=p, topics=["science"])
    account = put(db, "stk_accounts", owner_id=p, kind="paper", name="Practice")
    put(
        db,
        "stk_txns",
        account_id=account,
        owner_id=p,
        symbol="ACME",
        kind="buy",
        quantity=1,
    )
    session = put(db, "ai_sessions", owner_id=p, title="Health worries")
    put(db, "ai_messages", session_id=session, role="user", content="my secret worry")
    put(db, "ai_memories", owner_id=p, text="Zelda is allergic to penicillin")
    # notifications and push subscriptions
    put(
        db,
        "nt_items",
        person=f"p:{p}",
        category="medicine",
        title="Time for your medicine",
    )
    put(
        db,
        "nt_push_subs",
        person=f"p:{p}",
        endpoint="https://push.example/zelda",
        p256dh="k",
        auth="a",
    )
    put(db, "nt_prefs", person=f"p:{p}", prefs={})
    put(
        db,
        "suite_outbox",
        app="learn",
        kind="receipt",
        recipient=EMAIL,
        subject="Your deck",
        body="Hello Zelda",
        status="sent",
    )
    return out


def seed_customer(db) -> dict[str, str]:
    """Zelda as a customer of the household's (or a member's) business, in every business app."""
    out: dict[str, str] = {}
    out["order"] = order = put(
        db,
        "sh_orders",
        number="1001",
        customer_name=NAME,
        customer_email=EMAIL,
        customer_phone=PHONE,
        address={"line1": "1 Quokka Lane"},
        items=[{"name": "Tea", "quantity": 2}],
        subtotal_cents=1999,
        total_cents=1999,
        currency="USD",
        payment_status="paid",
    )
    kit.record_revenue(
        db,
        "shop",
        source_type="order",
        source_id=order,
        amount_cents=1999,
        label=f"Order 1001 for {NAME}",
        customer=NAME,
    )
    kit.log(
        db,
        "shop",
        "order",
        f"Order 1001 from {NAME}",
        ref_type="order",
        ref_id=order,
        actor=NAME,
    )
    put(db, "sh_restock_alerts", product_id="p1", email=EMAIL, name=NAME)
    customer = put(
        db,
        "bk_customers",
        name=NAME,
        email=EMAIL,
        phone=PHONE,
        notes="prefers mornings",
    )
    out["booking"] = put(
        db,
        "bk_bookings",
        reference="B-7",
        customer_id=customer,
        customer_name=NAME,
        customer_email=EMAIL,
        customer_phone="5550104477",
        service_name="Haircut",
        price_cents=5000,
        paid_cents=5000,
        status="completed",
        note="Zelda's diary entry",
    )
    put(db, "bk_waitlist", service_id="s1", name=NAME, email=EMAIL, phone=PHONE)
    subscriber = put(db, "mk_subscribers", email=EMAIL, name=NAME, status="subscribed")
    put(db, "mk_newsletter_recipients", newsletter_id="n1", subscriber_id=subscriber)
    put(
        db,
        "mk_submissions",
        form_id="f1",
        name=NAME,
        email=EMAIL,
        phone=PHONE,
        message="Call me",
    )
    case = put(
        db,
        "sv_cases",
        number="12",
        subject="Broken kettle",
        customer_name=NAME,
        customer_email=EMAIL,
        customer_phone=PHONE,
    )
    put(
        db,
        "sv_messages",
        case_id=case,
        author_kind="customer",
        author_name=NAME,
        body="It sparks",
    )
    out["event_order"] = ev_order = put(
        db,
        "ev_orders",
        event_id="e1",
        reference="T-3",
        buyer_name=NAME,
        buyer_email=EMAIL,
        buyer_phone=PHONE,
        total_cents=2500,
        status="paid",
    )
    put(
        db,
        "ev_tickets",
        order_id=ev_order,
        event_id="e1",
        holder_name=NAME,
        code="TK1",
        price_cents=2500,
    )
    put(db, "ev_waitlist", event_id="e1", name=NAME, email=EMAIL, phone=PHONE)
    out["enrolment"] = enrolment = put(
        db,
        "ac_enrolments",
        course_id="c1",
        name=NAME,
        email=EMAIL,
        price_cents=9900,
        paid_cents=9900,
        status="active",
    )
    put(
        db,
        "ac_progress",
        enrolment_id=enrolment,
        lesson_id="l1",
        completed_at="2026-11-01",
    )
    out["client"] = client = put(
        db, "st_clients", name=NAME, email=EMAIL, phone=PHONE, company=""
    )
    out["invoice"] = put(
        db,
        "st_invoices",
        number="INV-9",
        client_id=client,
        total_cents=70000,
        subtotal_cents=70000,
        state="paid",
    )
    put(db, "st_contacts", client_id=client, name=NAME, email=EMAIL)
    put(db, "leads", name=NAME, email=EMAIL, phone=PHONE, company="Quokka Ltd")
    out["payment"] = put(
        db,
        "payment_requests",
        app="shop",
        object_type="order",
        object_id=order,
        amount_minor=1999,
        currency="USD",
        customer_name=NAME,
        customer_email=EMAIL,
        customer_phone=PHONE,
    )
    put(
        db,
        "suite_inbound",
        message_id="<m1@example>",
        from_email=EMAIL,
        subject="Re: your order",
        received_at="2026-11-01",
    )
    put(db, "recruiters", recruiter_id="r-zelda", name=NAME, email=EMAIL, phone=PHONE)
    return out


def seed_social_and_videos(zelda) -> dict[str, str]:
    key = f"p:{zelda['id']}"
    with closing(social_db.connect()) as sc:
        social.create_account(
            sc,
            {
                "key": key,
                "profile": {
                    "id": zelda["id"],
                    "name": NAME,
                    "emoji": "🙂",
                    "age_group": "adult",
                },
                "home": "local:1",
                "moderator": False,
                "hosted": False,
            },
            {"handle": f"z{zelda['id'][:10]}", "display_name": NAME},
        )
        account = social.account_by_key(sc, key)
        put(
            sc,
            "so_posts",
            author_id=account["id"],
            text="my secret worry",
            audience="public",
            created_at="2026-11-01",
        )
    with closing(videos_db.connect()) as vc:
        channel = put(
            vc,
            "vi_channels",
            person_key=key,
            home="local:1",
            name=NAME,
            payout_details="zelda@upi",
            created_at="2026-11-01",
            updated_at="2026-11-01",
        )
        put(
            vc,
            "vi_videos",
            channel_id=channel,
            title="My garden",
            visibility="public",
            created_at="2026-11-01",
            updated_at="2026-11-01",
        )
        put(
            vc,
            "vi_comments",
            video_id="v-other",
            author_id=channel,
            text="Zelda's diary entry",
            created_at="2026-11-01",
        )
        put(
            vc,
            "vi_uploads",
            owner_key=channel,
            home="local:1",
            kind="video",
            filename="garden.mp4",
            size=10,
            created_at="2026-11-01",
            updated_at="2026-11-01",
        )
    return {"account": account["id"], "channel": channel}


def test_a_person_is_erased_from_every_app(db):
    owner, zelda = household(db)
    kept = seed_life(db, owner, zelda)
    seed_customer(db)
    outside = seed_social_and_videos(zelda)
    needles = (zelda["id"], EMAIL, PHONE, "5550104477", NAME, "Quokka", *SECRETS)
    assert mentions(db, needles)  # the seeds are there

    receipt = life.erase(db, owner, zelda["id"], pin="2468")

    assert receipt["status"] == "done" and receipt["failed"] == []
    for app in (
        "care",
        "journey",
        "family",
        "planner",
        "learn",
        "play",
        "news",
        "stocks",
        "ai",
        "notify",
        "outbox",
        "social",
        "videos",
        "shop",
        "bookings",
        "life",
    ):
        assert receipt["erased"].get(app), (app, receipt)
    # nothing in the account's database names her any more (the receipt and the job row are checked below)
    assert mentions(db, needles, skip=("audit_logs", "erasure_jobs")) == []
    with closing(social_db.connect()) as sc:
        assert mentions(sc, (*needles, outside["account"])) == []
    with closing(videos_db.connect()) as vc:
        assert mentions(vc, (*needles, outside["channel"])) == []
    # the profile is gone, the household and Olive's own things are not
    assert [p["name"] for p in life.profiles(db, include_archived=True)] == ["Olive"]
    assert (
        db.execute(
            "SELECT name FROM cr_medicines WHERE id = ?", (kept["olive_medicine"],)
        ).fetchone()["name"]
        == "Aspirin"
    )
    assert (
        db.execute(
            "SELECT COUNT(*) FROM fm_posts WHERE id = ?", (kept["olive_post"],)
        ).fetchone()[0]
        == 1
    )
    assert db.execute("SELECT COUNT(*) FROM fm_reactions").fetchone()[0] == 0
    assert json.loads(
        db.execute(
            "SELECT invitees FROM fm_events WHERE id = ?", (kept["event"],)
        ).fetchone()[0]
    ) == [owner["id"]]
    assert (
        db.execute(
            "SELECT assignee FROM pn_tasks WHERE id = ?", (kept["task"],)
        ).fetchone()[0]
        == ""
    )
    assert (
        db.execute(
            "SELECT name, created_by FROM pn_lists WHERE id = ?", (kept["list"],)
        ).fetchone()["name"]
        == "Groceries"
    )
    assert (
        db.execute(
            "SELECT amount_cents FROM pn_entries WHERE id = ?", (kept["entry"],)
        ).fetchone()[0]
        == 4500
    )
    assert (
        db.execute(
            "SELECT total_cents FROM cr_statements WHERE id = ?", (kept["statement"],)
        ).fetchone()[0]
        == 12000
    )
    assert (
        db.execute(
            "SELECT amount_cents FROM ln_sales WHERE id = ?", (kept["sale"],)
        ).fetchone()[0]
        == 300
    )
    assert (
        db.execute(
            "SELECT title FROM pl_watch WHERE id = ?", (kept["watch"],)
        ).fetchone()[0]
        == "Up"
    )
    # the job keeps who it was for only until it is done
    job = db.execute(
        "SELECT * FROM erasure_jobs WHERE id = ?", (receipt["job_id"],)
    ).fetchone()
    assert job["status"] == "done" and job["subject"] == ""


def test_accounting_rows_survive_a_customer_erasure_anonymised(db):
    seeded = seed_customer(db)
    preview = erasure.preview_customer(db, emails=[EMAIL.upper()])
    assert (
        preview["sh_orders"] == 1
        and preview["bk_bookings"] == 1
        and preview["leads"] == 1
    )

    receipt = erasure.erase_customer(
        db, phones=["555-010-4477"], emails=[EMAIL], actor="business"
    )

    assert receipt["status"] == "done" and receipt["reason"] == "customer_request"
    assert (
        "social" not in receipt["erased"] and "life" not in receipt["erased"]
    )  # a customer request stays in the business
    order = db.execute(
        "SELECT * FROM sh_orders WHERE id = ?", (seeded["order"],)
    ).fetchone()
    assert (str(order["number"]), order["total_cents"], order["payment_status"]) == (
        "1001",
        1999,
        "paid",
    )
    assert (
        order["customer_name"],
        order["customer_email"],
        order["customer_phone"],
    ) == (erasure.ERASED_NAME, "", "")
    assert (
        "Quokka" not in order["address"]
        and json.loads(order["items"])[0]["name"] == "Tea"
    )
    booking = db.execute(
        "SELECT * FROM bk_bookings WHERE id = ?", (seeded["booking"],)
    ).fetchone()
    assert (
        booking["price_cents"],
        booking["paid_cents"],
        booking["customer_email"],
        booking["note"],
    ) == (5000, 5000, "", "")
    assert db.execute(
        "SELECT total_cents, buyer_email FROM ev_orders WHERE id = ?",
        (seeded["event_order"],),
    ).fetchone()[:] == (2500, "")
    assert db.execute(
        "SELECT paid_cents, email FROM ac_enrolments WHERE id = ?",
        (seeded["enrolment"],),
    ).fetchone()[:] == (9900, "")
    assert db.execute(
        "SELECT total_cents, client_id FROM st_invoices WHERE id = ?",
        (seeded["invoice"],),
    ).fetchone()[:] == (70000, seeded["client"])
    assert db.execute(
        "SELECT name, email FROM st_clients WHERE id = ?", (seeded["client"],)
    ).fetchone()[:] == (erasure.ERASED_NAME, "")
    assert db.execute(
        "SELECT amount_minor, customer_email FROM payment_requests WHERE id = ?",
        (seeded["payment"],),
    ).fetchone()[:] == (1999, "")
    ledger = db.execute(
        "SELECT amount_cents, customer, label FROM suite_revenue"
    ).fetchone()
    assert (ledger["amount_cents"], ledger["customer"]) == (
        1999,
        "",
    ) and NAME not in ledger["label"]
    line = db.execute("SELECT title, actor FROM suite_activity").fetchone()
    assert line["title"] == "Order 1001 from a customer" and line["actor"] == ""
    # what was only about the customer is gone
    for table in (
        "sh_restock_alerts",
        "bk_waitlist",
        "mk_subscribers",
        "mk_newsletter_recipients",
        "mk_submissions",
        "sv_cases",
        "sv_messages",
        "ev_waitlist",
        "ac_progress",
        "st_contacts",
        "leads",
        "suite_inbound",
    ):
        assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0, table
    # a recruiter record is Careers', not the business's: a customer request leaves it
    assert db.execute("SELECT COUNT(*) FROM recruiters").fetchone()[0] == 1
    assert (
        mentions(
            db,
            (EMAIL, PHONE, "5550104477", NAME),
            skip=("audit_logs", "erasure_jobs", "recruiters"),
        )
        == []
    )


def _dating_pair(sc):
    zelda = SocialPerson(sc, "p:zelda", "Zelda")
    bob = SocialPerson(sc, "p:bob", "Bob")
    zelda.date(first_name="Zelda", bio="Zelda's diary entry")
    bob.date(first_name="Bob")
    match = _match(sc, zelda, bob)
    dating.chat_send(
        sc, zelda.ident, zelda.acct, match["id"], {"text": "my secret worry"}
    )
    return zelda, bob


def test_a_legal_hold_keeps_dating_evidence_until_the_report_closes(db):
    with closing(social_db.connect()) as sc:
        zelda, bob = _dating_pair(sc)
        zelda_id, bob_id = zelda.acct["id"], bob.acct["id"]
        filed = social.report(
            sc,
            bob.acct,
            {
                "target_type": "account",
                "target_id": zelda_id,
                "reason": "Rude messages",
            },
            dating_profiles=True,
        )
        photo = dating.profile_of(sc, zelda_id)["photos"][0].split(":", 1)[1]
    person = erasure.Person(profile_id="zelda")  # her Social key is p:zelda

    receipt = erasure.run_erasure(
        db, person, reason="profile_erased", actor="household", apps=["social"]
    )

    assert receipt["status"] == "done"
    assert (
        receipt["held"]["social"] >= 3
    )  # the report, its evidence, the ended chat (and the photo it shows)
    with closing(social_db.connect()) as sc:
        assert social.account_by_key(sc, "p:zelda") is None
        assert (
            sc.execute(
                "SELECT COUNT(*) FROM so_dating_profiles WHERE account_id = ?",
                (zelda_id,),
            ).fetchone()[0]
            == 0
        )
        report = sc.execute(
            "SELECT * FROM so_reports WHERE id = ?", (filed["report_id"],)
        ).fetchone()
        assert report["status"] == "open"
        evidence = sc.execute(
            "SELECT * FROM so_evidence WHERE report_id = ?", (filed["report_id"],)
        ).fetchall()
        assert evidence and all(e["legal_hold"] == social.LEGAL_HOLD for e in evidence)
        assert (
            "Zelda" in evidence[0]["data"]
        )  # the moderator still sees what was reported
        ended = sc.execute(
            "SELECT * FROM so_dating_ended WHERE a_id = ? OR b_id = ?",
            (zelda_id, zelda_id),
        ).fetchall()
        assert ended and all(e["legal_hold"] == social.LEGAL_HOLD for e in ended)
        assert "my secret worry" in ended[0]["transcript"]
        assert (
            sc.execute(
                "SELECT COUNT(*) FROM so_media WHERE id = ?", (photo,)
            ).fetchone()[0]
            == 1
        )
        shown = social.reports(sc)[0]
        assert (
            shown["evidence"][0]["legal_hold"] is True
            and shown["reporter"]["id"] == bob_id
        )
        # the background purge leaves held evidence while the report is open
        social.purge_kept(sc)
        assert sc.execute("SELECT COUNT(*) FROM so_evidence").fetchone()[0] == len(
            evidence
        )
        # the moderator closes the report: the hold ends and the next purge removes what it kept
        social.moderate(sc, "moderator", filed["report_id"], "dismiss")
        released = social.purge_kept(sc)
        assert released["evidence"] == len(evidence) and released["ended_chats"] >= 1
        assert sc.execute("SELECT COUNT(*) FROM so_evidence").fetchone()[0] == 0
        assert (
            sc.execute(
                "SELECT COUNT(*) FROM so_dating_ended WHERE a_id = ? OR b_id = ?",
                (zelda_id, zelda_id),
            ).fetchone()[0]
            == 0
        )
        assert sc.execute("SELECT COUNT(*) FROM so_reports").fetchone()[0] == 0
        assert (
            sc.execute(
                "SELECT COUNT(*) FROM so_media WHERE owner_id = ?", (zelda_id,)
            ).fetchone()[0]
            == 0
        )
        assert mentions(sc, ("my secret worry", "Zelda's diary entry", zelda_id)) == []
        # Bob is untouched
        assert social.account_by_key(sc, "p:bob") is not None


def test_without_an_open_report_nothing_is_held(db):
    with closing(social_db.connect()) as sc:
        zelda, _bob = _dating_pair(sc)
        zelda_id = zelda.acct["id"]
    receipt = erasure.run_erasure(
        db,
        erasure.Person(profile_id="zelda"),
        reason="profile_erased",
        actor="household",
        apps=["social"],
    )
    assert receipt["held"] == {} and receipt["erased"]["social"] > 0
    with closing(social_db.connect()) as sc:
        assert mentions(sc, ("my secret worry", zelda_id)) == []


def test_a_failing_eraser_does_not_stop_the_rest_and_can_be_retried(db):
    owner, zelda = household(db)
    kept = seed_life(db, owner, zelda)
    calls = []

    def broken(connection, person):
        calls.append(person.profile_id)
        connection.execute(
            "DELETE FROM cr_medicines WHERE id = ?", (kept["olive_medicine"],)
        )  # rolled back below
        if len(calls) == 1:
            raise RuntimeError(f"disk full while erasing {person.emails}")
        return {"elsewhere": 2}

    erasure.register("broken", broken, order=15)
    try:
        receipt = life.erase(db, owner, zelda["id"], pin="2468")
        assert receipt["status"] == "partial" and receipt["failed"] == ["broken"]
        # every other app ran, the profile included; the broken one's own change was rolled back
        assert (
            receipt["erased"]["care"]
            and receipt["erased"]["life"]
            and receipt["erased"]["notify"]
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM cr_medicines WHERE id = ?",
                (kept["olive_medicine"],),
            ).fetchone()[0]
            == 1
        )
        assert (
            db.execute(
                "SELECT COUNT(*) FROM life_profiles WHERE id = ?", (zelda["id"],)
            ).fetchone()[0]
            == 0
        )
        job = db.execute(
            "SELECT * FROM erasure_jobs WHERE id = ?", (receipt["job_id"],)
        ).fetchone()
        assert (
            job["status"] == "partial" and zelda["id"] in job["subject"]
        )  # kept so a retry knows who it was
        assert [j["status"] for j in erasure.jobs(db)] == ["partial"]
        assert "disk full" not in json.dumps(
            erasure.jobs(db)
        )  # an error's words may carry personal data
        # the background pass tries again: only the app that failed runs
        retried = erasure.retry_partial(db, actor="system")
        assert [r["status"] for r in retried] == ["done"] and calls == [
            zelda["id"],
            zelda["id"],
        ]
        assert (
            retried[0]["erased"]["broken"] == 2
            and retried[0]["erased"]["care"] == receipt["erased"]["care"]
        )
        job = db.execute(
            "SELECT * FROM erasure_jobs WHERE id = ?", (receipt["job_id"],)
        ).fetchone()
        assert job["status"] == "done" and job["subject"] == "" and job["attempts"] == 2
    finally:
        erasure.unregister("broken")


def test_the_audit_receipt_has_no_personal_data(db):
    owner, zelda = household(db)
    seed_life(db, owner, zelda)
    seed_customer(db)
    receipt = life.erase(db, owner, zelda["id"], pin="2468")
    rows = db.execute(
        "SELECT * FROM audit_logs WHERE target_type = 'erasure'"
    ).fetchall()
    assert [r["action"] for r in rows] == ["person.erased"]
    text = json.dumps([dict(r) for r in rows])
    for needle in (
        zelda["id"],
        EMAIL,
        "zelda",
        "Quokka",
        "5550104477",
        "010-4477",
        *SECRETS,
    ):
        assert needle.lower() not in text.lower(), needle
    detail = json.loads(rows[0]["detail"])
    assert detail["job_id"] == receipt["job_id"] == rows[0]["target_id"]
    assert detail["erased"]["care"] > 0 and set(detail) == {
        "job_id",
        "reason",
        "status",
        "erased",
        "held",
        "failed",
        "attempts",
    }
    assert all(isinstance(v, int) for v in detail["erased"].values())


def test_erasing_needs_an_adult_and_the_owners_pin(db):
    owner = life.current(Req(), db)
    zelda = life.add(db, owner, {"name": NAME, "age_group": "adult"})
    kid = life.add(db, owner, {"name": "Mia", "age_group": "child"})
    with pytest.raises(ValueError, match="PIN"):
        life.erase(db, owner, zelda["id"], pin="")  # the owner has no PIN yet
    life.set_pin(db, owner, owner["id"], "2468")
    owner = life.get(db, owner["id"])
    with pytest.raises(life.WrongPin):
        life.erase(db, owner, zelda["id"], pin="1111")
    with pytest.raises(PermissionError):
        life.erase(db, kid, zelda["id"], pin="2468")
    with pytest.raises(ValueError, match="owner"):
        life.erase(db, zelda, owner["id"], pin="2468")
    # "Archive" keeps the data; an archived person can be erased later
    put(
        db,
        "cr_diary",
        owner_id=zelda["id"],
        day="2026-11-01",
        note="kept while archived",
    )
    life.remove(db, owner, zelda["id"])
    assert [p["name"] for p in life.archived(db)] == [NAME]
    assert db.execute("SELECT COUNT(*) FROM cr_diary").fetchone()[0] == 1
    receipt = life.erase(db, owner, zelda["id"], pin="2468")
    assert receipt["erased"]["care"] == 1 and life.archived(db) == []
    assert db.execute("SELECT COUNT(*) FROM cr_diary").fetchone()[0] == 0


def test_deleting_the_account_erases_everyone_outside_its_file(db, tmp_path):
    owner, zelda = household(db)
    seed_social_and_videos(zelda)
    seed_social_and_videos(owner)
    path = db.execute("PRAGMA database_list").fetchone()[2]
    receipts = erasure.erase_account_database(path)
    assert len(receipts) == 2 and all(
        r["reason"] == "account_deleted" and r["status"] == "done" for r in receipts
    )
    with closing(social_db.connect()) as sc:
        assert sc.execute("SELECT COUNT(*) FROM so_accounts").fetchone()[0] == 0
    with closing(videos_db.connect()) as vc:
        assert vc.execute("SELECT COUNT(*) FROM vi_channels").fetchone()[0] == 0
    # the owner's whole account went with them: Careers too
    assert db.execute("SELECT COUNT(*) FROM life_profiles").fetchone()[0] == 0
    assert erasure.erase_account_database(tmp_path / "missing.sqlite3") == []


def test_every_app_has_an_eraser_and_a_person_must_be_named(db):
    apps = erasure.registered()
    assert apps[-1] == "life" and apps[-2] == "sweep"  # the profile goes last
    for app in (
        "social",
        "videos",
        "care",
        "journey",
        "life",
        "ai",
        "planner",
        "learn",
        "play",
        "news",
        "careers",
        "leads",
        "recruiters",
        "outbox",
        "notify",
        "shop",
        "bookings",
        "marketing",
        "service",
        "events",
        "academy",
    ):
        assert app in apps, app
    with pytest.raises(ValueError):
        erasure.run_erasure(db, erasure.Person(), reason="profile_erased", actor="x")
    with pytest.raises(ValueError):
        erasure.run_erasure(
            db, erasure.Person(profile_id="p"), reason="because", actor="x"
        )
    with pytest.raises(ValueError):
        erasure.erase_customer(db, emails=[""], actor="business")
    assert (
        erasure.phone_key("+91 98765 43210")
        == erasure.phone_key("098765-43210")
        == "9876543210"
    )


# who did something for the business (a member of staff), not a person whose data is theirs
NOT_PERSONAL = {"payment_refunds"}


def test_every_table_keyed_by_a_person_has_an_eraser(db):
    """A new table with an owner_id, profile_id or author_id column needs a line in its app's eraser."""
    from pathlib import Path

    source = (Path(erasure.__file__).parent / "erasers.py").read_text(encoding="utf-8")
    person_columns = {
        "owner_id",
        "profile_id",
        "author_id",
        "person",
        "created_by",
        "member_ref",
        "added_by",
        "assignee",
    }
    missing = []
    for (table,) in db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'"
    ).fetchall():
        have = {row[1] for row in db.execute(f'PRAGMA table_info("{table}")')}
        if (
            have & person_columns
            and f'"{table}"' not in source
            and table not in NOT_PERSONAL
        ):
            missing.append(table)
    assert missing == []


def _request(path_pattern, path, *, connect, header="", payload=None, audits=None):
    import re

    from aira.airaapi.http import Request

    return Request(
        match=re.fullmatch(path_pattern, path),
        payload=payload or {},
        query={},
        user={"user_id": "u1", "email": "olive@home.example"},
        headers={"X-Aira-Profile": header},
        client="127.0.0.1",
        connect=connect,
        setting=lambda name: "",
        secret_store=None,
        key_index=None,
        audit=lambda *args: audits.append(args) if audits is not None else None,
    )


def _connector(db):
    path = db.execute("PRAGMA database_list").fetchone()[2]

    def connect():
        connection = sqlite3.connect(path)
        connection.row_factory = sqlite3.Row
        return connection

    return connect


def test_the_life_route_erases_with_the_owners_pin_and_audits_no_person(db):
    from aira.suite import life_routes

    owner, zelda = household(db)
    seed_life(db, owner, zelda)
    header = life.unlock(db, owner)["header"]
    pattern = r"/api/life/profiles/([0-9a-f]{32})/erase"
    path = f"/api/life/profiles/{zelda['id']}/erase"
    audits: list[tuple] = []
    wrong = _request(
        pattern,
        path,
        connect=_connector(db),
        header=header,
        payload={"pin": "0000"},
        audits=audits,
    )
    with pytest.raises(life.WrongPin):
        life_routes.profiles_erase(wrong)
    assert life_routes._PIN_TRIES.get(owner["id"])  # counted like a wrong PIN at unlock
    right = _request(
        pattern,
        path,
        connect=_connector(db),
        header=header,
        payload={"pin": "2468"},
        audits=audits,
    )
    status, body = life_routes.profiles_erase(right)
    life_routes._PIN_TRIES.clear()
    assert (
        status == 200
        and body["erased"] is True
        and body["receipt"]["erased"]["care"] > 0
    )
    assert audits == [
        ("life.profile_erase", "erasure", body["receipt"]["job_id"], {"status": "done"})
    ]
    assert (
        db.execute(
            "SELECT COUNT(*) FROM life_profiles WHERE id = ?", (zelda["id"],)
        ).fetchone()[0]
        == 0
    )


def test_the_business_hub_erases_a_customer_by_email_or_phone(db):
    from aira.suite import erasure_routes

    seeded = seed_customer(db)
    connect = _connector(db)
    audits: list[tuple] = []

    def call(handler, path, payload=None):
        return handler(
            _request(path, path, connect=connect, payload=payload, audits=audits)
        )

    found = call(
        erasure_routes.preview, "/api/suite/privacy/preview", {"phone": PHONE}
    )[1]
    assert found["found"]["bk_bookings"] == 1 and found["total"] > 5
    with pytest.raises(ValueError, match="Confirm"):
        call(erasure_routes.erase, "/api/suite/privacy/erase", {"email": EMAIL})
    with pytest.raises(ValueError):
        call(erasure_routes.erase, "/api/suite/privacy/erase", {})
    status, body = call(
        erasure_routes.erase,
        "/api/suite/privacy/erase",
        {"email": EMAIL, "phone": PHONE, "confirm": True},
    )
    assert status == 200 and body["receipt"]["status"] == "done"
    assert audits == [
        (
            "suite.customer_erase",
            "erasure",
            body["receipt"]["job_id"],
            {"status": "done"},
        )
    ]
    assert (
        db.execute(
            "SELECT customer_email FROM sh_orders WHERE id = ?", (seeded["order"],)
        ).fetchone()[0]
        == ""
    )
    listed = call(erasure_routes.erasures, "/api/suite/privacy/erasures")[1]["erasures"]
    assert [item["reason"] for item in listed] == ["customer_request"]
    assert EMAIL not in json.dumps(listed)


def test_a_job_cut_short_resumes_where_it_stopped(db):
    owner, zelda = household(db)
    seed_life(db, owner, zelda)

    def crash(connection, person):
        raise SystemExit("the server stopped")

    erasure.register(
        "crash", crash, order=15
    )  # after the life apps, before Social and the rest
    try:
        with pytest.raises(SystemExit):
            life.erase(db, owner, zelda["id"], pin="2468")
    finally:
        erasure.unregister("crash")
    db.rollback()
    job = db.execute("SELECT * FROM erasure_jobs").fetchone()
    assert job["status"] == "running" and "care" in json.loads(job["counts"])
    assert erasure.retry_partial(db) == []  # not stalled yet
    db.execute("UPDATE erasure_jobs SET updated_at = '2026-01-01T00:00:00Z'")
    db.commit()
    [receipt] = erasure.retry_partial(db)
    assert (
        receipt["status"] == "done"
        and receipt["erased"]["care"] > 0
        and receipt["erased"]["life"] == 1
    )
    assert (
        db.execute(
            "SELECT COUNT(*) FROM life_profiles WHERE id = ?", (zelda["id"],)
        ).fetchone()[0]
        == 0
    )
