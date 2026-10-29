"""The agents: one per app, each with what it can read for the master agent and the actions it can take.

An agent **reads** to answer a question (a few short lines of facts about the person's day in its app) and **acts**
when asked (add a goal, a task, a shopping item, a note, a timeline event). Every action has a **risk**: ``minor``
(easy to undo, only touches the person's own things) or ``major``. The person's consent level for the agent
decides what runs alone (:mod:`aira.suite.ai.service`).

Agents that only read use the app's Today cards (:data:`aira.suite.life.TODAY`), so every everyday app has one
without extra code. The business agent reads the Business hub's numbers and is only there for people who may see
them (adults on this computer; owners, admins, coaches and auditors in an organization).
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal
from typing import Any

from aira import apps as app_catalog
from aira import suite
from aira.suite import kit, life

ReadFn = Callable[[sqlite3.Connection, dict[str, Any]], list[str]]
# An action's run returns the sentence to show, or done(sentence, **undo) to say how to undo it.
RunFn = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any]], Any]
UndoFn = Callable[[sqlite3.Connection, dict[str, Any], dict[str, Any]], str]
DescribeFn = Callable[[dict[str, Any]], str]
ParseFn = Callable[[str], list[dict[str, Any]]]

CONSENT_LEVELS = ("read_only", "auto_minor", "auto_major", "manual_approval")
CONSENT_INFO = {
    "read_only": {
        "label": "Read only",
        "text": "The agent can look at your data to answer you, but never changes anything.",
    },
    "auto_minor": {
        "label": "Small actions alone",
        "text": "Small, easy-to-undo actions (add a task, a note, a shopping item) happen at once; bigger ones wait for you.",
    },
    "auto_major": {
        "label": "All actions alone",
        "text": "Every action the agent can take happens at once. You can see each one in Activity.",
    },
    "manual_approval": {
        "label": "Ask me first",
        "text": "Every action waits in Approvals until you say yes.",
    },
}
MAX_FACTS = 12  # lines one agent adds to a question


def done(message: str, **undo: Any) -> dict[str, Any]:
    """What an action's run returns when it can be undone: the sentence and what its undo needs."""
    return {"message": message, "undo": undo}


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    risk: str  # "minor" or "major"
    fields: dict[str, str]  # argument -> what it is (shown to the writing model)
    run: RunFn
    describe: DescribeFn
    # Takes back a done action (``fn(connection, me, undo) -> sentence``; ``undo`` is what run gave done()).
    undo: UndoFn | None = None
    # Only runs alone when the person typed it as a command ("Remember that ..."). Proposed by the writing model, it
    # waits in Approvals whatever the consent level: what it changes lasts and steers every later answer.
    typed_only: bool = False


@dataclass(frozen=True)
class Agent:
    id: str
    name: str
    app: str  # the catalog app it belongs to (its switch in the hosted build)
    section: (
        str  # the app section it reads (a Today registry key, or "" for its own reader)
    )
    summary: str
    keywords: tuple[str, ...]
    default_consent: str = "read_only"
    actions: tuple[Action, ...] = field(default_factory=tuple)
    reader: ReadFn | None = None
    allowed: Callable[[dict[str, Any]], bool] | None = None
    # Plain commands this agent understands without the model: fn(message) -> [{"action", "args"}] (or []).
    parser: ParseFn | None = None
    # What the person earns, or could earn, in this app (lines for the Earn tab and EarnAgent).
    earner: ReadFn | None = None
    # Read for a morning brief (and when no agent's words match)? False: only when its own words are used.
    brief: bool = True
    # The most actions it takes alone in any 24 hours until the person sets their own (0: no limit); more wait in
    # Approvals. A trading or spending agent should set one.
    default_limit: int = 0

    def read(self, connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
        lines = (
            self.reader(connection, me)
            if self.reader is not None
            else today_facts(connection, me, self.section)
        )
        return unique(lines)[:MAX_FACTS]

    def action(self, name: str) -> Action | None:
        return next((a for a in self.actions if a.name == name), None)


# ---------------------------------------------------------------------------
# readers
# ---------------------------------------------------------------------------


def unique(lines: list[str]) -> list[str]:
    """The lines without repeats (an app may list the same headline twice), in their order."""
    seen: set[str] = set()
    out = []
    for line in lines:
        key = " ".join(str(line).split()).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(str(line))
    return out


def today_facts(
    connection: sqlite3.Connection, me: dict[str, Any], section: str
) -> list[str]:
    """An app's Today cards as short lines ("Medicines today: Metformin 500 mg (08:00)")."""
    fn = life.TODAY.get(section)
    if fn is None:
        return []
    out: list[str] = []
    for card in fn(connection, me):
        lines = card.get("lines") or []
        if not lines:
            if card.get("empty"):
                out.append(f"{card['title']}: {card['empty']}")
            continue
        for line in lines:
            sub = f" ({line['sub']})" if line.get("sub") else ""
            done = " [done]" if line.get("done") else ""
            out.append(f"{card['title']}: {line['text']}{sub}{done}")
    return out


def _life_facts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    data = journey.overview(connection, me)
    goals = data["goals"]
    out = [
        f"Goals: {goals['active']} active, {goals['completed']} completed, {goals['behind']} behind or overdue; "
        f"life score {data['life_score']} (average progress of active goals)."
    ]
    for goal in journey.list_goals(connection, me, status="active")[:6]:
        due = f", target {goal['target_date']}" if goal["target_date"] else ""
        out.append(
            f'Goal "{goal["title"]}" ({goal["category"]}): {goal["progress"]}% done{due}, {goal["state"].replace("_", " ")}.'
        )
    for item in data["milestones"][:4]:
        what = {
            "event": "Event",
            "goal": "Goal target",
            "document": "Document expires",
        }[item["type"]]
        out.append(
            f'Coming up: {what} "{item["title"]}" on {item["date"]} (in {item["days_left"]} days).'
        )
    docs = data["documents"]
    if docs["expired"] or docs["expires_soon"]:
        out.append(
            f"Documents: {docs['total']} in the vault, {docs['expires_soon']} expire within 90 days, {docs['expired']} expired."
        )
    from aira.suite.journey import lifebook

    stage = lifebook.summary(connection, me)
    out.append(
        f"Stage of life: {stage['life']['stage']['title']} ({stage['life']['stage']['label']}), "
        f"{stage['done']} of {stage['total']} things done in it"
        + (
            "; next: " + "; ".join(item["title"] for item in stage["next"])
            if stage["next"]
            else ""
        )
        + "."
    )
    for item in stage["due"][:4]:
        out.append(
            f'Due: {item["kind"]} "{item["title"]}" for {item["who"]} on {item["next_due"]}.'
        )
    return out


def _planner_facts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    from aira.suite.planner import service as planner, store as planner_store

    planner_store.ensure(connection)
    data = planner.overview(connection, me)
    out = [
        f"Tasks today: {len(data['today_tasks'])}, overdue: {len(data['overdue'])}, coming up this week: {len(data['upcoming'])}."
    ]
    for task in [*data["overdue"][:3], *data["today_tasks"][:5]]:
        when = f" at {task['due_time']}" if task.get("due_time") else ""
        late = " (overdue)" if task.get("overdue") else ""
        out.append(f"Task: {task['title']}{when}{late}.")
    for task in data["upcoming"][:3]:
        out.append(f"Coming up: {task['title']} on {task['due_date']}.")
    for bill in data["bills_soon"][:3]:
        out.append(f"Bill: {bill['name']} due in {bill['days_until']} days.")
    for shopping in data["lists"][:3]:
        out.append(
            f'Shopping list "{shopping["name"]}": {shopping["open"]} items to buy.'
        )
    return out


def _business_facts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    kit.ensure_core(connection)
    revenue = kit.revenue(connection)
    currency = revenue["currency"]
    change = (
        f" ({revenue['change_percent']:+d}% on last month)"
        if revenue["change_percent"] is not None
        else ""
    )
    out = [
        f"Revenue this month: {kit.fmt_money(revenue['this_month_cents'], currency)}{change}; "
        f"last month {kit.fmt_money(revenue['last_month_cents'], currency)}; "
        f"all time {kit.fmt_money(revenue['total_cents'], currency)} from {revenue['payments']} payments."
    ]
    for app in (*app_catalog.INDUSTRY_APPS, *app_catalog.LIFE_APPS):
        if not suite.app_enabled(app.id):
            continue
        for section in app.sections:
            fn = kit.SUMMARIES.get(section)
            if fn is None:
                continue
            try:
                cards = fn(connection).get("cards", [])
            except Exception:  # noqa: BLE001 - one app's numbers must not break the answer
                suite.logger.exception("Summary for %s failed", section)
                continue
            numbers = ", ".join(f"{c['label']}: {c['value']}" for c in cards[:4])
            if numbers:
                out.append(f"{app.name}: {numbers}.")
    counts = kit.outbox_counts(connection)
    if counts.get("pending"):
        out.append(
            f"{counts['pending']} customer emails wait for approval in the outbox."
        )
    return out


def _may_see_business(me: dict[str, Any]) -> bool:
    member = suite.actor()
    if member is not None:
        return str(member.get("role") or "") in {"owner", "admin", "coach", "auditor"}
    return life.is_adult(me)


# ---------------------------------------------------------------------------
# actions
# ---------------------------------------------------------------------------


def _date(value: Any, connection: sqlite3.Connection) -> str:
    """A date a person or the model wrote: 2026-11-02, today, tomorrow, in 3 days/weeks/months."""
    from aira.suite.journey import service as journey

    text = str(value or "").strip().lower()
    if not text:
        return ""
    now = journey.today(connection)
    if text == "today":
        return now.isoformat()
    if text == "tomorrow":
        return (now + timedelta(days=1)).isoformat()
    if text in {"next week", "in a week"}:
        return (now + timedelta(days=7)).isoformat()
    match = re.fullmatch(r"in (\d{1,3}) (day|week|month|year)s?", text)
    if match:
        n, unit = int(match.group(1)), match.group(2)
        days = {"day": 1, "week": 7, "month": 30, "year": 365}[unit] * n
        return (now + timedelta(days=days)).isoformat()
    return kit.day(text, "the date")


def _gone(kind: str) -> Callable[[Callable[[], None]], str]:
    """Run a removal; a thing that is already gone is fine (someone removed it in its app)."""

    def run(remove: Callable[[], None]) -> str:
        try:
            remove()
        except LookupError:
            return f"The {kind} was already gone."
        return ""

    return run


def _create_goal(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    category = str(args.get("category") or "").lower()
    goal = journey.create_goal(
        connection,
        me,
        {
            "title": args.get("title"),
            "category": category if category in journey.GOAL_CATEGORIES else "personal",
            "target_date": _date(args.get("target_date"), connection),
            "description": args.get("description") or "",
            "steps": args.get("steps") if isinstance(args.get("steps"), list) else None,
        },
    )
    due = f" by {goal['target_date']}" if goal["target_date"] else ""
    return done(f'Added the goal "{goal["title"]}"{due}.', goal_id=goal["id"])


def _remove_goal(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    gone = _gone("goal")(
        lambda: journey.delete_goal(connection, me, str(undo.get("goal_id") or ""))
    )
    return gone or "Removed the goal."


def _find_goal(
    connection: sqlite3.Connection, me: dict[str, Any], name: Any
) -> dict[str, Any]:
    from aira.suite.journey import service as journey

    wanted = str(name or "").strip().lower()
    if not wanted:
        raise ValueError("Say which goal.")
    goals = journey.list_goals(connection, me)
    exact = [g for g in goals if g["title"].lower() == wanted]
    partial = [
        g for g in goals if wanted in g["title"].lower() or g["title"].lower() in wanted
    ]
    found = exact or partial
    if not found:
        raise LookupError(f'You have no goal called "{name}".')
    if len(found) > 1 and not exact:
        names = ", ".join(f'"{g["title"]}"' for g in found[:4])
        raise ValueError(f"Which goal did you mean: {names}?")
    return found[0]


def _log_progress(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    goal = _find_goal(connection, me, args.get("goal"))
    before = int(goal["progress"])
    updated = journey.check_in(
        connection,
        me,
        goal["id"],
        {"progress": args.get("progress"), "note": args.get("note") or ""},
    )
    undo = (
        {"goal_id": goal["id"], "progress": before}
        if journey.measure_of(goal) == "percent"
        else {}
    )
    if updated["status"] == "completed":
        return done(f'Marked "{updated["title"]}" as done. Well done!', **undo)
    return done(f'Set "{updated["title"]}" to {updated["progress"]}%.', **undo)


def _restore_progress(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    try:
        goal = journey.check_in(
            connection,
            me,
            str(undo.get("goal_id") or ""),
            {"progress": undo.get("progress"), "note": "Undone from AI"},
        )
    except LookupError:
        return "The goal was already gone."
    return f'Set "{goal["title"]}" back to {goal["progress"]}%.'


def _add_event(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    kind = str(args.get("kind") or "").lower()
    event = journey.save_event(
        connection,
        me,
        {
            "title": args.get("title"),
            "event_date": _date(args.get("date"), connection)
            or journey.today(connection).isoformat(),
            "kind": kind if kind in journey.EVENT_KINDS else "life",
            "location": args.get("location") or "",
            "description": args.get("description") or "",
        },
    )
    return done(
        f'Added "{event["title"]}" to your timeline on {event["event_date"]}.',
        event_id=event["id"],
    )


def _remove_event(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.journey import service as journey, store as journey_store

    journey_store.ensure(connection)
    gone = _gone("timeline event")(
        lambda: journey.delete_event(connection, me, str(undo.get("event_id") or ""))
    )
    return gone or "Removed it from your timeline."


_TARGET = re.compile(
    r"(?:rs\.?|inr|usd|eur|gbp|aud|cad|[₹$€£])?\s*(\d{1,3}(?:,\d{3})+|\d+)(\.\d{1,2})?\s*"
    r"(k|thousand|m|million|lakhs?|lacs?|crores?)?\s*(?:rs\.?|inr|usd|eur|gbp|aud|cad|dollars?|rupees?|euros?|pounds?)?"
)
_SCALE = {"k": 1_000, "thousand": 1_000, "m": 1_000_000, "million": 1_000_000, "lakh": 100_000, "lac": 100_000,
          "crore": 10_000_000}  # fmt: skip


def earning_target(value: Any) -> str:
    """How much an earning goal is for, as a plain number ("50000", "1250.50"), from what a person or the model
    wrote: 50000, "50,000", "$5k", "2 lakh". Anything else (a minus sign, 1e6, "5 to 10 thousand") is refused rather
    than guessed at."""
    hint = "Say how much you want to earn as a number above 0, for example 50000."
    if isinstance(value, bool):
        raise ValueError(hint)
    if isinstance(value, int | float):
        if not value == value or value <= 0:  # NaN or not above 0
            raise ValueError(
                "An earning goal must be more than 0." if value == value else hint
            )
        value = f"{value:.2f}"
    text = " ".join(str(value or "").lower().split())
    if not text:
        raise ValueError("Say how much you want to earn, for example 50000.")
    if re.match(r"^(?:rs\.?|inr|usd|eur|gbp|[₹$€£])?\s*[-−–]", text):
        raise ValueError("An earning goal must be more than 0.")
    match = _TARGET.fullmatch(text)
    if not match:
        raise ValueError(hint)
    amount = Decimal(match.group(1).replace(",", "") + (match.group(2) or ""))
    unit = (match.group(3) or "").removesuffix("s")
    amount *= _SCALE.get(unit, 1)
    if amount <= 0:
        raise ValueError("An earning goal must be more than 0.")
    if amount > 10_000_000:
        raise ValueError("That earning goal is too large. Try a smaller amount.")
    return format(amount.quantize(Decimal("0.01")).normalize(), "f")


def _create_earning_goal(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira import suite
    from aira.suite.journey import service as journey, store as journey_store

    if not suite.app_enabled("journey"):
        raise ValueError("Earning goals live in the Life app. Add Life first.")
    journey_store.ensure(connection)
    target = earning_target(
        args.get("target")
        if args.get("target") not in (None, "")
        else args.get("amount")
    )
    title = str(args.get("title") or "").strip()
    if not title:  # "Earn $5,000", in the account's currency
        amount = kit.fmt_money(
            kit.cents(target, "the amount"), kit.business(connection)["currency"]
        )
        title = f"Earn {amount.removesuffix('.00')}"
    goal = journey.create_goal(
        connection,
        me,
        {
            "title": title,
            "category": "money",
            "measure": "money",
            "source": "earnings",
            "target": target,
            "target_date": _date(args.get("target_date"), connection),
        },
    )
    due = f" by {goal['target_date']}" if goal["target_date"] else ""
    return done(
        f'Added the earning goal "{goal["title"]}"{due}. It counts what your apps earn from today.',
        goal_id=goal["id"],
    )


def _add_task(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.planner import service as planner, store as planner_store

    planner_store.ensure(connection)
    time = str(args.get("due_time") or "").strip()
    task = planner.create_task(
        connection,
        me,
        {
            "title": args.get("title"),
            "assignee": me["id"],
            "due_date": _date(args.get("due_date"), connection),
            "due_time": time,
            "notes": args.get("notes") or "",
        },
    )
    when = f" for {task['due_date']}" if task.get("due_date") else ""
    at = f" at {task['due_time']}" if task.get("due_time") else ""
    return done(f'Added the task "{task["title"]}"{when}{at}.', task_id=task["id"])


def _remove_task(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.planner import service as planner, store as planner_store

    planner_store.ensure(connection)
    gone = _gone("task")(
        lambda: planner.delete_task(connection, me, str(undo.get("task_id") or ""))
    )
    return gone or "Removed the task."


def shopping_items(args: dict[str, Any]) -> list[str]:
    """The things to buy, however the model wrote them: a list, or "milk, eggs and bread"."""
    items = args.get("items")
    if items is None or items == "":
        items = args.get("name") or args.get("item") or ""
    if isinstance(items, str):
        items = re.split(r",|\band\b|;|\n", items)
    if not isinstance(items, list):
        items = [items]
    return [str(item).strip() for item in items if str(item or "").strip()][:20]


def _add_shopping(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.planner import service as planner, store as planner_store

    planner_store.ensure(connection)
    lists = planner.lists(connection)
    wanted = str(args.get("list") or "").strip().lower()
    target = next(
        (entry for entry in lists if entry["name"].lower() == wanted), lists[0]
    )
    added, ids = [], []
    for name in shopping_items(args):
        item = planner.add_item(
            connection,
            me["id"],
            target["id"],
            {"name": name, "quantity": args.get("quantity") or ""},
        )
        added.append(item["name"])
        ids.append(item["id"])
    if not added:
        raise ValueError("Say what to add to the list.")
    return done(f'Added {", ".join(added)} to "{target["name"]}".', item_ids=ids)


def _remove_shopping(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.planner import service as planner, store as planner_store

    planner_store.ensure(connection)
    removed = 0
    for item_id in list(undo.get("item_ids") or [])[:20]:
        if not _gone("item")(lambda i=str(item_id): planner.delete_item(connection, i)):
            removed += 1
    if not removed:
        return "Those items were already gone."
    return f"Took {removed} {'item' if removed == 1 else 'items'} off the list."


def _add_note(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.planner import service as planner, store as planner_store

    planner_store.ensure(connection)
    note = planner.save_note(
        connection,
        me,
        {
            "title": args.get("title") or "",
            "body": args.get("body") or "",
            "shared": False,
        },
    )
    return done(
        f'Saved a private note "{note.get("title") or (note.get("body") or "")[:40]}".',
        note_id=note["id"],
    )


def _remove_note(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.planner import service as planner, store as planner_store

    planner_store.ensure(connection)
    gone = _gone("note")(
        lambda: planner.delete_note(connection, me, str(undo.get("note_id") or ""))
    )
    return gone or "Removed the note."


def _remember(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.ai import memory

    saved = memory.add(connection, me, args.get("text") or args.get("fact"))
    if saved.get("existing"):
        return f'I already remember: "{saved["text"]}"'
    return done(
        f'I will remember: "{saved["text"]}"',
        memory_id=saved["id"],
        text=saved["text"],
    )


def _unremember(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.ai import memory

    memory_id = str(undo.get("memory_id") or "")
    try:
        now = memory.get(connection, me, memory_id)
    except LookupError:
        return "The memory was already gone."
    # Changed in the Memory tab since: undoing the old "remember" must not throw away what the person wrote after.
    said = undo.get("text")
    if said is not None and now["text"] != said:
        raise ValueError(
            f'You changed this memory since, to "{now["text"]}". To forget it, use Forget in AI > Memory.'
        )
    memory.delete(connection, me, memory_id)
    return f'Forgot "{now["text"]}" again.'


def _forget(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.ai import memory

    found = memory.find(connection, me, args.get("text") or args.get("fact"))
    memory.delete(connection, me, found["id"])
    return done(f'I forgot: "{found["text"]}"', text=found["text"])


def _reremember(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.ai import memory

    saved = memory.add(connection, me, undo.get("text"))
    return f'I remember again: "{saved["text"]}"'


def _forget_all(
    connection: sqlite3.Connection, me: dict[str, Any], args: dict[str, Any]
) -> Any:
    from aira.suite.ai import memory

    texts = [m["text"] for m in memory.list_memories(connection, me)]
    if not texts:
        return "There was nothing to forget: I do not remember anything about you yet."
    memory.clear(connection, me)
    return done(
        f"I forgot everything you asked me to remember ({len(texts)} {'thing' if len(texts) == 1 else 'things'}).",
        texts=texts,
    )


def _remember_all_again(
    connection: sqlite3.Connection, me: dict[str, Any], undo: dict[str, Any]
) -> str:
    from aira.suite.ai import memory

    back = 0
    for text in reversed(list(undo.get("texts") or [])[: memory.MAX_MEMORIES]):
        try:
            saved = memory.add(connection, me, text)
        except ValueError:  # full again, or an empty line
            continue
        back += 0 if saved.get("existing") else 1
    return f"I remember {back} {'thing' if back == 1 else 'things'} again."


def _memory_facts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    from aira.suite.ai import memory

    found = memory.lines(connection, me)
    if not found:
        return [
            'Nothing yet. Say "Remember that ..." and I will keep it in mind for every answer.'
        ]
    return [f"You asked me to remember: {text}" for text in found]


MEMORY_ACTIONS = (
    Action(
        "remember",
        "Remember something about you",
        "minor",
        {"text": "what to remember, as a short sentence"},
        _remember,
        lambda a: f'Remember "{str(a.get("text") or a.get("fact") or "")[:80]}"',
        _unremember,
        typed_only=True,
    ),
    Action(
        "forget",
        "Forget something",
        "minor",
        {"text": "the words of what to forget"},
        _forget,
        lambda a: f'Forget "{str(a.get("text") or a.get("fact") or "")[:80]}"',
        _reremember,
        typed_only=True,
    ),
    Action(
        "forget_all",
        "Forget everything",
        "major",
        {},
        _forget_all,
        lambda a: "Forget everything you asked Monosage to remember",
        _remember_all_again,
        typed_only=True,
    ),
)
LIFE_ACTIONS = (
    Action(
        "create_goal",
        "Add a goal",
        "minor",
        {
            "title": "the goal",
            "category": "career, health, money, learning, family, relationships, home, travel, creative or personal",
            "target_date": "YYYY-MM-DD, tomorrow, in 3 months (optional)",
            "steps": "a list of short steps (optional)",
        },
        _create_goal,
        lambda a: (
            f'Add the goal "{a.get("title", "")}"'
            + (f" by {a['target_date']}" if a.get("target_date") else "")
        ),
        _remove_goal,
    ),
    Action(
        "log_progress",
        "Update a goal's progress",
        "minor",
        {
            "goal": "the goal's name",
            "progress": "0 to 100",
            "note": "a short note (optional)",
        },
        _log_progress,
        lambda a: f'Set "{a.get("goal", "")}" to {a.get("progress", "?")}%',
        _restore_progress,
    ),
    Action(
        "add_life_event",
        "Add to the timeline",
        "minor",
        {
            "title": "what happened",
            "date": "YYYY-MM-DD or today",
            "kind": "life, birth, education, career, family, relationship, home, health, travel or achievement",
            "location": "where (optional)",
        },
        _add_event,
        lambda a: f'Add "{a.get("title", "")}" to the timeline',
        _remove_event,
    ),
)
PLANNER_ACTIONS = (
    Action(
        "add_task",
        "Add a task",
        "minor",
        {
            "title": "what needs doing",
            "due_date": "YYYY-MM-DD, today or tomorrow (optional)",
            "due_time": "HH:MM (optional)",
        },
        _add_task,
        lambda a: (
            f'Add the task "{a.get("title", "")}"'
            + (f" for {a['due_date']}" if a.get("due_date") else "")
        ),
        _remove_task,
    ),
    Action(
        "add_shopping_item",
        "Add to a shopping list",
        "minor",
        {"items": "a list of things to buy", "list": "the list's name (optional)"},
        _add_shopping,
        lambda a: (
            "Add " + (", ".join(shopping_items(a)) or "items") + " to the shopping list"
        ),
        _remove_shopping,
    ),
    Action(
        "add_note",
        "Save a note",
        "minor",
        {"title": "a short title", "body": "the note"},
        _add_note,
        lambda a: f'Save a note "{a.get("title") or str(a.get("body", ""))[:40]}"',
        _remove_note,
    ),
)
EARN_ACTIONS = (
    Action(
        "create_earning_goal",
        "Set an earning goal",
        "minor",
        {
            "target": "how much to earn (a number, in the account's currency)",
            "title": "a short name (optional)",
            "target_date": "YYYY-MM-DD, in 3 months (optional)",
        },
        _create_earning_goal,
        lambda a: (
            f"Set an earning goal of {a.get('target') or a.get('amount') or '?'}"
            + (f" by {a['target_date']}" if a.get("target_date") else "")
        ),
        _remove_goal,
    ),
)


def _earn_facts(connection: sqlite3.Connection, me: dict[str, Any]) -> list[str]:
    from aira.suite.ai import earn

    return earn.facts(connection, me)


def _may_count_earnings(me: dict[str, Any]) -> bool:
    from aira.suite.journey import service as journey

    return journey.may_count_earnings(me)


AGENTS: tuple[Agent, ...] = (
    Agent(
        "life",
        "LifeAgent",
        "journey",
        "journey",
        "Your goals, your timeline and the documents in your vault.",
        (
            "goal",
            "goals",
            "life",
            "milestone",
            "timeline",
            "document",
            "passport",
            "expire",
            "expiry",
            "progress",
            "achieve",
            "score",
            "licence",
            "license",
            "insurance",
            "visa",
            "certificate",
            "vault",
            "dream",
            "plan",
        ),
        "auto_minor",
        LIFE_ACTIONS,
        reader=_life_facts,
    ),
    Agent(
        "planner",
        "PlannerAgent",
        "planner",
        "planner",
        "Chores and tasks, shopping lists, bills and notes.",
        (
            "task",
            "tasks",
            "chore",
            "chores",
            "todo",
            "to-do",
            "shopping",
            "buy",
            "grocery",
            "groceries",
            "bill",
            "bills",
            "note",
            "notes",
            "remind",
            "reminder",
            "calendar",
            "schedule",
            "today",
            "tomorrow",
            "list",
        ),
        "auto_minor",
        PLANNER_ACTIONS,
        reader=_planner_facts,
    ),
    Agent(
        "health",
        "HealthAgent",
        "care",
        "care",
        "Medicines due, appointments and readings.",
        (
            "health",
            "medicine",
            "medicines",
            "medication",
            "dose",
            "pill",
            "pills",
            "tablet",
            "appointment",
            "doctor",
            "blood",
            "pressure",
            "sugar",
            "mood",
            "sleep",
            "weight",
            "wellbeing",
        ),
    ),
    Agent(
        "family",
        "FamilyAgent",
        "social",
        "family",
        "The family's calendar, birthdays and messages.",
        (
            "family",
            "birthday",
            "birthdays",
            "calendar",
            "event",
            "events",
            "mum",
            "mom",
            "dad",
            "kids",
            "children",
            "anniversary",
        ),
    ),
    Agent(
        "social",
        "SocialAgent",
        "social",
        "social",
        "Friends, chats and what is new in your feed.",
        (
            "social",
            "friend",
            "friends",
            "chat",
            "chats",
            "message",
            "messages",
            "post",
            "posts",
            "feed",
            "follower",
        ),
    ),
    Agent(
        "learn",
        "LearnAgent",
        "learn",
        "learn",
        "Flashcards due, streaks and practice.",
        (
            "learn",
            "study",
            "studying",
            "flashcard",
            "flashcards",
            "streak",
            "quiz",
            "deck",
            "practice",
            "revise",
            "homework",
            "maths",
            "math",
        ),
    ),
    Agent(
        "play",
        "PlayAgent",
        "play",
        "play",
        "Today's challenge, games and things to watch.",
        (
            "play",
            "game",
            "games",
            "trivia",
            "challenge",
            "fun",
            "watch",
            "film",
            "movie",
            "show",
            "bored",
        ),
    ),
    Agent(
        "news",
        "NewsAgent",
        "news",
        "news",
        "Today's headlines in your language.",
        ("news", "headline", "headlines", "happening", "world", "latest"),
    ),
    Agent(
        "videos",
        "VideosAgent",
        "videos",
        "videos",
        "Your channel, new videos and what you earned.",
        (
            "video",
            "videos",
            "reel",
            "reels",
            "channel",
            "creator",
            "earnings",
            "views",
            "subscribers",
            "upload",
        ),
    ),
    Agent(
        "business",
        "BusinessAgent",
        "",
        "",
        "Revenue, sales and the numbers of every business app.",
        (
            "business",
            "revenue",
            "sales",
            "sale",
            "invoice",
            "invoices",
            "order",
            "orders",
            "booking",
            "bookings",
            "customer",
            "customers",
            "income",
            "profit",
            "shop",
            "store",
            "clients",
            "deals",
            "insights",
        ),
        reader=_business_facts,
        allowed=_may_see_business,
    ),
    Agent(
        "earn",
        "EarnAgent",
        "ai",
        "",
        "What your apps earned, your earning goals and the next steps to earn more.",
        (
            "earn",
            "earned",
            "earning",
            "earnings",
            "money",
            "income",
            "hustle",
            "paid",
            "payment",
            "payments",
            "make money",
            "side income",
        ),
        "auto_minor",
        EARN_ACTIONS,
        reader=_earn_facts,
        allowed=_may_count_earnings,
    ),
    Agent(
        "memory",
        "MemoryAgent",
        "ai",
        "",
        "What you asked Monosage to remember about you. Every answer takes it into account.",
        ("remember", "memory", "memories", "forget", "forgot", "remembered"),
        "auto_minor",
        MEMORY_ACTIONS,
        reader=_memory_facts,
        brief=False,
    ),
)
BY_ID = {agent.id: agent for agent in AGENTS}
REGISTERED: list[
    Agent
] = []  # agents other apps add from their own route modules (register)


def register(agent: Agent) -> Agent:
    """Add an app's agent (call it from the app's ``<id>_routes.py``; registering an id again replaces it).

    A registered agent works like a built-in one: the person switches it on or off and sets its consent level;
    its actions pass the same consent gate (and the daily limit: ``default_limit`` until the person sets one),
    wait in Approvals and are logged in Activity (and can be undone when the action has an ``undo``); its
    ``earner`` lines show on Earn. Example (``aira/suite/stocks/stocks_routes.py``)::

        from aira.suite.ai import agents
        agents.register(agents.Agent("stocks", "StocksAgent", "stocks", "", "Quotes, movers and your portfolio.",
                                     ("stock", "share", "market"), "auto_minor", ACTIONS, reader=assistant.facts))
    """
    if agent.id in {a.id for a in AGENTS}:
        raise ValueError(f"The agent id {agent.id} is taken.")
    if not re.fullmatch(r"[a-z]{2,20}", agent.id):
        raise ValueError("An agent id is 2 to 20 lowercase letters.")
    if agent.default_consent not in CONSENT_LEVELS:
        raise ValueError(f"Unknown consent level {agent.default_consent}.")
    if not 0 <= int(agent.default_limit) <= 1000:
        raise ValueError("default_limit is 0 (no limit) to 1000 actions a day.")
    for action in agent.actions:
        if action.risk not in ("minor", "major"):
            raise ValueError(f"The action {action.name} needs a risk: minor or major.")
    REGISTERED[:] = [a for a in REGISTERED if a.id != agent.id] + [agent]
    BY_ID[agent.id] = agent
    return agent


def _business_apps() -> bool:
    return any(suite.app_enabled(app.id) for app in app_catalog.INDUSTRY_APPS)


def available(me: dict[str, Any]) -> list[Agent]:
    """The agents of the apps this account has, that this person may use."""
    out = []
    for agent in (*AGENTS, *REGISTERED):
        if agent.app:
            if not suite.app_enabled(agent.app):
                continue
            if (
                agent.section
                and agent.reader is None
                and agent.section not in life.TODAY
            ):
                continue
        elif not _business_apps():
            continue
        if agent.allowed is not None and not agent.allowed(me):
            continue
        out.append(agent)
    return out
