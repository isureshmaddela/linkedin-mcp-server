"""Life safety (aira/suite/life.py): ages that follow birthdays, who changes an age or a PIN, unlocks that expire
and lock when idle, and parental controls."""

from __future__ import annotations

import sqlite3
from datetime import date

import pytest

from aira.suite import kit, life


class Req:
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


@pytest.fixture
def home(db):
    owner = life.current(Req(), db)
    life.set_pin(db, owner, owner["id"], "1111")
    owner = life.get(db, owner["id"])
    dad = life.add(db, owner, {"name": "Ravi", "age_group": "adult", "pin": "2222"})
    kid = life.add(db, owner, {"name": "Mia", "birthday": "2018-03-01", "pin": "3333"})
    teen = life.add(db, owner, {"name": "Arjun", "birthday": "2011-01-15"})
    return {"owner": owner, "dad": dad, "kid": kid, "teen": teen}


def test_the_age_group_follows_the_birthday_and_moves_up_with_age(
    db, home, monkeypatch
):
    assert life.group_for("2018-03-01", today=date(2026, 11, 6)) == "child"
    assert life.group_for("2011-01-15", today=date(2026, 11, 6)) == "teen"
    assert life.group_for("1950-01-01", "senior", today=date(2026, 11, 6)) == "senior"
    assert life.group_for("1990-01-01", "child", today=date(2026, 11, 6)) == "adult"
    assert life.group_for("") is None
    assert home["kid"]["age_group"] == "child" and home["teen"]["age_group"] == "teen"
    # The browser's age group is ignored once there is a birthday.
    kid = life.update(db, home["owner"], home["kid"]["id"], {"age_group": "adult"})
    assert kid["age_group"] == "child"
    # The child turns 13 and the teen 18: their profiles move up by themselves.
    monkeypatch.setattr(life, "_today", lambda: date(2031, 3, 2))
    assert life.get(db, home["kid"]["id"])["age_group"] == "teen"
    assert life.get(db, home["teen"]["id"])["age_group"] == "adult"
    with pytest.raises(ValueError):
        life.update(db, home["owner"], home["kid"]["id"], {"birthday": "2099-01-01"})


def test_only_the_owner_or_a_guardian_changes_an_age(db, home):
    kid, teen, dad, owner = home["kid"], home["teen"], home["dad"], home["owner"]
    # A child or a teen can no longer make themselves an adult, by age group or by birthday.
    with pytest.raises(PermissionError):
        life.update(db, kid, kid["id"], {"birthday": "1990-01-01"})
    with pytest.raises(PermissionError):
        life.update(
            db, teen, teen["id"], {"birthday": "1990-01-01", "age_group": "adult"}
        )
    no_birthday = life.add(db, owner, {"name": "Lia", "age_group": "child"})
    with pytest.raises(PermissionError):
        life.update(db, no_birthday, no_birthday["id"], {"age_group": "adult"})
    # Their other details are still theirs to change (the edit form sends the age fields unchanged).
    renamed = life.update(
        db,
        kid,
        kid["id"],
        {"name": "Mia R", "age_group": "child", "birthday": kid["birthday"]},
    )
    assert renamed["name"] == "Mia R"
    # An adult is not a guardian of another adult: only the owner changes their birthday.
    with pytest.raises(PermissionError):
        life.update(db, dad, dad["id"], {"birthday": "2015-01-01"})
    with pytest.raises(PermissionError):
        life.update(db, dad, owner["id"], {"birthday": "1980-01-01"})
    assert (
        life.update(db, owner, dad["id"], {"birthday": "1985-05-05"})["birthday"]
        == "1985-05-05"
    )
    # A guardian (another adult of the household) corrects a child's birthday.
    assert (
        life.update(db, dad, teen["id"], {"birthday": "2016-01-01"})["age_group"]
        == "child"
    )
    # A grown-up switches between adult and senior freely: it only changes how the screens look.
    assert (
        life.update(db, dad, dad["id"], {"age_group": "senior"})["age_group"]
        == "senior"
    )
    with pytest.raises(ValueError):
        life.update(db, owner, owner["id"], {"birthday": "2015-01-01"})
    assert life.may_change_age(owner, kid) and life.may_change_age(dad, kid)
    assert not life.may_change_age(kid, kid) and not life.may_change_age(dad, owner)


def test_a_hosted_member_gives_their_birthday_once(db, monkeypatch):
    from aira import suite

    member = {"member_id": "m-0", "name": "Ana", "email": "ana@org.example"}
    monkeypatch.setattr(suite, "actor", lambda: member)
    assert life.current(Req(), db)["owner"]  # the first member
    member = {"member_id": "m-1", "name": "Sam", "email": "sam@org.example"}
    sam = life.current(Req(), db)
    assert sam["age_group"] == "adult"
    sam = life.update(db, sam, sam["id"], {"birthday": "2010-06-01"})
    assert sam["age_group"] == "teen"  # the safer way round is always open
    with pytest.raises(PermissionError):
        life.update(db, sam, sam["id"], {"birthday": "1990-06-01"})
    assert (
        life.update(db, sam, sam["id"], {"age_group": "adult"})["age_group"] == "teen"
    )


def test_pin_changes_need_the_right_pin(db, home):
    owner, dad, kid, teen = home["owner"], home["dad"], home["kid"], home["teen"]
    # Your own PIN: your current one.
    with pytest.raises(life.WrongPin):
        life.set_pin(db, dad, dad["id"], "4444")
    with pytest.raises(life.WrongPin):
        life.set_pin(db, dad, dad["id"], "4444", confirm="9999")
    life.set_pin(db, dad, dad["id"], "4444", confirm="2222")
    dad = life.get(db, dad["id"])
    assert life.check_pin(dad, "4444")
    # Another adult cannot reset the owner's PIN (or any adult's) without the owner's PIN.
    with pytest.raises(life.WrongPin) as caught:
        life.set_pin(db, dad, owner["id"], "")
    assert caught.value.profile_id == owner["id"]
    assert life.check_pin(life.get(db, owner["id"]), "1111")
    life.set_pin(db, dad, owner["id"], "5555", confirm="1111")
    assert life.check_pin(life.get(db, owner["id"]), "5555")
    owner = life.get(db, owner["id"])
    # A guardian resets their own child's PIN with the guardian's own PIN.
    with pytest.raises(life.WrongPin):
        life.set_pin(db, dad, kid["id"], "6666", confirm="5555")
    life.set_pin(db, dad, kid["id"], "6666", confirm="4444")
    assert life.check_pin(life.get(db, kid["id"]), "6666")
    # A child cannot change anyone else's PIN; a teen changes their own with their current one (none yet).
    with pytest.raises(PermissionError):
        life.set_pin(db, kid, teen["id"], "7777")
    life.set_pin(db, teen, teen["id"], "7777")
    rule = life.pin_rule(db, dad, owner)
    assert rule == {
        "allowed": True,
        "needed": True,
        "by": "owner",
        "label": "Pat's PIN (the household owner)",
    }
    assert life.pin_rule(db, dad, kid)["by"] == "guardian"
    assert life.pin_rule(db, kid, owner)["allowed"] is False


def test_unlocks_expire_and_lock_when_idle(db, home, monkeypatch):
    clock = {"now": 1_000_000.0}
    monkeypatch.setattr(life, "_clock", lambda: clock["now"])
    kid, dad = home["kid"], home["dad"]
    opened = life.unlock(db, kid)
    lasts, idle = life.UNLOCK_RULES["child"]
    assert opened["idle_seconds"] == idle and opened["expires_at"] == int(
        clock["now"] + lasts
    )
    assert (
        life.UNLOCK_RULES["child"]
        < life.UNLOCK_RULES["teen"]
        < life.UNLOCK_RULES["adult"]
    )
    header = opened["header"]
    assert life.current(Req(header), db)["id"] == kid["id"]
    # Used every few minutes, it stays open until it runs out.
    for _ in range(int(lasts // (idle - 60))):
        clock["now"] += idle - 60
        if clock["now"] - 1_000_000 >= lasts:
            break
        assert life.current(Req(header), db)["id"] == kid["id"]
    clock["now"] = 1_000_000 + lasts + 1
    with pytest.raises(life.ProfileLocked):
        life.current(Req(header), db)
    # Left alone longer than the idle time, it locks; background checks do not keep it open.
    adult = life.unlock(db, dad)
    _lasts, adult_idle = life.UNLOCK_RULES["adult"]
    clock["now"] += adult_idle - 30
    assert life.current(Req(adult["header"]), db, touch=False)["id"] == dad["id"]
    clock["now"] += 60
    with pytest.raises(life.ProfileLocked):
        life.current(Req(adult["header"]), db, touch=False)
    # Locking this browser ends that unlock only; a PIN change ends all of them.
    first, second = life.unlock(db, dad), life.unlock(db, dad)
    life.lock(db, dad["id"], first["header"].split(".", 1)[1])
    with pytest.raises(life.ProfileLocked):
        life.current(Req(first["header"]), db)
    assert life.current(Req(second["header"]), db)["id"] == dad["id"]
    life.set_pin(db, dad, dad["id"], "8888", confirm="2222")
    with pytest.raises(life.ProfileLocked):
        life.current(Req(second["header"]), db)
    # A token of one profile never opens another.
    other = life.unlock(db, kid)["header"].split(".", 1)[1]
    with pytest.raises(life.ProfileLocked):
        life.current(Req(f"{dad['id']}.{other}"), db)


def test_parental_controls(db, home):
    owner, dad, kid, teen = home["owner"], home["dad"], home["kid"], home["teen"]
    assert life.controls(db, owner)["off"] == []
    assert set(life.controls(db, kid)["off"]) == {"dating", "stocks_real"}
    assert life.app_allowed(db, kid, "social") and not life.app_allowed(
        db, kid, "dating"
    )
    saved = life.set_controls(
        db, dad, kid["id"], {"apps": {"social": False, "ai": "0"}}
    )
    assert saved["apps"] == {
        "social": False,
        "videos": True,
        "ai": False,
        "stocks": True,
    }
    with pytest.raises(life.AppTurnedOff) as caught:
        life.require_app(db, life.get(db, kid["id"]), "social")
    assert caught.value.code == "app_off"
    life.require_app(db, life.get(db, kid["id"]), "videos")
    with pytest.raises(PermissionError):
        life.set_controls(db, kid, kid["id"], {"apps": {"social": True}})
    with pytest.raises(PermissionError):
        life.set_controls(db, teen, kid["id"], {"apps": {"social": True}})
    with pytest.raises(PermissionError):
        life.set_controls(db, owner, teen["id"], {"apps": {"dating": True}})
    with pytest.raises(ValueError):
        life.set_controls(db, owner, dad["id"], {"apps": {"social": False}})
    with pytest.raises(ValueError):
        life.set_controls(db, owner, teen["id"], {"apps": {"careers": False}})
    life.require_app(db, owner, "dating")  # grown-ups have everything
