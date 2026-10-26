# Health

Health (app id `care`) keeps one person's medicines, readings, appointments and emergency information in one
place, and reminds them at the right time. It is made to be easy for older people and the people who look
after them. **It is not medical advice**: where Aira shows a note about a reading it is general information,
and it says so.

Open it from the app switcher (Everyday > Health), the bottom bar on a phone, or `g` then `8`.

## Today

The first tab is your day: each medicine due today with its time, and three big buttons:

* **Taken**: marks the dose. Pressing it twice changes nothing. If you keep a count of how many are left, it
  goes down by one.
* **Later**: reminds you again in 15 minutes.
* **Skip**: marks the dose skipped, with a reason if you want (it does not count as missed).

A dose is *due* from its time and *missed* if nobody marks it within an hour. The **Today** page of Aira shows
the same list with a Taken button, and while Aira is open a reminder pops up at the time ("Time for your
medicine"), read aloud if the person uses reading aloud or easy mode. Medicines taken only when needed get an
**I took …** button; if the last dose was too recent for the gap you set, Aira records it and shows a note.

The tab also shows how many doses were taken in the last seven days, the next appointment, a refill warning when
a medicine is running low, and **How do you feel today?** (five faces).

## Medicines

Add a medicine with its name, how much, its kind (tablet, liquid, inhaler…), its times of day (24-hour, in the
household's time zone from the Business hub), the days (every day, or for example Mon, Wed, Fri), start and
finish dates, a food note, how many are left and when to remind you to refill. Stopping a medicine keeps its
history. The tab shows the last 30 days: taken, skipped, missed and the share on track.

## Readings

Blood pressure (top, bottom and pulse), blood sugar (mg/dL or mmol/L), weight, temperature, oxygen, steps, water
and sleep, each with a time and a note, a small chart and a CSV download. A reading outside common ranges comes
with a gentle note (for example "above the usual range; talk to your doctor if this keeps happening").

## Diary

One entry a day: a mood from 😢 to 😄, anything you noticed (headache, tired…) and a note. **The diary is
private**: caregivers cannot read it.

## Appointments

Who with, when, where, what for, and questions to ask. Aira reminds you the day before and two hours before.

## Emergency card

Blood type, allergies, conditions, the medicines you take now (filled in for you), your doctor and up to three
emergency contacts. Share it as a link (only the card is shown, with phone numbers that can be tapped to call)
or print it for a wallet or the fridge. An adult in the household can keep a child's card; a child cannot share
a link.

## Caregivers

Name the people you trust (someone in the household, or anyone by email). They can see your medicines, doses,
readings, appointments and card, but not your diary, and they cannot change anything. If you miss a dose they
get a reminder in Aira and an email through the Business hub outbox (approve it there, or turn on automatic
sending for `missed_dose` under Business hub > Email). People who made you their caregiver appear under **People
you care for**, with how their day is going and a button to see their health.

## Privacy

Each person's health is their own. In the hosted build every member of an organization uses the same database,
and Aira still shows a member's health only to them and the caregivers they named; platform staff on a support
visit never see it.

## For developers

`aira/suite/care/` (`store.py` tables `cr_*`, `service.py` rules, `care_routes.py` routes under `/api/care`, the
`emergency_card` public page, Today cards and the pulse), `auto_apply_assistant/views-care.js`,
`public-care.js`, tests in `tests/test_suite_care.py` and `tests/test_suite_care_ui.py`.
