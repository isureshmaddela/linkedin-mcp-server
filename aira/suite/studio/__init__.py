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
* **Deposits** - a proposal can ask for a share up front: accepting it sends a deposit invoice at once, and the
  final invoice takes it off.
* **Earnings** - money received by month, clients ranked by what they paid, what is owed by how late it is, days
  to get paid, and what each project earned per hour logged.

By itself (the background task, ``service.tick``) Studio drafts and can send retainer invoices, reminds clients
about overdue invoices and follows up unanswered proposals; every email waits in the outbox for approval.

Aira never moves money: customers pay through the business's own payment link or instructions, and the business
records the payment.
"""
