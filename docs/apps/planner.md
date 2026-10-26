# Planner

Run the home together: chores, shopping, money and notes for everyone in the household, at any age.
Open it from the app switcher (Everyday > Planner), or from Today with the Home button and the Planner tile.

## My day

What you need to do today, with a big **Do it** button for each task, anything overdue, what is coming up this
week, bills due soon (adults) and your shopping lists. Type into "Add something to do today" for a quick task.
Your chores also appear on **Today** with a **Done** button, and a task with a time pops up as a reminder when
the time comes (it can be read aloud in easy mode).

## Chores

* A task is for one person, **anyone** (whoever does it first) or **everyone** (each person ticks it for
  themselves, like "brush teeth").
* Give it a day and, if you like, a time for a reminder.
* Repeats: every day, every weekday, every week on chosen days, every month on a day (the 31st becomes the
  last day of shorter months), or every few days. When a repeating task is done, the next one appears; doing it
  twice by mistake changes nothing, and **Not done** puts it back and takes its points away.
* Adults see everyone's tasks. Children and teens see their own, "anyone" and "everyone" tasks, can add tasks for
  themselves, and cannot give themselves points.

## Rewards

Adults set **points** on chores and add **rewards** ("Pick the film = 20 points"). Children earn points by doing
chores and tap **Ask for this**; an adult approves (the points are spent) or says not now. Everyone sees their
own points history; adults see everyone's points.

## Shopping

* Several lists (Groceries, Pharmacy...). Items are sorted by aisle automatically (fruit and vegetables, bakery,
  dairy, meat and fish, cupboard, frozen, drinks, household, pharmacy).
* Tap **Get** to tick an item; ticked items move to "In the basket" and **Clear ticked items** empties it.
* **Your usual items** are the things you buy most, one tap to add again.
* Lists update by themselves: two people shopping in different shops see each other's ticks within a few seconds.
* **Share this list** (adults): a link a relative can open without an account to see the list, tick items and add
  things, with their name. Turn the link off at any time.

## Budget (adults)

Money in and money spent per month, a monthly budget per category (tap a category to set it), what is left or
over, a chart of where the money went, and a CSV download. Children and teens cannot open it.

## Bills (adults)

Monthly, yearly or one-off bills with the usual amount and due day. **Paid** records the payment once and adds it
to the budget as spending; **Undo last payment** takes it back. Bills due in the next three days show on Today
with a **Paid** button, and a bill due today pops up as a reminder (not for bills paid automatically).

## Notes

Shared family notes (the wifi password, the plumber's number) or private notes only you can see. Notes can be
pinned and can have a checklist you tick off.

## Privacy

Budget and bills are for adults only, checked on the server. Private notes are only their writer's. Children
see only their own chores and points. In the hosted build the household is your organization's members, and
another organization never sees your Planner.

## For developers

`aira/suite/planner/` (`store.py` tables `pn_*`, `service.py` rules, `planner_routes.py` routes under
`/api/planner/*`, Today cards, pulse, the public page kind `shopping_list` with actions `tick`, `add` and
`refresh`), `auto_apply_assistant/views-planner.js`, `public-planner.js`, tests in `tests/test_suite_planner.py`
and `tests/test_suite_planner_ui.py`.
