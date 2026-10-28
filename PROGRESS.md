# Career Copilot Progress

Last updated: 2026-10-23.

## Completed

### Platform
- `aira/` domain package (16 modules) with the HTTP server in `auto_apply_assistant.py` reduced to routing and thin services.
- Route table with `@route` decorator: correlation IDs (`X-Request-ID`), per-client rate limit, same-origin CSRF check, consistent error envelopes (400/401/403/404/429/502/500), request metrics, structured logging with redaction.
- Versioned additive migrations (`aira/schema.py`, 8 versions, ledger table). Verified on a copy of the real local database: 11 jobs preserved, second run is a no-op.
- SQLite connections now close on `with` exit (previously leaked handles and blocked file deletion on Windows); WAL and busy timeout enabled.
- Security headers (CSP without inline script, frame/sniff/referrer/permissions policies), login lockout, health/readiness/metrics endpoints.

### Product slices
- **Jobs**: unified search across LinkedIn, Google Jobs (SerpApi), Greenhouse, Lever, Ashby, SmartRecruiters; import by posting URL; source catalog with planned providers and reasons; views All / LinkedIn / Google Jobs / Company Portals / Recommended / Saved; filters, sort, pagination, persisted filter state; save, notes, follow-ups, archive.
- **Matching**: `explainable-match-v2` with ten evidence-only dimensions, weights, versioning, persisted explanation, inputs fingerprint for recalculation, five classifications including Not Recommended with a reason, sponsorship hard blockers only on explicit wording.
- **Profile**: universal candidate profile with public/personal/sensitive classification; standard answers with explicit confirmation; 42-field ATS label mapping with confidence.
- **Applications**: packet preparation from Greenhouse questions or standard questions, prefill, sensitive gating, review, approval checks (documents, resume version, required answers), daily limit, apply hand-off, confirmation evidence (encrypted), duplicate-submission prevention, pipeline board, review queue.
- **Email intelligence**: paste or `.eml` import, nine categories, explicit extraction (date/time/time zone, meeting link, deadline, IDs, contacts), dedupe, user correction, confirmation gates, optional auto-apply above threshold for non-sensitive actions.
- **Interviews**: CRUD, participants, prep and follow-up tasks, duplicate guard, ICS export with stable UID/SEQUENCE/CANCEL, Google/Outlook/Office 365 links.
- **Recruiters**: CRM with interactions, follow-ups, related jobs and interviews, conservative dedupe; LinkedIn inbox and reply drafts kept.
- **Documents**: Fernet-encrypted versioned store, content sniffing, size/type limits, scan hook, audit, retention, owner isolation.
- **Analytics**: metric definitions, period/source/status filters, null-safe rates, pipeline, source conversion, companies, resume-version and cover-letter performance, timeline with table view, drill-down links.
- **Settings**: profile, resume, standard answers, integrations and sources, automation policy and schedules, data and privacy (export, retention, account deletion, audit log, health).
- **UI**: rebuilt as a responsive SPA (sidebar + mobile bottom nav), light/dark themes, keyboard shortcuts (`g` + letter, `/`, `?`, arrow keys in lists), focus-trapped drawers and modals, loading skeletons, empty/error/retry states, toasts, confirmation for destructive actions.

### LinkedIn replies (2026-10-22)
- New MCP tool `reply_to_conversation` replies inside a thread (works for recruiter InMail) and confirms delivery only when the reply box is empty and the text is in the thread.
- Messages tab: Draft with AI, Send now with confirmation, per-conversation Manual send / Auto-reply switch, Save as recruiter, sent/held history, and an Automatic replies panel (daily count, inbox schedule, Check inbox now, pause, held replies with Review).
- Auto-reply guardrails in `aira/messaging.py`: holds anything about pay, visa or work authorization, personal identifiers, offers, background checks, relocation, links, contact details, money, or placeholders; 6-hour cooldown per conversation; daily limit (default 20); pause switch; one send per conversation state; Aira works out who wrote last from sender labels and holds when it cannot tell.
- The inbox check no longer trusts the local model alone: it skips when you wrote last and forces a draft when the recruiter wrote last with a question.
- LinkedIn session recovery: a stopped session restarts from the saved sign-in (once per minute), Connect resumes a saved sign-in without a login window, Settings has Restart LinkedIn session and Sign in again, and a booting session is no longer mistaken for a stopped one.
- Validation: 1041 tests pass; browser walkthrough with a stand-in LinkedIn server and the real local model passed 14 of 14 checks (manual send delivered once, sensitive auto-reply held, nothing extra sent).

### Messages tab as the messenger (2026-10-22)
- Aira keeps its own copy of the inbox (`aira/inbox_store.py`, migration 11: `inbox_threads`, `inbox_messages`, `inbox_sync_runs`). `GET /api/inbox` and `GET /api/conversations/{id}` answer from it and never wait on LinkedIn; `?refresh=1` or a never-saved thread reads LinkedIn live. Nothing is a 404 when LinkedIn is not connected.
- Background sync (`POST /api/inbox/sync`, `GET /api/inbox/sync`): inbox list (20, as before), then the 10 newest threads one at a time, single flight, skips unchanged threads, stops after 3 failures in a row. The Messages tab starts it when the copy is older than 10 minutes; inbox checks save what they read, and replies sent from Aira appear at once and are reconciled on the next fetch.
- UI: two-pane messenger (search, All / Needs reply / Recruiters / Auto-reply chips, avatars, previews, relative times, unread dots, grouped bubbles with day separators, sticky composer, mobile list/thread screens, dark mode). No link to linkedin.com remains in the Messages tab or the Recruiters drawer. Automatic replies panel is collapsible.
- Review fixes (2026-10-22): the Messages tab's automatic sync reads only the inbox list and opens no thread (a sync the user asks for reads the newest 10, which LinkedIn marks read); syncs share the automation slot with inbox checks and are throttled; an unreadable list row keeps its earlier preview and reply flag and is never treated as unchanged; a thread the latest list confirmed is not scraped again when opened; a failed sync no longer reads as "just synced"; a reply sent twice with the same words and a fetch that began before a send no longer undo the new reply; saved copy can be cleared (`DELETE /api/inbox/cache`) and idle threads are pruned after 90 days; sends are refused if newer messages were saved meanwhile. UI: focus stays on the row and moves into the thread, roving tabindex, browser Back closes a thread, the composer is never under the mobile bottom navigation, taller messenger, one-line filter chips, no literal "null" in threads or the Recruiters drawer, the show-browser toggle lives in Settings > Integrations.
- Validation: 114 messenger tests (64 store, 50 integration), 1370 pass overall; stand-in LinkedIn walkthrough in a headless browser passed 43 of 43 checks (cache-first open, sync progress, send once, offline copy, filters, keyboard, mobile, dark).

### API and Careers (2026-10-22)
- **API** (`#/airaapi`, `g` then `p`): a console for all 136 SerpApi engines (126 official plus 10 sub-endpoints): Overview, Playground, Engines, History with diff and export, Locations, Monitors and saved searches, Account, API keys, Docs (with a coverage page of every SerpApi dashboard page), Status. Backend in `aira/airaapi/` (catalog, client, store, service, compat, routes; migration 12). A SerpApi-compatible, cached endpoint at `/v1/search.json` and friends is guarded by API keys (`aira_` + 40 characters, hash only).
- **Careers** (`#/careers`, `g` then `c`): Google Jobs search with Google's filters and `next_page_token` paging, duplicate-safe import into Jobs, six reports (company, salary, market, interview prep, travel, learn) with a cost plan before running, job alerts with a baseline run (migration 13). The Jobs-tab Google source shares history and cache with Careers.
- **Key handling**: one SerpApi key, from `SERPAPI_API_KEY` or pasted in API > Account (validated first, Fernet-encrypted, never returned, logged or stored in records). The top bar, Settings > Integrations, Jobs, the Dashboard widgets, `/api/sources` and `/api/status` all follow it.
- **Integration (this entry)**: one `NAV` list drives sidebar groups, the phone bottom bar with More, chords and shortcuts help; Dashboard widgets (`views-widgets.js`); Settings card for the key; Jobs refreshes sources on every visit and the Google Jobs tab explains a missing key; dashboard notifications with in-app links now open them; CSP allows `https:` images; dark-theme contrast tokens (`--on-brand`, `--mark-ink`) fix white-on-mint text app-wide.
- Not built, on purpose (listed on the Docs coverage page): outgoing webhooks and SerpApi team seats. XLSX export, `json_with_pixel_position` (X-Ray), per-monitor budget caps and search inside stored results were added on 2026-10-23 (next entry).
- Validation: see the table below. Browser walk of every page (desktop light, mobile dark) against a scratch server and the stand-in SerpApi (`tests/serpapi_stub.py`): no console errors, no horizontal overflow, 21 of 21 interaction checks.

### SerpApi dashboard parity (2026-10-23)
- Closed the remaining buildable gaps between the SerpApi dashboard (https://serpapi.com/dashboard) and the API console. Details in `docs/airaapi-spec.md` section 13; migration 15 `airaapi_parity`.
- **Search inside results**: FTS5 index of the readable text of every stored result (LIKE fallback without FTS5). History has an "Inside results" switch and shows highlighted match excerpts (`in_results=1`). The newest 5,000 older results are backfilled.
- **Export**: per-search Excel (`.xlsx`, standard-library writer in `aira/airaapi/xlsx.py`) and Markdown table, beside JSON, CSV, HTML and cURL.
- **Monitors**: monthly budget per monitor. Scheduled runs wait for the next month once it is used up, Run now is refused, and raising the budget resumes the monitor. Optional pause on error, with a notification. Cards show a budget meter.
- **X-Ray**: History detail > X-Ray, for Google Search and Google Ads. It reads `json_with_pixel_position` from SerpApi's Search Archive (no search used), keeps it with the search, and draws a page map with one colour per section. It can be shown over the stored HTML page, beside an element list with filter and keyboard navigation. The detail shows the JSON path, size, above-the-fold flag and `json_restrictor` path.
- The Docs coverage page was updated to match (X-Ray, export, scheduler and history search are now built). A stale "expired id answers 404" line was corrected to 410.
- Fixed: a literal "null" showed above the filter bar on every Jobs tab except Google Jobs (`views-jobs.js`, `replaceChildren` with a null child).
- Still out of scope: outgoing webhooks (data-leak risk); team seats, billing, profile and 2FA (SerpApi account actions); live `json_with_pixel_position` from the Playground or `/v1`.
- Validation:
  - Full suite: 1584 passed, 5 skipped, 2 failed. The 2 failures are the Linux-only container tests that earlier rows deselect.
  - The 31 new tests in `tests/test_airaapi_parity.py` pass, and the API and Careers suites pass (318).
  - ruff, ruff format, ty and the JS tests (32 and 9) pass.
  - Browser walk against a scratch server and the stand-in SerpApi: 30 of 30 checks, desktop light and phone dark, with no console errors and no overflow.
  - The scratch database at schema version 14 migrated to 15 on start.

### Auto-apply in Jobs (2026-10-23)
- **Jobs > Auto-apply** (`#/jobs/autopilot`). Search plans name the positions, where to search (LinkedIn, Google Jobs,
  company boards), a minimum match, applications per run, a schedule and a mode: apply automatically, or fill and ask
  first. On schedule or Run now, Aira searches, scores and applies to the best matches within the daily limit; each job
  is tried once.
- Contract and as-built details: `docs/auto-apply-spec.md` section 6. Code: `aira/autopilot.py`, migration 16, glue and
  routes in `auto_apply_assistant.py`, `views-autopilot.js`.
- Held jobs show their questions under *Needs you*. You answer once ("Remember" saves confirmed standard answers or
  custom answers) and the run resumes. Review-mode runs wait for one click. Every submission stores a screenshot and the
  confirmation page encrypted, is recorded as method `auto`, and moves the job to Applied with a follow-up task.
- The readiness checklist covers profile, resume file, work-authorization answers, LinkedIn, SerpApi and Ollama.
  "Your answers" confirms the answers almost every form asks for. The job drawer gets "Apply for me" and "Fill it,
  I'll submit". The status line says live what the run is doing.
- LinkedIn Easy Apply is opt-in per plan, with a warning: LinkedIn's User Agreement forbids automated applications.
  When Easy Apply asks a question, Aira answers only exact profile matches and confirmed answers, then retries once.
- Fixed: the form engine closed its own page on every run, because the popup guard also fired for the main page. It had
  never worked and had no tests; `tests/test_apply_engine.py` now covers every stand-in ATS and outcome.
- Also fixed:
  - Two threads migrating a new account's database at once (a request and a scheduler tick) leaked a connection that
    kept the database locked until restart. `_connect()` now migrates under a lock, only when behind, and closes the
    connection if migrating fails.
  - The router let a slower earlier page (the dashboard still loading) overwrite the page the user had just opened.
    Each render now draws into its own stage.
- Validation:
  - Full suite: 1620 passed, 5 skipped. The only failures are the 2 Linux-only container tests and one pinned-version
    assertion, since fixed.
  - `tests/test_autopilot.py`: 23 tests, including real Chromium against the stand-in ATS.
  - `tests/test_apply_engine.py`: 14 tests.
  - ruff, ty, `node --check` and the JS tests pass.
  - Browser walk with a stand-in board and ATS and the real local model: 22 of 22 checks. Covered: readiness, answers,
    plan editor, live status, 1 applied and 2 held, answering both, remembered answer, screenshot evidence, the job
    drawer, phone dark.
  - Every-page walk: 26 of 26 pages clean.

### Aira Jobs: finding jobs without SerpApi (2026-10-23)
- API had no data source of its own: every live search went to SerpApi. It now has its own engine **`aira_jobs`**,
  which searches free public job-board APIs (Himalayas, The Muse, Remotive, Jobicy, Arbeitnow) itself, with no key and
  no credits. Details are in `docs/airaapi-spec.md` section 14; code is in `aira/airaapi/jobboards.py` and
  `catalog/aira_engines.json`.
- Each board's terms are followed: per-board caching, credit and a link back on every result, apply links kept, and no
  scripted visits to Himalayas pages.
- It is used in Jobs (new **Aira Jobs** tab and source, the default without LinkedIn), Careers > Jobs ("Search with"
  switch, the default without a key, imports keep the board as source), Auto-apply plans (on by default; board pages
  followed to Greenhouse, Lever, Ashby or SmartRecruiters forms only) and `/v1`.
- Validation:
  - `tests/test_jobboards.py`: 15 tests, including HTTP journeys without a key and an Auto-apply run that submits a
    Muse job through the stand-in Greenhouse form.
  - Updated pinned engine and source counts (137 engines) and the keyless Careers tests.
  - Browser walk on live boards with no key: 16 of 16 checks (Jobs tab, job drawer credit, Careers search and switch,
    engine card, History at 0 credits, Auto-apply editor).

### Password reset, mail server, show/hide passwords (2026-10-26)
- Sign-in → **Forgot your password?** issues a one-time code (`ABCD-EFGH`, 15 minutes, 5 tries, stored hashed in `accounts.sqlite3` `password_resets`). It is always written to the console (`Reset code for ...`) and emailed when a mail server is set. The answer is the same for any email. Resetting signs out every session and signs in. Routes: `POST /api/auth/password/forgot`, `POST /api/auth/password/reset`.
- Settings → Data → **Password** changes it with the current one (`POST /api/account/password`); other sessions end.
- Settings → Integrations → **Password reset email**: Gmail (`smtp.gmail.com:587` STARTTLS + app password) or any SMTP server, with a test send. One for the installation, set by the first account; password Fernet-encrypted (`reset_mail` table). `AIRA_SMTP_*` in `.env` is the fallback. Routes `GET/POST/DELETE /api/settings/reset-mail`, `POST /api/settings/reset-mail/test`; all new routes DENY in `aira_cloud/policy.py`.
- Every password input gets a show/hide button (`enhancePasswordInputs` in `app.js`, also for inputs added later).
- The local sign-in page no longer offers "Sign in with your Aira account"; opening the installation from the account's app launcher (`#/launch?token=`) still works.
- Note: the log redactor masks the word after "password", so log lines say "Reset code", not "Password reset code".
- Validation: `tests/test_password_reset.py` 17 (SMTP stub), account-link and i18n tests pass; browser walkthroughs 12/12 (real server) and owner settings on a scratch server.

### Apply with Claude (2026-10-28)
- **Drafted answers to custom questions** (`aira/answer_drafts.py`): when a form holds on required questions nothing could answer, the model (through `_ollama_json`, so Claude when chosen) drafts answers from the resume and profile, each with a confidence and a "based on" note. A draft must quote the resume and match the choices, or it is dropped; salary, visa/sponsorship, EEO and demographic, criminal-record and attestation/signature questions are never drafted. Jobs > Auto-apply > **Review drafted answers** (setting `review_drafted_answers`, default on): on, drafts are pre-filled under Needs you for approval; off, high-confidence drafts are used and the application submits, anything else holds. Runs record each drafted answer (`questions[].drafted`, `drafted_answers`), shown in the run details, the job drawer and the activity note. Hooks: `Services.draft_answers`/`review_drafts` in `execute_run`. Tests: `tests/test_answer_drafts.py` (40).
- **Claude as the model provider** (`aira/model_provider.py`, official `anthropic` SDK, added with `uv add anthropic`): Settings → Integrations → **Claude (Anthropic)** picks the provider (`model_provider` setting: `ollama` default, or `claude`) and the model (`claude_model`: Sonnet 5.5 default, Opus 5.5, Haiku 5.5), saves an Anthropic API key (Fernet-encrypted in `airaapi_secrets` as `anthropic_api_key`, like the SerpApi key; only `last4` is ever returned), tests it and removes it (back to Ollama). `_ollama_json` keeps its name and signature and goes to Claude when the provider is `claude` and a key is saved (same system rule: job text is untrusted, never invent qualifications, JSON only; server-side refusal fallback `fallbacks: "default"` is on); otherwise it is unchanged, and `HOSTING.model_json` still wins in the hosted build. `_ollama_available()` (model ready) is true with Claude, `/api/status` adds `model_provider`, and the status pill says "Claude ready". SDK errors become `RuntimeError`s with no key in them (rejected key, no credit, rate limit, model unavailable, busy, timeout, offline, refusal, bad JSON, cut off). Routes (session + same-origin; saving, testing and removing need an adult profile, like the SerpApi key; DENY in `aira_cloud/policy.py`): `GET/POST /api/settings/model-provider`, `POST /api/settings/model-provider/test`, `DELETE /api/settings/model-provider/key`. Tests: `tests/test_model_provider.py` (20, stand-in Messages API server; never the real API).
- **Workday applications** (`aira/workday.py`, accounts in `aira/workday_accounts.py`): `apply_engine.run` hands Workday (`myworkdayjobs.com`/`myworkdaysite.com`, or source `workday`) to `run_workday`, which signs in to the tenant (or creates the account), then walks My Information, My Experience (uploads the resume; keeps what Workday's parsing filled in), Application Questions, Voluntary Disclosures, Self Identify and Review through `data-automation-id`s, including Workday's listbox dropdowns. It reuses the form engine's discovery, answers, uploads and evidence. Fill-only stops at Review with a screenshot (each page is saved as a draft on the employer's site); submit presses Submit and waits for the confirmation. Holds ("needs you") for unanswered or sensitive questions, a verification email, a CAPTCHA, a rejected password and unconfirmed account terms; Voluntary Disclosures and Self Identify are answered only from the person's run answers or confirmed saved choices (for example "I do not wish to answer"), never from the profile, and any open question there holds, required or not. Accounts: one per tenant host, password Fernet-encrypted with the SerpApi key's store, either the person's own or "create it for me" (profile email + generated password saved before the form is filled), plus a preference to create accounts on new tenants automatically; the last run's state is remembered (working, verify the email, sign-in failed, already exists). `ApplyRequest.workday_account` carries the decrypted account for one run (`_apply_with_engine` fills it via `credentials_for_run`). Routes (session, owner-only, same-origin): `GET/POST /api/autopilot/workday-accounts`, `PATCH/DELETE /api/autopilot/workday-accounts/<host>`, `POST /api/autopilot/workday-settings`; passwords are never returned (emails masked) or logged. UI: a **Workday accounts** card in Settings > Integrations (`views-workday.js`, registered through the new `Aira.settingsCards(tab)` hook). Tests: `tests/test_workday.py` (17, stand-in tenant `tests/workday_stub.py`; never a real Workday site).
- **Resume editor and one application count in Careers**: a job's **Documents** tab (job drawer) is now an editor: the tailored resume and cover letter (a Resume / Cover letter switch) beside the job description, edited in place. Every save is a new immutable `tailored_resume`/`cover_letter` version in the document store, linked to the job, with `approved`, `source` and the person's notes in its metadata; **Approve and save** (with "I reviewed both and every claim is true") is the approval and still moves the job to Ready to apply; **Save as a draft version** keeps an unapproved one; any older version opens again in the editor. **Regenerate with my notes** passes the notes to `_draft_documents(job_id, model, notes=...)` (through `_ollama_json`) as the person's guidance on emphasis, order and tone, after the source resume and before the untrusted job description; notes never add facts (2000 characters at most), and the result is a version that still needs approving. Applications upload the newest approved version: `_with_approved_documents` runs at the start of `_apply_with_engine` and `_apply_with_linkedin`, and `resume_for_upload` puts `job["approved_resume"]` ahead of the master resume file; without any approved version nothing changes (the current draft, as before). **Your applications** on top of Applications > Pipeline counts every application once, manual and automatic together, split "by you" / "automatically": Submitted, In process (queued or filling, or opened and not recorded), Needs you (held or waiting for your click), Failed, Interviews (submitted and reached an interview). Each count and each split links to the list behind it (`#/applications?tab=pipeline&show=<bucket>&via=manual|auto`, `show=all`). Definitions in `aira/apply_summary.py` (submissions, application runs, autopilot passes, interviews and status history). Routes: `GET /api/applications/summary?bucket=&via=&list=1`, `GET/POST /api/jobs/{id}/documents/versions`, `GET /api/jobs/{id}/documents/versions/{document_id}`, and `POST /api/jobs/{id}/generate` takes `notes`; all four are in `aira_cloud/policy.py`. UI in `views-applications.js` (`Aira.renderResumeEditor`; `views-jobs.js` `renderDocuments` delegates), CSS `cre-`/`cas-` at the end of `app.css`. Tests: `tests/test_resume_editor.py` (4), `tests/test_careers_apply_summary.py` (5), browser `tests/test_suite_careers_apply_ui.py` (2, desktop and phone dark; stub model).

## Current Work

None in progress. All slices above are implemented and validated.

## Decisions and Assumptions

- Kept the existing Python/uv/stdlib HTTP/SQLite/Ollama stack; no framework migration was justified for a loopback app.
- No connected source has an official candidate-side submission API, so submission stays user-performed and is recorded with evidence. Auto-submit is reported unavailable and cannot be enabled.
- Gmail/Outlook and Google/Microsoft calendar APIs need OAuth credentials that do not exist here; email uses paste/.eml and calendars use ICS plus deep links.
- Workday, iCIMS, Taleo, Oracle, SuccessFactors, BambooHR, UKG, and direct career sites are listed as planned because they lack an official public posting API.
- Sensitive profile fields never go into model prompts and are never prefilled without a confirmed standard answer.
- A manual draft save stamps the current resume version, because the user is reviewing against the resume that exists at that moment.
- Daily application limit 0 means unlimited.

## Commands Executed and Results

| Command | Result |
|---|---|
| `uv run python -m pytest -q --deselect <2 Linux-only container tests>` | 979 passed, 5 skipped (POSIX permission tests), 2 deselected. |
| Full suite after the API, Careers and integration work (`uv run python -m pytest -q`, 2 Linux-only container tests deselected) | 1494 passed, 5 skipped, 2 deselected. |
| `node tests/js/serp_kit.test.mjs`, `node tests/js/playground.test.mjs` | 32 and 9 passed, 0 failed. |
| `uv run ruff check auto_apply_assistant.py aira/ tests/ linkedin_mcp_server/` | All checks passed. |
| `uv run ty check auto_apply_assistant.py aira/ linkedin_mcp_server/` | All checks passed. |
| `node --check` on every file in `auto_apply_assistant/` (one call per file; a single glob checks only the first) | All scripts parse. |
| `uv build` | Wheel built; includes the `aira` package. |
| End-to-end API walkthrough against a live server (scratch script) | 93 of 93 checks passed, including live imports from the GitLab Greenhouse board and a live search of the Ashby board. |
| Headless Chromium walk of every page, desktop light and mobile dark | All pages render; only console error is the expected 401 session probe on the sign-in screen. |
| Migration on a copy of the real `applications.sqlite3` | Upgraded 0 → 8, 11 jobs preserved, idempotent. |

New test files: `tests/test_ats_adapters.py` (101), `tests/test_scoring.py` (74), `tests/test_field_mapping.py` (76), `tests/test_email_intel.py` (88), `tests/test_documents.py` (46), `tests/test_calendar_sync.py` (17), `tests/test_career_copilot.py` (11 HTTP integration journeys).

## Known Blockers

- No SerpApi key has ever been used here, so live Google Jobs, Careers and API responses are verified only against the stand-in server's fixture-shaped data (real response shapes, 402/429 wording and archive behaviour are unseen).
- No mailbox or calendar OAuth credentials.
- Two Linux container-detection tests fail on Windows by design; five POSIX permission tests are skipped.
- Sending a real LinkedIn reply was not exercised against live LinkedIn; the reply tool was verified with unit tests and a stand-in server. The first real send should be checked in the LinkedIn thread.

## Exact Next Action

Restart the running Aira server (`.\scripts\start_aira.ps1`) so it serves the new code; the database upgrades automatically on first request. Then connect LinkedIn from Settings → Integrations and paste a SerpApi key in API → Account (or add `SERPAPI_API_KEY` to `.env`) to verify those two live sources; check the first Google Jobs search, one Careers report and one `/v1` call against the real service.

### SerpApi-compatible API fixes (2026-10-23)
- The compatible API now answers on SerpApi's own paths (`/search`, `/search.json`, `/search.html`, `/account.json`, `/locations.json`, `/searches/{id}.json|.html`) as well as `/v1/...`, so an SDK or script only needs its base URL pointed at Aira and an API key.
- Unknown parameters pass through (as SerpApi does; its SDKs add `source=python`); `allow_unknown=false` restores strict validation.
- `json_endpoint`, `raw_html_file` and `serpapi_pagination` links in responses point at Aira, so `next_page()` and archive lookups stay on Aira.
- `/locations.json` needs no key (like SerpApi); a presented key is still checked.
- Verified with the official `serpapi` and `google-search-results` Python SDKs against a scratch server plus the stand-in SerpApi (search, next_page, account, locations, archive, html, async, 402/401 errors), and `/locations.json` live against real SerpApi. Live searches with a real SerpApi key remain unverified.
- Tests: 1487 passed; the only failures are the 2 Linux-only container tests.

### End-to-end test and fix pass (2026-10-23)
Four independent testers, each in an isolated scratch Aira with the stand-in SerpApi, then fixes:
- **All 136 engines**: API, SerpApi-compatible `/search.json` and Playground UI loops all pass; catalog lint clean; 32 engines checked against public SerpApi docs. Fixed: `google_jobs_listing` ratings renderer, `requires_any_of` on `ebay_seller`/`walmart_product_sellers`, double required markers on 4 engines, bogus `result_keys` on 5 engines, and YYYYMMDD dates on the engines that document them (`google_images`, `google_images_light`, `google_forums`, `google_ads_transparency_center`).
- **Security**: 13 backend fixes (no lockout of valid keys by junk keys, cold-cache stampede runs once, history cap on every insert, concurrent hourly-limit overshoot, DNS-rebinding Host check now on every route, key no longer in DEBUG logs when percent-encoded, CSP sandbox on stored HTML, zlib inflate bound, keyless `/locations.json` cached and rate limited, CSV formula neutralising, nested-JSON guard (400), key-file ACL on Windows). Archive ids that were deleted or pruned answer `410 gone` for 31 days (migration 14).
- **Careers**: every tab through API and browser; 5 fixes (alert naming, shared cache key with the Jobs tab, alert card copy, usage link, save-jobs copy); usage CSV export; "Save jobs to Aira" from History; honest coverage tables in Docs.
- **Design walk** at 1440/1024/390 light and dark: 11 CSS fixes (overflow, bidi text, contrast, tap targets, numerals, checkbox colour, stat-tile grid) plus literal "null" text on Jobs/Applications, missing labels on Standard answers, dashboard widget stretch, History filters folded into a disclosure on phones, Markdown output option in the Playground.
- **Live SerpApi** (key from `.env`, Free plan): 2 real searches (Google Jobs, Google); cache hit cost 0; archive by SerpApi id works; `json_endpoint` points at Aira. Official `serpapi` and `google-search-results` Python SDKs work unchanged with the base URL pointed at Aira.
- Validation: 1540 passed (only the 2 Linux-only container tests fail on Windows), ruff and ty clean, every script parses, 41 JS unit tests pass.
- Not verified: real responses for the other 134 engines (stand-in shapes only); the messenger against real LinkedIn.

## Multi-tenant SaaS (started 2026-10-24)

Spec: `C:\Users\Suresh\Downloads\Aira Career Copilot — Multi-Tenant SaaS Scope.md`. Build plan: a hosted shell `aira_cloud/` around the shared `aira/` domain code; the local single-user build is unchanged and its tests stay green.

### Design decisions (as built)
- **Control plane**: SQLite (`aira_cloud/db.py`, `schema.py`, migrations 1-2), `org_id` on every tenant table, portable SQL. PostgreSQL + row-level security is the production target; the workspace layout and SQL are kept portable for it but a PostgreSQL adapter is **not built**.
- **Workspaces**: one SQLite per candidate under `tenants/<org_id>/<candidate_id>/workspace.sqlite3` (the existing per-account database, domain modules unchanged). Documents `tenants/<org>/<candidate>/<doc>.bin` or S3 `tenant/<org>/<candidate>/<doc>.bin`, encrypted with the org's data key (envelope: master key wraps per-org keys; `crypto.SecretManager`, rotation without downtime).
- **Seams in shared code** (`aira/hosting.py`, all `None` locally): worker registry, documents provider, settings overlay, SerpApi key, secret key, model gateway, services wrapper (limits/policy), submission listener, background start/status/progress, key index. `aira.documents` got a blob-backend interface; `service.resolve_key` consults the hook.
- **Gateway** (`aira_cloud/gateway.py`): pure `HttpRequest -> HttpResponse`; control-plane routes `aira_cloud/routes/*` (decorator `cloud_route`), and the local build's 171 routes run through a shim under `aira_cloud/policy.py` (default deny, every route listed). Hooks per route kind in `route_hooks.py`; sensitive-field redaction in `redaction.py`.
- **Roles/permissions** `rbac.py` (matrix from the spec; coach scopes view<act<approve capped by plan; unknown/foreign candidate = 404). Seats: owner/admin/coach take "coach and admin" seats; billing contact and auditor do not.
- **Entitlements** `plans.py`, `entitlements.py` (60 s cache, operator feature flags with global kill switch, atomic usage counters). Billing `billing.py` + `billing_providers.py` (stand-in with hosted-checkout pages and signed webhooks; Stripe adapter with real signature verification). Lifecycle `lifecycle.py` (dunning 14/60 days, export window, deletion keeping billing record + audit stub).
- Queue/workers: `jobqueue.py` (tenant-fair lease, per-org concurrency cap), `worker.py`, `scheduler.py`, gate `gate.py` (re-checked at run start), `hooks.py`.
- Tests: `tests/test_cloud_*.py`, helpers in `tests/cloud_support.py` (FakeClock, `build_org`, `Browser` that calls the gateway directly).

### Status (updated 2026-10-24, late)
- **Backend built and tested** (each file passes): `test_cloud_identity` 17, `test_cloud_rbac` 24, `test_cloud_billing` 18, `test_cloud_tenancy` 25, `test_cloud_isolation` 8 (every route with another tenant's ids: 100% accounted for), `test_cloud_gateway` 24, `test_cloud_worker` 16 (fairness for 50 candidates/5 orgs; real Chromium pipeline, review mode, held questions, caps), `test_cloud_integrations` 20 (S3 with independent SigV4 check, Stripe stub, shared SerpApi cache, API metering and /v1, model gateway incl. no sensitive values sent), `test_cloud_hardening` 8 (key rotation incl. documents, legal hold, SIEM stream, HSTS, priority), `test_cloud_backup` 4 (restore drill: one tenant restored, others byte-identical), `test_cloud_agent` 9 (worker agents, signed forwarding, remote sign-in link), `test_cloud_sso` 38 (OIDC, SAML via signxml, SCIM; from the SSO fork), `test_cloud_legal` 45 (docs fork).
- **UI built by forks** (browser-verified by them): sign-in/onboarding/consent/coach (`cloud-auth.js`, `cloud-coach.js`, `cloud-boot.js`, `cloud.css`), organization administration (`cloud-org.js`, `cloud-people.js`, `cloud-admin.css`). Billing and platform console (`cloud-billing.js`, `cloud-platform.js`) was still being built.
- **Docs/deploy** by the docs fork: `docs/multitenant*.md`, `docs/hosted-api.md`, `docs/pricing.md`, `docs/legal/*`, `deploy/` (Dockerfile, compose, k8s, terraform skeleton: written, not run), `.github/workflows/cloud.yml`, `scripts/start_aira_cloud.ps1`.
- Local build: full suite 1636 passed, 5 skipped, 2 failed (the two Linux-only container tests, as before) after the shared-code seams were added.
- Shared-code changes (all behind `aira/hosting.py` hooks; local behaviour unchanged): documents blob backend, settings overlay, SerpApi key/secret key/model hooks, services wrapper (incl. `submit_allowed` so a person's click always submits), background start/status/progress, key index, max plans, search connection (shared cache), browser time cap. Local fix: `GET /api/jobs/<unknown>/preparation` is a 404.
- Added since: overage toggle (`POST /api/org/billing/overage`, billing page button), org-branded sign-in (`/api/orgs/<slug>/branding`, only for orgs that chose branding), `test_cloud_dashboard`, `docs/multitenant-acceptance.md` (criterion-to-test matrix), gaps list in `docs/multitenant.md` brought up to date, `docs/hosted-api.md` regenerated, ruff and ty clean for `aira_cloud` and the CI scope.
- Integrated browser walk (Chromium, demo server, all 8 sign-ins: owner, admin, coach, candidate, billing, auditor, Starter owner, trial owner; every nav page): no console errors or failed API calls except an auditor 403 on the SSO card, now replaced by a "visible to owners and admins" note.
- Internal adversarial security review (2026-10-24): MFA re-enrolment bypass, SCIM owner/admin email takeover, unmasked sensitive values in exports, model-endpoint SSRF, `X-Forwarded-For` spoofing, CEF newline injection: all fixed with regression tests; table in `docs/multitenant-security.md`. Exports now also include autopilot plans/runs, custom answers and message tables. Overage route bug (`req.json()`) caught by `ty` and covered by a route-level test.
- Final run (after the second security pass and the gap-closing work): SQLite `uv run python -m pytest -q -n 4 -p no:randomly`: 1995 passed, 21 skipped (PostgreSQL-only), 2 failed (the two Linux-only container tests, as before). PostgreSQL 16 in Docker (`AIRA_TEST_DATABASE_URL`): `tests/test_cloud_*.py` 375 passed. `ruff check`, `ruff format --check` and `ty check` clean.
- Built since the first report: PostgreSQL control plane with row-level security (`AIRA_CLOUD_DB=postgres`, `migrate-to-postgres`), KMS master keys (AWS KMS, Vault transit) and `rewrap-keys`, pooled SerpApi and API search metering with overage, custom limits and retention enforcement, remote sign-in broker (`python -m aira_cloud broker`, real-Chromium tests), Docker stack smoke test (31 checks), kind cluster smoke test (11 checks), Terraform validate/checkov/tflint, stripe-mock and Keycloak (OIDC + SAML) integration tests, second security pass (nine findings fixed, `docs/multitenant-security.md`), queue lease ownership and renewal, organization monitors scheduler.
- Open: no external penetration test; no real Stripe account, Okta/Entra/Google tenant or live LinkedIn sign-in; Terraform never planned or applied; PostgreSQL poolers/replicas/failover untested; DNS rebinding after a URL is saved is not covered by the SSRF check. Environment note: C: was nearly full while forks ran (Docker engine crashed; unrelated `monosage-*` containers are stopped, not removed); test temp files for the final runs went to D:\aira-test-tmp (safe to delete).

## Aira Leads (2026-10-24)

A lead-generation application next to Careers (`#/leads`, `g` then `l`): `aira/leads/` (store, sources, scoring, enrichment, outreach, service), `aira/airaapi/leads_routes.py` (`/api/leads/*`), migration 18, `auto_apply_assistant/views-leads.js` + `leads.css`, `docs/leads.md`, `docs/api.md` section.
- **Sources:** Google Maps (businesses with phone and website), Google web (`site:linkedin.com/in` people, `/company` companies), hiring signals (Google Jobs), Google News signals per lead, LinkedIn Sales Navigator (new read-only MCP tool `search_sales_navigator`, `linkedin_mcp_server/scraping/sales_navigator.py`, the person's own signed-in session), CSV import.
- **Also:** ICP fit scoring with reasons, website enrichment (public pages only, SSRF-safe, robots.txt), outreach drafts that are never sent, lists/statuses/bulk actions, CSV and XLSX export, de-duplication (LinkedIn profile, Sales Navigator lead, Google place, domain outside Maps, name+company).
- **Hosted build:** `leads` plan feature (Starter+), limits `leads_stored` and `lead_enrichments_per_month`, `leads.read/write` for the candidate and act/approve coaches only, Google sources metered like Google Jobs, Sales Navigator gated like LinkedIn and forwarded to the worker host, staff exports leave lead tables out, DPA section on prospect data.
- **Tests:** `test_leads` (30), `test_leads_sales_nav` (4, real Chromium reader -> Leads), `test_sales_navigator` (reader), `test_leads_ui` (real-browser journey incl. Sales Navigator results, viewports and themes), `test_cloud_leads` (17). Full suite: 2105 passed, 21 skipped (PostgreSQL-only), 2 failed (the Linux-only container tests). ruff and ty clean. Also fixed a flaky xdist collection in `tests/test_documents.py` (zip timestamps in test ids).
- **Not verified:** the real Sales Navigator DOM (only stand-in pages), a live SerpApi key (stub only). Restart the app and Settings > Integrations > LinkedIn restart before the first Sales Navigator run.

### Leads for services (2026-10-24)
Finding people and businesses that need web, marketing, content or ad work, and reaching them. `aira/leads/{intent,mail,calls}.py`, migration 19, `views-leads-outreach.js`, `docs/leads.md`.
- **Finding:** `google_intent` source (public posts asking for a service on Reddit, Quora, LinkedIn posts or anywhere, by freshness), Maps gaps (no website, few reviews, low rating), a plain website audit on Enrich (no HTTPS, mobile viewport, meta description, analytics, ad pixels, old copyright, thin content, social links, speed), hiring roles per service; ICP `services` raises the score of leads that need them.
- **Reaching:** email outbox with per-message approval, the person's own SMTP, a daily cap, a postal address and opt-out line in every message, a do-not-contact list (email, domain, phone), a regulated-region guard, drafts written by Ollama or a template from real facts only (guards against the model saying the sender is from the lead's business, greeting a business by name, or asking about prices).
- **Calls:** call assist only. **There is no auto-dialer and no Google Voice automation** (Google Voice policy forbids automated calling; AI-voice marketing calls need prior written consent under the US TCPA): talk track, `tel:` and Google Voice dialler links, calling-hours hint, call log with follow-ups, "do not call" adds to the do-not-contact list.
- **Hosted:** `leads_outreach` feature (Team and Enterprise), candidate-only `leads.outreach` permission, monthly and daily caps, kill switch, public SMTP hosts only, password encrypted under the org key, staff exports leave lead tables out.
- **Checked with real services:** two live SerpApi calls (Maps and the LinkedIn X-ray; the people parser was rewritten after seeing real headlines) and the real local Ollama qwen3.5:4b (found and fixed the "calling from <the lead's company>" slip).
- Full suite: 2170 passed, 21 skipped (PostgreSQL-only), 2 failed (the Linux-only container tests). ruff and ty clean. Not run against a real SMTP provider or a real Reddit search.

## One account, many apps (2026-10-25)
Design in `docs/apps.md`; catalog `aira/apps.py` (Careers = Dashboard, Jobs, Applications, Interviews, Recruiters, Documents, Analytics, Research; Leads; API; Settings/Security = account). "Aira API" renamed to "API" everywhere (312 replacements).
- **Navigation (both builds):** app switcher (nine-dot grid) in the top bar and in the phone More sheet, app-scoped sidebar with the app's name, account menu (email, organization, Manage your account, Theme, Shortcuts, Sign out), `g` + app key chords; hosted roles grouped into Account (organization pages), Coaching, Admin console. `tests/test_app_switcher_ui.py` (9 real-browser tests).
- **Per-app subscriptions (hosted):** migration v4 `org_apps`, `APP_PRICING` (Leads $4/seat on Starter and Team, API $3/seat on Team, both included in Enterprise, trial has all), `Entitlements.has_app`, `402 app_required` gating on app routes, billing add-on lines (stand-in and Stripe items), proration, cancel at period end, downgrade drops unavailable add-ons, operator grants; routes `/api/apps`, `/api/org/apps/*`, `/api/platform/tenants/<id>/apps`; Apps page `#/org/apps` (`cloud-apps.js`). `tests/test_cloud_apps.py` 17 (SQLite, PostgreSQL, stripe-mock).
- **Account link 8780 → 8765:** `GET /api/launch/local` issues a 60 s single-use token (hashed, migration v5 `launch_tokens`), `POST /api/internal/launch/validate` (signed, purpose `launch`), local `POST /api/auth/launch` creates a password-less linked account; env `AIRA_CLOUD_LOCAL_APP_URL` + `AIRA_CLOUD_LAUNCH_SECRET` (account) and `AIRA_ACCOUNT_URL` + `AIRA_ACCOUNT_SECRET` (local). `tests/test_cloud_launch.py`, `tests/test_account_link.py` (incl. a two-process real-browser run).
- Integration fixes: hosted `/api/apps` says `installation: "hosted"` (the hosted switcher shows Local workspace and More apps, not "Manage your account"); `tests/conftest.py` blanks `AIRA_ACCOUNT_*` so a linked `.env` cannot leak into tests. This checkout's `.env` / `.env.cloud` (ignored) link 8765 to 8780 with a shared secret; `start_aira_cloud.ps1` loads `.env.cloud`.
- Not built: `#/switch-org` page (the link never shows; switching stays sign-out + organization picker); platform tenant detail does not list apps (the platform apps route does).

## Industry apps and the superadmin (2026-10-25)
Request: every account sees only the apps it has; the superadmin sets up subscriptions; one app per industry (like CRM clouds), useful for earning money, built one by one.
- **Owners and admins use the apps themselves:** personal workspace (`POST/DELETE /api/workspace/own`, one candidate seat, only its owner can open it; gateway treats them as its candidate per request via `Principal.as_own_candidate`). `tests/test_cloud_personal.py`.
- **Catalog:** `aira/apps.py` `INDUSTRY_APPS` (Studio, Bookings, Shop, Academy, Events, Marketing; scope `org`, feature `app.<id>`, `g` + 1..6). Pricing $5/seat Starter, $4 Team, included in Enterprise and the trial (`plans.APP_PRICING`). Switcher shows only apps the account can open, grouped Core / Industry apps.
- **Superadmin:** `POST /api/platform/tenants/{id}/subscription` (plan, seats, paid months, app grants; both audit logs) + platform console "Set subscription" and an Apps table per tenant. `tests/test_cloud_suite_admin.py`.
- **Suite framework** (`docs/suite-sdk.md`): `aira/suite/kit.py` (validation, cents, business profile, activity, revenue ledger, checklists/summaries, outbox through the business's SMTP, `draft`, `public_url`), `aira/suite/public.py` (public links `/p/<token>`, page kinds and visitor actions; index in accounts DB locally, control-DB `public_links` (migration v6) hosted, `aira_cloud/public_links.py`), Business hub `/api/suite/*` + `views-hub.js` (revenue, monthly goal, start-here, apps, activity, outbox, business profile, email). Hosted rules generated by `policy.suite_rules()` (GET `suite.read`, else `suite.manage`; owner/admin/coach manage, auditor reads). `suite-kit.js` UI kit, `public.html/js/css`. `tests/test_suite_core.py`.
- **Apps** (each `aira/suite/<id>/`, `views-<id>.js`, `public-<id>.js`, `tests/test_suite_<id>*.py`, `docs/apps/<id>.md`): Studio (clients, proposals accepted online, projects/tasks/time, invoices + payments, retainers), Bookings (services, staff hours, slot engine, booking page, reschedule/cancel, packages, reminders), Shop (products/options/stock, storefront + cart, orders, discounts, customers), Academy (courses, lessons, quizzes graded server-side, enrolment, student pages, certificates, cohorts), Events (ticket types, no oversell, tickets with codes, check-in, promo, waitlist, sponsors), Marketing (calendar + AI drafts per channel, campaigns/ROI, tracked links, forms → subscribers, link-in-bio, newsletters with unsubscribe).
- `AIRA_SUITE_STRICT=1` (tests) makes a broken app fail loudly; otherwise it is logged and skipped.
- Known limits: Aira records payments the business confirms (no card processing); lesson videos are links (CSP blocks iframes); public slot lookups count as visitor actions (60/min).

## Everyday (life) apps for every age (2026-10-25)
Request: real-time everyday apps (social, entertainment, education, daily life) usable by people of every age, with easy navigation.
- **Catalog:** `aira/apps.py` `LIFE_APPS` (category `life`): Family (`family`), Health (`care`: `/api/health` is the server health check), Learn, Play, Planner. Hosted price $2/seat Starter, $1.50 Team, included in Enterprise and trial; every role has `life.read`/`life.manage`, never via support access.
- **Life core** (`docs/life-sdk.md`): `aira/suite/life.py` (profiles per person on a shared device with optional PIN; hosted the member is the person via `suite.set_actor_resolver`), `life_routes.py` (`/api/life`: profiles, unlock, me, prefs, today, pulse), `life.js` (Who's using Aira picker + PIN pad, Display and reading: easy mode, text size, contrast, read aloud, less motion, start page; Home button; Today; 20 s pulse with badges and spoken reminder dialogs; `Aira.suite.live` page polling). Errors carry `code` (`profile_required`, `profile_locked`).
- **Background:** `aira/suite/background.py` runs each app's task and sends due approved mail every 5 min (local scheduler thread; hosted `suite.tick` job). `kit.ALWAYS_SEND` = safety notices (`missed_dose`).
- **Apps:** Family (feed, live chat with receipts, calendar + RSVP, dates, public board for relatives), Health (medicines + dose slots + Taken/missed, caregivers, readings, diary, appointments, emergency card), Learn (SM-2 flashcards, streaks, 10 starter decks, quizzes, maths, reading, leaderboard, AI decks), Play (6 server-verified games, daily challenge, live trivia with public join, watch list), Planner (chores with recurrence/points/rewards, live shopping lists + public link, budget and bills for adults, notes).
- Tests: `tests/test_suite_life.py`, `test_suite_background.py`, `test_suite_{family,care,learn,play,planner}*.py`; helper `tests/life_support.py`.

## More apps, one catalog, and the superadmin's catalog controls (2026-10-25, later)
- **Added since the everyday apps:** Social (`docs/apps/social.md`, social + family), News (`docs/apps/news.md`), Service (`docs/apps/service.md`, helpdesk), and the developer platform in the API app, renamed Developers (`docs/developers.md`: scopes, OAuth 2.0, personal access tokens, webhooks; `aira_cloud/oauth.py`, `webhooks.py`, `routes/developers.py`, migration 7). UI languages: Telugu and Hindi (`docs/i18n.md`, `scripts/i18n_*.py`).
- **Consolidated catalog (13 apps, was 17):** Core: Careers, Developers (`api`). Business: Sales (`studio`), Marketing (sections Marketing and Leads), Service, Commerce (Shop + Bookings), Courses & Events (`experiences`: Academy + Events). Everyday: Social (Social + Family), Health, Learn, Play, Planner, News. `LEGACY_IDS` / `current_id` / `owner_of` in `aira/apps.py` map old ids (`shop`, `bookings`, `academy`, `events`, `leads`, `family`) everywhere; stored subscriptions under an old id move on first read (best status wins). Leads is org-level (owners, admins, coaches; auditors read; candidates and billing get 403), needs Marketing (`402 app_required`), refuses Sales Navigator (`403 source_unavailable`, import a CSV); Marketing grants `leads` only, sending lead email stays `leads_outreach` (Team+).
- **Superadmin catalog controls:** platform console **Apps** (`#/platform/apps`, `GET/PUT/DELETE /api/platform/apps/{id}`), `entitlements.AppSettings` over table `platform_apps` (migration 8, a global table). Switch an app off for everyone (closed to trial, Enterprise and grants; routes and public links `403 app_disabled`; not billed; data and add-on rows kept) and set seat prices per plan (cents, 0 = included, null = not offered; only differences from `plans.APP_PRICING` stored). After a change `Billing.resync_app` rewrites every paying organization's provider subscription without proration, so the new terms bill from the next period. Careers cannot be priced or switched off. Platform audit `app.settings_update` / `app.settings_reset`. Org Apps page shows a switched-off app as Paused. `tests/test_cloud_app_controls.py` (4, incl. a real-browser run).
- Fixed: `test_suite_bookings` "later today" booking failed after 4 pm New York (now books two hours ahead with `outside_hours`). `scripts/i18n_extract.py` also extracts `*Disabled` error messages.
- Not built: notifying organizations of a price change before it applies; the organization export does not yet include the business apps' shared data (Leads has its own audited export).

## Videos and Ads: watch, upload and earn (2026-10-25, night)
Request: an entertainment app where people watch, upload videos, reels and short films and earn money, and an
advertising manager so businesses pay to advertise and creators earn from those ads. Docs: `docs/apps/videos.md`,
`docs/apps/ads.md`; catalog, pricing and hosted API updated (`docs/apps.md`, `docs/pricing.md`, `docs/hosted-api.md`).
- **Catalog:** `videos` (everyday, $2/$1.50, no `g` key) and `ads` (business, $5/$4) in `aira/apps.py`; packages added to
  `aira.suite.LIFE_PACKAGES` / `INDUSTRY_PACKAGES`; NAV/APPS/icons in `app.js`; console pages in `cloud-platform.js`.
- **One platform-wide store** like Social: `aira/suite/videos/db.py` (`videos.sqlite3`, Videos `vi_*` and Ads `ad_*`
  tables; local next to the accounts database, hosted in the data root). Channel per person (`m:<member>` / `p:<profile>`).
- **Media** (`aira/suite/videos/media.py`): resumable uploads in 8 MB base64 parts (fits both builds' JSON limits, never a
  whole file in memory), type by magic bytes (MP4/MOV, WebM/Matroska, JPEG/PNG/WebP, WebVTT), MP4 `moov` parsing for
  length/size (rotation aware), caps (2 GB video, `AIRA_VIDEO_MAX_BYTES`), a 2 GB free-space reserve
  (`AIRA_MEDIA_RESERVE_BYTES`) whose refusal names `AIRA_MEDIA_DIR`, hosted quota against `storage_bytes`, HTTP ranges
  (206, ≤4 MB a response, 416), HMAC-signed media addresses (a `<video>` cannot send the profile header). Hosted keeps
  files on disk under `data_root/media` (the S3 document backend holds whole encrypted blobs, unsuitable for video).
  CSP gained `media-src 'self' blob:` (`aira/security.py`) for previews of a picked file.
- **Videos** (`service.py`, `videos_routes.py`, `views-videos.js`, `public-videos.js`): kinds video/reel (tall, ≤90 s)/
  short film (genre, credits), ratings everyone/teen/mature enforced per age (child, teen, easy mode, restricted mode),
  public/unlisted/private, picture from a frame picked in the browser (canvas) or an image, WebVTT captions; Home
  (subscriptions, a Reels shelf, Popular by a published formula, New, categories, search), watch page, Reels feed
  (muted autoplay, wheel/swipe/keys/buttons), Short films, Library (history, watch later, playlists, subscriptions),
  Studio; views count once a day after 30 s or 30% measured server-side, never the uploader's; reports, household
  moderation, computer-owner (first account) moderation, public copyright form `/videos/copyright` (new local route
  `ui_copyright`; hosted answered by a control-plane route), takedown/counter-notice/strikes (3 block uploads);
  public video page `/p/<token>`; Today card; rate limits.
- **Ads** (`aira/suite/ads/`, `views-ads.js`): campaigns (CPM/CPC, budgets, pacing, schedule, category/language/country
  targeting, family-safe, mature opt-in, frequency cap), creatives (skippable pre-roll after 5 s, banner, sponsored
  reel) reviewed before running; serving by highest effective bid; impressions count after 2 s (server-checked);
  invalid traffic (own ads, own video/household, repeats, hourly cap, late) never charged; signed click redirect;
  prepaid balance (hosted `POST /api/org/ads/topup` → `Billing.charge_ads_topup` adds an invoice item at the org's
  provider and credits when accepted; local owner records payments); pause at zero, resume on top-up; reports + CSV.
- **Creator money:** 55% share (settable 0–90), ledger estimated → confirmed at month close (auto from the 2nd, with a
  per-viewer-per-channel-per-day review) → payable; eligibility (adult, terms, a public video, not suspended, no strikes,
  no recent moderator hide); payout requests ≥ $10, marked paid with a reference; clawbacks refund advertisers.
  Superadmin: `aira_cloud/routes/creators.py` (Videos moderation, copyright, ad review, settings, payouts, close month,
  clawback; platform audit `videos.*`, `ads.*`); locally the Ads Admin tab (routes denied hosted via `HOSTED_RULES`
  `"deny"`, a new option in `policy.suite_rules`).
- **Earning overview (phase 3):** Business hub Ads card (balance, spend, impressions, clicks) and checklist; Today shows a
  creator's earnings; on this computer a paid payout joins the account's revenue ledger (`creator_payout`); hosted a
  member's payout stays theirs (not the organization's revenue).
- **Erasure/export (hosted):** `hooks.erase_videos` on member removal and organization deletion (channels, videos and
  files; advertiser's ads); `videos.json` in a person's export. Money rows stay without the person.
- **i18n:** 691 new strings translated into Telugu and Hindi; `i18n_check` te/hi: 11913 strings OK.
- **Tests:** `tests/test_suite_videos.py` 22, `tests/test_suite_ads.py` 18, `tests/test_suite_videos_ui.py` 5 (real
  Chromium: a reel recorded in the browser via MediaRecorder, uploaded with a picked frame, watched, Reels navigated;
  an ad played before a video, skipped after 5 s, creator credited and shown on Earnings/Today/hub; console Videos
  moderation and Creators & ads; phone and easy-mode layout). Updated app-list tests; isolation sweep green.
- **Not built / limits:** no transcoding (MKV may not play); public pages show no ads; no automated payouts; ad money in
  USD only; invalid-traffic rules are the simple listed ones; single-channel per person; no video download/offline.

## End-to-end pass (2026-10-25, night)
- Both servers restarted on the current code; `.env` / `.env.cloud` set `AIRA_MEDIA_DIR` to `D:/aira-media/local` and `D:/aira-media/cloud` (C: is nearly full).
- New `tests/test_e2e_walk.py`: every page of every app for every hosted role (owner, admin, coach, candidate, billing, auditor, every app on), every platform-console page, every local page, failing on script/console errors, failed requests or error boxes; plus a hosted upload through the real upload screen watched by a member of another organization.
- Fixed from the walk: staff opening a candidate-workspace page (Careers sections, Settings) without a workspace got a raw "send the X-Aira-Candidate header" error: now an "Open a workspace first" page (own workspace / choose a candidate; billing: not part of the role) via `Aira.sectionGate`; the local Developers page no longer asks the hosted-only API (404) first. Earlier the same day: closed-app page, Business hub for candidates, hosted Leads without Sales Navigator, sidebar redraw that could swallow a click, `test_suite_life` date taken in the account's time zone (failed every evening).
- Full suite: 2517 passed, 28 skipped, 8 failed: the 2 Linux-only container tests, and 6 that failed with "malloc failure" / browser start failures because the machine ran out of memory (C: full, page file cannot grow); all 6 pass when rerun.

## Life and AI apps, from MONOSAGE /ai and /life (2026-10-26)
Request: implement the applications listed at http://localhost:3000/ai and /life (the MONOSAGE Next.js app in
`D:\monosage-omnilife\monosage\apps\web\app\(dashboard)`: AI Chat, the Agent Control Panel; Life dashboard, Goals,
Life Timeline, Document Vault) in Aira. Built as two everyday apps (catalog `LIFE_APPS`, packages, hosted gating,
pricing and policy rules all derived): docs `docs/apps/journey.md`, `docs/apps/ai.md`.
- **Life** (`journey`; `/api/life` is the life core): `aira/suite/journey/` (`jr_goals`, `jr_steps`, `jr_checkins`,
  `jr_events`, `jr_documents`), `views-journey.js`. Goals with steps (progress follows the steps) or check-ins, states
  on track / behind / overdue, completing adds "Reached a goal" to the timeline once, CSV; timeline by year, private or
  shared with the household (read-only to others); vault with document number and scan (PDF/photo/text by magic bytes,
  10 MB) Fernet-encrypted via `request.secret_store`, expiry states, download, image preview; overview with life score
  and coming up; Today card and pulse reminders (30/7/1/0 days). Owner-only everywhere.
- **AI** (`ai`): `aira/suite/ai/` (`agents.py` registry, `service.py` chat + consent gate, `ai_routes.py`),
  `views-ai.js`. Agents per app: LifeAgent and PlannerAgent read and act (goal, progress, timeline event, task,
  shopping items, note); Health, Family, Social, Learn, Play, News, Videos read their Today cards; BusinessAgent reads
  revenue and hub summaries (adults locally; owner/admin/coach/auditor hosted; only with a business app). Consent levels
  read_only / auto_minor / auto_major / manual_approval per person and agent, approvals (also on Today, badge),
  activity log + CSV, sessions with history. Chat: plain commands parsed without the model (instant); otherwise the
  writing model (`kit.draft`, JSON `{reply, actions}`) over the agents' facts, built-in answers without a model;
  voice input via the browser's speech recognition.
- Fixed on the way: `Aira.fmt.date` showed a calendar day ("2026-11-20") as the day before west of UTC (now parsed as
  a local date); the local server left the body of a DELETE/GET unread, so the next request on the kept-alive
  connection failed ("{}GET" -> 501); now drained (`_discard_body`).
- i18n: 361 new strings in Telugu and Hindi (prompt/log fragments kept in English); `i18n_check` te/hi OK.
- Tests: `tests/test_suite_journey.py` 11, `tests/test_suite_ai.py` 12 (unit, local server, hosted 402/roles/isolation);
  app-list tests updated. Browser walk (real server, Ollama answering): 30/30, desktop and phone, no console errors.
- Not built: removing a member (hosted) leaves their Life/AI rows in the organization database, like Planner and
  Health; Health actions (mark a dose taken) are not agent actions yet; agent replies are in English (the UI is
  translated); token counts are estimates (the gateway returns only the JSON).

## Stocks, and one agent per app (2026-10-27)
Request: a Stocks app (each stock's log of highs and lows, top gainers/losers, predictions, a way to earn), every
app working end to end with one account, tests per app, more features where they help, one agent per app.
- **Stocks** (`stocks`, `aira/suite/stocks/`, `stk_` tables, `views-stocks.js`, `public-stocks.js`,
  `docs/apps/stocks.md`): Yahoo Finance chart provider (no key, cached) and a seeded offline provider
  (`AIRA_MARKET_PROVIDER=stub`, forced in tests by `tests/conftest.py`); US and NSE universes plus indices; markets
  (gainers, losers, most traded, 52-week highs/lows); each stock's chart and daily log with best/worst days and CSV;
  indicators, 5/20-day forecasts with an 80% band and a walk-forward track record against a no-change baseline,
  personal predictions scored later; practice portfolios (children too) and real holdings (adults) with P&L;
  watchlists, alerts (background + pulse + Today); earning: ideas with an automatic track record, a public analyst
  page with paid subscriptions (payments the analyst confirms, access codes, revenue ledger), a practice league;
  StocksAgent registered with AI via `agents.register` (new hook). Never executes trades; not financial advice.
- **Workflow (54 agents):** per app, an engineer (fix + 2-5 features, often an earning feature), an independent
  tester, and a fixer. Every app's feature list is in its `docs/apps/<id>.md`; per-app test files `tests/test_suite_<id>*`.
  Each app also has `auto_apply_assistant/app-<id>.css` (loaded on the app and public pages).
- **Shared fixes applied from the agents' requests:** `public.set_default_index` no longer replaces the hosted
  organization index (every hosted public link answered 404 on `aira_cloud serve`); `kit.cents` refuses Infinity/NaN/
  huge values (were 500s); `kit.record_revenue` moves a re-recorded or un-voided payment to its date; `kit.draft`
  says "model" only when the model gave every key; Business hub routes refuse a child's/teen's profile
  (`life.require_grown_up`, `GrownUpsOnly` code `adult_only`) and list everyday apps that earn (News, Learn, Stocks);
  personal public pages can drop the business block (`"brand": False`, `"business_payment": False`: Social profile/
  listing, Family board, Life goal, Stocks analyst, Learn deck payment); `public.html` loads `public-journey.js`;
  public notices replace each other, `ctx.button(..., {submit: true})`, language button in the flow on phones;
  `S.edit` submits on Enter, `S.table` opens a row only on the row's own Enter, `S.bars` scales small values, money
  typos are refused in `S.form`; `S.live` stops when the page redraws (`Router.renders`) or its node leaves;
  `aira:life-action` event after an alert action; tabs keep the open tab in sight on phones; a link to the current
  address redraws; `.suite-body` children can shrink (no sideways page scroll); toasts never cover dialog buttons;
  hub shows 4 summary cards; Videos uploader stops retrying explained 400s; `Aira.fmt.date` reads calendar days as
  local dates (earlier); local server drains unread request bodies (earlier).
- **Not applied (noted):** hosted policy giving `life.manage` to auditors/billing on Planner money routes (apps can
  read `suite.actor()["role"]`); a schema migration for the Developers rank/earning tables (store.ensure_extras
  covers it); first-request "database schema has changed" retry in the shared connect (Social/Family retry
  themselves); skipping a hosted organization's app task when it lost the app; hiding business apps in a child's
  switcher (their routes and the hub refuse); Careers' profile header before GET /api/profile; Today cards for Careers.
- **i18n:** 4,002 new strings translated (12 translator agents) + merge; te/hi 16,236 strings OK.
- **Validation:** full suite 3,247 passed, 21 skipped; the 2 Linux-only container tests fail as always; 2 browser
  tests that my shared changes affected were fixed (Learn money message, Sales phone table). Real server walk with one
  account (`tester@aira.test`): 18 apps, 30 sections, 155 tabs, 0 script errors, 0 failed requests, 0 error boxes;
  the AI app lists 13 agents including StocksAgent; "Add Apple to my watchlist" through the model now resolves
  "Apple Inc." (fixed, regression test).

## Google-style shell (2026-10-27)
- **Ask:** every app should feel like Gmail/Drive. The user kept the old pale app icons (no per-app colors).
- **Frame (`auto_apply_assistant/shell.js`, Suite shell section of `app.css`):** the top bar spans the page with the
  app's logo and name, a pill search box (`/` focuses it) and the launcher; each app has its own address (`/stocks`,
  `/health`, `/home` for Today; `aira/apps.py` `APP_PATHS`, route `ui_app_page`, public in `aira_cloud/policy.py`);
  sign-in lands on Home (`/home#/today`), whose menu lists every app; "+ New" above the menu
  (`Aira.appNew[appId]`); search with suggestions and Enter results (`Aira.appSearch[appId]`, Home searches every
  app); a page's tabs show as menu folders (`Aira.shell.tabs`, used by `S.tabs`); only the open page's folders show;
  the favicon is drawn per app. Page routes are exempt from the API rate limit (a page loads ~90 files).
- **Workflow (18 agents, one per app):** each app registers its + New (opening its existing forms via `?new=<kind>`,
  removed from the address so Back/reload don't reopen), a search over its own records (new `/search` routes for
  several apps), folders with icons, polish for desktop/phone/dark, `tests/test_suite_<id>_shell_ui.py` and an
  "In the Aira frame" section in `docs/apps/<id>.md`.
- **Shared fixes from their requests:** Enter cancels pending suggestions; folder counts and per-item `href` (Health
  keeps `?person=`); a short search hint on phones (`placeholderShort`, else "Search <app>"); "+ New" re-reads its
  list on click; status pills hidden on phones; `ui_app_page` in the hosted policy and isolation sweep; the e2e
  walk's Upload click (patchright `.last` with one match never resolves).
- **Not applied:** hiding every page's own tab bar on desktop (about 30 browser tests click it; Sales, Social and
  Careers hide it themselves); shared `link/mail/history/image` icons (Marketing ships its own).

## Monosage name, account menu, sun/moon (2026-10-27, night)
- **Name:** the product is now called Monosage in everything people read: the logo (top bar "Monosage <app>", sign-in
  and menu brand "MONOSAGE" with an "M" mark), page titles, sentences, emails, server messages and the te/hi tables
  (keys and values renamed together). "Aira Jobs" is "Jobs", "Aira apps" is "Apps", the API console's address is
  `#/api` (`Router.parse` maps it to the `airaapi` view; old `#/airaapi` links are rewritten). The code name stays:
  `window.Aira`, `AIRA_*` settings, `aira_*` ids and error codes, `/api/airaapi/*`, the `aira` package, `X-Aira-*`
  headers, the database and its folder, so existing data, keys and scripts keep working. Saved records written
  before the rename (old run messages) keep their wording. Docs under `docs/` still say Aira.
- **Account menu (top right):** "Manage your account" opens Settings here (it used to open the online account at
  AIRA_ACCOUNT_URL); under it every Account page (Today, Display and reading, Business hub; hosted: Security, Activity,
  My workspace, Organization settings for staff). The left menu shows only the open app. The online account link
  stays in the launcher as "Your organization online".
- **Theme:** a sun/moon button in the top bar (moon in the light theme, sun in the dark one) replaces the "Theme" menu
  item; it follows the system theme until clicked.
- **Fixes after the rename (2026-10-28):** the hosted tab-title watcher (`cloud-boot.js`) still replaced `/· Aira$/`
  while checking for "· Monosage", so it rewrote the title forever and froze every hosted member page (the hung
  browser tests). The renamer had also capitalised four route ids (`"apps"` → `"Apps"` in cloud-org.js,
  cloud-platform.js, views-developers.js), which broke the org and console Apps pages; both are fixed, with the
  tests that the renamer had changed. Server-made links now say `#/api`. Videos has a phone placeholder
  ("Search"). An AI routine added after its time today first runs the next day (`ai_routines.starts_on`), so
  saving one no longer pops its answer over the page at once. Full suite: 3309 passed; the 16 that failed under load
  or after the changes were fixed and pass (91 passed in the re-run).

## Careers navigation, in the order of a job search (2026-10-28)
- **Menu (`NAV` in app.js):** Home (Dashboard) · Find (Jobs, Research) · Apply (Auto-apply, Needs you, Applications)
  · Follow up (Interviews, Messages & recruiters, Email updates, Offers) · Your materials (Profile & resume,
  Documents) · Insights (Analytics). The group names are the menu headings. New sections `autopilot`, `needs`,
  `emails`, `offers`, `profile` (Careers `extra_sections`, hosted `WORKSPACE_IDS`); they have no `g` chord (every
  letter is already taken somewhere). Phone bar: Dashboard, Jobs, Needs you, Applications, More.
- **Jobs:** three folders (All jobs, Recommended, Saved). Where a job comes from is a chip row on All jobs
  (`#/jobs/all?from=linkedin|aira|google|portals`): Every source, LinkedIn, Free job boards, Google Jobs, Company
  portals; the chip narrows both the search form's sources and the list. The free boards source (`aira_jobs`) is
  "Free job boards" everywhere (source catalog, chip, hint, badges, Auto-apply plan editor, Research's provider
  switch, billing); the renamer had left it as a bare "Jobs". Research's own search tab is "Advanced search".
- **Needs you (`#/needs`):** Auto-apply's held runs (questions with the AI's drafts, filled forms waiting for a
  click; `renderAutopilot(host, { part: "needs" })`) above the review queue (Ready to apply, Needs your review, In
  progress), a job held by Auto-apply listed once. Count badge on the menu item and a dot on the phone bar: held runs
  plus queue items, by job (`Aira.careersNeeds`, refreshed on every Careers page, at most every 30 s, and at once on
  Needs you). Auto-apply (`#/autopilot`) keeps status, readiness, plans and activity and links to Needs you.
- **Applications** is the pipeline (with the summary); **Offers** (`#/offers`) and **Email updates** (`#/emails`)
  are pages of their own. **Profile & resume** (`#/profile?tab=profile|resume|answers`) draws Settings' Profile,
  Resume and Standard answers inside Careers; Careers' links and the Auto-apply readiness actions point there.
- **Old links redirect** (`Aira.redirects`, run by the router before a page is drawn, address replaced):
  `#/jobs/linkedin|aira|google|portals` and `#/jobs?tab=<source>` → `#/jobs/all?from=<source>` (job paths kept);
  `#/jobs/autopilot`, `#/jobs?tab=autopilot` → `#/autopilot`; `#/applications?tab=queue|offers|email` → `#/needs`,
  `#/offers`, `#/emails`. In-app links, Auto-apply notifications (`#/needs`, `#/applications`) and Analytics tiles
  use the new places.
- **Tests:** `test_suite_careers_shell_ui.py` (menu groups and order, redirects, source chips, Saved, Profile &
  resume folders, Needs you page with the menu count and phone dot), `test_app_switcher_ui.py`,
  `test_integration_shell.py` updated. te/hi translations for the 32 new strings (`i18n_check` clean for both).
