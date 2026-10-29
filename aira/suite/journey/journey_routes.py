"""Life routes (``/api/journey/*``), its Today card and its pulse.

Every route works for the person asking (:func:`aira.suite.life.current`); :mod:`aira.suite.journey.service`
holds the rules. Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, life, public
from aira.suite.journey import lifebook, service, store
from aira.suite.journey import lifebook_routes as _lifebook_routes  # noqa: F401 - registers the life stages routes

APP = store.APP
BASE = "/api/journey"
ID = "([0-9a-f]{32})"
# Step ideas change nothing: in the hosted build they are a read (life.read).
HOSTED_RULES = {"goals_suggest": "read"}


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    store.ensure(connection)
    me = life.current(request, connection)
    return connection, me


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            **service.overview(connection, me),
            "stage": lifebook.summary(connection, me),
        }


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    """The search box at the top of the Aira frame: a few of each kind of the person's own records."""
    connection, me = _open(request)
    with connection:
        return 200, service.search(
            connection, me, request.q("q"), limit=request.q("limit") or 6
        )


# ---------------------------------------------------------------------------
# goals
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/goals")
def goals_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goals": service.list_goals(
                connection,
                me,
                status=request.q("status"),
                category=request.q("category"),
                search=request.q("q"),
            ),
            "categories": list(service.GOAL_CATEGORIES),
            "today": service.today(connection).isoformat(),
        }


@aira_route("POST", f"{BASE}/goals")
def goals_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        goal = service.create_goal(connection, me, request.payload)
    request.audit("journey.goal_add", "goal", goal["id"])
    return 201, {"goal": goal}


@aira_route("GET", f"{BASE}/goals/{ID}")
def goals_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": service.goal_detail(connection, me, request.match.group(1))
        }


@aira_route("POST", f"{BASE}/goals/{ID}/link")
def goals_link(request: Request) -> tuple[int, dict[str, Any]]:
    """A public page for the goal, so friends and family can follow it and cheer (not for children)."""
    connection, me = _open(request)
    with connection:
        if not service.may_share(me):
            raise PermissionError("Children cannot share pages outside the family.")
        goal = service.get_goal(connection, me, request.match.group(1))
        link = public.create_link(
            connection, APP, "goal_page", goal["id"], title=goal["title"]
        )
    request.audit("journey.goal_share", "goal", goal["id"])
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/goals/{ID}/link")
def goals_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        goal = service.get_goal(connection, me, request.match.group(1))
        public.revoke(connection, APP, "goal_page", goal["id"])
        # Supporters back a goal on its page: with no page, the goal no longer takes support.
        stopped = bool(goal.get("support"))
        if stopped:
            service.set_support(connection, me, goal["id"], {"support": False})
        detail = service.goal_detail(connection, me, goal["id"])
    request.audit("journey.goal_unshare", "goal", goal["id"])
    return 200, {"link": None, "goal": detail, "support_stopped": stopped}


@aira_route("POST", f"{BASE}/goals/suggest")
def goals_suggest(request: Request) -> tuple[int, dict[str, Any]]:
    """Small steps for a goal from the writing model (or a template for its area), to edit before saving."""
    connection, _me = _open(request)
    connection.close()  # the model may take a while: do not hold the database
    return 200, service.suggest_steps(request.payload)


@aira_route("PATCH", f"{BASE}/goals/{ID}/support")
def goals_support(request: Request) -> tuple[int, dict[str, Any]]:
    """Let people back the goal from its public page (adults only), with how they pay the owner."""
    connection, me = _open(request)
    with connection:
        goal = service.set_support(
            connection, me, request.match.group(1), request.payload
        )
        if goal["support"] and not goal["link"]:
            public.create_link(
                connection, APP, "goal_page", goal["id"], title=goal["title"]
            )
            goal = service.goal_detail(connection, me, goal["id"])
    request.audit(
        "journey.goal_support_on" if goal["support"] else "journey.goal_support_off",
        "goal",
        goal["id"],
    )
    return 200, {"goal": goal}


@aira_route("PATCH", f"{BASE}/pledges/{ID}")
def pledges_update(request: Request) -> tuple[int, dict[str, Any]]:
    """Mark a supporter's pledge received (added to a money goal), promised again, or cancelled."""
    connection, me = _open(request)
    with connection:
        goal = service.set_pledge(
            connection, me, request.match.group(1), request.payload
        )
    request.audit("journey.pledge_update", "pledge", request.match.group(1))
    return 200, {"goal": goal}


@aira_route("DELETE", f"{BASE}/pledges/{ID}")
def pledges_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_pledge(connection, me, request.match.group(1))
    request.audit("journey.pledge_delete", "pledge", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("DELETE", f"{BASE}/cheers/{ID}")
def cheers_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_cheer(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/earnings")
def earnings_get(request: Request) -> tuple[int, dict[str, Any]]:
    """What the account's apps earned this month (for an earning goal), when this person may see it."""
    connection, me = _open(request)
    with connection:
        if not service.may_count_earnings(me):
            return 200, {"allowed": False}
        currency = kit.currency(
            request.q("currency"), default=kit.business(connection)["currency"]
        )
        start = service.today(connection).replace(day=1).isoformat()
        return 200, {
            "allowed": True,
            "currency": currency,
            "this_month": service.earnings(connection, currency, start),
        }


@aira_route("PATCH", f"{BASE}/goals/{ID}")
def goals_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": service.update_goal(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/goals/{ID}")
def goals_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_goal(connection, me, request.match.group(1))
    request.audit("journey.goal_delete", "goal", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/goals/{ID}/checkin")
def goals_checkin(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": service.check_in(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("POST", f"{BASE}/goals/{ID}/steps")
def steps_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {
            "goal": service.add_step(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("PATCH", f"{BASE}/steps/{ID}")
def steps_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": service.set_step(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/steps/{ID}")
def steps_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": service.delete_step(connection, me, request.match.group(1))
        }


@aira_route("GET", f"{BASE}/export/events")
def events_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(
            200,
            service.events_csv(connection, me),
            "text/csv; charset=utf-8",
            "timeline.csv",
        )


@aira_route("GET", f"{BASE}/export/documents")
def documents_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(
            200,
            service.documents_csv(connection, me),
            "text/csv; charset=utf-8",
            "documents.csv",
        )


@aira_route("GET", f"{BASE}/export/goals")
def goals_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(
            200,
            service.goals_csv(connection, me),
            "text/csv; charset=utf-8",
            "goals.csv",
        )


# ---------------------------------------------------------------------------
# timeline
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/events")
def events_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "events": service.list_events(
                connection,
                me,
                kind=request.q("kind"),
                year=request.q("year"),
                family=bool(request.qbool("family")),
                search=request.q("q"),
            ),
            "kinds": list(service.EVENT_KINDS),
            "years": service.event_years(connection, me),
        }


@aira_route("POST", f"{BASE}/events")
def events_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        event = service.save_event(connection, me, request.payload)
    request.audit("journey.event_add", "event", event["id"])
    return 201, {"event": event}


@aira_route("PATCH", f"{BASE}/events/{ID}")
def events_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "event": service.save_event(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/events/{ID}")
def events_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_event(connection, me, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# document vault
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/documents")
def documents_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "documents": service.list_documents(
                connection,
                me,
                kind=request.q("kind"),
                state=request.q("state"),
                search=request.q("q"),
            ),
            "kinds": list(service.DOCUMENT_KINDS),
            "bytes": service.vault_usage(connection, me),
            "max_file_bytes": service.MAX_FILE_BYTES,
            "max_vault_bytes": service.MAX_VAULT_BYTES,
        }


@aira_route("POST", f"{BASE}/documents")
def documents_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        document = service.save_document(
            connection, me, request.payload, request.secret_store
        )
    request.audit("journey.document_add", "document", document["id"])
    return 201, {"document": document}


@aira_route("GET", f"{BASE}/documents/{ID}")
def documents_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "document": service.get_document(
                connection, me, request.match.group(1), request.secret_store
            )
        }


@aira_route("PATCH", f"{BASE}/documents/{ID}")
def documents_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        document = service.save_document(
            connection,
            me,
            request.payload,
            request.secret_store,
            request.match.group(1),
        )
    request.audit("journey.document_update", "document", document["id"])
    return 200, {"document": document}


@aira_route("DELETE", f"{BASE}/documents/{ID}")
def documents_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_document(connection, me, request.match.group(1))
    request.audit("journey.document_delete", "document", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/documents/{ID}/file")
def documents_file(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        data, kind, name = service.document_file(
            connection, me, request.match.group(1), request.secret_store
        )
    request.audit("journey.document_open", "document", request.match.group(1))
    return Raw(200, data, kind, name, headers=(("Cache-Control", "no-store"),))


# ---------------------------------------------------------------------------
# habits
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/habits")
def habits_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "habits": service.list_habits(connection, me),
            "stopped": service.list_habits(connection, me, archived=True),
            "today": service.today(connection).isoformat(),
        }


@aira_route("POST", f"{BASE}/habits")
def habits_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        habit = service.save_habit(connection, me, request.payload)
    request.audit("journey.habit_add", "habit", habit["id"])
    return 201, {"habit": habit}


@aira_route("PATCH", f"{BASE}/habits/{ID}")
def habits_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "habit": service.save_habit(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/habits/{ID}")
def habits_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_habit(connection, me, request.match.group(1))
    request.audit("journey.habit_delete", "habit", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/habits/{ID}/done")
def habits_done(request: Request) -> tuple[int, dict[str, Any]]:
    """Tick a habit for today (or ``day``); ticking twice changes nothing (Today's button may be pressed again)."""
    connection, me = _open(request)
    with connection:
        return 200, {
            "habit": service.mark_habit(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("POST", f"{BASE}/habits/{ID}/undo")
def habits_undo(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "habit": service.unmark_habit(
                connection, me, request.match.group(1), request.payload
            )
        }


# ---------------------------------------------------------------------------
# journal
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/journal")
def journal_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "entries": service.list_entries(
                connection,
                me,
                month=request.q("month"),
                search=request.q("q"),
                mood=request.q("mood"),
            ),
            "summary": service.journal_summary(connection, me),
            "memories": service.memories(connection, me),
            "moods": [
                {"mood": mood, "label": label} for mood, label in service.MOODS.items()
            ],
        }


@aira_route("POST", f"{BASE}/journal")
def journal_save(request: Request) -> tuple[int, dict[str, Any]]:
    """Write the journal for a day (today unless ``day`` says otherwise); writing the same day again updates it."""
    connection, me = _open(request)
    with connection:
        return 200, {"entry": service.save_entry(connection, me, request.payload)}


@aira_route("POST", f"{BASE}/journal/mood")
def journal_mood(request: Request) -> tuple[int, dict[str, Any]]:
    """Today's mood in one tap (Today's card); pressing again changes nothing."""
    connection, me = _open(request)
    with connection:
        return 200, {"entry": service.set_mood(connection, me, request.payload)}


@aira_route("DELETE", f"{BASE}/journal/{ID}")
def journal_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_entry(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/export/journal")
def journal_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(
            200,
            service.journal_csv(connection, me),
            "text/csv; charset=utf-8",
            "journal.csv",
        )


# ---------------------------------------------------------------------------
# a goal's public page
# ---------------------------------------------------------------------------


@public.page(APP, "goal_page")
def _goal_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    # A person's goal: their own way to be paid (supporters), never the business's name or payment link.
    return {**service.public_goal(connection, link["ref_id"]), "brand": False}


@public.action(APP, "goal_page", "cheer")
def _cheer(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    cheer = service.add_cheer(
        connection, link["ref_id"], payload, str(visitor.get("client") or "")
    )
    return 201, {"cheer": cheer}


@public.action(APP, "goal_page", "pledge")
def _pledge(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """A visitor backs the goal with an amount they will pay the owner (through the owner's own link)."""
    store.ensure(connection)
    pledge = service.add_pledge(
        connection, link["ref_id"], payload, str(visitor.get("client") or "")
    )
    return 201, {"pledge": pledge}


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


def _habit_sub(habit: dict[str, Any]) -> str:
    if habit["every_day"]:
        if habit["streak"] > 1:
            return f"{habit['streak']} days in a row"
        return "Every day"
    week = f"{habit['this_week']} of {habit['per_week']} this week"
    return f"{week} · {habit['streak']} weeks in a row" if habit["streak"] > 1 else week


def _when(days_left: int | None) -> str:
    if days_left is None:
        return ""
    if days_left < 0:
        return f"{-days_left} days ago" if days_left < -1 else "Yesterday"
    if days_left == 0:
        return "Today"
    if days_left == 1:
        return "Tomorrow"
    return f"In {days_left} days"


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    due = service.reminders(connection, me)
    lines = [
        {
            "text": goal["title"],
            "sub": f"Goal {'was due' if (goal['days_left'] or 0) < 0 else 'due'}: {_when(goal['days_left'])} · {goal['progress']}%",
            "done": False,
            "link": "#/journey?tab=goals",
        }
        for goal in due["goals"]
    ] + [
        {
            "text": document["title"],
            "sub": f"{'Expired' if (document['days_left'] or 0) < 0 else 'Expires'}: {_when(document['days_left'])}",
            "done": False,
            "link": "#/journey?tab=documents",
        }
        for document in due["documents"]
    ]
    cards = []
    if lines:
        cards.append(
            life.card(
                APP,
                "Goals and documents",
                lines=lines[:6],
                tone="warn" if due["documents"] else "",
                icon="journey",
            )
        )
    habits = [
        habit
        for habit in service.list_habits(connection, me)
        if habit["every_day"]
        or habit["done_today"]
        or habit["this_week"] < habit["per_week"]
    ]
    if habits:
        cards.append(
            life.card(
                APP,
                "Habits today",
                lines=[
                    {
                        "text": habit["title"],
                        "sub": _habit_sub(habit),
                        "done": habit["done_today"],
                        **(
                            {}
                            if habit["done_today"]
                            else {
                                "action": life.action(
                                    "Done", f"{BASE}/habits/{habit['id']}/done"
                                )
                            }
                        ),
                    }
                    for habit in habits[:8]
                ],
                link="#/journey?tab=habits",
                icon="journey",
            )
        )
    health = lifebook.due(connection, me)
    if health:
        cards.append(
            life.card(
                APP,
                "Vaccines and check-ups",
                lines=[
                    {
                        "text": item["title"]
                        if item["self"]
                        else f"{item['who']}: {item['title']}",
                        "sub": f"{'Was due' if item['days_left'] < 0 else 'Due'}: {_when(item['days_left'])}",
                        "done": False,
                        "link": "#/journey?tab=stages"
                        + ("" if item["self"] else f"&life={item['life_id']}"),
                    }
                    for item in health[:6]
                ],
                link="#/journey?tab=stages",
                tone="warn" if any(item["days_left"] < 0 for item in health) else "",
                icon="journey",
            )
        )
    day = _your_day(connection, me)
    if day:
        cards.append(day)
    return cards


# Today offers three moods in one tap; the Journal tab has all five.
_TODAY_MOODS = (
    (5, "A good day", "\N{SMILING FACE WITH SMILING EYES}"),
    (3, "An okay day", "\N{NEUTRAL FACE}"),
    (1, "A hard day", "\N{DISAPPOINTED FACE}"),
)


_MOOD_MARKS = {
    5: "\N{SMILING FACE WITH SMILING EYES}",
    4: "\N{SLIGHTLY SMILING FACE}",
    3: "\N{NEUTRAL FACE}",
    2: "\N{CONFUSED FACE}",
    1: "\N{DISAPPOINTED FACE}",
}


def _your_day(connection: Any, me: dict[str, Any]) -> dict[str, Any] | None:
    """How the day is going (one tap writes it in the journal) and what happened on this day in earlier years.

    Only for people who use Life (a journal day, a goal or a habit): nobody else is asked every day."""
    memories = service.memories(connection, me, limit=2)
    if not memories and not service.uses_life(connection, me):
        return None
    journal = service.journal_summary(connection, me)
    entry = journal["entry_today"]
    if entry and entry["mood"]:
        # Not a "done" line: Today crosses those out, and a mood is not a task.
        lines = [
            {
                "text": f"{_MOOD_MARKS.get(entry['mood'], '')} Today: {entry['mood_label'].lower()}".strip(),
                "sub": "In your journal."
                if entry["body"] or entry["grateful"]
                else "In your journal. Add a few words in Life.",
                "done": False,
            }
        ]
    else:
        lines = [
            {
                "text": f"{mark} {label}",
                "sub": "How is today going?",
                "done": False,
                "action": life.action(
                    "This one", f"{BASE}/journal/mood", body={"mood": mood}
                ),
            }
            for mood, label, mark in _TODAY_MOODS
        ]
    for memory in memories:
        ago = memory["years_ago"]
        when = "1 year" if ago == 1 else f"{ago} years"
        lines.append(
            {
                "text": memory["title"],
                "sub": f"On this day {when} ago",
                "done": False,
            }
        )
    return life.card(
        APP, "Your day", lines=lines, link="#/journey?tab=journal", icon="journey"
    )


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    due = service.reminders(connection, me)
    alerts = [
        {
            "id": f"journey:doc:{document['id']}:{document['expires_on']}",
            "title": "A document expires soon"
            if (document["days_left"] or 0) >= 0
            else "A document has expired",
            "body": f"{document['title']}: {_when(document['days_left'])}",
            "tone": "warn",
            "speak": False,
            "actions": [],
        }
        for document in due["documents"]
        if document["days_left"] is not None and document["days_left"] in (30, 7, 1, 0)
    ]
    cheers = service.unread_cheers(connection, me)
    alerts += [
        {
            "id": f"journey:cheer:{cheer['id']}",
            "title": f"{cheer['name']} cheered you on",
            "body": f"{cheer['goal_title']}: {cheer['message']}",
            "tone": "good",
            "speak": False,
            "actions": [],
        }
        for cheer in cheers[:3]
    ]
    health = lifebook.due(connection, me)
    alerts += [
        {
            "id": f"journey:due:{item['id']}:{item['next_due']}",
            "title": f"{item['who']}: a vaccine is due"
            if item["kind"] == "vaccine"
            else f"{item['who']}: a check-up is due",
            "body": f"{item['title']}: {_when(item['days_left'])}",
            "tone": "warn",
            "speak": False,
            "actions": [],
        }
        for item in health
        if item["days_left"] in (7, 1, 0)
    ]
    pledges = service.unread_pledges(connection, me)
    alerts += [
        {
            "id": f"journey:pledge:{pledge['id']}",
            "title": f"{pledge['name']} backed your goal",
            "body": f"{pledge['goal_title']}: "
            + kit.fmt_money(int(pledge["amount"]), pledge["currency"])
            + (f" · {pledge['message']}" if pledge["message"] else ""),
            "tone": "good",
            "speak": False,
            "actions": [],
        }
        for pledge in pledges[:3]
    ]
    return {
        "badge": len(due["goals"])
        + len(due["documents"])
        + len(cheers)
        + len(pledges)
        + sum(1 for item in health if item["days_left"] <= 7),
        "alerts": alerts,
    }
