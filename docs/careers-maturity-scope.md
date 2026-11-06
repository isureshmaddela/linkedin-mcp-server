# Careers: maturity scope

Status: round 1 in progress (started 2026-11-06). This document records what the Careers app is missing, why it
matters, and what each round builds. Every item lists the evidence it came from (file:line at the time of the review)
and how it is accepted.

## Goals

1. Auto-apply finishes every application it can, and never submits one twice.
2. When a site needs a person (a CAPTCHA, a sign-in, an email check), Monosage hands over that one step and keeps the
   rest of the work.
3. Applying works with a browser (ATS forms, Workday, LinkedIn Easy Apply) and without one where the posting allows it
   (apply-by-email).
4. The AI acts like the account holder: it follows the organization's instructions, the person's own instructions,
   and task "skills", for drafts, application answers and recruiter replies, including automatic replies.
5. Everyday manual use is safe to recover from (undo, reopen, un-archive) and fast (bulk actions).

## Limits (by design)

- Monosage never solves or bypasses CAPTCHAs, bot checks or a site's sign-in or verification. Those steps go to the
  person (Needs you), with the rest of the form already done where the build can show a browser.
- Most applicant tracking systems accept applications only through their web form or with the employer's own API
  key. "Without a browser" therefore means postings that ask for an email application; everything else uses the
  browser engine.
- LinkedIn Easy Apply stays opt-in per plan with the User Agreement warning.
- Instructions and skills cannot override the fixed rules: job text is untrusted data, nothing is invented, sensitive
  topics (pay, visa, identifiers, offers, background checks, links, money) are held for the person in automatic
  replies.

## Findings

| # | Finding | Evidence | Round |
|---|---|---|---|
| F1 | A lost hosted worker can re-run a run whose Submit was already clicked: the queue redelivers up to 3 times and `execute_run` does not refuse a run that is already running | `monosage_cloud/jobqueue.py:31,58,368-387`; `aira/autopilot.py:919-1076` | 1 |
| F2 | A run left `running` by a crash is never reaped; Skip and Apply for me refuse it, so the job is locked | `auto_apply_assistant.py:7280,7307` | 1 |
| F3 | Hosted Auto-apply never writes cover letters: `_autopilot_documents` checks only the local Ollama | `auto_apply_assistant.py:6513-6518` | 1 |
| F4 | Scheduled reviews and drafts use the built-in `DEFAULT_MODEL`, not the chosen model | `auto_apply_assistant.py:4446,4450` | 1 |
| F5 | No standing instructions, persona or skills for any AI task; three copies of the system rule | `auto_apply_assistant.py:2193`; `aira/model_provider.py:55`; `monosage_cloud/models.py:41` | 1 |
| F6 | A member cannot use their own Claude in place of the organization's AI; the status pill always says "Organization AI" | `monosage_cloud/models.py:135`; `auto_apply_assistant/app.js:862` | 1 |
| F7 | "Apply manually" outcomes (unsupported site, refused visit, no form) never reach Needs you | `aira/autopilot.py:72,561-562`; `views-autopilot.js:16` | 1 |
| F8 | Nothing is retried: a timeout or HTTP 5xx fails the job for good | `aira/autopilot.py:14` | 1 |
| F9 | No application path without a browser (no apply-by-email; no `email` submission method) | `aira/applications.py:28`; `aira/ats.py:1-14` | 1 |
| F10 | Confirmation and "already applied" wording is English only; other languages end `failed` | `aira/apply_engine.py:25-31` | 1 |
| F11 | Google Jobs takes the first apply option whatever its host (often an aggregator) | `aira/job_sources.py:639-649` | 1 |
| F12 | The automatic path skips the approved packet and the Greenhouse question API (`fields=[]`) | `auto_apply_assistant.py:6578,6026` | 1 |
| F13 | Accepted, rejected and withdrawn have no way back; archived jobs cannot be restored from the UI | `auto_apply_assistant.py:296-298,5559` | 1 |
| F14 | Holds without questions (CAPTCHA, sign-in, Workday email check) close the browser; the person redoes the form elsewhere | `aira/apply_engine.py:873-879`; `views-autopilot.js:116-147` | 2 |
| F15 | The generic form engine handles one page only (no Next/Continue) and native controls only | `aira/apply_engine.py:1239-1245` | 2 |
| F16 | No bulk actions in Jobs, Applications or Needs you | `views-jobs.js:277`; `views-applications.js` | 2 |
| F17 | Reminders and follow-ups are in-app only (no email or browser notification); no installable app | `auto_apply_assistant.py:7825`; `app.js` | 2 |
| F18 | Research and interview prep have no AI synthesis | `aira/airaapi/careers_intel.py:233,1349`; `views-crm.js:62` | 2 |
| F19 | No mailbox sync for Email updates (paste or .eml only) | `views-settings.js:191` | 3 |

## Round 1 (this round)

### R1.1 One submission per application (F1, F2)
- A run moves `queued -> running` with a compare-and-set; a second worker, or a redelivered job, that finds it
  `running` or `submitted` does nothing.
- A run that has been `running` for longer than its time limit becomes `interrupted` and goes to Needs you with
  "Monosage could not confirm whether this was sent; check the site, then choose I applied or Try again". It is never
  submitted again on its own.
- Skip and Apply for me work on `interrupted` runs.
- Accepted: tests for double delivery, a crash after Submit, and the reaper.

### R1.2 Retries for passing failures (F8)
- Failures are classified: transient (timeout, HTTP 5xx or 429, browser failed to start, connection reset) and
  permanent (no form, refused visit, unsupported site, answers needed).
- Transient failures are retried twice (after about 15 minutes, then 2 hours), only when no Submit was clicked.
- Accepted: tests for each class and for the "Submit clicked" guard.

### R1.3 Needs you shows every job that needs the person (F7)
- Unsupported, refused and no-form outcomes appear in Needs you with Open the application, I applied (records a
  manual submission with the date) and Skip.

### R1.4 Apply without a browser: apply-by-email (F9)
- Postings that ask for an email application ("send your resume to …", `mailto:` apply links) are detected.
- A plan option "Apply by email when the posting asks for it" (off by default) sends one email with the resume and
  cover letter attached, through the mail the account already has (local SMTP settings; hosted, the organization's
  mail with its limits, opt-out lists and metering).
- The submission is recorded with method `email` and the Message-ID as evidence, under the same daily limit and
  duplicate rules.

### R1.5 Better coverage of what the browser engine already does (F10, F11, F12)
- A documented per-language table of confirmation and "already applied" wording (English, Spanish, French, German,
  Portuguese, Italian, Dutch).
- Google Jobs prefers a direct employer or ATS apply option over aggregators.
- The automatic path uses the approved packet answers and the Greenhouse question API like the manual path.

### R1.6 The AI acts like the account holder (F3, F4, F5)
- One system rule for every model and build.
- **Instructions**: the organization's instructions (owners and admins, hosted) and the person's own instructions
  ("About me and how I answer"), added to every Careers AI task after the fixed rules.
- **Skills**: named instruction sets for one or more tasks (fit review, resume and cover letter, application answers,
  recruiter replies, negotiation), switched on or off, from the organization or the person. Starter skills ship as
  templates.
- Messages "Draft with AI" takes an optional note for that one reply; automatic replies use the same instructions
  and skills, and the existing guardrails still hold sensitive topics for the person.
- Fixes: hosted Auto-apply writes cover letters with the organization's AI; scheduled jobs use the chosen model.
- Instructions and skills stay in Careers (Leads and the suite apps do not receive them).

### R1.7 My Claude or Organization AI (F6)
- Each member may use their own Anthropic key and Claude model for their own job applications, or the
  organization's AI, and switch at any time (Settings > Integrations). The key is stored encrypted, shown only as its
  last four characters, and deleted when the member leaves.
- Owners and admins can turn this off for the organization (policy, on by default).
- The status pill names the AI in use: "My Claude", "Claude" or "Organization AI".

### R1.8 Recover from mistakes (F13)
- Reopen an accepted, rejected or withdrawn application (recorded in the history); restore an archived job.

## Later rounds

- Round 2: Finish in browser (F14), multi-step generic forms (F15), bulk actions (F16), email and browser
  notifications and an installable app (F17), AI research and interview prep (F18).
- Round 3: mailbox sync (F19), then the same review for every other app.
