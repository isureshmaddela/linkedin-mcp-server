# Razorpay for plan subscriptions

Monosage Cloud can bill its plans (Starter, Team, app add-ons, metered overage and ad top-ups) through
[Razorpay Subscriptions](https://razorpay.com/docs/api/payments/subscriptions/) instead of Stripe. Customers pay
in Indian rupees with cards, UPI AutoPay or e-mandates on Razorpay's hosted page; Monosage never sees card or bank
details.

This is plan billing for the hosted product. Payment links that the business apps send to their own customers are
a separate integration (`aira/payments/`).

## Setup

1. In the Razorpay Dashboard, switch to **Test mode** first and create an API key (**Account & Settings > API keys**).
2. Set the environment of the web tier:

   | Variable | Value |
   | --- | --- |
   | `AIRA_CLOUD_BILLING` | `razorpay` |
   | `AIRA_CLOUD_RAZORPAY_KEY_ID` | the key id (`rzp_test_...`, later `rzp_live_...`) |
   | `AIRA_CLOUD_RAZORPAY_KEY_SECRET` | the key secret |
   | `AIRA_CLOUD_RAZORPAY_WEBHOOK_SECRET` | the secret you give the webhook (step 3) |
   | `AIRA_CLOUD_RAZORPAY_API_BASE` | default `https://api.razorpay.com`; tests point it at `tests/cloud_razorpay_stub.py` |
   | `AIRA_CLOUD_RAZORPAY_TOTAL_COUNT` | billing cycles a subscription is created for (default `120`, ten years) |
   | `AIRA_CLOUD_RAZORPAY_INR_PER_USD` | fixed rupees per dollar for amounts without an explicit rupee price (default `85`) |

3. Add a webhook (**Account & Settings > Webhooks**):
   - URL: `https://<your host>/api/billing/webhook/razorpay`
   - Secret: the value of `AIRA_CLOUD_RAZORPAY_WEBHOOK_SECRET`
   - Active events:
     - `subscription.activated` (the checkout completed, or a halted subscription recovered)
     - `subscription.charged` (each cycle's payment: the invoice is paid and the period moves on)
     - `subscription.pending` and `subscription.halted` (a charge failed: the organization is past due)
     - `subscription.updated` (a plan or seat change was applied: confirms an upgrade)
     - `subscription.cancelled` and `subscription.completed` (the subscription ended)
     - `payment.failed`
     - `invoice.paid`
     - `refund.processed` (takes back an ad top-up that a refunded payment had credited)
4. Restart the web tier. Run a checkout in test mode and watch **Platform > Billing events** fill.

The webhook is verified with `X-Razorpay-Signature` (the hex HMAC-SHA256 of the raw body with the webhook secret,
compared in constant time). Each event is applied once, keyed by `X-Razorpay-Event-Id`, and an event older than the
newest one already applied to the subscription (by `created_at`) does not change its state.

## How plans map to Razorpay

- **Customers**: `POST /v1/customers` with the organization's name, the owner's email and `notes.org_id`.
- **Plans**: one Razorpay plan per Monosage plan and set of app add-ons and rupee price (`period: monthly`,
  `interval: 1`, the amount per candidate seat in paise). Razorpay plans cannot change, so a new price makes a new
  plan; the ids are cached in the control plane (`cloud_kv`, `razorpay:plan:*`).
- **Checkout**: `POST /v1/subscriptions` with the plan, `quantity` = candidate seats, `total_count`,
  `customer_notify` and `notes` (`org_id`, `plan_id`, `seats`). The owner is sent to the subscription's `short_url`
  to authorise it. Starting another checkout cancels the one still open, so only one can be paid.
- **Upgrades** (more seats, or a higher plan): `PATCH /v1/subscriptions/{id}` with the new plan and quantity and
  `schedule_change_at: now`. The upgrade is shown as pending and applies when `subscription.updated` confirms it.
  Razorpay does not prorate, so the difference for the rest of the period is added as an add-on and charged with
  the next cycle.
- **Downgrades** apply at the end of the period (`schedule_change_at: now` at that moment).
- **Cancellation**: `POST /v1/subscriptions/{id}/cancel` with `cancel_at_cycle_end: true`; service continues to the
  end of the cycle, then the export window starts.
- **Overage** (Team and Enterprise): at the end of each Razorpay billing period (the period `subscription.charged`
  reports, not the calendar month) the excess is added with `POST /v1/subscriptions/{id}/addons`, one add-on per
  metric, charged with the next cycle. A period is claimed before Razorpay is called and marked billed only after it
  answers, so a failure is retried and overlapping ticks cannot bill it twice.
- **Ad top-ups** are add-ons too. The ad balance is credited only when the cycle charge that carries them is paid
  (`subscription.charged` or `invoice.paid`), never when the add-on is created; a failed charge credits nothing and
  `refund.processed` takes the credit back.
- **Portal**: Razorpay has no customer portal. **Manage billing** opens the in-app billing page; Razorpay's own
  emails carry the link to update the payment method.

## Rupee prices

The catalog is priced in US dollars. Rupee prices are set by hand, not converted at a live rate
(`monosage_cloud/plans.py`):

| Plan | USD per seat per month | INR per seat per month |
| --- | --- | --- |
| Starter | $14.00 | ₹1,199 |
| Team | $11.00 | ₹949 |

| App add-on (USD per seat) | INR per seat |
| --- | --- |
| $5.00 | ₹429 |
| $4.00 | ₹339 |
| $3.00 | ₹259 |
| $2.00 | ₹169 |
| $1.50 | ₹129 |

A plan the superadmin creates can carry its own rupee price (`inr_seat_paise_month` in its pricing). Anything with
no explicit rupee price (a changed seat price, overage, ad top-ups) uses `AIRA_CLOUD_RAZORPAY_INR_PER_USD`. Change
these before going live if your pricing differs; existing subscriptions keep the Razorpay plan they were created on
until they change plan or seats.

## Limits

- Razorpay add-ons are charged with the next cycle, so an ad top-up made through Razorpay reaches the balance at the
  next renewal, not at once (Stripe charges it immediately).
- A refund in rupees of a payment that carried dollar top-ups reverses every top-up that payment paid.
- Subscriptions authorised with UPI or e-mandate cannot be updated through the API (Razorpay's rule); a plan change
  for those needs a new checkout.
