"""The master agent's chat, each person's agent settings, the consent rules, approvals and the activity log.

A question goes to the agents whose words it uses (all of them for "morning brief" or when none match). Each
agent's facts go to the writing model with the conversation so far, and the model answers in JSON:
``{"reply": "...", "actions": [{"agent", "action", "args"}]}``. Without a model the answer is built from the facts.
Plain commands ("add milk to the shopping list", "add a goal: run a 5K by 2026-12-31") are understood without a
model too. Every action passes the consent gate of its agent:

* switched off or ``read_only``: not done (``blocked``);
* ``auto_minor``: minor actions run, major ones wait (``pending``);
* ``auto_major``: everything runs;
* ``manual_approval``: everything waits for the person in Approvals.

A done action whose agent knows how to take it back (``Action.undo``) can be undone from the chat or Activity.
"""

from __future__ import annotations

import re
import sqlite3
import time
from datetime import timedelta
from typing import Any

from aira import suite
from aira.suite import kit, life
from aira.suite.ai import agents as registry
from aira.suite.ai import memory

OP_STATUSES = ("done", "failed", "blocked", "pending", "rejected", "undone")
MAX_MESSAGE = 2000
MAX_SESSIONS = 200
HISTORY = 6  # earlier messages the model sees
MAX_DAILY_LIMIT = 1000
LIMIT_WINDOW = timedelta(hours=24)
CLAIM_TIMEOUT = timedelta(
    minutes=5
)  # an approval that was claimed but never finished may be tried again
BRIEF_WORDS = (
    "brief",
    "briefing",
    "summary",
    "summarise",
    "summarize",
    "overview",
    "everything",
    "my day",
    "what's up",
    "whats up",
    "how am i doing",
    "catch me up",
)
# Words that name a time, not an app: an agent that only matches one of these is asked only when no other matches
# ("Which medicines do I take today?" goes to HealthAgent; "What do I need to do today?" to PlannerAgent).
GENERIC_WORDS = frozenset({"today", "tomorrow", "list", "calendar", "plan", "schedule"})
LIKE_ESCAPE = str.maketrans({"\\": "\\\\", "%": "\\%", "_": "\\_"})


def _like(words: str) -> str:
    return "%" + words.translate(LIKE_ESCAPE) + "%"


# ---------------------------------------------------------------------------
# agent settings and stats
# ---------------------------------------------------------------------------


def _settings(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> dict[str, dict[str, Any]]:
    return {
        row["agent"]: row
        for row in kit.fetch_all(
            connection, "SELECT * FROM ai_agents WHERE owner_id = ?", (me["id"],)
        )
    }


def setting(
    connection: sqlite3.Connection, me: dict[str, Any], agent: registry.Agent
) -> dict[str, Any]:
    return _current(_settings(connection, me).get(agent.id), agent)


def _current(row: dict[str, Any] | None, agent: registry.Agent) -> dict[str, Any]:
    """An agent's settings for this person: their own, or the agent's defaults."""
    if row is None:
        return {
            "enabled": True,
            "consent": agent.default_consent,
            "daily_limit": int(agent.default_limit),
            "limit_set": False,
        }
    # Until the person chooses a limit themselves, the agent's own default applies (also one added later).
    limit_set = bool(row.get("limit_set"))
    return {
        "enabled": bool(row["enabled"]),
        "consent": row["consent"],
        "daily_limit": int(row.get("daily_limit") or 0)
        if limit_set
        else int(agent.default_limit),
        "limit_set": limit_set,
    }


def alone_today(
    connection: sqlite3.Connection, me: dict[str, Any], agent: registry.Agent
) -> int:
    """Actions the agent took alone (not approved by the person) in the last 24 hours."""
    return kit.count(
        connection,
        """SELECT COUNT(*) FROM ai_ops WHERE owner_id = ? AND agent = ? AND kind = 'action'
        AND status IN ('done', 'failed', 'undone') AND alone = 1 AND created_at >= ?""",
        (me["id"], agent.id, kit.iso(kit.utcnow() - LIMIT_WINDOW)),
    )


def _stats(
    connection: sqlite3.Connection, me: dict[str, Any]
) -> dict[str, dict[str, int]]:
    week = kit.iso(kit.utcnow() - timedelta(days=7))
    out: dict[str, dict[str, int]] = {}
    for row in kit.fetch_all(
        connection,
        """SELECT agent, status, COUNT(*) AS n, SUM(CASE WHEN created_at >= ? THEN 1 ELSE 0 END) AS week
        FROM ai_ops WHERE owner_id = ? GROUP BY agent, status""",
        (week, me["id"]),
    ):
        entry = out.setdefault(
            row["agent"], {"ops": 0, "week": 0, "done": 0, "failed": 0, "pending": 0}
        )
        entry["ops"] += int(row["n"])
        entry["week"] += int(row["week"] or 0)
        if row["status"] in ("done", "failed", "pending"):
            entry[row["status"]] += int(row["n"])
    return out


def _rate(done: int, failed: int) -> float | None:
    return round(100 * done / (done + failed), 1) if done + failed else None


def agent_view(
    agent: registry.Agent, current: dict[str, Any], stats: dict[str, int] | None
) -> dict[str, Any]:
    stats = stats or {"ops": 0, "week": 0, "done": 0, "failed": 0, "pending": 0}
    return {
        "id": agent.id,
        "name": agent.name,
        "app": agent.app or "hub",
        "summary": agent.summary,
        "enabled": current["enabled"],
        "consent": current["consent"],
        "default_consent": agent.default_consent,
        "daily_limit": int(current.get("daily_limit") or 0),
        "default_limit": int(agent.default_limit),
        "ops": stats["ops"],
        "ops_week": stats["week"],
        "pending": stats["pending"],
        "success_rate": _rate(stats["done"], stats["failed"]),
        "actions": [
            {"name": a.name, "label": a.label, "risk": a.risk} for a in agent.actions
        ],
    }


def list_agents(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    settings = _settings(connection, me)
    stats = _stats(connection, me)
    views = []
    for agent in registry.available(me):
        current = _current(settings.get(agent.id), agent)
        views.append(agent_view(agent, current, stats.get(agent.id)))
    rated = ratings(connection, me)
    done = sum(s["done"] for s in stats.values())
    failed = sum(s["failed"] for s in stats.values())
    consents = [v["consent"] for v in views if v["enabled"]]
    common = max(set(consents), key=consents.count) if consents else ""
    return {
        "agents": views,
        "kpis": {
            "active": sum(1 for v in views if v["enabled"]),
            "total": len(views),
            "ops": sum(s["ops"] for s in stats.values()),
            "ops_week": sum(s["week"] for s in stats.values()),
            "success_rate": _rate(done, failed),
            "common_consent": common,
            "pending": kit.count(
                connection,
                "SELECT COUNT(*) FROM ai_ops WHERE owner_id = ? AND status = 'pending'",
                (me["id"],),
            ),
            "rated": rated["rated"],
            "helpful_percent": rated["helpful_percent"],
        },
        "consent_levels": [
            {"id": level, **registry.CONSENT_INFO[level]}
            for level in registry.CONSENT_LEVELS
        ],
        "recent": list_ops(connection, me, limit=8),
    }


def find_agent(me: dict[str, Any], agent_id: str) -> registry.Agent:
    """An agent this person may use (LookupError, a 404, otherwise)."""
    return _agent(me, agent_id)


def _agent(me: dict[str, Any], agent_id: str) -> registry.Agent:
    agent = registry.BY_ID.get(agent_id)
    if agent is None or agent not in registry.available(me):
        raise LookupError("That agent was not found.")
    return agent


def configure(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    agent_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    agent = _agent(me, agent_id)
    current = setting(connection, me, agent)
    enabled = kit.flag(raw["enabled"]) if "enabled" in raw else current["enabled"]
    consent = (
        kit.choice(raw.get("consent"), "the consent level", registry.CONSENT_LEVELS)
        if "consent" in raw
        else current["consent"]
    )
    limit_set = "daily_limit" in raw or current["limit_set"]
    daily_limit = (
        kit.integer(
            raw.get("daily_limit"),
            "the most actions a day",
            default=0,
            minimum=0,
            maximum=MAX_DAILY_LIMIT,
        )
        if "daily_limit" in raw
        else current["daily_limit"]
    )
    # The switch or the consent level alone does not fix the limit: the agent's default keeps applying.
    connection.execute(
        """INSERT INTO ai_agents (owner_id, agent, enabled, consent, daily_limit, limit_set, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(owner_id, agent) DO UPDATE SET enabled = excluded.enabled,
        consent = excluded.consent, daily_limit = excluded.daily_limit, limit_set = excluded.limit_set,
        updated_at = excluded.updated_at""",
        (
            me["id"],
            agent.id,
            1 if enabled else 0,
            consent,
            daily_limit if limit_set else 0,
            1 if limit_set else 0,
            kit.now(),
        ),
    )
    connection.commit()
    return agent_view(
        agent,
        {"enabled": enabled, "consent": consent, "daily_limit": daily_limit},
        _stats(connection, me).get(agent.id),
    )


# ---------------------------------------------------------------------------
# operations (the activity log) and approvals
# ---------------------------------------------------------------------------


def _record(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    agent: registry.Agent,
    *,
    kind: str,
    summary: str,
    status: str,
    action: registry.Action | None = None,
    args: dict[str, Any] | None = None,
    result: str = "",
    session_id: str = "",
    undo: dict[str, Any] | None = None,
    alone: bool = False,
) -> str:
    op_id = kit.new_id()
    stamp = kit.now()
    connection.execute(
        """INSERT INTO ai_ops (id, owner_id, agent, kind, action, risk, summary, args, status, result, session_id,
        created_at, decided_at, undo, alone) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            op_id,
            me["id"],
            agent.id,
            kind,
            action.name if action else "",
            action.risk if action else "",
            summary[:300],
            kit.dumps(args or {}),
            status,
            result[:500],
            session_id,
            stamp,
            stamp if status in ("done", "failed", "blocked") else None,
            kit.dumps(undo) if undo else "",
            1 if alone else 0,
        ),
    )
    return op_id


def _undoable(row: dict[str, Any], agent: registry.Agent | None) -> bool:
    if row["status"] != "done" or not row.get("undo") or agent is None:
        return False
    action = agent.action(row["action"])
    return action is not None and action.undo is not None


def _op_view(row: dict[str, Any]) -> dict[str, Any]:
    agent = registry.BY_ID.get(row["agent"])
    return {
        "id": row["id"],
        "agent": row["agent"],
        "agent_name": agent.name if agent else row["agent"],
        "kind": row["kind"],
        "action": row["action"],
        "risk": row["risk"],
        "summary": row["summary"],
        "status": row["status"],
        "result": row["result"],
        "session_id": row["session_id"],
        "created_at": row["created_at"],
        "decided_at": row["decided_at"],
        "undone_at": row.get("undone_at"),
        "can_undo": _undoable(row, agent),
    }


def list_ops(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    agent: str = "",
    status: str = "",
    kind: str = "",
    q: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    sql = "SELECT * FROM ai_ops WHERE owner_id = ?"
    params: list[Any] = [me["id"]]
    if agent:
        if agent not in registry.BY_ID:
            raise ValueError("That agent does not exist.")
        sql += " AND agent = ?"
        params.append(agent)
    if status:
        sql += " AND status = ?"
        params.append(kit.choice(status, "the status", OP_STATUSES))
    if kind:
        sql += " AND kind = ?"
        params.append(kit.choice(kind, "the kind", ("read", "action")))
    words = " ".join(str(q or "").split())[:100]
    if words:
        sql += " AND (summary LIKE ? ESCAPE '\\' OR result LIKE ? ESCAPE '\\')"
        params += [_like(words), _like(words)]
    sql += " ORDER BY created_at DESC, rowid DESC LIMIT ?"
    params.append(max(1, min(int(limit), 1000)))
    return [_op_view(row) for row in kit.fetch_all(connection, sql, params)]


def ops_csv(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    *,
    agent: str = "",
    status: str = "",
    kind: str = "",
    q: str = "",
) -> bytes:
    """The activity log as a spreadsheet, with the same filters as the Activity tab."""
    rows = list_ops(
        connection, me, agent=agent, status=status, kind=kind, q=q, limit=1000
    )
    return kit.csv_bytes(
        rows,
        [
            "created_at",
            "agent_name",
            "kind",
            "action",
            "risk",
            "summary",
            "status",
            "result",
            "decided_at",
        ],
    )


def _op_row(
    connection: sqlite3.Connection, me: dict[str, Any], op_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM ai_ops WHERE id = ? AND owner_id = ?",
        (op_id, me["id"]),
    )
    if not found:
        raise LookupError("That action was not found.")
    return found


def _run(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    action: registry.Action,
    args: dict[str, Any],
) -> tuple[str, str, dict[str, Any]]:
    """Run an action; (status, sentence, what its undo needs). A failed action is rolled back and says why."""
    try:
        outcome = action.run(connection, me, args)
    except (ValueError, LookupError, PermissionError) as error:
        connection.rollback()
        return "failed", str(error), {}
    except Exception:  # noqa: BLE001 - the chat answers even when an app breaks
        connection.rollback()
        kit.suite.logger.exception("Agent action %s failed", action.name)
        return "failed", "Something went wrong in the app. Try it there.", {}
    if isinstance(outcome, dict):
        undo = outcome.get("undo")
        return (
            "done",
            str(outcome.get("message") or "Done."),
            undo if isinstance(undo, dict) and action.undo is not None else {},
        )
    return "done", str(outcome or "Done."), {}


def decide(
    connection: sqlite3.Connection, me: dict[str, Any], op_id: str, approve: bool
) -> dict[str, Any]:
    """Approve (run now) or reject an action that waits for the person.

    Asking again for what already happened (a second tap on Today) answers with the action as it is."""
    op = _op_row(connection, me, op_id)
    if op["status"] != "pending":
        if (approve and op["status"] in ("done", "failed", "undone")) or (
            not approve and op["status"] == "rejected"
        ):
            return _op_view(op)
        raise ValueError("This action was already decided.")
    agent = registry.BY_ID.get(op["agent"])
    action = agent.action(op["action"]) if agent else None
    stamp = kit.now()
    if not approve:
        kit.update_row(
            connection,
            "ai_ops",
            op_id,
            {"status": "rejected", "result": "You said no.", "decided_at": stamp},
        )
        connection.commit()
        return _op_view(_op_row(connection, me, op_id))
    if agent is None or action is None or agent not in registry.available(me):
        raise ValueError("This agent is no longer available.")
    if not setting(connection, me, agent)["enabled"]:
        raise ValueError(f"{agent.name} is switched off. Switch it on to run this.")
    # Claim it first: a second tap (Today and Approvals at once) must not run the action twice.
    claimed = connection.execute(
        """UPDATE ai_ops SET decided_at = ? WHERE id = ? AND owner_id = ? AND status = 'pending'
        AND (decided_at IS NULL OR decided_at < ?)""",
        (stamp, op_id, me["id"], kit.iso(kit.utcnow() - CLAIM_TIMEOUT)),
    ).rowcount
    connection.commit()
    if not claimed:
        return _op_view(_op_row(connection, me, op_id))
    status, result, undo_data = _run(connection, me, action, kit.loads(op["args"], {}))
    kit.update_row(
        connection,
        "ai_ops",
        op_id,
        {
            "status": status,
            "result": result,
            "decided_at": stamp,
            "undo": kit.dumps(undo_data) if undo_data else "",
        },
    )
    connection.commit()
    return _op_view(_op_row(connection, me, op_id))


def undo(
    connection: sqlite3.Connection, me: dict[str, Any], op_id: str
) -> dict[str, Any]:
    """Take back a done action (remove the goal, the task, the shopping items it added)."""
    op = _op_row(connection, me, op_id)
    if op["status"] == "undone":
        return _op_view(op)
    agent = registry.BY_ID.get(op["agent"])
    action = agent.action(op["action"]) if agent else None
    if (
        agent is None
        or action is None
        or action.undo is None
        or not _undoable(op, agent)
    ):
        raise ValueError("This action cannot be undone here. Change it in its app.")
    if agent not in registry.available(me):
        raise ValueError("This agent is no longer available.")
    try:
        sentence = action.undo(connection, me, kit.loads(op["undo"], {}))
    except (ValueError, PermissionError) as error:
        connection.rollback()
        raise ValueError(str(error)) from error
    except Exception as error:  # noqa: BLE001 - say so rather than fail the page
        connection.rollback()
        kit.suite.logger.exception("Undoing %s failed", action.name)
        raise ValueError("Monosage could not undo this. Change it in its app.") from error
    kit.update_row(
        connection,
        "ai_ops",
        op_id,
        {"status": "undone", "result": str(sentence)[:500], "undone_at": kit.now()},
    )
    connection.commit()
    return _op_view(_op_row(connection, me, op_id))


def gate(current: dict[str, Any], action: registry.Action, used: int = 0) -> str:
    """What the consent level allows for this action: ``run``, ``ask`` or ``blocked``.

    ``used``: actions the agent already took alone in the last 24 hours; at its daily limit it asks."""
    if not current["enabled"] or current["consent"] == "read_only":
        return "blocked"
    limit = int(current.get("daily_limit") or 0)
    if limit and used >= limit:
        return "ask"
    if current["consent"] == "auto_major":
        return "run"
    if current["consent"] == "auto_minor" and action.risk == "minor":
        return "run"
    return "ask"


def perform(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    agent: registry.Agent,
    action: registry.Action,
    args: dict[str, Any],
    session_id: str = "",
    *,
    proposed: bool = False,
) -> dict[str, Any]:
    """Pass one action through the consent gate and log it; returns what happened.

    ``proposed``: the writing model suggested it (the person did not type it as a command). An action that is
    ``typed_only`` (remembering something) then waits for the person whatever the consent level."""
    current = setting(connection, me, agent)
    used = alone_today(connection, me, agent) if current["daily_limit"] else 0
    decision = gate(current, action, used)
    held = proposed and action.typed_only and decision == "run"
    if held:
        decision = "ask"
    try:
        summary = str(action.describe(args) or action.label)
    except Exception:  # noqa: BLE001 - odd arguments from the model still get a name
        summary = action.label
    if decision == "blocked":
        reason = (
            f"{agent.name} is switched off."
            if not current["enabled"]
            else f"{agent.name} may only read. Change its consent level in Agents to let it act."
        )
        op_id = _record(
            connection,
            me,
            agent,
            kind="action",
            summary=summary,
            status="blocked",
            action=action,
            args=args,
            result=reason,
            session_id=session_id,
        )
        connection.commit()
        return {
            "id": op_id,
            "agent": agent.id,
            "agent_name": agent.name,
            "summary": summary,
            "status": "blocked",
            "result": reason,
            "can_undo": False,
        }
    if decision == "ask":
        reason = "Waiting for your approval."
        if held:
            reason = (
                "Waiting for your approval: you did not ask for this. Monosage only changes what it remembers when "
                "you say so."
            )
        elif gate(current, action) == "run":
            limit = current["daily_limit"]
            reason = (
                f"Waiting for your approval: {agent.name} already took {limit} "
                f"{'action' if limit == 1 else 'actions'} alone in the last 24 hours (its limit)."
            )
        op_id = _record(
            connection,
            me,
            agent,
            kind="action",
            summary=summary,
            status="pending",
            action=action,
            args=args,
            result=reason,
            session_id=session_id,
        )
        connection.commit()
        return {
            "id": op_id,
            "agent": agent.id,
            "agent_name": agent.name,
            "summary": summary,
            "status": "pending",
            "result": reason,
            "can_undo": False,
        }
    status, result, undo_data = _run(connection, me, action, args)
    op_id = _record(
        connection,
        me,
        agent,
        kind="action",
        summary=summary,
        status=status,
        action=action,
        args=args,
        result=result,
        session_id=session_id,
        undo=undo_data,
        alone=True,
    )
    connection.commit()
    return {
        "id": op_id,
        "agent": agent.id,
        "agent_name": agent.name,
        "summary": summary,
        "status": status,
        "result": result,
        "can_undo": bool(undo_data),
    }


def pending(connection: sqlite3.Connection, me: dict[str, Any]) -> list[dict[str, Any]]:
    return list_ops(connection, me, status="pending", limit=200)


# ---------------------------------------------------------------------------
# understanding a message
# ---------------------------------------------------------------------------

_WORD = re.compile(r"[a-z][a-z'-]+")
BUILTIN_IDS = frozenset(agent.id for agent in registry.AGENTS)


def route(
    message: str, candidates: list[registry.Agent]
) -> tuple[list[registry.Agent], bool]:
    """The agents a message is about, and whether it asks for a summary of everything."""
    text = message.lower()
    everyone = [agent for agent in candidates if agent.brief]
    if any(word in text for word in BRIEF_WORDS):
        return everyone, True
    words = set(_WORD.findall(text))

    def matches(agent: registry.Agent) -> set[str]:
        return {
            k
            for k in agent.keywords
            if ((k in words) if " " not in k and "-" not in k else (k in text))
        }

    found = {agent.id: matches(agent) for agent in candidates}
    hits = [agent for agent in candidates if found[agent.id]]
    specific = [agent for agent in hits if found[agent.id] - GENERIC_WORDS]
    if specific:
        hits = specific
    return (hits or everyone), not hits


_DATE_TAIL = r"(?:\s+(?:(?:by|on|for|before)\s+)?(today|tomorrow|next week|in \d{1,3} (?:days?|weeks?|months?|years?)|\d{4}-\d{2}-\d{2}))?"
_TIME_TAIL = r"(?:\s+at\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?)?"


def _clock(hour: str | None, minute: str | None, half: str | None) -> str:
    if not hour:
        return ""
    h = int(hour) % 24
    if half == "pm" and h < 12:
        h += 12
    if half == "am" and h == 12:
        h = 0
    return f"{h:02d}:{int(minute or 0):02d}"


def parse_commands(
    message: str, agents: list[registry.Agent] | None = None
) -> list[dict[str, Any]]:
    """Plain commands understood without a model: [{"agent", "action", "args"}].

    Aira's own commands come first; then each of ``agents`` with a ``parser`` (an app's own agent) may claim the
    message."""
    # "Buy 2 Apple shares" is for the Stocks agent, not the shopping list: a bare "buy ..." is only a shopping
    # item when no app's own agent knows a word of it.
    words = set(_WORD.findall(message.lower()))
    loose = any(
        k in words
        for agent in agents or []
        if agent.id not in BUILTIN_IDS
        for k in agent.keywords
        if k not in GENERIC_WORDS
    )
    found = _builtin_commands(message, loose)
    if found:
        return found
    for agent in agents or []:
        if agent.parser is None:
            continue
        try:
            claimed = agent.parser(message) or []
        except Exception:  # noqa: BLE001 - an app's parser must not break the chat
            kit.suite.logger.exception(
                "The %s agent could not read a command", agent.id
            )
            continue
        out = [
            {
                "agent": agent.id,
                "action": item.get("action"),
                "args": item.get("args") or {},
            }
            for item in claimed[:5]
            if isinstance(item, dict)
        ]
        if out:
            return out
    return []


_MONEY = r"(?:rs\.?\s*|inr\s*|₹\s*|\$\s*|€\s*|£\s*)?(\d[\d,]*(?:\.\d{1,2})?)(k)?"


def _builtin_commands(message: str, loose: bool = False) -> list[dict[str, Any]]:
    """Aira's own commands. ``loose``: the message is about another app (shares, tickets), so a bare "buy ..." is
    not a shopping list item."""
    text = " ".join(message.strip().rstrip(".!").split())
    lower = text.lower()
    found = _memory_command(text, lower)
    if found:
        return found
    match = re.fullmatch(
        r"(?:please\s+)?(?:add|set|create|start|new)\s+(?:an?\s+|my\s+)?(?:new\s+)?(?:earning|earnings|income)\s+goal"
        r"(?:\s+(?:of|to))?\s*:?\s+(?:earn\s+)?"
        + _MONEY
        + r"(?:\s+(?:a|per|this|each)\s+(?:month|year))?"
        + _DATE_TAIL,
        lower,
    )
    if match:
        amount = match.group(1).replace(",", "")
        if match.group(2):
            amount = str(round(float(amount) * 1000))
        return [
            {
                "agent": "earn",
                "action": "create_earning_goal",
                "args": {"target": amount, "target_date": match.group(3) or ""},
            }
        ]
    match = re.fullmatch(
        r"(?:please\s+)?(?:add|create|set|start|new)\s+(?:a\s+|my\s+)?(?:new\s+)?goal(?:\s+to)?\s*:?\s+(.+?)"
        + _DATE_TAIL,
        lower,
    )
    if match:
        title = text[match.start(1) : match.end(1)]
        return [
            {
                "agent": "life",
                "action": "create_goal",
                "args": {"title": title, "target_date": match.group(2) or ""},
            }
        ]
    match = re.fullmatch(
        r"(?:please\s+)?(?:log|update|set|mark)\s+(?:my\s+)?(?:progress\s+(?:on|for)\s+)?(?:goal\s+)?(.+?)\s+(?:to|at|as)\s+(\d{1,3})\s*%(?:\s+done)?",
        lower,
    )
    if match:
        return [
            {
                "agent": "life",
                "action": "log_progress",
                "args": {
                    "goal": text[match.start(1) : match.end(1)],
                    "progress": int(match.group(2)),
                },
            }
        ]
    match = re.fullmatch(
        r"(?:please\s+)?(?:add|record|log)\s+(?:an?\s+)?(?:life\s+)?(?:event|milestone)\s*:?\s+(.+?)"
        + _DATE_TAIL,
        lower,
    )
    if match:
        return [
            {
                "agent": "life",
                "action": "add_life_event",
                "args": {
                    "title": text[match.start(1) : match.end(1)],
                    "date": match.group(2) or "today",
                },
            }
        ]
    match = re.fullmatch(
        r"(?:please\s+)?(?:add|put)\s+(.+?)\s+(?:to|on)\s+(?:the\s+|my\s+)?(?:shopping|grocery|groceries)(?:\s+list)?",
        lower,
    ) or (
        None
        if loose
        else re.fullmatch(
            r"(?:please\s+)?(?:we need to buy|we need|i need to buy|buy)\s+(.+)", lower
        )
    )
    if match:
        raw = text[match.start(1) : match.end(1)]
        items = [part.strip() for part in re.split(r",|\band\b", raw) if part.strip()]
        return [
            {
                "agent": "planner",
                "action": "add_shopping_item",
                "args": {"items": items},
            }
        ]
    match = re.fullmatch(
        r"(?:please\s+)?(?:remind me to|remember to|don'?t forget to|do not forget to|add (?:a\s+)?(?:task|chore|to-?do)(?:\s+to)?\s*:?)\s+(.+?)"
        + _DATE_TAIL
        + _TIME_TAIL,
        lower,
    )
    if match:
        title = text[match.start(1) : match.end(1)]
        return [
            {
                "agent": "planner",
                "action": "add_task",
                "args": {
                    "title": title,
                    "due_date": match.group(2) or "today",
                    "due_time": _clock(match.group(3), match.group(4), match.group(5)),
                },
            }
        ]
    match = re.fullmatch(
        r"(?:please\s+)?(?:note|add (?:a\s+)?note|write (?:a\s+)?note|save (?:a\s+)?note)\s*:?\s+(.+)",
        lower,
    )
    if match:
        body = text[match.start(1) : match.end(1)]
        return [
            {
                "agent": "planner",
                "action": "add_note",
                "args": {"title": body[:60], "body": body},
            }
        ]
    return []


# "Remember what I told you about my diet" asks about a memory; it is not one.
_ASKING = re.compile(r"(?:what|when|where|who|whom|whose|why|how|which|if|whether)\b")
# "Don't forget my anniversary is on 12 May" is about the person; "Don't forget the milk" is a reminder.
_ABOUT_ME = re.compile(r"(?:i|i'm|im|i've|i'd|i'll|my|me|we|we're|our|us|mine)\b")
_FORGET_ALL = re.compile(
    r"(?:please\s+)?(?:forget|clear|delete|erase|wipe|remove)\s+(?:all|everything)"
    r"(?:\s+(?:of\s+)?(?:my|your|the)\s+memor(?:y|ies))?"
    r"(?:\s+(?:you\s+)?(?:know|remember)\s+about\s+me|\s+about\s+me)?"
    r"|(?:please\s+)?(?:clear|delete|erase|wipe|empty|reset)\s+(?:all\s+)?(?:of\s+)?(?:my|your|the)\s+memor(?:y|ies)"
)


def _memory_command(text: str, lower: str) -> list[dict[str, Any]]:
    """ "Remember (that) I am vegetarian" and "Forget (that) I am vegetarian" ("remember to ..." is a task).

    "Forget everything" forgets every memory (it waits for a yes unless MemoryAgent may take big actions alone).
    "Don't forget the milk" (not about the person) is a reminder: a task, like "don't forget to ..."."""
    if _FORGET_ALL.fullmatch(lower):
        return [{"agent": "memory", "action": "forget_all", "args": {}}]
    match = re.fullmatch(
        r"(?:please\s+)?(remember|don'?t forget|do not forget|keep in mind)(?!\s+to\s)"
        r"(\s+(?:that|this))?(\s*:)?\s+(.+)",
        lower,
    )
    if match and not text.endswith("?"):
        rest = match.group(4)
        said_that = bool(match.group(2) or match.group(3))
        if not said_that and _ASKING.match(rest):
            return []
        if "forget" in match.group(1) and not said_that and not _ABOUT_ME.match(rest):
            return [
                {
                    "agent": "planner",
                    "action": "add_task",
                    "args": {"title": text, "due_date": "today", "due_time": ""},
                }
            ]
        return [
            {
                "agent": "memory",
                "action": "remember",
                "args": {"text": text[match.start(4) : match.end(4)]},
            }
        ]
    match = re.fullmatch(
        r"(?:please\s+)?forget(?:\s+(?:that|about|what i said about))?\s*:?\s+(.+)",
        lower,
    )
    if match and match.group(1) not in {"it", "that", "this", "everything"}:
        return [
            {
                "agent": "memory",
                "action": "forget",
                "args": {"text": text[match.start(1) : match.end(1)]},
            }
        ]
    return []


# ---------------------------------------------------------------------------
# sessions
# ---------------------------------------------------------------------------


def _session_row(
    connection: sqlite3.Connection, me: dict[str, Any], session_id: str
) -> dict[str, Any]:
    found = kit.fetch_one(
        connection,
        "SELECT * FROM ai_sessions WHERE id = ? AND owner_id = ?",
        (session_id, me["id"]),
    )
    if not found:
        raise LookupError("That conversation was not found.")
    return found


def _message_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "role": row["role"],
        "content": row["content"],
        "agent": row["agent"],
        "agents": kit.loads(row["agents"], []),
        "tokens": int(row["tokens"]),
        "duration_ms": int(row["duration_ms"]),
        "source": row["source"],
        "actions": kit.loads(row["actions"], []),
        "created_at": row["created_at"],
        # The answer without the lines that say what its actions did (the action cards say that, up to date).
        # (A plain command's answer has no words of its own; answers saved before this was kept show it all.)
        "reply": row["reply"]
        if row.get("reply") or row["source"] == "command"
        else row["content"],
        "rating": int(row.get("rating") or 0),
    }


def _session_stats(connection: sqlite3.Connection, session_id: str) -> dict[str, Any]:
    row = (
        kit.fetch_one(
            connection,
            """SELECT COALESCE(SUM(tokens), 0) AS tokens, AVG(duration_ms) AS avg_ms, COUNT(*) AS answers
        FROM ai_messages WHERE session_id = ? AND role = 'assistant'""",
            (session_id,),
        )
        or {}
    )
    last = (
        kit.fetch_one(
            connection,
            "SELECT agent, source FROM ai_messages WHERE session_id = ? AND role = 'assistant' ORDER BY created_at DESC, rowid DESC LIMIT 1",
            (session_id,),
        )
        or {}
    )
    return {
        "tokens": int(row.get("tokens") or 0),
        "avg_ms": round(row["avg_ms"]) if row.get("avg_ms") is not None else None,
        "answers": int(row.get("answers") or 0),
        "agent": last.get("agent") or "",
        "source": last.get("source") or "",
    }


def list_sessions(
    connection: sqlite3.Connection, me: dict[str, Any], q: str = ""
) -> list[dict[str, Any]]:
    """The person's conversations, newest first; ``q`` finds words in their names and messages."""
    sql = "SELECT * FROM ai_sessions s WHERE owner_id = ?"
    params: list[Any] = [me["id"]]
    words = " ".join(str(q or "").split())[:100]
    if words:
        sql += """ AND (title LIKE ? ESCAPE '\\' OR EXISTS (SELECT 1 FROM ai_messages m
            WHERE m.session_id = s.id AND m.content LIKE ? ESCAPE '\\'))"""
        params += [_like(words), _like(words)]
    sql += " ORDER BY updated_at DESC LIMIT 100"
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "updated_at": row["updated_at"],
            "created_at": row["created_at"],
        }
        for row in kit.fetch_all(connection, sql, params)
    ]


def rename_session(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    session_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    _session_row(connection, me, session_id)
    title = kit.text(raw.get("title"), "a name", max_len=80, required=True)
    connection.execute(
        "UPDATE ai_sessions SET title = ? WHERE id = ?", (title, session_id)
    )
    connection.commit()
    return {"id": session_id, "title": title}


def session_text(
    connection: sqlite3.Connection, me: dict[str, Any], session_id: str
) -> tuple[str, bytes]:
    """A conversation as plain text to keep or share: (file name, bytes)."""
    data = get_session(connection, me, session_id)
    title = data["session"]["title"]
    lines = [title, "=" * min(80, max(3, len(title))), ""]
    for message in data["messages"]:
        who = "You" if message["role"] == "user" else (message["agent"] or "Monosage")
        when = message["created_at"][:16].replace("T", " ")
        lines += [f"{who} ({when} UTC):", message["content"]]
        for action in message["actions"]:
            lines.append(f"  - {action.get('summary', '')}: {action.get('status', '')}")
        lines.append("")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40]
    return f"aira-{slug or 'conversation'}.txt", "\n".join(lines).encode("utf-8")


def get_session(
    connection: sqlite3.Connection, me: dict[str, Any], session_id: str
) -> dict[str, Any]:
    session = _session_row(connection, me, session_id)
    messages = [
        _message_view(row)
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM ai_messages WHERE session_id = ? ORDER BY created_at, rowid",
            (session_id,),
        )
    ]
    pending_ids = {op["id"]: op for op in pending(connection, me)}
    decided = {
        row["id"]: row
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM ai_ops WHERE session_id = ? AND owner_id = ?",
            (session_id, me["id"]),
        )
    }
    for message in messages:
        for action in message["actions"]:
            now = decided.get(action.get("id", ""))
            if now:
                action["status"], action["result"] = now["status"], now["result"]
            action["can_undo"] = bool(
                now and _undoable(now, registry.BY_ID.get(now["agent"]))
            )
            action["can_decide"] = action.get("id") in pending_ids
    return {
        "session": {
            "id": session["id"],
            "title": session["title"],
            "created_at": session["created_at"],
        },
        "messages": messages,
        "stats": _session_stats(connection, session_id),
    }


def delete_session(
    connection: sqlite3.Connection, me: dict[str, Any], session_id: str
) -> None:
    _session_row(connection, me, session_id)
    connection.execute("DELETE FROM ai_messages WHERE session_id = ?", (session_id,))
    connection.execute("DELETE FROM ai_sessions WHERE id = ?", (session_id,))
    connection.commit()


def new_session(
    connection: sqlite3.Connection, me: dict[str, Any], message: str
) -> str:
    if (
        kit.count(
            connection,
            "SELECT COUNT(*) FROM ai_sessions WHERE owner_id = ?",
            (me["id"],),
        )
        >= MAX_SESSIONS
    ):
        oldest = kit.fetch_one(
            connection,
            "SELECT id FROM ai_sessions WHERE owner_id = ? ORDER BY updated_at LIMIT 1",
            (me["id"],),
        )
        if oldest:
            connection.execute(
                "DELETE FROM ai_messages WHERE session_id = ?", (oldest["id"],)
            )
            connection.execute("DELETE FROM ai_sessions WHERE id = ?", (oldest["id"],))
    session_id = kit.new_id()
    stamp = kit.now()
    title = " ".join(message.split())[:60] or "New conversation"
    connection.execute(
        "INSERT INTO ai_sessions (id, owner_id, title, created_at, updated_at) VALUES (?, ?, ?, ?, ?)",
        (session_id, me["id"], title, stamp, stamp),
    )
    return session_id


def _save_message(
    connection: sqlite3.Connection,
    session_id: str,
    role: str,
    content: str,
    **extra: Any,
) -> dict[str, Any]:
    message_id = kit.new_id()
    row = {
        "id": message_id,
        "session_id": session_id,
        "role": role,
        "content": content,
        "agent": extra.get("agent", ""),
        "agents": kit.dumps(extra.get("agents", [])),
        "tokens": int(extra.get("tokens", 0)),
        "duration_ms": int(extra.get("duration_ms", 0)),
        "source": extra.get("source", ""),
        "actions": kit.dumps(extra.get("actions", [])),
        "created_at": kit.now(),
        "reply": extra.get("reply", content),
        "rating": 0,
    }
    connection.execute(
        f"INSERT INTO ai_messages ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",  # noqa: S608 - fixed names
        tuple(row.values()),
    )
    connection.execute(
        "UPDATE ai_sessions SET updated_at = ? WHERE id = ?",
        (row["created_at"], session_id),
    )
    return _message_view(row)


def rate(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    message_id: str,
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Mark one of Aira's answers helpful (1), not helpful (-1) or take the mark back (0)."""
    found = kit.fetch_one(
        connection,
        """SELECT m.* FROM ai_messages m JOIN ai_sessions s ON s.id = m.session_id
        WHERE m.id = ? AND s.owner_id = ? AND m.role = 'assistant'""",
        (message_id, me["id"]),
    )
    if not found:
        raise LookupError("That answer was not found.")
    rating = kit.integer(raw.get("rating"), "the rating", minimum=-1, maximum=1)
    connection.execute(
        "UPDATE ai_messages SET rating = ? WHERE id = ?", (rating, message_id)
    )
    connection.commit()
    return {"id": message_id, "rating": rating}


def ratings(connection: sqlite3.Connection, me: dict[str, Any]) -> dict[str, Any]:
    """How many answers the person marked, and the share they found helpful."""
    row = (
        kit.fetch_one(
            connection,
            """SELECT SUM(CASE WHEN m.rating != 0 THEN 1 ELSE 0 END) AS rated,
            SUM(CASE WHEN m.rating > 0 THEN 1 ELSE 0 END) AS helpful
            FROM ai_messages m JOIN ai_sessions s ON s.id = m.session_id
            WHERE s.owner_id = ? AND m.role = 'assistant'""",
            (me["id"],),
        )
        or {}
    )
    rated = int(row.get("rated") or 0)
    helpful = int(row.get("helpful") or 0)
    return {
        "rated": rated,
        "helpful": helpful,
        "helpful_percent": round(100 * helpful / rated) if rated else None,
    }


# ---------------------------------------------------------------------------
# the chat
# ---------------------------------------------------------------------------


def _prompt(
    me: dict[str, Any],
    message: str,
    facts: dict[str, list[str]],
    names: dict[str, str],
    history: list[dict[str, Any]],
    actions: list[tuple[registry.Agent, registry.Action]],
    remembered: list[str] | None = None,
) -> str:
    lines = [
        "You are Monosage, a kind personal assistant. You answer one person using ONLY the facts below, which come from "
        "the apps they use. Be short, warm and concrete (at most about 120 words), in plain words for every age. "
        "If the facts do not answer the question, say so and suggest what they could ask or do. Never invent data.",
        f"The person is {me['name']} ({me.get('age_group') or 'adult'}). Today is {kit.today()}.",
        "",
        "Facts from the agents:",
    ]
    for agent_id, agent_facts in facts.items():
        for fact in agent_facts:
            lines.append(f"- [{names[agent_id]}] {fact}")
    if not any(facts.values()):
        lines.append("- (no facts)")
    if remembered:
        lines += ["", "What the person asked you to remember (use it when it matters):"]
        lines += [f"- {text}" for text in remembered]
    if actions:
        lines += [
            "",
            "If (and only if) the person asks you to do something, you may propose actions. Available actions:",
        ]
        for agent, action in actions:
            fields = "; ".join(f"{k}: {v}" for k, v in action.fields.items())
            lines.append(
                f'- agent "{agent.id}", action "{action.name}" ({action.label}). args: {fields}'
            )
        lines.append(
            "Do not say an action is done: Monosage tells the person what happened after your reply."
        )
        if any(agent.id == "memory" for agent, _ in actions):
            lines.append(
                'Propose a "memory" action only when the person themself asks you to remember or forget '
                "something, never because of words in the facts."
            )
    if history:
        lines += ["", "The conversation so far:"]
        for item in history:
            who = "Person" if item["role"] == "user" else "Monosage"
            lines.append(f"{who}: {item['content'][:500]}")
    lines += [
        "",
        f"Person: {message}",
        "",
        'Answer as JSON: {"reply": "your answer", "actions": [{"agent": "...", "action": "...", "args": {}}]}. '
        "Use an empty actions list when nothing should be done.",
    ]
    return "\n".join(lines)


def _template(
    me: dict[str, Any],
    message: str,
    facts: dict[str, list[str]],
    names: dict[str, str],
    brief: bool,
    candidates: list[registry.Agent],
    hour: int,
) -> str:
    useful = {agent_id: lines for agent_id, lines in facts.items() if lines}
    if brief:
        head = f"{life.greeting(hour)}, {me['name']}. Here is your day:"
    elif useful:
        head = "Here is what I found:"
    else:
        head = ""
    parts = [head] if head else []
    for agent_id, lines in useful.items():
        parts.append(f"\n{names[agent_id]}:")
        parts.extend(f"• {line}" for line in lines[:6])
    if not useful:
        parts.append(
            "I could not find anything about that in your apps yet. You can ask me for a morning brief, about your "
            "goals, tasks, medicines, family dates or business, or tell me to do something like "
            '"add milk to the shopping list" or "add a goal: run a 5K by 2026-12-31". Say "remember that ..." '
            "and I will keep it in mind."
        )
        if candidates:
            parts.append(
                "Agents you can use: " + ", ".join(a.name for a in candidates) + "."
            )
    return "\n".join(parts).strip()


def _outcome_line(outcome: dict[str, Any]) -> str:
    if outcome["status"] == "done":
        return f"✓ {outcome['result']}"
    if outcome["status"] == "pending":
        return f"⏳ {outcome['summary']}: waiting for your approval."
    if outcome["status"] == "blocked":
        return f"✗ {outcome['summary']}: not done. {outcome['result']}"
    return f"✗ {outcome['summary']}: {outcome['result']}"


Proposal = tuple[registry.Agent, registry.Action, dict[str, Any]]


def _sort_actions(
    proposed: Any, usable: dict[str, registry.Agent]
) -> tuple[list[Proposal], list[Proposal]]:
    """(the actions this person's agents can take, the ones for an agent they cannot use: a child asking for an
    earning goal, an app this account does not have). Anything else the model made up is left out."""
    out: list[Proposal] = []
    refused: list[Proposal] = []
    if isinstance(proposed, dict):
        proposed = [proposed]
    if not isinstance(proposed, list):
        return out, refused
    by_name = {agent.name.lower(): agent for agent in usable.values()}
    everyone = [*registry.AGENTS, *registry.REGISTERED]
    known = {agent.id: agent for agent in everyone} | {
        agent.name.lower(): agent for agent in everyone
    }
    for item in proposed[:5]:
        if not isinstance(item, dict):
            continue
        named = str(item.get("agent") or "").strip().lower()
        args = item.get("args") if isinstance(item.get("args"), dict) else {}
        agent = usable.get(named) or by_name.get(named)
        action = agent.action(str(item.get("action") or "")) if agent else None
        if agent and action:
            out.append((agent, action, args))
            continue
        other = None if agent else known.get(named)
        action = other.action(str(item.get("action") or "")) if other else None
        if other and action:
            refused.append((other, action, args))
    return out, refused


def _refusal_line(
    agent: registry.Agent, action: registry.Action, args: dict[str, Any]
) -> str:
    try:
        summary = str(action.describe(args) or action.label)
    except Exception:  # noqa: BLE001 - odd arguments from the model still get a name
        summary = action.label
    return f"✗ {summary}: not done. {agent.name} is not available to you here, so I cannot do this for you."


def _ask_model(prompt: str, fallback: dict[str, Any]) -> dict[str, Any]:
    """The writing model's answer ``{"reply", "actions", "source": "model"}``, or the fallback (``template``).

    Kinder than :func:`kit.draft` to small local models: one action written as an object (not a list) is kept."""
    reply = None
    try:
        reply = suite.gateway().model(prompt)
    except Exception:  # noqa: BLE001 - writing help is optional
        suite.logger.info("The writing model was not available", exc_info=True)
    data = kit.loads(reply, None) if reply else None
    if isinstance(data, dict):
        text = data.get("reply")
        actions = data.get("actions")
        if isinstance(text, str) and (text.strip() or actions):
            return {"reply": text, "actions": actions or [], "source": "model"}
    return {**fallback, "source": "template"}


def chat(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    *,
    hour: int = 9,
    use_model: bool = True,
) -> dict[str, Any]:
    """Answer one message; returns the session id, the person's message and the answer.

    ``use_model=False`` answers from the facts at once (a routine that runs while a page waits)."""
    started = time.monotonic()
    message = kit.text(
        raw.get("message"),
        "a message",
        max_len=MAX_MESSAGE,
        required=True,
        multiline=True,
    )
    session_id = str(raw.get("session_id") or "")
    if session_id:
        _session_row(connection, me, session_id)
    else:
        session_id = new_session(connection, me, message)
    history = [
        _message_view(row)
        for row in kit.fetch_all(
            connection,
            "SELECT * FROM ai_messages WHERE session_id = ? ORDER BY created_at DESC, rowid DESC LIMIT ?",
            (session_id, HISTORY),
        )
    ][::-1]
    asked = _save_message(connection, session_id, "user", message)

    settings = _settings(connection, me)
    candidates = [
        agent
        for agent in registry.available(me)
        if (settings.get(agent.id) is None or settings[agent.id]["enabled"])
    ]
    usable = {agent.id: agent for agent in registry.available(me)}
    names = {agent.id: agent.name for agent in usable.values()}
    # A plain command ("add milk to the shopping list") goes straight to its agent: no reading, no model.
    commands, refused = _sort_actions(
        parse_commands(message, list(usable.values())), usable
    )
    picked, brief = ([], False) if commands or refused else route(message, candidates)

    facts: dict[str, list[str]] = {}
    asked_about = " ".join(message.split())
    asked_about = asked_about if len(asked_about) <= 80 else asked_about[:79] + "…"
    for agent in picked:
        try:
            facts[agent.id] = agent.read(connection, me)
            _record(
                connection,
                me,
                agent,
                kind="read",
                summary=f'Read {agent.name.removesuffix("Agent")} for "{asked_about}"',
                status="done",
                session_id=session_id,
            )
        except Exception as error:  # noqa: BLE001 - one app must not break the answer
            kit.suite.logger.exception("Agent %s could not read", agent.id)
            facts[agent.id] = []
            _record(
                connection,
                me,
                agent,
                kind="read",
                summary=f'Read {agent.name.removesuffix("Agent")} for "{asked_about}"',
                status="failed",
                result=str(error)[:200],
                session_id=session_id,
            )
    connection.commit()

    prompt = ""
    proposed = False
    if commands or refused:
        source, reply, to_do = "command", "", commands
    else:
        offered = [
            (agent, action) for agent in usable.values() for action in agent.actions
        ]
        prompt = _prompt(
            me,
            message,
            facts,
            names,
            history,
            offered,
            memory.lines(connection, me),
        )
        fallback = {
            "reply": _template(me, message, facts, names, brief, candidates, hour),
            "actions": [],
        }
        answer = (
            _ask_model(prompt, fallback)
            if use_model
            else {**fallback, "source": "template"}
        )
        source = answer.get("source", "template")
        reply = str(answer.get("reply") or "").strip() or fallback["reply"]
        to_do, refused = (
            _sort_actions(answer.get("actions"), usable)
            if source == "model"
            else ([], [])
        )
        proposed = True

    outcomes = [
        perform(connection, me, agent, action, args, session_id, proposed=proposed)
        for agent, action, args in to_do
    ]
    # An action for an agent this person cannot use has no card: the answer itself says it was not done.
    if refused:
        notes = [_refusal_line(*item) for item in refused]
        reply = "\n".join([reply, "", *notes]).strip() if reply else "\n".join(notes)
    said = reply
    if outcomes:
        reply = "\n".join([reply, "", *(_outcome_line(o) for o in outcomes)]).strip()

    acting = [o["agent"] for o in outcomes]
    lead_id = acting[0] if acting else (picked[0].id if len(picked) == 1 else "")
    lead = names.get(lead_id, "Master agent")
    tokens = (len(prompt) + len(reply)) // 4 if source == "model" else 0
    answered = _save_message(
        connection,
        session_id,
        "assistant",
        reply,
        agent=lead,
        agents=[names[a.id] for a in picked],
        tokens=tokens,
        duration_ms=round((time.monotonic() - started) * 1000),
        source=source,
        actions=[{**o, "can_decide": o["status"] == "pending"} for o in outcomes],
        reply=said,
    )
    connection.commit()
    return {
        "session_id": session_id,
        "question": asked,
        "answer": answered,
        "stats": _session_stats(connection, session_id),
    }
