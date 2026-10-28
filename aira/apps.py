"""The app catalog: one account, many apps.

An *account* is the organization in the hosted build, or the local sign-in in the single-user build. An *app*
is a product the account can use: Careers (the job-search workspace), Developers (the search console, ``/v1``
and connected apps), the business apps (Sales, Marketing with Leads, Service, Commerce, Courses & Events) and the
everyday apps (Social, Health, Learn, Play, Planner, News, Videos, Life, AI, Stocks), with Ads among the business apps. Like the clouds of a CRM suite, one app holds several
sections: Commerce is Shop and Bookings, Marketing includes Leads, Social includes Family. The hosted build sells each app as a subscription the organization's admin
turns on from the account's Apps page; the local build has every app installed.

Both builds read this catalog: the navigation groups sections by app, the app switcher lists what the account
may open, and the hosted gateway refuses an app's routes when the organization has not subscribed to it.

Two kinds of app:

* **Core** apps: Careers keeps its data in a candidate's workspace (hosted), Developers in the organization's
  API workspace.
* **Industry** apps (one per kind of business, like the clouds of a CRM suite) keep their data per
  organization: a salon's bookings or a shop's orders belong to the business, and its owners, admins and team
  members work on them together. Each lives in ``aira/suite/<id>/`` with its routes in
  ``aira/suite/<id>/<id>_routes.py``, its page in ``auto_apply_assistant/views-<id>.js`` and its public pages
  (a booking page, a storefront, an invoice to pay) in ``auto_apply_assistant/public-<id>.js``. The developer
  guide is ``docs/suite-sdk.md``.
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
    category: str = "core"  # "core", "industry" or "life" (the switcher groups them)
    industry: str = (
        ""  # who an industry app is for, shown on the Apps page and in the switcher
    )
    scope: str = "workspace"  # "workspace" (a person's own data) or "org" (shared by the organization)
    highlights: tuple[str, ...] = field(
        default_factory=tuple
    )  # what it does, for the Apps page
    extra_features: tuple[str, ...] = field(
        default_factory=tuple
    )  # plan features the app carries when it is on (Marketing: "leads"; sending stays a plan feature)


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
API = App(
    id="api",
    name="Developers",
    tagline="APIs, keys and connected apps",
    icon="airaapi",
    home="airaapi",
    sections=("airaapi", "playground", "serp-kit", "developers"),
    route_prefixes=("/api/airaapi", "/v1"),
    feature="api.console",
    key="p",
    description="A console for 136 search engines with a shared cache, saved searches and monitors, a SerpApi-compatible endpoint for your own scripts, and the apps you connect to Aira.",
    addon=True,
    scope="org",
)


def _industry(
    id: str,
    name: str,
    tagline: str,
    key: str,
    industry: str,
    description: str,
    highlights: tuple[str, ...],
    *,
    sections: tuple[str, ...] = (),
    extra_features: tuple[str, ...] = (),
) -> App:
    """A business app: its sections (each an app package with routes under /api/<section>), sold as an add-on,
    data shared by the organization."""
    sections = sections or (id,)
    return App(
        id=id,
        name=name,
        tagline=tagline,
        icon=id,
        home=sections[0],
        sections=sections,
        route_prefixes=tuple(f"/api/{section}" for section in sections),
        extra_features=extra_features,
        feature=f"app.{id}",
        key=key,
        description=description,
        addon=True,
        category="industry",
        industry=industry,
        scope="org",
        highlights=highlights,
    )


STUDIO = _industry(
    "studio",  # the id stays: stored subscriptions, routes and the package are named after it
    "Sales",
    "Clients, deals, proposals and invoices",
    "1",
    "Agencies, freelancers, consultants and every business that sells to clients",
    "Win and bill client work: clients and contacts, a deals pipeline, proposals your clients accept online, "
    "projects with tasks and time tracking, invoices with a pay link, retainers and a revenue dashboard.",
    (
        "Clients and a deals pipeline",
        "Proposals clients accept from a link",
        "Invoices with a pay link and reminders",
        "Projects, tasks and billable time",
    ),
)
MARKETING = _industry(
    "marketing",
    "Marketing",
    "Leads, content, campaigns and forms",
    "2",
    "Marketers, creators and every business that needs customers",
    "Find and grow customers: leads from Google Maps, public requests, hiring signals, LinkedIn Sales Navigator "
    "and CSV with fit scoring and approved-only outreach, a content calendar with drafts for each channel, "
    "campaigns with tracked links, lead-capture forms, a link-in-bio page and newsletters.",
    (
        "Leads from Maps, LinkedIn and the web",
        "A content calendar with AI drafts",
        "Tracked links and campaign results",
        "Forms, a link-in-bio page and newsletters",
    ),
    sections=("marketing", "leads"),
    extra_features=("leads",),
)
SERVICE = _industry(
    "service",
    "Service",
    "Help your customers fast",
    "3",
    "Every business that answers customers",
    "Support that keeps customers: a shared inbox of cases from email, web forms and live chat, a help centre "
    "with answers customers find themselves, replies with saved answers, due times, and satisfaction ratings.",
    (
        "Cases from forms, chat and email",
        "A help centre customers search",
        "Live chat on your pages",
        "Due times and satisfaction ratings",
    ),
)
COMMERCE = _industry(
    "commerce",
    "Commerce",
    "Sell products and appointments",
    "4",
    "Shops, makers, salons, clinics, studios, tutors and consultants",
    "Sell online and fill your diary: a storefront with a cart, orders from placed to delivered, discount codes "
    "and stock, plus a booking page with services, deposits, team availability, reminders and packages.",
    (
        "A storefront with a cart",
        "A booking page customers use themselves",
        "Orders, deposits and reminders",
        "Discount codes, stock and packages",
    ),
    sections=("shop", "bookings"),
)
EXPERIENCES = _industry(
    "experiences",
    "Courses & Events",
    "Teach, sell tickets and check in",
    "5",
    "Educators, course creators, venues and event organisers",
    "Teach and gather people: courses with lessons, quizzes and certificates, enrolment pages, cohorts, plus "
    "event pages that sell tickets, door check-in, promo codes, waitlists and sponsors.",
    (
        "Course pages that enrol students",
        "Lessons, quizzes and certificates",
        "Event pages that sell tickets",
        "Check-in at the door",
    ),
    sections=("academy", "events"),
)

ADS = _industry(
    "ads",
    "Ads",
    "Advertise on videos, pay creators",
    "",
    "Every business that wants to be seen: shops, makers, services, apps and brands",
    "Reach people where they watch: prepaid campaigns with a budget, a bid per thousand views or per click, a "
    "schedule and targeting by video category, language and country; skippable video ads, banners on the watch page "
    "and sponsored reels, each reviewed before it runs; reports with impressions, clicks, CTR and spend. A share of "
    "every ad goes to the creator of the video it ran on.",
    (
        "Video ads, banners and sponsored reels",
        "Budgets, bids and pacing you control",
        "Reports with impressions, clicks and spend",
        "Creators earn from your ads",
    ),
)

INDUSTRY_APPS: tuple[App, ...] = (
    STUDIO,
    MARKETING,
    SERVICE,
    COMMERCE,
    EXPERIENCES,
    ADS,
)


def _life(
    id: str,
    name: str,
    tagline: str,
    key: str,
    description: str,
    highlights: tuple[str, ...],
    *,
    sections: tuple[str, ...] = (),
) -> App:
    """A life app: everyday use by every person in the account (a household locally, the organization's members
    hosted), each person with their own profile. Data is shared by the account and scoped per person in the app."""
    sections = sections or (id,)
    return App(
        id=id,
        name=name,
        tagline=tagline,
        icon=id,
        home=sections[0],
        sections=sections,
        route_prefixes=tuple(f"/api/{section}" for section in sections),
        feature=f"app.{id}",
        key=key,
        description=description,
        addon=True,
        category="life",
        industry="Everyone, every day",
        scope="org",
        highlights=highlights,
    )


CARE = _life(
    "care",  # not "health": /api/health is the server's own health check
    "Health",
    "Medicines, check-ups and wellbeing",
    "7",
    "Medicine reminders with a Taken button, appointments, blood pressure and other readings, a mood diary, "
    "caregivers who are told about a missed dose, and an emergency card you can share.",
    (
        "Medicine reminders",
        "Readings and a mood diary",
        "Caregiver alerts",
        "An emergency card",
    ),
)
LEARN = _life(
    "learn",
    "Learn",
    "Practise a little every day",
    "8",
    "Flashcards that come back just before you forget, daily streaks, quizzes, maths practice for children "
    "and decks made for you on any topic, for every age.",
    (
        "Flashcards that remember for you",
        "Daily streaks and goals",
        "Decks on any topic",
        "Family leaderboard",
    ),
)
PLAY = _life(
    "play",
    "Play",
    "Games, quizzes and things to watch",
    "9",
    "Brain games and a daily challenge, live family trivia, scores and personal bests, and a list of films, "
    "shows and books to enjoy together.",
    (
        "Brain games for every age",
        "Live family trivia",
        "A daily challenge",
        "Watch and read lists",
    ),
)
PLANNER = _life(
    "planner",
    "Planner",
    "Chores, shopping and bills",
    "",
    "Run the home together: chores with points for children, shopping lists everyone updates live, a simple "
    "budget, bills with reminders, and notes.",
    (
        "Shared shopping lists",
        "Chores with points",
        "Bills and budget",
        "Notes for the family",
    ),
)

NEWS = _life(
    "news",
    "News",
    "Today's headlines in your language",
    "",
    "The latest news every day in your own language and country: top stories and the topics you follow, "
    "headlines read aloud, saved articles and your own sources.",
    (
        "Headlines in your language",
        "Topics and keywords you follow",
        "Listen to the news",
        "A safe mode for children",
    ),
)

SOCIAL = _life(
    "social",
    "Social",
    "Friends, family, chats and photos",
    "6",
    "Aira's own social network: a profile, friends and followers, a feed with photos, stories, groups and "
    "business pages, private chats and a professional profile, and a private family space with a family chat, "
    "calendar, birthdays and a board for relatives, with blocking, reporting and safety for every age.",
    (
        "Chats with friends and family",
        "A feed of photos and news",
        "Groups, pages and a family space",
        "A professional profile",
    ),
    sections=("social", "family"),
)

VIDEOS = _life(
    "videos",
    "Videos",
    "Watch, upload and earn",
    "",
    "Watch videos, reels and short films from people everywhere, upload your own, and earn a share of the ads that "
    "run on them: subscriptions, playlists, comments, safe ratings for every age and a creator's earnings page.",
    (
        "Videos, reels and short films",
        "Upload from your phone or computer",
        "Safe for children and teens",
        "Earn from ads on your videos",
    ),
)

JOURNEY = _life(
    "journey",  # not "life": /api/life is the everyday apps' shared core (people, Today, pulse)
    "Life",
    "Goals, milestones and documents",
    "",
    "Your life in one place: goals with steps and check-ins, a life score, a timeline of the milestones of your "
    "life, and a vault for passports, certificates and insurance cards with reminders before they expire.",
    (
        "Goals with steps and progress",
        "A life score and what is coming up",
        "A timeline of your milestones",
        "A document vault with expiry reminders",
    ),
)

AI = _life(
    "ai",
    "AI",
    "Chat with Aira and manage its agents",
    "",
    "One assistant across all your apps: ask about your day, goals, medicines, chores, family or business and get "
    "a morning brief; ask it to add a goal, a task or a shopping item. One agent per app, each with the consent "
    "level you choose, approvals for what needs a yes, and a log of everything they did.",
    (
        "A chat that knows your apps",
        "A morning brief",
        "Agents with consent levels",
        "Approvals and an activity log",
    ),
)

STOCKS = _life(
    "stocks",
    "Stocks",
    "Markets, predictions and your portfolio",
    "",
    "Follow the markets and grow your money: live quotes, top gainers and losers, every stock's daily log of highs "
    "and lows, charts with indicators, predictions with their track record, a practice portfolio and your real "
    "holdings with profit and loss, price alerts, and paid subscriptions to the stock ideas you publish.",
    (
        "Top gainers, losers and each stock's log",
        "Predictions with their track record",
        "Practice trading and real holdings",
        "Earn from the stock ideas you publish",
    ),
)

LIFE_APPS: tuple[App, ...] = (
    SOCIAL,
    CARE,
    LEARN,
    PLAY,
    PLANNER,
    NEWS,
    VIDEOS,
    JOURNEY,
    AI,
    STOCKS,
)
APPS: tuple[App, ...] = (CAREERS, API, *INDUSTRY_APPS, *LIFE_APPS)
APP_IDS = tuple(app.id for app in APPS)
BY_ID = {app.id: app for app in APPS}
# Sections that belong to the account itself, shown under every app.
ACCOUNT_SECTIONS = ("settings", "security")

# Each app's own address, like mail.google.com and drive.google.com: /stocks opens Stocks. The web page is the same
# for every app (auto_apply_assistant/shell.js reads the address); /home is the account's home (Today).
APP_PATHS: dict[str, str] = {
    "careers": "careers",
    "api": "developers",
    "studio": "sales",
    "marketing": "marketing",
    "service": "service",
    "commerce": "commerce",
    "experiences": "courses",
    "ads": "ads",
    "social": "social",
    "care": "health",
    "learn": "learn",
    "play": "play",
    "planner": "planner",
    "news": "news",
    "videos": "videos",
    "journey": "life",
    "ai": "ai",
    "stocks": "stocks",
}
PAGE_PATHS: tuple[str, ...] = ("home", *APP_PATHS.values())
# The route pattern of those addresses (with or without a trailing slash).
PAGE_PATH_PATTERN = r"/(?:" + "|".join(PAGE_PATHS) + r")/?"


# Apps that became sections of a bigger app. Subscriptions, grants and packages stored under the old id belong
# to the new app.
LEGACY_IDS = {
    "shop": "commerce",
    "bookings": "commerce",
    "academy": "experiences",
    "events": "experiences",
    "leads": "marketing",
    "family": "social",
}


def owner_of(app_or_section: str) -> App | None:
    """The catalog app for an app id, an old app id (``"shop"``) or a section/package (``"bookings"``)."""
    key = str(app_or_section or "")
    if key in BY_ID:
        return BY_ID[key]
    if key in LEGACY_IDS:
        return BY_ID[LEGACY_IDS[key]]
    return app_of_section(key)


def current_id(app_id: str) -> str:
    """An app id as the catalog knows it now (``"shop"`` -> ``"commerce"``); unknown ids are returned as they are."""
    owner = owner_of(app_id)
    return owner.id if owner else app_id


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
            "path": APP_PATHS.get(app.id, app.id),
            "name": app.name,
            "tagline": app.tagline,
            "icon": app.icon,
            "home": app.home,
            "sections": list(app.sections),
            "feature": app.feature,
            "key": app.key,
            "description": app.description,
            "addon": app.addon,
            "category": app.category,
            "industry": app.industry,
            "scope": app.scope,
            "highlights": list(app.highlights),
            "extra_features": list(app.extra_features),
        }
        for app in APPS
    ]
