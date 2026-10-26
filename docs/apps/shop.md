# Shop

Sell products online: a storefront customers order from, orders from placed to delivered, stock, discount codes
and the customers who come back. For retailers, makers and creators. Open it from the app switcher (`g` then `3`)
or at `#/shop`. In the hosted build an organization turns it on under Apps (Starter and Team: per seat; included
in Enterprise and the trial).

## Getting started

1. **Business details** (Business hub > Business profile): your shop name, contact, currency, tax rate and your
   **payment link** (Stripe, PayPal, Razorpay, UPI) or payment instructions. Customers see these on every page.
2. **Products**: name, price, a photo link (https), category. Optional: a "was" price shown crossed out, SKU,
   stock tracking with a low-stock alert, and options such as sizes (`Small | 5 | 18.00` = name, stock, price;
   stock and price are optional). "Write the description for me" drafts a description from your notes.
3. **Share your storefront** (Overview > Create link) on social media, WhatsApp or your website.
4. Orders arrive in **Orders**. Mark them paid when the money reaches you, packed, shipped (with a tracking
   link) and delivered or picked up.

## The storefront

Customers browse by category or search, add items to a cart (kept in their browser), choose pickup or delivery,
enter a discount code and place the order with their name and an email or phone number. The shop prices every
order on its own server: prices, discounts, delivery and tax are never taken from the browser, and stock is
checked and taken in the same step, so the last item cannot be sold twice. After ordering, the customer sees the
order number and how to pay, and gets a link to their order page.

The **order page** shows the order's progress, its items and totals, how to pay and the tracking link. A
customer who paid can say so ("I have paid", with a reference); the order is marked paid only when you confirm.

When the shop is closed (Settings > Taking orders off) customers can browse but not order and see your message.

## Orders

* **Payment**: unpaid, paid, refunded. Marking an order paid records its total as revenue (once, however often you
  press it); refunding or unmarking it stops counting it. Aira does not take card payments or move money: refund
  through your payment provider and mark the order refunded.
* **Fulfilment**: placed, packed, shipped, delivered (picked up), cancelled. Cancelling gives the stock and the
  discount use back; a paid order stays paid until you refund it. A delivered order cannot be cancelled.
* **New order** enters a phone or walk-in order: drafts can be sold this way, the shop may be closed, and it can
  be marked paid at once.
* Internal notes are only for your team. Search by name, email, phone or order number; download a CSV.

## Discount codes

Percent off (1 to 100) or an amount off, with an optional minimum order, expiry date and use limit. A code never
takes an order below zero and a used-up, expired or inactive code is refused with a clear message.

## Customers

Everyone who ordered, merged by email (or phone): number of orders, what they paid, first and last order, and
whether they came back. Click one to see their orders; download a CSV.

## Messages

Order confirmations (`order_confirmation`), payment receipts (`payment_receipt`) and shipping notices
(`order_shipped`, with the tracking link) go to the shared outbox (Business hub > Outbox). They wait for your
approval unless you list the kind under automatic sending in Business hub > Email.

## In the Business hub

Revenue from paid orders adds up with your other apps. The Shop card shows revenue this month, orders to fulfil
and low-stock products, and a setup checklist: business details, first product, storefront shared, first order,
first paid order.

## API

All under `/api/shop/`: `overview`, `settings`, `storefront` (+ `/link`), `products` (+ `/draft`, `/{id}`),
`orders` (+ `/{id}`, `/paid`, `/unpaid`, `/refund`, `/cancel`, `/fulfil`, `/link`), `quote`, `discounts`
(+ `/{id}`), `customers`. Lists accept `?format=csv`. Money is integer cents. Public pages: `storefront` (actions
`quote`, `place_order`) and `order` (action `notify_paid`).
