# Service

A helpdesk for any business that answers customers: shops, clinics, agencies, schools, software teams. Every
question becomes a case with a due time, your team answers from one inbox, customers follow their request on
their own page and rate the help, and a help centre with live chat answers many questions before they reach you.
Help that is worth paying for (a setup call, a repair, a priority answer) can be sold from the help centre or
charged on any case, and the money counts in the Business hub's revenue.

## Getting started

1. **Business hub > Business profile**: your name, contact details, time zone and your **payment link** (and
   payment instructions, if you take bank transfers). Due times in business hours follow that time zone, replies
   are signed with your name, and customers pay you through your own link: Aira never takes the money.
2. **Service > Help centre**: add a category or two, then write the answers to the questions customers ask most.
   Publish them (a draft only your team sees) and **Create link** for your help centre.
3. **Service > Paid help** (optional): add the services you charge for, with a price. They appear in your help
   centre under **Paid help**.
4. **Service > Live chat > Create link** if you want visitors to talk to you live. Put the help centre and chat
   links on your website, social profiles and email signature.
5. **Service > Settings**: how fast you aim to reply and solve for each priority (in hours; 0.5 is half an hour),
   your business hours, and when live chat is on.

## Where cases come from

* **The help centre's contact form** (channel *web*): name, email, optional subject and the message. While the
  customer writes, answers from your help centre that might already help (sharing whole words with what they
  wrote) appear under the message ("These answers
  might help"), so many people find the answer without waiting. The customer gets a request number, an email
  confirming it and the link to their request.
* **Paid help** (channel *web*, tag *paid help*): the customer picks a service, leaves their name, email, an
  optional phone and message, and gets one email with their request number, the price and how to pay. The case
  carries the charge.
* **Live chat** (*chat*): turn any chat into a case with **Make it a case**; the conversation is copied message by
  message, and an answer your team already gave in the chat counts as the first reply. When chat is off, visitors
  see your away message and leave a message, which becomes a case.
* **By hand** (*manual*, *phone*, *email*): **New case** for calls, emails you received and walk-ins.

## Working a case

The **Inbox** has views: Open, My cases (assigned to you), Unassigned, Due soon (within two hours), Overdue,
Waiting on customer, Solved, Closed and All. Search by subject, customer, phone (typed with or without spaces and
dashes) or number; filter by priority and
channel; click a tag to see every case with it. **Download CSV** exports what is on screen, filters included.
Tick cases (or **Select all**) to **Assign to me**, **Mark solved**, **Close** or **Delete** several at once; a case
the action cannot apply to (a closed case to solve, a case with a payment to delete) is skipped with the reason.

Open a case to see the conversation, change the status, priority, assignee and tags, edit its subject and the
customer's details, and reply:

* **Reply** goes to the customer through Business hub > Outbox (kind `case_reply`) with the link to their
  request page. Choose what happens after sending: wait for the customer (the default), keep it open, or mark it
  solved. Attach files as https links.
* **Internal note** stays inside the team; the customer never sees it.
* **Saved replies** insert a prepared answer; `{customer}` becomes the customer's first name and `{case}` the
  case number.
* **Help articles**: "Articles that may answer this" lists published answers that match the case; one click puts
  the article in the reply (its title and link when the help centre is shared, otherwise its text). Any published
  article can also be inserted from the list.
* **Suggest a reply** drafts an answer from the conversation with your writing model; without one it starts
  from a polite template. Always read it before sending.
* **Merge into another case** moves the messages and charges of a duplicate into the case you keep and closes the
  duplicate. The duplicate's customer link shows the kept case, and they can reply, pay and rate it there. Only
  cases from the same customer merge (the first detail both cases have, email, then phone, then name, must match),
  so nobody ever sees someone else's conversation; correct the details with **Edit details** if it is the same
  person. A kept case with no customer details takes the duplicate's.
* **Delete case** removes it for good with its messages, charges and customer link (for spam, tests and
  mistakes), together with the duplicates merged into it. A case with a payment you marked received (on it or on
  a duplicate merged into it) cannot be deleted until you mark it unpaid.
* **Earlier requests from this customer** lists their other cases, by email (or by name when there is no email,
  or by phone number when they left only a phone).

Statuses: *new* → *open* → *waiting on customer* → *solved* → *closed*. A customer's reply reopens a waiting or
solved case. A closed case cannot change; the customer starts a new request.

## Paid help: earning from Service

* **Paid services** (Service > Paid help): a name, what the customer gets and a price. Hide one from the help
  centre without deleting it; deleting it keeps the charges already made.
* **Charges on a case**: **Add a charge** in the case (pick a paid service or type what it is for and the amount).
  The customer's request page shows what is due with your **Pay** button and payment instructions, and an email
  with the amount (`case_charge`) waits in the outbox.
* **Getting paid**: when the money arrives, **Mark paid** (in the case or under Paid help > Waiting to be paid).
  The payment goes into the Business hub's revenue (once, however many times it is marked), the customer gets a
  receipt (`payment_receipt`) and their page shows it paid. **Mark unpaid** takes it back out of the revenue. A due
  charge can be removed; a paid one must be marked unpaid first.
* **Paid help** shows what you earned this month and in total, what is waiting to be paid, and the latest
  payments; each paid service shows how often it was paid and what it earned.

## Due times (SLA)

Each priority has a first reply target and a solution target (defaults: urgent 1 h and 8 h, high 4 h and 24 h,
normal 8 h and 2 days, low 24 h and 5 days). With business hours on, only your working days and hours count:
a high case that arrives on Friday at 16:00 with hours 09:00 to 17:00 is due for a first reply on Monday at
12:00. Cases past either target show as overdue.

## Customers

**Service > Customers** lists everyone who asked for help (grouped by email, or by name when there is no email, or
by phone number when that is all they left):
how many requests, how many are open, the last contact, their average rating, and what they paid. Search by name,
email or phone, download the list as CSV, and open a customer to see all their requests.

## Customers' pages

* **Help centre**: search, read an answer, say whether it helped (one vote per visitor, counted in the articles
  table with the views), ask for a paid service, start a live chat while it is on, or contact you (with answers
  suggested while they write).
* **Live chat**: a name, email and first question start the chat; new messages appear every two seconds and the
  chat survives a page reload in that browser. Answers show your business name, never a team member's own email.
  Your team answers under **Live chat**; a waiting chat shows on the Service menu badge.
* **Request page**: the conversation (internal notes never appear), what is due and how to pay, a reply box, and
  once the case is solved a 1 to 5 rating with an optional comment (once per case).

## In the background

Every few minutes Service marks cases that passed their due time, prepares the rating request email
(`csat_request`) for solved cases, and closes solved cases with no customer reply after the number of days in
Settings (7 by default). Emails it prepares: `case_received`, `case_reply`, `case_charge`, `payment_receipt` and
`csat_request`. Nothing is sent until you approve it in the Outbox, unless you list these kinds for automatic
sending under Business hub > Email.

## Reports

Open (and unassigned) and overdue cases; the average time to the first reply and to a solution, with the share
that met their due time; the satisfaction average and the number of ratings; cases solved and created this week;
paid help earned this month and what is waiting. Then **Your team** (per person: open, solved, average first reply,
rating), **Latest ratings** with the customers' comments, **What customers ask about** (the most used tags in the
last 30 days), cases by channel, the getting-started checklist and the paid services sold. The Business hub shows
the open, overdue and satisfaction figures (once you offer paid help: open cases with the overdue count, paid help
this month and satisfaction), and a getting-started
checklist (profile, a published answer, a published help centre, a shared contact or chat link, the first solved
case).

## In the Aira frame

Service lives at `/service`, with its own logo, the search box across the top, **+ New** above the menu and the
launcher, like the other Aira apps.

* **+ New** offers *New case*, *New help article*, *New paid service* and *New saved reply*; each opens that tab
  with its form open (the address drops `?new=` straight away, so Back and refresh do not open it again). On a phone,
  where the menu is folded away, the Inbox and Customers pages keep their own **New case** button. A child or teen
  profile, and a hosted auditor or billing contact, are offered nothing to create.
* **Search** suggests up to six of Service's own records as you type: customers (by name, email or phone), cases
  (by subject, customer, phone or case number, showing number, status and customer) and help articles (by title).
  Choosing one opens it: the case's drawer in the Inbox, the customer's history, or the article's editor. **Enter**
  shows every matching case in the Inbox (*All cases* with the search filled in), with a link to the same search
  in Customers.
* **The menu's folders** are Service's tabs, each with its icon: Inbox, Customers, Live chat, Help centre, Paid
  help, Saved replies, Settings and Reports. The page head names the open folder.

## In the hosted product

In the single-user build Service is for the grown-ups of the household: a child or teen profile is refused,
since Service holds customers' details and money. Service is an add-on in the hosted product. Owners, admins and
coaches manage it (cases, charges, paid services, marking payments);
auditors can read it (including customers, reports and paid help); candidates do not see it. The cases, chats,
customers, charges and help centre belong to the organization, and its public pages work for anyone with the link
once the organization subscribes.

## API

Everything is under `/api/service/`: `overview`, `settings`, `reports`, `cases` (with `view`, `q`, `status`,
`priority`, `channel`, `tag` and `format=csv`), `cases/bulk` (`ids`, `action`: assign, solve, close or delete,
`assignee`), `cases/{id}` (GET, PATCH, DELETE) with `reply`, `merge`, `suggest`, `link` and `charges`,
`charges/{id}` (DELETE) with `paid` (`{"paid": true|false}`), `offers` and `offers/{id}`, `customers` (with `q` and
`format=csv`) and `customers/cases?key=`, `macros` (with `use`), `categories`, `articles`, `help/link`,
`chat/link` and `chats/{id}` (with `after` for new messages, `reply`, `end` and `case`). Public pages:
`help_center` (actions `search`, `article`, `vote`, `contact`, `request`; query `answers`), `chat` and `case`
(actions `reply`, `rate`; the page includes `charges` and `due_cents`).
