"""Planner routes (``/api/planner/*``), its Today cards and pulse, and the public shopping list page.

Every route works for the person asking (:func:`aira.suite.life.current`); :mod:`aira.suite.planner.service` holds
the rules. Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, life, public
from aira.suite.planner import service, store

APP = store.APP
BASE = "/api/planner"
ID = "([0-9a-f]{32})"


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    store.ensure(connection)
    me = life.current(request, connection)
    return connection, me


# ---------------------------------------------------------------------------
# my day
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.overview(connection, me)


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/tasks")
def tasks_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "tasks": service.list_tasks(
                connection,
                me,
                scope=request.q("scope", "mine"),
                status=request.q("status", "open"),
            ),
            "people": list(service.people(connection).values()),
            "today": service.today(connection).isoformat(),
        }


@aira_route("POST", f"{BASE}/tasks")
def tasks_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"task": service.create_task(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/tasks/{ID}")
def tasks_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "task": service.update_task(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/tasks/{ID}")
def tasks_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_task(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/tasks/{ID}/done")
def tasks_done(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"task": service.complete(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/tasks/{ID}/undo")
def tasks_undo(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"task": service.undo(connection, me, request.match.group(1))}


# ---------------------------------------------------------------------------
# points and rewards
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/rewards")
def rewards_view(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.rewards_view(connection, me)


@aira_route("POST", f"{BASE}/rewards")
def rewards_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"reward": service.save_reward(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/rewards/{ID}")
def rewards_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "reward": service.save_reward(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/rewards/{ID}")
def rewards_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.remove_reward(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/rewards/{ID}/request")
def rewards_request(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {
            "request": service.request_reward(connection, me, request.match.group(1))
        }


@aira_route("POST", f"{BASE}/requests/{ID}/approve")
def requests_approve(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "request": service.decide_reward(
                connection, me, request.match.group(1), True
            )
        }


@aira_route("POST", f"{BASE}/requests/{ID}/decline")
def requests_decline(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "request": service.decide_reward(
                connection, me, request.match.group(1), False
            )
        }


# ---------------------------------------------------------------------------
# shopping
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/lists")
def lists_view(request: Request) -> tuple[int, dict[str, Any]]:
    connection, _me = _open(request)
    with connection:
        return 200, {"lists": service.lists(connection), "aisles": list(service.AISLES)}


@aira_route("POST", f"{BASE}/lists")
def lists_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"list": service.save_list(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/lists/{ID}")
def lists_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "list": service.save_list(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/lists/{ID}")
def lists_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_list(connection, me, request.match.group(1))
        public.revoke(connection, APP, "shopping_list", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/lists/{ID}")
def lists_get(request: Request) -> tuple[int, dict[str, Any]]:
    """The list (or, with ``since``, only what changed since then, deleted items included)."""
    since = kit.moment(request.q("since"), "since") if request.q("since") else ""
    connection, _me = _open(request)
    with connection:
        list_id = request.match.group(1)
        data = service.list_detail(connection, list_id, since=since)
        if not since:
            data["usual"] = service.usual(connection, list_id)
            data["link"] = public.link_for(connection, APP, "shopping_list", list_id)
        return 200, data


@aira_route("POST", f"{BASE}/lists/{ID}/items")
def items_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {
            "item": service.add_item(
                connection, me["id"], request.match.group(1), request.payload
            )
        }


@aira_route("PATCH", f"{BASE}/items/{ID}")
def items_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": service.update_item(
                connection, me["id"], request.match.group(1), request.payload
            )
        }


@aira_route("DELETE", f"{BASE}/items/{ID}")
def items_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, _me = _open(request)
    with connection:
        service.delete_item(connection, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/lists/{ID}/clear")
def lists_clear(request: Request) -> tuple[int, dict[str, Any]]:
    connection, _me = _open(request)
    with connection:
        return 200, {
            "cleared": service.clear_checked(connection, request.match.group(1))
        }


@aira_route("POST", f"{BASE}/lists/{ID}/link")
def lists_link(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        if not life.is_adult(me):
            raise PermissionError("Ask an adult to share a list.")
        the_list = store.one(connection, "pn_lists", request.match.group(1), "list")
        link = public.create_link(
            connection, APP, "shopping_list", the_list["id"], title=the_list["name"]
        )
    request.audit("planner.list_link", "list", the_list["id"])
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/lists/{ID}/link")
def lists_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        if not life.is_adult(me):
            raise PermissionError("Ask an adult to stop sharing a list.")
        the_list = store.one(connection, "pn_lists", request.match.group(1), "list")
        public.revoke(connection, APP, "shopping_list", the_list["id"])
    request.audit("planner.list_unlink", "list", the_list["id"])
    return 200, {"link": None}


# ---------------------------------------------------------------------------
# budget and bills (adults)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/budget")
def budget_view(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    connection, me = _open(request)
    with connection:
        if request.q("format") == "csv":
            month = request.q("month") or service.today(connection).strftime("%Y-%m")
            return Raw(
                200,
                service.budget_csv(connection, me, month),
                "text/csv; charset=utf-8",
                filename=f"budget-{month}.csv",
            )
        return 200, service.budget(connection, me, request.q("month"))


@aira_route("POST", f"{BASE}/budget/categories")
def categories_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"category": service.save_category(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/budget/categories/{ID}")
def categories_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "category": service.save_category(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/budget/categories/{ID}")
def categories_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_category(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/entries")
def entries_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"entry": service.save_entry(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/entries/{ID}")
def entries_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "entry": service.save_entry(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/entries/{ID}")
def entries_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_entry(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("GET", f"{BASE}/bills")
def bills_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "bills": service.bills(connection, me),
            "categories": service.categories(connection),
        }


@aira_route("POST", f"{BASE}/bills")
def bills_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"bill": service.save_bill(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/bills/{ID}")
def bills_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "bill": service.save_bill(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/bills/{ID}")
def bills_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_bill(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/bills/{ID}/paid")
def bills_paid(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "bill": service.pay_bill(
                connection, me, request.match.group(1), request.payload
            )
        }


@aira_route("POST", f"{BASE}/bills/{ID}/unpaid")
def bills_unpaid(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        period = kit.text(
            request.payload.get("period"), "the period", max_len=10, required=True
        )
        return 200, {
            "bill": service.unpay_bill(connection, me, request.match.group(1), period)
        }


# ---------------------------------------------------------------------------
# notes
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/notes")
def notes_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"notes": service.notes(connection, me)}


@aira_route("POST", f"{BASE}/notes")
def notes_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"note": service.save_note(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/notes/{ID}")
def notes_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "note": service.save_note(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/notes/{ID}")
def notes_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.delete_note(connection, me, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


def _when(task: dict[str, Any], on: str) -> str:
    if task["due_date"] and task["due_date"] < on:
        return "Overdue" + (
            f" (was due {task['due_date']})" if task["due_date"] else ""
        )
    return f"Today at {task['due_time']}" if task["due_time"] else "Today"


@life.today(APP)
def _today(connection: Any, me: dict[str, Any]) -> list[dict[str, Any]]:
    store.ensure(connection)
    on = service.today(connection).isoformat()
    cards = []
    tasks = service.due_today(connection, me)
    lines = [
        {
            "text": t["title"],
            "sub": " · ".join(
                x
                for x in (_when(t, on), f"{t['points']} points" if t["points"] else "")
                if x
            ),
            "done": False,
            "action": life.action("Done", f"{BASE}/tasks/{t['id']}/done"),
        }
        for t in tasks[:8]
    ]
    overdue = any(t["overdue"] for t in tasks)
    cards.append(
        life.card(
            APP,
            "Your chores today",
            lines=lines,
            tone="warn" if overdue else "",
            empty="Nothing to do today. Well done!",
            icon="planner",
        )
    )
    if life.is_adult(me):
        soon = [
            b
            for b in service.bills(connection, me)
            if b["days_until"] is not None and b["days_until"] <= 3
        ]
        if soon:
            bill_lines = [
                {
                    "text": f"{b['name']} {kit.fmt_money(int(b['amount_cents']), kit.business(connection)['currency'])}"
                    if b["amount_cents"]
                    else b["name"],
                    "sub": (
                        "Overdue"
                        if b["days_until"] < 0
                        else "Due today"
                        if b["days_until"] == 0
                        else f"Due in {b['days_until']} day{'s' if b['days_until'] != 1 else ''}"
                    )
                    + (" · paid automatically" if b["autopay"] else ""),
                    "action": life.action("Paid", f"{BASE}/bills/{b['id']}/paid"),
                }
                for b in soon[:5]
            ]
            tone = "bad" if any(b["days_until"] < 0 for b in soon) else "warn"
            cards.append(
                life.card(
                    APP,
                    "Bills due soon",
                    lines=bill_lines,
                    tone=tone,
                    link="#/planner?tab=bills",
                )
            )
    shopping = [entry for entry in service.lists(connection) if entry["open"]]
    if shopping:
        cards.append(
            life.card(
                APP,
                "Shopping",
                lines=[
                    {
                        "text": entry["name"],
                        "sub": f"{entry['open']} item{'s' if entry['open'] != 1 else ''} to buy",
                    }
                    for entry in shopping[:4]
                ],
                link="#/planner?tab=shopping",
            )
        )
    return cards


@life.pulse(APP)
def _pulse(connection: Any, me: dict[str, Any], since: str) -> dict[str, Any]:
    store.ensure(connection)
    now = service.local_now(connection)
    on, clock = now.date().isoformat(), now.strftime("%H:%M")
    tasks = service.due_today(connection, me)
    alerts = [
        {
            "id": f"planner:task:{t['id']}:{t['due_date']}",
            "title": f"Time for: {t['title']}",
            "body": t["notes"]
            or (
                f"{t['points']} points when it is done."
                if t["points"]
                else "Tap Done when it is finished."
            ),
            "tone": "warn",
            "speak": True,
            "actions": [life.action("Done", f"{BASE}/tasks/{t['id']}/done")],
        }
        for t in tasks
        if t["due_date"] == on and t["due_time"] and t["due_time"] <= clock
    ]
    if life.is_adult(me):
        for bill in service.bills(connection, me):
            if bill["days_until"] == 0 and not bill["autopay"]:
                alerts.append(
                    {
                        "id": f"planner:bill:{bill['id']}:{bill['next_period']}",
                        "title": f"{bill['name']} is due today",
                        "body": kit.fmt_money(
                            int(bill["amount_cents"]),
                            kit.business(connection)["currency"],
                        )
                        if bill["amount_cents"]
                        else "",
                        "tone": "warn",
                        "speak": True,
                        "actions": [
                            life.action("Paid", f"{BASE}/bills/{bill['id']}/paid")
                        ],
                    }
                )
    return {"badge": len(tasks), "alerts": alerts}


# ---------------------------------------------------------------------------
# the public shopping list (a relative without an account views and ticks it)
# ---------------------------------------------------------------------------


@public.page(APP, "shopping_list")
def shopping_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    data = service.list_detail(connection, link["ref_id"], names=False)
    return {
        "list": data["list"],
        "items": data["items"],
        "aisles": list(service.AISLES),
        "now": data["now"],
    }


def _guest(payload: dict[str, Any]) -> str:
    name = kit.text(payload.get("by"), "your name", max_len=40) or "A guest"
    return f"guest:{name}"


@public.action(APP, "shopping_list", "tick")
def shopping_tick(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    item_id = kit.text(payload.get("item_id"), "the item", max_len=32, required=True)
    item = store.one(connection, "pn_items", item_id, "item")
    if item["list_id"] != link["ref_id"]:
        raise LookupError("That item is not on this list.")
    updated = service.update_item(
        connection, _guest(payload), item_id, {"checked": payload.get("checked", True)}
    )
    return 200, {
        "item": {
            k: updated[k]
            for k in (
                "id",
                "name",
                "quantity",
                "category",
                "checked",
                "updated_at",
                "deleted",
            )
        }
    }


@public.action(APP, "shopping_list", "add")
def shopping_add(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    item = service.add_item(
        connection,
        _guest(payload),
        link["ref_id"],
        {"name": payload.get("name"), "quantity": payload.get("quantity")},
    )
    kit.log(
        connection,
        APP,
        "shopping",
        f"{_guest(payload)[6:]} added {item['name']}",
        ref_type="list",
        ref_id=link["ref_id"],
        public=True,
    )
    connection.commit()
    return 201, {
        "item": {
            k: item[k]
            for k in (
                "id",
                "name",
                "quantity",
                "category",
                "checked",
                "updated_at",
                "deleted",
            )
        }
    }


@public.action(APP, "shopping_list", "refresh")
def shopping_refresh(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """Live updates on the public page: what changed since ``since``."""
    store.ensure(connection)
    since = kit.moment(payload.get("since"), "since") if payload.get("since") else ""
    data = service.list_detail(connection, link["ref_id"], since=since, names=False)
    return 200, {
        "changed": data.get("changed", data.get("items", [])),
        "now": data["now"],
    }
