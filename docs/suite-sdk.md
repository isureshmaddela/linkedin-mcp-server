# Building an industry app (the suite SDK)

Aira is one account with many apps (see [apps.md](apps.md)). **Industry apps** are one app per kind of business
(like the clouds of a CRM suite): Studio (agencies), Bookings (appointments), Shop (retail), Academy (education),
Events (entertainment), Marketing (growth). The platform sells each as a subscription and the superadmin can
grant it; the app itself only has to be its own domain. This guide is the contract every app follows.

## What the platform already does for you

| Concern | Where | What you do |
| --- | --- | --- |
| Catalog, switcher, keyboard (`g` + digit) | `aira/apps.py` (`INDUSTRY_APPS`), `app.js` (`APPS`, `NAV`) | nothing: your app is listed with id, name, icon, section `#/<id>` |
| Subscriptions and gating | `aira_cloud/plans.py` `APP_PRICING`, gateway `402 app_required` on `/api/<id>/*` | nothing |
| Hosted permissions | `aira_cloud/policy.py` `suite_rules()` | GET routes need `suite.read`, others `suite.manage`; mark a non-GET read with `HOSTED_RULES = {"handler": "read"}` |
| Where data lives | `request.connect()` | the person's database (single-user build) or the organization's shared database (hosted) |
| Shared tables | `aira/suite/kit.py` | business profile, activity feed, revenue ledger, settings, email outbox |
| Public pages for customers | `aira/suite/public.py`, `public.html`, `public.js` | register a page kind and its actions; write a renderer |
| Business hub (`#/hub`) | `views-hub.js`, `aira/suite/suite_routes.py` | register a checklist and a summary |
| UI kit | `suite-kit.js` (`Aira.suite`) | build every screen from it |

## Files of an app

```
aira/suite/<id>/__init__.py          docstring: what the app is for and its main objects
aira/suite/<id>/store.py             SCHEMA (CREATE TABLE IF NOT EXISTS ...), ensure(connection), CRUD
aira/suite/<id>/service.py           domain logic (pricing, availability, stock, progress, ...)
aira/suite/<id>/<id>_routes.py       HTTP routes (@aira_route), public pages (@public.page / @public.action),
                                     @kit.checklist / @kit.summary registrations
auto_apply_assistant/views-<id>.js   the app's screen (#/<id>, tabs in ?tab=)
auto_apply_assistant/public-<id>.js  renderers for the app's public pages (AiraPublic.register)
tests/test_suite_<id>.py             unit, single-user server and hosted gateway tests
docs/apps/<id>.md                    what the app does, for the people who use it
```

The route module **must** be named `<id>_routes.py` (route handler names are `airaapi__<id>_routes__<fn>`).
`aira/suite/__init__.py` already imports `aira.suite.<id>.<id>_routes` when it exists; `index.html` and
`public.html` already load `views-<id>.js` and `public-<id>.js`. Do not edit shared files (`app.js`,
`index.html`, `public.html`, `app.css`, `suite-kit.js`, `public.js`, `public.css`, `kit.py`, `public.py`,
`suite_routes.py`, `aira/apps.py`, `aira_cloud/*`). Put app-only styles in your view file as inline style objects,
or use the kit's classes. If you need a platform change, say so in your report instead of making it.

## Backend

```python
# aira/suite/bookings/store.py
from aira.suite import kit

SCHEMA = (
    """CREATE TABLE IF NOT EXISTS bk_services (
        id TEXT PRIMARY KEY, name TEXT NOT NULL, minutes INTEGER NOT NULL, price_cents INTEGER NOT NULL DEFAULT 0,
        active INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
)

def ensure(connection):
    kit.ensure_core(connection)          # the shared tables
    kit.ensure(connection, "bookings.v1", SCHEMA)
```

* Prefix your tables with a short app prefix (`st_`, `bk_`, `sh_`, `ac_`, `ev_`, `mk_`). Ids are `kit.new_id()`
  (32 hex), times `kit.now()` (ISO UTC), money integer cents (`kit.cents()` to parse, `kit.fmt_money()` to show),
  currency from `kit.business(connection)["currency"]`.
* Validate every input with `kit.text/email/phone/url/integer/cents/cents_value/choice/day/moment/flag/tags`. `kit.cents` parses an amount a person typed (`"12.50"` -> 1250); a value that is already cents (the UI kit's `type: "money"` fields send cents, so `price_cents` from the browser) goes through `kit.cents_value`. They raise
  `ValueError` with a sentence a person can act on; the server answers 400. Raise `LookupError` for 404 and
  `PermissionError` for 403.
* Routes:

```python
# aira/suite/bookings/bookings_routes.py
from aira.airaapi.http import Raw, Request, aira_route
from aira.suite import kit, public
from aira.suite.bookings import service, store

BASE = "/api/bookings"
ID = "([0-9a-f]{32})"

@aira_route("GET", f"{BASE}/services")
def services_list(request: Request):
    with request.connect() as connection:
        store.ensure(connection)
        return 200, {"services": store.list_services(connection)}
```

  Every route path starts with `/api/<id>/` (that is what the subscription gate covers). Write a log line for
  anything a person would want to see later (`kit.log(connection, "<id>", kind, title, ref_type=, ref_id=,
  amount_cents=, public=)`) and call `request.audit("<id>.<action>", type, id)` for changes.
* **Revenue.** When money is received (an invoice marked paid, an order paid, a booking deposit, a ticket sold, an
  enrolment paid), call `kit.record_revenue(connection, "<id>", source_type=, source_id=, amount_cents=, label=,
  customer=)`; recording the same source twice updates it. Call `kit.void_revenue` on refund or "mark unpaid".
  Aira does not process cards: customers pay through the business's own payment link
  (`kit.business(...)["payment_link"]`) or instructions, and the business marks the payment received (or the app
  records it when the business confirms). Never claim money moved that did not.
* **Email.** `kit.queue_email(connection, "<id>", kind, to=, subject=, body=, ref_type=, ref_id=,
  send_after=)` puts a message for a customer in the shared outbox (Business hub > Outbox). It waits for approval
  unless the business listed `kind` under automatic sending. Use stable kind names (`booking_confirmation`,
  `booking_reminder`, `invoice`, `payment_receipt`, `order_confirmation`, `ticket`, `enrolment`, ...).
* **Writing model.** `kit.draft(prompt, fallback_dict)` returns the model's JSON object (keys and types of the
  fallback) or the fallback marked `"source": "template"` when no model runs. Always ship a good template.
* **Public pages.** A link is made for one thing (`public.create_link(connection, "<id>", kind, ref_id,
  title=)`, idempotent while live; `public.link_for` reads it; `public.revoke` turns it off). Expose routes like
  `POST /api/<id>/<things>/<id>/link` and `DELETE ...` that call these. Register what the page shows and what a
  visitor may do:

```python
@public.page("bookings", "booking_page")
def booking_page(connection, link):
    store.ensure(connection)
    return {"services": [...], "slots": [...]}        # only what customers may see

@public.action("bookings", "booking_page", "book")
def book(connection, link, payload, visitor):
    store.ensure(connection)
    ...validate payload, re-check availability/capacity/stock server-side...
    kit.log(connection, "bookings", "booked", f"{name} booked {service}", public=True, ref_type="booking", ref_id=bid)
    connection.commit()
    return 201, {"booking": {...public fields...}}
```

  A visitor is anonymous: never return other customers' data, internal notes or settings; re-check every limit
  on the server (capacity, stock, slot still free, enrolment code); accept only the fields you need; cap text
  lengths. The platform rate-limits visitors per address (300 page views and 60 actions a minute) and returns 404 for revoked links or an app the
  organization no longer has.
* **Hub.** Register a checklist (what a new business should do first) and a summary (headline numbers):

```python
@kit.checklist("bookings")
def _checklist(connection):
    store.ensure(connection)
    return [kit.step("service", "Add a service", kit.count(connection, "SELECT COUNT(*) FROM bk_services") > 0, "Name, length and price"), ...]

@kit.summary("bookings")
def _summary(connection):
    store.ensure(connection)
    return {"cards": [{"label": "Bookings this week", "value": 12}, {"label": "No-shows", "value": 1, "tone": "warn"}]}
```

## Background work

Every few minutes the hosting server runs `aira.suite.background.run` for each account that uses the apps (the
single-user build from its scheduler thread, the hosted build as a `suite.tick` job per organization). It runs
each app's registered task and then sends approved messages whose `send_after` has come, through the business's
own mail server. Register a task for anything that must happen without anyone looking:

```python
from aira.suite import background

@background.task("care")
def _tick(connection):
    store.ensure(connection)
    return service.notify_missed(connection)   # e.g. tell caregivers about a missed dose, once
```

Tasks must be idempotent (they run again five minutes later) and quick. Message kinds in `kit.ALWAYS_SEND`
(safety notices a person set up, such as `missed_dose`) are approved on creation; everything else waits for
approval unless the business listed the kind under automatic sending.

## Frontend

`views-<id>.js` replaces the placeholder and defines `Aira.views.<id> = async (main, route) => { ... }`. Build it
from `Aira.suite` (see the contract at the top of `suite-kit.js`):

```js
(() => {
  const Aira = window.Aira, h = window.h, S = Aira.suite;
  const TABS = [{ id: "overview", label: "Overview" }, { id: "services", label: "Services" }];
  Aira.views.bookings = async (main, route) => {
    await S.business();                                   // currency for S.money
    const body = S.page(main, { title: "Bookings", subtitle: "Appointments that pay", actions: [S.button("New booking", ...)] });
    const tab = S.tabs(body, route, "bookings", TABS);
    const data = await S.api("bookings", "/overview");
    body.append(S.kpis([...]), S.section("Upcoming", S.table({ columns: [...], rows: data.upcoming, onRow: open })));
  };
})();
```

* Tabs live in the address (`#/<id>?tab=...`), so every screen can be linked and the back button works.
* Forms open with `S.edit({title, fields, values, onSave})`; money fields use `type: "money"` (cents in, cents out).
* Show the public link with `S.share({title, link, onCreate, onRevoke})` wherever a page can be shared.
* Every list has an empty state that says what to do next. Every number has a label. Build DOM with `h()` and
  `text`, never `innerHTML` with server data.

`public-<id>.js` registers renderers for the app's public pages:

```js
AiraPublic.register("bookings", "booking_page", (root, data, ctx) => {
  const { h } = ctx;
  root.append(h("h1", { text: data.title || "Book an appointment" }));
  // ... ctx.action("book", payload) posts to /api/public/<token>/book; show ctx.notice(...) on success.
  // ctx.paymentBox(amountCents, reference) shows the business's own payment link and instructions.
});
```

Something a page asks every few seconds (a chat window's new messages) is a read-only **query**, not an action:
`@public.query("service", "chat", "messages")` takes `(connection, link, params)` and must not change anything;
the page calls `ctx.query("messages", {after, secret})`, which is `GET /api/public/<token>/messages?after=..` with
a `secret` sent in the `Aira-Secret` header (never in the address). Queries count against the page-view limit
(300 a minute per visitor address), actions against the action limit (60).

Public pages use only `public.css` classes (`pub-card`, `pub-grid`, `pub-item`, `pub-btn`, `pub-field`,
`pub-form`, `pub-slots`, `pub-slot`, `pub-table`, `pub-total`, `pub-badge`, `pub-code`, `pub-progress`,
`pub-notice`, `pub-muted`) and must work on a phone.

## Tests

`tests/test_suite_<id>.py`, following `tests/test_suite_core.py`:

1. **Unit** tests of `service.py` and `store.py` on a scratch SQLite file (`kit.forget_schema()` and
   `public.set_index(public._MemoryIndex())` in the fixture): money, availability, stock, capacity, status
   changes, revenue recorded once, outbox messages queued.
2. **Single-user server** journeys with the real request handler (`env`/`api` fixtures from `test_careers`):
   create things through `/api/<id>/*`, make a public link, use it anonymously (`/api/public/<token>` and its
   actions), see the result in the app and in `/api/suite/home`. Use the `local_index` fixture pattern from
   `test_suite_core.py`.
3. **Hosted** checks with `cloud_support` (`build_org`, `sign_in`, `Browser`): the app's routes answer
   `402 app_required` until `cloud.billing.subscribe_app(org.owner, "<id>")`; owner/admin/coach may use it,
   auditor reads only, candidate gets 403; a public link works for an anonymous `Browser(cloud)` once
   subscribed; one organization never sees another's data.

Run with `uv run python -m pytest -q -p no:randomly tests/test_suite_<id>.py` (set `TMP`, `TEMP`, `TMPDIR`
and `--basetemp` to a folder on `D:` on this machine), then `uv run ruff format`, `uv run ruff check`,
`uv run ty check aira/` and `node --check` on your JS files.

## The bar

An app is done when a small business could run on it: the main objects with create, edit, delete, search and
filters; the workflow from first contact to money received; a public page customers really use; reminders or
confirmations through the outbox; headline numbers and a setup checklist in the hub; CSV export of the main list;
helpful empty states; and tests for every rule that protects the business or its customers.
