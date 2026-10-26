"""The life apps' shared core (aira/suite/life.py, /api/life): people, PINs, preferences, Today and pulse."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest
from cloud_support import build_org, sign_in

import test_careers
from aira import suite
from aira.suite import kit, life

env = test_careers.env
api = test_careers.api
stub = test_careers.stub


class Req:
    """Just enough of a request for life.current()."""

    def __init__(self, header: str = "", email: str = "pat@home.example") -> None:
        self._header = header
        self.user = {"email": email}

    def header(self, name: str, default: str = "") -> str:
        return self._header if name == "X-Aira-Profile" else default


@pytest.fixture
def db(tmp_path):
    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "life.sqlite3")
    connection.row_factory = sqlite3.Row
    yield connection
    connection.close()


def test_the_first_person_is_the_account_and_others_need_choosing(db):
    owner = life.current(Req(), db)
    assert owner["owner"] and owner["name"] == "Pat" and owner["age_group"] == "adult"
    assert (
        life.current(Req(), db)["id"] == owner["id"]
    )  # one person, no PIN: nothing to choose
    kid = life.add(
        db, owner, {"name": "Mia", "age_group": "child", "emoji": "👧", "pin": "1234"}
    )
    gran = life.add(db, owner, {"name": "Rosa", "age_group": "senior"})
    assert kid["prefs"]["easy_mode"] is True and gran["prefs"]["text_size"] == "large"
    with pytest.raises(life.ProfileRequired):
        life.current(Req(), db)
    assert life.current(Req(gran["id"]), db)["id"] == gran["id"]
    with pytest.raises(life.ProfileLocked):
        life.current(Req(kid["id"]), db)
    with pytest.raises(life.ProfileLocked):
        life.current(Req(f"{kid['id']}.{'0' * 32}"), db)
    assert (
        life.current(Req(f"{kid['id']}.{life.unlock_token(kid)}"), db)["id"]
        == kid["id"]
    )
    assert life.check_pin(kid, "1234") and not life.check_pin(kid, "9999")
    # A child cannot manage the household; nobody removes the owner; PINs are digits.
    with pytest.raises(PermissionError):
        life.add(db, kid, {"name": "X"})
    with pytest.raises(PermissionError):
        life.update(db, kid, gran["id"], {"name": "Nope"})
    with pytest.raises(ValueError):
        life.remove(db, gran, owner["id"])
    with pytest.raises(ValueError):
        life.set_pin(db, owner, kid["id"], "12ab")
    assert "pin_hash" not in life.public(kid) and life.public(kid)["has_pin"] is True
    # Changing the PIN invalidates the old unlock.
    old = life.unlock_token(kid)
    changed = life.set_pin(db, owner, kid["id"], "5678")
    assert life.unlock_token(changed) != old
    life.remove(db, owner, gran["id"])
    assert [p["name"] for p in life.profiles(db)] == ["Pat", "Mia"]


def test_preferences_make_the_app_easier(db):
    me = life.current(Req(), db)
    prefs = life.save_prefs(
        db,
        me,
        {
            "easy_mode": True,
            "text_size": "xlarge",
            "contrast": "1",
            "start_page": "today",
        },
    )
    assert prefs == {
        **life.PREF_DEFAULTS,
        "easy_mode": True,
        "text_size": "xlarge",
        "contrast": True,
        "start_page": "today",
    }
    with pytest.raises(ValueError):
        life.save_prefs(db, me, {"text_size": "huge"})
    with pytest.raises(ValueError):
        life.save_prefs(db, me, {"start_page": "javascript:alert(1)"})


def test_card_and_action_helpers():
    card = life.card(
        "care",
        "Medicines today",
        lines=[
            {"text": "Metformin", "action": life.action("Taken", "/api/care/doses")}
        ],
    )
    assert card["link"] == "#/care" and card["lines"][0]["action"]["method"] == "POST"
    with pytest.raises(ValueError):
        life.action("x", "https://evil.example")
    assert life.greeting(8) == "Good morning" and life.greeting(20) == "Good evening"


# ---------------------------------------------------------------------------
# the single-user build
# ---------------------------------------------------------------------------


def test_household_journey_on_the_local_build(env, api, monkeypatch):
    kit.forget_schema()
    listed = api.get("/api/life/profiles")
    assert [p["name"] for p in listed["profiles"]] == [
        "Careers"
    ]  # made from the sign-in address
    owner = api.get("/api/life/me")["profile"]
    assert owner["owner"] is True and "pin_hash" not in owner
    # the account's day (its time zone is UTC until set), not this computer's: they differ in the evening
    soon = (
        (datetime.now(timezone.utc).date() + timedelta(days=3))
        .replace(year=1950)
        .isoformat()
    )
    kid = api.post(
        "/api/life/profiles",
        {"name": "Mia", "age_group": "child", "pin": "2468"},
        expect=201,
    )["profile"]
    # Now there are two people: every request says who is using Aira.
    gran = _call(
        env,
        "POST",
        "/api/life/profiles",
        {"name": "Rosa", "age_group": "senior", "birthday": soon},
        header=owner["id"],
        api=api,
    )["profile"]
    # With three people the browser must say who it is.
    status, body = api.call("GET", "/api/life/me")
    assert status == 403 and body["code"] == "profile_required"
    status, body = api.call(
        "POST", "/api/life/unlock", {"profile_id": kid["id"], "pin": "1111"}
    )
    assert status == 403
    header = api.post("/api/life/unlock", {"profile_id": kid["id"], "pin": "2468"})[
        "header"
    ]
    assert header.startswith(kid["id"] + ".")
    me = _call(env, "GET", "/api/life/me", header=header, api=api)
    assert me["profile"]["name"] == "Mia" and me["can_manage"] is False
    status, body = _raw(env, api, "GET", "/api/life/me", header=kid["id"])
    assert status == 403 and body["code"] == "profile_locked"
    gran_header = api.post("/api/life/unlock", {"profile_id": gran["id"]})["header"]
    prefs = _call(
        env,
        "POST",
        "/api/life/prefs",
        {"text_size": "xlarge"},
        header=gran_header,
        api=api,
    )["prefs"]
    assert prefs["text_size"] == "xlarge" and prefs["easy_mode"] is True

    # Today gathers every life app's cards (a stand-in app here) and the household's birthdays.
    def cards(connection, profile):
        return [
            life.card("learn", f"Hello {profile['name']}", lines=[{"text": "Call Mia"}])
        ]

    def beat(connection, profile, since):
        return {
            "badge": 2,
            "alerts": [{"id": "a1", "title": "Time for tea", "speak": True}],
        }

    monkeypatch.setitem(life.TODAY, "learn", cards)
    monkeypatch.setitem(life.PULSE, "learn", beat)
    # Family (when installed) shows the household's dates itself; without it Today lists the birthdays.
    monkeypatch.delitem(life.TODAY, "family", raising=False)
    day = _call(env, "GET", "/api/life/today", header=gran_header, api=api)
    hello = next(card for card in day["cards"] if card["title"] == "Hello Rosa")
    assert day["greeting"].endswith("Rosa") and hello["app"] == "learn"
    assert day["birthdays"][0]["name"] == "Rosa" and day["birthdays"][0]["in_days"] == 3
    beat_now = _call(
        env,
        "GET",
        "/api/life/pulse?since=2026-01-01T00:00:00Z",
        header=gran_header,
        api=api,
    )
    assert (
        beat_now["apps"]["learn"]["badge"] == 2
        and beat_now["alerts"][0]["app"] == "learn"
    )


def _raw(env, api, method, path, body=None, header=""):
    import json
    import urllib.error
    import urllib.request

    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(env.base + path, data=data, method=method)
    for name, value in {
        "Content-Type": "application/json",
        "X-Requested-With": "aira",
        "Origin": env.base,
        "X-Aira-Profile": header,
    }.items():
        request.add_header(name, value)
    try:
        with api.opener.open(request, timeout=30) as response:
            return response.status, json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read() or b"{}")


def _call(env, method, path, body=None, *, header, api):
    status, data = _raw(env, api, method, path, body, header)
    assert status < 400, (path, status, data)
    return data


# ---------------------------------------------------------------------------
# the hosted build
# ---------------------------------------------------------------------------


def test_hosted_members_each_get_their_own_profile(cloud):
    kit.forget_schema()
    org = build_org(cloud)
    seen = {}
    for who in ("owner", "candidate", "auditor", "billing", "coach"):
        browser = sign_in(cloud, getattr(org, who))
        status, me = browser.call("GET", "/api/life/me")
        assert status == 200, (who, me)
        seen[who] = me["profile"]
        assert me["hosted"] is True and me["can_manage"] is False
    assert len({p["id"] for p in seen.values()}) == 5
    owner = sign_in(cloud, org.owner)
    status, listed = owner.call("GET", "/api/life/profiles")
    assert len(listed["profiles"]) == 5
    # Members join through the organization, never by adding a profile here.
    assert owner.call("POST", "/api/life/profiles", {"name": "Extra"})[0] == 403
    # Team does not include the life apps: Today has no app cards until one is turned on.
    status, day = owner.call("GET", "/api/life/today")
    assert status == 200 and day["apps"] == []
    cloud.billing.subscribe_app(
        org.owner, "family"
    )  # the old id: Family is part of Social
    status, day = owner.call("GET", "/api/life/today")
    assert day["apps"] == ["social", "family"]
    assert suite.actor() is None  # outside a request there is no person
