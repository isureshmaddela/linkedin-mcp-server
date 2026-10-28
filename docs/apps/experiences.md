# Courses & Events

Teach and gather people, and earn from both. **Courses & Events** (catalog id `experiences`) is one app with two
sections: **Courses** (Academy: courses with lessons, quizzes, certificates and cohorts, see
[academy.md](academy.md)) and **Events** (ticket types, an event page, door check-in, promo codes, sponsors and a
waitlist, see [events.md](events.md)). It lives at `/courses` (like a mail app at its own address); open it
from the app switcher (or `g` then `5`); `#/experiences` opens Courses. Both sections share partners, earnings and a catalog page, described here.

One account runs it: in the single-user build your own sign-in; hosted, the organization turns the app on under
**Apps** (until then its routes answer `402 app_required`). Owners, admins and coaches manage it, auditors read
it, candidates and billing contacts do not see it. Organizations never see each other's courses, events, partners
or money.

## How you earn

* **Courses**: a price per course, paid through your own payment link or instructions; you mark each enrolment
  paid (Aira never takes cards and never says money arrived when it did not).
* **Events**: paid tickets, marked paid the same way, and **sponsors** (a paid sponsorship counts as revenue).
* **Partners** bring you students and guests and earn a commission from it (below), so other people earn with you.

## Partners (referrals that earn a commission)

A partner is someone who sends you customers: a teacher, a club, a creator, a past student. Add one under
**Partners** (in either section): a name, an email, a commission (0 to 90 %) and a code (made for you when left
empty).

* Each partner gets **their own links**: every published course page and upcoming event page with `?ref=CODE`
  added, and the catalog page. Copy them from the partner's panel.
* Someone who enrols or orders through a partner's link becomes **their referral** (the code is kept for the
  visit, for your business only: another business's pages never receive it). You can also type a partner code
  yourself in **Enrol a student** and **Sell tickets**; there a code that is not a partner's, a switched-off
  partner, or the partner's own email is refused with a message, so a typo never loses a commission silently.
* The commission is **earned** when you mark the payment received, **waiting** until then, and **dropped** when
  the sale is refunded or cancelled. It is always worked out at the rate in force **when the sale was made** (the
  rate the partner and you were shown while it waited); changing a partner's rate applies to new sales only. A
  partner who buys with their own code earns nothing (the activity log says so). A free enrolment or a free ticket counts as a referral that earns nothing;
  complimentary tickets never count. An unknown or switched-off code never stops a sale.
* **Record payout** when you have paid the partner (yourself, outside Aira): everything owed is marked paid, the
  payout is kept with a note, and the partner is emailed through the outbox. A refund after a payout leaves the
  paid commission on record.
* **Email them their page** sends the partner a link to **their own page**: their code, their links, how much
  their referrals bought, what is waiting, earned and paid. It never shows your customers' names.
* A partner with any history (refunded referrals too) cannot be deleted, only switched off (their links stop
  counting, their page closes, their history stays). Download the list as CSV (money as 2000.00, like the
  earnings download).

## Earnings

**Earnings** (in either section) answers "what earns, and what does not":

* This month (and the change on last month), all time split into courses and events, refunded, partner
  commission and what you keep after it.
* **Earning the most** and **Earning the least** (never the same item twice), with a hint for the slow ones.
* The last six months as bars, and **every course and event** ranked from the top earner to the lowest: earned,
  this month, payments, refunds and commission. Open a row to go to it; download it as CSV.
* The **payment log**: every payment you marked received, newest first; a refund stays in the log, marked
  refunded, and stops counting.

The numbers come from the shared revenue ledger, so they match the Business hub.

## The catalog page

**Your catalog page** (Overview of either section) is one public page with every published course and every
upcoming published event, each linking to its own page, with prices, dates, ratings and "sold out" or "full".
An event's price comes from the tickets on sale now ("Tickets soon" until sales start; "From $X · free tickets
too" when a free ticket sits next to paid ones).
Put it in a bio, on posters and in emails; a partner's code in its address is passed on to every link. Turn it
off at any time.

## Reviews

Students rate a course (1 to 5 stars and an optional comment) from their own page once they have finished a
lesson; sending again updates their review. The course page shows the average and the latest reviews with the
student's first name and initial only. **Reviews** (Courses) lists them all; hide one that is off topic (the
average then leaves it out). Refunding a student hides their review, so it stops counting; you can show it again.

## Who may use it in a household

In the single-user build a household shares one Aira. Courses & Events holds customers' names and the business's
money, so a request made from a **child's** profile is refused (403); adults and teens use it as usual.

## Times and time zones

Event times are typed and shown in the **business's time zone** (Business hub > Business profile), on the
organiser's screens and on the public pages, whatever time zone the computer is set to. With no time zone set
(UTC), the computer's own zone is used.

## In the Aira frame

Like the other Aira apps, Courses & Events sits in the shared frame: its name and logo top left, a search box
across the top, a **+ New** button above its menu, and the launcher to every other app.

* **+ New** offers *New course*, *Draft a course with AI*, *New event*, *Enrol a student* and *New partner*. Each
  opens the same form as the button on its tab (the address asks with `?new=<kind>` and forgets it at once, so Back
  or a refresh never opens the form again). It is not offered to someone who cannot create here: a child's
  household profile, a hosted auditor or billing contact, or anyone while the app is off.
* **Search** finds courses (title, subtitle), events (title, venue), students (name, email, reference), ticket
  orders (buyer, email, order number) and partners (name, email, code), all in one request to `GET /search`. The
  list under the box shows up to six, one of each kind in turn; choosing one opens it (a course or an event on its
  own page, a student, an order or a partner in its panel). **Enter** opens a results page (`?tab=search&q=`, in
  the section you are in) with every match by kind, and a link to the tab that lists them all.
* **Folders**: the left menu shows each section's tabs with their own icons, like a mail app's folders: Courses
  (Overview, Courses, Students, Cohorts, Announcements, Reviews, Partners, Earnings) and Events (Overview, Events,
  Orders, Check-in, Promo codes, Sponsors, Waitlist, Partners, Earnings). The page head names the open folder, or
  the open course or event.
* **On a phone** the menu and its + New are folded away, so the page head keeps the section's main create (*New
  course*, *New event*), and lists drop their wide columns, showing those facts under each name instead, so
  nothing scrolls sideways.

## API

Under `/api/academy` (the gate covers both `/api/academy` and `/api/events`):

| Route | What |
| --- | --- |
| `GET/POST /partners`, `GET/PATCH/DELETE /partners/{id}` | partners with their totals (`?format=csv`), one partner with referrals, payouts and links |
| `POST /partners/{id}/payout`, `POST /partners/{id}/invite`, `POST/DELETE /partners/{id}/link` | record a payout, email the partner their page, make or turn off their page |
| `GET /earnings` (`?format=csv`) | the earnings report |
| `GET /catalog`, `POST/DELETE /catalog/link` | the catalog's contents and its public link |
| `GET /reviews`, `POST /reviews/{id}/status` | reviews (`course_id`, `status`), hide or show one |
| `GET /search?q=&limit=` | the search box: `courses`, `events`, `students`, `orders` and `partners` that match (newest first, `limit` 1 to 50 of each, default 5) and `totals` of each |

Public pages: `academy/catalog`, `academy/partner`, and the `review` action on `academy/student`. The `enrol`
action (course page) and the `order` action (event page) accept `ref`, a partner code.

Messages (Business hub > Outbox, approve first unless sent automatically): `partner_invite`, `partner_payout`,
plus each section's own.

## Known shared issue (worked around here)

Hosted servers started with `python -m aira_cloud serve` build the `CloudApp` (which installs the organization's
public-link index) before the gateway first imports the single-user module, whose `public.set_default_index(...)`
used to make the single-user index current again, so every public link made afterwards answered 404.
`aira/suite/academy/__init__.py` wraps `set_default_index` so it keeps an index installed over the old default (the
fix belongs in `aira/suite/public.py`; the wrapper is harmless once that lands).
