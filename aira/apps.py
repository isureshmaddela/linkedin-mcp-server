"""The app catalog: one account, many apps.

An *account* is the organization in the hosted build, or the local sign-in in the single-user build. An *app*
is a product the account can use: Careers (the job-search workspace), Leads (sales lead generation) and API
(the search console and ``/v1``). The hosted build sells each app as a subscription the organization's admin
turns on from the account's Apps page; the local build has every app installed.

Both builds read this catalog: the navigation groups sections by app, the app switcher lists what the account
may open, and the hosted gateway refuses an app's routes when the organization has not subscribed to it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class App:
    id: str
    name: str
    tagline: str
    icon: str
    home: str  # the route id the switcher opens
    sections: tuple[str, ...]  # route ids (nav items) that belong to this app
    route_prefixes: tuple[str, ...]  # API path prefixes the app owns (hosted gating)
    feature: str  # the plan feature that includes the app ("" = part of every plan)
    key: str  # the `g` then <key> chord that opens the app
    description: str = ""
    addon: bool = (
        False  # sold as an add-on to the plan (price per seat) rather than included
    )
    extra_sections: tuple[str, ...] = field(
        default_factory=tuple
    )  # sections other builds add (hosted)


CAREERS = App(
    id="careers",
    name="Careers",
    tagline="Find, apply and track",
    icon="careers",
    home="dashboard",
    sections=(
        "dashboard",
        "jobs",
        "applications",
        "interviews",
        "recruiters",
        "documents",
        "analytics",
        "careers",  # the research workspace (Google Jobs search, reports, alerts)
    ),
    route_prefixes=(
        "/api/jobs",
        "/api/applications",
        "/api/interviews",
        "/api/recruiters",
        "/api/documents",
        "/api/analytics",
        "/api/careers",
        "/api/autopilot",
        "/api/schedules",
        "/api/email",
        "/api/inbox",
        "/api/messages",
        "/api/profile",
        "/api/resume",
        "/api/tasks",
        "/api/notifications",
        "/api/linkedin",
        "/api/search",
    ),
    feature="",
    key="c",
    description="Job search across LinkedIn, Google Jobs and company boards, automatic applications with a person in the loop, interviews, recruiters, documents and analytics.",
    extra_sections=("onboarding", "activity", "coach", "coach-queue"),
)
LEADS = App(
    id="leads",
    name="Leads",
    tagline="Find people who need what you sell",
    icon="leads",
    home="leads",
    sections=("leads",),
    route_prefixes=("/api/leads",),
    feature="leads",
    key="l",
    description="Leads from Google Maps, public requests, hiring signals, LinkedIn Sales Navigator and CSV; fit scoring, website checks, approved-only email and call assist.",
    addon=True,
)
API = App(
    id="api",
    name="API",
    tagline="Search engines and keys",
    icon="airaapi",
    home="airaapi",
    sections=("airaapi", "playground", "serp-kit"),
    route_prefixes=("/api/airaapi", "/v1"),
    feature="api.console",
    key="p",
    description="A console for 136 search engines with a shared cache, saved searches and monitors, and a SerpApi-compatible endpoint for your own scripts.",
    addon=True,
)

APPS: tuple[App, ...] = (CAREERS, LEADS, API)
APP_IDS = tuple(app.id for app in APPS)
BY_ID = {app.id: app for app in APPS}
# Sections that belong to the account itself, shown under every app.
ACCOUNT_SECTIONS = ("settings", "security")


def app_of_section(section: str) -> App | None:
    for app in APPS:
        if section in app.sections or section in app.extra_sections:
            return app
    return None


def app_of_path(path: str) -> App | None:
    """The app an API path belongs to (None for account, auth and platform routes)."""
    for app in APPS:
        for prefix in app.route_prefixes:
            if (
                path == prefix
                or path.startswith(prefix + "/")
                or path.startswith(prefix + "?")
            ):
                return app
    return None


def catalog() -> list[dict[str, object]]:
    """The catalog as the front end and the Apps page receive it."""
    return [
        {
            "id": app.id,
            "name": app.name,
            "tagline": app.tagline,
            "icon": app.icon,
            "home": app.home,
            "sections": list(app.sections),
            "feature": app.feature,
            "key": app.key,
            "description": app.description,
            "addon": app.addon,
        }
        for app in APPS
    ]
