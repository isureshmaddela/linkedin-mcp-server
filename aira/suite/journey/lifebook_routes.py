"""Life stages routes (``/api/journey/lives``, ``/records``, ``/wishes``), loaded with :mod:`journey_routes`.

Every route works for the person asking and only on the lives they keep; :mod:`aira.suite.journey.lifebook` holds
the rules and :mod:`aira.suite.journey.stages` what each stage of life asks for.
"""

from __future__ import annotations

from typing import Any

from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import life
from aira.suite.journey import lifebook, stages, store

BASE = "/api/journey"
ID = "([0-9a-f]{32})"
ITEM = r"([a-z_]{2,20}\.[a-z0-9_.]{1,40})"  # a stage item (baby.vaccines) or an interest idea (music.adult.0)
# A plan changes nothing: in the hosted build it is a read (life.read).
HOSTED_RULES = {"lives_plan": "read"}


def _open(request: Request) -> tuple[Any, dict[str, Any]]:
    connection = request.connect()
    store.ensure(connection)
    me = life.current(request, connection)
    return connection, me


@aira_route("GET", f"{BASE}/lives")
def lives_list(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "lives": lifebook.list_lives(connection, me),
            "may_keep_others": lifebook.may_keep_others(me),
        }


@aira_route("POST", f"{BASE}/lives")
def lives_create(request: Request) -> tuple[int, dict[str, Any]]:
    """Someone you look after: a child (even one on the way), a parent, a partner (adults only)."""
    connection, me = _open(request)
    with connection:
        made = lifebook.save_life(connection, me, request.payload)
    request.audit("journey.life_add", "life", made["id"])
    return 201, {"life": made}


@aira_route("GET", f"{BASE}/lives/me")
def lives_me(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, lifebook.book(connection, me)


@aira_route("GET", f"{BASE}/lives/{ID}")
def lives_get(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, lifebook.book(connection, me, request.match.group(1))


@aira_route("PATCH", f"{BASE}/lives/{ID}")
def lives_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "life": lifebook.save_life(
                connection, me, request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/lives/{ID}")
def lives_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        lifebook.delete_life(connection, me, request.match.group(1))
    request.audit("journey.life_delete", "life", request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/lives/{ID}/passed")
def lives_passed(request: Request) -> tuple[int, dict[str, Any]]:
    """Someone you looked after has died: their book becomes a remembrance."""
    connection, me = _open(request)
    with connection:
        changed = lifebook.set_passed(
            connection, me, request.match.group(1), request.payload
        )
    request.audit("journey.life_passed", "life", changed["id"])
    return 200, {"life": changed}


@aira_route("DELETE", f"{BASE}/lives/{ID}/passed")
def lives_unpassed(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "life": lifebook.clear_passed(connection, me, request.match.group(1))
        }


@aira_route("PUT", f"{BASE}/lives/{ID}/items/{ITEM}")
def lives_item(request: Request) -> tuple[int, dict[str, Any]]:
    """Done, not for me, planned (with the goal it became) or open again."""
    connection, me = _open(request)
    with connection:
        return 200, {
            "item": lifebook.set_item(
                connection,
                me,
                request.match.group(1),
                request.match.group(2),
                request.payload,
            )
        }


@aira_route("POST", f"{BASE}/lives/{ID}/records")
def records_create(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        record = lifebook.save_record(
            connection, me, request.match.group(1), request.payload
        )
    return 201, {"record": record}


@aira_route("PATCH", f"{BASE}/records/{ID}")
def records_update(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        return 200, {
            "record": lifebook.save_record(
                connection, me, "", request.payload, request.match.group(1)
            )
        }


@aira_route("DELETE", f"{BASE}/records/{ID}")
def records_delete(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        lifebook.delete_record(connection, me, request.match.group(1))
    return 200, {"deleted": True}


@aira_route("POST", f"{BASE}/lives/{ID}/milestones")
def lives_milestone(request: Request) -> tuple[int, dict[str, Any]]:
    """A moment of a life: on your own timeline, or in the book of someone you look after."""
    connection, me = _open(request)
    with connection:
        return 201, lifebook.add_milestone(
            connection, me, request.match.group(1), request.payload
        )


@aira_route("POST", f"{BASE}/lives/{ID}/plan")
def lives_plan(request: Request) -> tuple[int, dict[str, Any]]:
    """Next steps for this stage of the life, from the writing model or the stage's own checklist."""
    connection, me = _open(request)
    with connection:
        context = lifebook.plan_context(connection, me, request.match.group(1))
    connection.close()  # the model may take a while: do not hold the database
    return 200, lifebook.plan(context)


@aira_route("PUT", f"{BASE}/wishes")
def wishes_save(request: Request) -> tuple[int, dict[str, Any]]:
    connection, me = _open(request)
    with connection:
        wishes = lifebook.save_wishes(connection, me, request.payload)
    request.audit("journey.wishes_save", "life", me["id"])
    return 200, {"wishes": wishes}


@aira_route("GET", f"{BASE}/export/wishes")
def wishes_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        return Raw(
            200,
            lifebook.wishes_text(connection, me),
            "text/plain; charset=utf-8",
            "my-wishes.txt",
            headers=(("Cache-Control", "no-store"),),
        )


@aira_route("GET", f"{BASE}/export/lives/{ID}")
def lives_export(request: Request) -> Raw:
    connection, me = _open(request)
    with connection:
        data, name = lifebook.book_csv(connection, me, request.match.group(1))
        return Raw(200, data, "text/csv; charset=utf-8", name)


@aira_route("GET", f"{BASE}/stages")
def stages_catalog(request: Request) -> tuple[int, dict[str, Any]]:
    """Every stage of life with its checklist and milestones, and the interests (the same for everyone)."""
    _open(request)[0].close()
    return 200, {
        "stages": [
            {
                key: stage[key]
                for key in (
                    "id",
                    "title",
                    "label",
                    "description",
                    "items",
                    "milestones",
                )
            }
            for stage in stages.STAGES
        ],
        "areas": list(stages.AREAS),
        "interests": [
            {"id": interest["id"], "title": interest["title"], "mark": interest["mark"]}
            for interest in stages.INTERESTS
        ],
    }
