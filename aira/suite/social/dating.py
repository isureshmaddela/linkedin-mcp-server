"""Dating in Social: meet new people, adults only, with a separate profile that is off until the person turns it on.

The rules, in one place:

* **Adults only.** Only someone whose household profile (or hosted membership) is an adult or senior *and* who is 18
  or over by their birthday may open Dating. The birthday comes from their household profile when it has one;
  otherwise they enter it once (and confirm they are 18 or over). Children and teens are refused by every function
  here, and nobody under 18 is ever shown to anyone.
* **Opt-in and separate.** A dating profile (``so_dating_profiles``) has its own first name, photos, bio, three
  prompts, interests and preferences. People are known in Dating by the profile's own random id, never by their
  Social account, @name or display name; nothing of Dating appears on the Social profile, feed, chats list,
  badges, reminders or the data an organization exports for a member.
* **Who sees whom** (both ways, every time): only active profiles; never someone blocked on Social (either way);
  never a suspended account; the genders each wants to see and the age range each chose; never a Social friend
  when either of the two asked to hide from friends; never someone of the same household or organization when
  either asked for that (always, on this computer: a household is a family).
* **Likes and matches.** Like, pass or star (a like that is shown first, with a small daily allowance) each person
  once; a like back is a match, which opens a one-to-one chat in Social's chat system (``context = 'dating'``)
  that is allowed while the match lasts, even when the two are not friends. Unmatching, blocking or deleting the
  dating profile removes the chat for both. "Likes you" shows only how many and a hint of each (an initial and the
  interests you share): you meet them in Discover, where liking back makes the match; a pass is never told.
* **Pause** hides the profile from Discover and keeps matches and chats; **delete** removes the profile, every
  like and every match (and their chats).
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterable
from datetime import date, datetime, timedelta, timezone
from typing import Any

from aira.suite import kit
from aira.suite.social import photos, service, store
from aira.suite.social.store import new_id, row, rows, stamp

ADULT_GROUPS = ("adult", "senior")
MIN_AGE = 18
MAX_AGE = 99
LIKES_PER_DAY = 30
STARS_PER_DAY = 3
GENDERS = ("woman", "man", "nonbinary")
SHOW_ME = {
    "women": ("woman",),
    "men": ("man",),
    "everyone": GENDERS,
}
LOOKING_FOR = ("friendship", "casual", "relationship", "unsure")
# The questions a profile answers (three of them). Ids are stable; the browser shows the text.
PROMPTS = {
    "slow_sunday": "My ideal slow Sunday…",
    "small_joy": "A small thing that makes my day…",
    "talk_hours": "I could talk for hours about…",
    "recent_learn": "The last thing I learned that surprised me…",
    "friends_say": "My friends come to me for…",
    "show_you": "A place near me I'd love to show you…",
    "quietly_proud": "I'm quietly proud of…",
    "this_year": "Something I want to do this year…",
    "best_meal": "The best meal I've ever had…",
    "get_along": "We'll get along if you…",
    "comfort": "My comfort film, book or song…",
    "first_date": "A first date I'd say yes to…",
    "unpopular": "A harmless opinion I'll defend…",
    "green_flag": "Something that instantly makes me smile about a person…",
}
INTERESTS = (
    "hiking",
    "cooking",
    "baking",
    "music",
    "concerts",
    "films",
    "reading",
    "writing",
    "travel",
    "photography",
    "art",
    "gaming",
    "board_games",
    "fitness",
    "yoga",
    "running",
    "cycling",
    "swimming",
    "dancing",
    "coffee",
    "food",
    "gardening",
    "dogs",
    "cats",
    "volunteering",
    "technology",
    "theatre",
    "football",
    "camping",
    "languages",
    "fashion",
    "podcasts",
    "comedy",
    "science",
    "faith",
    "family",
)
MAX_INTERESTS = 10
MAX_PHOTOS = 6
CHAT_CONTEXT = "dating"
NOT_FOUND = "That profile was not found."
ADULTS_ONLY = "Dating is only for adults, 18 and over."

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS so_dating_profiles (
        id TEXT PRIMARY KEY,
        account_id TEXT NOT NULL UNIQUE,
        status TEXT NOT NULL DEFAULT 'active',
        first_name TEXT NOT NULL,
        birthday TEXT NOT NULL,
        gender TEXT NOT NULL,
        show_me TEXT NOT NULL,
        age_min INTEGER NOT NULL,
        age_max INTEGER NOT NULL,
        looking_for TEXT NOT NULL,
        bio TEXT NOT NULL DEFAULT '',
        prompts TEXT NOT NULL DEFAULT '[]',
        interests TEXT NOT NULL DEFAULT '[]',
        photos TEXT NOT NULL DEFAULT '[]',
        city TEXT NOT NULL DEFAULT '',
        height_cm INTEGER,
        hide_friends INTEGER NOT NULL DEFAULT 0,
        hide_home INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS so_dating_profiles_status ON so_dating_profiles (status, updated_at)",
    """CREATE TABLE IF NOT EXISTS so_dating_swipes (
        actor_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        action TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (actor_id, target_id)
    )""",
    "CREATE INDEX IF NOT EXISTS so_dating_swipes_target ON so_dating_swipes (target_id, action)",
    """CREATE TABLE IF NOT EXISTS so_dating_matches (
        id TEXT PRIMARY KEY,
        a_id TEXT NOT NULL,
        b_id TEXT NOT NULL,
        chat_id TEXT NOT NULL,
        a_seen INTEGER NOT NULL DEFAULT 0,
        b_seen INTEGER NOT NULL DEFAULT 0,
        created_at TEXT NOT NULL,
        UNIQUE (a_id, b_id)
    )""",
    """CREATE TABLE IF NOT EXISTS so_dating_daily (
        account_id TEXT NOT NULL,
        day TEXT NOT NULL,
        likes INTEGER NOT NULL DEFAULT 0,
        stars INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (account_id, day)
    )""",
    # An ended match's chat and both profiles as they were, kept store.RETENTION_DAYS for moderators only (a report
    # filed later still has its evidence), then purged by Social's background task.
    """CREATE TABLE IF NOT EXISTS so_dating_ended (
        id TEXT PRIMARY KEY,
        match_id TEXT NOT NULL,
        a_id TEXT NOT NULL,
        b_id TEXT NOT NULL,
        reason TEXT NOT NULL,
        transcript TEXT NOT NULL DEFAULT '[]',
        profiles TEXT NOT NULL DEFAULT '{}',
        ended_at TEXT NOT NULL,
        purge_after TEXT NOT NULL
    )""",
    "CREATE INDEX IF NOT EXISTS so_dating_ended_pair ON so_dating_ended (a_id, b_id)",
)


def ensure(sc: sqlite3.Connection) -> None:
    """Dating's tables live in Social's database; made on first use (the file may be new)."""
    if not row(
        sc,
        "SELECT 1 AS x FROM sqlite_master WHERE type = 'table' AND name = 'so_dating_ended'",
    ):
        for statement in SCHEMA:
            sc.execute(statement)
        sc.commit()


# ---------------------------------------------------------------------------
# age and who may use Dating
# ---------------------------------------------------------------------------


def _today() -> date:
    return datetime.now(timezone.utc).date()


def age_of(birthday: str, today: date | None = None) -> int | None:
    try:
        born = date.fromisoformat(str(birthday or ""))
    except ValueError:
        return None
    now = today or _today()
    return now.year - born.year - ((now.month, now.day) < (born.month, born.day))


def profile_birthday(ident: dict[str, Any]) -> str:
    """The birthday the household profile (or hosted membership) keeps, if any."""
    return str((ident.get("profile") or {}).get("birthday") or "")


def adult_group(ident: dict[str, Any], acct: dict[str, Any]) -> bool:
    group = (ident.get("profile") or {}).get("age_group") or "adult"
    return group in ADULT_GROUPS and acct.get("age_group") in ADULT_GROUPS


def eligible(ident: dict[str, Any], acct: dict[str, Any], birthday: str = "") -> bool:
    if not adult_group(ident, acct):
        return False
    known = profile_birthday(ident) or birthday
    years = age_of(known) if known else None
    return years is None or years >= MIN_AGE


def require_adult(
    ident: dict[str, Any], acct: dict[str, Any], birthday: str = ""
) -> None:
    if not eligible(ident, acct, birthday):
        raise PermissionError(ADULTS_ONLY)


# ---------------------------------------------------------------------------
# the dating profile
# ---------------------------------------------------------------------------


def _profile(found: dict[str, Any] | None) -> dict[str, Any] | None:
    if not found:
        return None
    found["prompts"] = store.loads(found.get("prompts"), [])
    found["interests"] = store.loads(found.get("interests"), [])
    found["photos"] = store.loads(found.get("photos"), [])
    found["hide_friends"] = bool(found.get("hide_friends"))
    found["hide_home"] = bool(found.get("hide_home"))
    return found


def profile_of(sc: sqlite3.Connection, account_id: str) -> dict[str, Any] | None:
    return _profile(
        row(sc, "SELECT * FROM so_dating_profiles WHERE account_id = ?", (account_id,))
    )


def profile_by_id(sc: sqlite3.Connection, profile_id: str) -> dict[str, Any]:
    found = _profile(
        row(
            sc,
            "SELECT * FROM so_dating_profiles WHERE id = ?",
            (store.check_id(profile_id, "profile"),),
        )
    )
    if not found:
        raise LookupError(NOT_FOUND)
    return found


def gate(
    sc: sqlite3.Connection, ident: dict[str, Any], acct: dict[str, Any]
) -> dict[str, Any] | None:
    """Refuse anyone under 18 (by age group and by birthday); the dating profile, if any, kept in step with the
    birthday the household profile holds (so a corrected birthday hides a profile at once)."""
    ensure(sc)
    found = profile_of(sc, acct["id"])
    known = profile_birthday(ident)
    if found and known and known != found["birthday"]:
        sc.execute(
            "UPDATE so_dating_profiles SET birthday = ? WHERE id = ?",
            (known, found["id"]),
        )
        sc.commit()
        found["birthday"] = known
    if found and not adult_group(ident, acct) and found["status"] == "active":
        # no longer an adult on their household profile: hidden from everyone at once
        sc.execute(
            "UPDATE so_dating_profiles SET status = 'paused' WHERE id = ?",
            (found["id"],),
        )
        sc.commit()
    require_adult(ident, acct, found["birthday"] if found else "")
    return found


def mine(
    sc: sqlite3.Connection, ident: dict[str, Any], acct: dict[str, Any]
) -> dict[str, Any] | None:
    """The person's own dating profile (or None)."""
    return gate(sc, ident, acct)


def _prompts(value: Any, sc: sqlite3.Connection) -> list[dict[str, str]]:
    items = value if isinstance(value, list) else []
    if len(items) != 3:
        raise ValueError("Answer three prompts.")
    out: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise ValueError("Each prompt is a question and your answer.")
        prompt_id = str(item.get("id") or "").strip()
        if prompt_id not in PROMPTS:
            raise ValueError("Choose each prompt from the list.")
        if any(p["id"] == prompt_id for p in out):
            raise ValueError("Choose three different prompts.")
        answer = service.filtered(
            sc, kit.text(item.get("answer"), "an answer", max_len=200, required=True)
        )
        out.append({"id": prompt_id, "answer": answer})
    return out


def _interests(value: Any) -> list[str]:
    items = (
        value
        if isinstance(value, list)
        else [v for v in str(value or "").split(",") if v.strip()]
    )
    out: list[str] = []
    for item in items:
        key = str(item or "").strip().lower()
        if key not in INTERESTS:
            raise ValueError("Choose interests from the list.")
        if key not in out:
            out.append(key)
    if len(out) > MAX_INTERESTS:
        raise ValueError(f"Choose up to {MAX_INTERESTS} interests.")
    return out


def _height(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return kit.integer(value, "your height in centimetres", minimum=120, maximum=230)


def save(
    sc: sqlite3.Connection,
    ident: dict[str, Any],
    acct: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Make the dating profile (turning Dating on) or change it. Every field is checked again on each save."""
    require_adult(ident, acct)
    service._active(acct)
    ensure(sc)
    existing = profile_of(sc, acct["id"])
    merged = {**(existing or {}), **raw}
    birthday = profile_birthday(ident) or kit.day(
        merged.get("birthday"), "your birthday", required=True
    )
    years = age_of(birthday)
    if years is None or years < MIN_AGE:
        raise PermissionError(ADULTS_ONLY)
    if years > 120:
        raise ValueError("Check your birthday.")
    if not existing and not kit.flag(raw.get("confirm_adult")):
        raise ValueError("Confirm that you are 18 or over.")
    age_min = kit.integer(
        merged.get("age_min"),
        "the youngest age",
        default=max(MIN_AGE, years - 8),
        minimum=MIN_AGE,
        maximum=MAX_AGE,
    )
    age_max = kit.integer(
        merged.get("age_max"),
        "the oldest age",
        default=min(MAX_AGE, years + 8),
        minimum=MIN_AGE,
        maximum=MAX_AGE,
    )
    if age_min > age_max:
        raise ValueError("The youngest age must not be above the oldest.")
    hide_home = kit.flag(merged.get("hide_home", True))
    if not ident.get("hosted"):
        hide_home = (
            True  # on this computer a household is a family: never shown to each other
        )
    values = {
        "first_name": service.filtered(
            sc,
            kit.text(
                merged.get("first_name")
                or str(acct.get("display_name") or "").split(" ")[0],
                "your first name",
                max_len=30,
                required=True,
            ),
        ),
        "birthday": birthday,
        "gender": kit.choice(merged.get("gender"), "your gender", GENDERS),
        "show_me": kit.choice(
            merged.get("show_me"), "who you want to meet", tuple(SHOW_ME)
        ),
        "age_min": age_min,
        "age_max": age_max,
        "looking_for": kit.choice(
            merged.get("looking_for"),
            "what you are looking for",
            LOOKING_FOR,
            default="unsure",
        ),
        "bio": service.filtered(
            sc,
            kit.text(
                merged.get("bio"), "a few words about you", max_len=300, multiline=True
            ),
        ),
        "prompts": store.dumps(_prompts(merged.get("prompts"), sc)),
        "interests": store.dumps(_interests(merged.get("interests"))),
        # https links, or photos uploaded from the device (``media:<id>``, only the person's own)
        "photos": store.dumps(
            service._photos(
                merged.get("photos") or [], limit=MAX_PHOTOS, sc=sc, owner=acct["id"]
            )
        ),
        "city": kit.text(merged.get("city"), "your city or area", max_len=60),
        "height_cm": _height(merged.get("height_cm")),
        "hide_friends": 1 if kit.flag(merged.get("hide_friends")) else 0,
        "hide_home": 1 if hide_home else 0,
        "updated_at": stamp(),
    }
    if existing:
        columns = list(values)
        sc.execute(
            f"UPDATE so_dating_profiles SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?",  # noqa: S608 - fixed names
            (*[values[c] for c in columns], existing["id"]),
        )
    else:
        values.update(
            {
                "id": new_id(),
                "account_id": acct["id"],
                "status": "active",
                "created_at": values["updated_at"],
            }
        )
        columns = list(values)
        sc.execute(
            f"INSERT INTO so_dating_profiles ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",  # noqa: S608
            tuple(values[c] for c in columns),
        )
    sc.commit()
    return profile_of(sc, acct["id"]) or {}


def pause(
    sc: sqlite3.Connection, ident: dict[str, Any], acct: dict[str, Any], on: bool
) -> dict[str, Any]:
    found = _require_profile(sc, ident, acct)
    sc.execute(
        "UPDATE so_dating_profiles SET status = ?, updated_at = ? WHERE id = ?",
        ("paused" if on else "active", stamp(), found["id"]),
    )
    sc.commit()
    return profile_of(sc, acct["id"]) or {}


def delete(sc: sqlite3.Connection, ident: dict[str, Any], acct: dict[str, Any]) -> None:
    """Turn Dating off: the profile, every like and pass, every match and its chat are gone."""
    require_adult(ident, acct)
    ensure(sc)
    erase(sc, [acct["id"]])


def erase(sc: sqlite3.Connection, account_ids: Iterable[str]) -> None:
    """Remove these people's Dating (an account erased, a member removed, a profile deleted)."""
    ensure(sc)
    for account_id in [str(a) for a in account_ids if a]:
        for match in rows(
            sc,
            "SELECT * FROM so_dating_matches WHERE a_id = ? OR b_id = ?",
            (account_id, account_id),
        ):
            _end_match(sc, match, "deleted")
        for statement in (
            "DELETE FROM so_dating_swipes WHERE actor_id = ? OR target_id = ?",
            "DELETE FROM so_dating_daily WHERE account_id = ?",
            "DELETE FROM so_dating_profiles WHERE account_id = ?",
        ):
            sc.execute(statement, (account_id,) * statement.count("?"))
    sc.commit()


def _require_profile(
    sc: sqlite3.Connection, ident: dict[str, Any], acct: dict[str, Any]
) -> dict[str, Any]:
    found = mine(sc, ident, acct)
    if not found:
        raise LookupError("Turn Dating on first: make your dating profile.")
    return found


# ---------------------------------------------------------------------------
# who sees whom
# ---------------------------------------------------------------------------


def _wants(viewer: dict[str, Any], other: dict[str, Any]) -> bool:
    years = age_of(other["birthday"])
    return (
        years is not None
        and years >= MIN_AGE
        and viewer["age_min"] <= years <= viewer["age_max"]
        and other["gender"] in SHOW_ME.get(viewer["show_me"], ())
    )


def _other_account(sc: sqlite3.Connection, account_id: str) -> dict[str, Any] | None:
    try:
        return service.account(sc, account_id)
    except LookupError:
        return None


def _may_meet(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    mine_: dict[str, Any],
    other_acct: dict[str, Any] | None,
    other: dict[str, Any],
    *,
    friends: set[str] | None = None,
    blocked: set[str] | None = None,
) -> bool:
    """Whether these two may see each other in Discover (both ways: every rule is checked for each side)."""
    if (
        other_acct is None
        or other_acct["id"] == me_["id"]
        or other["status"] != "active"
    ):
        return False
    if (
        other_acct.get("suspended_at")
        or other_acct.get("age_group") not in ADULT_GROUPS
    ):
        return False
    blocked = service.blocked_ids(sc, me_["id"]) if blocked is None else blocked
    if other_acct["id"] in blocked:
        return False
    if not (_wants(mine_, other) and _wants(other, mine_)):
        return False
    if mine_["hide_friends"] or other["hide_friends"]:
        friends = service.friend_ids(sc, me_["id"]) if friends is None else friends
        if other_acct["id"] in friends:
            return False
    if (
        (mine_["hide_home"] or other["hide_home"])
        and me_.get("home")
        and me_.get("home") == other_acct.get("home")
    ):
        return False
    return True


def _shared(mine_: dict[str, Any], other: dict[str, Any]) -> list[str]:
    return [i for i in other["interests"] if i in mine_["interests"]]


def card(
    mine_: dict[str, Any] | None,
    other: dict[str, Any],
    *,
    starred_you: bool = False,
    likes_you: bool = False,
) -> dict[str, Any]:
    """What another person sees of a dating profile (never the Social account, @name or birthday)."""
    return {
        "id": other["id"],
        "first_name": other["first_name"],
        "age": age_of(other["birthday"]),
        "gender": other["gender"],
        "city": other["city"],
        "height_cm": other["height_cm"],
        "looking_for": other["looking_for"],
        "bio": other["bio"],
        "photos": photos.display_list(other["photos"]),
        "prompts": [
            {"id": p["id"], "question": PROMPTS.get(p["id"], ""), "answer": p["answer"]}
            for p in other["prompts"]
        ],
        "interests": other["interests"],
        "shared": _shared(mine_, other) if mine_ else [],
        "starred_you": starred_you,
        "likes_you": likes_you,
    }


def own_view(found: dict[str, Any]) -> dict[str, Any]:
    """The person's own profile, with what they set (preferences and privacy) and how others see it."""
    return {
        **card(None, found),
        "birthday": found["birthday"],
        "status": found["status"],
        "show_me": found["show_me"],
        "age_min": found["age_min"],
        "age_max": found["age_max"],
        "hide_friends": found["hide_friends"],
        "hide_home": found["hide_home"],
        "created_at": found["created_at"],
        "updated_at": found["updated_at"],
    }


def _incoming(sc: sqlite3.Connection, account_id: str) -> dict[str, str]:
    """Who liked or starred this person and has not been answered: {account_id: action}."""
    return {
        r["actor_id"]: r["action"]
        for r in rows(
            sc,
            """SELECT s.actor_id, s.action FROM so_dating_swipes s WHERE s.target_id = ? AND s.action IN ('like', 'star')
            AND NOT EXISTS (SELECT 1 FROM so_dating_swipes m WHERE m.actor_id = s.target_id AND m.target_id = s.actor_id)""",
            (account_id,),
        )
    }


def discover(
    sc: sqlite3.Connection,
    ident: dict[str, Any],
    me_: dict[str, Any],
    *,
    mode: str = "",
    limit: int = 10,
) -> dict[str, Any]:
    """The next people to meet, best first: who starred you, then interests in common, the same area, the same
    hopes, and who was here most recently. ``mode="likes"`` shows only people who already like you."""
    mine_ = _require_profile(sc, ident, me_)
    out: dict[str, Any] = {
        "cards": [],
        "paused": mine_["status"] == "paused",
        "limits": limits(sc, me_),
    }
    if out["paused"]:
        return out
    seen = {
        r["target_id"]
        for r in rows(
            sc,
            "SELECT target_id FROM so_dating_swipes WHERE actor_id = ?",
            (me_["id"],),
        )
    }
    incoming = _incoming(sc, me_["id"])
    blocked = service.blocked_ids(sc, me_["id"])
    friends = service.friend_ids(sc, me_["id"])
    found = rows(
        sc,
        """SELECT p.*, a.last_seen_at AS last_seen FROM so_dating_profiles p JOIN so_accounts a ON a.id = p.account_id
        WHERE p.status = 'active' AND p.account_id != ? ORDER BY a.last_seen_at DESC LIMIT 3000""",
        (me_["id"],),
    )
    scored = []
    for item in found:
        other = _profile(item) or {}
        if other["account_id"] in seen:
            continue
        if mode == "likes" and other["account_id"] not in incoming:
            continue
        if not _may_meet(
            sc,
            me_,
            mine_,
            _other_account(sc, other["account_id"]),
            other,
            friends=friends,
            blocked=blocked,
        ):
            continue
        action = incoming.get(other["account_id"], "")
        score = (
            len(_shared(mine_, other)) * 3
            + (
                2
                if other["city"] and other["city"].lower() == mine_["city"].lower()
                else 0
            )
            + (1 if other["looking_for"] == mine_["looking_for"] else 0)
        )
        scored.append(
            (
                1 if action == "star" else 0,
                score,
                item.get("last_seen") or "",
                other,
                action,
            )
        )
    scored.sort(key=lambda entry: (entry[0], entry[1], entry[2]), reverse=True)
    out["cards"] = [
        card(
            mine_,
            other,
            starred_you=action == "star",
            likes_you=mode == "likes" and bool(action),
        )
        for _star, _score, _seen, other, action in scored[: max(1, min(limit, 30))]
    ]
    out["remaining"] = len(scored)
    return out


def view(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any], profile_id: str
) -> dict[str, Any]:
    """One profile: someone you may meet, or a match."""
    mine_ = _require_profile(sc, ident, me_)
    other = profile_by_id(sc, profile_id)
    match = _match_between(sc, me_["id"], other["account_id"])
    if not match and not _may_meet(
        sc, me_, mine_, _other_account(sc, other["account_id"]), other
    ):
        raise LookupError(NOT_FOUND)
    if match and service.blocked(sc, me_["id"], other["account_id"]):
        raise LookupError(NOT_FOUND)
    action = _incoming(sc, me_["id"]).get(other["account_id"], "")
    return card(mine_, other, starred_you=action == "star")


# ---------------------------------------------------------------------------
# likes, passes, stars and the daily allowance
# ---------------------------------------------------------------------------


def _day() -> str:
    return _today().isoformat()


def limits(sc: sqlite3.Connection, me_: dict[str, Any]) -> dict[str, Any]:
    used = row(
        sc,
        "SELECT likes, stars FROM so_dating_daily WHERE account_id = ? AND day = ?",
        (me_["id"], _day()),
    ) or {"likes": 0, "stars": 0}
    tomorrow = datetime.combine(
        _today() + timedelta(days=1), datetime.min.time(), tzinfo=timezone.utc
    )
    last = row(
        sc,
        "SELECT action FROM so_dating_swipes WHERE actor_id = ? ORDER BY created_at DESC LIMIT 1",
        (me_["id"],),
    )
    return {
        "likes_left": max(0, LIKES_PER_DAY - used["likes"]),
        "stars_left": max(0, STARS_PER_DAY - used["stars"]),
        "likes_per_day": LIKES_PER_DAY,
        "stars_per_day": STARS_PER_DAY,
        "resets_at": stamp(tomorrow),
        "can_undo": bool(last and last["action"] == "pass"),
    }


def _spend(sc: sqlite3.Connection, me_: dict[str, Any], action: str) -> None:
    left = limits(sc, me_)
    if left["likes_left"] <= 0:
        raise PermissionError(
            "You have used today's likes. New ones arrive tomorrow: take your time with the people you liked."
        )
    if action == "star" and left["stars_left"] <= 0:
        raise PermissionError("You have used today's stars. New ones arrive tomorrow.")
    sc.execute(
        """INSERT INTO so_dating_daily (account_id, day, likes, stars) VALUES (?, ?, 1, ?)
        ON CONFLICT(account_id, day) DO UPDATE SET likes = likes + 1, stars = stars + excluded.stars""",
        (me_["id"], _day(), 1 if action == "star" else 0),
    )


def swipe(
    sc: sqlite3.Connection,
    ident: dict[str, Any],
    me_: dict[str, Any],
    profile_id: str,
    action: str,
) -> dict[str, Any]:
    """Like, pass or star someone you may meet. A like (or star) back makes a match and its chat."""
    service._active(me_)
    mine_ = _require_profile(sc, ident, me_)
    if mine_["status"] != "active":
        raise PermissionError(
            "Your dating profile is paused. Turn it back on to meet people."
        )
    action = kit.choice(action, "what you chose", ("like", "pass", "star"))
    other = profile_by_id(sc, profile_id)
    other_acct = _other_account(sc, other["account_id"])
    if not _may_meet(sc, me_, mine_, other_acct, other) or other_acct is None:
        raise LookupError(NOT_FOUND)
    if row(
        sc,
        "SELECT 1 AS x FROM so_dating_swipes WHERE actor_id = ? AND target_id = ?",
        (me_["id"], other_acct["id"]),
    ):
        raise ValueError("You already answered this profile.")
    if action != "pass":
        _spend(sc, me_, action)
    moment = stamp()
    sc.execute(
        "INSERT INTO so_dating_swipes (actor_id, target_id, action, created_at) VALUES (?, ?, ?, ?)",
        (me_["id"], other_acct["id"], action, moment),
    )
    match = None
    if action != "pass" and row(
        sc,
        "SELECT 1 AS x FROM so_dating_swipes WHERE actor_id = ? AND target_id = ? AND action IN ('like', 'star')",
        (other_acct["id"], me_["id"]),
    ):
        match = _make_match(sc, me_, other_acct, moment)
    sc.commit()
    return {
        "action": action,
        "match": match_view(sc, me_, match) if match else None,
        "limits": limits(sc, me_),
    }


def undo(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any]
) -> dict[str, Any]:
    """Take back the last pass: that person comes back to the top of Discover."""
    _require_profile(sc, ident, me_)
    last = row(
        sc,
        "SELECT * FROM so_dating_swipes WHERE actor_id = ? ORDER BY created_at DESC LIMIT 1",
        (me_["id"],),
    )
    if not last or last["action"] != "pass":
        raise ValueError("There is no pass to undo.")
    sc.execute(
        "DELETE FROM so_dating_swipes WHERE actor_id = ? AND target_id = ?",
        (me_["id"], last["target_id"]),
    )
    sc.commit()
    other = profile_of(sc, last["target_id"])
    mine_ = profile_of(sc, me_["id"])
    return {
        "card": card(mine_, other) if other else None,
        "limits": limits(sc, me_),
    }


def likes_you(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any]
) -> dict[str, Any]:
    """How many people like you, with only a hint of each: meet them in Discover (``mode=likes``)."""
    mine_ = _require_profile(sc, ident, me_)
    hints = []
    for account_id, action in _incoming(sc, me_["id"]).items():
        other = profile_of(sc, account_id)
        if not other or not _may_meet(
            sc, me_, mine_, _other_account(sc, account_id), other
        ):
            continue
        hints.append(
            {
                "initial": other["first_name"][:1].upper(),
                "shared": _shared(mine_, other),
                "star": action == "star",
                "tint": int(other["id"][:6], 16) % 360,
            }
        )
    hints.sort(key=lambda hint: (hint["star"], len(hint["shared"])), reverse=True)
    return {"count": len(hints), "hints": hints[:24]}


# ---------------------------------------------------------------------------
# matches and their chats
# ---------------------------------------------------------------------------


def _pair(a: str, b: str) -> tuple[str, str]:
    return (a, b) if a < b else (b, a)


def _match_between(sc: sqlite3.Connection, a: str, b: str) -> dict[str, Any] | None:
    x, y = _pair(a, b)
    return row(
        sc, "SELECT * FROM so_dating_matches WHERE a_id = ? AND b_id = ?", (x, y)
    )


def _make_match(
    sc: sqlite3.Connection, me_: dict[str, Any], other: dict[str, Any], moment: str
) -> dict[str, Any]:
    x, y = _pair(me_["id"], other["id"])
    existing = _match_between(sc, x, y)
    if existing:
        return existing
    chat_id = new_id()
    sc.execute(
        """INSERT INTO so_chats (id, kind, title, direct_key, created_by, created_at, updated_at, context)
        VALUES (?, 'direct', '', ?, ?, ?, ?, ?)""",
        (chat_id, f"{CHAT_CONTEXT}:{x}:{y}", me_["id"], moment, moment, CHAT_CONTEXT),
    )
    for member in (x, y):
        sc.execute(
            "INSERT INTO so_chat_members (chat_id, account_id, role, joined_at, last_read_at) VALUES (?, ?, 'member', ?, ?)",
            (chat_id, member, moment, moment),
        )
    match_id = new_id()
    sc.execute(
        "INSERT INTO so_dating_matches (id, a_id, b_id, chat_id, a_seen, b_seen, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            match_id,
            x,
            y,
            chat_id,
            1 if x == me_["id"] else 0,
            1 if y == me_["id"] else 0,
            moment,
        ),
    )
    return row(sc, "SELECT * FROM so_dating_matches WHERE id = ?", (match_id,)) or {}


def transcript(sc: sqlite3.Connection, chat_id: str, a_id: str = "") -> list[dict[str, Any]]:
    """A chat as a moderator reads it: who wrote each message (``a`` or ``b`` of the match), what, and when,
    including messages its author deleted or a moderator hid."""
    return [
        {
            "id": m["id"],
            "by": ("a" if m["author_id"] == a_id else "b") if a_id else m["author_id"],
            "author_id": m["author_id"],
            "text": m["text"],
            "photo": m["photo"],
            "at": m["created_at"],
            "deleted": bool(m["deleted_at"]),
            "hidden": bool(m["hidden_at"]),
        }
        for m in rows(
            sc,
            "SELECT * FROM so_messages WHERE chat_id = ? ORDER BY created_at LIMIT 2000",
            (chat_id,),
        )
    ]


def profile_snapshot(sc: sqlite3.Connection, account_id: str) -> dict[str, Any]:
    """A dating profile as it is now (for evidence): never the Social account's own details."""
    found = profile_of(sc, account_id)
    if not found:
        return {"account_id": account_id, "dating_profile": None}
    return {
        "account_id": account_id,
        "dating_profile": {
            key: found.get(key)
            for key in (
                "id",
                "first_name",
                "birthday",
                "gender",
                "bio",
                "prompts",
                "interests",
                "photos",
                "city",
                "status",
                "created_at",
                "updated_at",
            )
        },
    }


def evidence_between(
    sc: sqlite3.Connection, reporter_id: str, other_id: str, *, profiles: bool = False
) -> dict[str, Any] | None:
    """What a report between these two copies from Dating: their match's chat if it still runs, the chats of
    matches that ended within store.RETENTION_DAYS, and both dating profiles (always for a report made in Dating,
    otherwise only when there was a match). None when Dating has nothing about the two."""
    ensure(sc)
    x, y = _pair(reporter_id, other_id)
    match = _match_between(sc, x, y)
    ended = rows(
        sc,
        "SELECT * FROM so_dating_ended WHERE a_id = ? AND b_id = ? AND purge_after >= ? ORDER BY ended_at",
        (x, y, stamp()),
    )
    if not (match or ended or profiles):
        return None
    if not (profile_of(sc, reporter_id) or profile_of(sc, other_id) or ended):
        return None
    side = {x: "a", y: "b"}
    return {
        "reporter": side[reporter_id],
        "profiles": {
            side[reporter_id]: profile_snapshot(sc, reporter_id),
            side[other_id]: profile_snapshot(sc, other_id),
        },
        "live_chat": transcript(sc, match["chat_id"], x) if match else None,
        "ended_chats": [
            {
                "reason": item["reason"],
                "ended_at": item["ended_at"],
                "transcript": store.loads(item["transcript"], []),
                "profiles": store.loads(item["profiles"], {}),
            }
            for item in ended
        ],
    }


def ended_chats(sc: sqlite3.Connection) -> list[dict[str, Any]]:
    """Every ended match's kept chat (for a platform moderator), newest first."""
    ensure(sc)
    return [
        {
            **{k: item[k] for k in ("id", "match_id", "a_id", "b_id", "reason", "ended_at", "purge_after")},
            "transcript": store.loads(item["transcript"], []),
            "profiles": store.loads(item["profiles"], {}),
        }
        for item in rows(
            sc, "SELECT * FROM so_dating_ended ORDER BY ended_at DESC LIMIT 200"
        )
    ]


def _end_match(
    sc: sqlite3.Connection, match: dict[str, Any], reason: str = "unmatched"
) -> None:
    """The match and its chat are gone for both; neither is shown to the other again. The chat and both profiles
    are copied first into ``so_dating_ended``, which only moderators read (a report needs its evidence), and are
    purged after store.RETENTION_DAYS."""
    chat_id = match["chat_id"]
    sc.execute(
        """INSERT INTO so_dating_ended (id, match_id, a_id, b_id, reason, transcript, profiles, ended_at, purge_after)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            new_id(),
            match["id"],
            match["a_id"],
            match["b_id"],
            reason,
            store.dumps(transcript(sc, chat_id, match["a_id"])),
            store.dumps(
                {
                    "a": profile_snapshot(sc, match["a_id"]),
                    "b": profile_snapshot(sc, match["b_id"]),
                }
            ),
            stamp(),
            store.later(24 * store.RETENTION_DAYS),
        ),
    )
    sc.execute("DELETE FROM so_messages WHERE chat_id = ?", (chat_id,))
    sc.execute("DELETE FROM so_chat_members WHERE chat_id = ?", (chat_id,))
    sc.execute(
        "DELETE FROM so_chats WHERE id = ? AND context = ?", (chat_id, CHAT_CONTEXT)
    )
    sc.execute("DELETE FROM so_dating_matches WHERE id = ?", (match["id"],))
    for actor, target in (
        (match["a_id"], match["b_id"]),
        (match["b_id"], match["a_id"]),
    ):
        sc.execute(
            """INSERT INTO so_dating_swipes (actor_id, target_id, action, created_at) VALUES (?, ?, 'ended', ?)
            ON CONFLICT(actor_id, target_id) DO UPDATE SET action = 'ended'""",
            (actor, target, stamp()),
        )


def _other_id(match: dict[str, Any], account_id: str) -> str:
    return match["b_id"] if match["a_id"] == account_id else match["a_id"]


def match_for(
    sc: sqlite3.Connection, me_: dict[str, Any], match_id: str
) -> dict[str, Any]:
    found = row(
        sc,
        "SELECT * FROM so_dating_matches WHERE id = ?",
        (store.check_id(match_id, "match"),),
    )
    if not found or me_["id"] not in (found["a_id"], found["b_id"]):
        raise LookupError("That match was not found.")
    if service.blocked(sc, found["a_id"], found["b_id"]):
        raise LookupError("That match was not found.")
    return found


def match_view(
    sc: sqlite3.Connection, me_: dict[str, Any], match: dict[str, Any]
) -> dict[str, Any]:
    other = profile_of(sc, _other_id(match, me_["id"]))
    mine_ = profile_of(sc, me_["id"])
    member = row(
        sc,
        "SELECT * FROM so_chat_members WHERE chat_id = ? AND account_id = ?",
        (match["chat_id"], me_["id"]),
    ) or {"account_id": me_["id"], "last_read_at": ""}
    last = row(
        sc,
        "SELECT * FROM so_messages WHERE chat_id = ? AND hidden_at IS NULL ORDER BY created_at DESC LIMIT 1",
        (match["chat_id"],),
    )
    seen_column = "a_seen" if match["a_id"] == me_["id"] else "b_seen"
    return {
        "id": match["id"],
        "profile": card(mine_, other) if other else None,
        "created_at": match["created_at"],
        "new": not match[seen_column],
        "unread": service._unread(sc, match["chat_id"], member),
        "last": None
        if not last
        else {
            "text": ""
            if last["deleted_at"]
            else (last["text"] or ("📷" if last["photo"] else "")),
            "mine": last["author_id"] == me_["id"],
            "at": last["created_at"],
        },
    }


def matches(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any]
) -> dict[str, Any]:
    _require_profile(sc, ident, me_)
    out = []
    hidden = service.blocked_ids(sc, me_["id"])
    for match in rows(
        sc,
        """SELECT m.* FROM so_dating_matches m LEFT JOIN so_chats c ON c.id = m.chat_id
        WHERE m.a_id = ? OR m.b_id = ? ORDER BY COALESCE(c.updated_at, m.created_at) DESC LIMIT 300""",
        (me_["id"], me_["id"]),
    ):
        other_id = _other_id(match, me_["id"])
        other_acct = _other_account(sc, other_id)
        if other_id in hidden or other_acct is None or other_acct.get("suspended_at"):
            continue
        view_ = match_view(sc, me_, match)
        if view_["profile"]:
            out.append(view_)
    return {
        "matches": out,
        "new": sum(1 for m in out if m["new"]),
        "unread": sum(m["unread"] for m in out),
    }


ICEBREAKERS_FOR_INTEREST = "You like {interest} too! What got you into it?"
ICEBREAKERS_FOR_PROMPT = (
    "I liked your answer to “{question}” What's the story behind it?"
)
ICEBREAKERS_ALWAYS = (
    "What has been the best part of your week so far?",
    "If we met for a coffee this weekend, where would you take me?",
)


def match_detail(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any], match_id: str
) -> dict[str, Any]:
    _require_profile(sc, ident, me_)
    match = match_for(sc, me_, match_id)
    view_ = match_view(sc, me_, match)
    profile = view_["profile"] or {}
    ideas = [
        ICEBREAKERS_FOR_PROMPT.format(question=p["question"])
        for p in profile.get("prompts", [])[:2]
    ]
    ideas += [
        ICEBREAKERS_FOR_INTEREST.format(interest=i.replace("_", " "))
        for i in profile.get("shared", [])[:1]
    ]
    ideas += list(ICEBREAKERS_ALWAYS)
    me_profile = profile_of(sc, me_["id"]) or {}
    return {
        **view_,
        "icebreakers": ideas[:4],
        "me": {
            "first_name": me_profile.get("first_name", ""),
            "photos": photos.display_list(me_profile.get("photos", [])[:1]),
        },
    }


def seen(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any], match_id: str
) -> None:
    _require_profile(sc, ident, me_)
    match = match_for(sc, me_, match_id)
    column = "a_seen" if match["a_id"] == me_["id"] else "b_seen"
    sc.execute(
        f"UPDATE so_dating_matches SET {column} = 1 WHERE id = ?", (match["id"],)
    )  # noqa: S608 - fixed name
    sc.commit()


def unmatch(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any], match_id: str
) -> None:
    _require_profile(sc, ident, me_)
    match = match_for(sc, me_, match_id)
    _end_match(sc, match, "unmatched")
    sc.commit()


def chat_messages(
    sc: sqlite3.Connection,
    ident: dict[str, Any],
    me_: dict[str, Any],
    match_id: str,
    *,
    since: str = "",
) -> dict[str, Any]:
    """The match's chat (Social's chat system), with nothing of either Social account in it."""
    _require_profile(sc, ident, me_)
    match = match_for(sc, me_, match_id)
    data = service.messages(sc, me_, match["chat_id"], since=since)
    return {
        "messages": [
            {
                key: item[key]
                for key in (
                    "id",
                    "text",
                    "photo",
                    "deleted",
                    "created_at",
                    "mine",
                    "seen",
                    "can_delete",
                    "removed",
                )
                if key in item
            }
            for item in data["messages"]
        ],
        "read_upto": data["read_upto"],
        "now": data["now"],
        "can_send": data["chat"]["can_send"],
    }


def chat_send(
    sc: sqlite3.Connection,
    ident: dict[str, Any],
    me_: dict[str, Any],
    match_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    _require_profile(sc, ident, me_)
    match = match_for(sc, me_, match_id)
    result = service.send(
        sc, me_, match["chat_id"], {"text": raw.get("text"), "photo": raw.get("photo")}
    )
    column = "a_seen" if match["a_id"] == me_["id"] else "b_seen"
    sc.execute(
        f"UPDATE so_dating_matches SET {column} = 1 WHERE id = ?", (match["id"],)
    )  # noqa: S608 - fixed name
    sc.commit()
    return result


def chat_read(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any], match_id: str
) -> None:
    _require_profile(sc, ident, me_)
    service.mark_read(sc, me_, match_for(sc, me_, match_id)["chat_id"])


def chat_open(
    sc: sqlite3.Connection,
    me_: dict[str, Any],
    other: dict[str, Any],
    chat: dict[str, Any],
) -> bool:
    """Whether a message may go into a dating chat now (Social's ``may_send_direct`` asks): while the match
    lasts, between two adults, neither blocked nor suspended, whether or not they are friends."""
    if me_.get("suspended_at") or other.get("suspended_at"):
        return False
    if (
        me_.get("age_group") not in ADULT_GROUPS
        or other.get("age_group") not in ADULT_GROUPS
    ):
        return False
    if service.blocked(sc, me_["id"], other["id"]):
        return False
    match = _match_between(sc, me_["id"], other["id"])
    return bool(match and match["chat_id"] == chat.get("id"))


# ---------------------------------------------------------------------------
# safety: block and report
# ---------------------------------------------------------------------------


def block(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any], profile_id: str
) -> dict[str, Any]:
    """Block someone met in Dating: Social's block (both ways, everywhere), and any match between you ends."""
    _require_profile(sc, ident, me_)
    other = profile_by_id(sc, profile_id)
    match = _match_between(sc, me_["id"], other["account_id"])
    if match:
        _end_match(sc, match, "blocked")
    service.block(sc, me_, other["account_id"])
    return {"blocked": True}


def report(
    sc: sqlite3.Connection,
    ident: dict[str, Any],
    me_: dict[str, Any],
    profile_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Report a dating profile to Social's moderators (the platform's, hosted). They stop showing up for you;
    ``block`` also blocks them."""
    mine_ = _require_profile(sc, ident, me_)
    other = profile_by_id(sc, profile_id)
    match = _match_between(sc, me_["id"], other["account_id"])
    if not match and not _may_meet(
        sc, me_, mine_, _other_account(sc, other["account_id"]), other
    ):
        # someone you already passed or liked can still be reported, as long as nothing hides them from you
        if service.blocked(sc, me_["id"], other["account_id"]) or not row(
            sc,
            "SELECT 1 AS x FROM so_dating_swipes WHERE (actor_id = ? AND target_id = ?) OR (actor_id = ? AND target_id = ?)",
            (me_["id"], other["account_id"], other["account_id"], me_["id"]),
        ):
            raise LookupError(NOT_FOUND)
    reason = (
        kit.text(raw.get("reason"), "a reason", max_len=400, multiline=True)
        or "No reason given"
    )
    # Both profiles and the chat are copied into the report before anything ends (block below ends the match).
    service.report(
        sc,
        me_,
        {
            "target_type": "account",
            "target_id": other["account_id"],
            "reason": f"Dating profile: {reason}",
        },
        dating_profiles=True,
    )
    sc.execute(
        "INSERT OR IGNORE INTO so_dating_swipes (actor_id, target_id, action, created_at) VALUES (?, ?, 'pass', ?)",
        (me_["id"], other["account_id"], stamp()),
    )
    sc.commit()
    if kit.flag(raw.get("block")):
        block(sc, ident, me_, profile_id)
    return {"reported": True, "blocked": kit.flag(raw.get("block"))}


# ---------------------------------------------------------------------------
# the person's own data, and the badge
# ---------------------------------------------------------------------------


def export(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any]
) -> dict[str, Any]:
    """The person's own Dating data, for them alone (an organization's export of a member never carries it)."""
    found = _require_profile(sc, ident, me_)
    chats = [
        m["chat_id"]
        for m in rows(
            sc,
            "SELECT chat_id FROM so_dating_matches WHERE a_id = ? OR b_id = ?",
            (me_["id"], me_["id"]),
        )
    ]
    written = []
    for chat_id in chats:
        written += rows(
            sc,
            "SELECT id, text, photo, created_at FROM so_messages WHERE chat_id = ? AND author_id = ? AND deleted_at IS NULL ORDER BY created_at",
            (chat_id, me_["id"]),
        )
    return {
        "profile": own_view(found),
        "likes_and_passes": rows(
            sc,
            "SELECT action, created_at FROM so_dating_swipes WHERE actor_id = ? ORDER BY created_at",
            (me_["id"],),
        ),
        "matches": len(chats),
        "messages_you_wrote": written,
    }


def badge(
    sc: sqlite3.Connection, ident: dict[str, Any], me_: dict[str, Any]
) -> dict[str, int]:
    """New matches and unread dating messages (Dating's own badge: Social's badges leave them out)."""
    if not eligible(ident, me_):
        return {"matches": 0, "messages": 0, "likes": 0}
    ensure(sc)
    if not profile_of(sc, me_["id"]):
        return {"matches": 0, "messages": 0, "likes": 0}
    data = matches(sc, ident, me_)
    return {
        "matches": data["new"],
        "messages": data["unread"],
        "likes": likes_you(sc, ident, me_)["count"],
    }


def options() -> dict[str, Any]:
    """The choices a dating profile is made from (the browser labels them)."""
    return {
        "prompts": [{"id": key, "question": text} for key, text in PROMPTS.items()],
        "interests": list(INTERESTS),
        "genders": list(GENDERS),
        "show_me": list(SHOW_ME),
        "looking_for": list(LOOKING_FOR),
        "max_interests": MAX_INTERESTS,
        "max_photos": MAX_PHOTOS,
        "min_age": MIN_AGE,
        "max_age": MAX_AGE,
    }
