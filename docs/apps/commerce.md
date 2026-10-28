# Commerce (Shop and Bookings)

Sell products and appointments from one app: a storefront with a cart, orders from placed to delivered, discount
codes and stock, plus a booking page with services, deposits, team availability, reminders and packages. For shops,
makers, salons, clinics, studios, tutors and consultants.

Open it from the app switcher or with `g` then `4`. It has two sections in the left menu:

* **Shop** (`#/shop`): Overview, Orders, Products, Discounts, Customers, Share and earn, Settings. Details in
  [shop.md](shop.md).
* **Bookings** (`#/bookings`): Overview, Calendar, Appointments, Waiting list, Services, Staff, Customers, Packages,
  Team pay, Settings. Details in [bookings.md](bookings.md).

The business details (name, contact, time zone, currency, tax, payment link) are set once in **Business hub >
Business profile** and used by both sections, every customer page and every email.

## In the Aira frame

Commerce lives at its own address (`/commerce`) with its own logo, like a Google app. The frame gives it three
things (set up in `views-shop.js`, for both sections):

* **+ New** (above the left menu) offers New order, New product, New discount code, New booking and New service.
  Each opens the same form as the button inside the tab. A child's or a teen's household profile, and a hosted role
  that only reads (an auditor), get no **+ New**. On a phone or a tablet the menu is a sheet, so Bookings keeps its
  own **New booking** button in the page head there (on a wide screen it hides, and the tabs' buttons show).
* **Search** (across the top) finds orders (customer name, email, phone or `#number`), products (name, SKU,
  category), bookings (customer, reference or service), booking customers and services. Up to six show as you
  type, a few of each kind, and choosing one opens it: the order or the product in its drawer, the booking or the
  customer in theirs, the service in its form. **Enter** opens a results page (`#/shop?tab=search&q=`) with every
  match by kind (up to 20 each, with a link to the full filtered list). On **Home** the same search shows
  Commerce's records next to the other apps'.
* **Folders**: the Shop tabs (Overview, Orders, Products, Discounts, Customers, Share and earn, Settings) and the
  Bookings tabs (Overview, Calendar, Appointments, Waiting list, Services, Staff, Customers, Packages, Team pay,
  Settings) are in the left menu with their own icons, from the moment Commerce opens.

A link can open a form or a record once: `#/shop?tab=orders&new=order` (also `new=product`, `new=discount`),
`#/shop?tab=orders&order=<id>`, `#/shop?tab=products&product=<id>`, `#/bookings?tab=appointments&new=booking`,
`#/bookings?tab=services&new=service`, `#/bookings?tab=appointments&booking=<id>`,
`#/bookings?tab=customers&customer=<id>` and `#/bookings?tab=services&service=<id>`. The page takes the request
out of the address as it opens it, so Back and refresh do not open it again.

On a phone, the wide tables keep their key columns (an order: number, customer, total, payment and status), the
calendar is one day per row, and a free day is one short line.

## How Commerce helps you earn

| Where | What it does for your income |
| --- | --- |
| Storefront and booking page | Customers order and book themselves, day and night, without an account. |
| Deposits and packages | Money before the work: deposits cut no-shows, packages are paid up front. |
| **Share and earn** (Shop) | Every customer who pays gets their own code (customers who paid before you turned it on get one too). A new customer who uses it gets a percent off their first order (0% is allowed: the shared link still credits the sharer); when that order is paid, the customer who shared it earns an amount off their next order. Marking that order unpaid, refunding or cancelling it withdraws an unused reward. Your customers earn by bringing you new ones. |
| **Back in stock** (Shop) | Sold-out products ask for an email. When you add stock, everyone waiting gets an email with the link: sales you would have lost, and a list of what people want most. |
| **Waiting list** (Bookings) | A full day takes names. When a booking that day is cancelled or moved, everyone waiting whose service now has a free time with that person gets an email (also for a different, shorter service), and the first to book fills the gap. A day whose times are over, too soon, closed or not open for booking yet is not "full" and has no waiting list. |
| **Team pay** (Bookings) | Each person's commission rate, and what they brought in from completed appointments and what you owe them, for any dates (up to a year, every appointment counted), with a CSV for payroll. A changed rate counts from the moment it is saved: earlier appointments keep the rate they had. |
| Business hub | Revenue from paid orders, deposits and packages adds up with your other apps. |

Aira never takes card payments or moves money: customers pay through your own payment link or instructions, and
you mark the payment received. Rewards are discount codes, not cash.

## A customer's journey

1. They open your storefront or booking page (Shop > Overview > Create link; Bookings > Overview > Create link).
2. They order or book. Prices, discounts, stock and free times are always worked out on the server: the last item
   cannot be sold twice, and two people cannot take the same time.
3. They get a link to their own order or booking page, and an email (in Business hub > Outbox until you approve it,
   or straight away if you turned on automatic sending for that kind of message).
4. They pay with your payment link; you mark it paid. A paying shop customer now has a share code on their order
   page and receipt. A refunded or cancelled order cannot be marked paid again (place a new order instead).
5. A friend opens the shared link (`/p/<storefront>?ref=CODE`): the code is already filled in at checkout.

## Messages Commerce prepares

`order_confirmation`, `payment_receipt` (with the customer's share code), `order_shipped`, `back_in_stock`,
`referral_reward`, `booking_confirmation`, `booking_reminder`, `booking_rescheduled`, `booking_cancelled` (with the
reason you gave), `booking_cancelled_notice` (to you), `review_request`, `waitlist_opening`. Each waits in
Business hub > Outbox for approval unless you list its kind under Business hub > Email.

## One account, every app

* **On your own computer** (the single-user build): one sign-in opens Commerce with your other apps; its data is
  in your own database. Commerce holds customers' contact details and money, so a child's or a teen's household
  profile (`X-Aira-Profile`) gets `403 adult_only` on every Shop and Bookings route; the customer pages are
  unchanged.
* **Hosted** (an organization): Commerce is an add-on (Starter and Team per seat; included in Enterprise and the
  trial). Until the organization subscribes, its routes answer `402 app_required` and it is not in the switcher.
  Owners, admins and coaches manage it; auditors read it; candidates and billing contacts do not see it. Each
  organization's shop and bookings are its own.

## API

Shop routes are under `/api/shop/`, Bookings under `/api/bookings/` (listed in each section's page). Added for the
features above:

| Route | What it does |
| --- | --- |
| `GET /api/shop/referrals` (`?format=csv`) | Share and earn: settings, totals, everyone with a code |
| `GET /api/shop/alerts` (`?product_id=`, `?format=csv`), `DELETE /api/shop/alerts/{id}` | Back-in-stock requests |
| `POST /api/shop/settings` | also `referrals_enabled`, `referral_friend_percent`, `referral_reward_cents`, `referral_reward_days` |
| public `storefront` action `notify_me` | `{product_id, option, email, name}` |
| `GET /api/bookings/waitlist` (`?status=`, `?when=all`), `PATCH`/`DELETE /api/bookings/waitlist/{id}` | The waiting list |
| `GET /api/bookings/earnings?from=&to=` (`&format=csv`) | Team pay (this month by default) |
| `POST`/`PATCH /api/bookings/staff` | also `commission_percent` (0 to 100) |
| public `booking_page` action `join_waitlist` | `{service_id, day, staff_id, name, email, phone}`; refused for a day with no times left to free |
| public `slots` actions (booking page and a booking's own page) | each day has `state`: `open`, `full` (all booked: waiting list), `closed`, `past` or `not_yet` (beyond how far ahead customers may book), and `open` (true for open or full) |
| `GET /api/bookings/staff`, `GET /api/bookings/services` | also `today`: the business's day, which the forms start on |
| public `storefront` page | also `referrals` (share and earn is on, so a `?ref=` link counts even at 0% off) |
| `GET /api/shop/search?q=&limit=` | The search box: `products` and `orders` matching the words (`limit` 1 to 50, default 6 of each); an empty `q` finds nothing |
| `GET /api/bookings/search?q=&limit=` | The search box: `bookings` (latest first), `customers` and `services` matching the words, and the `timezone` |

## Tests

`tests/test_suite_shop.py`, `tests/test_suite_shop_more.py`, `tests/test_suite_bookings.py`,
`tests/test_suite_bookings_more.py`, `tests/test_suite_bookings_ui.py`, `tests/test_suite_commerce_fixes.py` and
`tests/test_suite_commerce_ui.py` (real Chromium, phone width included). The frame: `tests/test_suite_commerce_search.py`
(the search routes, both builds) and `tests/test_suite_commerce_shell_ui.py` (+ New, the search box, Enter, the
folders and a phone in light and dark, in real Chromium).
