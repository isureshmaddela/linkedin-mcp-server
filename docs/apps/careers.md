# Careers

Find work, apply with a person in the loop, track every application to an offer, and get paid what the job is
worth. Open it from the app switcher (Core > Careers) or with `g` then `c`. The app's id is `careers`; its sections
are Dashboard, Jobs, Applications, Interviews, Recruiters, Documents, Analytics and Research (`#/careers`).

Careers is part of every plan in the hosted build: each candidate works in their own workspace, coaches and admins
open it only as their role allows, and one organization never sees another's jobs. Owners and admins can start their
own workspace to use Careers themselves. In the single-user build it is your own data on this computer.

**Your job search is yours.** When the account is a household (a child or a partner has their own profile for the
everyday apps), only the account owner's profile opens Careers: applications, offers and pay, recruiter messages and
the resume. Anyone else who opens it sees whose job search it is and a **Switch person** button. If the owner's
profile has a PIN, Careers asks for it like every other app.

## In the Aira frame

Careers lives at `/careers` with its own logo, like the other apps (`auto_apply_assistant/shell.js`). Its plug-ins are
in `views-jobs.js`.

* **+ New** offers Add a job, Schedule an interview, Add a recruiter, Upload a document and Create a job alert. Each
  opens the page's own form (`#/jobs/all?new=job`, `#/interviews?new=interview`, `#/recruiters?tab=contacts&new=recruiter`,
  `#/documents?new=document`, `#/careers?tab=alerts&new=alert`); the page drops `new=` once the form is open, so Back
  and a reload do not open it again. Upload a document opens the file chooser on the upload card. Another person's
  profile in a household gets no **+ New**; in the hosted build the creates need `candidates.write` and the job alert
  `search.run`.
* **Search across the top** ("Search jobs, people, interviews") suggests up to six records as you type: jobs (title,
  company or place, the server's own search), recruiters (name, company, title or email) and interviews (company,
  role, job or recruiter). Choosing one opens it: the job's panel, the recruiter's or the interview's. Enter opens the
  jobs list filtered by the words (`#/jobs/all?q=...`, headed "Jobs matching ..."); on Recruiters it filters the
  contacts instead.
* **Menu folders** with their own icons: Jobs (All Jobs, LinkedIn, Aira Jobs, Google Jobs, Company Portals,
  Recommended, Saved, Auto-apply), Applications (Pipeline, Queue & review, Offers, Email updates), Interviews
  (Upcoming, Past, All), Recruiters (Contacts, Messages), Documents (All files, Resumes, Tailored resumes, Cover
  letters, Confirmations, Offers) and Research (its eight workspaces, Alerts with the count of new jobs). Each opens
  `#/<page>?tab=<id>`. Only the open page's folders show, and on wider screens they stand in for the tab strip under
  the heading, which stays on phones and tablets.
* On a phone the Jobs search form folds into one line above the list, and the Jobs and Analytics filters sit two to a
  row.

## Dashboard

* A checklist (master resume, profile, LinkedIn, the local writing model) and the numbers that matter: recommended
  jobs, items waiting for your review, applications ready to send (and how many are left in today's limit), applied,
  interviewing and offers. Each number opens the list behind it.
* **Today**: upcoming interviews and open tasks (follow-ups, reminders), each with **Done**.
* Recent jobs, updates (job alerts, new messages, monitors) and the activity of searches and scheduled runs.

## Jobs

* **Find roles** across LinkedIn (your own session), **Aira Jobs** (free job boards, no key), **Google Jobs**
  (a SerpApi key, connected once in API > Account) and company portals (Greenhouse, Lever, Ashby and
  SmartRecruiters). Every result goes into one list without duplicates.
* **Import job** pastes a posting's address.
* **+ Add a job** is for a job that has no posting Aira can read: one a friend told you about, a sign in a shop
  window, a phone call. Only the title and the employer are needed; add a link, the pay range and a few lines about
  the job and Aira scores it like any other. A job added by hand shows **Added by you**; without a link it has no
  **Open posting** button.
* Tabs by source (All, LinkedIn, Aira Jobs, Google Jobs, Company Portals), **Recommended** (best matches) and
  **Saved** (starred). Filter by words, status, match, score and workplace; sort by date, match, company or title.
  A link from Analytics can narrow the list to one source: a **Source** chip shows it and **Show every source**
  clears it.
* A job added by hand can be corrected later: **Edit job** on its Overview changes the title, employer, link, pay,
  workplace or description, and keeps its history, documents and offer. Without a description the panel offers
  **Add a description** (Aira scores the job from it). Adding a job by hand with a link Aira already tracks opens
  that job and says so; nothing is added twice.
* A job opens in a panel: **Overview** (status, description, skills, notes and a follow-up reminder, the offer once
  there is one, interviews and recruiters), **Fit & evidence** (an explainable score from facts in your resume only;
  the local model's review), **Documents** (a tailored resume and cover letter you review and approve), **Apply**
  and **History** (every status change with who, when and why).
* Clearing a follow-up reminder also removes its task from the dashboard.
* **Auto-apply** (Jobs > Auto-apply): search plans that find, score and apply to the best matches on a schedule,
  within your limits; questions Aira cannot answer come back to you.

## Applying

The Apply tab walks through four steps: complete your profile, approve the tailored documents, prepare and review
the answers (Aira never guesses work authorization, visa, salary, demographic or legal answers), then open the
application, submit it yourself and **Record submission** with the confirmation number and, if you like, a
screenshot (stored encrypted in Documents). A recorded submission moves the job to Applied and adds a follow-up task
a week later. Recording it twice is harmless. The time can never be in the future (a year typed wrong is refused).

**Already applied another way?** Sent your CV by email, handed it in at the shop, applied on a site Aira does not
search, or before you started using Aira: record **how** you applied (the employer's website, a job board, email, in
person, phone, a referral, an agency), the day and a note. The job moves straight to Applied **on that day**: it is
counted in Analytics in the period it was sent, its follow-up is due a week after that day (or now, if that week has
passed) and it appears in the job search log. Applications made this way do not use up the daily limit, which paces
applications sent through Aira.

## Applications

* **Pipeline**: every application by stage (Preparing, Applied, Interviewing, Offers, Closed), however many jobs
  are still only discovered. Each column counts all of its applications; the most recently updated 200 are shown
  first and **Show more applications** loads the rest.
* **Queue & review**: today's budget, packets ready to send, items waiting for review and applications in progress.
* **Offers**: see below.
* **Email updates**: paste a recruiter's email (or upload an `.eml`); Aira recognises interview invitations,
  assessments, confirmations, rejections and offers, and suggests the action (create the interview, move the
  status) for you to confirm. Pick the job it is about by its title: typing searches every job you track, not only
  the latest ones (the same picker links interviews, recruiter notes and documents to a job).
* **Download job search log**: every application as a spreadsheet (CSV): the date, employer, role, location,
  status, how you applied, the confirmation, the link, the people you spoke to, interviews and any offer. Many
  unemployment offices ask for exactly this record of work search. Cells are written so a spreadsheet never runs
  them as formulas.

## Offers: get paid what the job is worth

* **Record the offer** (on a job's Overview, or in Applications > Offers): base pay a year, a month or an hour
  (with hours a week), yearly bonus, stock or equity a year, a one-time sign-on payment and your own estimate of
  the benefits, the start date, the date to answer by, a status (thinking about it, negotiating, accepted, declined,
  expired) and notes. Recording an offer moves the application to **Offer** (or **Accepted**). A **declined** or
  **expired** offer still counts as an offer received, and closes the application (Withdrawn; the job search log says
  "Offer declined" or "Offer expired"). If the employer comes back, change the status and it is an open offer again.
* **Compare offers**: every offer turned into the same numbers: **pay a year** (base + bonus + equity + benefits)
  and **first-year pay** (plus the sign-on payment). The best open offer is marked; each other offer shows how far
  below it is; a declined or expired offer shows **No longer open** and has no **Ask for more**. With the pay you want in your profile (Settings > Profile), each offer shows whether it meets it.
  Offers in another currency are never added to or compared with each other. On a phone the table becomes cards.
* **Ask for more** writes a counter-offer email from facts only: the offer, the pay you want, and (only if you tick
  it) that you have a higher offer. Aira suggests an amount and says why (about 8% more is a common, polite start;
  your profile's target; a higher offer you hold); you can change it. Your local writing model may polish the
  wording, but its version is used only if it keeps the amount you ask for. Aira never sends it: copy it and send it
  yourself.

## Interviews

Upcoming, past and all interviews with their time zone, people, preparation, notes, feedback and action items.
Add one by hand (link it to a job by picking its title) or from an invitation email. Each interview has calendar
links (Google, Outlook, Office 365) and an `.ics` file; **Download all** gives every upcoming interview at once.
Cancelling keeps the calendar entry in step (same UID, next sequence).

## Recruiters

A small contact book: people, their company and how you met, every call, message, email or note (link one to a job
by picking it), follow-up reminders and the jobs and interviews each person is connected to. The Messages tab is
your LinkedIn inbox with drafted replies you approve (or automatic replies within the limits you set).

## Documents

Master resumes, tailored versions, cover letters, confirmations, screenshots, offers and certificates. Every file is
encrypted at rest, versioned and kept for the retention period you choose. A document linked to a job shows the
job's title.

## Analytics

Honest numbers: rates show n/a when there is nothing to divide by. Jobs discovered and saved, applications prepared
and submitted, response, interview and offer rates, automation success, recruiter activity and **interviews booked**
in the period (with how many are coming up). The activity chart has one point per day of the period. Source
conversion and top companies link to the jobs behind them; resume versions and cover letters show what works.

## Research

The research workspace (`#/careers`): Google Jobs search with Google's own filters, saved searches and alerts,
company research, pay checks and interview preparation reports.

## For developers

* Routes are the single-user build's (`auto_apply_assistant.py`); the hosted build runs the same handlers in the
  candidate's workspace under the permissions in `aira_cloud/policy.py`.
* New in this release, all on existing routes:
  * `POST /api/jobs/import` with `{"manual": {title, company, location?, url?, description?, workplace_type?,
    employment_type?, salary_min?, salary_max?, currency?}}` adds a job by hand (source `manual`).
  * `POST /api/jobs/{id}/submit` with `{"outside": true, "channel": "website|job_board|email|in_person|phone|referral|agency|other",
    "submitted_at"?, "confirmation_text"?, "confirmation_number"?}` records an application sent another way.
    Any submission now moves a job that has not been applied to straight to Applied (before, a submission from Ready
    to apply was stored but the status change failed).
  * `PATCH /api/jobs/{id}/notes` with `{"offer": {...}}` records or changes the offer, `{"offer": null}` removes it;
    `GET /api/jobs/{id}` returns `offer` (with `annual_base`, `yearly_total`, `first_year_total`) and `has_posting`.
  * `GET /api/jobs?view=offers` lists jobs with offers plus `comparison` (offers best first, `best_job_id`,
    `vs_best`, `vs_expectation`, `meets_expectation`); `view=pipeline` lists every job past Discovered and Saved.
  * `POST /api/jobs/{id}/generate` with `{"kind": "negotiation", "ask"?, "mention_other_offer"?}` returns a
    counter-offer `draft` (`subject`, `body`, `ask`, `suggested_ask`, `reasons`, `source`).
  * `GET /api/jobs?format=csv&scope=applications|all` downloads the job search log.
  * `PATCH /api/jobs/{id}/notes` with `{"job": {title?, company?, location?, url?, description?, workplace_type?,
    employment_type?, salary_min?, salary_max?, currency?}}` corrects a job added by hand (400 for any other job, for
    a link another job has, or for a value that is not text). `POST /api/jobs/import` with `manual` and a link Aira
    already tracks answers 200 with `existing: true` and that job.
  * Submissions record `method`: `manual`, `assisted`, `auto` or `outside`. The daily limit counts today's
    (UTC) submissions except `outside`. `submitted_at` more than 14 hours ahead is refused; up to that, it is taken as
    now. A past `submitted_at` dates the Applied event and `applied_at`.
* Household profiles (single-user build): every signed-in Careers route (`/api/jobs`, `/api/applications`,
  `/api/interviews`, `/api/recruiters`, `/api/documents`, `/api/analytics`, `/api/dashboard`, `/api/autopilot`,
  `/api/email`, `/api/inbox`, `/api/messages`, `/api/tasks`, `/api/profile`, `/api/careers`, ...) answers `403`
  with `code: "careers_owner_only"` for a profile that is not the owner, and `profile_required` / `profile_locked`
  when the owner has a PIN and the request does not unlock it (`GET /api/profile` without a profile is answered,
  because the shell loads it before it restores the profile). The hosted build is unaffected.
* The offer table (`job_offers`, included in the account export) and the tracker logic live in
  `aira/airaapi/careers_tracker.py`.
* Tests: `tests/test_careers_tracker.py` and `tests/test_careers_regressions.py` (with `test_careers.py`, `test_auto_apply_assistant.py` and the other
  Careers suites).
