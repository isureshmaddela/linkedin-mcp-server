# Building a life app (everyday apps for every age)

Life apps are for everyday life: **Family** (social), **Health** (`care`), **Learn** (education), **Play**
(entertainment) and **Planner** (home). They use the same platform as the industry apps ([suite-sdk.md](suite-sdk.md):
`kit`, `public`, routes under `/api/<id>/`, the UI kit `Aira.suite`), plus a shared core for **people**,
**Today**, **live updates** and **accessibility** (`aira/suite/life.py`, `aira/suite/life_routes.py`,
`auto_apply_assistant/life.js`). Read suite-sdk.md first; this page lists what is different.

## People and privacy

Every request is for one person. Get them first, in every route:

```python
from aira.suite import life

with request.connect() as connection:
    me = life.current(request, connection)   # raises ProfileRequired/ProfileLocked (403 + code) -> the picker
    store.ensure(connection)
```

* `me["id"]` is the person (a household profile locally, the signed-in member hosted). Store `owner_id` (or
  `profile_id`) on every personal row and filter by it.
* The account's people: `life.profiles(connection)`, `life.public(p)` for what others may see, `life.name_of`.
  `me["age_group"]` is `child`, `teen`, `adult` or `senior`; `life.is_adult(me)`.
* **Privacy rules (enforce on the server):**
  * Health data is its owner's. Another person sees it only if the owner named them a caregiver.
  * Money (budget, bills) is for adults. Children see their own chores and points.
  * Family content (feed, chat, calendar, shopping lists, shared notes) is for everyone in the household.
  * Games and learning scores appear on family leaderboards (name and score only).
  * A child cannot create public links or post to a public board.
* In the hosted build the "household" is the organization's members. The data lives in the organization's
  shared database: never let one member read another member's private rows.

## Today and live updates

Register a Today card list and a pulse in your route module:

```python
@life.today("care")
def _today(connection, me):
    store.ensure(connection)
    lines = [{"text": "Metformin 500 mg", "sub": "08:00", "done": False,
              "action": life.action("Taken", f"/api/care/doses/{dose_id}/take")}]
    return [life.card("care", "Medicines today", lines=lines, tone="warn", empty="No medicines today.")]

@life.pulse("care")
def _pulse(connection, me, since):
    # badge: a number for the menu; alerts: things to show now as a big dialog (and read aloud).
    return {"badge": 2, "alerts": [{"id": f"dose:{dose_id}:{slot}", "title": "Time for your medicine",
            "body": "Metformin 500 mg", "tone": "warn", "speak": True,
            "actions": [life.action("Taken", f"/api/care/doses/{dose_id}/take")]}]}
```

* A card's action is a button on Today (`POST` with `body`); make that route idempotent.
* An alert's `id` must be stable: the browser shows each id once (snoozing hides it for 10 minutes).
* The pulse runs every 20 seconds for every signed-in person: keep it to a few indexed queries.
* Pages that change while open (chat, a shared shopping list, a live game) poll their own data:
  `const stop = Aira.suite.live(refresh, 3000)` (stops by itself when the person leaves the page). Give your list
  routes a `since=<ISO time>` parameter that returns only what changed, plus `now` to send next time.

## Made for every age (the UI bar)

* Big, clear targets: primary actions are buttons with words (not icon-only), at least 44 px high. The kit's
  `.btn` and `.life-check` already are; easy mode makes them bigger.
* Plain words: "Take medicine", not "Log adherence event". Short sentences. Dates as "Tomorrow at 9:00".
* One main thing per screen, tabs for the rest (`S.tabs`); every list has an empty state that says what to do.
* Never rely on colour alone; every icon has text; everything works with the keyboard and a screen reader.
* Children: simpler words, encouraging feedback (points, stars), no money, no public sharing.
* Seniors: nothing time-pressured by default (games offer a relaxed mode), large text works (the page zooms).
* Test your screens with `document.body.classList.add("easy", "text-xlarge")`: nothing may overflow or hide.

## Files, tests and checks

Same layout as suite-sdk.md: `aira/suite/<id>/` (`store.py`, `service.py`, `<id>_routes.py`),
`views-<id>.js`, `public-<id>.js`, `tests/test_suite_<id>.py`, `docs/apps/<id>.md`. Placeholders exist; index.html,
public.html and the catalog already list your app. Do not edit shared files (see suite-sdk.md; also `life.py`,
`life_routes.py`, `life.js`).

Tests use `tests/life_support.py` on the single-user server:

```python
from life_support import Household
household = Household(env, api)
mia = household.add("Mia", age_group="child", pin="2468")
mia.post("/api/learn/decks", {...}, expect=201)
```

Cover: the rules above (privacy between people, children's limits), Today cards and pulse alerts, `since`
updates, public pages if any, and the hosted build (`402 app_required` until
`cloud.billing.subscribe_app(org.owner, "<id>")`; every role may use it; two members never see each other's
private data; another organization is isolated). Stub the writing model in tests (a real Ollama runs here).
