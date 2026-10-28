# Shop

Sell products online: a storefront customers order from, orders from placed to delivered, stock, discount codes
and the customers who come back. For retailers, makers and creators. Shop is a section of **Commerce**
([commerce.md](commerce.md)): open it from the app switcher (`g` then `4`) or at `#/shop`. In the hosted build an
organization turns Commerce on under Apps (Starter and Team: per seat; included in Enterprise and the trial).

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

A sold-out product (or a sold-out option, like one size) shows **Email me when it's back**: the customer leaves
an email address (see Back in stock below).

## Orders

* **Payment**: unpaid, paid, refunded. Marking an order paid records its total as revenue (once, however often you
  press it); refunding or unmarking it stops counting it. A refunded or cancelled order cannot be marked paid
  again (its money went back, or its stock did): place a new order instead. Aira does not take card payments or move money: refund
  through your payment provider and mark the order refunded.
* **Fulfilment**: placed, packed, shipped, delivered (picked up), cancelled. Cancelling gives the stock and the
  discount use back; a paid order stays paid until you refund it. A delivered order cannot be cancelled.
* **New order** enters a phone or walk-in order: drafts can be sold this way, the shop may be closed, and it can
  be marked paid at once.
* Internal notes are only for your team. Search by name, email, phone or order number; download a CSV.

## Discount codes

Percent off (1 to 100) or an amount off, with an optional minimum order, expiry date and use limit. A code never
takes an order below zero and a used-up, expired or inactive code is refused with a clear message.

## Back in stock

Customers ask for a sold-out product on the storefront. You see it everywhere you look at stock: "N waiting" on the
product in Products, the list of who is waiting (and who was already told) when you open the product, and
**Customers are waiting for** on the Overview. When you add stock (or a cancelled order gives stock back),
everyone waiting for what is now available gets one email (`back_in_stock`) with the storefront link, and is
marked told. A draft or archived product sends nothing. Remove a request from the product's drawer; deleting a
product removes its requests. CSV:
`/api/shop/alerts?format=csv`.

## Share and earn

Your customers earn by bringing you new ones (Shop > Share and earn):

1. Turn it on and choose what a friend gets (a percent off their first order, 0 to 90) and what the customer who
   shared gets for each new friend who buys (an amount off their next order, usable for the days you choose).
2. Everyone who pays for an order gets a code of their own, for example `ANA-7K3Q` (customers who paid before you
   turned it on get theirs when you turn it on). It is on their order page
   (with **Copy your link**, **Copy your code** and **Share**) and in their payment receipt.
3. A friend opens the link (the storefront with `?ref=ANA-7K3Q`): the code is already filled in at checkout and
   shown as "Friend's code" (also when friends get 0% off: the code is still added, so the sharer earns). It works
   only for a first order (a new email) and never for the person who shared it.
4. When you mark the friend's order paid, the customer who shared earns a single-use reward code (for example
   `THANKS-P4M2`, listed under Discounts) and gets an email (`referral_reward`). Marking paid twice earns once.
   Refunding, cancelling or marking that order unpaid takes back a reward that was not used yet; marking it paid
   again brings the same code back (no second email).

The tab shows everyone with a code: the friends they brought, what those friends paid, rewards earned and used,
with a CSV. Discount codes and share codes never clash.

## Customers

Everyone who ordered, merged by email (or phone): number of orders, what they paid, first and last order, and
whether they came back. Click one to see their orders; download a CSV.

## Messages

Order confirmations (`order_confirmation`), payment receipts (`payment_receipt`, with the customer's share code
when share and earn is on), shipping notices (`order_shipped`, with the tracking link), back-in-stock emails
(`back_in_stock`) and rewards (`referral_reward`) go to the shared outbox (Business hub > Outbox). They wait for your
approval unless you list the kind under automatic sending in Business hub > Email.

## In the Business hub

Revenue from paid orders adds up with your other apps. The Shop card shows revenue this month, orders to fulfil,
low-stock products and (when there are any) customers waiting for stock, and a setup checklist: business details, first product, storefront shared, first order,
first paid order.

## API

All under `/api/shop/`: `overview`, `settings`, `storefront` (+ `/link`), `products` (+ `/draft`, `/{id}`),
`orders` (+ `/{id}`, `/paid`, `/unpaid`, `/refund`, `/cancel`, `/fulfil`, `/link`), `quote`, `discounts`
(+ `/{id}`), `customers`, `alerts` (+ `/{id}`), `referrals`. Lists accept `?format=csv` (the orders CSV lists the
items, the address and the note). Money is integer cents. Public pages: `storefront` (actions `quote`,
`place_order`, `notify_me`) and `order` (action `notify_paid`; it shows the customer's share code once paid).
