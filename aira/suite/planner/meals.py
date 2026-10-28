"""Planner meals: a week of breakfasts, lunches and dinners, saved recipes with their ingredients, and one tap to
put what the week's meals need on a shopping list.

Meals are family content: everyone in the household sees the plan and may add a meal (children too: "Can we
have pancakes on Saturday?"). Changing or removing a meal or a recipe is for adults and for whoever added it.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import date, timedelta
from typing import Any

from aira.suite import kit, life
from aira.suite.planner import service, store

APP = store.APP
SLOTS = ("breakfast", "lunch", "dinner")
SLOT_NAMES = {"breakfast": "Breakfast", "lunch": "Lunch", "dinner": "Dinner"}
MAX_INGREDIENTS = 60
# "2 onions", "500 g flour", "1.5 kg rice", "3x eggs", "a pinch of salt" -> (quantity, name)
_QUANTITY = re.compile(
    r"^\s*(\d+(?:[.,/]\d+)?\s*(?:x|g|kg|mg|ml|l|litres?|liters?|tbsp|tsp|cups?|pcs|packs?|tins?|cans?|bunch(?:es)?|cloves?)?\.?)\s+(.+)$",
    re.IGNORECASE,
)


def parse_ingredient(line: Any) -> dict[str, str] | None:
    """One ingredient from a typed line or a ``{name, quantity}`` object; None for an empty line."""
    if isinstance(line, dict):
        name = kit.text(line.get("name"), "an ingredient", max_len=80)
        quantity = kit.text(line.get("quantity"), "the quantity", max_len=30)
    else:
        text = kit.text(line, "an ingredient", max_len=120)
        match = _QUANTITY.match(text)
        if match:
            quantity, name = match.group(1).strip(), match.group(2).strip()
        else:
            quantity, name = "", text
        name = name[:80]
        quantity = quantity[:30]
    if not name:
        return None
    return {"name": name, "quantity": quantity}


def _ingredients(value: Any) -> list[dict[str, str]]:
    if isinstance(value, str):
        value = value.splitlines()
    items = value if isinstance(value, list) else []
    out = [p for p in (parse_ingredient(item) for item in items[:MAX_INGREDIENTS]) if p]
    return out


def week_start(connection: sqlite3.Connection, value: Any = "") -> date:
    """The Monday of the week holding ``value`` (a day), or of this week."""
    text = kit.day(value, "the week")
    on = date.fromisoformat(text) if text else service.today(connection)
    return on - timedelta(days=on.weekday())


# ---------------------------------------------------------------------------
# recipes
# ---------------------------------------------------------------------------


def _recipe_view(row: dict[str, Any], me: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "ingredients": kit.loads(row["ingredients"], []) or [],
        "notes": row["notes"],
        "minutes": row["minutes"],
        "can_edit": life.is_adult(me) or row["created_by"] == me["id"],
    }


def recipes(
    connection: sqlite3.Connection, me: dict[str, Any], q: str = ""
) -> list[dict[str, Any]]:
    q = kit.text(q, "the search", max_len=80)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_recipes WHERE deleted_at IS NULL ORDER BY title COLLATE NOCASE",
    )
    out = [_recipe_view(r, me) for r in rows]
    if q:
        needle = q.lower()
        out = [
            r
            for r in out
            if needle in r["title"].lower()
            or any(needle in i["name"].lower() for i in r["ingredients"])
        ]
    return out


def _recipe(connection: sqlite3.Connection, recipe_id: str) -> dict[str, Any]:
    return store.one(connection, "pn_recipes", recipe_id, "recipe")


def save_recipe(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    recipe_id: str = "",
) -> dict[str, Any]:
    current = _recipe(connection, recipe_id) if recipe_id else None
    if current and not (life.is_adult(me) or current["created_by"] == me["id"]):
        raise PermissionError("Ask an adult to change this recipe.")
    merged = {**(current or {}), **raw}
    if current is not None and "ingredients" not in raw:
        merged["ingredients"] = kit.loads(current["ingredients"], [])
    values = {
        "title": kit.text(
            merged.get("title"), "the meal name", max_len=120, required=True
        ),
        "ingredients": kit.dumps(_ingredients(merged.get("ingredients"))),
        "notes": kit.text(
            merged.get("notes"), "the method", max_len=4000, multiline=True
        ),
        "minutes": kit.integer(
            merged.get("minutes"), "the minutes", default=0, minimum=0, maximum=1440
        ),
    }
    stamp = kit.now()
    if recipe_id:
        kit.update_row(
            connection, "pn_recipes", recipe_id, {**values, "updated_at": stamp}
        )
    else:
        recipe_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_recipes (id, title, ingredients, notes, minutes, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recipe_id,
                values["title"],
                values["ingredients"],
                values["notes"],
                values["minutes"],
                me["id"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    return _recipe_view(_recipe(connection, recipe_id), me)


def delete_recipe(
    connection: sqlite3.Connection, me: dict[str, Any], recipe_id: str
) -> None:
    current = _recipe(connection, recipe_id)
    if not (life.is_adult(me) or current["created_by"] == me["id"]):
        raise PermissionError("Ask an adult to remove this recipe.")
    stamp = kit.now()
    kit.update_row(
        connection, "pn_recipes", recipe_id, {"deleted_at": stamp, "updated_at": stamp}
    )
    # Planned meals keep their name; they just no longer bring ingredients.
    connection.execute(
        "UPDATE pn_meals SET recipe_id = '', updated_at = ? WHERE recipe_id = ?",
        (stamp, recipe_id),
    )
    connection.commit()


# ---------------------------------------------------------------------------
# the week's meals
# ---------------------------------------------------------------------------


def _meal_view(
    row: dict[str, Any],
    me: dict[str, Any],
    everyone: dict[str, dict[str, Any]],
    recipe_titles: dict[str, str],
) -> dict[str, Any]:
    cook = row["cook"]
    return {
        "id": row["id"],
        "day": row["day"],
        "slot": row["slot"],
        "slot_name": SLOT_NAMES.get(row["slot"], row["slot"]),
        "title": row["title"],
        "recipe_id": row["recipe_id"] if row["recipe_id"] in recipe_titles else "",
        "cook": cook,
        "cook_name": str(everyone.get(cook, {}).get("name") or "") if cook else "",
        "notes": row["notes"],
        "can_edit": life.is_adult(me) or row["created_by"] == me["id"],
    }


def week(
    connection: sqlite3.Connection, me: dict[str, Any], start: Any = ""
) -> dict[str, Any]:
    first = week_start(connection, start)
    last = first + timedelta(days=6)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_meals WHERE deleted_at IS NULL AND day BETWEEN ? AND ? ORDER BY day",
        (first.isoformat(), last.isoformat()),
    )
    everyone = service.people(connection)
    all_recipes = recipes(connection, me)
    titles = {r["id"]: r["title"] for r in all_recipes}
    by_day: dict[str, dict[str, Any]] = {}
    for row in rows:
        by_day.setdefault(row["day"], {})[row["slot"]] = _meal_view(
            row, me, everyone, titles
        )
    on = service.today(connection)
    days = []
    for offset in range(7):
        day = first + timedelta(days=offset)
        meals = by_day.get(day.isoformat(), {})
        days.append(
            {
                "day": day.isoformat(),
                "name": day.strftime("%A"),
                "today": day == on,
                "meals": {slot: meals.get(slot) for slot in SLOTS},
            }
        )
    planned = sum(1 for d in days for m in d["meals"].values() if m)
    return {
        "start": first.isoformat(),
        "end": last.isoformat(),
        "previous": (first - timedelta(days=7)).isoformat(),
        "next": (first + timedelta(days=7)).isoformat(),
        "today": on.isoformat(),
        "days": days,
        "planned": planned,
        "slots": [{"id": s, "name": SLOT_NAMES[s]} for s in SLOTS],
        "recipes": all_recipes,
        "people": list(everyone.values()),
        "lists": service.lists(connection),
    }


def _meal(connection: sqlite3.Connection, meal_id: str) -> dict[str, Any]:
    return store.one(connection, "pn_meals", meal_id, "meal")


def save_meal(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
    meal_id: str = "",
) -> dict[str, Any]:
    """Plan a meal. A day and meal that already has something is changed rather than doubled."""
    current = _meal(connection, meal_id) if meal_id else None
    merged = {**(current or {}), **raw}
    day = kit.day(merged.get("day"), "the day", required=True)
    slot = kit.choice(merged.get("slot"), "the meal", SLOTS, default="dinner")
    recipe_id = kit.text(merged.get("recipe_id"), "the recipe", max_len=32)
    recipe = _recipe(connection, recipe_id) if recipe_id else None
    title = kit.text(merged.get("title"), "the meal", max_len=120)
    if recipe and (not title or ("recipe_id" in raw and "title" not in raw)):
        title = recipe["title"]
    if not title:
        raise ValueError("Write the meal or choose a recipe.")
    cook = kit.text(merged.get("cook"), "the cook", max_len=40)
    if cook and cook not in service.people(connection):
        raise ValueError("Choose who is cooking from the household.")
    values = {
        "day": day,
        "slot": slot,
        "title": title,
        "recipe_id": recipe["id"] if recipe else "",
        "cook": cook,
        "notes": kit.text(merged.get("notes"), "a note", max_len=300),
    }
    taken = kit.fetch_one(
        connection,
        "SELECT * FROM pn_meals WHERE day = ? AND slot = ? AND deleted_at IS NULL",
        (day, slot),
    )
    if taken and taken["id"] != meal_id:
        if current is None:
            current, meal_id = (
                taken,
                taken["id"],
            )  # the slot's meal is changed, not doubled
        else:
            raise ValueError(
                f"{SLOT_NAMES[slot]} on that day is already planned. Change that one instead."
            )
    if current is not None and not (
        life.is_adult(me) or current["created_by"] == me["id"]
    ):
        raise PermissionError("Ask an adult to change this meal.")
    stamp = kit.now()
    if meal_id:
        kit.update_row(connection, "pn_meals", meal_id, {**values, "updated_at": stamp})
    else:
        meal_id = kit.new_id()
        connection.execute(
            "INSERT INTO pn_meals (id, day, slot, title, recipe_id, cook, notes, created_by, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                meal_id,
                values["day"],
                values["slot"],
                values["title"],
                values["recipe_id"],
                values["cook"],
                values["notes"],
                me["id"],
                stamp,
                stamp,
            ),
        )
    connection.commit()
    everyone = service.people(connection)
    titles = {recipe["id"]: recipe["title"]} if recipe else {}
    return _meal_view(_meal(connection, meal_id), me, everyone, titles)


def delete_meal(
    connection: sqlite3.Connection, me: dict[str, Any], meal_id: str
) -> None:
    current = _meal(connection, meal_id)
    if not (life.is_adult(me) or current["created_by"] == me["id"]):
        raise PermissionError("Ask an adult to remove this meal.")
    stamp = kit.now()
    kit.update_row(
        connection, "pn_meals", meal_id, {"deleted_at": stamp, "updated_at": stamp}
    )
    connection.commit()


def copy_week(
    connection: sqlite3.Connection, me: dict[str, Any], start: Any = ""
) -> int:
    """Fill this week's empty meals with last week's (the same day of the week). Planned meals stay."""
    first = week_start(connection, start)
    before = first - timedelta(days=7)
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_meals WHERE deleted_at IS NULL AND day BETWEEN ? AND ?",
        (before.isoformat(), (before + timedelta(days=6)).isoformat()),
    )
    copied = 0
    stamp = kit.now()
    for row in rows:
        day = (date.fromisoformat(row["day"]) + timedelta(days=7)).isoformat()
        changed = connection.execute(
            """INSERT INTO pn_meals (id, day, slot, title, recipe_id, cook, notes, created_by, created_at, updated_at)
            SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            WHERE NOT EXISTS (SELECT 1 FROM pn_meals WHERE day = ? AND slot = ? AND deleted_at IS NULL)""",
            (
                kit.new_id(),
                day,
                row["slot"],
                row["title"],
                row["recipe_id"],
                row["cook"],
                row["notes"],
                me["id"],
                stamp,
                stamp,
                day,
                row["slot"],
            ),
        ).rowcount
        copied += changed
    connection.commit()
    return copied


def shop(
    connection: sqlite3.Connection,
    me: dict[str, Any],
    raw: dict[str, Any],
) -> dict[str, Any]:
    """Put the ingredients of the week's meals (or of one recipe) on a shopping list. Something already waiting
    on the list is not added twice, and the same ingredient from two meals goes on once."""
    list_id = kit.text(raw.get("list_id"), "the list", max_len=32)
    if not list_id:
        list_id = service.lists(connection)[0]["id"]
    store.one(connection, "pn_lists", list_id, "list")
    recipe_id = kit.text(raw.get("recipe_id"), "the recipe", max_len=32)
    if recipe_id:
        sources = [_recipe(connection, recipe_id)]
    else:
        first = week_start(connection, raw.get("start"))
        # From today on: meals already eaten this week need nothing more.
        on = (
            max(first, service.today(connection))
            if not raw.get("whole_week")
            else first
        )
        ids = [
            r["recipe_id"]
            for r in kit.fetch_all(
                connection,
                "SELECT recipe_id FROM pn_meals WHERE deleted_at IS NULL AND recipe_id != '' AND day BETWEEN ? AND ?",
                (on.isoformat(), (first + timedelta(days=6)).isoformat()),
            )
        ]
        sources = []
        for rid in ids:
            try:
                sources.append(_recipe(connection, rid))
            except LookupError:
                continue
    waiting = {
        service._usual_key(r["name"])
        for r in kit.fetch_all(
            connection,
            "SELECT name FROM pn_items WHERE list_id = ? AND deleted_at IS NULL AND checked = 0",
            (list_id,),
        )
    }
    added: list[str] = []
    skipped: list[str] = []
    added_keys: set[str] = set()
    for recipe in sources:
        for ingredient in kit.loads(recipe["ingredients"], []) or []:
            key = service._usual_key(ingredient.get("name", ""))
            if not key or key in added_keys:
                continue
            if key in waiting:
                if ingredient["name"] not in skipped:
                    skipped.append(ingredient["name"])
                continue
            added_keys.add(key)
            item = service.add_item(
                connection,
                me["id"],
                list_id,
                {
                    "name": ingredient["name"],
                    "quantity": ingredient.get("quantity", ""),
                },
            )
            added.append(item["name"])
    return {
        "added": added,
        "skipped": skipped,
        "list_id": list_id,
        "recipes": len(sources),
    }


def today_meals(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    on = service.today(connection).isoformat()
    rows = kit.fetch_all(
        connection,
        "SELECT * FROM pn_meals WHERE deleted_at IS NULL AND day = ?",
        (on,),
    )
    order = {slot: index for index, slot in enumerate(SLOTS)}
    return sorted(rows, key=lambda r: order.get(r["slot"], 9))
