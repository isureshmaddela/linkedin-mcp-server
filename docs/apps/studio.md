# Sales (Studio)

Sales (its id and address stay `studio`) is the industry app for agencies, freelancers and consultants: win client work with proposals, deliver it
as projects, and get paid with invoices. It opens at `#/studio` (keyboard: `g` then `1`). In the hosted build an
organization turns it on under Apps (Starter $5, Team $4 per seat a month, included in Enterprise).

## The flow

1. **Add a client** (Clients): name, company, email, phone, tags, notes and a status (lead, active, past). Each
   client shows what you billed, what they paid and what they still owe. Search, filter by status or tag, and
   export the list as CSV.
2. **Write a proposal** (Proposals > New proposal). Type a short brief and choose *Write it for me*: Aira drafts
   the title, a friendly introduction, the scope (phases, what is not included) and the lines. You set the
   prices. Lines have a quantity and a unit price; a discount comes off the subtotal and your tax rate (from the
   business profile, changeable per proposal) is added. Proposals are numbered PRO-0001, PRO-0002, ...
3. **Send it.** *Send to client* creates the client page and puts an email with its link in the Business hub
   outbox (you approve it there, or turn on automatic sending for `proposal`). You can also copy the link from
   the *Client page* card and send it yourself. Sharing the page marks the proposal as sent.
4. **The client answers on the page.** They read the proposal and accept it by typing their full name (recorded
   with the time and address as their signature), or decline with a reason. You see it in the activity feed and
   on the Studio overview. A proposal past its *valid until* date shows as expired and can no longer be accepted.
   If a client agreed by phone or email you can mark it accepted or declined yourself.
5. **Start the work.** On an accepted proposal, *Start project and invoice* creates a project (with the
   proposal's total as its budget) and a draft invoice with the same lines. Doing it twice does nothing new.
6. **Run the project** (Projects): tasks with due dates, time entries (hours, note, rate, billable or not), the
   budget against what you invoiced, and *Invoice unbilled time*, which turns billable time into an invoice (a
   line per entry) and marks the time as billed.
7. **Invoice and get paid** (Invoices). Invoices are numbered INV-0001, ... and are due after your payment terms
   (14 days unless you change it in Settings). *Send to client* and *Send reminder* queue emails with the client
   page, which shows what is due and your own payment link or instructions (Business hub > Business profile).
   If the client pays, they can press *Let them know I paid* with a reference; Studio shows it on the invoice
   but never marks anything paid by itself. When the money arrives, choose *Record payment* (part payments are
   fine) or *Mark paid in full*.
8. **Retainers** (Retainers): a fixed amount every month for a client. *Create due invoices* drafts the
   invoices that have come due (catching up on missed months); a month is never invoiced twice.

## Sales: contacts, deals and activities

- **Contacts.** Each client has the people you talk to: name, role, email, phone and one main contact (the first
  one added; removing the main contact makes another one main). They are listed in the client's drawer.
- **Deals** (Deals tab) are the work you hope to win: a title, the client (and one of its contacts), a value, an
  expected close date, an owner, the next step, where it came from (a lead, a referral, the website, other) and
  a stage. Stages and their default chance of winning: new 10%, qualified 25%, proposal 50%, negotiation 75%,
  won 100%, lost 0%. Change the open stages' percentages in Settings. A deal's weighted value is its value times
  that chance, rounded half up to the cent.
- **The board** shows a column per stage. Nothing is dragged: each card has buttons to move it back or on, *Won*
  and *Lost*, and with a card focused the left and right arrow keys move it and Enter opens it. Marking a deal
  lost asks why. *Show as a list* gives a table with the same search and filter (open, won, lost); *Export CSV*
  downloads every deal.
- **Winning.** Link a proposal to a deal in the deal's drawer; when the client accepts it (on the client page or
  marked by you) the deal moves to won. On a won deal, *Start project and invoice* makes a project and a draft
  invoice. With an accepted linked proposal it uses the proposal's lines (the same as the proposal's own
  button); otherwise one line for the deal's value. Doing it twice does nothing new.
- **Activities** (Activities tab, and on each client and deal): calls, meetings, tasks and notes with a date and
  a done flag. Late ones are marked. The overview lists your activities due today or late.
- **Forecast** (under the board): weighted pipeline by expected close month for the next six months (deals
  without a date and late ones are counted apart), won this month and this quarter, win rate (won out of won plus
  lost), the average deal won and the average days from adding a deal to winning it.
- **Add from Leads.** When the Leads app has saved leads, *Add from Leads* lists them (Sales only reads them) and
  makes a client (the company), a contact (the person) and a new deal from one. A lead already added opens its
  deal instead. Without Leads data the button just says there is nothing to add.

## Statuses

| Invoice status | Meaning |
| --- | --- |
| draft | not sent; can be edited or deleted |
| sent | issued, nothing paid yet, not past due |
| partially_paid | some money received, not past due |
| overdue | money still owed after the due date |
| paid | paid in full |
| void | cancelled; stays on record, is not owed, and its payments stop counting as revenue |

An invoice with payments cannot be deleted or lowered below what was paid; void it instead. A sent invoice is
voided, not deleted, so your numbering has no gaps you cannot explain.

## Money and revenue

Amounts are kept in cents. A line is quantity x unit price rounded to the cent; tax is your rate on the subtotal
after the discount, rounded half up. Every payment you record is revenue in the Business hub in the month it was
received; removing a payment or voiding the invoice takes it back out. Aira does not take card payments: clients
pay through your own payment link (Stripe, PayPal, Razorpay, UPI, ...) or the instructions you give.

## Overview and hub

The overview starts with the pipeline, the weighted forecast, won this month and the win rate, then your
activities today and deals closing this month. It also shows what is outstanding and overdue, what was paid this month, proposals waiting for an answer,
accepted proposals to start, deadlines and tasks due in the next 14 days, and your top clients. The Business
hub shows Studio's setup checklist (business details, first client, first deal, first proposal sent, first invoice sent,
first payment) and its headline numbers, including *Pipeline value* and *Won this month*.

## Roles (hosted)

Owners, admins and coaches (as team members) use Studio; auditors can read it; candidates cannot. Data belongs to
the organization and is shared by its members.
