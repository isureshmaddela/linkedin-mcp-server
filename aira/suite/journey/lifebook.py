"""Life stages: a life from before birth to remembrance, what matters at each age, and the record of it.

A person keeps their own life (made on first use, its birth date shared with their profile) and, if they are an
adult, the lives of people they look after: a child (even one not born yet), an older parent, a partner. For each
life:

* **The stage of life** comes from the age (:mod:`aira.suite.journey.stages`): the checklist of that stage (with the
  important unfinished items of earlier stages carried along), its milestones, and ideas for each chosen interest.
  An item is done, set aside ("not for me"), or planned (turned into a goal, which finishes it when the goal is
  reached).
* **The life book**: growth (height, weight, head), vaccines and check-ups with the date the next one is due
  (reminders on Today), schools, work, the homes lived in, and milestones. A person's own milestones go to their
  timeline; those of a life they look after are kept in its book.
* **Wishes and legacy** (one's own life, adults): who decides if you cannot, the care you want, where the will is,
  funeral wishes, who looks after your children, your online accounts and words for the people you love.
* **A life that has ended**: its date of death turns it into a remembrance, with what the people left behind need
  to do.

Everything is the keeper's own (``owner_id``): nobody else in the household or organization sees it.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from aira.suite import kit, life
from aira.suite.journey import service, stages

MAX_LIVES = 20  # one's own and the people one looks after
MAX_RECORDS = 2000  # in one life's book
DUE_DAYS = 30  # Today lists a check-up or a vaccine due this many days ahead
LATE_DAYS = 60  # ... and keeps one that was due this long ago
EXPECTED_DAYS = 300  # a baby can be added this long before the day it is due
RELATIONS = (
    "self",
    "child",
    "grandchild",
    "partner",
    "parent",
    "grandparent",
    "sibling",
    "friend",
    "other",
)
BLOOD_GROUPS = ("", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-")
RECORD_KINDS = (
    "growth",
    "vaccine",
    "checkup",
    "education",
    "work",
    "home",
    "milestone",
)
ITEM_STATUSES = ("open", "done", "skipped", "planned")
ORGAN_CHOICES = ("", "yes", "no", "undecided")
WISHES: tuple[dict[str, str], ...] = (
    {
        "id": "health_proxy",
        "title": "Who decides for me if I cannot",
        "hint": "A name and how to reach them, for health and for money.",
    },
    {
        "id": "care",
        "title": "The care I want, and do not want",
        "hint": "At the end of life: where you want to be, treatments you would or would not want.",
    },
    {
        "id": "will_place",
        "title": "Where my will and important papers are",
        "hint": "The will, insurance, property papers, and who has copies.",
    },
    {
        "id": "dependents",
        "title": "Who looks after my children, pets or the people I care for",
        "hint": "",
    },
    {
        "id": "funeral",
        "title": "Funeral or memorial wishes",
        "hint": "Burial or cremation, faith, music, who to tell.",
    },
    {
        "id": "digital",
        "title": "My phone, email and online accounts",
        "hint": "What should happen to them. Never write passwords here.",
    },
    {
        "id": "messages",
        "title": "Words for the people I love",
        "hint": "",
    },
)
WISH_IDS = tuple(wish["id"] for wish in WISHES)
# The timeline kind a milestone becomes on one's own timeline, by the area or the stage it belongs to.
_EVENT_KIND = {
    "pregnancy": "birth",
    "baby": "birth",
    "toddler": "family",
    "preschool": "education",
    "child": "education",
    "teen": "education",
}


# ---------------------------------------------------------------------------
# ages
# ---------------------------------------------------------------------------


def months_between(start: date, end: date) -> int:
    """Whole months from ``start`` to ``end`` (negative when ``end`` comes first): a baby born on 31 January is
    one month old on 28 February only once the day has come."""
    months = (end.year - start.year) * 12 + end.month - start.month
    if end >= start:
        if end.day < start.day and not _last_day(end):
            months -= 1
    elif end.day > start.day:
        months += 1
    return months


def _last_day(day: date) -> bool:
    return (day + timedelta(days=1)).month != day.month


def _parse(value: str) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# lives
# ---------------------------------------------------------------------------


def _self_row(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """The person's own life, made on first use."""
    found = kit.fetch_one(
        connection,
        "SELECT * FROM jr_lives WHERE owner_id = ? AND relation = 'self'",
        (me["id"],),
    )
    if found:
        return found
    stamp = kit.now()
    life_id = kit.new_id()
    connection.execute(
        "INSERT INTO jr_lives (id, owner_id, relation, birth_date, created_at, updated_at) VALUES (?, ?, 'self', ?, ?, ?)",
        (life_id, me["id"], me.get("birthday") or "", stamp, stamp),
    )
    connection.commit()
    return kit.require_row(connection, "jr_lives", life_id, "life")


def _life_row(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM jr_lives WHERE id = ? AND owner_id = ?",
        (life_id, me["id"]),
    )
    if not found:
        raise LookupError("That person was not found.")
    return found


def may_keep_others(me: dict[str, Any]) -> bool:
    """Adults keep the lives of the people they look after; a child or a teen keeps their own."""
    return life.is_adult(me)


def _interests(row: dict[str, Any]) -> list[str]:
    value = kit.loads(row.get("interests"), []) or []
    return [item for item in value if item in stages.INTEREST_IDS]


def life_view(me: dict[str, Any], row: dict[str, Any], now: date) -> dict[str, Any]:
    """A life with its age (in months; negative before birth) and its stage."""
    own = row["relation"] == "self"
    birth = row["birth_date"] or (me.get("birthday", "") if own else "")
    born = _parse(birth)
    passed = bool(row["died_on"])
    until = _parse(row["died_on"]) if passed else now
    months = months_between(born, until or now) if born else None
    if months is None and own:
        # No birth date yet: the profile's age group says roughly where they are.
        months = {"child": 96, "teen": 180, "adult": 360, "senior": 840}.get(
            me.get("age_group", "adult"), 360
        )
        approximate = True
    else:
        approximate = False
    expected = bool(born and born > now and not passed)
    stage = (
        stages.BY_ID["pregnancy"]
        if expected
        else stages.stage_for(months, passed=passed)
    )
    following = None
    if (
        not passed
        and months is not None
        and stage["to_months"] is not None
        and not expected
    ):
        nxt = next(
            (s for s in stages.LIVING_STAGES if s["from_months"] == stage["to_months"]),
            None,
        )
        if nxt:
            following = {
                "id": nxt["id"],
                "title": nxt["title"],
                "label": nxt["label"],
                "in_months": stage["to_months"] - months,
            }
    return {
        "id": row["id"],
        "self": own,
        "name": me["name"] if own else row["name"],
        "relation": row["relation"],
        "birth_date": birth,
        "birth_time": row["birth_time"],
        "birth_place": row["birth_place"],
        "blood_group": row["blood_group"],
        "notes": row["notes"],
        "interests": _interests(row),
        "age_months": months,
        "age_days": (now - born).days if born and not passed else None,
        "approximate": approximate,
        "expected": expected,
        "passed": passed,
        "died_on": row["died_on"],
        "died_place": row["died_place"],
        "died_note": row["died_note"],
        "stage": {
            "id": stage["id"],
            "title": stage["title"],
            "label": stage["label"],
            "description": stage["description"],
        },
        "next_stage": following,
        "updated_at": row["updated_at"],
    }


def list_lives(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    """The person's own life first, then the people they look after (the youngest last)."""
    own = _self_row(connection, me)
    others = kit.fetch_all(
        connection,
        "SELECT * FROM jr_lives WHERE owner_id = ? AND relation != 'self' ORDER BY birth_date = '', birth_date, created_at",
        (me["id"],),
    )
    now = service.today(connection)
    return [life_view(me, row, now) for row in (own, *others)]


def _clean_life(
    raw: dict[str, Any], current: dict[str, Any] | None, now: date, *, own: bool
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    values: dict[str, Any] = {
        "birth_date": kit.day(merged.get("birth_date"), "the birth date"),
        "birth_time": kit.text(
            merged.get("birth_time"), "the time of birth", max_len=5
        ),
        "birth_place": kit.text(
            merged.get("birth_place"), "the place of birth", max_len=120
        ),
        "blood_group": str(merged.get("blood_group") or "").strip().upper(),
        "notes": kit.text(
            merged.get("notes"), "the notes", max_len=2000, multiline=True
        ),
    }
    if values["birth_time"] and not re.fullmatch(
        r"(?:[01]\d|2[0-3]):[0-5]\d", values["birth_time"]
    ):
        raise ValueError("The time of birth must look like 06:45.")
    if values["blood_group"] not in BLOOD_GROUPS:
        raise ValueError(
            f"The blood group must be one of: {', '.join(BLOOD_GROUPS[1:])}."
        )
    born = _parse(values["birth_date"])
    if born and born > now:
        if own:
            raise ValueError("Your birth date cannot be in the future.")
        if (born - now).days > EXPECTED_DAYS:
            raise ValueError(
                "A baby can be added up to ten months before the day it is due."
            )
    if born and born.year < now.year - 130:
        raise ValueError("Check the birth date: it is more than 130 years ago.")
    if "interests" in raw or current is None:
        chosen = raw.get("interests") if "interests" in raw else []
        if not isinstance(chosen, list):
            raise ValueError("Choose interests from the list.")
        unknown = [item for item in chosen if item not in stages.INTEREST_IDS]
        if unknown:
            raise ValueError(f"{unknown[0]} is not an interest Monosage knows.")
        values["interests"] = kit.dumps(list(dict.fromkeys(chosen)))
    if not own:
        values["name"] = kit.text(
            merged.get("name"), "their name", max_len=60, required=True
        )
        values["relation"] = kit.choice(
            merged.get("relation"),
            "who they are to you",
            RELATIONS[1:],
            default="child",
        )
    return values


def save_life(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    life_id: str = "",
) -> dict[str, Any]:
    """Change a life's details, or (adults) add someone you look after. ``add_to_timeline`` puts the birth of a
    new person on your own timeline."""
    now = service.today(connection)
    stamp = kit.now()
    if life_id:
        current = _life_row(connection, me, life_id)
        own = current["relation"] == "self"
        if own and not current["birth_date"]:
            current = {**current, "birth_date": me.get("birthday") or ""}
        values = _clean_life(raw, current, now, own=own)
        if own:
            values.pop("name", None)
        if (
            current["died_on"]
            and values["birth_date"]
            and values["birth_date"] > current["died_on"]
        ):
            raise ValueError("The birth date must come before the date they died.")
        kit.update_row(connection, "jr_lives", life_id, {**values, "updated_at": stamp})
        if own and values["birth_date"] != (me.get("birthday") or ""):
            # One birth date: the profile's birthday (the family's birthday list) follows it.
            kit.update_row(
                connection,
                "life_profiles",
                me["id"],
                {"birthday": values["birth_date"], "updated_at": stamp},
            )
            me["birthday"] = values["birth_date"]
    else:
        if not may_keep_others(me):
            raise PermissionError(
                "Ask an adult in your household to add the people they look after."
            )
        _self_row(connection, me)
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM jr_lives WHERE owner_id = ?",
                (me["id"],),
            )
            >= MAX_LIVES
        ):
            raise ValueError(f"Life stages can keep {MAX_LIVES} lives.")
        values = _clean_life({"interests": [], **raw}, None, now, own=False)
        life_id = kit.new_id()
        connection.execute(
            """INSERT INTO jr_lives (id, owner_id, relation, name, birth_date, birth_time, birth_place, blood_group,
            interests, notes, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                life_id,
                me["id"],
                values["relation"],
                values["name"],
                values["birth_date"],
                values["birth_time"],
                values["birth_place"],
                values["blood_group"],
                values["interests"],
                values["notes"],
                stamp,
                stamp,
            ),
        )
        born = _parse(values["birth_date"])
        if kit.flag(raw.get("add_to_timeline")) and born and born <= now:
            service.save_event(
                connection,
                me,
                {
                    "title": f"{values['name']} was born",
                    "kind": "birth",
                    "event_date": values["birth_date"],
                    "location": values["birth_place"],
                },
            )
    connection.commit()
    return life_view(me, _life_row(connection, me, life_id), now)


def delete_life(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str
) -> None:
    row = _life_row(connection, me, life_id)
    if row["relation"] == "self":
        raise ValueError("Your own life stays. You can clear its details instead.")
    connection.execute("DELETE FROM jr_records WHERE life_id = ?", (life_id,))
    connection.execute("DELETE FROM jr_life_items WHERE life_id = ?", (life_id,))
    connection.execute("DELETE FROM jr_lives WHERE id = ?", (life_id,))
    connection.commit()


def set_passed(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    life_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Record that someone you looked after has died (adults); their book becomes a remembrance."""
    row = _life_row(connection, me, life_id)
    if row["relation"] == "self":
        raise ValueError("This is your own life.")
    if not may_keep_others(me):
        raise PermissionError("Only an adult can record this.")
    now = service.today(connection)
    died_on = kit.day(raw.get("died_on"), "the date they died", required=True)
    if died_on > now.isoformat():
        raise ValueError("The date they died cannot be in the future.")
    if row["birth_date"] and died_on < row["birth_date"]:
        raise ValueError("The date they died must come after their birth date.")
    values = {
        "died_on": died_on,
        "died_place": kit.text(raw.get("died_place"), "the place", max_len=120),
        "died_note": kit.text(
            raw.get("died_note"), "the words", max_len=2000, multiline=True
        ),
        "updated_at": kit.now(),
    }
    kit.update_row(connection, "jr_lives", life_id, values)
    if kit.flag(raw.get("add_to_timeline")):
        service.save_event(
            connection,
            me,
            {
                "title": f"In memory of {row['name']}",
                "kind": "family",
                "event_date": died_on,
                "location": values["died_place"],
                "description": values["died_note"],
            },
        )
    connection.commit()
    return life_view(me, _life_row(connection, me, life_id), now)


def clear_passed(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str
) -> dict[str, Any]:
    """Undo a date of death recorded by mistake."""
    row = _life_row(connection, me, life_id)
    kit.update_row(
        connection,
        "jr_lives",
        row["id"],
        {"died_on": "", "died_place": "", "died_note": "", "updated_at": kit.now()},
    )
    connection.commit()
    return life_view(me, _life_row(connection, me, life_id), service.today(connection))


# ---------------------------------------------------------------------------
# the checklist and the ideas
# ---------------------------------------------------------------------------


def _is_idea(item_id: str) -> bool:
    interest, _, rest = item_id.partition(".")
    group, _, index = rest.partition(".")
    return (
        interest in stages.INTERESTS_BY_ID
        and group in {g["id"] for g in stages.GROUPS}
        and index.isdigit()
        and int(index) < len(stages.INTERESTS_BY_ID[interest]["ideas"][group])
    )


def _statuses(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str
) -> dict[str, dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        """SELECT i.item_id, i.status, i.goal_id, i.at, g.title AS goal_title, g.progress AS goal_progress,
        g.status AS goal_status FROM jr_life_items i
        LEFT JOIN jr_goals g ON g.id = i.goal_id AND g.owner_id = ?
        WHERE i.life_id = ?""",
        (me["id"], life_id),
    )
    out = {}
    for row in rows:
        status = row["status"]
        goal = None
        if row["goal_id"] and row["goal_title"] is not None:
            goal = {
                "id": row["goal_id"],
                "title": row["goal_title"],
                "progress": row["goal_progress"],
                "status": row["goal_status"],
            }
            if status == "planned" and row["goal_status"] == "completed":
                status = "done"  # the goal it became was reached
        out[row["item_id"]] = {"status": status, "goal": goal, "at": row["at"]}
    return out


def set_item(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    life_id: str,
    item_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Tick a stage item or an interest idea: done, not for me (skipped), planned (with the goal it became), or open
    again."""
    _life_row(connection, me, life_id)
    if item_id not in stages.ITEMS and not _is_idea(item_id):
        raise LookupError("That suggestion was not found.")
    goal_id = str(raw.get("goal_id") or "").strip()
    status = kit.choice(
        raw.get("status"),
        "the status",
        ITEM_STATUSES,
        default="planned" if goal_id else None,
    )
    if goal_id:
        service.get_goal(connection, me, goal_id)  # their own goal, or LookupError
        if status == "open":
            status = "planned"
    if status == "open":
        connection.execute(
            "DELETE FROM jr_life_items WHERE life_id = ? AND item_id = ?",
            (life_id, item_id),
        )
    else:
        connection.execute(
            """INSERT INTO jr_life_items (life_id, item_id, status, goal_id, at) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT (life_id, item_id) DO UPDATE SET status = excluded.status, goal_id = excluded.goal_id,
            at = excluded.at""",
            (life_id, item_id, status, goal_id, kit.now()),
        )
    connection.commit()
    return {
        "item_id": item_id,
        **_statuses(connection, me, life_id).get(
            item_id, {"status": "open", "goal": None, "at": ""}
        ),
    }


def _item_view(
    item: dict[str, Any], stage: dict[str, Any], status: dict[str, Any] | None
) -> dict[str, Any]:
    return {
        **item,
        "stage": stage["id"],
        "stage_title": stage["title"],
        "goal_area": stages.GOAL_AREA[item["area"]],
        "status": (status or {}).get("status", "open"),
        "goal": (status or {}).get("goal"),
    }


def _counts(items: list[dict[str, Any]]) -> dict[str, int]:
    kept = [item for item in items if item["status"] != "skipped"]
    return {
        "done": sum(1 for item in kept if item["status"] == "done"),
        "planned": sum(1 for item in kept if item["status"] == "planned"),
        "total": len(kept),
    }


# ---------------------------------------------------------------------------
# the life book: records
# ---------------------------------------------------------------------------


def _number(value: Any, name: str, maximum: float) -> float | None:
    if value is None or str(value).strip() == "":
        return None
    try:
        number = float(str(value).strip().replace(",", "."))
    except ValueError:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} must be a number like 72.5."
        ) from None
    if not 0 < number <= maximum:
        raise ValueError(
            f"{name[:1].upper()}{name[1:]} must be between 0 and {maximum:g}."
        )
    return round(number, 1)


def _clean_record(
    kind: str, raw: dict[str, Any], current: dict[str, Any] | None, now: date
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    if current:
        merged.setdefault("data", current.get("data"))
    titles = {
        "vaccine": "the vaccine",
        "checkup": "the check-up",
        "education": "the school or course",
        "work": "where you worked",
        "home": "the place",
        "milestone": "what happened",
    }
    values: dict[str, Any] = {
        "title": kit.text(
            merged.get("title"),
            titles.get(kind, "a name"),
            max_len=160,
            required=kind != "growth",
        ),
        "detail": kit.text(merged.get("detail"), "the details", max_len=200),
        "day": kit.day(
            merged.get("day"),
            "the date",
            required=kind not in ("education", "work", "home"),
        ),
        "end_day": kit.day(merged.get("end_day"), "the end date"),
        "next_due": kit.day(merged.get("next_due"), "the date the next one is due"),
        "notes": kit.text(
            merged.get("notes"), "the notes", max_len=2000, multiline=True
        ),
        "data": "{}",
    }
    if values["day"] and values["day"] > now.isoformat() and kind != "milestone":
        raise ValueError(
            "The date cannot be in the future. Put the next one in Next due."
        )
    if values["end_day"] and values["day"] and values["end_day"] < values["day"]:
        raise ValueError("The end date must come after the start.")
    if values["next_due"] and values["day"] and values["next_due"] < values["day"]:
        raise ValueError("The next one must be due after this one.")
    if kind not in ("vaccine", "checkup"):
        values["next_due"] = ""
    if kind not in ("education", "work", "home"):
        values["end_day"] = ""
    if kind == "growth":
        data = (
            kit.loads(merged.get("data"), {})
            if isinstance(merged.get("data"), str)
            else {}
        )
        source = {
            **data,
            **{k: raw[k] for k in ("height_cm", "weight_kg", "head_cm") if k in raw},
        }
        measured = {
            "height_cm": _number(source.get("height_cm"), "the height", 260),
            "weight_kg": _number(source.get("weight_kg"), "the weight", 400),
            "head_cm": _number(source.get("head_cm"), "the head size", 70),
        }
        if all(value is None for value in measured.values()):
            raise ValueError("Enter the height, the weight or the head size.")
        values["data"] = kit.dumps({k: v for k, v in measured.items() if v is not None})
        values["title"] = values["title"] or "Growth"
    return values


def _record_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "kind": row["kind"],
        "title": row["title"],
        "detail": row["detail"],
        "day": row["day"],
        "end_day": row["end_day"],
        "next_due": row["next_due"],
        "notes": row["notes"],
        **(kit.loads(row["data"], {}) or {}),
    }


def save_record(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    life_id: str,
    raw: dict[str, Any],
    record_id: str = "",
) -> dict[str, Any]:
    now = service.today(connection)
    stamp = kit.now()
    if record_id:
        current = kit.fetch_one(
            connection,
            "SELECT * FROM jr_records WHERE id = ? AND owner_id = ?",
            (record_id, me["id"]),
        )
        if not current:
            raise LookupError("That record was not found.")
        values = _clean_record(current["kind"], raw, current, now)
        kit.update_row(
            connection, "jr_records", record_id, {**values, "updated_at": stamp}
        )
    else:
        _life_row(connection, me, life_id)
        kind = kit.choice(raw.get("kind"), "the kind of record", RECORD_KINDS)
        if (
            kit.count(
                connection,
                "SELECT COUNT(*) FROM jr_records WHERE life_id = ?",
                (life_id,),
            )
            >= MAX_RECORDS
        ):
            raise ValueError(f"A life book can hold {MAX_RECORDS} records.")
        values = _clean_record(kind, raw, None, now)
        record_id = kit.new_id()
        connection.execute(
            """INSERT INTO jr_records (id, owner_id, life_id, kind, title, detail, day, end_day, next_due, data, notes,
            created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                record_id,
                me["id"],
                life_id,
                kind,
                values["title"],
                values["detail"],
                values["day"],
                values["end_day"],
                values["next_due"],
                values["data"],
                values["notes"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    return _record_view(kit.require_row(connection, "jr_records", record_id, "record"))


def delete_record(
    connection: sqlite3.Connection, me: dict[str, Any], record_id: str
) -> None:
    found = kit.fetch_one(
        connection,
        "SELECT id FROM jr_records WHERE id = ? AND owner_id = ?",
        (record_id, me["id"]),
    )
    if not found:
        raise LookupError("That record was not found.")
    connection.execute("DELETE FROM jr_records WHERE id = ?", (record_id,))
    connection.commit()


def add_milestone(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    life_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """A moment of a life: on one's own timeline, or in the book of someone you look after."""
    row = _life_row(connection, me, life_id)
    if row["relation"] != "self":
        record = save_record(connection, me, life_id, {**raw, "kind": "milestone"})
        return {"record": record}
    stage = str(raw.get("stage") or "")
    event = service.save_event(
        connection,
        me,
        {
            "title": raw.get("title"),
            "event_date": raw.get("day"),
            "kind": raw.get("kind") or _EVENT_KIND.get(stage, "achievement"),
            "location": raw.get("location"),
            "description": raw.get("notes"),
        },
    )
    return {"event": event}


def _due_rows(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str = ""
) -> list[dict[str, Any]]:
    """The latest vaccine or check-up of each name, with a next date: a later dose answers an earlier reminder."""
    sql = """SELECT r.*, l.name AS life_name, l.relation AS relation FROM jr_records r
        JOIN jr_lives l ON l.id = r.life_id
        WHERE r.owner_id = ? AND r.kind IN ('vaccine', 'checkup') AND l.died_on = ''"""
    params: list[Any] = [me["id"]]
    if life_id:
        sql += " AND r.life_id = ?"
        params.append(life_id)
    latest: dict[tuple[str, str, str], dict[str, Any]] = {}
    for row in kit.fetch_all(connection, sql + " ORDER BY r.day, r.created_at", params):
        latest[(row["life_id"], row["kind"], row["title"].lower())] = row
    return [row for row in latest.values() if row["next_due"]]


def due(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    days: int = DUE_DAYS,
    life_id: str = "",
) -> list[dict[str, Any]]:
    """Vaccines and check-ups due within ``days`` (or late by up to LATE_DAYS), soonest first."""
    now = service.today(connection)
    out = []
    for row in _due_rows(connection, me, life_id):
        left = service.days_until(row["next_due"], now)
        if left is None or left > days or left < -LATE_DAYS:
            continue
        out.append(
            {
                "id": row["id"],
                "life_id": row["life_id"],
                "who": me["name"] if row["relation"] == "self" else row["life_name"],
                "self": row["relation"] == "self",
                "kind": row["kind"],
                "title": row["title"],
                "detail": row["detail"],
                "next_due": row["next_due"],
                "days_left": left,
            }
        )
    return sorted(out, key=lambda item: (item["next_due"], item["title"]))


# ---------------------------------------------------------------------------
# the whole book
# ---------------------------------------------------------------------------


def _moments(
    connection: sqlite3.Connection, me: dict[str, Any], row: dict[str, Any]
) -> list[dict[str, Any]]:
    """The recorded moments of a life: one's own timeline events, or the milestones in someone else's book."""
    if row["relation"] == "self":
        return [
            {
                "id": event["id"],
                "type": "event",
                "title": event["title"],
                "day": event["event_date"],
                "kind": event["kind"],
            }
            for event in kit.fetch_all(
                connection,
                "SELECT id, title, event_date, kind FROM jr_events WHERE owner_id = ? ORDER BY event_date",
                (me["id"],),
            )
        ]
    return [
        {
            "id": record["id"],
            "type": "record",
            "title": record["title"],
            "day": record["day"],
            "kind": "milestone",
        }
        for record in kit.fetch_all(
            connection,
            "SELECT id, title, day FROM jr_records WHERE life_id = ? AND kind = 'milestone' ORDER BY day",
            (row["id"],),
        )
    ]


def book(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str = ""
) -> dict[str, Any]:
    """Everything about one life (one's own when ``life_id`` is empty): its stage, the road of every stage with
    its checklist and milestones, the ideas for its interests, the records and what is due, and the wishes."""
    row = _life_row(connection, me, life_id) if life_id else _self_row(connection, me)
    now = service.today(connection)
    view = life_view(me, row, now)
    statuses = _statuses(connection, me, row["id"])
    born = _parse(view["birth_date"])
    moments = _moments(connection, me, row)
    current = view["stage"]["id"]
    order = [stage["id"] for stage in stages.STAGES]
    position = order.index(current)
    road = []
    for index, stage in enumerate(stages.STAGES):
        if stage["id"] == "remembered" and not view["passed"]:
            continue
        if stage["id"] == "pregnancy" and view["self"] and current != "pregnancy":
            continue  # before birth is kept for a child you look after; your own road starts at birth
        items = [
            _item_view(item, stage, statuses.get(item["id"])) for item in stage["items"]
        ]
        recorded = {m["title"].strip().lower(): m for m in moments}
        stage_moments = []
        if born and stage["from_months"] is not None:
            start = _add_months(born, stage["from_months"])
            end = _add_months(born, stage["to_months"])
            stage_moments = [
                m
                for m in moments
                if m["day"] and start.isoformat() <= m["day"] < end.isoformat()
            ]
        elif stage["id"] == "remembered":
            stage_moments = [
                m for m in moments if m["day"] and m["day"] >= view["died_on"]
            ]
        road.append(
            {
                "id": stage["id"],
                "title": stage["title"],
                "label": stage["label"],
                "description": stage["description"],
                "state": "current"
                if index == position
                else ("past" if index < position else "future"),
                "starts_on": _add_months(born, stage["from_months"]).isoformat()
                if born
                and stage["from_months"] is not None
                and stage["from_months"] >= 0
                else "",
                "items": items,
                **_counts(items),
                "milestones": [
                    {
                        "title": m["title"],
                        "recorded": recorded.get(m["title"].strip().lower()),
                    }
                    for m in stage["milestones"]
                ],
                "moments": stage_moments,
            }
        )
    # Important things from earlier stages that are still open come along to the current one.
    carried = [
        _item_view(item, stage, statuses.get(item["id"]))
        for stage in stages.STAGES[:position]
        for item in stage["items"]
        if item["carry"]
        and statuses.get(item["id"], {}).get("status", "open") == "open"
        and not view["passed"]
    ]
    ideas = [
        {
            **idea,
            **{
                "status": statuses.get(idea["id"], {}).get("status", "open"),
                "goal": statuses.get(idea["id"], {}).get("goal"),
            },
        }
        for idea in stages.ideas_for(current, view["interests"])
    ]
    records: dict[str, list[dict[str, Any]]] = {kind: [] for kind in RECORD_KINDS}
    for record in kit.fetch_all(
        connection,
        "SELECT * FROM jr_records WHERE life_id = ? ORDER BY day DESC, created_at DESC",
        (row["id"],),
    ):
        records[record["kind"]].append(_record_view(record))
    now_stage = next(stage for stage in road if stage["state"] == "current")
    wishes = None
    if view["self"] and life.is_adult(me):
        saved = kit.loads(row["wishes"], {}) or {}
        wishes = {
            "values": {
                key: saved.get(key, "") for key in (*WISH_IDS, "organ_donation")
            },
            "fields": list(WISHES),
            "filled": sum(1 for key in WISH_IDS if saved.get(key)),
            "total": len(WISH_IDS),
            "updated_at": row["wishes_at"],
        }
    return {
        "life": view,
        "today": now.isoformat(),
        "road": road,
        "current": {
            **{key: now_stage[key] for key in ("done", "planned", "total")},
            "carried": carried,
        },
        "ideas": ideas,
        "records": records,
        "due": due(connection, me, life_id=row["id"], days=365),
        "wishes": wishes,
        "may_keep_others": may_keep_others(me),
        "areas": list(stages.AREAS),
        "interests": [
            {"id": interest["id"], "title": interest["title"], "mark": interest["mark"]}
            for interest in stages.INTERESTS
        ],
        "relations": list(RELATIONS[1:]),
        "blood_groups": list(BLOOD_GROUPS[1:]),
    }


def _add_months(day: date, months: int) -> date:
    total = day.year * 12 + day.month - 1 + months
    year, month = divmod(total, 12)
    month += 1
    for last in (31, 30, 29, 28):
        try:
            return date(year, month, min(day.day, last))
        except ValueError:
            continue
    return date(year, month, 28)


def summary(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """For the overview: one's own stage, how much of it is done, the next few things, and what is due."""
    data = book(connection, me)
    current = next(stage for stage in data["road"] if stage["state"] == "current")
    upcoming = [
        item
        for item in current["items"] + data["current"]["carried"]
        if item["status"] == "open"
    ]
    others = kit.count(
        connection,
        "SELECT COUNT(*) FROM jr_lives WHERE owner_id = ? AND relation != 'self'",
        (me["id"],),
    )
    return {
        "life": data["life"],
        "done": data["current"]["done"],
        "total": data["current"]["total"] + len(data["current"]["carried"]),
        "next": [
            {
                "id": item["id"],
                "title": item["title"],
                "area": item["area"],
                "stage_title": item["stage_title"],
            }
            for item in upcoming[:3]
        ],
        "ideas": [idea for idea in data["ideas"] if idea["status"] == "open"][:2],
        "others": others,
        "due": due(connection, me),
        "wishes": data["wishes"]["filled"] if data["wishes"] else None,
    }


# ---------------------------------------------------------------------------
# wishes and legacy
# ---------------------------------------------------------------------------


def save_wishes(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    if not life.is_adult(me):
        raise PermissionError("Wishes and legacy are for adults.")
    row = _self_row(connection, me)
    saved = kit.loads(row["wishes"], {}) or {}
    for key in WISH_IDS:
        if key in raw:
            saved[key] = kit.text(raw[key], "your wishes", max_len=4000, multiline=True)
    if "organ_donation" in raw:
        saved["organ_donation"] = kit.choice(
            raw["organ_donation"], "organ donation", ORGAN_CHOICES, default=""
        )
    kit.update_row(
        connection,
        "jr_lives",
        row["id"],
        {"wishes": kit.dumps(saved), "wishes_at": kit.now(), "updated_at": kit.now()},
    )
    connection.commit()
    return book(connection, me)["wishes"] or {}


def wishes_text(connection: sqlite3.Connection, me: dict[str, Any]) -> bytes:
    """The wishes as a plain text file to print, sign or give to someone you trust."""
    if not life.is_adult(me):
        raise PermissionError("Wishes and legacy are for adults.")
    row = _self_row(connection, me)
    saved = kit.loads(row["wishes"], {}) or {}
    lines = [
        f"My wishes: {me['name']}",
        f"Written in Monosage, last changed {row['wishes_at'][:10] or 'never'}",
        "",
    ]
    organ = {"yes": "Yes", "no": "No", "undecided": "Not decided yet"}.get(
        saved.get("organ_donation", ""), ""
    )
    for wish in WISHES:
        lines += [
            wish["title"],
            "-" * len(wish["title"]),
            saved.get(wish["id"], "") or "(not written yet)",
            "",
        ]
    lines += ["Organ donation", "--------------", organ or "(not written yet)", ""]
    return "\n".join(lines).encode("utf-8")


# ---------------------------------------------------------------------------
# a plan for this stage
# ---------------------------------------------------------------------------


def plan_context(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str = ""
) -> dict[str, Any]:
    """What the plan is made from, read before the model is asked (so the database is not held meanwhile)."""
    data = book(connection, me, life_id)
    current = next(stage for stage in data["road"] if stage["state"] == "current")
    goals = [
        goal["title"]
        for goal in service.list_goals(connection, me)
        if goal["status"] == "active"
    ][:8]
    return {
        "life": data["life"],
        "open": [
            item
            for item in current["items"] + data["current"]["carried"]
            if item["status"] == "open"
        ],
        "ideas": [idea for idea in data["ideas"] if idea["status"] == "open"],
        "goals": goals if data["life"]["self"] else [],
    }


def plan(context: dict[str, Any]) -> dict[str, Any]:
    """Five or six next steps for this stage of the life: the writing model's when it runs, or the checklist's and
    the interests' own."""
    view = context["life"]
    months = view["age_months"]
    if view["passed"]:
        age = "a person who has died; the plan is for the family left behind"
    elif months is None or months < 0:
        age = "a baby not yet born"
    elif months < 24:
        age = f"a baby of {months} months"
    else:
        age = f"a person of {months // 12} years"
    interests = [stages.INTERESTS_BY_ID[i]["title"] for i in view["interests"]]
    fallback_steps = [
        {
            "title": item["title"],
            "description": item["description"],
            "area": item["area"],
        }
        for item in context["open"][:4]
    ] + [
        {"title": idea["title"], "description": idea["description"], "area": "purpose"}
        for idea in context["ideas"][:2]
    ]
    prompt = (
        "You are a wise, warm life coach. Suggest the 5 or 6 most useful next steps for this stage of a life, "
        "practical and specific, in plain friendly words. Health steps say what to ask a doctor about, never doses "
        "or diagnoses. "
        'Reply with JSON only, like {"summary": "two sentences", "steps": [{"title": "...", "description": "...", '
        '"area": "health|mind|heart|money|purpose|papers|legacy"}]}.\n'
        f"Who: {age}, written for {'themselves' if view['self'] else 'the person who looks after them'}.\n"
        f"Stage of life: {view['stage']['title']} ({view['stage']['label']}): {view['stage']['description']}\n"
        + (f"Interests: {', '.join(interests)}\n" if interests else "")
        + (
            f"Their goals now: {'; '.join(context['goals'])}\n"
            if context["goals"]
            else ""
        )
        + (
            "Still to do on their checklist: "
            + "; ".join(item["title"] for item in context["open"][:10])
            + "\n"
            if context["open"]
            else ""
        )
    )
    answer = kit.draft(prompt, {"summary": "", "steps": []})
    steps = []
    for step in answer.get("steps") or []:
        if not isinstance(step, dict):
            continue
        title = " ".join(str(step.get("title") or "").split())[:120]
        if not title:
            continue
        area = str(step.get("area") or "").strip().lower()
        steps.append(
            {
                "title": title,
                "description": " ".join(str(step.get("description") or "").split())[
                    :300
                ],
                "area": area if area in stages.AREA_IDS else "purpose",
            }
        )
    if answer.get("source") == "model" and len(steps) >= 2:
        return {
            "summary": str(answer.get("summary") or "")[:600],
            "steps": steps[:6],
            "source": "model",
        }
    return {
        "summary": view["stage"]["description"],
        "steps": fallback_steps[:6],
        "source": "template",
    }


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------


def book_csv(
    connection: sqlite3.Connection, me: dict[str, Any], life_id: str
) -> tuple[bytes, str]:
    """The life book as a spreadsheet: the records and moments by date, then the checklist."""
    data = book(connection, me, life_id)
    rows = []
    for kind, records in data["records"].items():
        for record in records:
            measured = ", ".join(
                f"{label} {record[key]}"
                for key, label in (
                    ("height_cm", "height cm"),
                    ("weight_kg", "weight kg"),
                    ("head_cm", "head cm"),
                )
                if record.get(key) is not None
            )
            rows.append(
                {
                    "date": record["day"],
                    "type": kind,
                    "title": record["title"],
                    "detail": measured or record["detail"],
                    "until": record["end_day"],
                    "next_due": record["next_due"],
                    "notes": record["notes"],
                }
            )
    if data["life"]["self"]:
        for stage in data["road"]:
            for moment in stage["moments"]:
                rows.append(
                    {
                        "date": moment["day"],
                        "type": "timeline",
                        "title": moment["title"],
                    }
                )
    rows.sort(key=lambda r: r.get("date") or "")
    for stage in data["road"]:
        for item in stage["items"]:
            if item["status"] != "open":
                rows.append(
                    {
                        "type": f"checklist ({stage['title']})",
                        "title": item["title"],
                        "detail": item["status"],
                    }
                )
    name = (
        re.sub(r"[^A-Za-z0-9]+", "-", data["life"]["name"]).strip("-").lower() or "life"
    )
    return (
        kit.csv_bytes(
            rows, ["date", "type", "title", "detail", "until", "next_due", "notes"]
        ),
        f"life-book-{name}.csv",
    )
