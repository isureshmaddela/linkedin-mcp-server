"""Routes of the life apps' shared core (``/api/life/*``): who is using Aira, their preferences, Today and pulse.

Loaded by :func:`aira.suite.load_apps`. Each life app registers its Today cards and pulse with
:mod:`aira.suite.life`; these routes ask every app the account has.
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from aira import apps as app_catalog
from aira import suite
from aira.airaapi.http import Request, aira_route
from aira.suite import kit, life

BASE = "/api/life"
ID = "([0-9a-f]{32})"
_PIN_TRIES: dict[
    str, list[float]
] = {}  # profile id -> recent wrong PIN times (a shared device, not a server)
PIN_LIMIT, PIN_WINDOW = 5, 300


def _open(request: Request) -> Any:
    connection = request.connect()
    life.ensure(connection)
    return connection


def _apps() -> list[str]:
    """The life app sections this account has (Social brings Family too), by package id: Today and pulse ask
    each package; the hosted build asks the organization's subscriptions."""
    return [
        section
        for app in app_catalog.LIFE_APPS
        if suite.app_enabled(app.id)
        for section in app.sections
    ]


def _local_now(connection: Any) -> datetime:
    zone = kit.business(connection).get("timezone") or "UTC"
    try:
        return datetime.now(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        return datetime.now(ZoneInfo("UTC"))


@aira_route("GET", f"{BASE}/profiles")
def profiles_list(request: Request) -> tuple[int, dict[str, Any]]:
    """Everyone in the household, for the "who's using Aira?" picker (no profile needed to ask)."""
    with _open(request) as connection:
        everyone = life.profiles(connection)
        if not everyone and suite.actor() is None:
            life.current(
                request, connection
            )  # the account's first person, made from the sign-in
            everyone = life.profiles(connection)
        return 200, {
            "profiles": [life.public(p) for p in everyone],
            "hosted": suite.actor() is not None,
        }


@aira_route("POST", f"{BASE}/unlock")
def unlock(request: Request) -> tuple[int, dict[str, Any]]:
    """Choose a profile (with its PIN when it has one); answers the value for the X-Aira-Profile header."""
    profile_id = str(request.payload.get("profile_id") or "")
    with _open(request) as connection:
        profile = life.get(connection, profile_id)
        if profile.get("pin_hash"):
            now = time.monotonic()
            tries = [t for t in _PIN_TRIES.get(profile_id, []) if now - t < PIN_WINDOW]
            if len(tries) >= PIN_LIMIT:
                raise PermissionError(
                    "Too many wrong PINs. Wait five minutes and try again."
                )
            if not life.check_pin(profile, str(request.payload.get("pin") or "")):
                _PIN_TRIES[profile_id] = [*tries, now]
                raise PermissionError("That PIN is not right.")
            _PIN_TRIES.pop(profile_id, None)
            header = f"{profile_id}.{life.unlock_token(profile)}"
        else:
            header = profile_id
        return 200, {"header": header, "profile": life.mine(profile)}


@aira_route("GET", f"{BASE}/me")
def me(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        profile = life.current(request, connection)
        return 200, {
            "profile": life.mine(profile),
            "profiles": [life.public(p) for p in life.profiles(connection)],
            "can_manage": life.can_manage_household(profile),
            "hosted": suite.actor() is not None,
            "apps": _apps(),
        }


@aira_route("POST", f"{BASE}/prefs")
def prefs_save(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        profile = life.current(request, connection)
        return 200, {"prefs": life.save_prefs(connection, profile, request.payload)}


@aira_route("POST", f"{BASE}/profiles")
def profiles_add(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        actor = life.current(request, connection)
        if suite.actor() is not None:
            raise PermissionError(
                "People join through your organization's invitations."
            )
        added = life.add(connection, actor, request.payload)
    request.audit("life.profile_add", "profile", added["id"])
    return 201, {"profile": life.public(added)}


@aira_route("PATCH", f"{BASE}/profiles/{ID}")
def profiles_update(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        actor = life.current(request, connection)
        updated = life.update(
            connection, actor, request.match.group(1), request.payload
        )
        return 200, {
            "profile": life.mine(updated)
            if updated["id"] == actor["id"]
            else life.public(updated)
        }


@aira_route("DELETE", f"{BASE}/profiles/{ID}")
def profiles_remove(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        actor = life.current(request, connection)
        life.remove(connection, actor, request.match.group(1))
    request.audit("life.profile_remove", "profile", request.match.group(1))
    return 200, {"removed": True}


@aira_route("POST", f"{BASE}/profiles/{ID}/pin")
def profiles_pin(request: Request) -> tuple[int, dict[str, Any]]:
    with _open(request) as connection:
        actor = life.current(request, connection)
        updated = life.set_pin(
            connection,
            actor,
            request.match.group(1),
            str(request.payload.get("pin") or ""),
        )
        header = None
        if updated["id"] == actor["id"]:
            header = (
                f"{updated['id']}.{life.unlock_token(updated)}"
                if updated.get("pin_hash")
                else updated["id"]
            )
    request.audit("life.profile_pin", "profile", request.match.group(1))
    return 200, {"profile": life.public(updated), "header": header}


@aira_route("GET", f"{BASE}/today")
def today(request: Request) -> tuple[int, dict[str, Any]]:
    """The person's day: a greeting and one card per thing that matters now, from every life app they have."""
    with _open(request) as connection:
        profile = life.current(request, connection)
        moment = _local_now(connection)
        cards: list[dict[str, Any]] = []
        for app_id in _apps():
            fn = life.TODAY.get(app_id)
            if fn is None:
                continue
            try:
                cards.extend(fn(connection, profile))
            except Exception:  # noqa: BLE001 - one app's card must not break the day
                suite.logger.exception("Today cards for %s failed", app_id)
        # Family shows the household's dates (with "Send wishes"); without it, Today lists the birthdays itself.
        family_dates = "family" in _apps() and "family" in life.TODAY
        birthdays = [] if family_dates else _birthdays(connection, moment)
        return 200, {
            "profile": life.mine(profile),
            "greeting": f"{life.greeting(moment.hour)}, {profile['name']}",
            "salutation": life.greeting(
                moment.hour
            ),  # translated on screen; the name is shown as written
            "date": moment.strftime("%A %d %B %Y"),
            "time": moment.strftime("%H:%M"),
            "cards": cards,
            "birthdays": birthdays,
            "apps": _apps(),
        }


def _birthdays(connection: Any, moment: datetime) -> list[dict[str, Any]]:
    """Household birthdays in the next 14 days (from the profiles)."""
    out = []
    for person in life.profiles(connection):
        if not person["birthday"]:
            continue
        try:
            born = datetime.strptime(person["birthday"], "%Y-%m-%d").date()
        except ValueError:
            continue
        today_date = moment.date()
        try:
            next_one = born.replace(year=today_date.year)
        except ValueError:  # 29 February
            next_one = born.replace(year=today_date.year, day=28)
        if next_one < today_date:
            try:
                next_one = next_one.replace(year=today_date.year + 1)
            except ValueError:
                next_one = next_one.replace(year=today_date.year + 1, day=28)
        days = (next_one - today_date).days
        if days <= 14:
            out.append(
                {
                    "profile_id": person["id"],
                    "name": person["name"],
                    "emoji": person["emoji"],
                    "date": next_one.isoformat(),
                    "in_days": days,
                    "age": next_one.year - born.year if born.year > 1900 else None,
                }
            )
    return sorted(out, key=lambda b: b["in_days"])


@aira_route("GET", f"{BASE}/pulse")
def pulse(request: Request) -> tuple[int, dict[str, Any]]:
    """What changed since ``since`` (ISO time): badges per app and alerts to show now (live updates)."""
    since = kit.moment(request.q("since"), "since") if request.q("since") else ""
    with _open(request) as connection:
        profile = life.current(request, connection)
        apps: dict[str, Any] = {}
        alerts: list[dict[str, Any]] = []
        for app_id in _apps():
            fn = life.PULSE.get(app_id)
            if fn is None:
                continue
            try:
                with kit.origin_of(
                    request
                ):  # links in anything an app emails from here
                    result = fn(connection, profile, since) or {}
            except Exception:  # noqa: BLE001
                suite.logger.exception("Pulse for %s failed", app_id)
                continue
            apps[app_id] = {"badge": int(result.get("badge") or 0)}
            for alert in result.get("alerts") or []:
                alerts.append({**alert, "app": app_id})
        return 200, {"now": kit.now(), "apps": apps, "alerts": alerts[:20]}
