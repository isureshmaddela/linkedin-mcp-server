# Health

Health (app id `care`) keeps one person's medicines, readings, appointments and emergency information in one
place, reminds them at the right time, makes a one-page report for the doctor, and helps people who look after
others **earn from it** (Care work). It is made to be easy for older people and the people who look after them.
**It is not medical advice**: where Aira shows a note about a reading it is general information, and it says so.

Open it from the app switcher (Everyday > Health), the bottom bar on a phone, or `g` then `7`. The tabs are
Today, Medicines, Readings, Diary, Appointments, Doctor report, Emergency card, Caregivers and Care work.

## Time zone

Medicine times are times of day where the person lives. The first time someone opens Health, Aira keeps their
device's time zone (shown on the Medicines tab: "Times are Asia/Kolkata time", with **Change**). A time zone chosen
in the Business hub profile wins; UTC there does not count as a choice, because saving the hub profile without
touching the zone stores UTC. In the hosted build the zone is the organization's.

The zone sets everyone's reminder times, so once it is set only the people who run the account may change it: at
home the owner or another adult (a child sees the zone but no **Change** button, and the server refuses), in the
hosted build the organization's owner or an admin. The first time, anyone's browser may fill it in.

## Today

The first tab is your day: each medicine due today with its time, and three big buttons:

* **Taken**: marks the dose. Pressing it twice changes nothing. If you keep a count of how many are left, it
  goes down by one.
* **Later**: reminds you again in 15 minutes.
* **Skip**: marks the dose skipped, with a reason if you want (it does not count as missed).

A dose is *due* from its time and *missed* if nobody marks it within an hour. The **Today** page of Aira shows
the same list with a Taken button, and while Aira is open a reminder pops up at the time ("Time for your
medicine"), read aloud if the person uses reading aloud or easy mode. Medicines taken only when needed get an
**I took …** button; if the last dose was too recent for the gap you set, Aira records it and says how long ago
the last one was ("25 minutes ago").

The tab also shows how many doses were taken today and in the last seven days ("Nothing due" when no medicine has
a time today), the next appointment, a **Time to refill** warning with an **I got a refill** button, **How do you
feel today?** (five faces), **Add a reading** (it asks what you measured first) and a link to the doctor report.

## Medicines

Add a medicine with its name, how much, its kind (tablet, liquid, inhaler…), its times of day (24-hour), the days
(every day, or for example Mon, Wed, Fri), start and finish dates, a food note, how many are left and when to be
told to refill. Each medicine shows:

* **How long it lasts**: "66 left · lasts about 33 days (until Sun 29 Nov)", worked out from its times and days.
* **Refill soon**: when the count reaches the number you chose, or, if you chose none, when fewer than 5 days
  are left. Today, the Today page and a reminder say so.
* **I got a refill**: adds the new box to the count (it starts a count if you kept none) and remembers when and
  where ("Last refill 27 Oct: +60, City Pharmacy").
* How it went in the last 30 days (taken of due, and the share on time).

**Stop** keeps the history (a medicine never taken is simply removed); a stopped medicine can be started again
with Change, or **Delete**d with its history. A stopped medicine stays in the numbers and in the doctor report
(marked "(stopped)") for the days it was taken, because a medicine stopped for side effects is exactly what a
doctor needs to see.

Aira keeps each medicine's schedule over time. Changing its times or days counts from now on: doses taken at the
old time stay taken, and the days a medicine was stopped are never counted as missed. Below the medicines: the last 30 days (taken, skipped, missed, on
track), a list you can filter by medicine and by what happened, and **Download** (CSV).

## Readings

Blood pressure (top, bottom and pulse), blood sugar (mg/dL or mmol/L), weight, temperature, oxygen, steps, water
and sleep, each with a time (now, or when you measured it; never in the future) and a note. For each kind: the average, lowest and
highest of the last 90 days, how many were outside the usual range, a chart of the last 14 (a blood pressure is a
bar from the bottom number to the top one; readings outside the usual range are marked in amber) and the list.
A reading outside common ranges comes with a gentle note (for example "above the usual range; talk to your
doctor if this keeps happening"). Deleting a reading asks first. **Download all** gives a CSV.

## Diary

One entry a day (today or an earlier day): a mood from 😢 to 😄, anything you noticed (headache, tired…) and a
note. **The diary is
private**: caregivers cannot read it, and it goes into the doctor report only if you tick the box. Deleting an
entry asks first.

## Appointments

Who with, when, where, what for, and questions to ask. Aira reminds you the day before and two hours before.
**Add to my calendar** downloads the appointment as a calendar file (.ics) with the same two reminders, for a phone
or computer calendar.

## Doctor report

One page to take to a visit, for the last 30, 90 or 180 days: allergies and conditions, how many doses were taken
on time (and per medicine, with how often a "when needed" medicine was used), each kind of reading (how many,
average, lowest, highest, how many outside the usual range), the questions for the next appointment, and, only if
you tick **Include my mood diary**, how you felt (days written, average mood, low days, what you noticed most).

* **Print** prints just the report. **Download** gives it as a CSV.
* **Make a link** shares it with a doctor: anyone with the link can read it for **14 days**, then it stops working.
  **Update to these settings** changes the period or the diary choice and gives it another 14 days; **Turn off**
  ends it sooner. Children cannot share a report.
* Caregivers can read and print the report of the person they look after, never with the diary, and cannot share it.

## Emergency card

Blood type, allergies, conditions, the medicines you take now (filled in for you), your doctor and up to three
emergency contacts. Share it as a link (only the card is shown, with phone numbers that can be tapped to call) or
**Print the card** for a wallet or the fridge. An adult in the household can keep a child's card; a child cannot
share a link.

## Caregivers

Name the people you trust (someone in the household, or anyone by email). They can see your medicines, doses,
readings, appointments, report and card, but not your diary, and they cannot change anything. If you miss a dose
they get a reminder in Aira and an email through the Business hub outbox (sent at once: a missed-dose notice is a
safety message). People who made you their caregiver appear under **People you care for**, with how their day is
going and a button to see their health.

## Care work: earn by looking after people

For home carers, visiting nurses, and anyone paid to help someone every day. Only adults see this tab.

1. **Add a client**: the person you look after, or the family member who pays, with your rate for an hour, an
   email for statements and private notes.
2. **Record a visit** after it happens: the day, the start time if you like, how long (in hours, for example 1.5)
   and what you did ("medicines, lunch, a walk"). Notes on a visit are private. A visit cannot be in the future.
3. **Bill now** turns every visit not billed yet (up to a day you choose) into a **statement** (CARE-0001,
   CARE-0002…; a number is never reused). Billed visits are locked; deleting an unpaid statement frees them.
4. The client opens the statement's link: the visits (day, how long, what was done, amount), the total, and **How
   to pay** from your Business hub profile (your payment link or bank details; Aira does not take payments). They
   can press **I have paid** with a note (for example a bank or UPI reference): you get a reminder ("A client says
   they paid", with their note) and the statement shows the note, so you can check it against your account.
   **Email it** puts the link in an email to the client in the Business hub outbox, waiting for your approval. The
   statement says "Email waiting for approval" until the email has gone (then "Sent"), and while it waits there is
   no second **Email it**, so the client never gets two.
5. **Mark paid** when the money arrives: it counts once in your earnings and in the Business hub's revenue
   (as Health). **Not paid after all** takes it back out.

The tab shows hours this month, paid this month, waiting to be paid and not billed yet, a reminder to add how
clients pay you if you have not, the clients, the visits of the last 90 days (filter by client, **Download all
visits** as CSV) and the statements. Today shows a **Care work** card with the same money. A client with visits
is kept as a past client when removed; a past client's **Delete for good** removes them with their visits and
statements (not while a statement waits to be paid), and the money marked paid stays in your earnings.

## In the Aira frame

Health lives at `/health`, with its own name and logo in the bar, like the other Aira apps.

* **+ New** (above the menu) offers **Add a medicine**, **Add a reading** (it asks what you measured), **Add an
  appointment**, **Write in the diary** (the diary's form takes focus) and, for adults, **Record a care visit**
  (or **Add a client** first when there is none). Each opens the same form as the button on its tab, always for
  your own health, even while you look at someone you care for.
* **The search box** across the top finds your medicines (by name, dose, food note or notes), appointments (who,
  where, what for, questions), the kinds of reading ("blood" finds blood pressure and blood sugar), diary days
  (by note or symptom, the last year), care clients and people (those you look after, and your caregivers), up to
  six as you type. Choosing one opens its tab with that record outlined and focused. **Enter** shows every match,
  grouped by kind ("Results for …"). While you look at someone you care for, it searches what you may see of
  theirs: their medicines, appointments and readings, never their diary.
* **The menu's folders** are Health's tabs: Today, Medicines, Readings, Diary, Appointments, Doctor report,
  Emergency card, Caregivers and Care work (only what the person may see). On a phone, where the menu is folded
  away, the same tabs are the strip under the heading. The heading names the open tab.

## Privacy

Each person's health is their own. In the hosted build every member of an organization uses the same database,
and Aira still shows a member's health and care work only to them (health also to the caregivers they named);
platform staff on a support visit never see it. Public pages show only what they are for: the card, the report
the person chose to share (until its date), or a statement without the carer's private notes or rate.

## For developers

`aira/suite/care/`: `store.py` (tables `cr_*`; `care.v2` adds `cr_refills`, `cr_report_links`, `cr_clients`,
`cr_visits`, `cr_statements`), `service.py` (rules: time zone, slots, adherence, supply and refills, readings,
appointments and their calendar files, the card), `report.py` (the doctor report and its expiring link),
`work.py` (care work), `links.py` (public links, also put in the hosted organization's index whatever order
the server started in), `care_routes.py` (routes under `/api/care`, the public pages `emergency_card`,
`health_report` and `care_statement` with its `paid` action, Today cards, the pulse and the background task).
`auto_apply_assistant/views-care.js`, `public-care.js`, `app-care.css`. Tests in `tests/test_suite_care.py`,
`tests/test_suite_care_features.py`, `tests/test_suite_care_fixes.py`, `tests/test_suite_care_search.py`,
`tests/test_suite_care_ui.py` and `tests/test_suite_care_shell_ui.py` (Chromium; `CARE_SHELL_SHOTS=<folder>` saves
screenshots). `search.py` is the search box's matching. Table `cr_schedules` keeps each medicine's times and days with when they held.

Main routes: `GET /api/care/search?q=&person=&limit=` (the search box), `POST /api/care/settings` (time zone), `POST /api/care/medicines/{id}/refill`,
`GET /api/care/doses?medicine=&status=&format=csv`, `GET /api/care/appointments/{id}/calendar`,
`GET /api/care/report?days=30|90|180&diary=1&format=csv`, `POST|DELETE /api/care/report/link`,
`GET /api/care/work`, `/api/care/work/clients`, `/api/care/work/visits` (`?format=csv`),
`/api/care/work/statements` and `/{id}/link`, `/{id}/send`, `/{id}/paid`.
