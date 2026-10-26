# Events

Sell tickets, fill the room and run the door. Events is for entertainment businesses, venues, promoters and
anyone who runs meetups, workshops, conferences, comedy nights, concerts or screenings. Open it from the app
switcher (or `g` then `5`); the Business hub adds its revenue and setup progress to the other apps.

## The flow

1. **Create an event**: title, category, start and end, a venue (or an online link that only confirmed ticket
   holders see), an overall capacity if the room has one, a cover image and a description. **Draft
   description** writes a first version for you (with the local model when it runs, a template otherwise).
2. **Add ticket types**: Early bird, General, VIP, Student... Each has a price (0 = free), a quantity, a sales window
   and a limit per order. A ticket type that has sold can be switched off but not deleted; its tickets stay valid.
3. **Publish**: the event gets a public page. Share the link (social media, newsletter, posters). Customers see the
   tickets on sale, scarce ones with "3 left", enter a promo code, give their name and email (and the names on the
   tickets if they want) and order.
   * A **free** order is confirmed at once: the buyer lands on their tickets page with one check-in code per ticket,
     and a ticket email plus a reminder for the day before go into the Business hub outbox.
   * A **paid** order waits for payment through your own payment link or instructions (Business hub > Business
     profile). The buyer can press "I have paid"; you check your account and press **Mark paid**. Only then is the
     revenue recorded and the tickets sent.
   * When everything is sold out the page offers a **waitlist** instead.
4. **At the door**: Check-in takes the code from a ticket (`ABCD-2345`, case and dash do not matter). A code works
   once; a second try says when it was used. Unpaid and cancelled tickets are refused. The guest list finds people
   by name, email or order and has an Undo.
5. **After the event**: Thank attendees, export the attendee list (CSV) and duplicate the event for the next date.

Aira never takes card payments itself and never says money arrived when it did not: you mark orders paid.

## Also

* **Orders**: filter by event, status or name; resend tickets; cancel (the seats are released) or refund a paid
  order (the revenue stops counting and reminders are dropped). Sell at the door or by phone with **Sell tickets**,
  including complimentary tickets for guests and press.
* **Promo codes**: percent or amount off, for one event or all, with a use limit and an expiry. A cancelled order
  gives its use back.
* **Sponsors**: packages from pitched to agreed to paid; a paid sponsorship counts as revenue.
* **Waitlist**: when seats free up, **Offer seats** emails the event page to the person (first come, first served).
* **Messages** to ticket holders (doors open earlier, a change of venue) and the cancellation notice go through the
  outbox like everything else; nothing is sent until you approve it, unless you turned on automatic sending for that
  kind (`ticket`, `event_reminder`, `payment_request`, `event_update`, `waitlist_offer`, `post_event_thanks`).

## Guarantees

* Two buyers can never get the same last seat: availability is re-checked inside one write transaction.
* The public pages show nobody else's details: no other buyers, no internal notes, no exact counts unless 20 or
  fewer are left.
* Hosted, Events is an add-on per seat (included in Enterprise). Owners, admins and coaches manage it, auditors
  can only read, candidates do not see it, and its public pages stop working if the organization turns it off.
