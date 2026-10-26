# Service

A helpdesk for any business that answers customers: shops, clinics, agencies, schools, software teams. Every
question becomes a case with a due time, your team answers from one inbox, customers follow their request on
their own page and rate the help, and a help centre with live chat answers many questions before they reach you.

## Getting started

1. **Business hub > Business profile**: your name, contact details and time zone. Due times in business hours
   follow that time zone, and replies are signed with your name.
2. **Service > Help centre**: add a category or two, then write the answers to the questions customers ask most.
   Publish them (a draft only your team sees) and **Create link** for your help centre.
3. **Service > Live chat > Create link** if you want visitors to talk to you live. Put the help centre and chat
   links on your website, social profiles and email signature.
4. **Service > Settings**: how fast you aim to reply and solve for each priority, your business hours, and when
   live chat is on.

## Where cases come from

* **The help centre's contact form** (channel *web*): name, email, optional subject and the message. The
  customer gets a request number, an email confirming it and the link to their request.
* **Live chat** (*chat*): turn any chat into a case with **Make it a case**; the conversation is copied in.
  When chat is off, visitors see your away message and leave a message, which becomes a case.
* **By hand** (*manual*, *phone*, *email*): **New case** for calls, emails you received and walk-ins.

## Working a case

The **Inbox** has views: Open, My cases (assigned to you), Unassigned, Due soon (within two hours), Overdue,
Waiting on customer, Solved, Closed and All. Search by subject, customer or number; filter by priority and
channel; download the view as CSV.

Open a case to see the conversation, change the status, priority, assignee and tags, and reply:

* **Reply** goes to the customer through Business hub > Outbox (kind `case_reply`) with the link to their
  request page. Choose what happens after sending: wait for the customer (the default), keep it open, or mark it
  solved. Attach files as https links.
* **Internal note** stays inside the team; the customer never sees it.
* **Saved replies** insert a prepared answer; `{customer}` becomes the customer's first name and `{case}` the
  case number.
* **Suggest a reply** drafts an answer from the conversation with your writing model; without one it starts
  from a polite template. Always read it before sending.
* **Merge into another case** moves the messages of a duplicate into the case you keep and closes the duplicate.

Statuses: *new* → *open* → *waiting on customer* → *solved* → *closed*. A customer's reply reopens a waiting or
solved case. A closed case cannot change; the customer starts a new request.

## Due times (SLA)

Each priority has a first reply target and a solution target (defaults: urgent 1 h and 8 h, high 4 h and 24 h,
normal 8 h and 2 days, low 24 h and 5 days). With business hours on, only your working days and hours count:
a high case that arrives on Friday at 16:00 with hours 09:00 to 17:00 is due for a first reply on Monday at
12:00. Cases past either target show as overdue.

## Customers' pages

* **Help centre**: search, read an answer, say whether it helped (one vote per visitor, counted in the articles
  table with the views), start a live chat while it is on, or contact you.
* **Live chat**: a name, email and first question start the chat; new messages appear every two seconds and the
  chat survives a page reload in that browser. Your team answers under **Live chat**; a waiting chat shows on
  the Service menu badge.
* **Request page**: the conversation (internal notes never appear), a reply box, and once the case is solved a
  1 to 5 rating with an optional comment (once per case).

## In the background

Every few minutes Service marks cases that passed their due time, prepares the rating request email
(`csat_request`) for solved cases, and closes solved cases with no customer reply after the number of days in
Settings (7 by default). Emails it prepares: `case_received`, `case_reply` and `csat_request`. Nothing is sent
until you approve it in the Outbox, unless you list these kinds for automatic sending under Business hub >
Email.

## Reports

Open and overdue cases, the average time to the first reply and to a solution, the satisfaction average and the
number of ratings, cases solved and created this week, and cases by channel. The Business hub shows the open,
overdue and satisfaction figures and a getting-started checklist (profile, a published answer, a published help
centre, a shared contact or chat link, the first solved case).

## In the hosted product

Service is an add-on. Owners, admins and coaches manage it; auditors can read it; candidates do not see it. The
cases, chats and help centre belong to the organization, and its public pages work for anyone with the link once
the organization subscribes.

## API

Everything is under `/api/service/`: `overview`, `settings`, `cases` (with `view`, `q`, `status`, `priority`,
`channel`, `tag` and `format=csv`), `cases/{id}` with `reply`, `merge`, `suggest` and `link`, `macros` (with
`use`), `categories`, `articles`, `help/link`, `chat/link` and `chats/{id}` (with `after` for new messages,
`reply`, `end` and `case`). Public pages: `help_center`, `chat` and `case`.
