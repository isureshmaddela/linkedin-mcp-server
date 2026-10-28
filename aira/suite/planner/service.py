"""Planner rules: chores and their repeats, points and rewards, shopping lists, budget, bills and notes.

Every function takes the person asking (``me``, from :func:`aira.suite.life.current`) and enforces who may do what:

* Chores: adults see and manage every task; children and teens see their own, "anyone" and "everyone" tasks,
  create tasks only for themselves and never award themselves points.
* Points come from chores; rewards cost points and need an adult's approval.
* Shopping lists and shared notes are for everyone; a link to a list is made by an adult.
* Budget and bills are for adults only (``PermissionError`` for anyone else).
* Private notes are their owner's.
"""

from __future__ import annotations

import calendar
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.suite import kit, life
from aira.suite.planner import store

APP = store.APP
REPEATS = ("none", "daily", "weekdays", "weekly", "monthly", "every_n")
BILL_REPEATS = ("monthly", "yearly", "once")
EVERYONE, ANYONE = "everyone", ""
DAY_NAMES = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
_TIME = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
DEFAULT_CATEGORIES = (
    "Groceries",
    "Home",
    "Bills",
    "Transport",
    "Health",
    "Children",
    "Fun",
    "Other",
)

# Shopping aisles, in the order a shop is usually walked, and the words that put an item in each.
AISLES = (
    "fruit & veg",
    "bakery",
    "dairy",
    "meat & fish",
    "pantry",
    "frozen",
    "drinks",
    "household",
    "pharmacy",
    "other",
)
AISLE_WORDS: dict[str, tuple[str, ...]] = {
    "fruit & veg": (
        "apple",
        "banana",
        "orange",
        "lemon",
        "lime",
        "grape",
        "berry",
        "berries",
        "pear",
        "peach",
        "mango",
        "melon",
        "tomato",
        "potato",
        "onion",
        "garlic",
        "carrot",
        "lettuce",
        "salad",
        "spinach",
        "cucumber",
        "pepper",
        "broccoli",
        "cabbage",
        "mushroom",
        "avocado",
        "herb",
        "ginger",
        "fruit",
        "veg",
    ),
    "bakery": (
        "bread",
        "roll",
        "bun",
        "bagel",
        "croissant",
        "cake",
        "muffin",
        "tortilla",
        "pita",
        "loaf",
        "baguette",
    ),
    "dairy": (
        "milk",
        "cheese",
        "butter",
        "yogurt",
        "yoghurt",
        "cream",
        "egg",
        "eggs",
        "curd",
        "paneer",
        "ghee",
    ),
    "meat & fish": (
        "chicken",
        "beef",
        "pork",
        "lamb",
        "mutton",
        "fish",
        "salmon",
        "tuna",
        "prawn",
        "shrimp",
        "sausage",
        "bacon",
        "ham",
        "turkey",
        "mince",
    ),
    "pantry": (
        "rice",
        "pasta",
        "flour",
        "sugar",
        "salt",
        "oil",
        "beans",
        "lentil",
        "dal",
        "cereal",
        "oats",
        "tea",
        "coffee",
        "spice",
        "sauce",
        "jam",
        "honey",
        "biscuit",
        "cookie",
        "noodle",
        "soup",
        "can ",
        "tin ",
    ),
    "frozen": ("frozen", "ice cream", "peas"),
    "drinks": ("juice", "water", "soda", "cola", "beer", "wine", "drink"),
    "household": (
        "soap",
        "detergent",
        "toilet",
        "tissue",
        "paper towel",
        "bin bag",
        "trash bag",
        "sponge",
        "cleaner",
        "bleach",
        "battery",
        "batteries",
        "bulb",
        "foil",
        "wrap",
        "dish",
    ),
    "pharmacy": (
        "medicine",
        "tablet",
        "pill",
        "plaster",
        "bandage",
        "vitamin",
        "paracetamol",
        "ibuprofen",
        "toothpaste",
        "toothbrush",
        "shampoo",
        "razor",
        "sunscreen",
        "nappy",
        "diaper",
        "cough",
    ),
}


# ---------------------------------------------------------------------------
# time and people
# ---------------------------------------------------------------------------


def local_now(connection: sqlite3.Connection) -> datetime:
    zone = kit.business(connection).get("timezone") or "UTC"
    try:
        return datetime.now(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo("UTC"))


def today(connection: sqlite3.Connection) -> date:
    return local_now(connection).date()


def local_day(connection: sqlite3.Connection, stamp: str) -> date:
    """The household's calendar day of a stored UTC time."""
    return kit.parse_time(stamp).astimezone(local_now(connection).tzinfo).date()


def people(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {p["id"]: life.public(p) for p in life.profiles(connection)}


def require_adult(me: dict[str, Any]) -> None:
    if not life.is_adult(me):
        raise PermissionError("Budget and bills are for adults.")


def _name(everyone: dict[str, dict[str, Any]], profile_id: str) -> str:
    if profile_id.startswith("guest:"):
        return profile_id[6:] or "A guest"
    return str(everyone.get(profile_id, {}).get("name") or "")


# ---------------------------------------------------------------------------
# chores and tasks
# ---------------------------------------------------------------------------


def _month_day(year: int, month: int, day: int) -> date:
    return date(year, month, min(max(1, day), calendar.monthrange(year, month)[1]))


def next_due(task: dict[str, Any], after: date) -> date | None:
    """The next due day of a repeating task after ``after`` (its current due day), or None."""
    rule = task.get("repeat") or "none"
    if rule == "daily":
        return after + timedelta(days=1)
    if rule == "weekdays":
        step = after + timedelta(days=1)
        while step.weekday() >= 5:
            step += timedelta(days=1)
        return step
    if rule == "weekly":
        days = sorted(
            {
                int(d)
                for d in str(task.get("repeat_days") or "").split(",")
                if d.strip().isdigit()
            }
        )
        if not days:
            return after + timedelta(days=7)
        for offset in range(1, 8):
            step = after + timedelta(days=offset)
            if step.weekday() in days:
                return step
        return after + timedelta(days=7)
    if rule == "monthly":
        wanted = int(task.get("repeat_day") or after.day)
        year, month = (
            (after.year + 1, 1) if after.month == 12 else (after.year, after.month + 1)
        )
        return _month_day(year, month, wanted)
    if rule == "every_n":
        return after + timedelta(days=max(1, int(task.get("repeat_every") or 1)))
    return None


def _rotation(
    value: Any, everyone: dict[str, dict[str, Any]], *, strict: bool = True
) -> list[str]:
    """The people a chore takes turns between, in order. ``strict`` refuses someone not in the household;
    otherwise (a stored rotation) people who have since left are skipped."""
    if isinstance(value, str):
        value = [part for part in value.split(",") if part.strip()]
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for raw in value[:12]:
        pid = str(raw or "").strip()
        if pid not in everyone:
            if strict:
                raise ValueError("Choose the people who take turns from the household.")
            continue
        if pid not in out:
            out.append(pid)
    return out


def live_rotation(value: Any, everyone: dict[str, dict[str, Any]]) -> list[str]:
    """A stored rotation as it is now: the people still in the household, or none when fewer than two are left
    (one person does not take turns)."""
    rotation = _rotation(value, everyone, strict=False)
    return rotation if len(rotation) >= 2 else []


def next_turn(
    rotation: list[str], current: str, everyone: dict[str, dict[str, Any]]
) -> str:
    """Whose turn comes after ``current`` (the first person when ``current`` is not one of them)."""
    live = [pid for pid in rotation if pid in everyone]
    if not live:
        return current
    if current in live:
        return live[(live.index(current) + 1) % len(live)]
    return live[0]


def visible_to(task: dict[str, Any], me: dict[str, Any]) -> bool:
    if life.is_adult(me):
        return True
    return (
        task["assignee"] in (me["id"], ANYONE, EVERYONE)
        or task["created_by"] == me["id"]
    )


def _clean_task(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    me: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    everyone = people(connection)
    assignee = str(merged.get("assignee") or "")
    stored_turns = live_rotation((current or {}).get("rotation"), everyone)
    if (
        current is not None
        and "assignee" not in raw
        and assignee not in (ANYONE, EVERYONE)
        and assignee not in everyone
    ):
        # The person it was for has left the household: the next one in the turns, or anyone.
        assignee = (
            next_turn(stored_turns, assignee, everyone) if stored_turns else ANYONE
        )
    if assignee not in (ANYONE, EVERYONE) and assignee not in everyone:
        raise ValueError("Choose who the task is for.")
    if not life.is_adult(me) and assignee not in (me["id"], ANYONE):
        raise PermissionError("You can add tasks for yourself.")
    due_date = kit.day(merged.get("due_date"), "the due day")
    due_time = kit.text(merged.get("due_time"), "the time", max_len=5)
    if due_time and not _TIME.match(due_time):
        raise ValueError("Write the time like 18:30.")
    if due_time and not due_date:
        # A time is for a reminder, and a reminder needs a day: today, unless another day is chosen.
        due_date = today(connection).isoformat()
    repeat = kit.choice(merged.get("repeat"), "the repeat", REPEATS, default="none")
    days = merged.get("repeat_days") or []
    if isinstance(days, str):
        days = [d for d in days.split(",") if d.strip()]
    day_list = sorted({kit.integer(d, "a weekday", minimum=0, maximum=6) for d in days})
    if repeat == "weekly" and not day_list:
        raise ValueError("Choose the days of the week it repeats on.")
    if repeat != "none" and not due_date:
        due_date = today(connection).isoformat()
    repeat_day = kit.integer(
        merged.get("repeat_day"),
        "the day of the month",
        default=0,
        minimum=0,
        maximum=31,
    )
    if repeat == "monthly" and not repeat_day:
        repeat_day = date.fromisoformat(due_date).day
    repeat_every = kit.integer(
        merged.get("repeat_every"), "how many days", default=0, minimum=0, maximum=365
    )
    if repeat == "every_n" and repeat_every < 1:
        raise ValueError("Say every how many days it repeats.")
    points = kit.integer(
        merged.get("points"), "the points", default=0, minimum=0, maximum=1000
    )
    if (
        points
        and not life.is_adult(me)
        and int((current or {}).get("points") or 0) != points
    ):
        raise PermissionError("An adult sets the points for a chore.")
    rotation = _rotation(merged.get("rotation"), everyone, strict="rotation" in raw)
    before = _rotation((current or {}).get("rotation"), everyone, strict=False)
    if rotation != before and not life.is_adult(me):
        raise PermissionError("An adult chooses who takes turns.")
    if len(rotation) < 2 and rotation == before:
        # Someone in the turns left the household and one person is left: it is their chore now.
        if rotation and assignee not in (ANYONE, EVERYONE, *rotation):
            assignee = rotation[0]
        rotation = []
    if rotation:
        if len(rotation) < 2:
            raise ValueError("Choose at least two people to take turns.")
        if repeat == "none":
            raise ValueError(
                "Taking turns is for a chore that repeats. Choose how often it repeats."
            )
        chosen = "assignee" in raw and (
            current is None or str(raw.get("assignee") or "") != current["assignee"]
        )
        if assignee not in rotation:
            if chosen and assignee not in (ANYONE, EVERYONE):
                who = _name(everyone, assignee) or "That person"
                raise ValueError(
                    f"{who} is not one of the people taking turns. Choose one of them in Who, or tick {who} "
                    "under Take turns too."
                )
            # Anyone, Everyone, or the person whose turn it was is no longer in the turns: the next one's.
            assignee = next_turn(rotation, assignee, everyone)
    return {
        "title": kit.text(
            merged.get("title"), "what needs doing", max_len=120, required=True
        ),
        "notes": kit.text(merged.get("notes"), "notes", max_len=1000, multiline=True),
        "assignee": assignee,
        "due_date": due_date,
        "due_time": due_time,
        "repeat": repeat,
        "repeat_days": ",".join(str(d) for d in day_list),
        "repeat_day": repeat_day,
        "repeat_every": repeat_every,
        "points": points,
        "rotation": ",".join(rotation),
    }


def _done_by(connection: sqlite3.Connection, task_id: str) -> list[str]:
    return [
        r["profile_id"]
        for r in kit.fetch_all(
            connection,
            "SELECT profile_id FROM pn_task_done WHERE task_id = ? ORDER BY at",
            (task_id,),
        )
    ]


def task_view(
    connection: sqlite3.Connection,
    task: dict[str, Any],
    me: dict[str, Any],
    everyone: dict[str, dict[str, Any]],
    now_day: date,
) -> dict[str, Any]:
    done_list = _done_by(connection, task["id"]) if task["assignee"] == EVERYONE else []
    mine_done = task["status"] == "done" or me["id"] in done_list
    overdue = (
        bool(task["due_date"])
        and task["status"] == "open"
        and task["due_date"] < now_day.isoformat()
        and not mine_done
    )
    if task["assignee"] == EVERYONE:
        who = "Everyone"
    elif task["assignee"] == ANYONE:
        who = "Anyone"
    else:
        who = _name(everyone, task["assignee"]) or "Someone"
    rotation = live_rotation(task.get("rotation"), everyone)
    return {
        "id": task["id"],
        "title": task["title"],
        "notes": task["notes"],
        "assignee": task["assignee"],
        "assignee_name": who,
        "due_date": task["due_date"],
        "due_time": task["due_time"],
        "repeat": task["repeat"],
        "repeat_days": [int(d) for d in task["repeat_days"].split(",") if d],
        "repeat_day": task["repeat_day"],
        "repeat_every": task["repeat_every"],
        "repeat_label": repeat_label(task),
        "rotation": rotation,
        "rotation_names": [_name(everyone, pid) or "Someone" for pid in rotation],
        "next_turn": _name(everyone, next_turn(rotation, task["assignee"], everyone))
        if rotation
        else "",
        "points": task["points"],
        "status": task["status"],
        "done": task["status"] == "done",
        "done_by_me": mine_done,
        "done_by": [_name(everyone, p) for p in done_list]
        if done_list
        else ([_name(everyone, task["done_by"])] if task["done_by"] else []),
        "done_at": task["done_at"],
        "overdue": overdue,
        "mine": task["assignee"] in (me["id"], ANYONE, EVERYONE),
        "can_edit": life.is_adult(me) or task["created_by"] == me["id"],
        "created_by": task["created_by"],
    }


def repeat_label(task: dict[str, Any]) -> str:
    rule = task.get("repeat") or "none"
    if rule == "daily":
        return "Every day"
    if rule == "weekdays":
        return "Every weekday"
    if rule == "weekly":
        days = [
            DAY_NAMES[int(d)]
            for d in str(task.get("repeat_days") or "").split(",")
            if d.strip().isdigit()
        ]
        return "Every " + ", ".join(days) if days else "Every week"
    if rule == "monthly":
        return f"Every month on day {task.get('repeat_day') or ''}".strip()
    if rule == "every_n":
        return f"Every {task.get('repeat_every')} days"
    return ""


def get_task(
    connection: sqlite3.Connection, task_id: str, me: dict[str, Any]
) -> dict[str, Any]:
    task = store.one(connection, "pn_tasks", task_id, "task")
    if not visible_to(task, me):
        raise LookupError("That task was not found.")
    return task


def list_tasks(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    scope: str = "mine",
    status: str = "open",
    q: str = "",
    person: str = "",
) -> list[dict[str, Any]]:
    """Tasks this person may see. ``q`` searches the title and notes; ``person`` keeps one person's tasks
    (plus "anyone" and "everyone" tasks, which are theirs too)."""
    scope = kit.choice(scope, "the scope", ("mine", "family"), default="mine")
    status = kit.choice(status, "the status", ("open", "done", "all"), default="open")
    q = kit.text(q, "the search", max_len=80)
    person = kit.text(person, "the person", max_len=40)
    clauses = ["deleted_at IS NULL"]
    params: list[Any] = []
    if status != "all":
        clauses.append("status = ?")
        params.append(status)
    if q:
        pattern = (
            "%" + q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"
        )
        clauses.append("(title LIKE ? ESCAPE '\\' OR notes LIKE ? ESCAPE '\\')")
        params.extend([pattern, pattern])
    if person:
        clauses.append("assignee IN (?, ?, ?)")
        params.extend([person, ANYONE, EVERYONE])
    rows = kit.fetch_all(
        connection,
        f"SELECT * FROM pn_tasks WHERE {' AND '.join(clauses)} ORDER BY CASE WHEN due_date = '' THEN 1 ELSE 0 END, due_date, due_time, created_at",  # noqa: S608
        params,
    )
    everyone, now_day = people(connection), today(connection)
    out = []
    for row in rows:
        if not visible_to(row, me):
            continue
        if scope == "mine" and row["assignee"] not in (me["id"], ANYONE, EVERYONE):
            continue
        out.append(task_view(connection, row, me, everyone, now_day))
    if status == "done":
        out.sort(key=lambda t: t["done_at"] or "", reverse=True)
        out = out[:100]
    return out


def create_task(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    values = _clean_task(connection, raw, me)
    task_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO pn_tasks (id, title, notes, assignee, created_by, due_date, due_time, repeat, repeat_days, repeat_day,
        repeat_every, points, rotation, status, series_id, created_at, updated_at)
        VALUES (:id, :title, :notes, :assignee, :created_by, :due_date, :due_time, :repeat, :repeat_days, :repeat_day,
        :repeat_every, :points, :rotation, 'open', :series_id, :created_at, :updated_at)""",
        {
            **values,
            "id": task_id,
            "created_by": me["id"],
            "series_id": task_id,
            "created_at": stamp,
            "updated_at": stamp,
        },
    )
    kit.log(
        connection,
        APP,
        "task",
        f"{me['name']} added a task: {values['title']}",
        ref_type="task",
        ref_id=task_id,
        actor=me["name"],
    )
    connection.commit()
    return task_view(
        connection,
        store.one(connection, "pn_tasks", task_id, "task"),
        me,
        people(connection),
        today(connection),
    )


def update_task(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    task_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    task = get_task(connection, task_id, me)
    if not (life.is_adult(me) or task["created_by"] == me["id"]):
        raise PermissionError("Ask an adult to change this task.")
    values = _clean_task(connection, raw, me, task)
    if values["due_date"] != task["due_date"] and kit.fetch_one(
        connection,
        "SELECT id FROM pn_tasks WHERE series_id = ? AND due_date = ? AND id != ? AND deleted_at IS NULL",
        (task["series_id"], values["due_date"], task_id),
    ):
        # A repeating task already has its next (or last) day there; two of the same task on one day would clash.
        raise ValueError(
            "This repeating task is already on that day. Choose another day."
        )
    kit.update_row(connection, "pn_tasks", task_id, {**values, "updated_at": kit.now()})
    connection.commit()
    return task_view(
        connection,
        store.one(connection, "pn_tasks", task_id, "task"),
        me,
        people(connection),
        today(connection),
    )


def delete_task(
    connection: sqlite3.Connection, me: dict[str, Any], task_id: str
) -> None:
    task = get_task(connection, task_id, me)
    if not (life.is_adult(me) or task["created_by"] == me["id"]):
        raise PermissionError("Ask an adult to remove this task.")
    kit.update_row(
        connection,
        "pn_tasks",
        task_id,
        {"deleted_at": kit.now(), "updated_at": kit.now()},
    )
    connection.commit()


def _award(
    connection: sqlite3.Connection, profile_id: str, points: int, task: dict[str, Any]
) -> None:
    if points <= 0 or not profile_id:
        return
    connection.execute(
        "INSERT OR IGNORE INTO pn_points (id, profile_id, delta, reason, ref_type, ref_id, at) VALUES (?, ?, ?, ?, 'task', ?, ?)",
        (kit.new_id(), profile_id, points, task["title"][:120], task["id"], kit.now()),
    )


def _schedule_next(connection: sqlite3.Connection, task: dict[str, Any]) -> None:
    """The next one of a repeating task. A chore done late catches up: the next one is today or later, never
    another day already in the past. Taking turns: the next one is the next person's."""
    if (task.get("repeat") or "none") == "none" or not task["due_date"]:
        return
    upcoming = next_due(task, date.fromisoformat(task["due_date"]))
    on = today(connection)
    for _ in range(1000):
        if upcoming is None or upcoming >= on:
            break
        upcoming = next_due(task, upcoming)
    if upcoming is None:
        return
    everyone = people(connection)
    turns = _rotation(task.get("rotation"), everyone, strict=False)
    rotation = turns if len(turns) >= 2 else []
    assignee = (
        next_turn(turns, task["assignee"], everyone) if turns else task["assignee"]
    )
    stamp = kit.now()
    connection.execute(
        """INSERT OR IGNORE INTO pn_tasks (id, title, notes, assignee, created_by, due_date, due_time, repeat, repeat_days, repeat_day,
        repeat_every, points, rotation, status, series_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)""",
        (
            kit.new_id(),
            task["title"],
            task["notes"],
            assignee,
            task["created_by"],
            upcoming.isoformat(),
            task["due_time"],
            task["repeat"],
            task["repeat_days"],
            task["repeat_day"],
            task["repeat_every"],
            task["points"],
            ",".join(rotation),
            task["series_id"],
            stamp,
            stamp,
        ),
    )


def complete(
    connection: sqlite3.Connection, me: dict[str, Any], task_id: str
) -> dict[str, Any]:
    """Mark a task done (doing it twice changes nothing). A repeating task gets its next day."""
    task = get_task(connection, task_id, me)
    if task["assignee"] not in (me["id"], ANYONE, EVERYONE) and not life.is_adult(me):
        raise PermissionError("This task is someone else's.")
    stamp = kit.now()
    if task["assignee"] == EVERYONE:
        if me["id"] in _done_by(connection, task_id):
            return task_view(
                connection, task, me, people(connection), today(connection)
            )
        connection.execute(
            "INSERT OR IGNORE INTO pn_task_done (task_id, profile_id, at) VALUES (?, ?, ?)",
            (task_id, me["id"], stamp),
        )
        _award(connection, me["id"], int(task["points"]), task)
        _schedule_next(connection, task)
        everyone_ids = set(people(connection))
        if everyone_ids <= set(_done_by(connection, task_id)):
            connection.execute(
                "UPDATE pn_tasks SET status = 'done', done_by = ?, done_at = ?, updated_at = ? WHERE id = ?",
                (me["id"], stamp, stamp, task_id),
            )
    else:
        if task["status"] == "done":
            return task_view(
                connection, task, me, people(connection), today(connection)
            )
        connection.execute(
            "UPDATE pn_tasks SET status = 'done', done_by = ?, done_at = ?, updated_at = ? WHERE id = ? AND status = 'open'",
            (me["id"], stamp, stamp, task_id),
        )
        earner = (
            task["assignee"] if task["assignee"] not in (ANYONE, EVERYONE) else me["id"]
        )
        _award(connection, earner, int(task["points"]), task)
        _schedule_next(connection, task)
    kit.log(
        connection,
        APP,
        "done",
        f"{me['name']} did: {task['title']}",
        ref_type="task",
        ref_id=task_id,
        actor=me["name"],
    )
    connection.commit()
    return task_view(
        connection,
        store.one(connection, "pn_tasks", task_id, "task"),
        me,
        people(connection),
        today(connection),
    )


def undo(
    connection: sqlite3.Connection, me: dict[str, Any], task_id: str
) -> dict[str, Any]:
    """Not done after all: the task is open again and its points are taken back (the next repeat stays)."""
    task = get_task(connection, task_id, me)
    if task["assignee"] not in (me["id"], ANYONE, EVERYONE) and not life.is_adult(me):
        raise PermissionError("This task is someone else's.")
    if (
        task["assignee"] != EVERYONE
        and task["status"] == "done"
        and task["done_by"] not in ("", me["id"])
        and not life.is_adult(me)
    ):
        # A child must not take a brother's or sister's points away by un-ticking their chore.
        raise PermissionError("Someone else did this one. Ask an adult to change it.")
    if task["assignee"] == EVERYONE:
        connection.execute(
            "DELETE FROM pn_task_done WHERE task_id = ? AND profile_id = ?",
            (task_id, me["id"]),
        )
        connection.execute(
            "DELETE FROM pn_points WHERE ref_type = 'task' AND ref_id = ? AND profile_id = ?",
            (task_id, me["id"]),
        )
    else:
        connection.execute(
            "DELETE FROM pn_points WHERE ref_type = 'task' AND ref_id = ?", (task_id,)
        )
    connection.execute(
        "UPDATE pn_tasks SET status = 'open', done_by = '', done_at = NULL, updated_at = ? WHERE id = ?",
        (kit.now(), task_id),
    )
    connection.commit()
    return task_view(
        connection,
        store.one(connection, "pn_tasks", task_id, "task"),
        me,
        people(connection),
        today(connection),
    )


def due_today(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    """My open tasks due today or earlier (overdue first)."""
    now_day = today(connection).isoformat()
    return [
        t
        for t in list_tasks(connection, me, scope="mine", status="open")
        if t["due_date"] and t["due_date"] <= now_day and not t["done_by_me"]
    ]


# ---------------------------------------------------------------------------
# points and rewards
# ---------------------------------------------------------------------------


def balance(connection: sqlite3.Connection, profile_id: str) -> int:
    return kit.count(
        connection,
        "SELECT COALESCE(SUM(delta), 0) FROM pn_points WHERE profile_id = ?",
        (profile_id,),
    )


def pending_points(connection: sqlite3.Connection, profile_id: str) -> int:
    return kit.count(
        connection,
        "SELECT COALESCE(SUM(points), 0) FROM pn_reward_requests WHERE profile_id = ? AND status = 'pending'",
        (profile_id,),
    )


def rewards_view(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    everyone = people(connection)
    adult = life.is_adult(me)
    rewards = kit.fetch_all(
        connection, "SELECT * FROM pn_rewards WHERE active = 1 ORDER BY points, title"
    )
    request_rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_reward_requests ORDER BY created_at DESC, rowid DESC LIMIT 200"
        if adult
        else "SELECT * FROM pn_reward_requests WHERE profile_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 200",
        () if adult else (me["id"],),
    )
    history = kit.fetch_all(
        connection,
        "SELECT * FROM pn_points WHERE profile_id = ? ORDER BY at DESC, rowid DESC LIMIT 50",
        (me["id"],),
    )
    balances = (
        [
            {
                "profile_id": pid,
                "name": p["name"],
                "emoji": p["emoji"],
                "points": balance(connection, pid),
            }
            for pid, p in everyone.items()
        ]
        if adult
        else [
            {
                "profile_id": me["id"],
                "name": me["name"],
                "emoji": me["emoji"],
                "points": balance(connection, me["id"]),
            }
        ]
    )
    return {
        "rewards": [
            {"id": r["id"], "title": r["title"], "points": r["points"]} for r in rewards
        ],
        "balance": balance(connection, me["id"]),
        "pending": pending_points(connection, me["id"]),
        "balances": balances,
        "requests": [
            {
                "id": r["id"],
                "title": r["title"],
                "points": r["points"],
                "status": r["status"],
                "who": _name(everyone, r["profile_id"]),
                "profile_id": r["profile_id"],
                "created_at": r["created_at"],
                "decided_by": _name(everyone, r["decided_by"])
                if r["decided_by"]
                else "",
            }
            for r in request_rows
        ],
        "history": [
            {"delta": h["delta"], "reason": h["reason"], "at": h["at"]} for h in history
        ],
        "can_manage": adult,
        # Children learn that points can become pocket money, never how much: money is for adults.
        "pocket_money": pocket_rate(connection) > 0,
    }


def save_reward(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    reward_id: str = "",
) -> dict[str, Any]:
    if not life.is_adult(me):
        raise PermissionError("An adult sets the rewards.")
    title = kit.text(raw.get("title"), "the reward", max_len=120, required=True)
    points = kit.integer(raw.get("points"), "the points", minimum=1, maximum=100000)
    if reward_id:
        store.one(connection, "pn_rewards", reward_id, "reward")
        kit.update_row(
            connection, "pn_rewards", reward_id, {"title": title, "points": points}
        )
    else:
        reward_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_rewards (id, title, points, active, created_by, created_at) VALUES (?, ?, ?, 1, ?, ?)",
            (reward_id, title, points, me["id"], kit.now()),
        )
    connection.commit()
    return (
        kit.fetch_one(
            connection,
            "SELECT id, title, points FROM pn_rewards WHERE id = ?",
            (reward_id,),
        )
        or {}
    )


def remove_reward(
    connection: sqlite3.Connection, me: dict[str, Any], reward_id: str
) -> None:
    if not life.is_adult(me):
        raise PermissionError("An adult sets the rewards.")
    store.one(connection, "pn_rewards", reward_id, "reward")
    kit.update_row(connection, "pn_rewards", reward_id, {"active": 0})
    connection.commit()


def request_reward(
    connection: sqlite3.Connection, me: dict[str, Any], reward_id: str
) -> dict[str, Any]:
    reward = store.one(connection, "pn_rewards", reward_id, "reward")
    if not reward["active"]:
        raise LookupError("That reward was not found.")
    available = balance(connection, me["id"]) - pending_points(connection, me["id"])
    if available < int(reward["points"]):
        raise ValueError(
            f"You need {reward['points']} points; you have {max(0, available)} to spend."
        )
    request_id = kit.new_id()
    connection.execute(
        "INSERT INTO pn_reward_requests (id, reward_id, title, profile_id, points, status, created_at) VALUES (?, ?, ?, ?, ?, 'pending', ?)",
        (request_id, reward_id, reward["title"], me["id"], reward["points"], kit.now()),
    )
    kit.log(
        connection,
        APP,
        "reward",
        f"{me['name']} asked for: {reward['title']}",
        ref_type="reward_request",
        ref_id=request_id,
        actor=me["name"],
    )
    connection.commit()
    return {
        "id": request_id,
        "title": reward["title"],
        "points": reward["points"],
        "status": "pending",
    }


def decide_reward(
    connection: sqlite3.Connection, me: dict[str, Any], request_id: str, approve: bool
) -> dict[str, Any]:
    if not life.is_adult(me):
        raise PermissionError("An adult approves rewards.")
    row = kit.fetch_one(
        connection, "SELECT * FROM pn_reward_requests WHERE id = ?", (request_id,)
    )
    if not row:
        raise LookupError("That request was not found.")
    if row["status"] != "pending":
        return {"id": request_id, "status": row["status"]}
    if approve:
        if balance(connection, row["profile_id"]) < int(row["points"]):
            raise ValueError("They do not have enough points any more.")
        connection.execute(
            "INSERT OR IGNORE INTO pn_points (id, profile_id, delta, reason, ref_type, ref_id, at) VALUES (?, ?, ?, ?, 'reward', ?, ?)",
            (
                kit.new_id(),
                row["profile_id"],
                -int(row["points"]),
                f"Reward: {row['title']}"[:120],
                request_id,
                kit.now(),
            ),
        )
    status = "approved" if approve else "declined"
    connection.execute(
        "UPDATE pn_reward_requests SET status = ?, decided_by = ?, decided_at = ? WHERE id = ?",
        (status, me["id"], kit.now(), request_id),
    )
    connection.commit()
    return {"id": request_id, "status": status}


# ---------------------------------------------------------------------------
# pocket money: chore points an adult pays out as money (adults see the money; children see their points)
# ---------------------------------------------------------------------------

POCKET_CATEGORY = "Children"


def pocket_rate(connection: sqlite3.Connection) -> int:
    """What 100 points are worth, in cents (0 = pocket money is not set up)."""
    stored = kit.get_setting(connection, APP, "pocket", {}) or {}
    try:
        return max(0, int(stored.get("per_100_cents") or 0))
    except (TypeError, ValueError):
        return 0


def points_worth(points: int, per_100_cents: int) -> int:
    """Rounded down to the cent, so a payout never pays more than the points are worth."""
    return max(0, int(points)) * int(per_100_cents) // 100


def set_pocket_rate(
    connection: sqlite3.Connection, me: dict[str, Any], raw: dict[str, Any]
) -> dict[str, Any]:
    if not life.is_adult(me):
        raise PermissionError("An adult sets pocket money.")
    per_100 = kit.cents_value(
        raw.get("per_100_cents"), "what 100 points are worth", default=0
    )
    if per_100 > 100_000:
        raise ValueError("100 points can be worth at most 1,000.00.")
    kit.set_setting(connection, APP, "pocket", {"per_100_cents": per_100})
    connection.commit()
    return pocket(connection, me)


def pocket(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """Adults: what each person's points are worth and the payouts so far."""
    if not life.is_adult(me):
        raise PermissionError("Pocket money is managed by an adult.")
    rate = pocket_rate(connection)
    everyone = people(connection)
    rows = []
    for pid, person in everyone.items():
        points = balance(connection, pid)
        spendable = max(0, points - pending_points(connection, pid))
        rows.append(
            {
                "profile_id": pid,
                "name": person["name"],
                "emoji": person["emoji"],
                "age_group": person["age_group"],
                "points": points,
                "available": spendable,
                "worth_cents": points_worth(spendable, rate),
            }
        )
    payouts = kit.fetch_all(
        connection, "SELECT * FROM pn_payouts ORDER BY at DESC, rowid DESC LIMIT 50"
    )
    month = today(connection).strftime("%Y-%m")
    return {
        "per_100_cents": rate,
        "currency": kit.business(connection)["currency"],
        "people": rows,
        "paid_this_month_cents": sum(
            int(p["amount_cents"]) for p in payouts if p["at"][:7] == month
        ),
        "payouts": [
            {
                "id": p["id"],
                "profile_id": p["profile_id"],
                "name": _name(everyone, p["profile_id"]) or "Someone",
                "points": p["points"],
                "amount_cents": p["amount_cents"],
                "paid_by": _name(everyone, p["paid_by"]),
                "at": p["at"],
            }
            for p in payouts
        ],
    }


def pay_out(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    profile_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Turn some of a person's points into pocket money: the points are spent, the money is recorded as
    spending in the budget (Children). Only points not already promised to a reward can be paid out."""
    if not life.is_adult(me):
        raise PermissionError("An adult pays out pocket money.")
    rate = pocket_rate(connection)
    if not rate:
        raise ValueError("First set how much 100 points are worth.")
    everyone = people(connection)
    if profile_id not in everyone:
        raise LookupError("That person was not found.")
    spendable = max(
        0, balance(connection, profile_id) - pending_points(connection, profile_id)
    )
    points = kit.integer(
        raw.get("points"),
        "the points",
        default=spendable,
        minimum=1,
        maximum=10_000_000,
    )
    if points > spendable:
        raise ValueError(
            f"{everyone[profile_id]['name']} has {spendable} points to turn into pocket money."
        )
    amount = points_worth(points, rate)
    if amount < 1:
        raise ValueError("Those points are worth less than 0.01. Pay out more points.")
    payout_id, stamp = kit.new_id(), kit.now()
    name = everyone[profile_id]["name"]
    connection.execute(
        "INSERT INTO pn_points (id, profile_id, delta, reason, ref_type, ref_id, at) VALUES (?, ?, ?, 'Pocket money', 'payout', ?, ?)",
        (kit.new_id(), profile_id, -points, payout_id, stamp),
    )
    entry_id = kit.new_id()
    connection.execute(
        "INSERT INTO pn_entries (id, kind, amount_cents, day, category, note, profile_id, created_at) VALUES (?, 'expense', ?, ?, ?, ?, ?, ?)",
        (
            entry_id,
            amount,
            today(connection).isoformat(),
            POCKET_CATEGORY,
            f"Pocket money: {name}"[:200],
            me["id"],
            stamp,
        ),
    )
    connection.execute(
        "INSERT INTO pn_payouts (id, profile_id, points, amount_cents, entry_id, paid_by, at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (payout_id, profile_id, points, amount, entry_id, me["id"], stamp),
    )
    kit.log(
        connection,
        APP,
        "pocket_money",
        f"{name} got pocket money for {points} points",
        ref_type="payout",
        ref_id=payout_id,
        actor=me["name"],
    )
    connection.commit()
    return {"id": payout_id, "points": points, "amount_cents": amount, "name": name}


def undo_payout(
    connection: sqlite3.Connection, me: dict[str, Any], payout_id: str
) -> None:
    """Paid by mistake: the points come back and the spending leaves the budget."""
    if not life.is_adult(me):
        raise PermissionError("An adult pays out pocket money.")
    row = kit.fetch_one(
        connection, "SELECT * FROM pn_payouts WHERE id = ?", (payout_id,)
    )
    if not row:
        raise LookupError("That payout was not found.")
    connection.execute(
        "DELETE FROM pn_points WHERE ref_type = 'payout' AND ref_id = ?", (payout_id,)
    )
    if row["entry_id"]:
        connection.execute("DELETE FROM pn_entries WHERE id = ?", (row["entry_id"],))
    connection.execute("DELETE FROM pn_payouts WHERE id = ?", (payout_id,))
    connection.commit()


# ---------------------------------------------------------------------------
# shopping
# ---------------------------------------------------------------------------


def aisle(name: str) -> str:
    text = f" {str(name or '').lower()} "
    for key in AISLES:
        for word in AISLE_WORDS.get(key, ()):
            if re.search(rf"\b{re.escape(word.strip())}", text):
                return key
    return "other"


def _usual_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(name).lower()).strip()


def lists(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_lists WHERE deleted_at IS NULL ORDER BY created_at",
    )
    if not rows:
        stamp = kit.now()
        connection.execute(
            "INSERT INTO pn_lists (id, name, created_at, updated_at) VALUES (?, 'Groceries', ?, ?)",
            (kit.new_id(), stamp, stamp),
        )
        connection.commit()
        rows = kit.fetch_all(
            connection,
            "SELECT * FROM pn_lists WHERE deleted_at IS NULL ORDER BY created_at",
        )
    out = []
    for row in rows:
        out.append(
            {
                "id": row["id"],
                "name": row["name"],
                "open": kit.count(
                    connection,
                    "SELECT COUNT(*) FROM pn_items WHERE list_id = ? AND deleted_at IS NULL AND checked = 0",
                    (row["id"],),
                ),
                "checked": kit.count(
                    connection,
                    "SELECT COUNT(*) FROM pn_items WHERE list_id = ? AND deleted_at IS NULL AND checked = 1",
                    (row["id"],),
                ),
            }
        )
    return out


def save_list(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    list_id: str = "",
) -> dict[str, Any]:
    name = kit.text(raw.get("name"), "the list name", max_len=60, required=True)
    stamp = kit.now()
    if list_id:
        store.one(connection, "pn_lists", list_id, "list")
        kit.update_row(
            connection, "pn_lists", list_id, {"name": name, "updated_at": stamp}
        )
    else:
        list_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_lists (id, name, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
            (list_id, name, me["id"], stamp, stamp),
        )
    connection.commit()
    return {"id": list_id, "name": name}


def delete_list(
    connection: sqlite3.Connection, me: dict[str, Any], list_id: str
) -> None:
    if not life.is_adult(me):
        raise PermissionError("Ask an adult to remove a list.")
    store.one(connection, "pn_lists", list_id, "list")
    kit.update_row(
        connection,
        "pn_lists",
        list_id,
        {"deleted_at": kit.now(), "updated_at": kit.now()},
    )
    connection.commit()


def _item_view(
    item: dict[str, Any], everyone: dict[str, dict[str, Any]], *, names: bool = True
) -> dict[str, Any]:
    view = {
        "id": item["id"],
        "name": item["name"],
        "quantity": item["quantity"],
        "category": item["category"],
        "checked": bool(item["checked"]),
        "updated_at": item["updated_at"],
        "deleted": bool(item.get("deleted_at")),
    }
    if names:
        view["checked_by"] = (
            _name(everyone, item["checked_by"]) if item["checked"] else ""
        )
        view["added_by"] = _name(everyone, item["added_by"])
    return view


def _sorted(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    order = {key: index for index, key in enumerate(AISLES)}
    return sorted(
        items,
        key=lambda i: (i["checked"], order.get(i["category"], 99), i["name"].lower()),
    )


def list_detail(
    connection: sqlite3.Connection, list_id: str, *, since: str = "", names: bool = True
) -> dict[str, Any]:
    the_list = store.one(connection, "pn_lists", list_id, "list")
    everyone = people(connection)
    now = kit.now()
    if since:
        # ">=": a change written later in the same second as the cursor has the same time. Changes from that
        # second come again on the next poll, which is harmless (the page applies them by id).
        rows = kit.fetch_all(
            connection,
            "SELECT * FROM pn_items WHERE list_id = ? AND updated_at >= ? ORDER BY updated_at",
            (list_id, since),
        )
        return {
            "list": {"id": the_list["id"], "name": the_list["name"]},
            "changed": [_item_view(r, everyone, names=names) for r in rows],
            "now": now,
        }
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_items WHERE list_id = ? AND deleted_at IS NULL",
        (list_id,),
    )
    return {
        "list": {"id": the_list["id"], "name": the_list["name"]},
        "items": _sorted([_item_view(r, everyone, names=names) for r in rows]),
        "now": now,
    }


def _touch(connection: sqlite3.Connection) -> str:
    """A change time strictly after the last one, so ``since`` polling never misses a change in the same second."""
    stamp = kit.now()
    latest = connection.execute("SELECT MAX(updated_at) FROM pn_items").fetchone()[0]
    if latest and latest >= stamp:
        moment = kit.parse_time(latest) + timedelta(seconds=1)
        stamp = kit.iso(moment)
    return stamp


def add_item(
    connection: sqlite3.Connection, by: str, list_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    store.one(connection, "pn_lists", list_id, "list")
    name = kit.text(raw.get("name"), "the item", max_len=80, required=True)
    quantity = kit.text(raw.get("quantity"), "the quantity", max_len=30)
    category = raw.get("category") or aisle(name)
    category = kit.choice(category, "the aisle", AISLES, default="other")
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM pn_items WHERE list_id = ? AND deleted_at IS NULL",
            (list_id,),
        )
        >= 300
    ):
        raise ValueError("A list can hold 300 items. Clear the ticked ones first.")
    item_id = kit.new_id()
    stamp = _touch(connection)
    connection.execute(
        "INSERT INTO pn_items (id, list_id, name, quantity, category, added_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (item_id, list_id, name, quantity, category, by, stamp, stamp),
    )
    key = _usual_key(name)
    if key:
        connection.execute(
            """INSERT INTO pn_usual (key, name, category, times, last_at) VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(key) DO UPDATE SET times = times + 1, last_at = excluded.last_at, name = excluded.name, category = excluded.category""",
            (key, name, category, stamp),
        )
    connection.commit()
    return _item_view(
        store.one(connection, "pn_items", item_id, "item"), people(connection)
    )


def update_item(
    connection: sqlite3.Connection, by: str, item_id: str, raw: dict[str, Any]
) -> dict[str, Any]:
    item = store.one(connection, "pn_items", item_id, "item")
    values: dict[str, Any] = {}
    if "name" in raw:
        values["name"] = kit.text(raw["name"], "the item", max_len=80, required=True)
    if "quantity" in raw:
        values["quantity"] = kit.text(raw["quantity"], "the quantity", max_len=30)
    if "category" in raw:
        values["category"] = kit.choice(
            raw["category"], "the aisle", AISLES, default="other"
        )
    if "checked" in raw:
        checked = kit.flag(raw["checked"])
        if checked != bool(item["checked"]):
            values.update(
                {
                    "checked": 1 if checked else 0,
                    "checked_by": by if checked else "",
                    "checked_at": kit.now() if checked else None,
                }
            )
    if values:
        values["updated_at"] = _touch(connection)
        kit.update_row(connection, "pn_items", item_id, values)
        connection.commit()
    return _item_view(
        store.one(connection, "pn_items", item_id, "item"), people(connection)
    )


def delete_item(connection: sqlite3.Connection, item_id: str) -> None:
    store.one(connection, "pn_items", item_id, "item")
    stamp = _touch(connection)
    kit.update_row(
        connection, "pn_items", item_id, {"deleted_at": stamp, "updated_at": stamp}
    )
    connection.commit()


def clear_checked(connection: sqlite3.Connection, list_id: str) -> int:
    store.one(connection, "pn_lists", list_id, "list")
    stamp = _touch(connection)
    changed = connection.execute(
        "UPDATE pn_items SET deleted_at = ?, updated_at = ? WHERE list_id = ? AND checked = 1 AND deleted_at IS NULL",
        (stamp, stamp, list_id),
    ).rowcount
    connection.commit()
    return changed


def usual(
    connection: sqlite3.Connection, list_id: str = "", limit: int = 24
) -> list[dict[str, Any]]:
    """Things often bought, not already waiting on the list."""
    waiting = set()
    if list_id:
        waiting = {
            _usual_key(r["name"])
            for r in kit.fetch_all(
                connection,
                "SELECT name FROM pn_items WHERE list_id = ? AND deleted_at IS NULL AND checked = 0",
                (list_id,),
            )
        }
    rows = kit.fetch_all(
        connection, "SELECT * FROM pn_usual ORDER BY times DESC, last_at DESC LIMIT 80"
    )
    return [
        {"name": r["name"], "category": r["category"], "times": r["times"]}
        for r in rows
        if r["key"] not in waiting
    ][:limit]


# ---------------------------------------------------------------------------
# budget (adults)
# ---------------------------------------------------------------------------


def categories(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = kit.fetch_all(connection, "SELECT * FROM pn_categories ORDER BY name")
    if not rows:
        for name in DEFAULT_CATEGORIES:
            connection.execute(
                "INSERT OR IGNORE INTO pn_categories (id, name, monthly_cents, created_at) VALUES (?, ?, 0, ?)",
                (kit.new_id(), name, kit.now()),
            )
        connection.commit()
        rows = kit.fetch_all(connection, "SELECT * FROM pn_categories ORDER BY name")
    return [
        {"id": r["id"], "name": r["name"], "monthly_cents": r["monthly_cents"]}
        for r in rows
    ]


def save_category(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    category_id: str = "",
) -> dict[str, Any]:
    require_adult(me)
    name = kit.text(raw.get("name"), "the category", max_len=40, required=True)
    monthly = kit.cents_value(raw.get("monthly_cents"), "the monthly budget", default=0)
    clash = kit.fetch_one(
        connection,
        "SELECT id FROM pn_categories WHERE name = ? COLLATE NOCASE",
        (name,),
    )
    if clash and clash["id"] != category_id:
        raise ValueError(f"There is already a category called {name}.")
    if category_id:
        old = store.one(connection, "pn_categories", category_id, "category")
        kit.update_row(
            connection,
            "pn_categories",
            category_id,
            {"name": name, "monthly_cents": monthly},
        )
        if old["name"] != name:
            connection.execute(
                "UPDATE pn_entries SET category = ? WHERE category = ?",
                (name, old["name"]),
            )
    else:
        category_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_categories (id, name, monthly_cents, created_at) VALUES (?, ?, ?, ?)",
            (category_id, name, monthly, kit.now()),
        )
    connection.commit()
    return {"id": category_id, "name": name, "monthly_cents": monthly}


def delete_category(
    connection: sqlite3.Connection, me: dict[str, Any], category_id: str
) -> None:
    require_adult(me)
    store.one(connection, "pn_categories", category_id, "category")
    connection.execute("DELETE FROM pn_categories WHERE id = ?", (category_id,))
    connection.commit()


def _month(value: str, connection: sqlite3.Connection) -> str:
    text = str(value or "").strip()
    if not text:
        return today(connection).strftime("%Y-%m")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", text):
        raise ValueError("Choose a month like 2026-10.")
    return text


def save_entry(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    entry_id: str = "",
) -> dict[str, Any]:
    require_adult(me)
    current = store.one(connection, "pn_entries", entry_id, "entry") if entry_id else {}
    merged = {**current, **raw}
    values = {
        "kind": kit.choice(
            merged.get("kind"),
            "income or spending",
            ("income", "expense"),
            default="expense",
        ),
        "amount_cents": kit.cents_value(
            merged.get("amount_cents"), "the amount", minimum=1
        ),
        "day": kit.day(merged.get("day"), "the day") or today(connection).isoformat(),
        "category": kit.text(merged.get("category"), "the category", max_len=40)
        or ("Income" if merged.get("kind") == "income" else "Other"),
        "note": kit.text(merged.get("note"), "a note", max_len=200),
    }
    if entry_id:
        from aira.suite.planner import selling

        # Money in from a sale or a side job: the sale, the job, Aira's earnings and any goal follow.
        selling.entry_changed(connection, current, values)
        kit.update_row(connection, "pn_entries", entry_id, values)
    else:
        entry_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_entries (id, kind, amount_cents, day, category, note, profile_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                values["kind"],
                values["amount_cents"],
                values["day"],
                values["category"],
                values["note"],
                me["id"],
                kit.now(),
            ),
        )
    connection.commit()
    return _entry_view(
        store.one(connection, "pn_entries", entry_id, "entry"), people(connection)
    )


def delete_entry(
    connection: sqlite3.Connection, me: dict[str, Any], entry_id: str
) -> None:
    require_adult(me)
    entry = store.one(connection, "pn_entries", entry_id, "entry")
    if entry["bill_id"]:
        connection.execute(
            "DELETE FROM pn_bill_payments WHERE entry_id = ?", (entry_id,)
        )
    payout = kit.fetch_one(
        connection, "SELECT id FROM pn_payouts WHERE entry_id = ?", (entry_id,)
    )
    if payout:  # removing pocket money from the budget gives the points back
        undo_payout(connection, me, payout["id"])
    sold = kit.fetch_one(
        connection,
        "SELECT id FROM pn_sale_items WHERE entry_id = ? AND status = 'sold'",
        (entry_id,),
    )
    if sold:  # removing a sale's money puts the thing back on sale (a removed thing's money stops counting)
        from aira.suite.planner import selling

        selling.unsell(connection, me, sold["id"], removed_too=True)
    job = kit.fetch_one(
        connection, "SELECT id FROM pn_jobs WHERE entry_id = ?", (entry_id,)
    )
    if job:  # removing a side job's money takes the job off the record
        from aira.suite.planner import selling

        selling.undo_job(connection, me, job["id"])
    connection.execute("DELETE FROM pn_entries WHERE id = ?", (entry_id,))
    connection.commit()


def _entry_view(
    entry: dict[str, Any], everyone: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": entry["id"],
        "kind": entry["kind"],
        "amount_cents": entry["amount_cents"],
        "day": entry["day"],
        "category": entry["category"],
        "note": entry["note"],
        "who": _name(everyone, entry["profile_id"]),
        "bill_id": entry["bill_id"],
    }


def budget(
    connection: sqlite3.Connection, me: dict[str, Any], month: str = ""
) -> dict[str, Any]:
    """Spending against the monthly budget per category, income, and what is left."""
    require_adult(me)
    month = _month(month, connection)
    cats = categories(connection)
    entries = kit.fetch_all(
        connection,
        "SELECT * FROM pn_entries WHERE substr(day, 1, 7) = ? ORDER BY day DESC, created_at DESC",
        (month,),
    )
    spent: dict[str, int] = {}
    income = expense = 0
    for entry in entries:
        if entry["kind"] == "income":
            income += int(entry["amount_cents"])
        else:
            expense += int(entry["amount_cents"])
            spent[entry["category"]] = spent.get(entry["category"], 0) + int(
                entry["amount_cents"]
            )
    rows = []
    for cat in cats:
        used = spent.pop(cat["name"], 0)
        budget_cents = int(cat["monthly_cents"])
        rows.append(
            {
                **cat,
                "spent_cents": used,
                "left_cents": budget_cents - used if budget_cents else None,
                "percent": round(used * 100 / budget_cents) if budget_cents else None,
                "over": bool(budget_cents and used > budget_cents),
            }
        )
    for name, used in sorted(
        spent.items()
    ):  # spending in categories that have no budget line
        rows.append(
            {
                "id": "",
                "name": name,
                "monthly_cents": 0,
                "spent_cents": used,
                "left_cents": None,
                "percent": None,
                "over": False,
            }
        )
    planned = sum(int(c["monthly_cents"]) for c in cats)
    everyone = people(connection)
    trend = _trend(connection, month)
    return {
        "month": month,
        "today": today(connection).isoformat(),
        "currency": kit.business(connection)["currency"],
        "income_cents": income,
        "expense_cents": expense,
        "net_cents": income - expense,
        "planned_cents": planned,
        "categories": rows,
        "entries": [_entry_view(e, everyone) for e in entries],
        "trend": trend,
    }


def _trend(
    connection: sqlite3.Connection, month: str, months: int = 6
) -> list[dict[str, Any]]:
    """Money in, money spent and what was left for ``month`` and the months before it (oldest first)."""
    year, number = (int(x) for x in month.split("-"))
    keys = []
    for _ in range(months):
        keys.append(f"{year:04d}-{number:02d}")
        year, number = (year - 1, 12) if number == 1 else (year, number - 1)
    keys.reverse()
    totals = {
        (r["month"], r["kind"]): int(r["total"] or 0)
        for r in kit.fetch_all(
            connection,
            "SELECT substr(day, 1, 7) AS month, kind, SUM(amount_cents) AS total FROM pn_entries "
            "WHERE substr(day, 1, 7) BETWEEN ? AND ? GROUP BY substr(day, 1, 7), kind",
            (keys[0], keys[-1]),
        )
    }
    out = []
    for key in keys:
        income = totals.get((key, "income"), 0)
        spent = totals.get((key, "expense"), 0)
        out.append(
            {
                "month": key,
                "income_cents": income,
                "expense_cents": spent,
                "net_cents": income - spent,
            }
        )
    return out


def _amount(cents: int) -> str:
    """Cents as a plain number a spreadsheet adds up (1250 -> "12.50")."""
    sign = "-" if cents < 0 else ""
    return f"{sign}{abs(int(cents)) // 100}.{abs(int(cents)) % 100:02d}"


def budget_csv(
    connection: sqlite3.Connection, me: dict[str, Any], month: str = ""
) -> bytes:
    """The month's entries: money in is positive, money spent negative, so a spreadsheet's SUM is what is left."""
    data = budget(connection, me, month)
    rows = [
        {
            "day": e["day"],
            "type": "Money in" if e["kind"] == "income" else "Money spent",
            "category": e["category"],
            "amount": _amount(
                e["amount_cents"] if e["kind"] == "income" else -e["amount_cents"]
            ),
            "currency": data["currency"],
            "note": e["note"],
            "who": e["who"],
        }
        for e in data["entries"]
    ]
    return kit.csv_bytes(
        rows, ("day", "type", "category", "amount", "currency", "note", "who")
    )


def tasks_csv(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    scope: str = "family",
    status: str = "all",
    q: str = "",
    person: str = "",
) -> bytes:
    rows = [
        {
            "task": t["title"],
            "who": t["assignee_name"],
            "takes_turns": ", ".join(t["rotation_names"]),
            "day": t["due_date"],
            "time": t["due_time"],
            "repeats": t["repeat_label"],
            "points": t["points"],
            "status": "done" if t["done"] else "to do",
            "done_by": ", ".join(t["done_by"]),
            "notes": t["notes"],
        }
        for t in list_tasks(
            connection, me, scope=scope, status=status, q=q, person=person
        )
    ]
    return kit.csv_bytes(
        rows,
        (
            "task",
            "who",
            "takes_turns",
            "day",
            "time",
            "repeats",
            "points",
            "status",
            "done_by",
            "notes",
        ),
    )


# ---------------------------------------------------------------------------
# bills (adults)
# ---------------------------------------------------------------------------


def _bill_due(bill: dict[str, Any], period: str) -> date:
    if bill["repeat"] == "once":
        return date.fromisoformat(bill["due_date"])
    if bill["repeat"] == "yearly":
        base = date.fromisoformat(bill["due_date"])
        return _month_day(int(period), base.month, base.day)
    year, month = (int(x) for x in period.split("-"))
    return _month_day(year, month, int(bill["due_day"]))


def _period_of(bill: dict[str, Any], day: date) -> str:
    if bill["repeat"] == "once":
        return bill["due_date"]
    if bill["repeat"] == "yearly":
        return str(day.year)
    return day.strftime("%Y-%m")


def _next_period(bill: dict[str, Any], period: str) -> str | None:
    if bill["repeat"] == "once":
        return None
    if bill["repeat"] == "yearly":
        return str(int(period) + 1)
    year, month = (int(x) for x in period.split("-"))
    return f"{year + 1}-01" if month == 12 else f"{year}-{month + 1:02d}"


def _paid_periods(connection: sqlite3.Connection, bill_id: str) -> set[str]:
    return {
        r["period"]
        for r in kit.fetch_all(
            connection,
            "SELECT period FROM pn_bill_payments WHERE bill_id = ?",
            (bill_id,),
        )
    }


def _previous_period(bill: dict[str, Any], period: str) -> str | None:
    if bill["repeat"] == "yearly":
        return str(int(period) - 1)
    if bill["repeat"] == "monthly":
        year, month = (int(x) for x in period.split("-"))
        return f"{year - 1}-12" if month == 1 else f"{year}-{month - 1:02d}"
    return None


def next_bill(
    connection: sqlite3.Connection, bill: dict[str, Any], on: date
) -> tuple[str, date] | None:
    """The earliest unpaid period: last period's when it was due after the bill was added and is still unpaid
    (overdue), otherwise this period's or the next unpaid one. None when a one-off bill is paid."""
    paid = _paid_periods(connection, bill["id"])
    if bill["repeat"] == "once":
        return (
            None
            if bill["due_date"] in paid
            else (bill["due_date"], date.fromisoformat(bill["due_date"]))
        )
    created = local_day(connection, bill["created_at"])
    period = _period_of(bill, on)
    previous = _previous_period(bill, period)
    if previous and previous not in paid and created <= _bill_due(bill, previous) < on:
        return previous, _bill_due(bill, previous)
    for _ in range(36):
        # A period whose day came before the bill was added is not owed (a bill added on the 27th that is due
        # on the 5th is next due on the 5th of next month, not overdue straight away).
        if period not in paid and _bill_due(bill, period) >= created:
            return period, _bill_due(bill, period)
        following = _next_period(bill, period)
        if following is None:
            return None
        period = following
    return None


def _valid_period(bill: dict[str, Any], period: str) -> bool:
    if bill["repeat"] == "once":
        return period == bill["due_date"]
    if bill["repeat"] == "yearly":
        return bool(re.fullmatch(r"\d{4}", period))
    return bool(re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period))


def _clean_bill(
    connection: sqlite3.Connection,
    raw: dict[str, Any],
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    merged = {**(current or {}), **raw}
    repeat = kit.choice(
        merged.get("repeat"), "how often", BILL_REPEATS, default="monthly"
    )
    due_day = kit.integer(
        merged.get("due_day"), "the due day", default=1, minimum=1, maximum=31
    )
    due_date = kit.day(merged.get("due_date"), "the due date")
    if repeat in ("once", "yearly") and not due_date:
        raise ValueError("Choose the date it is due.")
    return {
        "name": kit.text(merged.get("name"), "the bill", max_len=80, required=True),
        "amount_cents": kit.cents_value(
            merged.get("amount_cents"), "the amount", default=0
        ),
        "repeat": repeat,
        "due_day": due_day,
        "due_date": due_date,
        "category": kit.text(merged.get("category"), "the category", max_len=40)
        or "Bills",
        "autopay": 1 if kit.flag(merged.get("autopay")) else 0,
        "notes": kit.text(merged.get("notes"), "notes", max_len=300),
    }


def bill_view(
    connection: sqlite3.Connection, bill: dict[str, Any], on: date
) -> dict[str, Any]:
    upcoming = next_bill(connection, bill, on)
    period, due = upcoming if upcoming else ("", None)
    days = (due - on).days if due else None
    if due is None:
        status = "settled"
    elif days is not None and days < 0:
        status = "overdue"
    elif days == 0:
        status = "due"
    else:
        status = "upcoming"
    last = kit.fetch_one(
        connection,
        "SELECT * FROM pn_bill_payments WHERE bill_id = ? ORDER BY paid_at DESC",
        (bill["id"],),
    )
    return {
        "id": bill["id"],
        "name": bill["name"],
        "amount_cents": bill["amount_cents"],
        "repeat": bill["repeat"],
        "due_day": bill["due_day"],
        "due_date": bill["due_date"],
        "category": bill["category"],
        "autopay": bool(bill["autopay"]),
        "notes": bill["notes"],
        "next_period": period,
        "next_due": due.isoformat() if due else "",
        "days_until": days,
        "status": status,
        "last_paid_at": last["paid_at"] if last else None,
        "last_paid_period": last["period"] if last else "",
    }


def bills(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    require_adult(me)
    on = today(connection)
    rows = kit.fetch_all(
        connection, "SELECT * FROM pn_bills WHERE active = 1 ORDER BY name"
    )
    out = [bill_view(connection, b, on) for b in rows]
    return sorted(out, key=lambda b: (b["next_due"] == "", b["next_due"]))


def save_bill(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    bill_id: str = "",
) -> dict[str, Any]:
    require_adult(me)
    current = store.one(connection, "pn_bills", bill_id, "bill") if bill_id else None
    values = _clean_bill(connection, raw, current)
    stamp = kit.now()
    if bill_id:
        kit.update_row(connection, "pn_bills", bill_id, {**values, "updated_at": stamp})
    else:
        bill_id = kit.new_id()
        connection.execute(
            """INSERT INTO pn_bills (id, name, amount_cents, repeat, due_day, due_date, category, autopay, active, notes, created_at, updated_at)
            VALUES (:id, :name, :amount_cents, :repeat, :due_day, :due_date, :category, :autopay, 1, :notes, :created_at, :updated_at)""",
            {**values, "id": bill_id, "created_at": stamp, "updated_at": stamp},
        )
    connection.commit()
    return bill_view(
        connection,
        store.one(connection, "pn_bills", bill_id, "bill"),
        today(connection),
    )


def delete_bill(
    connection: sqlite3.Connection, me: dict[str, Any], bill_id: str
) -> None:
    require_adult(me)
    store.one(connection, "pn_bills", bill_id, "bill")
    kit.update_row(
        connection, "pn_bills", bill_id, {"active": 0, "updated_at": kit.now()}
    )
    connection.commit()


def pay_bill(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    bill_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Mark the bill's current period paid: one payment and one spending entry, however often it is pressed."""
    require_adult(me)
    bill = store.one(connection, "pn_bills", bill_id, "bill")
    on = today(connection)
    period = kit.text(raw.get("period"), "the period", max_len=10)
    if period and not _valid_period(bill, period):
        raise ValueError(
            "Choose the month (like 2026-10) or year (like 2026) this payment is for."
        )
    if not period:
        upcoming = next_bill(connection, bill, on)
        if upcoming is None:
            return bill_view(connection, bill, on)
        period = upcoming[0]
    if period in _paid_periods(connection, bill_id):
        return bill_view(connection, bill, on)
    amount = kit.cents_value(
        raw.get("amount_cents"), "the amount", default=int(bill["amount_cents"])
    )
    entry_id = kit.new_id() if amount > 0 else ""
    if entry_id:
        connection.execute(
            "INSERT INTO pn_entries (id, kind, amount_cents, day, category, note, profile_id, bill_id, created_at) VALUES (?, 'expense', ?, ?, ?, ?, ?, ?, ?)",
            (
                entry_id,
                amount,
                on.isoformat(),
                bill["category"],
                f"Bill: {bill['name']}"[:200],
                me["id"],
                bill_id,
                kit.now(),
            ),
        )
    connection.execute(
        "INSERT INTO pn_bill_payments (id, bill_id, period, amount_cents, entry_id, paid_by, paid_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (kit.new_id(), bill_id, period, amount, entry_id, me["id"], kit.now()),
    )
    kit.log(
        connection,
        APP,
        "bill",
        f"{bill['name']} paid",
        ref_type="bill",
        ref_id=bill_id,
        actor=me["name"],
    )
    connection.commit()
    return bill_view(connection, bill, on)


def unpay_bill(
    connection: sqlite3.Connection, me: dict[str, Any], bill_id: str, period: str
) -> dict[str, Any]:
    require_adult(me)
    bill = store.one(connection, "pn_bills", bill_id, "bill")
    row = kit.fetch_one(
        connection,
        "SELECT * FROM pn_bill_payments WHERE bill_id = ? AND period = ?",
        (bill_id, period),
    )
    if row:
        if row["entry_id"]:
            connection.execute(
                "DELETE FROM pn_entries WHERE id = ?", (row["entry_id"],)
            )
        connection.execute("DELETE FROM pn_bill_payments WHERE id = ?", (row["id"],))
        connection.commit()
    return bill_view(connection, bill, today(connection))


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


def _note_view(
    note: dict[str, Any], me: dict[str, Any], everyone: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    return {
        "id": note["id"],
        "title": note["title"],
        "body": note["body"],
        "checklist": kit.loads(note["checklist"], []) or [],
        "shared": bool(note["shared"]),
        "pinned": bool(note["pinned"]),
        "owner": _name(everyone, note["owner_id"]),
        "mine": note["owner_id"] == me["id"],
        "updated_at": note["updated_at"],
    }


def notes(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_notes WHERE deleted_at IS NULL AND (shared = 1 OR owner_id = ?) ORDER BY pinned DESC, updated_at DESC",
        (me["id"],),
    )
    everyone = people(connection)
    return [_note_view(n, me, everyone) for n in rows]


def _note_for(
    connection: sqlite3.Connection, me: dict[str, Any], note_id: str
) -> dict[str, Any]:
    note = store.one(connection, "pn_notes", note_id, "note")
    if not note["shared"] and note["owner_id"] != me["id"]:
        raise LookupError("That note was not found.")
    return note


def _checklist(value: Any) -> list[dict[str, Any]]:
    items = value if isinstance(value, list) else []
    out = []
    for item in items[:50]:
        if isinstance(item, dict):
            text = kit.text(item.get("text"), "a checklist line", max_len=200)
            done = kit.flag(item.get("done"))
        else:
            text, done = kit.text(item, "a checklist line", max_len=200), False
        if text:
            out.append({"text": text, "done": done})
    return out


def save_note(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    note_id: str = "",
) -> dict[str, Any]:
    current = _note_for(connection, me, note_id) if note_id else None
    merged = {**(current or {}), **raw}
    if current is not None and "checklist" not in raw:
        merged["checklist"] = kit.loads(current["checklist"], [])
    values = {
        "title": kit.text(merged.get("title"), "a title", max_len=120),
        "body": kit.text(merged.get("body"), "the note", max_len=5000, multiline=True),
        "checklist": kit.dumps(_checklist(merged.get("checklist"))),
        "shared": 1 if kit.flag(merged.get("shared", True)) else 0,
        "pinned": 1 if kit.flag(merged.get("pinned")) else 0,
    }
    if not values["title"] and not values["body"] and values["checklist"] == "[]":
        raise ValueError("Write something in the note.")
    if current is not None and current["owner_id"] != me["id"] and not values["shared"]:
        raise PermissionError("Only the person who wrote a note can make it private.")
    stamp = kit.now()
    if note_id:
        kit.update_row(connection, "pn_notes", note_id, {**values, "updated_at": stamp})
    else:
        note_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_notes (id, title, body, checklist, shared, pinned, owner_id, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                note_id,
                values["title"],
                values["body"],
                values["checklist"],
                values["shared"],
                values["pinned"],
                me["id"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    return _note_view(
        store.one(connection, "pn_notes", note_id, "note"), me, people(connection)
    )


def delete_note(
    connection: sqlite3.Connection, me: dict[str, Any], note_id: str
) -> None:
    note = _note_for(connection, me, note_id)
    if note["owner_id"] != me["id"] and not life.is_adult(me):
        raise PermissionError(
            "Ask the person who wrote it, or an adult, to remove this note."
        )
    kit.update_row(
        connection,
        "pn_notes",
        note_id,
        {"deleted_at": kit.now(), "updated_at": kit.now()},
    )
    connection.commit()


# ---------------------------------------------------------------------------
# my day
# ---------------------------------------------------------------------------


def overview(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    on = today(connection)
    open_tasks = list_tasks(connection, me, scope="mine", status="open")
    todays = [
        t for t in open_tasks if t["due_date"] == on.isoformat() and not t["done_by_me"]
    ]
    overdue = [t for t in open_tasks if t["overdue"]]
    week = (on + timedelta(days=7)).isoformat()
    upcoming = [
        t
        for t in open_tasks
        if t["due_date"]
        and on.isoformat() < t["due_date"] <= week
        and not t["done_by_me"]
    ][:15]
    anytime = [t for t in open_tasks if not t["due_date"] and not t["done_by_me"]][:15]
    adult = life.is_adult(me)
    soon = (
        [
            b
            for b in bills(connection, me)
            if b["days_until"] is not None and b["days_until"] <= 7
        ]
        if adult
        else []
    )
    pending = (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM pn_reward_requests WHERE status = 'pending'",
        )
        if adult
        else 0
    )
    return {
        "me": {
            "id": me["id"],
            "name": me["name"],
            "age_group": me["age_group"],
            "adult": adult,
        },
        "today": on.isoformat(),
        "today_tasks": todays,
        "overdue": overdue,
        "upcoming": upcoming,
        "anytime": anytime,
        "points": balance(connection, me["id"]),
        "pending_requests": pending,
        "bills_soon": soon,
        "lists": lists(connection),
        "people": list(people(connection).values()),
        "meals_today": _meals_today(connection),
        "sale_requests": _sale_requests(connection) if adult else 0,
        "earned_this_month_cents": _earned(connection) if adult else 0,
        "currency": kit.business(connection)["currency"],
    }


def _meals_today(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    from aira.suite.planner import meals

    return [
        {
            "slot": m["slot"],
            "slot_name": meals.SLOT_NAMES.get(m["slot"], m["slot"]),
            "title": m["title"],
        }
        for m in meals.today_meals(connection)
    ]


def _earned(connection: sqlite3.Connection) -> int:
    from aira.suite.planner import selling

    return selling.earned_this_month(connection)


def _sale_requests(connection: sqlite3.Connection) -> int:
    from aira.suite.planner import selling

    return selling.new_request_count(connection)
