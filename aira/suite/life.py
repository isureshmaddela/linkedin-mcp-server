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
"""

from __future__ import annotations

import hashlib
import hmac
import sqlite3
from collections.abc import Callable
from typing import Any

from aira import suite
from aira.suite import kit

AGE_GROUPS = ("child", "teen", "adult", "senior")
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


class ProfileRequired(PermissionError):
    """The browser has not said which person is using the app (the picker is shown)."""

    code = "profile_required"


class ProfileLocked(PermissionError):
    """The chosen profile has a PIN and the browser has not unlocked it."""

    code = "profile_locked"


class GrownUpsOnly(PermissionError):
    """A child's or a teen's profile asked for something only the household's adults may open (money, customers)."""

    code = "adult_only"


def ensure(connection: sqlite3.Connection) -> None:
    kit.ensure_core(connection)
    kit.ensure(connection, "life.v1", SCHEMA)


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
    return [p for p in (_row(r) for r in rows) if p]


def get(connection: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    ensure(connection)
    found = _row(
        kit.fetch_one(
            connection,
            "SELECT * FROM life_profiles WHERE id = ? AND archived_at IS NULL",
            (profile_id,),
        )
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
    """Adding people, setting PINs for others: the owner, or another adult in the single-user build."""
    if profile.get("member_ref"):
        return False  # hosted: members are managed by the organization, not here
    return bool(profile.get("owner")) or is_adult(profile)


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", pin.encode("utf-8"), salt.encode("utf-8"), 120_000
    ).hex()


def unlock_token(profile: dict[str, Any]) -> str:
    """What the browser sends after the PIN was entered (it changes whenever the PIN changes)."""
    return hashlib.sha256(
        f"{profile['id']}:{profile.get('pin_hash', '')}".encode()
    ).hexdigest()[:32]


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
    values = {
        "name": kit.text(merged.get("name"), "a name", max_len=40, required=True),
        "emoji": kit.text(merged.get("emoji") or "🙂", "an emoji", max_len=8) or "🙂",
        "color": kit.color(merged.get("color"), default=COLORS[0]),
        "age_group": kit.choice(
            merged.get("age_group"), "the age group", AGE_GROUPS, default="adult"
        ),
        "birthday": kit.day(merged.get("birthday"), "a birthday"),
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
    group = kit.choice(
        raw.get("age_group"), "the age group", AGE_GROUPS, default="adult"
    )
    count = len(profiles(connection))
    raw = {
        **raw,
        "emoji": raw.get("emoji")
        or {"child": "🧒", "teen": "🧑", "adult": "🙂", "senior": "👵"}[group],
        "color": raw.get("color") or COLORS[count % len(COLORS)],
    }
    values = _clean(raw)
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
        set_pin(connection, actor, profile["id"], str(raw["pin"]))
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
    if target["owner"] and values["age_group"] not in ("adult", "senior"):
        raise ValueError("The household owner is an adult.")
    kit.update_row(
        connection, "life_profiles", profile_id, {**values, "updated_at": kit.now()}
    )
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
    connection.commit()


def set_pin(
    connection: sqlite3.Connection, actor: dict[str, Any], profile_id: str, pin: str
) -> dict[str, Any]:
    target = get(connection, profile_id)
    if target["id"] != actor["id"] and not can_manage_household(actor):
        raise PermissionError("You can change your own PIN only.")
    pin = str(pin or "").strip()
    if pin and (not pin.isdigit() or not 4 <= len(pin) <= 8):
        raise ValueError("A PIN is 4 to 8 digits.")
    kit.update_row(
        connection,
        "life_profiles",
        profile_id,
        {
            "pin_hash": _hash_pin(pin, profile_id) if pin else "",
            "updated_at": kit.now(),
        },
    )
    connection.commit()
    return get(connection, profile_id)


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


def current(request: Any, connection: sqlite3.Connection) -> dict[str, Any]:
    """The person this request is for. Raises ProfileRequired / ProfileLocked (403 with a code) when the
    browser must show the picker or ask for a PIN."""
    ensure(connection)
    member = suite.actor()
    if member and member.get("member_id"):
        found = _row(
            kit.fetch_one(
                connection,
                "SELECT * FROM life_profiles WHERE member_ref = ?",
                (member["member_id"],),
            )
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
            raise ProfileRequired("Choose who is using Aira.")
        if chosen.get("pin_hash") and not hmac.compare_digest(
            token, unlock_token(chosen)
        ):
            raise ProfileLocked(f"Enter {chosen['name']}'s PIN.")
        return chosen
    owner = next((p for p in everyone if p["owner"]), None)
    if len(everyone) == 1 and owner and not owner.get("pin_hash"):
        return owner
    raise ProfileRequired("Choose who is using Aira.")


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
