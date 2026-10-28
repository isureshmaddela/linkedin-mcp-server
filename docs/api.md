# API Reference

Base URL: `http://127.0.0.1:8765` (loopback only). All responses are JSON unless noted. Errors use the envelope `{"error": "plain-language message"}`. Every response carries an `X-Request-ID` correlation header; send your own `X-Request-ID` (4–64 `[A-Za-z0-9-]`) to propagate one.

## Conventions

- **Authentication**: cookie session (`apply_desk_session`, HttpOnly, SameSite=Strict) issued by `/api/auth/login` or `/api/auth/register`. Unauthenticated calls to `/api/*` return `401`.
- **CSRF**: state-changing methods must be same-origin (`Origin`/`Sec-Fetch-Site` checked). The dashboard also sends `X-Requested-With: aira`.
- **Rate limits**: 600 requests/minute per client; 8 failed sign-ins per 10 minutes then a 15-minute lockout (`429`).
- **Pagination**: list endpoints accept `page`, `page_size` (≤200) and return `total`, `page`, `pages`.
- **Idempotency**: job imports are idempotent by `(source, external_id)`; submissions by `idempotency_key` (default `job:<job_id>`); email imports by message fingerprint.
- **Job keys**: LinkedIn jobs use the numeric ID; other sources use `source:external_id` (URL-encode the colon in browsers).
- **Status codes**: `400` validation, `401` sign-in required, `402` SerpApi account out of searches, `403` forbidden/cross-origin, `404` not found, `409` conflict or SerpApi not configured, `422` SerpApi rejected the key, `429` rate limited, `502` upstream provider failure, `503` not ready. Errors from the API routes also carry a machine-readable `code`.

## Health and observability

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/health` | no | Liveness. |
| GET | `/api/ready` | no | Readiness: accounts DB, data directory writable, Ollama (optional). `503` when degraded. |
| GET | `/api/metrics` | yes | Request counts, status codes, per-route latency (avg/p95), counters, integration health. |
| GET | `/api/audit?limit=&offset=&target_type=&target_id=&action=` | yes | Append-only audit log. |
| GET | `/api/notifications?unread=1` / POST `/api/notifications/read` | yes | In-app notifications. |

## Accounts

| Method | Path | Body | Notes |
|---|---|---|---|
| POST | `/api/auth/register` | `{email, password}` | Password ≥ 12 chars. Creates an isolated data store. |
| POST | `/api/auth/login` | `{email, password}` | Sets the session cookie. |
| POST | `/api/auth/logout` | `{}` | Clears the session. |
| GET | `/api/auth/me` | | Current account. |
| GET | `/api/account/export` | | Downloads a JSON export of every table. |
| POST | `/api/account/delete` | `{password}` | Deletes the account, databases, documents, and LinkedIn profile. |

## Profile and answers

| Method | Path | Body / Query | Notes |
|---|---|---|---|
| GET | `/api/profile` | | Resume text, legacy `application_profile`, `candidate_profile` (universal profile), `completeness`, `profile_fields`, matching preferences. |
| POST | `/api/profile` | `{resume, profile_url, resume_name, application_profile, matching_preferences}` | `application_profile` may contain any universal field; omit it to keep existing values. Triggers background rescoring. |
| POST | `/api/resume/extract` | `{filename, content_base64}` | PDF/DOCX/TXT/MD → text (≤700 KB). |
| GET | `/api/profile/answers` | | Standard answers plus canonical field definitions (sensitivity, confirmation). |
| POST | `/api/profile/answers` | `{canonical_field, value, confirmed, sensitivity}` | Sensitive answers are used only when `confirmed`. |
| DELETE | `/api/profile/answers/{field}` | | |

## Jobs

| Method | Path | Body / Query | Notes |
|---|---|---|---|
| GET | `/api/jobs` | `view=all|linkedin|google_jobs|portals|recommended|saved`, `source`, `stage`, `classification`, `min_score`, `q`, `sort=updated|score|posted|company|title|stage`, `workplace_type`, `page`, `page_size`, `archived=1` | Paginated list with facets. Without query parameters returns the legacy `{jobs: [...]}`. |
| POST | `/api/jobs/search` | `{source, identifier, keywords, location, max_pages, work_type, job_type, w2_search}` | Unified search. `identifier` is the board token/site/company for ATS sources. Records a sync run. |
| POST | `/api/jobs/import` | `{url}` | Import one posting from LinkedIn, Greenhouse, Lever, Ashby, or SmartRecruiters. |
| POST | `/api/jobs/rescore` | `{}` | Rescore up to 400 jobs with descriptions. |
| GET | `/api/sources` | | Source catalog with status, access method, capabilities, connection hints, last run. |
| GET | `/api/jobs/{id}` | | Full detail: source records, score explanation, events, interviews, tasks, documents, preparation, submission, recruiters, allowed stages. |
| GET | `/api/jobs/{id}/events` | | Status history. |
| POST | `/api/jobs/{id}/details` | `{}` | Read the posting from LinkedIn (LinkedIn jobs only) and score. |
| POST | `/api/jobs/{id}/score` | `{}` | Force rescore. |
| POST | `/api/jobs/{id}/evaluate` | `{model}` | Local Ollama fit review. |
| POST | `/api/jobs/{id}/generate` | `{model, notes?}` | Tailored resume + cover letter drafts (no fabrication prompt). With `notes` (the person's own guidance, at most 2000 characters) the drafts are also kept as versions that still need approving. |
| POST | `/api/jobs/{id}/save` | `{saved}` | Star/unstar; moves discovered ⇄ saved. |
| POST | `/api/jobs/{id}/archive` | `{}` | Soft delete. |
| PATCH | `/api/jobs/{id}/stage` | `{stage, reason}` | Guarded transition; records an event and audit row. |
| PATCH | `/api/jobs/{id}/documents` | `{resume, cover_letter}` | Saves drafts and stamps the current resume version. |
| PATCH | `/api/jobs/{id}/notes` | `{notes, follow_up_at}` | Follow-up creates a task. |
| POST | `/api/jobs/{id}/documents/snapshot` | `{}` | Store drafts as versioned encrypted documents. |
| GET | `/api/jobs/{id}/documents/versions` | | Resume editor history: versions of the tailored resume and cover letter, newest first, with `approved` and what applications send now (`in_use`). |
| POST | `/api/jobs/{id}/documents/versions` | `{resume, cover_letter, approve}` | Saves the texts as the drafts and as new versions when they changed; `approve: true` ("I reviewed this") makes them the approved versions that applications upload. |
| GET | `/api/jobs/{id}/documents/versions/{document_id}` | | One version's text. |
| POST | `/api/search`, `/api/search/greenhouse`, `/api/search/google` | | Legacy single-source search routes (kept for compatibility). |

## Applications

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/applications/queue` | | Ready, needs-review, in-progress lists; daily budget; auto-submit policy. |
| GET | `/api/applications/summary` | `?bucket=submitted\|in_process\|needs_you\|failed\|interviews&via=manual\|auto` | Every application counted once (manual and automatic): `total`, `counts`, `by_via`; with a filter (or `list=1`) also `items`. Definitions in `aira/apply_summary.py`. |
| POST | `/api/jobs/{id}/prepare` | `{}` | Build the packet: maps portal questions (Greenhouse) or standard questions to the profile, prefills non-sensitive confirmed values, flags sensitive/unknown/missing. |
| GET | `/api/jobs/{id}/preparation` | | Latest packet. |
| PATCH | `/api/preparations/{prep_id}` | `{answers: {field_id_or_label: value}, notes, remember_answers}` | User review; confirmed answers can be remembered as standard answers. |
| POST | `/api/preparations/{prep_id}/approve` | `{}` | Requires documents, no required gaps, no pending sensitive answers; moves the job to Ready to Apply. |
| POST | `/api/jobs/{id}/apply` | `{}` | Checks the daily limit and approval, moves to Applying, returns the application URL to open. |
| POST | `/api/jobs/{id}/submit` | `{confirmation_number, confirmation_text, evidence_base64, evidence_filename, submitted_at, idempotency_key}` | Records the user's submission with encrypted evidence; duplicate-safe; moves to Applied; creates a follow-up task. |

Status model: `discovered → saved → ready_to_apply → applying → needs_review → applied → assessment → recruiter_screen → technical_interview → manager_interview → panel_interview → final_interview → offer → accepted | rejected | withdrawn | failed` with the transition table in `auto_apply_assistant.APPLICATION_TRANSITIONS`.

## Email intelligence

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/email?status=&limit=` | | Classified messages with details, signals, suggested action. |
| POST | `/api/email/import` | `{subject, body, sender, sender_name, received_at, job_id}` or `{eml_base64, filename}` | Classifies, extracts explicit facts, links to a job by company/role, suggests an action. Auto-applies only when enabled, above threshold, and non-sensitive. |
| POST | `/api/email/classify` | `{subject, body, sender}` | Dry-run classification. |
| POST | `/api/email/{id}/apply` | `{confirm: true, job_id?, action_kind?, stage?}` | Creates an interview / assessment task / recruiter, or updates the status. |
| PATCH | `/api/email/{id}` | `{category?, job_id?, status: "dismissed"?}` | User correction. |
| DELETE | `/api/email/{id}` | | |

## Interviews and calendar

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/interviews?scope=upcoming|past|all&job_id=` | | |
| POST | `/api/interviews` | `{job_id, company, role, interview_type, start_at, end_at, timezone, meeting_link, location, hiring_manager, participants[], preparation, notes, feedback, action_items[], follow_up_at, recruiter_id, update_stage}` | Creates prep and follow-up tasks; duplicate-time guard. |
| GET | `/api/interviews/{id}` | | Interview plus `calendar` (ICS text, Google/Outlook/Office 365 links, UID, sequence). |
| PATCH | `/api/interviews/{id}` | partial fields | Time/location changes bump the calendar `SEQUENCE`; `status: cancelled` emits `METHOD:CANCEL`. |
| DELETE | `/api/interviews/{id}` | | |
| GET | `/api/interviews/{id}/calendar.ics` | | `text/calendar` download. |
| GET | `/api/interviews/calendar.ics` | | All upcoming interviews. |
| GET/POST/PATCH/DELETE | `/api/tasks`, `/api/tasks/{id}` | `{title, kind, due_at, job_id, interview_id, recruiter_id}` / `{status}` | Follow-ups, prep reminders, assessment deadlines. |

## Recruiters

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/recruiters?q=&company=` | | Contacts with interaction counts and next follow-up; upcoming follow-ups. |
| POST | `/api/recruiters` | `{name, title, company, email, phone, linkedin_url, notes, consent_note, merge}` | Conservative dedupe on email, LinkedIn URL, or name+company. |
| GET/PATCH/DELETE | `/api/recruiters/{id}` | | Detail includes interactions, related jobs, interviews. |
| POST | `/api/recruiters/{id}/interactions` | `{kind, direction, summary, occurred_at, follow_up_at, job_id}` | |
| DELETE | `/api/interactions/{id}` | | |

## Documents

| Method | Path | Body / Query | Notes |
|---|---|---|---|
| GET | `/api/documents?kind=&job_id=&deleted=1` | | Metadata list, usage, kinds, limits, retention. |
| POST | `/api/documents` | `{filename, content_base64, kind, title, job_id, metadata, parent_document_id}` | Validates type/size/content, scans (hook), encrypts at rest. |
| GET | `/api/documents/{id}/download` | | Decrypts, verifies SHA-256, audits the download. |
| GET | `/api/documents/{id}/versions` | | Version chain and audit trail. |
| DELETE | `/api/documents/{id}` | | Soft delete; blob removed. |

## Analytics, dashboard, settings

| Method | Path | Query / Body | Notes |
|---|---|---|---|
| GET | `/api/dashboard` | | Counts, queue budget, upcoming interviews, tasks, notifications, recent jobs, setup status. |
| GET | `/api/analytics` | `days`, `start`, `end`, `source`, `stage` | Metrics with definitions, pipeline, by-source conversion, top companies, resume-version and cover-letter performance, drill-down IDs, timeline. Rates are `null` when the denominator is zero. |
| GET | `/api/settings` | | Settings, auto-submit policy, migration ledger. |
| POST | `/api/settings` | `{daily_application_limit, retention_days, default_model, email_auto_apply, email_confidence_threshold, timezone}` | `auto_submit_enabled` is rejected while no source has an approved submission adapter. |

## LinkedIn replies

| Method | Path | Body | Notes |
|---|---|---|---|
| GET | `/api/inbox` | `q` (names and message text), `filter=all\|needs_reply\|recruiters\|auto\|unread`, `limit` (1-500, default 100) | Cache first: answers from Aira's saved copy and never waits on LinkedIn. `{conversations[], total, saved_total, counts{all,needs_reply,recruiters,auto,unread}, filter, q, message, synced_at, stale, syncing, sync{}, linkedin_connected, linkedin_state}`. Each conversation: `thread_id, participant, snippet, last_message_label, last_message_at, last_sender (them\|us\|unknown), needs_reply, unread, message_count, has_messages, is_recruiter, synced_at, mode, auto, mode_set_by_user, recruiter_id, recruiter_name, last_status`. `synced_at` is epoch seconds and counts only lists and sync runs that succeeded; `stale` is true after 10 minutes. A row whose list preview could not be read keeps its earlier preview and reply flag, and falls back to the newest saved message. |
| GET | `/api/inbox/sync` | | Sync status: `{running, status: idle\|running\|ok\|partial\|error\|interrupted, phase: listing\|threads\|done, trigger, run_id, total, done, failed, started_at, finished_at, error}`. |
| POST | `/api/inbox/sync` | `{trigger: manual\|tab\|schedule, only_if_stale, force}` | Starts a background sync: reads the inbox list (20 conversations, as before). With `trigger: tab` (what the Messages tab sends by itself) it stops there and opens no conversation, so nothing is marked read on LinkedIn. Other triggers then read the full history of the 10 newest threads one at a time (LinkedIn marks those read). Threads whose preview did not change since their last fetch, and whose list row was readable, are skipped unless `force`. Single flight per account, and it shares the automation slot with inbox checks. Repeated requests within 10 seconds are refused unless `force`. `202` when started; `200 {started: false, reason: not_connected\|already_running\|fresh\|recent_attempt\|busy}` otherwise. Always returns `message`, `linkedin_connected`, and `sync`. |
| DELETE | `/api/inbox/cache` | | Deletes Aira's saved copy of the inbox (threads, messages, sync history). Refused while a sync runs. Returns `{cleared, threads, messages}`. Nothing is deleted on LinkedIn. |
| GET | `/api/conversations/{thread}` | `refresh=1`, `auto=1` | Cache first: saved messages return at once. LinkedIn is read only with `refresh=1` or when nothing is saved and LinkedIn is connected. With `auto=1` (the refresh the page makes by itself when a thread is opened) LinkedIn is read only if the copy is really out of date; a copy the latest inbox list confirmed is served as is. Returns `thread_id, saved, conversation, messages[{sender, own, date, sent_on, time, text, source: linkedin\|aira}], speaker, effective_mode, mode_reason, participant, draft, policy, sends, needs_reply, message_count, synced_at, confirmed_at, stale, source: cache\|live, linkedin_connected, note, live_error`. Never 404s for a missing copy: with nothing saved and LinkedIn not connected it returns an empty `messages` list and a `note`. A failed live read falls back to the saved copy and sets `live_error`. Opening a thread marks it read in Aira only. |
| POST | `/api/conversations/{thread}/send` | `{reply, participant, confirm: true, seen_messages?}` | Sends in-thread through the LinkedIn worker. Rejects without `confirm`, placeholders, or the same reply twice in 24 hours. When `seen_messages` is given and Aira has saved more messages since, it answers 400 so the reply is written against the latest thread. Logs to the recruiter when linked. |
| PATCH | `/api/conversations/{thread}/policy` | `{mode: "manual" or "auto", participant}` | Returns the policy and whether inbox checks are scheduled. |
| POST | `/api/conversations/{thread}/recruiter` | `{participant, company}` | Saves the participant as a recruiter (deduplicated) and links the thread. |
| GET | `/api/messages/policies` | | All conversation reply modes. |
| GET | `/api/messages/auto-reply` | | Paused flag, daily limit, sent today, auto conversations, inbox schedule, held replies, recent sends. |
| POST | `/api/messages/auto-reply/run` | `{}` | Runs one inbox check now in the background. |
| POST | `/api/linkedin/restart` | `{}` | Restarts the account's LinkedIn worker from the saved sign-in. `POST /api/linkedin/connect {fresh_login: true}` opens a new sign-in. |

Settings keys: `daily_message_limit` (0–100, default 20) and `auto_reply_paused`.

Messages cache (migration 11): `inbox_threads`, `inbox_messages`, and `inbox_sync_runs` hold Aira's copy of the inbox. A reply sent from Aira is appended to the saved thread at once (`source: aira`) and dropped when a later fetch lists one more own message with the same text than the fetch before it (an older identical message does not count). While it is pending the thread stays "you wrote last". Idle threads are pruned after 90 days and unlisted replies after 48 hours. Migration 11 holds its own copy of the table definitions. The scheduled inbox check also saves the list and the threads it reads, so the Messages tab stays current without extra LinkedIn visits. Both tables are part of `GET /api/account/export`.

## API console and SerpApi-compatible `/v1`

Design contract: [airaapi-spec.md](airaapi-spec.md). The console wraps every SerpApi engine in `aira/airaapi/catalog/` (136 engines: 126 official plus 10 sub-endpoints). Searches, results, saved searches, monitors, usage and API keys are stored per account (migration 12). The SerpApi key is a secret: it comes from `SERPAPI_API_KEY` or is pasted once and stored Fernet-encrypted (`airaapi.key` beside the database, or `AIRA_SECRET_KEY`); an account's own key wins over the environment. It is never returned, logged, stored in a record or put in a URL the browser sees; responses expose only `configured`, `source` (`env` or `app`) and `last4`. A key SerpApi rejects answers `422` (never `401`, which the browser app treats as an ended session).

Session-authenticated routes (`/api/airaapi/*`):

| Method | Path | Body / Query | Notes |
|---|---|---|---|
| GET | `/api/airaapi/overview` | | `{configured, key{source,last4}, account, account_error, usage{today,last_7_days,last_30_days,by_day[],by_engine[],by_source[],cache_hit_rate,avg_ms,error_rate,credits_used_30d}, recent[], monitors{active,due_next}, saved, engines, categories[]}`. |
| GET | `/api/airaapi/account?refresh=1` | | Plan, searches left, hourly rate limit from `account.json` (free, cached 60 s). `409 not_configured` without a key. |
| POST / DELETE | `/api/airaapi/key` | `{api_key}` | POST validates against `account.json` before saving (the old key stays on failure). DELETE removes the account's key; the environment key cannot be removed (`409 conflict`). |
| GET | `/api/airaapi/status?refresh=1` | | `{local, serpapi{reachable,latency_ms,checked_at,error}, throttle, queue}`; the reachability probe is cached 30 s. |
| GET | `/api/airaapi/engines?category=&family=&q=` | | Engine summaries, categories, families. `GET /api/airaapi/engines/{id}` adds every parameter (types, enums, groups, help), result keys and code samples (`curl`, `python`, `javascript`, `ruby`, `php`, `go`, `java`, `csharp`, `aira_v1`, and a direct `serpapi` variant). |
| POST | `/api/airaapi/search` | `{engine, params{}, options{no_cache,output,async,zero_trace,json_restrictor,allow_unknown}, label, save_as}` | Validates, serves a fresh identical run from the local cache (`cache_hit: true`, `credits: 0`, no SerpApi call), otherwise calls SerpApi. Returns the record with `result`. Failures are recorded and answer with `code` and `record_id`. |
| GET | `/api/airaapi/searches` | `engine, status, source, q, pinned, cache, from, to, limit (1-200), offset` | History without result bodies. `GET /api/airaapi/searches/{id}?html=1` returns the record with `result` (a running async search is polled); `html=1` adds the stored page or fetches it from the free Search Archive. |
| PATCH / DELETE | `/api/airaapi/searches/{id}` | `{pinned, label}` | |
| POST | `/api/airaapi/searches/{id}/rerun` | `{options}` | Fresh by default; `options.use_cache` allows the cache. |
| GET | `/api/airaapi/searches/{id}/export?format=json\|csv\|html\|curl` | | Download. CSV flattens the main result list (formula-safe, capped at 60 columns); curl uses a `YOUR_SERPAPI_KEY` placeholder. |
| GET | `/api/airaapi/diff?a=&b=` | | `{added[], removed[], changed[{path,before,after}], counts, summary, list_key}` between two stored JSON searches. |
| DELETE | `/api/airaapi/searches?before=&engine=&all=1&include_pinned=1` | | Bulk delete; needs `before`, `engine` or `all=1`. Pinned records are kept unless asked. |
| GET | `/api/airaapi/locations?q=&limit=` | | Google locations (free, no key needed, cached 24 h, 2 to 120 characters). |
| GET / POST / PATCH / DELETE | `/api/airaapi/saved[/{id}]`, `POST /api/airaapi/saved/{id}/run` | `{name, engine, params, tags, pinned}` | Saved searches (up to 300). |
| GET / POST / PATCH / DELETE | `/api/airaapi/monitors[/{id}]`, `POST /api/airaapi/monitors/{id}/run` | `{saved_id, interval_minutes (>=15), notify_on_change, enabled}` | A monitor re-runs a saved search live on schedule, diffs it against the previous run and raises an in-app notification when the main result list changed. Each monitor reports `estimated_monthly_searches`. Up to 25. Runs from the existing scheduler loop; accounts without a key are skipped. |
| GET / POST / DELETE | `/api/airaapi/keys[/{id}]` | `{name, hourly_limit}` | API keys (`aira_` plus 40 characters). The secret is returned once by POST; only a SHA-256 hash is stored. DELETE revokes. |
| GET | `/api/airaapi/docs` | | Generated `/v1` reference, errors and code samples. |
| GET | `/api/airaapi/platform` | | Platform reference for the Docs and Account pages: plans, billing rules, SDKs, `search_metadata` fields, dashboard-page coverage. |

Local limits are settings (`POST /api/settings`): `airaapi_cache_ttl_minutes` (0 to 10,080, default 60; 0 turns the cache off), `airaapi_hourly_limit` (live searches per hour, 0 = off), `airaapi_retention_days` (1 to 3,650, default 30), `airaapi_max_records` (50 to 100,000, default 2,000) and `airaapi_result_cap_mb` (1 to 50, default 5; larger bodies are returned but not stored). Pinned records and the baselines of saved searches and monitors are never pruned. At most four searches run at once per process.

SerpApi-compatible endpoint, authenticated by an API key as `api_key=aira_...` or `Authorization: Bearer aira_...` (no cookies, no CORS headers; GET only). It answers on SerpApi's own paths at the root and on the same paths under `/v1`, so an SDK only needs its base URL pointed at Aira (`serpapi.http.HTTPClient.BASE_DOMAIN = "http://127.0.0.1:8765"` for the `serpapi` package, `GoogleSearch.BACKEND = ...` for `google-search-results`):

| Path | Notes |
|---|---|
| `/search.json` (aliases `/search`, `/search.html`) | SerpApi's query parameters; `engine` defaults to `google`. Same cache, history and usage as the console (`source: api`). Body is the SerpApi JSON plus `search_metadata.aira = {id, cache_hit}`; `json_endpoint`, `raw_html_file` and `serpapi_pagination` links point back at Aira. `output=html` or `md` returns the raw page. Unknown parameters are forwarded like SerpApi does; `allow_unknown=false` rejects them. |
| `/account.json` | The SerpApi account behind Aira (no key in the reply). |
| `/locations.json?q=&limit=` | JSON array. Needs no key (a presented key is still checked); cached and rate limited. |
| `/v1/searches.json`, `/searches/{id}.json\|.html` | Stored searches, by Aira id or SerpApi search id. An id that was deleted or pruned in the last 31 days answers `410 gone`, like SerpApi's expired archive. |
| `/v1/engines.json[?full=1]` | The catalog. |

Errors use SerpApi's `{"error": "..."}` plus `code`: `401 invalid_aira_key` (missing, unknown or revoked key), `400` bad parameters, `410 gone`, `429 rate_limited` (the key's hourly limit) or `429 local_rate_limit`, `409 not_configured`, `402 out_of_searches`, and `502` for upstream failures including a SerpApi key SerpApi rejects. Rejected keys are rate limited per client and never lock out valid keys. Every route in the app also refuses a non-loopback `Host` with 403 (DNS rebinding); `AIRA_ALLOWED_HOSTS` adds names.

## Leads (`/api/leads/*`)

Aira Leads ([leads.md](leads.md)) builds lead generation on the API layer and the person's own LinkedIn connection. Google searches are normal recorded API searches (history source `leads`) that use the same SerpApi key, cache window and limits; without a key they answer `409 not_configured` (`dry_run` still works). Sales Navigator reads the person's own signed-in LinkedIn, read only. Migration 18 adds `lead_lists`, `leads`, `lead_keys`, `lead_searches`, `lead_runs` and `lead_settings`; migration 19 adds `lead_messages` (the outbox), `lead_suppressions` and `lead_calls`. Email is sent only after a person approves each message, through the person's own mail server; Aira never dials a call and never sends a LinkedIn message.

| Method | Path | Body or query | Result |
| --- | --- | --- | --- |
| GET | `/api/leads/overview` | | `{lists[{id,name,lead_count}], counts{total,by_status,by_source,with_email,with_phone,enriched}, icp, icp_empty, searches[], runs[], sources[{id,label,available,needs,cost}], serpapi{source,last4}, linkedin_state, key_error}`. |
| GET / POST | `/api/leads/icp` | `{titles[], keywords[], industries[], locations[], exclude_keywords[], exclude_companies[], require[website\|phone\|email\|linkedin], min_rating}` | The ICP. A POST saves it and re-scores every lead: `{icp, rescored}`. |
| GET / POST | `/api/leads/lists` | `{name, description}` | Lists with `lead_count`. Up to 100; names are unique. |
| PATCH / DELETE | `/api/leads/lists/{id}` | `{name, description}` | Rename, or delete a list **and its leads** (`leads_removed`). |
| POST | `/api/leads/search` | `{source, dry_run, auto_add, list_id, search_id, ...form}` | One search on one source (below). Returns annotated leads and, for Google sources, `record_id`, `cache_hit`, `credits`. Nothing is saved unless `auto_add` is true. `dry_run` returns `{dry_run, engine, params, applied, warnings, credits, cache_hit}` and calls nothing. |
| GET / POST | `/api/leads/searches` | `{name, source, params, list_id}` | Saved searches (up to 50). |
| DELETE | `/api/leads/searches/{id}` | | |
| POST | `/api/leads/searches/{id}/run` | `{auto_add}` | Runs a saved search and adds the leads not already saved (default). |
| GET | `/api/leads/items` | `list_id, status, source, kind, q, min_score, has_email, tag, sort (updated\|score\|name\|company\|created), limit (<=500), offset` | `{leads[], total}`. |
| POST | `/api/leads/items` | `{leads[], list_id}` | Save leads from a search preview (the first list is used and created if none is named). Duplicates are skipped. `{created, duplicates, failed, errors[], leads[], list_id}`. `201`. |
| POST | `/api/leads/items/import-csv` | `{csv, list_id}` | Import CSV text with a header row (3 MB, 5,000 rows). `{created, duplicates, skipped[], rows}`. |
| POST | `/api/leads/items/bulk` | `{ids[], action (delete\|status\|move\|tag\|untag), value}` | `{changed}`. |
| GET / PATCH / DELETE | `/api/leads/items/{id}` | editable: name, first_name, last_name, title, company, industry, location, website, email, phone, linkedin_url, status, notes, tags | A lead. An edit that would duplicate another lead is refused. |
| POST | `/api/leads/items/{id}/enrich` | | Reads the lead's public website: `{lead}` with `enrichment{emails[{address,kind,same_domain,source_url}], phones[], socials, pages[], errors[], robots_blocked}`. `400` without a website, `502` when the site cannot be read safely. |
| POST | `/api/leads/items/{id}/signals` | `{topic, dry_run}` | Looks up recent Google News about the lead's company (1 search, 0 when cached) and keeps up to 3 headlines as `news` signals; re-scores the lead. `{lead, found, credits, cache_hit, record_id}`; `dry_run` returns `{credits, cache_hit, params}`. |
| GET | `/api/leads/services` | | The service presets: `{services[{id, label, requests[], roles[]}]}` (website, marketing, content, ads, social). |
| GET / POST | `/api/leads/outreach/settings` | `{from_name, from_email, reply_to, smtp_host, smtp_port, smtp_security (starttls\\|ssl\\|none), smtp_username, smtp_password (write-only), clear_password, postal_address, signature, offer, services[], calling_number, daily_cap (1-100), delay_seconds (>=2), allow_regulated_regions}` | Sender settings. The password is stored encrypted and never returned: `{settings, has_password, problems[], counts, limits}`. `problems` lists what is missing before a message can be sent. |
| POST | `/api/leads/outreach/test` | | Sends one test message to the from address. `502` with the mail server's reason on failure. |
| GET | `/api/leads/outbox` | `status (draft\\|approved\\|sent\\|failed\\|rejected\\|skipped), lead_id, limit` | `{messages[{id, lead_id, lead_name, to_address, subject, body, status, error, source (model\\|template\\|edited), approved_at, sent_at}], counts{..., sent_today}}`. |
| POST | `/api/leads/outbox/drafts` | `{lead_ids[], offer, tone, services[], use_model}` | Writes one draft per lead that has an email and may be contacted. `{created, messages[], skipped[{lead_id, name, reason}]}`. Nothing is sent. |
| PATCH / DELETE | `/api/leads/outbox/{id}` | `{subject, body}` | Edit (withdraws an approval) or delete an unsent message. |
| POST | `/api/leads/outbox/approve` / `reject` | `{ids[]}` | Only approved messages are ever sent. |
| POST | `/api/leads/outbox/send` | `limit (<=10)` | Sends approved messages one at a time with a pause, up to the daily cap, re-checking the do-not-contact list, the lead's status and the region guard for each. `{sent, failed, skipped[], remaining_today, daily_cap, details[], note}`. `400` until the sender settings are complete. |
| GET / POST | `/api/leads/suppressions` | `{value, reason}` | The do-not-contact list: an email, a domain or a phone number. |
| POST | `/api/leads/suppressions/remove` | `{key}` | |
| GET | `/api/leads/items/{id}/dial` | | What the person needs to place the call themselves: `{dial{number, e164, tel_url, google_voice_url, calling_from, blocked, window{known, ok, local_time, note}, reminders[]}}`. Aira never dials. |
| POST | `/api/leads/items/{id}/call-script` | `{offer, services[], use_model}` | A talk track: `{script{opener, pitch, questions[], objections[{objection, response}], close, voicemail, source}}`. |
| GET / POST | `/api/leads/items/{id}/calls` | `{outcome (no_answer\\|voicemail\\|spoke\\|interested\\|callback\\|not_interested\\|wrong_number\\|do_not_call), notes, follow_up_days}` | The call log. A POST moves the lead along, clears a wrong number, and adds a "do not call" lead to the do-not-contact list. |
| GET | `/api/leads/follow-ups` | | Leads whose latest logged call asked for a follow-up that has come due. |
| POST | `/api/leads/items/{id}/draft` | `{offer, tone (friendly\|direct\|formal), channel (email\|linkedin), sender, use_model}` | `{draft{subject, body, source (model\|template)}}`. Never sent. |
| GET | `/api/leads/export` | `format=csv\|xlsx` plus the list filters | A download. |

Search sources and their forms:

| `source` | Form | Notes |
| --- | --- | --- |
| `google_maps` | `{query, location, ll, start (0-100 step 20)}` | Businesses with phone and website. `next_start` pages on. |
| `google_web` | `{mode (people\|companies\|custom), titles[], keywords, company, location, query, site, start}` | `people` builds `site:linkedin.com/in ("Title" OR ...) ...`; results that are not a person or company page are counted in `skipped`. |
| `google_jobs` | `{query, location, next_page_token}` | Companies hiring for the role, with the postings as `signals`. |
| `google_intent` | `{services[] (website\|marketing\|content\|ads\|social), where[] (reddit\|quora\|linkedin\|web), freshness (week\|month\|year), location, start}` | Public posts asking for the services. One `request` lead per post, with what it says, budget/urgent/age notes and the link. Never replied to by Aira. |
| `sales_navigator` | `{url \| keywords + location, max_pages (1-5)}` | `url` must be a Sales Navigator people search or lead list address. `403` when LinkedIn is not connected, `502` with LinkedIn's own reason otherwise. |

## Careers (`/api/careers/*`)

Design contract: [airaapi-spec.md](airaapi-spec.md) sections 6 and 10. Careers builds job search, reports and alerts on the API layer: every engine call is a normal recorded search (history source `careers` for job searches, `intel` for report sections) that uses the same SerpApi key, cache window and local limits. Without a key the routes answer `409 not_configured` (cache hits and `dry_run` still work). Migration 13 adds `careers_alerts`, `careers_alert_jobs` and `careers_reports`.

| Method | Path | Body or query | Notes |
|---|---|---|---|
| GET | `/api/careers/overview` | | `{configured, key{source,last4}, saved_searches[], alerts[], recent_intel[], playbooks[{id,title,description,engines[],sections[],max_credits}], usage_30d{jobs,reports}}`. |
| POST | `/api/careers/jobs/search` | `{q, location, gl, hl, remote, posted_within (today\|3days\|week\|month), employment_type (full_time\|part_time\|contract\|internship), lrad, chips[], ltype, uds, next_page_token, save_as, dry_run}` | One Google Jobs page (10 results, 1 credit, 0 when cached). Returns `{jobs[], next_page_token, filters[{name, parameter, options[{text,value}]}], record_id, cache_hit, credits, params, applied[], warnings[], note}`. `remote` becomes `ltype=1`, `posted_within` and `employment_type` become `chips`; Google has deprecated both, which is stated in `warnings`. The `filters` Google returns (`uds` tokens) are the supported refinement: send an option's `value` back as `uds` (or `chips` when `parameter` is `chips`). `dry_run: true` returns `{credits, cache_hit, params}` without calling SerpApi. |
| POST | `/api/careers/jobs/import` | `{job}` (a Job from a search) | Rebuilds the posting from Google's fields, never trusts client flags, and saves it through the normal import and scoring path with source `google_jobs`. Returns `{job, created, duplicate_of, matched_by}`; a posting already known (same Google record, same posting URL under any apply link, or same content) is merged, never duplicated. `201` when created, `200` otherwise. |
| POST | `/api/careers/company-intel` `/salary` `/trends` `/interview-prep` `/travel` `/learn` | see below | A report: 1 to 5 engine calls, at most 4 at once, partial failure tolerated. `{id, kind, title, generated_at, input, sections[{id, title, engine, params, status (ok\|empty\|error), summary, items[{title, subtitle, url, thumbnail, meta}], record_id, cache_hit, credits, error, code, stats}], credits, cache_hits, errors}`. `dry_run: true` returns `{plan[{id,title,engine,params,cache_hit,credits}], credits, max_credits, cache_hits, configured}` and calls nothing. `sections: [ids]` runs a subset. If nothing succeeds because of the key (not configured, rejected, out of searches) that error is returned instead of an empty report. |
| GET / DELETE | `/api/careers/reports[?kind=&limit=]`, `/api/careers/reports/{id}` | | The last 100 reports are kept. |
| GET / POST | `/api/careers/alerts` | `{q, location, ..., name, interval_minutes (>=60, default 720), auto_import, min_score (0-100, default 75), notify}` | A job alert is a saved Google Jobs search (tags `careers`, `alert`) plus an API monitor. Up to 10. The response carries `estimated_monthly_searches`. |
| GET / PATCH / DELETE | `/api/careers/alerts/{id}` | `{name, interval_minutes, enabled, auto_import, min_score, notify}` | GET adds `new_jobs[]` (Jobs with `found_at`, `record_id`, `match`, `imported_job_id`); `?all=1` includes dismissed ones. DELETE removes the monitor and saved search too. |
| POST | `/api/careers/alerts/{id}/run`, `/dismiss` | dismiss: `{job_keys[]}` or `{all: true}` | Run costs one search and never uses the cache. |

Report inputs: `company-intel {company, role?, location?}` (overview, news, open roles, employee threads, offices; 5 credits), `salary {role, location?, company?}` (estimates, pay in listings, pay at the company, community; 3 to 4), `trends {terms[1-5], geo?, window? (7d\|30d\|90d\|12m\|5y\|all)}` (interest over time, related queries, related topics, news; 4), `interview-prep {company, role}` (questions, candidate experiences, videos, news, postings; 5), `travel {kind: flights\|hotels, ...engine parameters}` (1; flights need `departure_id`, `arrival_id`, `outbound_date`, hotels need `q`, `check_in_date`, `check_out_date`; `type` defaults to round trip when `return_date` is given), `learn {skill, level?}` (courses, videos, jobs asking for it, community advice; 4). Identical sections are shared across reports through the cache (company-intel and interview-prep share the news section).

Job alert behaviour: the first run records what is currently posted (a baseline, no notification); later runs list only jobs not seen before, raise one in-app notification linking to `#/careers?tab=alerts&alert={id}` and, if `auto_import` is on, import new jobs whose match score reaches `min_score`. A job that cannot be scored (no resume, no description) is listed but never imported. Alerts never apply to a job. They run from the same scheduler loop as monitors and are skipped for accounts without a SerpApi key.

## Automation and LinkedIn messaging (existing features)

`GET /api/automation`, `POST /api/schedules`, `POST /api/schedules/{id}/run`, `PATCH /api/schedules/{id}`, `DELETE /api/schedules/{id}`, `GET /api/activity`, `POST|PATCH /api/conversations/{thread}/draft`, `GET /api/linkedin/status`, `POST /api/linkedin/connect`, `GET /api/status`.
