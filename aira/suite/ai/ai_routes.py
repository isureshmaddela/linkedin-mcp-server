"""AI routes (``/api/ai/*``): the chat and its conversations (with answers marked helpful or not), agents and
their consent and daily limits, approvals, undo and the activity log, routines, memory, and Earn; its Today cards (actions waiting for the person, today's routines) and pulse
(the badge, due routines run once and their answers shown).

Every route works for the person asking (:func:`aira.suite.life.current`). Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, life
from aira.suite.ai import agents, earn, memory, routines, service, store

APP = store.APP
BASE = "/api/ai"
ID = "([0-9a-f]{32})"
AGENT = "([a-z]{2,20})"


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    store.ensure(connection)
    me = life.current(request, connection)
    return connection, me


def _hour(connection: Any) -> int:
    return routines.local_now(connection).hour


# ---------------------------------------------------------------------------
# chat
# ---------------------------------------------------------------------------


@aira_route("POST", f"{BASE}/chat")
def chat(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.chat(
            connection, me, request.payload, hour=_hour(connection)
        )


@aira_route("GET", f"{BASE}/sessions")
def sessions_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "sessions": service.list_sessions(connection, me, q=request.q("q"))
        }


@aira_route("GET", f"{BASE}/sessions/{ID}")
def sessions_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.get_session(connection, me, request.match.group(1))


@aira_route("PATCH", f"{BASE}/sessions/{ID}")
def sessions_rename(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "session": service.rename_session(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("GET", f"{BASE}/sessions/{ID}/export")
def sessions_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        name, body = service.session_text(connection, me, request.match.group(1))
    return Raw(200, body, "text/plain; charset=utf-8", name)


@aira_route("POST", f"{BASE}/messages/{ID}/rate")
def messages_rate(request: Request) -> tuple[int, dict[str, Any]]:
    """Mark an answer helpful (``{"rating": 1}``), not helpful (``-1``) or take the mark back (``0``)."""
    connection, me = _open(request)
    with connection:
        return 200, {
            "message": service.rate(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/sessions/{ID}")
def sessions_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_session(connection, me, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# agents
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/agents")
def agents_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.list_agents(connection, me)


@aira_route("PATCH", f"{BASE}/agents/{AGENT}")
def agents_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        agent = service.configure(
            connection, me, request.match.group(1), request.payload
        )
    request.audit(
        "ai.agent_update",
        "agent",
        agent["id"],
        {
            "enabled": agent["enabled"],
            "consent": agent["consent"],
            "daily_limit": agent["daily_limit"],
        },
    )
    return 200, {"agent": agent}


# ---------------------------------------------------------------------------
# approvals and the activity log
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/ops")
def ops_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "ops": service.list_ops(
                connection,
                me,
                agent=request.q("agent"),
                status=request.q("status"),
                kind=request.q("kind"),
                q=request.q("q"),
                limit=request.qint("limit", 200, 1, 1000),
            )
        }


@aira_route("GET", f"{BASE}/approvals")
def approvals_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"ops": service.pending(connection, me)}


@aira_route("POST", f"{BASE}/ops/{ID}/approve")
def ops_approve(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        op = service.decide(connection, me, request.match.group(1), True)
    request.audit("ai.action_approve", "ai_op", op["id"])
    return 200, {"op": op}


@aira_route("POST", f"{BASE}/ops/{ID}/reject")
def ops_reject(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        op = service.decide(connection, me, request.match.group(1), False)
    request.audit("ai.action_reject", "ai_op", op["id"])
    return 200, {"op": op}


@aira_route("POST", f"{BASE}/ops/{ID}/undo")
def ops_undo(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        op = service.undo(connection, me, request.match.group(1))
    request.audit("ai.action_undo", "ai_op", op["id"])
    return 200, {"op": op}


@aira_route("GET", f"{BASE}/export/ops")
def ops_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(
            200,
            service.ops_csv(
                connection,
                me,
                agent=request.q("agent"),
                status=request.q("status"),
                kind=request.q("kind"),
                q=request.q("q"),
            ),
            "text/csv; charset=utf-8",
            "agent-activity.csv",
        )


# ---------------------------------------------------------------------------
# routines
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/routines")
def routines_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "routines": routines.list_routines(connection, me),
            "suggested": list(routines.SUGGESTED),
            "clock": routines.local_now(connection).strftime("%H:%M"),
        }


@aira_route("POST", f"{BASE}/routines")
def routines_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        routine = routines.save(connection, me, request.payload)
    request.audit("ai.routine_create", "ai_routine", routine["id"])
    return 201, {"routine": routine}


@aira_route("PATCH", f"{BASE}/routines/{ID}")
def routines_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        routine = routines.save(connection, me, request.payload, request.match.group(1))
    request.audit("ai.routine_update", "ai_routine", routine["id"])
    return 200, {"routine": routine}


@aira_route("DELETE", f"{BASE}/routines/{ID}")
def routines_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        routines.delete(connection, me, request.match.group(1))
    request.audit("ai.routine_delete", "ai_routine", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/routines/{ID}/run")
def routines_run(request: Request) -> tuple[int, dict[str, Any]]:
    """Run a routine now (``{"quick": true}`` answers from the facts without waiting for the writing model;
    ``{"seen": true}`` when the person reads the answer at once, so it does not pop up again)."""
    connection, me = _open(request)
    with connection:
        return 200, routines.run(
            connection,
            me,
            request.match.group(1),
            use_model=not kit.flag(request.payload.get("quick")),
            seen=kit.flag(request.payload.get("seen")),
        )


@aira_route("POST", f"{BASE}/routines/{ID}/seen")
def routines_seen(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"routine": routines.seen(connection, me, request.match.group(1))}


# ---------------------------------------------------------------------------
# memory
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/memories")
def memories_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "memories": memory.list_memories(connection, me, q=request.q("q")),
            "limit": memory.MAX_MEMORIES,
        }


@aira_route("POST", f"{BASE}/memories")
def memories_create(request: Request) -> tuple[int, dict[str, Any]]:
    """Remember something typed in the Memory tab (the person's own words: no consent gate needed)."""
    connection, me = _open(request)
    with connection:
        saved = memory.add(connection, me, request.payload.get("text"))
    request.audit("ai.memory_create", "ai_memory", saved["id"])
    return 201, {"memory": saved}


@aira_route("PATCH", f"{BASE}/memories/{ID}")
def memories_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        saved = memory.update(connection, me, request.match.group(1), request.payload)
    request.audit("ai.memory_update", "ai_memory", saved["id"])
    return 200, {"memory": saved}


@aira_route("DELETE", f"{BASE}/memories/{ID}")
def memories_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        memory.delete(connection, me, request.match.group(1))
    request.audit("ai.memory_delete", "ai_memory", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("DELETE", f"{BASE}/memories")
def memories_clear(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        removed = memory.clear(connection, me)
    request.audit("ai.memory_clear", "ai_memory", "", {"removed": removed})
    return 200, {"deleted": removed}


# ---------------------------------------------------------------------------
# earn
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/earn")
def earn_overview(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, earn.overview(connection, me)


@aira_route("POST", f"{BASE}/earn/goals")
def earn_goal(request: Request) -> tuple[int, dict[str, Any]]:
    """Set an earning goal from the Earn tab: EarnAgent's action, through its consent gate and into Activity."""
    connection, me = _open(request)
    with connection:
        agent = service.find_agent(me, "earn")
        action = agent.action("create_earning_goal")
        assert action is not None
        raw = request.payload
        # A wrong amount (-50, 1e6) is refused at once, not left waiting in Approvals to fail later.
        target = agents.earning_target(raw.get("target"))
        outcome = service.perform(
            connection,
            me,
            agent,
            action,
            {
                "target": target,
                "title": str(raw.get("title") or "")[:120],
                "target_date": str(raw.get("target_date") or ""),
            },
        )
    if outcome["status"] == "failed":
        raise ValueError(outcome["result"])
    return (201 if outcome["status"] == "done" else 200), {"outcome": outcome}


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    cards = _routine_card(connection, me)
    waiting = service.pending(connection, me)
    if not waiting:
        return cards
    lines = [
        {
            "text": op["summary"],
            "sub": op["agent_name"],
            "done": False,
            "action": life.action("Approve", f"{BASE}/ops/{op['id']}/approve"),
        }
        for op in waiting[:5]
    ]
    if len(waiting) > 5:
        lines.append(
            {"text": f"And {len(waiting) - 5} more in AI > Approvals", "done": False}
        )
    return [
        life.card(
            APP,
            "Waiting for your approval",
            lines=lines,
            tone="warn",
            link="#/ai?tab=approvals",
        ),
        *cards,
    ]


def _routine_card(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    """Today's routines: the ones that ran (open the answer) and the ones still to come (run now)."""
    due = routines.today_runs(connection, me)
    if not due:
        return []
    lines = []
    for routine in due[:6]:
        if routine["ran_today"]:
            first = next(
                (line for line in routine["last_answer"].splitlines() if line.strip()),
                "",
            )
            lines.append(
                {"text": routine["title"], "sub": first[:140] or "Ready", "done": True}
            )
        else:
            lines.append(
                {
                    "text": routine["title"],
                    "sub": f"At {routine['at']}",
                    "done": False,
                    "action": life.action(
                        "Run now",
                        f"{BASE}/routines/{routine['id']}/run",
                        body={"quick": True},
                    ),
                }
            )
    ran = next((r for r in due if r["ran_today"] and r["session_id"]), None)
    link = f"#/ai?tab=chat&session={ran['session_id']}" if ran else "#/ai?tab=routines"
    return [life.card(APP, "Aira's routines today", lines=lines, link=link)]


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    try:
        routines.run_due(connection, me)
        alerts = routines.alerts(connection, me)
    except Exception:  # noqa: BLE001 - routines must never stop the pulse
        connection.rollback()
        kit.suite.logger.exception("AI routines failed in the pulse")
        alerts = []
    return {
        "badge": kit.count(
            connection,
            "SELECT COUNT(*) FROM ai_ops WHERE owner_id = ? AND status = 'pending'",
            (me["id"],),
        ),
        "alerts": alerts,
    }
