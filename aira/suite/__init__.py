"""The industry apps: one app per kind of business, run by the same account, sold as subscriptions.

Every app lives in its own package (``aira/suite/<id>/``) and keeps its data in the account's database: the
person's own database in the single-user build, the organization's shared database in the hosted build. The
shared pieces are here:

* :mod:`aira.suite.kit` - validation, money, the business profile, the activity feed, the revenue ledger,
  setup checklists, CSV export, the email outbox and the writing model.
* :mod:`aira.suite.public` - public links: pages a business shares with its customers (a booking page, a
  storefront, an invoice to pay) and the actions customers take on them, without signing in.
* :mod:`aira.suite.suite_routes` - the Business hub (``/api/suite/*``) across every app, and the public page
  routes (``/p/<token>``, ``/api/public/<token>``).

The developer guide is ``docs/suite-sdk.md``.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger("aira.suite")


class Gateway(Protocol):
    """What the hosting server lends the apps: the writing model."""

    def model(self, prompt: str) -> str | None:
        """The model's JSON reply to ``prompt`` as text, or None when no model is available."""
        ...


class _NoGateway:
    def model(self, prompt: str) -> str | None:
        return None


_gateway: Gateway = _NoGateway()


def set_gateway(gateway: Gateway) -> None:
    global _gateway
    _gateway = gateway


def gateway() -> Gateway:
    return _gateway


# The address customers use to reach public pages (``https://app.example.com``). The hosted build sets it from
# its configured base URL; the single-user build leaves it unset and links use the address of the request.
_public_origin: Callable[[], str] | None = None


def set_public_origin(origin: Callable[[], str] | None) -> None:
    global _public_origin
    _public_origin = origin


def public_origin() -> str:
    try:
        return str(_public_origin() if _public_origin else "").rstrip("/")
    except Exception:  # noqa: BLE001 - a link falls back to the request's address
        return ""


# Who is making the request, when the hosting server knows it better than the app (the hosted build: the
# signed-in member, ``{"member_id", "name", "email", "role"}``). None in the single-user build, where the person is
# the profile the browser picked (aira.suite.life).
_actor_resolver: Callable[[], dict[str, Any] | None] | None = None


def set_actor_resolver(resolver: Callable[[], dict[str, Any] | None] | None) -> None:
    global _actor_resolver
    _actor_resolver = resolver


def actor() -> dict[str, Any] | None:
    if _actor_resolver is None:
        return None
    try:
        return _actor_resolver()
    except Exception:  # noqa: BLE001 - no actor means the single-user rules apply
        logger.debug("No actor for this request", exc_info=True)
        return None


# Whether the account has an app (the hosted build asks the organization's subscriptions; locally every app
# is installed).
_app_filter: Callable[[str], bool] | None = None


def set_app_filter(app_filter: Callable[[str], bool] | None) -> None:
    global _app_filter
    _app_filter = app_filter


def app_enabled(app_id: str) -> bool:
    if _app_filter is None:
        return True
    try:
        return bool(_app_filter(app_id))
    except Exception:  # noqa: BLE001 - an unknown answer hides the app's cards rather than failing the page
        logger.debug("Could not tell whether %s is enabled", app_id, exc_info=True)
        return False


# The industry apps' route modules, imported by aira.airaapi.http.load_route_modules. A module that does not
# exist yet is skipped, so an app can be added one at a time.
INDUSTRY_PACKAGES = (
    "studio",
    "bookings",
    "shop",
    "academy",
    "events",
    "marketing",
    "service",
    "ads",
    "sites",
)
# The life apps (everyday use by every person; aira.suite.life is their shared core).
LIFE_PACKAGES = (
    "family",
    "care",
    "learn",
    "play",
    "planner",
    "news",
    "social",
    "videos",
    "journey",
    "ai",
    "stocks",
)
APP_PACKAGES = (*INDUSTRY_PACKAGES, *LIFE_PACKAGES)
ROUTE_MODULES = (
    "aira.suite.suite_routes",
    "aira.suite.life_routes",
    *(f"aira.suite.{name}.{name}_routes" for name in APP_PACKAGES),
)


FAILED: dict[str, str] = {}  # app route modules that could not be imported, and why


def load_apps() -> list[str]:
    """Import every app's route module (registering its routes, public pages and checklist); returns the loaded.

    An app that fails to import is logged and left out, so the other apps keep working; the Business hub must
    import. ``AIRA_SUITE_STRICT=1`` (the tests set it) raises instead."""
    import os

    loaded = []
    for name in ROUTE_MODULES:
        try:
            importlib.import_module(name)
        except ModuleNotFoundError as error:
            if error.name and name.startswith(error.name):
                continue  # the app is not built yet
            if name in ROUTE_MODULES[:2] or os.getenv("AIRA_SUITE_STRICT") == "1":
                raise
            FAILED[name] = repr(error)
            logger.exception("Industry app %s could not be loaded", name)
            continue
        except Exception as error:
            if name in ROUTE_MODULES[:2] or os.getenv("AIRA_SUITE_STRICT") == "1":
                raise
            FAILED[name] = repr(error)
            logger.exception("Industry app %s could not be loaded", name)
            continue
        loaded.append(name)
    return loaded


def describe() -> dict[str, Any]:
    return {"apps": list(APP_PACKAGES), "loaded": load_apps()}
