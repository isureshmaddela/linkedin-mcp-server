# Bookings

Appointments that pay, for salons, clinics, studios, tutors and consultants. Customers book themselves on your
booking page; you run the day from the calendar; deposits, packages and reminders cut no-shows.

## Getting started

1. **Business hub > Business profile**: your name, contact, time zone, currency and payment link (Stripe,
   PayPal, Razorpay, UPI). Customers see them on every page.
2. **Bookings > Staff**: add yourself (and anyone else who takes appointments) with weekly hours, for example
   `09:00-12:00, 13:00-17:00`. Add time off for one person or close the whole business for a day.
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
cancellation notice; after that, the page asks them to contact you.

## Running the day

* **Calendar**: a week per person, colour coded; open a booking to confirm it, mark it completed or a
  no-show, move it, cancel it (with a reason the customer sees) or add notes (a private note only your team
  sees).
* **New booking**: for phone calls and walk-ins, inside your hours or outside them (never on top of another
  booking).
* **Payments**: record the deposit, the full price or another amount; mark unpaid to undo. Payments count once
  in the Business hub revenue. Aira does not take card payments itself: customers pay through your own payment
  link or instructions and you record it.
* **Packages**: sell sessions up front (for example 5 massages), mark them paid (counted in revenue once), and
  use a session to pay a booking. Cancelling that booking gives the session back.
* **Customers**: every customer with visits, no-shows, spend, last and next visit, tags and notes; CSV export.

## Messages

Bookings prepares, in Business hub > Outbox: a confirmation with the customer's own link, a reminder that goes
out 24 hours before (dropped when the booking moves, is cancelled or completed), a notice when you cancel, a
message to you when a customer cancels, and a review request after a completed visit (when you set a review
link). Nothing is sent until you approve it, unless you list the kinds (`booking_confirmation`,
`booking_reminder`, ...) for automatic sending under Business hub > Email. Mail goes out through your own mail
server.

## In the hosted product

Bookings is an add-on (Starter and Team, per seat; included in Enterprise). Owners, admins and coaches (as team
members) manage it; auditors can read it; candidates do not see it. The bookings belong to the organization.
