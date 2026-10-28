"""Routines: a question or command Aira runs for the person at a set time on chosen days.

"Give me my morning brief" every day at 07:00, "How is my business doing?" on Mondays, "Add milk to the shopping
list" on Saturdays. When a routine is due, the person's next pulse (their open Aira asks every 20 seconds) runs it
once that day, answered from the facts at once (no waiting for the writing model), keeps the answer in the
routine's own conversation and shows it as an alert (read aloud in easy mode) and on Today. **Run now** asks the
writing model too. Commands in a routine pass the same consent gate as the chat.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira.suite import kit
from aira.suite.ai import service

DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
DAY_NAMES = (
    "Mondays",
    "Tuesdays",
    "Wednesdays",
    "Thursdays",
    "Fridays",
    "Saturdays",
    "Sundays",
)
MAX_ROUTINES = 20
MAX_PROMPT = 500
ANSWER_PREVIEW = 600
SUGGESTED = (
    {
        "title": "Morning brief",
        "prompt": "Give me my morning brief",
        "at": "07:00",
        "days": list(range(7)),
    },
    {
        "title": "Weekly goals check",
        "prompt": "How are my goals going?",
        "at": "18:00",
        "days": [6],
    },
    {
        "title": "Money check",
        "prompt": "How much did I earn this month and how can I earn more?",
        "at": "09:00",
        "days": [0],
    },
)


def local_now(connection: sqlite3.Connection) -> datetime:
    """Now on the account's clock (Settings > time zone; UTC when it has none)."""
    zone = kit.business(connection).get("timezone") or "UTC"
    try:
        return datetime.now(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo("UTC"))


def _days(value: Any) -> list[int]:
    if value is None or value == "":
        return list(range(7))
    if isinstance(value, str):
        value = [part for part in value.replace(" ", "").split(",") if part]
    if not isinstance(value, list):
        raise ValueError("Choose the days.")
    out: set[int] = set()
    for item in value:
        text = str(item).strip().lower()[:3]
        if text in DAYS:
            out.add(DAYS.index(text))
        elif text.isdigit() and int(text) < 7:
            out.add(int(text))
        else:
            raise ValueError("Choose days from Monday to Sunday.")
    if not out:
        raise ValueError("Choose at least one day.")
    return sorted(out)


def _time(value: Any) -> str:
    text = str(value or "").strip()
    parts = text.split(":")
    if len(parts) != 2 or not all(p.isdigit() for p in parts):
        raise ValueError("Enter the time as HH:MM, for example 07:30.")
    hour, minute = int(parts[0]), int(parts[1])
    if not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError("Enter the time as HH:MM, for example 07:30.")
    return f"{hour:02d}:{minute:02d}"


def describe_days(days: list[int]) -> str:
    if len(days) == 7:
        return "Every day"
    if days == [0, 1, 2, 3, 4]:
        return "Weekdays"
    if days == [5, 6]:
        return "Weekends"
    return ", ".join(DAY_NAMES[d] for d in days)


def view(row: dict[str, Any], today: str) -> dict[str, Any]:
    days = [int(d) for d in str(row["days"]).split(",") if d.strip().isdigit()]
    return {
        "id": row["id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "at": row["at"],
        "days": days,
        "days_label": describe_days(days),
        "enabled": bool(row["enabled"]),
        "alert": bool(row["alert"]),
        "session_id": row["session_id"],
        "last_run_on": row["last_run_on"],
        "ran_today": row["last_run_on"] == today,
        "starts_on": row.get("starts_on") or "",
        "last_answer": row["last_answer"],
        "created_at": row["created_at"],
    }


def _row(
    connection: sqlite3.Connection, me: dict[str, Any], routine_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM ai_routines WHERE id = ? AND owner_id = ?",
        (routine_id, me["id"]),
    )
    if not found:
        raise LookupError("That routine was not found.")
    return found


def list_routines(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    today = local_now(connection).date().isoformat()
    return [
        view(row, today)
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM ai_routines WHERE owner_id = ? ORDER BY at, created_at",
            (me["id"],),
        )
    ]


def get(
    connection: sqlite3.Connection, me: dict[str, Any], routine_id: str
) -> dict[str, Any]:
    return view(
        _row(connection, me, routine_id), local_now(connection).date().isoformat()
    )


def save(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    routine_id: str = "",
) -> dict[str, Any]:
    """Create a routine, or change one (only the fields given)."""
    current = _row(connection, me, routine_id) if routine_id else None
    if current is None and (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM ai_routines WHERE owner_id = ?",
            (me["id"],),
        )
        >= MAX_ROUTINES
    ):
        raise ValueError(f"You can keep {MAX_ROUTINES} routines. Remove one first.")

    def given(key: str) -> bool:
        return current is None or key in raw

    values: dict[str, Any] = {}
    if given("prompt"):
        values["prompt"] = kit.text(
            raw.get("prompt"),
            "what Monosage should do",
            max_len=MAX_PROMPT,
            required=True,
            multiline=True,
        )
    if given("title"):
        fallback = values.get("prompt") or (current or {}).get("prompt") or ""
        values["title"] = (
            kit.text(raw.get("title"), "a name", max_len=60)
            or " ".join(fallback.split())[:60]
        )
    if given("at"):
        values["at"] = _time(raw.get("at") or ("07:00" if current is None else ""))
    if given("days"):
        values["days"] = ",".join(str(d) for d in _days(raw.get("days")))
    if given("enabled"):
        values["enabled"] = 1 if kit.flag(raw.get("enabled", True)) else 0
    if given("alert"):
        values["alert"] = 1 if kit.flag(raw.get("alert", True)) else 0
    stamp = kit.now()
    now = local_now(connection)
    if current is None:
        routine_id = kit.new_id()
        # Added after its time today: it first runs on its next day, not at once (Run now is there for that).
        starts_on = (
            (now.date() + timedelta(days=1)).isoformat()
            if values["at"] <= now.strftime("%H:%M")
            else ""
        )
        connection.execute(
            """INSERT INTO ai_routines (id, owner_id, title, prompt, at, days, enabled, alert, starts_on, created_at,
            updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                routine_id,
                me["id"],
                values["title"],
                values["prompt"],
                values["at"],
                values["days"],
                values["enabled"],
                values["alert"],
                starts_on,
                stamp,
                stamp,
            ),
        )
    elif values:
        if "at" in values and values["at"] != current["at"]:
            # A new time today runs again today if it has not come yet.
            later = values["at"] > now.strftime("%H:%M")
            values["last_run_on"] = "" if later else current["last_run_on"]
            if later:
                values["starts_on"] = ""
        kit.update_row(
            connection, "ai_routines", routine_id, {**values, "updated_at": stamp}
        )
    connection.commit()
    return get(connection, me, routine_id)


def delete(connection: sqlite3.Connection, me: dict[str, Any], routine_id: str) -> None:
    _row(connection, me, routine_id)
    connection.execute("DELETE FROM ai_routines WHERE id = ?", (routine_id,))
    connection.commit()


def run(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    routine_id: str,
    *,
    use_model: bool = True,
    hour: int | None = None,
    seen: bool = False,
) -> dict[str, Any]:
    """Run a routine now; its answer goes to the routine's own conversation.

    ``seen``: the person is about to read the answer (Run now in the Routines tab), so no alert pops up."""
    routine = _row(connection, me, routine_id)
    now = local_now(connection)
    session_id = routine["session_id"]
    if not session_id or not kit.fetch_one(
        connection,
        "SELECT id FROM ai_sessions WHERE id = ? AND owner_id = ?",
        (session_id, me["id"]),
    ):
        session_id = service.new_session(connection, me, routine["title"])
    result = service.chat(
        connection,
        me,
        {"message": routine["prompt"], "session_id": session_id},
        hour=now.hour if hour is None else hour,
        use_model=use_model,
    )
    kit.update_row(
        connection,
        "ai_routines",
        routine_id,
        {
            "session_id": session_id,
            "last_run_on": now.date().isoformat(),
            "last_answer": result["answer"]["content"][:ANSWER_PREVIEW],
            "seen_at": kit.now() if seen else None,
        },
    )
    connection.commit()
    return {**result, "routine": get(connection, me, routine_id)}


def run_due(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    """Run this person's routines whose time has come today and that have not run today (each once)."""
    now = local_now(connection)
    today, clock, weekday = (
        now.date().isoformat(),
        now.strftime("%H:%M"),
        str(now.weekday()),
    )
    ran = []
    for row in kit.fetch_all(
        connection,
        """SELECT * FROM ai_routines WHERE owner_id = ? AND enabled = 1 AND last_run_on != ? AND at <= ?
        AND starts_on <= ? ORDER BY at LIMIT 5""",
        (me["id"], today, clock, today),
    ):
        if weekday not in str(row["days"]).split(","):
            continue
        # Claim it first: two pulses at the same moment must not both run it.
        claimed = connection.execute(
            "UPDATE ai_routines SET last_run_on = ? WHERE id = ? AND last_run_on != ?",
            (today, row["id"], today),
        ).rowcount
        connection.commit()
        if not claimed:
            continue
        try:
            run(connection, me, row["id"], use_model=False, hour=now.hour)
            ran.append(row["id"])
        except Exception:  # noqa: BLE001 - a routine must not break the pulse; it shows as not run
            connection.rollback()
            kit.suite.logger.exception("Routine %s failed", row["id"])
    return ran


def seen(
    connection: sqlite3.Connection, me: dict[str, Any], routine_id: str
) -> dict[str, Any]:
    _row(connection, me, routine_id)
    kit.update_row(connection, "ai_routines", routine_id, {"seen_at": kit.now()})
    connection.commit()
    return get(connection, me, routine_id)


def today_runs(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> list[dict[str, Any]]:
    """Routines due today (run or still to come), for Today."""
    now = local_now(connection)
    weekday, today = now.weekday(), now.date().isoformat()
    return [
        routine
        for routine in list_routines(connection, me)
        if routine["enabled"]
        and weekday in routine["days"]
        and routine["starts_on"] <= today
    ]


def alerts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    """Answers from today's routines the person has not seen yet."""
    today = local_now(connection).date().isoformat()
    out = []
    for row in kit.fetch_all(
        connection,
        """SELECT * FROM ai_routines WHERE owner_id = ? AND alert = 1 AND last_run_on = ? AND seen_at IS NULL
        AND last_answer != ''""",
        (me["id"], today),
    ):
        out.append(
            {
                "id": f"ai-routine:{row['id']}:{today}",
                "title": row["title"],
                "body": row["last_answer"],
                "tone": "info",
                "speak": True,
                "actions": [
                    {
                        "label": "Got it",
                        "method": "POST",
                        "path": f"/api/ai/routines/{row['id']}/seen",
                        "body": {},
                    }
                ],
            }
        )
    return out
