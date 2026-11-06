"""Online payments for the business apps: a customer pays an order, an invoice or a booking, and the app hears it.

* :mod:`aira.payments.razorpay` - a small stdlib client for Razorpay (payment links, orders, payments, refunds)
  and the two signature checks (webhooks and the payment-link return).
* :mod:`aira.payments.service` - provider-neutral: payment requests and the events that change them, the
  registry where each app says what can be paid and what to do when it is, refunds as dated ledger lines.
* :mod:`aira.payments.apps` - each app's entry in that registry (Shop orders, Bookings, Academy, Events...).

The setup guide is ``docs/payments-razorpay.md``.
"""
