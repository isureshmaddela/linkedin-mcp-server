"""Aira Events: sell tickets, fill the room and run the door.

For entertainment businesses, venues and event organisers. The main objects:

* **Events** (``ev_events``): what, where (a venue or an online link), when, how many people fit, and whether
  it is a draft, published (on sale), cancelled or ended.
* **Ticket types** (``ev_ticket_types``): a price (0 = free), how many, a sales window and a per-order limit.
* **Orders** (``ev_orders``) and **tickets** (``ev_tickets``): a buyer's order holds one ticket per seat, each with a
  short check-in code. A free order is confirmed at once; a paid one waits for payment through the business's own
  payment link and is confirmed when the organiser marks it paid (that is when revenue is recorded).
* **Promo codes** (``ev_promos``), a **waitlist** (``ev_waitlist``) for sold-out events and **sponsors**
  (``ev_sponsors``).

Customers use two public pages: the event page (buy tickets, join the waitlist) and their tickets page (codes for
the door, add to calendar, "I have paid"). Capacity is re-checked inside one write transaction, so two buyers can
never get the same last seat.
"""
