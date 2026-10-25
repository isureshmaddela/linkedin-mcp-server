# Integrations

Every provider sits behind an adapter that declares its capabilities. The catalog is served at `GET /api/sources` and shown under Settings → Integrations & sources. Status is **available** only when the integration is implemented and verified; **planned** entries carry the reason.

## Job sources

| Source | Status | Access method | Identifier | Capabilities | Notes |
|---|---|---|---|---|---|
| LinkedIn | Available | Existing MCP server over the user's own browser session (one isolated worker per Aira account) | — | search, normalize | Filters: remote/hybrid/on-site, employment type, optional W-2 wording. Job details read on demand; Easy Apply detected from posting text. No application submission tool. Messaging: inbox, conversation reads, and `reply_to_conversation` (new in-thread reply tool that works for recruiter InMail) used by manual Send and per-conversation Auto-reply. A crashed worker restarts from the saved sign-in (at most once per minute); Settings offers Restart and Sign in again. |
| Google Jobs | Available (key-gated) | SerpApi `google_jobs` JSON engine | — (`SERPAPI_API_KEY`, or the key connected in API > Account) | search, import, normalize, resolve link | Pagination by `next_page_token`; filters applied only on explicit result fields. Free tier ~250 searches/month. |
| Greenhouse | Available | Official public Job Board API (`boards-api.greenhouse.io`, GET) | Board token | search, import, normalize, resolve link | Local keyword/location filtering; single-posting import; application questions fetched with `?questions=true` for packet mapping. Verified live during testing (GitLab board). |
| Lever | Available | Official public Postings API (`api.lever.co/v0/postings/{site}`) | Site name | search, import, normalize, resolve link, health | Company name is the site identifier (API omits it). Verified live (boards with zero postings return an empty, successful result). |
| Ashby | Available | Official public Job Board API (`api.ashbyhq.com/posting-api/job-board/{name}`) | Job board name | search, import, normalize, resolve link, health | Unlisted jobs skipped in search; compensation parsed when present. Verified live (Ashby board, 71 postings). |
| SmartRecruiters | Available | Official public Posting API (`api.smartrecruiters.com/v1/companies/{id}/postings`) | Company identifier | search, import, normalize, resolve link, health | Server-side keyword search, offset pagination, detail fetch for up to 25 postings per search. |
| Workday, iCIMS, Taleo, Oracle Recruiting, SAP SuccessFactors, BambooHR, UKG, direct career sites | Planned | — | — | — | No official public candidate-facing posting API; tenant-specific portals would need per-portal scraping, which this build does not do. |

Shared behaviour: retry with backoff on 429/5xx (max 3 attempts), URL-level canonicalisation, dedupe by `(source, external_id)` then canonical URL then fingerprint+company+title+location, `source_sync_runs` history, health reported to `/api/metrics`.

Portal URL detection (`aira.ats.detect_portal`) recognises Greenhouse, Lever, Ashby, SmartRecruiters (and Workday hosts, reported as planned) so a pasted careers URL imports the posting or explains why it cannot.

## SerpApi (API console)

API gives every SerpApi engine in `aira/airaapi/catalog/` (136: 126 official plus 10 sub-endpoints) a playground, history, saved searches, monitors, usage accounting and a SerpApi-compatible local endpoint (`/v1`, see `docs/api.md`). It uses only the documented HTTP API with the account's own key: `search.json`, `account.json`, `locations.json` and the Search Archive (`searches/{id}.json|.html`). Account, locations and archive reads are free and do not count toward the plan. Nothing here scrapes serpapi.com or uses a SerpApi login.

- **Key**: `SERPAPI_API_KEY` in the environment, or a key pasted in API > Account (validated against `account.json`, stored Fernet-encrypted, never echoed). An account's own key takes precedence. The same key powers Google Jobs search in Jobs.
- **Cost model**: one credit per successful live search (an empty result still counts; errors do not). `extract` in `crawl` mode counts `max_pages`. Identical searches inside the cache window (default 60 minutes, setting `airaapi_cache_ttl_minutes`) are answered locally for 0 credits and shown as cache hits everywhere. Monitors always run live and show their estimated monthly cost before they are enabled.
- **Careers**: Google Jobs search with every filter Google returns, duplicate-safe import into Jobs, job alerts (a saved search plus a monitor that lists only new postings, optionally importing those above a match-score threshold) and six intelligence reports (company, salary, trends, interview prep, travel, learning) that are fixed plans of engine calls. Reports show their cost before they run (`dry_run`) and are built from cached sections where possible. The Jobs-tab Google Jobs source goes through the same history and cache. See `docs/api.md` (Careers). `remote`, posted-within and employment-type use Google's `ltype` and `chips` parameters, which Google has deprecated; the responses say so.
- **Reliability**: one retry on network errors and 5xx, never on 4xx; redirects are refused so the key cannot be forwarded; at most four searches run at once; an optional local hourly limit protects the quota; SerpApi error replies map to codes (`invalid_api_key`, `out_of_searches`, `rate_limited`, `search_failed`, `upstream_error`, `timeout`, `network_error`).
- **Catalog honesty**: the catalog was compiled from public documentation. Enums whose documented values were truncated, and engines with no documented result keys (`google_sports`), accept free text rather than rejecting valid values; each engine links to its documentation.
- **Tests and demos** use a stand-in server (`tests/serpapi_stub.py`) selected with `AIRA_SERPAPI_BASE_URL`; its responses are fixture-shaped and labelled `search_metadata.fixture = true`. Plain `http` is only accepted for loopback hosts.

## Application submission

No connected source offers an official candidate-side submission API (Greenhouse's job-board POST requires the employer's API key; LinkedIn exposes no apply tool). Aira therefore prepares and prefills, opens the application, and records the user's confirmation. The auto-submit policy is reported as unavailable and cannot be switched on.

## AI (Ollama)

Local chat completion (`OLLAMA_URL`, default `http://127.0.0.1:11434`, JSON mode) for fit reviews, tailored resume/cover-letter drafts, and LinkedIn reply drafts. Prompts contain the resume, the job text (labelled untrusted), and only prompt-safe contact fields; sensitive profile fields are never included. Default model is configurable in Settings.

## Email

Mailbox providers are not connected (no OAuth credentials in this environment). Emails are imported by pasting subject/body or uploading `.eml`; classification and extraction are deterministic (`aira.email_intel`, `email-rules-v1`) with confidence scores, duplicate detection by fingerprint, user correction, and confirmation gates. The connector boundary is the `_import_email` service: a future Gmail/Outlook connector only needs to feed parsed messages into it.

## Calendar

Standards-based: per-interview and all-upcoming ICS downloads (RFC 5545 with TZID, VALARM reminders, stable UID, SEQUENCE increments, METHOD:CANCEL) plus Google Calendar, Outlook.com, and Office 365 deep links. Provider APIs (Google Calendar, Microsoft Graph) are not connected; the interview record keeps the UID/sequence so a future connector can update the same event.

## Integration gate for new providers

Record the official API or permitted method, terms and rate limits, data fields, credentials and scopes, retention, failure modes, and a fixture-based test strategy before adding a provider. Prefer official APIs; isolate any browser automation behind an adapter; never bypass CAPTCHA, MFA, access controls, or rate limits.
