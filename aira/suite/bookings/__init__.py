"""Bookings: appointments that pay, for salons, clinics, studios, tutors and consultants.

The main objects:

* **Services** (``bk_services``): what a customer books, with a length, a buffer after it, a price and an
  optional deposit, offered by some or all of the staff.
* **Staff** (``bk_staff``) with weekly hours (``bk_hours``, several ranges a day) and time off
  (``bk_time_off``, for one person or the whole business).
* **Bookings** (``bk_bookings``): an appointment of one service with one person, stored in UTC and shown in the
  business's time zone. Statuses: booked, confirmed, completed, cancelled, no_show.
* **Customers** (``bk_customers``): made from bookings (by email, else phone), with visits, spend and notes.
* **Packages** (``bk_packages``) sold to customers (``bk_customer_packages``) and redeemed against bookings.

Customers book themselves on the public booking page (kind ``booking_page``) and manage their appointment on
its own page (kind ``booking``: cancel or reschedule within the business's rules). Slots are computed on the
server and re-checked when a booking is made, so two customers can never take the same time.
"""
