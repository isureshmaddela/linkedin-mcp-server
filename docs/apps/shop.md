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

## Selling to businesses and consumers (B2B and B2C)

**Who you sell to** is set once for every app (Business hub > Business profile, or Shop > Business buyers):
consumers (B2C, the default: nothing changes), businesses (B2B) or both.

When you sell to businesses:

* **Trade accounts.** A business applies on your storefront ("Buying for a business?": business name, contact,
  email, phone, tax number, address), or you add one yourself (Business buyers > Add a business). You approve it
  with **payment terms** (pay when ordering, or net 7, 15, 30 or 60 days), a **percent off** your trade prices, an
  optional **credit limit** and, for a reseller with a certificate, **no tax**. Approval makes a **trade code**
  (like `ABCD-EFGH-JKLM`), shown to you once and emailed to the business (`trade_approved`). The code is stored only
  as a hash: you can make a new one (the old one stops working) but never read it again. Pause an account to stop
  it ordering; one with orders is paused rather than deleted. Settings > "New trade accounts" can approve every
  application at once on the default terms.
* **Trade prices.** Each product can have a **trade price** (what businesses pay; empty = the price), a
  **minimum quantity** for businesses and **price breaks** (`10 | 9.00` = from 10, 9.00 each; the quantity counts
  every option of the product). An option keeps its difference from the product's price. **Who can buy it**:
  everyone, consumers only or businesses only. A business may order up to 100,000 of a product (consumers 99).
* **On the storefront** a business signs in with its email and trade code (for that browser session). It sees its
  trade prices, minimums and price breaks, what it saves, its terms and its credit, and checks out with a **PO
  number** (Settings can require one) on its account: the order uses the account's contact details and address.
  A shop for businesses only shows its products without prices until a business signs in, and takes no consumer
  orders.
* **Orders** from businesses carry the business, its tax number, the PO number, the terms and a **due date**; the
  order page is the **invoice** (your and their tax numbers, Print or save as PDF). An order on terms that would
  take a business over its credit limit is refused. You can enter an order for a business yourself (New order >
  Buyer).
* **What businesses owe** (Business buyers): unpaid business orders with their due dates and how late they are,
  per business, with **Remind** (`payment_reminder`) and a CSV. The overview shows business (B2B) and consumer
  (B2C) sales this month, what is owed and overdue, and accounts waiting for approval.

Sites also writes for the buyer you chose: a B2B site leads with trade terms and "Request a quote".

## Customers

Everyone who ordered, merged by email (or phone): number of orders, what they paid, first and last order, and
whether they came back; a business shows under its name. Click one to see their orders; download a CSV.

## Messages

Order confirmations (`order_confirmation`), payment receipts (`payment_receipt`, with the customer's share code
when share and earn is on), shipping notices (`order_shipped`, with the tracking link), back-in-stock emails
(`back_in_stock`), rewards (`referral_reward`), trade codes (`trade_approved`) and payment reminders
(`payment_reminder`) go to the shared outbox (Business hub > Outbox). They wait for your
approval unless you list the kind under automatic sending in Business hub > Email.

## In the Business hub

Revenue from paid orders adds up with your other apps. The Shop card shows revenue this month, orders to fulfil,
low-stock products and (when there are any) customers waiting for stock, and a setup checklist: business details, first product, storefront shared, first order,
first paid order.

## API

All under `/api/shop/`: `overview`, `settings`, `storefront` (+ `/link`), `products` (+ `/draft`, `/{id}`),
`orders` (+ `/{id}`, `/paid`, `/unpaid`, `/refund`, `/cancel`, `/fulfil`, `/link`, `/remind`; `?buyer=business` or
`consumer`), `quote`, `discounts` (+ `/{id}`), `customers`, `alerts` (+ `/{id}`), `referrals`, `trade-accounts`
(+ `/{id}`, `/approve`, `/decline`, `/code`), `receivables`. `quote` and `orders` take `trade_account_id` for a
business. Lists accept `?format=csv` (the orders CSV lists the items, the address, the note and the business
fields). Money is integer cents. Public pages: `storefront` (actions `quote`, `place_order` and, with
`trade_email` and `trade_code`, the same for a business; `notify_me`, `trade_apply`, `trade_sign_in`) and `order`
(action `notify_paid`; it shows the customer's share code once paid, and a business order's invoice).
