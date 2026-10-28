# Automatic and assisted applications: build contract

Status: build contract. Backend engine, LinkedIn Easy Apply tool, Aira integration and tests are built against
this file; where code and this file disagree after the build, update this file.

## 1. The three ways to apply

| Mode | Who fills the form | Who presses Submit | When |
|---|---|---|---|
| **Manual** (exists today) | Hyma, on the employer site, with Aira's packet beside her | Hyma | Always available. Aira records the confirmation and evidence afterwards. |
| **Assisted** (new) | Aira, in its own browser, from the approved packet | Hyma, with one click in Aira after seeing the filled form | Any supported form, any job. |
| **Automatic** (new) | Aira | Aira | Only when the policy allows it (section 5) and every required question has a confirmed answer. Anything else is held for review with the exact questions. |

Hard rules in every mode: never invent an answer; never fill a sensitive field (work authorization, visa,
compensation, demographics, legal attestations) without a confirmed standard answer; one submission per job;
respect the daily limit; capture evidence (screenshot + page text + confirmation number when shown) and store it
encrypted; every run is audited; nothing is clicked except form controls and the form's own navigation/submit
buttons; never bypass CAPTCHAs, verification steps or sign-in walls (report `needs_review` with the reason).

## 2. Form engine (`aira/apply_engine.py`, new)

Runs a real browser (patchright, Chromium, sync API, in a worker thread; never inside an asyncio loop) against the
employer's application page.

```python
@dataclass(frozen=True, slots=True)
class ApplyRequest:
    job_id: str
    source: str                       # aira job source: greenhouse | lever | ashby | smartrecruiters | google_jobs | linkedin | ...
    application_url: str
    fields: list[dict[str, Any]]      # the approved packet fields exactly as stored (label, provider_field_id, canonical_field,
                                      #   field_type, required, options, value, value_source, status, sensitivity, requires_confirmation)
    profile: Mapping[str, str]        # candidate profile (public + personal fields), for form fields the packet did not list
    standard_answers: Mapping[str, Mapping[str, Any]]   # CONFIRMED standard answers only (canonical_field -> {value, sensitivity, confirmed})
    resume_path: str | None           # a real .pdf/.docx file on disk
    cover_letter_path: str | None
    cover_letter_text: str
    submit: bool                      # False: fill, report, do not submit (assisted / inspection). True: fill and submit.
    headless: bool = True
    screenshot_dir: str | None = None
    timeout_seconds: int = 120
    extra_answers: Mapping[str, str] = field(default_factory=dict)   # label -> value answered by the user for this run (review answers)
    workday_account: Any = None       # Workday only: aira.workday.WorkdayAccount (decrypted for this run), see section 2a

@dataclass(slots=True)
class FormQuestion:
    label: str
    kind: str              # text | textarea | select | radio | checkbox | file | date | number | email | tel | url | unknown
    required: bool
    options: list[str]
    provider_field_id: str
    canonical_field: str | None
    value: str | None
    status: str            # filled | missing | needs_confirmation | unknown | unsupported | skipped
    message: str

@dataclass(slots=True)
class ApplyEvidence:
    screenshot_path: str | None
    page_text: str
    confirmation_text: str
    confirmation_number: str
    url: str
    submitted_at: str | None

@dataclass(slots=True)
class ApplyOutcome:
    status: str            # submitted | filled | needs_review | failed | unsupported | already_applied
    ats: str               # greenhouse | lever | ashby | smartrecruiters | workday | generic | linkedin
    questions: list[FormQuestion]
    unanswered: list[FormQuestion]       # required questions still without a usable value
    evidence: ApplyEvidence
    error: str
    steps: list[str]                     # plain-language log: "Opened form", "Filled First name", "Uploaded resume.pdf", ...

def detect_ats(url: str) -> str
def run(request: ApplyRequest) -> ApplyOutcome
```

Behaviour:
* `detect_ats` by host/path: `boards.greenhouse.io`, `job-boards.greenhouse.io`, `boards.eu.greenhouse.io` ->
  greenhouse; `jobs.lever.co` -> lever; `jobs.ashbyhq.com` -> ashby; `jobs.smartrecruiters.com` -> smartrecruiters;
  `myworkdayjobs.com` / `myworkdaysite.com` (or source `workday` on another host) -> workday, run by
  `aira.workday.run_workday` (section 2a); otherwise generic.
* Each ATS adapter knows its field names/ids/labels and its custom-question markup; the generic adapter maps by
  `<label for>`, `aria-label`, `aria-labelledby`, `placeholder` and `name`. Values come from, in order: the packet
  field with the same label/provider id, `extra_answers` by label, `aira.field_mapping.map_field` on the live label
  + `prefill_fields` from profile/standard answers. Sensitive mappings are filled only from a confirmed standard
  answer. Unknown required questions stay empty and are reported.
* Resume/cover letter: `input[type=file]` matched by label/name (resume, cv, cover letter); upload with
  `set_input_files`; fallback to the text field when the form offers "paste" instead of upload.
* Already applied: page text or a disabled apply control that the ATS uses for "already applied" -> `already_applied`.
* `submit=False`: fill everything possible, take a full-page screenshot, return `filled` (or `needs_review` if
  required questions are unanswered). `submit=True`: refuse to submit when `unanswered` is non-empty
  (`needs_review`); otherwise click the form's submit, wait for the ATS's success marker (per-adapter list of
  phrases and URL patterns, plus a generic "thank you / application received / submitted" list), screenshot,
  extract a confirmation number when one is shown (regex over the page text), return `submitted`.
  No success marker within the timeout -> `failed` with the page text in evidence (never claim success).
* Never follow links off the form page; never open a new tab; headless by default.

## 2a. Workday (`aira/workday.py`, accounts in `aira/workday_accounts.py`)

Workday is account-based and multi-step: each employer's tenant (`acme.wd5.myworkdayjobs.com`) has its own
candidate accounts, and an application is a sequence of pages.

* Accounts: one per tenant host, password Fernet-encrypted at rest with the SerpApi key's secret store
  (`workday_accounts` table, `password_ciphertext`). Either the person's own email and password, or "create it
  for me": the profile email and a generated strong password, saved before the account form is filled. A
  preference (`workday_preferences.auto_create`) creates accounts on new tenants automatically. The last run's
  state is remembered: `new`, `active`, `verification_needed`, `sign_in_failed`, `exists`. Passwords are never
  returned by the API (emails are masked), logged, audited or left in steps and errors.
* `credentials_for_run(connect, store, url, source=, profile_email=)` decrypts the account into
  `ApplyRequest.workday_account` (`WorkdayAccount(host, email, password, create, on_result)`); without one,
  `run` returns `needs_review` before any browser starts.
* The run: job page -> `adventureButton` (Apply) -> `applyManually`; sign in (`signInLink`, `email`, `password`,
  `signInSubmitButton`) or create the account (`createAccountLink`, `verifyPassword`, `createAccountCheckbox`,
  `createAccountSubmitButton`; an existing account falls back to signing in). Then one page at a time, named by
  `progressBarActiveStep`, inside `applyFlowPage`: the form engine's discovery and answers for native controls,
  Workday's listbox dropdowns (`button[aria-haspopup=listbox]` -> `role=option`), the resume on
  `file-upload-input-ref` (values Workday's parsing filled in are kept), then `bottom-navigation-next-button`
  (or `pageFooterNextButton`); a Workday error banner after Save and Continue holds with its text.
* Voluntary Disclosures and Self Identify (and any page asking a demographic question): answered only from the
  person's answers for the run or confirmed saved standard answers ("I do not wish to answer" matches the page's own
  decline wording); never from the profile or a packet; every open question there holds, required or not.
* Holds (`needs_review`): unanswered required or sensitive questions, a verification email, a CAPTCHA, a rejected
  password, account terms without a confirmed legal attestation, a second sign-in prompt.
* `submit=False` stops at Review with a screenshot (`filled`; Workday has saved each page as a draft).
  `submit=True` presses Submit on Review and waits for the confirmation like the other adapters.
* API (session, owner-only, same-origin): `GET/POST /api/autopilot/workday-accounts`,
  `PATCH/DELETE /api/autopilot/workday-accounts/<host>`, `POST /api/autopilot/workday-settings`. UI: Settings >
  Integrations > Workday accounts (`views-workday.js`, via `Aira.settingsCards(tab)`).
* Limits: English step names and notices; hierarchical dropdowns and multi-select prompt inputs are not chosen
  automatically (they hold as questions); tests run against `tests/workday_stub.py`, never a real tenant.

## 3. Files for upload (`aira/resume_file.py` and `aira/pdf_text.py`, new)

* `resume_for_upload(connection, store, owner_id, job, profile_resume_text) -> tuple[Path, Callable[[], None]]`:
  newest document of kind `resume` (the master resume the user uploaded, `.pdf`/`.docx`) decrypted to a temp file
  with its original extension; otherwise a PDF built from the job's tailored `resume_draft` (fallback: the profile
  resume text). Cover letter: `cover_letter_for_upload(...)` builds a PDF from `job["cover_letter"]`.
* `pdf_text.text_to_pdf(text, title) -> bytes`: dependency-free, Letter size, Helvetica 11 pt, wrapped lines,
  multi-page, valid in Chromium and ATS parsers (tested by re-reading with `pypdf` if available, else by structure).

## 4. LinkedIn Easy Apply (`linkedin_mcp_server/tools/job.py` + `scraping/extractor.py`)

New MCP tool `apply_to_job`:

```
apply_to_job(job_id: str, answers: dict[str, str], resume_path: str | None, submit: bool,
             profile: dict[str, str]) -> {
  "status": "submitted" | "filled" | "needs_review" | "external" | "already_applied" | "failed",
  "questions": [{"label","kind","required","options","value","status","message"}],
  "unanswered": [...],
  "external_url": str | None,          # when the job uses an external site: Aira routes it to the form engine
  "evidence": {"page_text": str, "confirmation_text": str, "screenshot_base64": str | None, "url": str},
  "steps": [str], "error": str
}
```
* Opens `/jobs/view/{job_id}/`. Detection is locale-independent where possible (structural: the apply control
  inside the job header, the `role="dialog"` modal, `input`/`select`/`fieldset` with labels, the footer's primary
  button); where English text is the only signal (Next / Review / Submit application / Application sent / Applied)
  keep it in one per-locale table with a documented limitation, exactly as the repo's scraping rules require.
* `answers` is keyed by the question label as LinkedIn shows it; `profile` carries canonical fields (first_name,
  last_name, email, phone, city, linkedin_url, ...) for the contact step. Fills each step, uploads the resume on the
  resume step, unchecks "follow company" boxes, never submits when `submit=False` or when a required question has
  no answer (returns the questions instead). After submit, waits for the post-submit dialog, captures page text and
  a PNG screenshot (base64), closes the dialog.
* Stand-in LinkedIn for tests (`tests/linkedin_easy_apply_stub.py`): local pages mimicking the job view with an
  Easy Apply modal of three steps (contact, resume upload, questions incl. a required select and a sensitive
  question), an "Applied" state, an external-apply variant, and the "Application sent" dialog.

## 5. Aira integration (`auto_apply_assistant.py`, `aira/applications.py`, `aira/schema.py`, UI)

Storage (built as migration 16 `autopilot`, see section 6; `application_runs`):
```
run_id TEXT PK, job_id, prep_id, mode (manual|assisted|auto), trigger (user|schedule|resume), status
(queued|running|filled|needs_review|submitted|failed|unsupported|already_applied|cancelled), ats, questions_json,
unanswered_json, answers_json (user answers for this run), steps_json, evidence_document_id (page text),
screenshot_document_id (PNG), confirmation_text, confirmation_number, error, started_at, finished_at, actor,
correlation_id
```
`record_submission(method=...)` accepts `manual | assisted | auto`.

Settings (`app_*`): `auto_apply_enabled` (0/1, default 0), `auto_apply_paused` (0/1), `auto_apply_min_score`
(default 75), `auto_apply_classifications` (default `Strong Match,Recommended`), `auto_apply_sources`
(csv, default `greenhouse,lever,ashby,linkedin,google_jobs`), `apply_show_browser` (0/1), reuse
`daily_application_limit` (0 = unlimited). The old `auto_submit_enabled` key and `auto_submit_policy()` are
replaced by `auto_apply_policy(settings)` that reports `available: true` and the policy.

Schedule kind `applications` (min interval 30 min, max 5 jobs per run): picks jobs that are not applied/archived,
match the policy, have a loaded description and a complete profile; prepares a packet if none; if the packet has no
unknown/unconfirmed required questions it runs the engine with `submit=True`; otherwise it files a `needs_review`
run with the questions. Runs are sequential (one browser at a time), each job once, with the daily limit and a
pause switch; a run never starts for a job with a submission or a run in `queued|running`.

Routes (session auth, JSON):
```
GET  /api/applications/auto                     -> {policy, enabled, paused, available, sent_today, daily_limit, schedule, running, held: [...], recent: [...], last_check}
POST /api/applications/auto/run                 -> 202 start a policy pass now (same single-flight as schedules)
POST /api/jobs/{id}/auto-apply {mode: "assisted"|"auto"}  -> 202 {run}  (assisted = fill only; auto = fill and submit)
GET  /api/applications/runs?status=&job_id=&limit= -> {runs: [...]}
GET  /api/applications/runs/{run_id}            -> run + questions + evidence summary + screenshot url
POST /api/applications/runs/{run_id}/answer {answers: {label: value}, remember: bool} -> 202 resumes the run (assisted keeps submit=False; auto submits)
POST /api/applications/runs/{run_id}/submit     -> 202 for a `filled` assisted run: submit it now (the user's click)
POST /api/applications/runs/{run_id}/cancel     -> run cancelled (only queued/needs_review/filled)
GET  /api/applications/runs/{run_id}/screenshot -> the PNG (download route through the document store)
```
Also `POST /api/settings` accepts the new keys with validation; `GET /api/automation` lists the new schedule kind;
LinkedIn jobs go through the MCP tool, everything else through the form engine; `external` from LinkedIn is routed
to the form engine in the same run.

UI (`auto_apply_assistant/views-applications.js`, `views-jobs.js` drawer, `views-settings.js`, `app.css`):
* Applications page: an **Automatic applications** panel modelled on the Messages tab's Automatic replies panel:
  enable switch with a plain-language confirmation, policy summary (min score, classifications, sources, daily
  limit, sent today), schedule control (every 2 hours by default), Run now, Pause, show-browser toggle, held runs
  with **Review** (answer form: one input per unanswered question with its options; "Remember this answer";
  Resume), recent runs table (job, mode, status, when, confirmation, Open), run detail drawer (steps log, every
  question with its value and status, evidence screenshot preview, confirmation number, links to the job).
* Job drawer and queue: three buttons: **Apply manually** (existing hand-off), **Fill it for me** (assisted),
  **Apply automatically** (auto, with a confirmation dialog naming the job). Disabled with a reason when the
  packet is not approved, the daily limit is reached, the source is unsupported, or a run is active.
* Settings > Automation: the policy controls and the `applications` schedule.
* Dashboard: counts of automatic submissions today and held runs.

Tests: engine against the ATS stub (every adapter, every outcome), Easy Apply tool against the LinkedIn stub,
Aira routes and scheduler with the engine faked (and one real engine run against the stub through the server),
UI walkthrough in headless Chromium against a scratch server with both stubs.

## 6. As built: Auto-apply in Jobs (2026-10-23)

Section 5 was built around **search plans**, which the user manages in **Jobs > Auto-apply** (`#/jobs/autopilot`).
Where this section and section 5 differ, this section wins.

**Search plans** (`aira/autopilot.py`, migration 16 `autopilot`; tables `autopilot_plans`, `autopilot_passes`,
`application_runs`, `custom_answers`). A plan has:
- what to look for: name, up to 5 positions, location, workplace, employment type;
- where to look: sources (`linkedin`, `google_jobs`) and company boards (`greenhouse|lever|ashby|smartrecruiters` +
  identifier);
- exclusions: excluded words and companies;
- limits: `min_score` (default 70), `max_per_run` (1–10, default 3), `interval_minutes` (60 to 7 days, default 240);
- `mode`: `apply` submits automatically, `review` fills the form and waits for the user's click;
- switches: `match_title` (the job title must name one of the positions; half the key words, seniority words ignored),
  `cover_letters` (draft one with Ollama when it runs), `linkedin_easy_apply` (off by default; the editor shows that
  LinkedIn's User Agreement forbids automated applications), `enabled`.

**A pass** (`run_plan`, run by the scheduler tick every 15 s or by "Run now"):
1. Search every position on every place.
2. Drop jobs that already have a run or a submission.
3. Drop jobs on the title or exclusions first, before any description is read (at most 20 description loads per pass).
4. Load the remaining descriptions and score the jobs.
5. Keep jobs with stage `discovered|saved`, no hard blocker, not `Not Recommended`, and a score of at least `min_score`.
6. Apply to the best of them, at most `max_per_run`, stopping at the daily limit or the global pause.

Every job is tried once. Each pass is logged with counts, the reasons jobs were left out, and a summary; 50 passes are
kept per plan. Sources that cannot run (LinkedIn offline, no SerpApi key) are skipped with the reason.

**An application run** (`execute_run`) has statuses `queued|running|submitted|ready|needs_review|failed|unsupported|already_applied|skipped`.
- LinkedIn jobs marked Easy Apply go through the MCP tool `apply_to_job`, only when the plan allows it. When the tool
  returns `needs_review`, Aira answers the questions it can (exact field matches from the profile, and confirmed
  standard answers for sensitive ones) and retries once. `external` with a URL goes to the form engine.
- Every other job goes through the form engine (`aira/apply_engine.py`) with these inputs:
  - the profile, confirmed standard answers, and remembered custom answers as `extra_answers`;
  - the uploaded master resume file (else a PDF from the resume text);
  - the cover letter as a PDF;
  - a screenshot directory.
- Evidence is stored Fernet-encrypted in the document store: the screenshot (`screenshot`) and the confirmation page
  text (`application_confirmation`).
- A submission is recorded with `method = "auto"` and idempotency key `autopilot:{run_id}`. The job moves
  discovered/saved → needs_review → applying → applied, and gets a notification and a 7-day follow-up task.
- A held run moves the job to `needs_review` and sends a notification.
- Answering a held run (`remember: true`) saves each answer:
  - as a confirmed standard answer when its label maps to a canonical field;
  - otherwise in `custom_answers`, keyed by the normalised label.

  The run then resumes.

**Routes** (session auth):
```
GET    /api/autopilot                          -> plans, open runs, recent runs, passes, status {paused, daily_limit, sent_today,
                                                  remaining_today, show_browser, running {label, detail}}, readiness, answers, remembered
POST   /api/autopilot/settings                 {auto_apply_paused, apply_show_browser, daily_application_limit}
POST   /api/autopilot/plans                    -> 201 {plan}
PATCH  /api/autopilot/plans/{id}               -> {plan}
DELETE /api/autopilot/plans/{id}
POST   /api/autopilot/plans/{id}/run           -> 202 (background, the account's single automation slot)
GET    /api/autopilot/plans/{id}/passes
GET    /api/autopilot/runs?status=open|<status>&plan_id=&job_id=&limit=
GET    /api/autopilot/runs/{id}
POST   /api/autopilot/runs/{id}/answer         {answers: {label: value}, remember} -> 202
POST   /api/autopilot/runs/{id}/submit         -> 202 (a `ready` run only)
POST   /api/autopilot/runs/{id}/skip
DELETE /api/autopilot/answers/{label_key}
POST   /api/jobs/{id}/auto-apply               {mode: apply|review} -> 202 {run}
```
`GET /api/jobs/{id}` carries `auto_runs`. `auto_submit_policy()` now reports `available: true` and points to Auto-apply;
the legacy `auto_submit_enabled` switch is still refused.

**UI** (`views-autopilot.js`, `aap-` classes). The Jobs page has an **Auto-apply** tab with these sections:
- a status line with what the run is doing now (searching, writing a cover letter, applying), Pause/Resume, the daily
  limit and "Show the browser";
- a readiness checklist: profile, resume file, work-authorization answers, LinkedIn, SerpApi and Ollama, as each
  applies. "Your answers" sets the confirmed answers almost every form asks for;
- **Needs you**: held runs with one input per question and "Remember these answers"; review-mode runs with "Submit
  application";
- plan cards: Run now, an on/off switch, Edit, History and Delete;
- recent activity with a details drawer: steps, every question with its value, the screenshot and the confirmation.

The plan editor asks for confirmation before the first automatic plan. The job drawer's Apply tab adds "Apply for me"
and "Fill it, I'll submit".

**Fixed in the engine**: `context.on("page", popup.close)` also fired for the engine's own page and closed it, so every
run failed at its first navigation. The handler is now registered after the page exists and skips that page.

Tests: `tests/test_apply_engine.py` (every stand-in ATS and outcome, real Chromium), `tests/test_autopilot.py` (rules,
the pipeline with fakes, the LinkedIn route, and HTTP journeys with the real engine against `tests/ats_stub.py`).
