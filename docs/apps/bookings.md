# Bookings

Appointments that pay, for salons, clinics, studios, tutors and consultants. Customers book themselves on your
booking page; you run the day from the calendar; deposits, packages and reminders cut no-shows. Bookings is a
section of **Commerce** ([commerce.md](commerce.md)): `g` then `4`, or `#/bookings`.

## Getting started

1. **Business hub > Business profile**: your name, contact, time zone, currency and payment link (Stripe,
   PayPal, Razorpay, UPI). Customers see them on every page.
2. **Bookings > Staff**: add yourself (and anyone else who takes appointments) with weekly hours, for example
   `09:00-12:00, 13:00-17:00`, and their commission if you pay one (see Team pay). Add time off for one person or
   close the whole business for a day.
3. **Bookings > Services**: a name, a length, an optional buffer after (clean-up, travel), a price and an
   optional deposit. Tick who offers it (none ticked = everyone).
4. **Bookings > Overview > Create link**: your booking page. Put it on your website, Instagram bio, Google
   profile and email signature.

## How customers book

The booking page lists your services by category. A customer picks a service, a person (or anyone available),
a day and one of the free times, enters a name and an email or phone, and books. They get a reference, the
link to their own booking page, and (when the service has a deposit) your payment link with the reference.

Free times are computed on the server from each person's hours, time off and other bookings (with buffers),
in your time zone (clock changes included), and the time is checked again at the moment of booking: two
customers can never take the same slot. The rules (Settings): start times every N minutes, minimum notice,
how far ahead, how long before the appointment customers may cancel or move online, automatic confirmation,
and how many upcoming bookings one customer may hold.

On their own booking page a customer can move the appointment to another free time or cancel it until the
cancellation notice; after that, the page asks them to contact you. If you cancel, the page and the email show the
reason you gave.

## The waiting list

On the booking page a day whose times are all booked reads "full" and can still be picked: it offers **Join the
waiting list** (name, and an email or a phone number). A day with nothing to wait for reads "closed" (nobody works,
or its times are over or too soon to book), and a day beyond how far ahead customers may book reads "not open
yet"; neither has a waiting list. A customer can also join from a day with times when none of them suit. When a
booking on that day is cancelled (by you or the customer) or moved to another day, everyone waiting whose service
now has a free time with that person gets a message (`waitlist_opening`, with the free times and the booking page
link), also when they wait for a different service that fits the freed time: the first to book gets it. Bookings > **Waiting list** shows everyone waiting,
whether they were told, and "time free" when there is a free time that day right now; **Book** opens a new booking
with their details filled in, **Remove** takes them off. Booking that day takes them off the list by itself. One
customer can wait for at most five days at once. A phone number counts as the same customer however it is
written (`+1 555 0199` and `+15550199`), for the waiting list and for the limit on upcoming bookings.

## Running the day

* **Calendar**: a week per person, colour coded, starting on today in your business's time zone; **Book** under a
  day adds a booking there. Open a booking to confirm it, mark it completed or a no-show, move it, cancel it (with
  a reason the customer sees) or add notes (a private note only your team sees).
* **New booking**: for phone calls and walk-ins, inside your hours or outside them (never on top of another
  booking).
* **Payments**: record the deposit, the full price or another amount; mark unpaid to undo. Payments count once
  in the Business hub revenue. Aira does not take card payments itself: customers pay through your own payment
  link or instructions and you record it.
* **Packages**: sell sessions up front (for example 5 massages), mark them paid (counted in revenue once), and
  use a session to pay a booking. Cancelling that booking gives the session back.
* **Customers**: every customer with visits, no-shows, spend, last and next visit, tags and notes; CSV export.
  **Book again** opens a new booking with the customer filled in.
* **Overview**: "Completed, money still due" counts completed visits paid less than their price (nothing paid, or
  only the deposit); a booking with a deposit paid offers **Take the rest** with what is still due.

## Team pay

Set each person's commission (a percent of what they bring in, decimals allowed) under Staff. **Team pay** shows,
for any dates (this month first, with Previous and Next month): completed appointments, no-shows, takings and the
commission owed per person, and the totals. Takings are the payments recorded on their completed appointments; a
session paid from a package counts at its share of the package price. **Download CSV** lists every appointment
with its takings, rate and commission, for payroll (up to a year at a time, every appointment counted). Changing
a rate counts from the moment you save it: appointments before it keep the rate they had, so last month's pay
never changes, and the Rate column shows "40%, then 10%" when the dates span a change. The first rate you give a
person (from none) covers their earlier appointments too.

## Messages

Bookings prepares, in Business hub > Outbox: a confirmation with the customer's own link, a reminder that goes
out 24 hours before (dropped when the booking moves, is cancelled or completed), a notice when you cancel (with
your reason), a message to you when a customer cancels, a review request after a completed visit (when you set a
review link) and a message to the waiting list when a time opens. Nothing is sent until you approve it, unless you list the kinds (`booking_confirmation`,
`booking_reminder`, ...) for automatic sending under Business hub > Email. Mail goes out through your own mail
server.

## API

All under `/api/bookings/`: `overview`, `settings`, `page-link`, `services` (+ `/{id}`), `staff` (+ `/{id}`,
`/{id}/hours`), `time-off` (+ `/{id}`), `slots`, `bookings` (+ `/{id}`, `/cancel`, `/reschedule`, `/pay`, `/unpay`,
`/redeem`, `/link`), `agenda` (without `start`: today in the business's time zone), `customers` (+ `/{id}`),
`packages` (+ `/{id}`), `customer-packages` (+ `/{id}/pay`, `/{id}/cancel`), `waitlist` (+ `/{id}`), `earnings`.
Public pages: `booking_page` (actions `slots`, `book`, `join_waitlist`) and `booking` (actions `slots`, `cancel`,
`reschedule`).

## In the hosted product

Bookings comes with the Commerce add-on (Starter and Team, per seat; included in Enterprise). Owners, admins and coaches (as team
members) manage it; auditors can read it; candidates do not see it. The bookings belong to the organization.
