# Sales (Studio)

Sales (its id and address stay `studio`) is the industry app for agencies, freelancers and consultants: win client work with proposals, deliver it
as projects, and get paid with invoices. It lives at `/sales` and opens at `#/studio` (keyboard: `g` then `1`). In the hosted build an
organization turns it on under Apps (Starter $5, Team $4 per seat a month, included in Enterprise).

## In the Aira frame

Sales looks and works like the other Aira apps (one account, its own address, logo, search box and launcher):

- **+ New** (above the left menu) offers *New deal*, *New client*, *New proposal*, *New invoice* and *New
  activity*. Each opens the same form as the button on its tab (`#/studio?tab=<tab>&new=<kind>`; the page takes
  `new` off the address first, so Back and refresh do not open it again). A child's or teen's profile, and in the
  hosted build a member who may only read the business apps, get no button.
- **Search** (the box across the top, *Search Sales*) suggests up to six matches as you type, taking turns between
  clients (by name, company, email, phone, tag or one of their contacts), deals (title, next step, client),
  invoices (number, client), proposals (number, title, client) and projects (name, client); choosing one opens it
  (`?client=`, `?deal=`, `?invoice=`, `?proposal=`, `?project=`). Enter shows every kind that matches, up to 25 of
  each, at `#/studio?tab=search&q=`, with a link to the tab's own search when a kind is full. Answers come from
  `GET /api/studio/search?q=&limit=` (`aira/suite/studio/search.py`, newest first, LIKE wildcards taken as text).
- **Folders**: the tabs (Overview, Deals, Clients, Activities, Proposals, Projects, Invoices, Retainers, Earnings,
  Settings) are folders with their own icons in the left menu, and each page is headed by its folder's name. On a
  wide screen the page's own tab bar steps aside; phones and tablets, where the menu is a sheet, keep it.

## The flow

1. **Add a client** (Clients): name, company, email, phone, tags, notes and a status (lead, active, past). Each
   client shows what you billed, what they paid and what they still owe. Search, filter by status or tag, and
   export the list as CSV.
2. **Write a proposal** (Proposals > New proposal). Type a short brief and choose *Write it for me*: Aira drafts
   the title, a friendly introduction, the scope (phases, what is not included) and the lines. You set the
   prices. Lines have a quantity and a unit price; a discount comes off the subtotal and your tax rate (from the
   business profile, changeable per proposal) is added. Proposals are numbered PRO-0001, PRO-0002, ...
   You can also ask for a **deposit** (see *Deposits* below).
3. **Send it.** *Send to client* creates the client page and puts an email with its link in the Business hub
   outbox (you approve it there, or turn on automatic sending for `proposal`). You can also copy the link from
   the *Client page* card and send it yourself. Sharing the page marks the proposal as sent.
4. **The client answers on the page.** They read the proposal and accept it by typing their full name (recorded
   with the time and address as their signature), or decline with a reason. You see it in the activity feed and
   on the Studio overview. A proposal past its *valid until* date shows as expired and can no longer be accepted.
   If a client agreed by phone or email you can mark it accepted or declined yourself.
5. **Start the work.** On an accepted proposal, *Start project and invoice* creates a project (with the
   proposal's total as its budget) and a draft invoice with the same lines (less the deposit, if one was invoiced).
   Doing it twice does nothing new; if you delete or void that invoice, the button comes back and makes a new one
   (for the same project).
6. **Run the project** (Projects): tasks with due dates, time entries (hours, note, rate, billable or not), the
   budget against what you invoiced, and *Invoice unbilled time*, which turns billable time into an invoice (a
   line per entry) and marks the time as billed.
7. **Invoice and get paid** (Invoices). Invoices are numbered INV-0001, ... and are due after your payment terms
   (14 days unless you change it in Settings). *Send to client* and *Send reminder* queue emails with the client
   page, which shows what is due and your own payment link or instructions (Business hub > Business profile).
   If the client pays, they can press *Let them know I paid* with a reference; Studio shows it on the invoice
   but never marks anything paid by itself. When the money arrives, choose *Record payment* (part payments are
   fine) or *Mark paid in full*.
8. **Retainers** (Retainers): a fixed amount every month for a client. Sales drafts each invoice on its date by
   itself (and can send it, see below); *Create due invoices* does it at once, catching up on missed months. A
   month is never invoiced twice. Pausing a retainer skips the months it is paused: when you resume it, the next
   invoice is the first date from today on (same day of the month).

## Deposits

A proposal can ask for a deposit: a percent of the total before tax (*Deposit when accepted (%)* in the proposal,
with a default for new proposals in Settings). The proposal, its email and the client page say how much it is.
When the client accepts (on the page, or you mark it accepted), Sales at once makes a **deposit invoice** (one line,
the same tax rate, due that day), shares its page and puts its email in the outbox. Right after accepting, the
client page shows *Pay your deposit to get started* with your payment link and a link to the deposit invoice.
*Start project and invoice* later takes the deposit (tax included) off the final invoice as *Deposit already
invoiced (INV-...)*, so the two together come to the proposal's total; the editor of that invoice shows the
deposit taken off. A voided deposit invoice is not taken off, and voiding the deposit after the final invoice exists
puts the full amount back on the final invoice (noted in the activity feed). The deposit invoice joins the project,
so the project's earnings include it. Deposit invoices are marked *deposit* in the app and called *Deposit invoice*
on the client's page, where an unpaid one says *Not paid yet*. The default deposit in Settings takes the same
percents as a proposal (12.5 is fine).

A proposal can be answered once: when the client accepts in two tabs at the same moment, or while you mark it
accepted, only the first answer counts, so there is only ever one deposit invoice and one email. Invoice and
proposal numbers are never given twice.

## What Sales does by itself

Every few minutes (the platform's background run, also when Aira is closed; *Check now* in Settings does it at
once), Sales can:

- **draft retainer invoices** on their date (on unless you turn it off), and **send** them (off unless you turn it on);
- **remind clients about overdue invoices** (off unless you turn it on): the first reminder a number of days after
  the due date (3), then every few days (7), up to a limit per invoice (3). A reminder you send by hand counts too.
  When a late invoice's client has no email address nothing is sent, and *Check now* says how many were left out
  so you can add the address;
- **follow up a proposal** that has had no answer for a number of days (0, never, unless you set it), once.

Every email waits in the Business hub outbox for your approval, unless you listed its kind (`invoice`,
`invoice_reminder`, `proposal_followup`) under automatic sending. Nothing is ever marked paid by itself.
An account that never used Sales is left alone.

## Earnings

The Earnings tab shows how the business is doing, for the last 3, 6, 12 or 24 months:

- **Received** in the period, this year, the average a month and the best month; payments count in the month they
  arrived, and payments on void invoices do not count;
- **Money received each month** as bars, with what was invoiced in the same months;
- **Who pays you most**: clients ranked from top to lowest earner, with their share of your income, how many
  payments and what they still owe (the lowest are marked so you can look at them);
- **Still owed, by how late it is**: not due yet, 1-30, 31-60, 61-90 and more than 90 days late, with a link to the
  overdue invoices;
- **Days to get paid**: the average from an invoice's date to its last payment, over the invoices paid in full;
- **Which work pays best**: each project's invoiced amount for every hour logged on it (low rates show work to price
  higher next time);
- **How clients paid**, by payment method; *Export payments (CSV)* downloads the payments on screen (from the start
  of the period up to today).

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
- **Exports.** Clients, deals, invoices and payments export as CSV; amounts are written as money (`4000.50`), and an
  export follows the search and filter on the screen.
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

## Who can use it

In a household (the single-user build), Sales is for the grown-ups: a child's or teen's profile sees *Sales is for
the grown-ups* with a *Switch person* button, and every Sales route answers 403 (`code: studio_grown_ups`) for
them. The client pages (a proposal to accept, an invoice to pay) are public and unaffected.

## Roles (hosted)

Owners, admins and coaches (as team members) use Studio; auditors can read it (Earnings included, not *Check
now*); candidates cannot. Data belongs to the organization and is shared by its members.

## For developers

Routes added for the features above: `GET /api/studio/reports?months=`, `GET /api/studio/payments/export?since=&until=`,
`POST /api/studio/automation/run`; `deposit_percent` on proposal create and update; settings `auto_retainers`,
`retainer_auto_send`, `auto_reminders`, `reminder_after_days`, `reminder_every_days`, `reminder_max`,
`proposal_followup_days`, `default_deposit_percent`. The background task is registered in `studio_routes.py`
(`service.tick`). Newer columns (`deposit_bp`, `deposit_invoice_id`, `followed_up_at` on proposals; `credit_cents`,
`credit_label`, `credit_invoice_id`, `kind`, `reminder_count` on invoices) are added to older databases by
`store.ensure`, which also repairs older rows once (a final invoice learns which deposit it took off, a deposit joins
its project, a proposal pointing at a void invoice is freed) and adds unique indexes on the invoice and proposal
numbers (`store.insert_numbered` tries the next number when another request took it). Answers to a proposal change
it only if its status is still the one read (`service._change_if`). The background result has `no_email` beside
`retainer_invoices`, `reminders` and `followups`. Tests: `tests/test_suite_studio.py`, `test_suite_studio_sales.py`,
`test_suite_studio_earn.py`, `test_suite_studio_fixes.py` and the browser tests `test_suite_studio_ui.py`,
`test_suite_studio_earn_ui.py`, `test_suite_studio_fixes_ui.py`. The frame: `test_suite_studio_search.py` and the
browser test `test_suite_studio_shell_ui.py`.
