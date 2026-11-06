# Online payments with Razorpay

Customers can pay a business's orders, invoices, bookings, course enrolments and tickets online: by UPI, card,
netbanking or wallet on Razorpay's hosted page. Each payment link is made for the exact amount and carries the
order's reference. When Razorpay says it is paid, Monosage marks the order, booking or enrolment paid itself,
just as the business would by hand.

The code is in `aira/payments/`:

- `razorpay.py` is the API client (standard library only) and the two signature checks.
- `service.py` holds payment requests, webhook events, refunds and the registry of what can be paid.
- `apps.py` is each app's entry in that registry.

## Setting it up

1. **Razorpay keys.** Go to the Razorpay dashboard, then **Account & Settings > API keys**, and generate a key. In
   Monosage, open **Business hub > Business profile > Payments**:
   - Pick **Razorpay** as how customers pay.
   - Paste the **key id** (`rzp_test_...` or `rzp_live_...`) and the **key secret**.
   - Press **Save payments**, then **Test connection**.

   The key id is a plain setting. The key secret is stored encrypted in the secret store (the same way as the SMTP
   password), and is never sent back to the browser. The screen only says that it is saved.
2. **Test or live.** The mode comes from the key id: `rzp_test_` keys take test payments only, and Razorpay's test
   cards and test UPI IDs work with them. Switch to live keys once a test order has gone through end to end. Each
   payment request records the mode it was made in.
3. **The webhook.** The Payments screen shows your webhook address. In the Razorpay dashboard, go to **Account &
   Settings > Webhooks > Add new webhook**:
   - Paste the address.
   - Type a **secret**, and enter the same secret in Monosage as the **webhook secret**.
   - Tick these events: `payment_link.paid`, `payment_link.expired`, `payment_link.cancelled`, `payment.failed`,
     `refund.processed` and `refund.failed`.
4. **A public address (local build).** Razorpay and your customers must be able to reach this computer. Run a
   tunnel and enter its https address under **Business profile > Public address**:
   - Cloudflare Tunnel: `cloudflared tunnel --url http://127.0.0.1:8765`
   - ngrok: `ngrok http 8765`

   Customer links, payment-link callbacks and the webhook address then use that address. The hub warns while
   customer links still point to `127.0.0.1`. The hosted build already has a public https address and needs no
   tunnel.
5. **UPI apps (optional).** Enter your UPI ID (VPA, for example `yourshop@okaxis`). Public pages in INR then also show
   a **Pay with a UPI app** button (`upi://pay?pa=..&pn=..&am=..&tn=<reference>&cu=INR`) for phones. Monosage does
   not hear about these payments, so you confirm them by hand, as with a bank transfer.

The other choices are **No online payment** (customers see your payment instructions only) and **My own payment
link** (the fixed `payment_link` from the profile, as before). A business that had a payment link before this
feature starts on **My own payment link**.

### Webhook addresses

| Build | Address |
|---|---|
| Local | `https://<your public address>/api/payments/razorpay/webhook/<token>` |
| Hosted | `https://<deployment base URL>/api/payments/razorpay/webhook/<token>` |

The token is a public-link token (`aira.suite.public`, kind `payments/razorpay_webhook`). It names the business's
database: the account locally, the organization hosted, through the `public_links` index. So one server answers
every business's own Razorpay account. A token that is not a webhook token answers 404, and so does a page view of a
webhook token. In the hosted build the route is a `public_link` rule in `monosage_cloud/policy.py`, derived like
every other suite route. A suspended organization's webhook answers 404.

## The flow

1. **A payment request** is created in one of two ways:
   - By the customer: the storefront at checkout, a booking with a deposit, or the **Pay online** button on any
     public page that shows an amount due (order, booking, enrolment, tickets, newsletter, deck, analyst page).
   - By the business: `POST /api/suite/payments/requests` with `{app, object_type, object_id}`.

   Monosage calls `POST /payment_links` with:
   - the amount in paise and the currency;
   - a unique `reference_id` (`MS...`) and a description ("Order #1001");
   - the customer, when the page is the customer's own;
   - `notify` (off by default, so your own emails go out instead);
   - `callback_url`, which is the public page, and `callback_method=get`;
   - `expire_by`;
   - notes with the request id.

   An open link for the same amount is reused. If the amount changed, the old link is cancelled first.
2. **The customer pays** on Razorpay's page and returns to the public page with `razorpay_payment_link_*`
   parameters. The page posts them to `pay_return`, and the server checks
   `HMAC-SHA256(key_secret, plink_id|reference_id|status|payment_id)`. A valid return shows **Paid** at once. The
   server also fetches the payment from Razorpay and records it if the amount is right.
3. **The webhook is the source of truth.** `X-Razorpay-Signature`, the HMAC-SHA256 hex of the raw body with the
   webhook secret, is checked in constant time before anything is parsed. The body limit is 256 KB, and the local
   server refuses a larger one before reading it. Events are stored by `X-Razorpay-Event-Id`, so a retried delivery
   changes nothing. A delivery that fails part way is forgotten, so Razorpay's retry processes it again.
4. **Marking it paid.** The request is marked paid only when the payment's amount and currency equal the request's.
   The app is then told through its own `mark_paid`, with the Razorpay payment id in the note. Anything else makes
   the request a **mismatch**, shown in the hub with the reason:
   - another amount or currency arrived;
   - the payment was not captured;
   - the order was cancelled meanwhile.
5. **Other events:**
   - `payment_link.expired` / `payment_link.cancelled` close the request.
   - `payment.failed` notes the failed attempt (the customer can try again on the same link).
   - `refund.processed` / `refund.failed` settle refunds, including refunds made in the Razorpay dashboard.

Wired apps (`aira/payments/apps.py`):

| App | What is paid |
|---|---|
| Shop | Orders |
| Bookings | The deposit first, then the rest |
| Academy | Enrolments |
| Events | Ticket orders |
| Marketing | Won leads |
| Sites | Website projects |
| Learn | Deck sales |
| News | A month of a paid newsletter |
| Stocks | A month of an analyst's ideas |
| Play | Guest entry fees |

Ads top-ups are platform billing (`monosage_cloud/billing*.py`) and are not here. Studio invoices are not wired
yet. Pages that find the thing by a short reference (newsletter, deck, analyst, course) do not pre-fill the
customer's details on Razorpay's page.

## Refunds

From the order (`POST /api/shop/orders/<id>/refund` with an optional `amount_cents`) or from the hub
(`POST /api/suite/payments/requests/<id>/refund`), the business refunds all or part of a payment. Monosage calls
`POST /payments/{id}/refund`. Once Razorpay says the refund is processed (at once, or through `refund.processed`):

- **The ledger** gets a **negative line dated in the refund's month** (`kit.void_revenue(..., refund_cents=)` /
  `kit.record_refund`). The sale keeps counting in its own month.
- **The request** becomes `partially_refunded` or `refunded`.
- **The Shop**:
  - records the refunded amount;
  - on a full refund, returns the stock and the discount use, cancels an order that has not shipped, and takes
    back the referral reward.

A Shop order paid by hand is refunded the same way, without calling Razorpay.

## The ledger and GST

- **Currencies.** Every revenue line carries its currency. `kit.revenue` gives the business currency at the top
  level and every other currency under `by_currency`, and never adds them together. The hub shows a card per
  other currency.
- **Months.** Months are the business's months: its time zone, from the Business profile. Fixed-offset zones such
  as `Asia/Kolkata` and `UTC+05:30` work without a time-zone database.
- **Tax-inclusive prices.** With **My prices already include tax**, nothing is added on top and the tax is shown
  as included.
- **GST.**
  - Choose **GST (India)**, enter your GSTIN as the tax number, and price in INR. The GSTIN's check character is
    verified, and your state is read from its first two digits.
  - Products take an HSN/SAC code, printed on the order lines.
  - The storefront asks for the customer's state; a business buyer's GSTIN gives it.
  - The same state gives **CGST + SGST** (half the rate each, the odd paisa on CGST). Another state gives
    **IGST**.
  - One rate applies to the whole order (`tax_rate_bp`). Per-product GST rates are not built yet.
- **Flat rate.** The flat `tax_rate_bp` works as before for businesses that do not use GST.

## Limits and protections

- Storefront orders:
  - **Unpaid orders lapse** and their stock comes back (Shop settings): 30 minutes when the customer was sent to
    pay online, plus a 10-minute grace for a payment already under way; 48 hours for manual payment; 0 means
    never. The Shop's background task does this.
  - A payment that lands after an order lapsed takes the stock again if it is still there. Otherwise the payment
    is shown as a mismatch to refund.
  - Orders entered by the shop, and business orders on credit terms, never lapse.
- **Caps:** at most 5 storefront orders an hour per email or phone, and 20 per visitor address (stored hashed).
  Both can be changed in the Shop settings.
- **Bot checks:** a hidden honeypot field, and a signed form token that must be at least 3 seconds and at most
  24 hours old.
- **Confirmations:**
  - An order paid online is confirmed by its payment receipt once it is paid, and the receipt lists the items.
  - An order paid by hand gets its confirmation at once, but one address receives at most 3 a day.
- **The public address** reaches only the public page `/p/<token>`, the files it loads (`public*.js`, `public.css`,
  `app-*.css`, `i18n`), the public link API and the payment webhook. Every other route stays loopback-only, signed
  in or not. Webhooks have their own rate limit (600 a minute), separate from customers'.
- **Testing.** `AIRA_RAZORPAY_API_BASE` points the client at another API base. It must be https, or http to this
  machine; the tests' stub is `tests/razorpay_payments_stub.py`.

## Not built yet

- **Razorpay Route.** Payouts to creators and marketplace sellers (and taking a platform cut) are not built.
  Creators' payout details are still held by the platform (see P0-6 in the platform review).
- **Razorpay Checkout.** The client can create orders (`POST /orders`) for a future embedded Checkout, but no
  screen uses them yet.
- **Studio.** Studio invoices still use the business's own payment link.
- **Stripe.** Stripe Connect for businesses outside India is not built.
