"""Studio: win and bill client work (agencies, freelancers and consultants).

The main objects:

* **Clients** - the people and companies the business works for (lead, active, past), with what was billed and
  paid over time.
* **Proposals** - priced line items with a discount and tax, sent as a public page the client accepts (typing
  their name as a signature) or declines. An accepted proposal converts, in one step, into a project and an
  invoice.
* **Projects** - the work: tasks and time entries; billable time that has not been invoiced becomes an invoice.
* **Invoices** - numbered (INV-0001), with partial payments, overdue detection, a public page with the
  business's own payment link, and reminders through the shared outbox. Every payment is revenue in the
  Business hub; deleting a payment or voiding the invoice takes it back out.
* **Retainers** - a fixed monthly amount that becomes an invoice once per period.

Aira never moves money: customers pay through the business's own payment link or instructions, and the business
records the payment.
"""
