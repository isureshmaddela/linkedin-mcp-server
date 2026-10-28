"""Planner routes (``/api/planner/*``), its Today cards and pulse, and the public shopping list page.

Every route works for the person asking (:func:`aira.suite.life.current`); :mod:`aira.suite.planner.service` holds
the rules. Loaded by :func:`aira.suite.load_apps`.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, life, public
from aira.suite.planner import goals, meals, search, selling, service, store

APP = store.APP
BASE = "/api/planner"
ID = "([0-9a-f]{32})"


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    """The database and the person asking. The caller closes the connection with ``with connection:``; when
    there is no person yet (the picker, a PIN) the connection is closed here before the 403 goes out."""
    connection = request.connect()
    try:
        store.ensure(connection)
        me = life.current(request, connection)
    except BaseException:
        connection.close()
        raise
    return connection, me


def _csv(body: bytes, name: str) -> Raw:
    return Raw(200, body, "text/csv; charset=utf-8", filename=name)


# ---------------------------------------------------------------------------
# my day
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/overview")
def overview(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.overview(connection, me)


@aira_route("GET", f"{BASE}/search")
def search_all(request: Request) -> tuple[int, dict[str, Any]]:
    """What the search box across the top finds (``q``, up to ``limit`` of each kind): :mod:`.search`."""
    connection, me = _open(request)
    with connection:
        return 200, search.find(
            connection, me, request.q("q", ""), request.q("limit", "8")
        )


# ---------------------------------------------------------------------------
# tasks
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/tasks")
def tasks_list(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    """Tasks (``scope`` mine/family, ``status`` open/done/all, ``q`` search, ``person`` one person's);
    ``format=csv`` downloads them."""
    connection, me = _open(request)
    with connection:
        filters = {
            "q": request.q("q", ""),
            "person": request.q("person", ""),
        }
        if request.q("format") == "csv":
            return _csv(
                service.tasks_csv(
                    connection,
                    me,
                    scope=request.q("scope", "family"),
                    status=request.q("status", "all"),
                    **filters,
                ),
                "chores.csv",
            )
        return 200, {
            "tasks": service.list_tasks(
                connection,
                me,
                scope=request.q("scope", "mine"),
                status=request.q("status", "open"),
                **filters,
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
# pocket money (adults)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/pocket")
def pocket_view(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.pocket(connection, me)


@aira_route("POST", f"{BASE}/pocket/rate")
def pocket_rate(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, service.set_pocket_rate(connection, me, request.payload)


@aira_route("POST", f"{BASE}/pocket/{ID}/pay")
def pocket_pay(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        payout = service.pay_out(
            connection, me, request.match.group(1), request.payload
        )
    request.audit("planner.pocket_money", "payout", payout["id"])
    return 201, {"payout": payout}


@aira_route("DELETE", f"{BASE}/pocket/payouts/{ID}")
def pocket_undo(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        service.undo_payout(connection, me, request.match.group(1))
    request.audit("planner.pocket_money_undo", "payout", request.match.group(1))
    return 200, {"deleted": True}


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
            month = service._month(request.q("month"), connection)
            return _csv(
                service.budget_csv(connection, me, month), f"budget-{month}.csv"
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
# meals and recipes
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/meals")
def meals_week(request: Request) -> tuple[int, dict[str, Any]]:
    """The week holding ``start`` (any day; default this week), Monday first."""
    connection, me = _open(request)
    with connection:
        return 200, meals.week(connection, me, request.q("start", ""))


@aira_route("POST", f"{BASE}/meals")
def meals_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"meal": meals.save_meal(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/meals/{ID}")
def meals_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "meal": meals.save_meal(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/meals/{ID}")
def meals_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        meals.delete_meal(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/meals/copy")
def meals_copy(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "copied": meals.copy_week(connection, me, request.payload.get("start"))
        }


@aira_route("POST", f"{BASE}/meals/shop")
def meals_shop(request: Request) -> tuple[int, dict[str, Any]]:
    """Put the ingredients of the week's meals (or of one ``recipe_id``) on a shopping list."""
    connection, me = _open(request)
    with connection:
        return 200, meals.shop(connection, me, request.payload)


@aira_route("GET", f"{BASE}/recipes")
def recipes_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"recipes": meals.recipes(connection, me, request.q("q", ""))}


@aira_route("POST", f"{BASE}/recipes")
def recipes_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"recipe": meals.save_recipe(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/recipes/{ID}")
def recipes_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "recipe": meals.save_recipe(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/recipes/{ID}")
def recipes_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        meals.delete_recipe(connection, me, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# selling things the household no longer needs (adults)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/sell")
def sell_view(request: Request) -> tuple[int, dict[str, Any]] | Raw:
    connection, me = _open(request)
    with connection:
        if request.q("format") == "csv":
            return _csv(selling.csv(connection, me), "things-for-sale.csv")
        return 200, selling.overview(
            connection, me, status=request.q("status", ""), q=request.q("q", "")
        )


@aira_route("POST", f"{BASE}/sell/items")
def sell_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"item": selling.save_item(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/sell/items/{ID}")
def sell_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": selling.save_item(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/sell/items/{ID}")
def sell_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        selling.delete_item(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/sell/items/{ID}/sold")
def sell_sold(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        item = selling.mark_sold(
            connection, me, request.match.group(1), request.payload
        )
    request.audit("planner.sold", "sale_item", item["id"])
    return 200, {"item": item}


@aira_route("POST", f"{BASE}/sell/items/{ID}/unsold")
def sell_unsold(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        item = selling.unsell(connection, me, request.match.group(1))
    request.audit("planner.unsold", "sale_item", item["id"])
    return 200, {"item": item}


@aira_route("POST", f"{BASE}/sell/items/{ID}/available")
def sell_available(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"item": selling.put_back(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/sell/items/{ID}/pause")
def sell_pause(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"item": selling.pause(connection, me, request.match.group(1))}


@aira_route("POST", f"{BASE}/sell/items/{ID}/jobs")
def sell_job(request: Request) -> tuple[int, dict[str, Any]]:
    """A side job done and paid (``amount_cents``, ``hours``, ``customer``, ``day``, ``request_id``, ``goal_id``)."""
    connection, me = _open(request)
    with connection:
        job = selling.record_job(
            connection, me, request.match.group(1), request.payload
        )
    request.audit("planner.job", "job", job["id"])
    return 201, {"job": job}


@aira_route("DELETE", f"{BASE}/sell/jobs/{ID}")
def sell_job_undo(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        selling.undo_job(connection, me, request.match.group(1))
    request.audit("planner.job_undo", "job", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/sell/requests/{ID}/accept")
def sell_accept(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": selling.decide(connection, me, request.match.group(1), True)
        }


@aira_route("POST", f"{BASE}/sell/requests/{ID}/decline")
def sell_decline(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": selling.decide(connection, me, request.match.group(1), False)
        }


@aira_route("POST", f"{BASE}/sell/settings")
def sell_settings(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {"settings": selling.save_settings(connection, me, request.payload)}


@aira_route("POST", f"{BASE}/sell/link")
def sell_link(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        if not life.is_adult(me):
            raise PermissionError("Selling is for adults.")
        link = public.create_link(
            connection,
            APP,
            selling.KIND,
            selling.REF,
            title=selling.settings(connection)["title"],
        )
    request.audit("planner.sale_link", "sale_page", selling.REF)
    return 200, {"link": link}


@aira_route("DELETE", f"{BASE}/sell/link")
def sell_unlink(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        if not life.is_adult(me):
            raise PermissionError("Selling is for adults.")
        public.revoke(connection, APP, selling.KIND, selling.REF)
    request.audit("planner.sale_unlink", "sale_page", selling.REF)
    return 200, {"link": None}


# ---------------------------------------------------------------------------
# savings goals (adults)
# ---------------------------------------------------------------------------


@aira_route("GET", f"{BASE}/goals")
def goals_view(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, goals.goals(connection, me)


@aira_route("POST", f"{BASE}/goals")
def goals_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 201, {"goal": goals.save_goal(connection, me, request.payload)}


@aira_route("PATCH", f"{BASE}/goals/{ID}")
def goals_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": goals.save_goal(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/goals/{ID}")
def goals_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        goals.delete_goal(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/goals/{ID}/add")
def goals_add(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": goals.move(connection, me, request.match.group(1), request.payload)
        }


@aira_route("POST", f"{BASE}/goals/{ID}/take")
def goals_take(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "goal": goals.move(
                connection, me, request.match.group(1), request.payload, take_out=True
            )
        }


@aira_route("DELETE", f"{BASE}/goals/moves/{ID}")
def goals_undo_move(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        goals.undo_move(connection, me, request.match.group(1))
    return 200, {"deleted": True}


# ---------------------------------------------------------------------------
# Today and pulse
# ---------------------------------------------------------------------------


def _asked_line(r: dict[str, Any], currency: str) -> dict[str, Any]:
    """A neighbour's request on Today: to buy a thing (keep it for them) or for a side job (say yes)."""
    if r.get("item_kind") == "service":
        return {
            "text": f"{r['name']}: {r['item_title']}",
            "sub": "Asked for this job",
            "action": life.action("Say yes", f"{BASE}/sell/requests/{r['id']}/accept"),
        }
    return {
        "text": f"{r['name']}: {r['item_title']}",
        "sub": (
            f"Offers {kit.fmt_money(int(r['offer_cents']), currency)}"
            if r["offer_cents"]
            else "Asked to buy it"
        ),
        "action": life.action(
            "Keep it for them", f"{BASE}/sell/requests/{r['id']}/accept"
        ),
    }


def _when(task: dict[str, Any], on: str) -> str:
    if task["due_date"] and task["due_date"] < on:
        due = date.fromisoformat(task["due_date"])
        return f"Overdue (was due {due.strftime('%a')} {due.day} {due.strftime('%b')})"
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
        asked = selling.new_requests(connection, limit=4)
        if asked:
            currency = kit.business(connection)["currency"]
            cards.append(
                life.card(
                    APP,
                    "People want to buy"
                    if all(r.get("item_kind") != "service" for r in asked)
                    else "People are asking",
                    lines=[_asked_line(r, currency) for r in asked],
                    tone="good",
                    link="#/planner?tab=sell",
                )
            )
    planned = meals.today_meals(connection)
    if planned:
        cards.append(
            life.card(
                APP,
                "Meals today",
                lines=[
                    {
                        "text": f"{meals.SLOT_NAMES.get(m['slot'], m['slot'])}: {m['title']}",
                        "sub": f"{life.name_of(connection, m['cook'])} is cooking"
                        if m["cook"]
                        else "",
                    }
                    for m in planned
                ],
                link="#/planner?tab=meals",
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
        currency = kit.business(connection)["currency"]
        for asked in selling.new_requests(connection, limit=5):
            job = asked.get("item_kind") == "service"
            offer = (
                f" and offers {kit.fmt_money(int(asked['offer_cents']), currency)}"
                if asked["offer_cents"]
                else ""
            )
            alerts.append(
                {
                    "id": f"planner:sale:{asked['id']}",
                    "title": f"Someone asks for {asked['item_title']}"
                    if job
                    else f"Someone wants to buy {asked['item_title']}",
                    "body": f"{asked['name']} ({asked['contact']}) asked{offer}."
                    + (f" “{asked['message']}”" if asked["message"] else ""),
                    "tone": "good",
                    "speak": False,
                    "actions": [
                        life.action(
                            "Say yes" if job else "Keep it for them",
                            f"{BASE}/sell/requests/{asked['id']}/accept",
                        )
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


def _changes(connection: Any, link: dict[str, Any], since_value: Any) -> dict[str, Any]:
    store.ensure(connection)
    since = kit.moment(since_value, "since") if since_value else ""
    data = service.list_detail(connection, link["ref_id"], since=since, names=False)
    return {
        "changed": data.get("changed", data.get("items", [])),
        "now": data["now"],
    }


@public.query(APP, "shopping_list", "changes")
def shopping_changes(
    connection: Any, link: dict[str, Any], params: dict[str, Any]
) -> tuple[int, dict[str, Any]]:
    """Live updates on the public page: what changed since ``since``. A read-only query, so the page's polling
    neither counts as visitor actions on the link nor uses up the visitor's action limit."""
    return 200, _changes(connection, link, params.get("since"))


@public.action(APP, "shopping_list", "refresh")
def shopping_refresh(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    """The older page's polling (kept for pages opened before ``changes`` existed)."""
    return 200, _changes(connection, link, payload.get("since"))


# ---------------------------------------------------------------------------
# the public "things for sale" page (neighbours and friends ask to buy)
# ---------------------------------------------------------------------------


@public.page(APP, selling.KIND)
def sale_page(connection: Any, link: dict[str, Any]) -> dict[str, Any]:
    store.ensure(connection)
    return selling.public_page(connection)


@public.action(APP, selling.KIND, "ask")
def sale_ask(
    connection: Any,
    link: dict[str, Any],
    payload: dict[str, Any],
    visitor: dict[str, Any],
) -> tuple[int, dict[str, Any]]:
    store.ensure(connection)
    return 201, {"request": selling.ask(connection, payload)}
