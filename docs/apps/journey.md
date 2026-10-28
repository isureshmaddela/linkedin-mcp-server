# Life

Your life in one place: goals, habits, a journal, the story of your life and the papers that matter. Open it from
the app switcher (Everyday > Life). The app's id is `journey` (`/api/journey/*`, `#/journey`) because `/api/life`
is the everyday apps' shared core.

Everything in Life is yours alone. Other people in the household (or your organization's members, hosted) cannot see
your goals, habits, journal or documents; a timeline event is shared only when you tick **Share with my household**,
and a goal is public only when you make its page.

## In the Aira frame

Life lives at `/life`, with its own logo and tab title ("Goals · Life · Aira").

* **+ New** (above the left menu) offers **New goal**, **New habit**, **Write in your journal**, **Add to your
  timeline** and **Add a document**. Each opens the tab and its usual form (the journal's form is on its page: today's
  mood takes focus). Everyone gets all five: Life is each person's own, children and hosted members included.
* **Search** (the box across the top) finds your own goals, habits (stopped ones too), journal days, timeline events
  and documents by their words: a goal's title or why it matters, a journal day's text, an event's title, story or
  place, a document's name, issuer, tags or notes (never its number or scan). Choosing a goal or a document opens it;
  a habit, a journal day or an event opens its tab with it outlined. **Enter** lists every match by kind
  (`#/journey?tab=search&q=<words>`). It is one request, `GET /api/journey/search?q=<words>&limit=<1-50>`.
* **Folders**: the left menu shows Overview, Goals, Habits, Journal, Timeline and Documents like a mail app's folders;
  the page is named after the open one. On a phone the menu folds away, so the tabs (and the overview's create
  buttons) stay under the heading.

Links: `?new=goal|habit|entry|event|document` opens the form to add one, `?open=habit:<id>|entry:<id>|event:<id>`
shows that record; both leave the address once answered, so Back or a reload does not ask again.

## Overview

* **Active goals** (and how many are in progress or behind), your **life score** (the average progress of your active
  goals), **habits** done today and your best streak, **documents** (and how many expire within 90 days), your
  **journal** streak and today's mood, and **timeline events**.
* **On this day**: timeline events and journal days from this date in earlier years.
* **Recent goals** with their progress and whether they are on track, behind or overdue. Each opens the goal.
* **Coming up**: future timeline events, goal target dates and document expiry dates in the next year, soonest first.
  A goal or a document opens straight away.
* **Money goals**: what you are saving or earning, and what supporters gave or promised.
* **Progress by area of life**: one bar per area (career, health, money, learning, family, ...) with active goals.

A link can open one item: `#/journey?tab=goals&goal=<id>` opens a goal, `#/journey?tab=documents&doc=<id>` a
document.

## Goals

* A goal has an area of life, an optional target date, why it matters, and how it is measured:
  * **By percent, or by ticking steps.** With steps, progress follows the steps you tick. Without steps,
    **Update progress** sets it (0 to 100%) with a note; every update is kept as a check-in.
  * **A number** (100 km, 12 books, 50 hours): **Add** records what you did; it is finished at its target.
  * **Money** (save or earn an amount, in your account's currency): **Add money** records what you put aside (a
    minus sign takes some out).
  * **An earning goal** (adults who can see the account's earnings): counts by itself what your Aira apps earn from
    the day it starts (paid invoices in Sales, shop orders, bookings, event tickets, courses and your share of the ads
    on your videos). The goal shows what each app earned towards it.
* **Suggest steps** (in the new goal form and in a goal's details) proposes 4 to 7 small steps from Aira's writing
  helper, or a good list for the goal's area of life when the helper is not running. You edit them before they are
  added; ideas you already have are left out.
* A goal is **behind** when its progress is more than 15 points below where the time elapsed says it should be, and
  **overdue** after its target date.
* Reaching 100% (or **Mark done**) completes the goal and puts "Reached a goal: ..." on your timeline, once.
  **Make active** reopens it. Goals can be paused or dropped.
* Filter by status (**All goals** shows the finished and paused ones too), area or words; **Export CSV** downloads
  your goals.

### A goal's page: cheers and supporters

* **Share this goal** makes a public page (adults and teens; children do not make public pages). Anyone with the
  link sees the goal, why it matters, its progress and steps, and can send a **cheer** (a name and a short message).
  Never shown: your check-in notes, your other goals, your documents, or the amounts of a money goal (only its
  percentage). **Turn off** stops the link at once, and stops taking support too (turn supporters on again for a
  new link).
* **Supporters** (adults only): turn on **Let people back this goal** in the goal's details, with how to pay you: a
  payment link of your own (PayPal.me, a UPI or Stripe link) and a short note (bank details, "cash when we meet").
  Turning it on makes the goal's page if it has none. On the page, family and friends **back the goal** with an
  amount they choose, their name and a message; the page shows who backed it, never how much.
* Aira never takes the money: supporters pay you directly. In the goal's details you see each pledge and mark it
  **received** when the money arrives (**Not received** undoes that), or **cancel** it. On a money goal you fill in
  yourself, a received pledge is added to the goal ("Support from Aunt Lata"), so a laptop for college, course fees
  or a trip can be funded by the people who care. On any other goal (a sponsored run for a cause) the pledges are
  counted for you.
* Limits: 5 pledges and 5 cheers a day from one visitor on one page; 500 of each per goal; up to 100,000 in the goal's
  currency per pledge. An amount must be a plain number (12.50); a mark of another currency ("₹50" on a USD goal),
  "Infinity" or "1e999" is refused with a clear message.
* A money goal that supporters backed keeps its currency while those pledges stand (cancel or remove them to change
  it). Taking a pledge or a habit's amount back from a goal now measured in percent leaves its progress as it is.
* A new cheer or pledge pops up as a reminder and is counted on the Life menu item until you open the goal.

## Habits

* Small things you do often: every day, or a number of days a week. Tick **Done** here or on **Today**; ticking
  twice changes nothing, and **Done yesterday** catches up a day you forgot (up to 30 days back).
* Streaks count days in a row (or weeks in a row that met the target), with your best streak, the last four weeks
  and how much of the last 30 days you did.
* A habit can add an amount to a number or money goal each time ("put 5 aside every day"); unticking takes it back.
* **Stop** keeps a habit's history; **Start again** brings it back.

## Journal

* A mood (Great, Good, Okay, Low or Hard), a few lines about the day and one thing you are grateful for. Write today
  at the top of the tab, or **Write about another day** (not a day to come). Writing the same day again updates it:
  picking a day already written loads what is there into the form, so saving never wipes it.
* **Days in a row**, days written this month and the **mood of the last 30 days** (a bar per day: the taller, the
  better the day).
* Filter by month, mood or words; **Export CSV** downloads the journal, oldest day first.
* **On this day** brings back your journal days and timeline events from this date in earlier years (a leap day is
  remembered on 28 February in other years).
* The journal is private: nobody else in your household or organization can read it.

## Timeline

Milestones by year: births, school, jobs, moves, relationships, health, travel and achievements, with a place and a
story. **Include what my household shared** shows the events other people shared with you (read only). Filter by
kind, year or words; **Export CSV** downloads your own timeline.

## Document vault

* Passports, ID cards, licences, certificates, visas, insurance, medical, tax, property, vehicle and bank papers, and
  wills, with who issued them, the issue and expiry dates, tags and notes.
* The **document number** and the **scan** (a PDF, a photo or a text file up to 10 MB; 200 MB for all your scans) are
  encrypted with the installation's secret key before they are stored. The list shows only the last four characters;
  **Show number** reveals it to you. **View** previews a photo; **Download** gives the file back.
* A document is **valid**, **expires soon** (within 90 days), **expired**, or has **no expiry**. **Export list**
  downloads the names and dates (never the numbers or the scans).

## Today and reminders

* **Goals and documents**: goals due within a week (or overdue) and documents expiring within 30 days. A document
  that expired more than 30 days ago (an old passport kept for the record) stops reminding; it is still listed as
  expired in the vault.
* **Habits today** with a **Done** button.
* **Your day** (for people who keep goals, habits or a journal in Life): pick how today is going in one tap (a good,
  an okay or a hard day), and see what happened on this day in earlier years.
* A reminder pops up 30 days, 7 days and 1 day before a document expires and on the day, and when someone cheers or
  backs one of your goals. The Life menu item shows how many things need you.

## The AI agent

The **LifeAgent** in the AI app reads your goals, what is coming up and expiring documents to answer questions, and
can add a goal, update a goal's progress or add a timeline event when you ask it to ([ai.md](ai.md)).

## For developers

`aira/suite/journey/` (`store.py`: tables `jr_*`, versions 1 to 3; `service.py`: the rules; `journey_routes.py`:
routes, the public page `goal_page` with the actions `cheer` and `pledge`, Today and pulse),
`auto_apply_assistant/views-journey.js`, `public-journey.js`, `app-journey.css`. Tests:
`tests/test_suite_journey.py`, `tests/test_suite_journey_features.py`, `tests/test_suite_journey_journal.py`,
`tests/test_suite_journey_search.py` and, in a real browser, `tests/test_suite_journey_shell_ui.py`.
