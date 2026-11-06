"""The shared core of the life apps (Family, Health, Learn, Play, Planner): people, their preferences, Today
and live updates.

**People.** Every life app works for one person at a time: their medicines, their flashcards, their chores.
In the single-user build the account is a household and each person has a *profile* (like profiles on a shared
TV): the browser says which one is using it (``X-Aira-Profile: <id>[.<unlock token>]``), and a profile may have a
PIN so a child cannot open a parent's. In the hosted build the person is the signed-in member, whose profile is
made on first use; other members of the organization are the "family".

**Preferences** make the apps usable at any age: easy mode (big buttons, fewer menus), text size, high
contrast, reading pages aloud, less motion and the page to start on.

**Today** asks every life app for cards (a medicine due, today's chores, the next birthday) and **pulse** asks
for what changed since the browser last looked (badges, and alerts such as "time for your medicine") so pages
can update live. Apps register both:

    @life.today("care")
    def _today(connection, profile) -> list[card]
    @life.pulse("care")
    def _pulse(connection, profile, since) -> {"badge": int, "alerts": [alert]}

A card is ``{"title", "icon", "tone", "lines": [{"text", "sub", "done", "action": {"label", "method", "path",
"body"}}], "link", "empty"}``; an action's ``path`` must start with the app's own ``/api/<id>/``. An alert is
``{"id" (stable), "title", "body", "tone", "speak" (bool), "actions": [action]}``.

**Age and guardians.** A profile's age group comes from its birthday when it has one (under 13 a child, 13 to 17 a
teen, 18 and over an adult, or a senior if that was chosen), and it moves up by itself as the person gets older.
Only the household owner, or a guardian of that profile, changes a birthday or an age group; nobody changes their
own (a grown-up may still switch between adult and senior, which only changes the display). In the single-user
build every adult of the household is a guardian of its children and teens. A hosted member may give their birthday
once; after that the organization manages it.

**PINs** (:func:`set_pin`). Changing your own PIN needs your current one. A guardian changes or resets a child's or
a teen's PIN with the guardian's own PIN. Anyone else's PIN, the owner's included, needs the owner's PIN.

**Unlocking** (:func:`unlock`). Entering a PIN opens the profile for a while: the browser's token stops working
after :data:`UNLOCK_RULES` (shorter for children and teens) or after that long without use, and whenever the PIN
changes.

**Parental controls** (:func:`set_controls`). A guardian turns Social, Videos, AI chat and Stocks on or off for each
child or teen; Dating and Stocks with real money are always off for them.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import sqlite3
import time
from collections.abc import Callable
from datetime import date, datetime, timezone
from typing import Any

from aira import suite
from aira.suite import kit

AGE_GROUPS = ("child", "teen", "adult", "senior")
GROWN_UPS = ("adult", "senior")
TEEN_FROM, ADULT_FROM = 13, 18  # years: under 13 a child, 13 to 17 a teen
# How long an unlocked profile stays open (seconds): (at most, without use). Children and teens lock sooner.
UNLOCK_RULES: dict[str, tuple[int, int]] = {
    "child": (2 * 3600, 10 * 60),
    "teen": (4 * 3600, 15 * 60),
    "adult": (12 * 3600, 30 * 60),
    "senior": (12 * 3600, 30 * 60),
}
TOUCH_SECONDS = 60  # an unlock's "last used" is written at most this often
# What a guardian turns on or off for a child or a teen (on unless turned off), and what is always off for them.
CONTROLLED_APPS: dict[str, str] = {
    "social": "Social",
    "videos": "Videos",
    "ai": "AI chat",
    "stocks": "Stocks",
}
ALWAYS_OFF_FOR_YOUNG: dict[str, str] = {
    "dating": "Dating",
    "stocks_real": "Stocks with real money",
}
TEXT_SIZES = ("normal", "large", "xlarge")
EMOJIS = (
    "🙂",
    "😀",
    "🧑",
    "👩",
    "👨",
    "👵",
    "👴",
    "👧",
    "👦",
    "🧒",
    "🐱",
    "🐶",
    "🌻",
    "⭐",
    "🚀",
    "🎵",
)
COLORS = (
    "#2563eb",
    "#16a34a",
    "#db2777",
    "#ea580c",
    "#7c3aed",
    "#0891b2",
    "#ca8a04",
    "#475569",
)
PREF_DEFAULTS: dict[str, Any] = {
    "easy_mode": False,
    "text_size": "normal",
    "contrast": False,
    "read_aloud": False,
    "reduce_motion": False,
    "start_page": "",
    "language": "",  # the screens' language (aira/i18n.py); empty = this browser's choice
}
MAX_PROFILES = 12

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS life_profiles (
        id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        emoji TEXT NOT NULL DEFAULT '🙂',
        color TEXT NOT NULL DEFAULT '#2563eb',
        age_group TEXT NOT NULL DEFAULT 'adult',
        birthday TEXT NOT NULL DEFAULT '',
        email TEXT NOT NULL DEFAULT '',
        pin_hash TEXT NOT NULL DEFAULT '',
        owner INTEGER NOT NULL DEFAULT 0,
        member_ref TEXT,
        prefs TEXT NOT NULL DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        archived_at TEXT
    )""",
    "CREATE UNIQUE INDEX IF NOT EXISTS life_profiles_member ON life_profiles (member_ref) WHERE member_ref IS NOT NULL",
)
SCHEMA_V2 = (
    # an unlocked profile in one browser: the token is kept only as its hash; times are seconds since 1970
    """CREATE TABLE IF NOT EXISTS life_unlocks (
        token_hash TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        created REAL NOT NULL,
        expires REAL NOT NULL,
        idle REAL NOT NULL,
        used REAL NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS life_unlocks_profile ON life_unlocks (profile_id)",
    # parental controls of a child or a teen: {"social": false, ...}; an app not named is on
    """CREATE TABLE IF NOT EXISTS life_controls (
        profile_id TEXT PRIMARY KEY,
        apps TEXT NOT NULL DEFAULT '{}',
        updated_by TEXT NOT NULL DEFAULT '',
        updated_at TEXT NOT NULL
    )""",
)


class ProfileRequired(PermissionError):
    """The browser has not said which person is using the app (the picker is shown)."""

    code = "profile_required"


class ProfileLocked(PermissionError):
    """The chosen profile has a PIN and the browser has not unlocked it."""

    code = "profile_locked"


class GrownUpsOnly(PermissionError):
    """A child's or a teen's profile asked for something only the household's adults may open (money, customers)."""

    code = "adult_only"


class AppTurnedOff(PermissionError):
    """A guardian turned this app off for this child or teen (or it is never for under-18s)."""

    code = "app_off"


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "life.v1", SCHEMA)
    kit.ensure(connection, "life.v2", SCHEMA_V2)


def _clock() -> float:
    return time.time()


# ---------------------------------------------------------------------------
# age from the birthday
# ---------------------------------------------------------------------------


def _today() -> date:
    return datetime.now(timezone.utc).date()


def age_on(birthday: str, today: date | None = None) -> int | None:
    """Whole years since the birthday, or None without a (valid) birthday."""
    try:
        born = date.fromisoformat(str(birthday or ""))
    except ValueError:
        return None
    now = today or _today()
    return now.year - born.year - ((now.month, now.day) < (born.month, born.day))


def group_for(birthday: str, wanted: str = "", today: date | None = None) -> str | None:
    """The age group a birthday gives (None without one). A grown-up keeps "senior" when that was chosen."""
    years = age_on(birthday, today)
    if years is None:
        return None
    if years < TEEN_FROM:
        return "child"
    if years < ADULT_FROM:
        return "teen"
    return "senior" if wanted == "senior" else "adult"


def _aged(
    connection: sqlite3.Connection, profile: dict[str, Any] | None
) -> dict[str, Any] | None:
    """The profile with its age group following its birthday (a child becomes a teen on their 13th birthday)."""
    if not profile:
        return profile
    derived = group_for(profile.get("birthday") or "", profile.get("age_group") or "")
    if profile.get("owner") and derived not in (None, *GROWN_UPS):
        return profile  # the household owner is an adult (an old, wrong birthday is fixed by editing it)
    if derived and derived != profile.get("age_group"):
        connection.execute(
            "UPDATE life_profiles SET age_group = ?, updated_at = ? WHERE id = ?",
            (derived, kit.now(), profile["id"]),
        )
        connection.commit()
        profile["age_group"] = derived
    return profile


# ---------------------------------------------------------------------------
# profiles
# ---------------------------------------------------------------------------


def _row(row: dict[str, Any] | None) -> dict[str, Any] | None:
    if not row:
        return None
    row["prefs"] = {**PREF_DEFAULTS, **(kit.loads(row.get("prefs"), {}) or {})}
    row["owner"] = bool(row.get("owner"))
    return row


def public(profile: dict[str, Any]) -> dict[str, Any]:
    """What other people in the household see of a profile (never the PIN)."""
    return {
        "id": profile["id"],
        "name": profile["name"],
        "emoji": profile["emoji"],
        "color": profile["color"],
        "age_group": profile["age_group"],
        "birthday": profile["birthday"],
        "owner": bool(profile["owner"]),
        "has_pin": bool(profile.get("pin_hash")),
        "hosted": bool(profile.get("member_ref")),
    }


def mine(profile: dict[str, Any]) -> dict[str, Any]:
    """The profile as its own person sees it."""
    return {
        **public(profile),
        "email": profile.get("email", ""),
        "prefs": profile["prefs"],
    }


def profiles(
    connection: sqlite3.Connection, *, include_archived: bool = False
) -> list[dict[str, Any]]:
    ensure(connection)
    where = "" if include_archived else "WHERE archived_at IS NULL"
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM life_profiles {where} ORDER BY owner DESC, created_at",
    )  # noqa: S608
    return [p for p in (_aged(connection, _row(r)) for r in rows) if p]


def get(connection: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    ensure(connection)
    found = _aged(
        connection,
        _row(
            kit.fetch_one(
                connection,
                "SELECT * FROM life_profiles WHERE id = ? AND archived_at IS NULL",
                (profile_id,),
            )
        ),
    )
    if not found:
        raise LookupError("That person was not found.")
    return found


def name_of(connection: sqlite3.Connection, profile_id: str) -> str:
    try:
        return str(get(connection, profile_id)["name"])
    except LookupError:
        return "Someone"


def is_adult(profile: dict[str, Any]) -> bool:
    return profile.get("age_group") in ("adult", "senior")


def require_grown_up(
    request: Any,
    connection: sqlite3.Connection,
    message: str = "This is for the grown-ups in your household. Ask one of them to open it.",
) -> None:
    """Refuse a child's or a teen's household profile (the single-user build: business apps, the Business hub).

    Hosted members are adults (their organization decides what they may do), and a request that names no profile is
    the account itself, so both pass."""
    if suite.actor() is not None:
        return
    header = (
        str(request.header("X-Aira-Profile") or "").strip()
        if hasattr(request, "header")
        else ""
    )
    if not header:
        return
    if not is_adult(current(request, connection)):
        raise GrownUpsOnly(message)


def can_manage_household(profile: dict[str, Any]) -> bool:
    """Adding people, editing their names and pictures: the owner, or another adult in the single-user build."""
    if profile.get("member_ref"):
        return False  # hosted: members are managed by the organization, not here
    return bool(profile.get("owner")) or is_adult(profile)


def is_young(profile: dict[str, Any]) -> bool:
    return profile.get("age_group") in ("child", "teen")


def is_guardian(actor: dict[str, Any], target: dict[str, Any]) -> bool:
    """A guardian of a child's or a teen's profile: an adult of the same household (the single-user build; hosted
    members have no guardians yet)."""
    return (
        actor.get("id") != target.get("id")
        and is_young(target)
        and not target.get("member_ref")
        and can_manage_household(actor)
    )


def _owner_of(connection: sqlite3.Connection) -> dict[str, Any] | None:
    return next((p for p in profiles(connection) if p["owner"]), None)


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()


def check_pin(profile: dict[str, Any], pin: str) -> bool:
    if not profile.get("pin_hash"):
        return True
    return hmac.compare_digest(
        profile["pin_hash"], _hash_pin(str(pin or ""), profile["id"])
    )


def _clean(
    raw: dict[str, Any], current: dict[str, Any] | None = None
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    birthday = kit.day(merged.get("birthday"), "a birthday")
    if birthday and birthday > _today().isoformat():
        raise ValueError("A birthday cannot be in the future.")
    wanted = kit.choice(
        merged.get("age_group"), "the age group", AGE_GROUPS, default="adult"
    )
    values = {
        "name": kit.text(merged.get("name"), "a name", max_len=40, required=True),
        "emoji": kit.text(merged.get("emoji") or "🙂", "an emoji", max_len=8) or "🙂",
        "color": kit.color(merged.get("color"), default=COLORS[0]),
        # with a birthday the age group follows it, whatever the browser sent
        "age_group": group_for(birthday, wanted) or wanted,
        "birthday": birthday,
        "email": kit.email(merged.get("email")),
    }
    return values


def _create(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    *,
    owner: bool = False,
    member_ref: str | None = None,
) -> dict[str, Any]:
    if len(profiles(connection)) >= MAX_PROFILES and not member_ref:
        raise ValueError(f"A household can have at most {MAX_PROFILES} people.")
    count = len(profiles(connection))
    values = _clean(
        {
            **raw,
            "emoji": raw.get("emoji") or "🙂",
            "color": raw.get("color") or COLORS[count % len(COLORS)],
        }
    )
    if not raw.get("emoji"):
        values["emoji"] = {"child": "🧒", "teen": "🧑", "adult": "🙂", "senior": "👵"}[
            values["age_group"]
        ]
    if owner and values["age_group"] not in GROWN_UPS:
        raise ValueError("The household owner is an adult.")
    profile_id = kit.new_id()
    prefs = {**PREF_DEFAULTS}
    if values["age_group"] == "senior":
        prefs.update(
            {"easy_mode": True, "text_size": "large"}
        )  # a gentle start; they can change it
    if values["age_group"] == "child":
        prefs.update({"easy_mode": True})
    connection.execute(
        """INSERT INTO life_profiles (id, name, emoji, color, age_group, birthday, email, owner, member_ref, prefs, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            profile_id,
            values["name"],
            values["emoji"],
            values["color"],
            values["age_group"],
            values["birthday"],
            values["email"],
            1 if owner else 0,
            member_ref,
            kit.dumps(prefs),
            kit.now(),
            kit.now(),
        ),
    )
    connection.commit()
    return get(connection, profile_id)


def add(
    connection: sqlite3.Connection, actor: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    if not can_manage_household(actor):
        raise PermissionError("Ask an adult in the household to add people.")
    profile = _create(connection, raw)
    if raw.get("pin"):
        # a new person's first PIN, set by the grown-up adding them
        _write_pin(connection, profile["id"], _valid_pin(raw["pin"]))
        profile = get(connection, profile["id"])
    kit.log(
        connection,
        "life",
        "profile",
        f"{profile['name']} joined the household",
        actor=actor["name"],
    )
    connection.commit()
    return profile


AGE_NOT_YOURS = "Ask a parent or the household owner to change an age or a birthday."


def may_change_age(actor: dict[str, Any], target: dict[str, Any]) -> bool:
    """The owner, or a guardian of that profile (never the person themselves; hosted members: see :func:`update`)."""
    if target.get("member_ref"):
        return False
    if actor.get("owner") and not actor.get("member_ref"):
        return True
    return is_guardian(actor, target)


def update(
    connection: sqlite3.Connection,
    actor: dict[str, Any],
    profile_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    target = get(connection, profile_id)
    if target["id"] != actor["id"] and not can_manage_household(actor):
        raise PermissionError("You can change your own profile only.")
    if target.get("member_ref") and "email" in raw:
        raw = {
            k: v for k, v in raw.items() if k != "email"
        }  # a member's address is their sign-in
    values = _clean(raw, target)
    birthday_changed = values["birthday"] != (target.get("birthday") or "")
    group_changed = values["age_group"] != target["age_group"] and not (
        values["age_group"] in GROWN_UPS and target["age_group"] in GROWN_UPS
    )  # adult and senior differ only in how the screens look: a grown-up switches freely
    if birthday_changed or group_changed:
        first_hosted_birthday = (
            bool(target.get("member_ref"))
            and target["id"] == actor["id"]
            and not target.get("birthday")
            and birthday_changed
        )
        if not (first_hosted_birthday or may_change_age(actor, target)):
            raise PermissionError(
                "Your organization manages your birthday once it is set."
                if target.get("member_ref")
                else AGE_NOT_YOURS
            )
    if target["owner"] and values["age_group"] not in GROWN_UPS:
        raise ValueError("The household owner is an adult.")
    kit.update_row(
        connection, "life_profiles", profile_id, {**values, "updated_at": kit.now()}
    )
    if values["age_group"] != target["age_group"]:
        end_unlocks(connection, profile_id)  # the new group's unlock rules apply from the next PIN
    connection.commit()
    return get(connection, profile_id)


def remove(
    connection: sqlite3.Connection, actor: dict[str, Any], profile_id: str
) -> None:
    target = get(connection, profile_id)
    if not can_manage_household(actor):
        raise PermissionError("Ask an adult in the household to remove people.")
    if target["owner"]:
        raise ValueError("The household owner cannot be removed.")
    if target.get("member_ref"):
        raise ValueError(
            "A member of the organization is removed by its administrators."
        )
    kit.update_row(
        connection,
        "life_profiles",
        profile_id,
        {"archived_at": kit.now(), "updated_at": kit.now()},
    )
    end_unlocks(connection, profile_id)
    connection.commit()


def archived(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    """People removed with "Archive": hidden everywhere, their data kept until someone erases it."""
    return [p for p in profiles(connection, include_archived=True) if p.get("archived_at")]


def erase(
    connection: sqlite3.Connection,
    actor: dict[str, Any],
    profile_id: str,
    *,
    pin: str,
) -> dict[str, Any]:
    """Remove a person with "Erase everything": every app's data about them is deleted, and what a business must
    keep (an order they placed) is anonymised (:mod:`aira.erasure`). Archived people can be erased too.

    An adult of the household asks, and the household owner's PIN confirms it; the owner needs a PIN for this (an
    erasure cannot be undone). Returns the erasure's receipt (counts per app, nothing about the person)."""
    ensure(connection)
    target = _row(
        kit.fetch_one(
            connection, "SELECT * FROM life_profiles WHERE id = ?", (profile_id,)
        )
    )
    if not target:
        raise LookupError("That person was not found.")
    if not can_manage_household(actor):
        raise PermissionError("Ask an adult in the household to erase people.")
    if target["owner"]:
        raise ValueError(
            "The household owner cannot be erased here. Delete the account in Settings instead."
        )
    if target.get("member_ref"):
        raise ValueError(
            "A member of the organization is removed by its administrators."
        )
    owner = _owner_of(connection)
    if owner is None or not owner.get("pin_hash"):
        raise ValueError(
            "Erasing everything needs the household owner's PIN. Set a PIN for the owner first."
        )
    if not check_pin(owner, pin):
        raise WrongPin(
            f"Type {owner['name']}'s PIN (the household owner) to erase everything.",
            owner["id"],
        )
    from aira import erasure

    end_unlocks(connection, profile_id)
    connection.commit()
    person = erasure.Person(
        profile_id=target["id"],
        emails=(target.get("email") or "",),
        names=(target.get("name") or "",),
    )
    return erasure.run_erasure(
        connection, person, reason="profile_erased", actor="household"
    )


# ---------------------------------------------------------------------------
# PINs: who confirms a change, and unlocking a profile for a while
# ---------------------------------------------------------------------------

PIN_RULE_TEXT = (
    "To change your own PIN, type your current one. A parent changes a child's or a teen's PIN with their own PIN. "
    "Anyone else's PIN, the owner's too, needs the owner's PIN."
)


def pin_confirmer(
    connection: sqlite3.Connection, actor: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any] | None:
    """Whose PIN confirms ``actor`` changing ``target``'s PIN: ``{"by": "self"|"guardian"|"owner", "profile",
    "needed"}``, or None when the actor may not change it at all."""
    if target["id"] == actor["id"]:
        return {"by": "self", "profile": actor, "needed": bool(actor.get("pin_hash"))}
    if not can_manage_household(actor) or target.get("member_ref"):
        return None
    if is_guardian(actor, target):
        return {
            "by": "guardian",
            "profile": actor,
            "needed": bool(actor.get("pin_hash")),
        }
    owner = _owner_of(connection)
    if owner is None:
        return None
    return {"by": "owner", "profile": owner, "needed": bool(owner.get("pin_hash"))}


def pin_rule(
    connection: sqlite3.Connection, actor: dict[str, Any], target: dict[str, Any]
) -> dict[str, Any]:
    """What the browser shows above a PIN change: whose PIN to type, if any."""
    found = pin_confirmer(connection, actor, target)
    if found is None:
        return {"allowed": False, "needed": False, "by": "", "label": ""}
    label = {
        "self": "Your current PIN",
        "guardian": "Your own PIN, to confirm",
        "owner": f"{found['profile']['name']}'s PIN (the household owner)",
    }[found["by"]]
    return {
        "allowed": True,
        "needed": found["needed"],
        "by": found["by"],
        "label": label,
    }


def _valid_pin(pin: Any) -> str:
    pin = str(pin or "").strip()
    if pin and (not pin.isdigit() or not 4 <= len(pin) <= 8):
        raise ValueError("A PIN is 4 to 8 digits.")
    return pin


def _write_pin(connection: sqlite3.Connection, profile_id: str, pin: str) -> None:
    kit.update_row(
        connection,
        "life_profiles",
        profile_id,
        {
            "pin_hash": _hash_pin(pin, profile_id) if pin else "",
            "updated_at": kit.now(),
        },
    )
    end_unlocks(connection, profile_id)  # every browser that had it open asks for the new PIN
    connection.commit()


class WrongPin(PermissionError):
    """The confirming PIN was not right (the route counts these like wrong PINs at unlock)."""

    code = "pin_wrong"

    def __init__(self, message: str, profile_id: str) -> None:
        super().__init__(message)
        self.profile_id = profile_id


def set_pin(
    connection: sqlite3.Connection,
    actor: dict[str, Any],
    profile_id: str,
    pin: str,
    *,
    confirm: str = "",
) -> dict[str, Any]:
    """Change or remove (empty ``pin``) a PIN, confirmed by :func:`pin_confirmer`'s PIN (``confirm``)."""
    target = get(connection, profile_id)
    found = pin_confirmer(connection, actor, target)
    if found is None:
        raise PermissionError("You can change your own PIN only.")
    pin = _valid_pin(pin)
    if found["needed"] and not check_pin(found["profile"], confirm):
        raise WrongPin(
            {
                "self": "Type your current PIN to change it.",
                "guardian": "Type your own PIN to change this PIN.",
                "owner": f"Type {found['profile']['name']}'s PIN (the household owner) to change this PIN.",
            }[found["by"]],
            found["profile"]["id"],
        )
    _write_pin(connection, profile_id, pin)
    return get(connection, profile_id)


def _token_hash(token: str) -> str:
    return hashlib.sha256(str(token).encode("utf-8")).hexdigest()


def unlock(connection: sqlite3.Connection, profile: dict[str, Any]) -> dict[str, Any]:
    """Open a profile with a PIN for a while: ``{"header", "expires_at", "idle_seconds"}`` (seconds since 1970).
    A profile without a PIN needs no token."""
    ensure(connection)
    if not profile.get("pin_hash"):
        return {"header": profile["id"], "expires_at": None, "idle_seconds": None}
    lasts, idle = UNLOCK_RULES.get(
        profile.get("age_group") or "adult", UNLOCK_RULES["adult"]
    )
    now = _clock()
    connection.execute(
        "DELETE FROM life_unlocks WHERE expires < ? OR used + idle < ?", (now, now)
    )
    token = secrets.token_hex(16)
    connection.execute(
        "INSERT INTO life_unlocks (token_hash, profile_id, created, expires, idle, used) VALUES (?, ?, ?, ?, ?, ?)",
        (_token_hash(token), profile["id"], now, now + lasts, idle, now),
    )
    connection.commit()
    return {
        "header": f"{profile['id']}.{token}",
        "expires_at": int(now + lasts),
        "idle_seconds": idle,
    }


def unlocked(
    connection: sqlite3.Connection,
    profile: dict[str, Any],
    token: str,
    *,
    touch: bool = True,
) -> bool:
    """Whether ``token`` (from the X-Aira-Profile header) still opens this PIN-protected profile. ``touch`` counts
    the request as use (a background check, such as the pulse, does not keep a profile open)."""
    if not profile.get("pin_hash"):
        return True
    if not token:
        return False
    ensure(connection)
    found = kit.fetch_one(
        connection,
        "SELECT * FROM life_unlocks WHERE token_hash = ?",
        (_token_hash(token),),
    )
    if not found or found["profile_id"] != profile["id"]:
        return False
    now = _clock()
    if now > found["expires"] or now - found["used"] > found["idle"]:
        connection.execute(
            "DELETE FROM life_unlocks WHERE token_hash = ?", (found["token_hash"],)
        )
        connection.commit()
        return False
    if touch and now - found["used"] >= TOUCH_SECONDS:
        connection.execute(
            "UPDATE life_unlocks SET used = ? WHERE token_hash = ?",
            (now, found["token_hash"]),
        )
        connection.commit()
    return True


def end_unlocks(connection: sqlite3.Connection, profile_id: str) -> None:
    """Lock this profile in every browser (its PIN or age group changed, it was removed)."""
    ensure(connection)
    connection.execute("DELETE FROM life_unlocks WHERE profile_id = ?", (profile_id,))


def lock(connection: sqlite3.Connection, profile_id: str, token: str) -> None:
    """Lock the profile in this browser only (Switch person, or the browser's idle lock)."""
    ensure(connection)
    connection.execute(
        "DELETE FROM life_unlocks WHERE token_hash = ? AND profile_id = ?",
        (_token_hash(token), profile_id),
    )
    connection.commit()


# ---------------------------------------------------------------------------
# parental controls
# ---------------------------------------------------------------------------


def controls(
    connection: sqlite3.Connection, profile: dict[str, Any]
) -> dict[str, Any]:
    """Which controlled apps are on for this person, and what is always off. Grown-ups have everything on."""
    ensure(connection)
    young = is_young(profile)
    stored: dict[str, Any] = {}
    if young:
        found = kit.fetch_one(
            connection,
            "SELECT apps FROM life_controls WHERE profile_id = ?",
            (profile["id"],),
        )
        stored = (kit.loads(found["apps"], {}) or {}) if found else {}
    apps = {
        app: (bool(stored.get(app, True)) if young else True)
        for app in CONTROLLED_APPS
    }
    always_off = list(ALWAYS_OFF_FOR_YOUNG) if young else []
    return {
        "young": young,
        "apps": apps,
        "always_off": always_off,
        "off": [app for app, on in apps.items() if not on] + always_off,
        "labels": {**CONTROLLED_APPS, **ALWAYS_OFF_FOR_YOUNG},
    }


def set_controls(
    connection: sqlite3.Connection,
    actor: dict[str, Any],
    profile_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """A guardian turns apps on or off for a child or a teen (``{"apps": {"social": false}}``)."""
    target = get(connection, profile_id)
    if not is_young(target):
        raise ValueError("Parental controls are for children and teens.")
    if not is_guardian(actor, target):
        raise PermissionError("Only a parent or guardian can change these.")
    wanted = raw.get("apps") if isinstance(raw.get("apps"), dict) else {}
    current = controls(connection, target)["apps"]
    for app, value in wanted.items():
        if app in ALWAYS_OFF_FOR_YOUNG:
            if kit.flag(value):
                raise PermissionError(
                    f"{ALWAYS_OFF_FOR_YOUNG[app]} is always off for under-18s."
                )
            continue
        if app not in CONTROLLED_APPS:
            raise ValueError("Choose an app from the list.")
        current[app] = kit.flag(value)
    connection.execute(
        """INSERT INTO life_controls (profile_id, apps, updated_by, updated_at) VALUES (?, ?, ?, ?)
        ON CONFLICT(profile_id) DO UPDATE SET apps = excluded.apps, updated_by = excluded.updated_by,
        updated_at = excluded.updated_at""",
        (target["id"], kit.dumps(current), actor["id"], kit.now()),
    )
    kit.log(
        connection,
        "life",
        "controls",
        f"{actor['name']} changed the apps {target['name']} can open",
        actor=actor["name"],
    )
    connection.commit()
    return controls(connection, target)


def app_allowed(
    connection: sqlite3.Connection, profile: dict[str, Any], app: str
) -> bool:
    if not is_young(profile):
        return True
    if app in ALWAYS_OFF_FOR_YOUNG:
        return False
    return app not in controls(connection, profile)["off"]


def require_app(
    connection: sqlite3.Connection, profile: dict[str, Any], app: str
) -> None:
    """Refuse an app a guardian turned off for this child or teen (403 ``app_off``)."""
    if app_allowed(connection, profile, app):
        return
    name = {**CONTROLLED_APPS, **ALWAYS_OFF_FOR_YOUNG}.get(app, app)
    if app in ALWAYS_OFF_FOR_YOUNG:
        raise AppTurnedOff(f"{name} is not for under-18s.")
    raise AppTurnedOff(
        f"A parent or guardian turned {name} off for you. Ask them if you need it."
    )


def save_prefs(
    connection: sqlite3.Connection, profile: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    prefs = {**profile["prefs"]}
    for key in ("easy_mode", "contrast", "read_aloud", "reduce_motion"):
        if key in raw:
            prefs[key] = kit.flag(raw[key])
    if "text_size" in raw:
        prefs["text_size"] = kit.choice(
            raw["text_size"], "the text size", TEXT_SIZES, default="normal"
        )
    if "language" in raw:
        from aira import i18n

        wanted = str(raw["language"] or "").strip().lower()
        if wanted and wanted not in i18n.CODES:
            raise ValueError(f"Choose a language: {', '.join(i18n.CODES)}.")
        prefs["language"] = wanted
    if "start_page" in raw:
        page = kit.text(raw["start_page"], "the start page", max_len=40)
        if page and not page.replace("-", "").isalnum():
            raise ValueError("Choose a page from the menu.")
        prefs["start_page"] = page
    kit.update_row(
        connection,
        "life_profiles",
        profile["id"],
        {"prefs": kit.dumps(prefs), "updated_at": kit.now()},
    )
    connection.commit()
    return prefs


# ---------------------------------------------------------------------------
# who is using this request
# ---------------------------------------------------------------------------


def current(
    request: Any, connection: sqlite3.Connection, *, touch: bool = True
) -> dict[str, Any]:
    """The person this request is for. Raises ProfileRequired / ProfileLocked (403 with a code) when the
    browser must show the picker or ask for a PIN (also when the unlock expired or was idle too long).
    ``touch=False`` for background checks that must not keep an unlocked profile open."""
    ensure(connection)
    member = suite.actor()
    if member and member.get("member_id"):
        # The hosted build: the signed-in member is the person. Their profile follows its birthday like any other
        # (group_for); members start as adults and give their birthday once (update).
        found = _aged(
            connection,
            _row(
                kit.fetch_one(
                    connection,
                    "SELECT * FROM life_profiles WHERE member_ref = ?",
                    (member["member_id"],),
                )
            ),
        )
        if found and found.get("archived_at"):
            raise PermissionError("Your profile was removed.")
        if found:
            return found
        name = (
            str(member.get("name") or str(member.get("email") or "Me").split("@")[0])[
                :40
            ]
            or "Me"
        )
        first = not profiles(connection)
        return _create(
            connection,
            {
                "name": name.replace(".", " ").title(),
                "email": member.get("email", ""),
                "age_group": "adult",
            },
            owner=first,
            member_ref=str(member["member_id"]),
        )
    everyone = profiles(connection)
    if not everyone:
        user = getattr(request, "user", None) or {}
        email = str(user.get("email") or "")
        name = (email.split("@")[0].replace(".", " ").title() if email else "Me")[
            :40
        ] or "Me"
        return _create(
            connection, {"name": name, "email": email, "age_group": "adult"}, owner=True
        )
    header = (
        str(request.header("X-Aira-Profile") or "").strip()
        if hasattr(request, "header")
        else ""
    )
    if header:
        profile_id, _, token = header.partition(".")
        chosen = next((p for p in everyone if p["id"] == profile_id), None)
        if chosen is None:
            raise ProfileRequired("Choose who is using Monosage.")
        if chosen.get("pin_hash") and not unlocked(
            connection, chosen, token, touch=touch
        ):
            raise ProfileLocked(f"Enter {chosen['name']}'s PIN.")
        return chosen
    owner = next((p for p in everyone if p["owner"]), None)
    if len(everyone) == 1 and owner and not owner.get("pin_hash"):
        return owner
    raise ProfileRequired("Choose who is using Monosage.")


# ---------------------------------------------------------------------------
# Today and pulse registries
# ---------------------------------------------------------------------------

TodayFn = Callable[[sqlite3.Connection, dict[str, Any]], list[dict[str, Any]]]
PulseFn = Callable[[sqlite3.Connection, dict[str, Any], str], dict[str, Any]]
TODAY: dict[str, TodayFn] = {}
PULSE: dict[str, PulseFn] = {}


def today(app: str) -> Callable[[TodayFn], TodayFn]:
    def wrap(fn: TodayFn) -> TodayFn:
        TODAY[app] = fn
        return fn

    return wrap


def pulse(app: str) -> Callable[[PulseFn], PulseFn]:
    def wrap(fn: PulseFn) -> PulseFn:
        PULSE[app] = fn
        return fn

    return wrap


def card(
    app: str,
    title: str,
    *,
    lines: list[dict[str, Any]] | None = None,
    link: str = "",
    tone: str = "",
    empty: str = "",
    icon: str = "",
) -> dict[str, Any]:
    return {
        "app": app,
        "title": title,
        "icon": icon or app,
        "tone": tone,
        "lines": lines or [],
        "link": link or f"#/{app}",
        "empty": empty,
    }


def action(
    label: str, path: str, *, method: str = "POST", body: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not path.startswith("/api/"):
        raise ValueError("An action path starts with /api/.")
    return {"label": label, "method": method, "path": path, "body": body or {}}


def greeting(hour: int) -> str:
    if hour < 5:
        return "Good night"
    if hour < 12:
        return "Good morning"
    if hour < 18:
        return "Good afternoon"
    return "Good evening"
