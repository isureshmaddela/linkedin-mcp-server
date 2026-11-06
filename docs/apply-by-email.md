# Applying by email

Most application systems accept applications only through their web form, which Monosage fills in a browser
(`aira/apply_engine.py`, Workday, LinkedIn Easy Apply). Some postings instead ask for an email: a `mailto:` apply
link, or wording such as "send your resume to jobs@example.com". For those, Monosage applies without a browser:
it emails the application through the mail the account already has.

Code: `aira/email_apply.py` (finding the address, the message, the ledger), the glue in `auto_apply_assistant.py`
(`_email_apply`, `_email_apply_preview`, `_email_apply_send`, `_email_target`), migration 22 `email_apply`.

## Finding the address

- A `mailto:` application link (or job link) is always the apply address; its `subject=` is used as the subject.
- In a description, an address counts only when apply wording comes right before it in the same sentence
  ("send your CV to", "apply by email", "applications should be sent to", "interested candidates should send"),
  from the explicit per-language table `APPLY_CUES` (English, Spanish, French, German, Portuguese, Italian, Dutch).
- An address is never an apply address when its sentence is about something else (accommodations, accessibility,
  privacy, questions, agencies, "do not send"), when it is a no-reply, privacy or accommodations mailbox, or a
  placeholder (`example.com`). An address in a footer or a "questions? write to" line has no apply wording and is
  ignored.
- A job reference in the posting ("Reference: ENG-2041") goes in the subject.

Limitation: apply wording is the only signal, so a posting in a language outside the table is not detected; it
is applied to another way.

## When Monosage emails

- **Auto-apply**: only when the plan's option "Apply by email when the posting asks for it" is on (off by default),
  the posting asks for an email, and it has no application form Monosage fills (Greenhouse, Lever, Ashby,
  SmartRecruiters, Workday). A `mailto:` application link always counts. LinkedIn Easy Apply jobs stay on Easy Apply.
  "Ask me first" plans prepare the email and wait for the one-click OK in Needs you.
- **The job drawer** (Apply tab > Apply by email): shows the address and the sentence it came from, then a preview
  (To, From, Replies go to, subject, message, attached files). The person may edit the subject and message; nothing
  is sent without the preview and a confirmation. "Apply for me" alone never emails.

## The message

- To the apply address; From the account's sender address with the person's name; Reply-To the person's own
  address. Hosted, the organization's mail with "Member name via Organization" and Reply-To the member's sign-in
  address.
- A short, factual text: the role and reference, what is attached, and the person's name, email, phone and
  LinkedIn. Nothing is invented.
- Attachments: the resume (the approved tailored resume for the job first, then the uploaded resume file, then
  a PDF of the resume text) and the cover letter as a PDF when one exists. Each file at most 8 MB, together at most
  15 MB; a larger file holds the job with "Upload a smaller file in Documents".

## One email per job, ever

- The ledger `email_applications` (one row per job) is claimed before anything is sent.
- `sent`: never sent again. `sending` (a send that started and was never confirmed, for example the process
  stopped): never sent again on its own; the person is asked to check their Sent folder and record it.
  `failed` (the mail server refused it): may be tried again.
- A job that already has a recorded application is never emailed. Email applications count toward the daily
  application limit like every application Monosage sends.

## What is recorded

- The submission with method `email`, the confirmation text "Emailed to ADDRESS on DATE. Message-ID ID." and, as
  evidence (encrypted in Documents), the headers, the text and the names of the attached files (not the files).
- The job moves to Applied, with the usual notification and a follow-up task a week later.

## No mail set up

The run goes to Needs you with "Set up email to apply by email: add your mail server in Business hub > Email."
(hosted: "your organization has not set up email yet. An owner or admin sets it up in Business hub > Email.").
Nothing fails and nothing is sent.

## Hosted (Monosage Cloud)

- `HOOKS.application_mail` -> `monosage_cloud/outreach_mail.application_mail`: the organization's Business hub >
  Email and password, the member's name and address, the organization's do-not-contact list, the operator's
  outbound switch and the organization's state (`unavailable`). Applying is part of Careers on every plan, so no
  outbound-email feature is required.
- Routes: `GET /api/jobs/{id}/apply-email` (preview, `read()`), `POST /api/jobs/{id}/apply-email`
  (`write("applications.submit", kind="email_apply", audit=True)`). The `email_apply` pre-hook applies the
  organization's state gates and requires the candidate's consent when someone else sends for them. Auto-apply's
  email route runs inside the same gate as every automatic application (decision, sources, usage).

## Routes

| Method | Path | Body | Answer |
|---|---|---|---|
| GET | `/api/jobs/{id}/apply-email` | | `available`, `target` (address, how, evidence, subject, reference), `subject`, `body`, `attachments`, `mail` (ready, problem, from, reply_to), `record`, `problem` |
| POST | `/api/jobs/{id}/apply-email` | `confirm: true`, optional `subject`, `body` | `submission`, `recipient`, `message_id`, `job` |
