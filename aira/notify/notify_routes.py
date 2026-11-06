"""Routes of the notification centre (``/api/notify/*``): the person's notifications, their choices, and the
browsers they turned notifications on in. The live stream (``GET /api/notify/stream``) is the hosting server's
own (it holds the connection open).

The person is the one using the app (:func:`aira.suite.life.current`): a household profile, or the signed-in
member in the hosted build.
"""

from __future__ import annotations

from typing import Any

from aira import notify
from aira.airaapi.http import Request, aira_route
from aira.notify import store, webpush
from aira.suite import life

BASE = "/api/notify"


def _person(request: Request, connection: Any) -> dict[str, Any]:
    life.ensure(connection)
    store.ensure(connection)
    return life.current(request, connection)


def _settings(connection: Any, profile: dict[str, Any]) -> dict[str, Any]:
    key = store.person_key(profile)
    choice = store.prefs(connection, key)
    devices = store.subscriptions(connection, store.keys_of(connection, profile))
    try:
        vapid = webpush.public_key()
    except Exception:  # noqa: BLE001 - no key file: the page offers no push
        notify.logger.exception("The Web Push key is not available")
        vapid = ""
    return {
        "prefs": choice,
        "categories": [
            {"id": name, "label": meta["label"], "hint": meta["hint"]}
            for name, meta in store.CATEGORIES.items()
        ],
        "email": choice.get("email") or profile.get("email") or "",
        "push": {
            "key": vapid,
            "devices": [
                {"device": d["device"], "created_at": d["created_at"]} for d in devices
            ],
        },
        "live": notify.live(),
        "timezone": store.zone_name(connection, choice),
    }


@aira_route("GET", f"{BASE}/items")
def items_list(request: Request) -> tuple[int, dict[str, Any]]:
    """The newest notifications (``?unread=1`` for unread only) and how many are unread."""
    with request.connect() as connection:
        profile = _person(request, connection)
        keys = store.keys_of(connection, profile)
        return 200, {
            "items": store.items(
                connection,
                keys,
                unread_only=request.q("unread") == "1",
                limit=request.qint("limit", 50, 1, 200),
            ),
            "unread": store.unread(connection, keys),
            "person": store.person_key(profile),
        }


@aira_route("POST", f"{BASE}/read")
def items_read(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"ids": [...]}`` marks those read; without ids, all."""
    ids = request.payload.get("ids")
    with request.connect() as connection:
        profile = _person(request, connection)
        keys = store.keys_of(connection, profile)
        changed = store.mark_read(
            connection, keys, [str(i) for i in ids] if isinstance(ids, list) else None
        )
        unread = store.unread(connection, keys)
    home = notify.home_of(request.user)
    if home:
        notify.publish(home, "notify", {"read": True}, person=store.person_key(profile))
    return 200, {"updated": changed, "unread": unread}


@aira_route("GET", f"{BASE}/settings")
def settings_get(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        return 200, _settings(connection, _person(request, connection))


@aira_route("PUT", f"{BASE}/settings")
def settings_save(request: Request) -> tuple[int, dict[str, Any]]:
    """``{"categories": {"careers": {"push": bool, "email": bool}}, "quiet": {"on", "start", "end"},
    "timezone", "email"}``"""
    with request.connect() as connection:
        profile = _person(request, connection)
        store.save_prefs(connection, store.person_key(profile), request.payload)
        return 200, _settings(connection, profile)


@aira_route("POST", f"{BASE}/push/subscribe")
def push_subscribe(request: Request) -> tuple[int, dict[str, Any]]:
    """The browser's ``PushSubscription`` (``{"endpoint", "keys": {"p256dh", "auth"}}``) and a device name."""
    with request.connect() as connection:
        profile = _person(request, connection)
        saved = store.subscribe(connection, store.person_key(profile), request.payload)
        return 201, {"subscription": saved, **_settings(connection, profile)}


@aira_route("POST", f"{BASE}/push/unsubscribe")
def push_unsubscribe(request: Request) -> tuple[int, dict[str, Any]]:
    with request.connect() as connection:
        profile = _person(request, connection)
        removed = store.unsubscribe(
            connection,
            store.keys_of(connection, profile),
            str(request.payload.get("endpoint") or ""),
        )
        return 200, {"removed": removed, **_settings(connection, profile)}


@aira_route("POST", f"{BASE}/push/test")
def push_test(request: Request) -> tuple[int, dict[str, Any]]:
    """A test notification to every browser this person turned notifications on in."""
    with request.connect() as connection:
        profile = _person(request, connection)
        keys = store.keys_of(connection, profile)
        queued = notify.push_to(
            connection,
            keys,
            {
                "title": "Monosage",
                "body": "Notifications are on for this browser.",
                "url": "/#/notifications",
                "tag": "test",
                "category": "",
            },
        )
    if not queued:
        raise ValueError("Turn on notifications in this browser first.")
    return 200, {"queued": queued}
