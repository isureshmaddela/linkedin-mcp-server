"""Play routes (``/api/play/*``), the live trivia public page (``trivia_room``), and Play's Today card and pulse.

Loaded by :func:`aira.suite.load_apps`. Every route is for the person using Aira (``life.current``); hosted rules
come from :func:`aira_cloud.policy.suite_rules` (``life.read`` / ``life.manage``, which every member has).
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, life, public
from aira.suite.play import quizzes, service, store
from aira.suite.play.store import APP

BASE = "/api/play"
ID = "([0-9a-f]{32})"


def _csv(rows: list[dict[str, Any]], columns: tuple[str, ...], name: str) -> Raw:
    return Raw(
        200, kit.csv_bytes(rows, columns), "text/csv; charset=utf-8", filename=name
    )


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    store.ensure(connection)
    me = life.current(request, connection)
    return connection, me


# ---------------------------------------------------------------------------
# games
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/games")
def games_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.catalog(connection, me)


@aira_route("GET", f"{BASE}/search")
def search(request: Request) -> tuple[int, dict[str, Any]]:
    """Games, quizzes, the watch list and live games that match ``q`` (the search box across the top)."""
    connection, me = _open(request)
    with connection:
        limit = request.qint("limit", 8, 1, 50)
        return 200, service.search(connection, me, request.q("q"), limit=limit)


@aira_route("POST", f"{BASE}/sessions")
def sessions_start(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"session": service.start(connection, me, request.payload)}


@aira_route("GET", f"{BASE}/sessions/{ID}")
def sessions_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "session": service.get_session(connection, me, request.match.group(1))
        }


@aira_route("POST", f"{BASE}/sessions/{ID}/move")
def sessions_move(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.move(
            connection, me, request.match.group(1), request.payload
        )


@aira_route("POST", f"{BASE}/sessions/{ID}/finish")
def sessions_finish(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "session": service.finish(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("GET", f"{BASE}/daily")
def daily(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.daily(connection, me)


@aira_route("GET", f"{BASE}/scores")
def scores(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """My scores (``?format=csv``: every game I finished, as a spreadsheet)."""
    connection, me = _open(request)
    with connection:
        if request.q("format") == "csv":
            return _csv(
                service.history_rows(connection, me),
                service.HISTORY_COLUMNS,
                "my-games.csv",
            )
        return 200, service.scores(connection, me, game=request.q("game"))


# ---------------------------------------------------------------------------
# challenges ("beat my score" on the same puzzle)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/challenges")
def challenges_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.challenges(connection, me)


@aira_route("POST", f"{BASE}/challenges")
def challenges_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"challenges": service.challenge(connection, me, request.payload)}


@aira_route("POST", f"{BASE}/challenges/{ID}/play")
def challenges_play(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "session": service.play_challenge(connection, me, request.match.group(1))
        }


@aira_route("DELETE", f"{BASE}/challenges/{ID}")
def challenges_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.decline_challenge(connection, me, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# quizzes people write
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/quizzes")
def quizzes_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"quizzes": quizzes.list_quizzes(connection, me)}


@aira_route("POST", f"{BASE}/quizzes")
def quizzes_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        quiz = quizzes.create(connection, me, request.payload)
    request.audit("play.quiz_create", "quiz", quiz["id"])
    return 201, {"quiz": quiz}


@aira_route("POST", f"{BASE}/quizzes/draft")
def quizzes_draft(request: Request) -> tuple[int, dict[str, Any]]:
    """Suggested questions on a topic (nothing is saved)."""
    connection, me = _open(request)
    with connection:
        return 200, quizzes.draft(connection, me, request.payload)


@aira_route("GET", f"{BASE}/quizzes/{ID}")
def quizzes_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"quiz": quizzes.get_quiz(connection, me, request.match.group(1))}


@aira_route("GET", f"{BASE}/quizzes/{ID}/board")
def quizzes_board(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"board": quizzes.board(connection, me, request.match.group(1))}


@aira_route("PATCH", f"{BASE}/quizzes/{ID}")
def quizzes_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        quiz = quizzes.update(connection, me, request.match.group(1), request.payload)
    request.audit("play.quiz_update", "quiz", quiz["id"])
    return 200, {"quiz": quiz}


@aira_route("DELETE", f"{BASE}/quizzes/{ID}")
def quizzes_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        quizzes.delete(connection, me, request.match.group(1))
    request.audit("play.quiz_delete", "quiz", request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# live trivia
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/rooms")
def rooms_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"rooms": service.rooms(connection, me)}


@aira_route("POST", f"{BASE}/rooms")
def rooms_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        room = service.create_room(connection, me, request.payload)
    request.audit("play.room_create", "room", room["id"])
    return 201, {"room": room}


@aira_route("POST", f"{BASE}/rooms/join")
def rooms_join_code(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "room": service.join_by_code(
                connection, me, str(request.payload.get("code") or "")
            )
        }


@aira_route("GET", f"{BASE}/rooms/{ID}")
def rooms_get(request: Request) -> tuple[int, dict[str, Any]]:
    """The room as it is now; poll this every couple of seconds while playing (``since`` = the last version)."""
    connection, me = _open(request)
    with connection:
        room = service._room(connection, request.match.group(1))
        state = service.room_state(connection, room, viewer_profile=me["id"])
        link = public.link_for(connection, APP, "trivia_room", room["id"])
        state["link"] = link if state["is_host"] else None
        state["changed"] = state["version"] != request.q("since")
        return 200, {"room": state}


@aira_route("POST", f"{BASE}/rooms/{ID}/join")
def rooms_join(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"room": service.join_room(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/rooms/{ID}/start")
def rooms_start(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"room": service.start_room(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/rooms/{ID}/next")
def rooms_next(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "room": service.next_question(connection, me, request.match.group(1))
        }


@aira_route("POST", f"{BASE}/rooms/{ID}/end")
def rooms_end(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"room": service.end_room(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/rooms/{ID}/answer")
def rooms_answer(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "room": service.answer_as(
                connection, me, request.match.group(1), request.payload.get("choice")
            )
        }


@aira_route("POST", f"{BASE}/rooms/{ID}/players/{ID}/paid")
def rooms_paid(request: Request) -> tuple[int, dict[str, Any]]:
    """The host marks a guest's entry fee as received (``{"paid": true}``) or takes the mark back."""
    connection, me = _open(request)
    paid = request.payload.get("paid", True)
    with connection:
        room = service.mark_paid(
            connection,
            me,
            request.match.group(1),
            request.match.group(2),
            paid if isinstance(paid, bool) else kit.flag(paid),
        )
    request.audit("play.entry_paid", "room", room["id"])
    return 200, {"room": room}


@aira_route("GET", f"{BASE}/rooms/{ID}/entries")
def rooms_entries(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """Who played and who paid (the host only; ``?format=csv`` for a spreadsheet)."""
    connection, me = _open(request)
    with connection:
        rows = service.entry_rows(connection, me, request.match.group(1))
    if request.q("format") == "csv":
        return _csv(rows, service.ENTRY_COLUMNS, "quiz-night-entries.csv")
    return 200, {"entries": rows}


@aira_route("GET", f"{BASE}/nights")
def nights(request: Request) -> tuple[int, dict[str, Any]]:
    """The quiz nights I hosted with an entry fee and what each one took."""
    connection, me = _open(request)
    with connection:
        return 200, service.nights(connection, me)


@aira_route("POST", f"{BASE}/rooms/{ID}/link")
def rooms_link(request: Request) -> tuple[int, dict[str, Any]]:
    """A link relatives without an account can use to join this room (the host, not a child)."""
    connection, me = _open(request)
    with connection:
        room = service._host_room(connection, me, request.match.group(1))
        if me.get("age_group") == "child":
            raise PermissionError(
                "Ask a grown-up to share the game outside the family."
            )
        link = public.create_link(
            connection, APP, "trivia_room", room["id"], title=room["title"]
        )
    request.audit("play.room_link", "room", room["id"])
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/rooms/{ID}/link")
def rooms_link_off(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        room = service._host_room(connection, me, request.match.group(1))
        public.revoke(connection, APP, "trivia_room", room["id"])
    return 200, {"link": None}


@public.page(APP, "trivia_room")
def trivia_room_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    room = service._room(connection, link["ref_id"])
    state = service.room_state(connection, room)
    state.pop("me", None)
    state["joinable"] = state["phase"] in ("lobby", "question", "reveal")
    return {"room": state}


@public.action(APP, "trivia_room", "join")
def trivia_room_join(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    token, state = service.join_guest(
        connection,
        link["ref_id"],
        str(payload.get("name") or ""),
        client=str(visitor.get("client") or ""),
    )
    return 201, {"token": token, "room": state}


@public.action(APP, "trivia_room", "state")
def trivia_room_state(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    player = service.guest_player(
        connection, link["ref_id"], str(payload.get("token") or "")
    )
    room = service._room(connection, link["ref_id"])
    return 200, {
        "room": service.room_state(connection, room, viewer_player=player["id"])
    }


@public.action(APP, "trivia_room", "answer")
def trivia_room_answer(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    player = service.guest_player(
        connection, link["ref_id"], str(payload.get("token") or "")
    )
    service.answer(connection, link["ref_id"], player, payload.get("choice"))
    room = service._room(connection, link["ref_id"])
    return 200, {
        "room": service.room_state(connection, room, viewer_player=player["id"])
    }


# ---------------------------------------------------------------------------
# watch and read list
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/watch")
def watch_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    connection, me = _open(request)
    with connection:
        items = service.watch_list(
            connection,
            me,
            kind=request.q("kind"),
            status=request.q("status"),
            query=request.q("q"),
        )
        if request.q("format") == "csv":
            return _csv(
                service.watch_rows(items), service.WATCH_COLUMNS, "watch-list.csv"
            )
        people = [life.public(p) for p in life.profiles(connection)]
        return 200, {
            "items": items,
            "people": people,
            "kinds": list(service.WATCH_KINDS),
        }


@aira_route("GET", f"{BASE}/watch/pick")
def watch_pick(request: Request) -> tuple[int, dict[str, Any]]:
    """Something to enjoy tonight, picked at random from the list (``together=1``: nobody has finished it)."""
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": service.pick(
                connection,
                me,
                kind=request.q("kind"),
                together=kit.flag(request.q("together")),
            )
        }


@aira_route("POST", f"{BASE}/watch")
def watch_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"item": service.add_item(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/watch/{ID}")
def watch_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": service.update_item(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/watch/{ID}")
def watch_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_item(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/watch/{ID}/status")
def watch_status(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": service.set_status(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("POST", f"{BASE}/watch/{ID}/plans")
def watch_plan(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {
            "item": service.add_plan(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/watch/plans/{ID}")
def watch_plan_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_plan(connection, me, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    lines: list[dict[str, Any]] = []
    day = service.daily(connection, me)
    if day["mine"] and day["mine"]["status"] == "finished":
        lines.append(
            {
                "text": f"Today's challenge done: {day['mine']['score']} points",
                "sub": f"{day['name']}. Streak: {day['streak']} day{'s' if day['streak'] != 1 else ''}.",
                "done": True,
            }
        )
    elif day["mine"]:
        lines.append(
            {
                "text": f"Today's challenge: {day['name']}",
                "sub": "You have started it. Open Play to carry on.",
            }
        )
    else:
        lines.append(
            {
                "text": f"Today's challenge: {day['name']}",
                "sub": "One try for everyone. Open Play to have a go.",
            }
        )
    waiting = service.challenges(connection, me)["incoming"]
    for item in waiting[:3]:
        lines.append(
            {
                "text": f"{item['who']} challenged you to {item['name']}",
                "sub": f"Beat {item['target']} points. Open Play to take it on.",
            }
        )
    for plan in service.plans_today(connection, me):
        who = ", ".join(plan["who"][:4])
        lines.append(
            {
                "text": f"Watch together: {plan['title']}",
                "sub": f"{plan['local'][-5:]} with {who}"
                if who
                else plan["local"][-5:],
            }
        )
    best = service.recent_best(connection, me)
    if best:
        lines.append(
            {
                "text": f"{best['name']} set a new best in {best['game']}: {best['score']}",
                "sub": "Can you beat it?",
            }
        )
    for room in service.rooms(connection, me):
        if not room["joined"]:
            lines.append(
                {
                    "text": f"{room['host']} is hosting live trivia",
                    "sub": f"Code {room['code']}",
                    "action": life.action("Join", f"{BASE}/rooms/{room['id']}/join"),
                }
            )
    return [
        life.card(
            APP,
            "Play",
            lines=lines,
            link="#/play" if waiting else "#/play?tab=daily",
            tone="good" if day["mine"] else "",
        )
    ]


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    open_rooms = [room for room in service.rooms(connection, me) if not room["joined"]]
    fresh = kit.iso(kit.utcnow() - timedelta(minutes=30))
    alerts = [
        {
            "id": f"play-room:{room['id']}",
            "title": f"{room['host']} started live trivia",
            "body": f"Join the family game (code {room['code']}), then open Play > Live trivia.",
            "tone": "good",
            "speak": True,
            "actions": [life.action("Join", f"{BASE}/rooms/{room['id']}/join")],
        }
        for room in open_rooms
        if room["phase"] == "lobby"
        and room["host_id"] != me["id"]
        and room["created_at"] >= fresh
    ]
    waiting, challenge_alerts = service.challenges_pulse(connection, me)
    return {
        "badge": len(open_rooms) + waiting,
        "alerts": alerts + challenge_alerts,
    }
