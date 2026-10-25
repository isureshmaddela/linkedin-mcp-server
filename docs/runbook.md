# Operations Runbook

## Prerequisites

- Windows, macOS, or Linux with Python 3.12–3.14 and `uv`.
- Chromium for Patchright: `uv run patchright install chromium` (LinkedIn features only).
- Ollama running locally with a chat model pulled (default `qwen3.5:4b`).
- Optional: a SerpApi key for Google Jobs, Careers and the API console: paste it in API > Account, or set `SERPAPI_API_KEY` in `.env`.

## Install and start

```powershell
uv sync --group dev
.\scripts\start_aira.ps1 -OpenBrowser        # starts http://127.0.0.1:8765
# or
uv run python auto_apply_assistant.py
```

The LinkedIn MCP server is started per account by Aira itself (Settings → Integrations → Connect LinkedIn opens an isolated browser profile for sign-in). The standalone MCP server (`uv run -m linkedin_mcp_server --transport streamable-http`) remains available for other MCP clients.

## First-run checklist (three navigation actions or fewer per workflow)

1. Create a local account (sign-in screen).
2. Settings → Resume: upload or paste the master resume.
3. Settings → Profile: fill identity, contact, career, and matching preferences; sensitive fields are optional.
4. Jobs → search a source or paste a posting URL; open a job → Fit & evidence.
5. Job → Documents: generate and approve drafts; Job → Apply: prepare, review, approve, open, record confirmation.
6. Applications → Email updates: paste interview emails to create interviews; Interviews → add to calendar.
7. API → Account: paste a SerpApi key (optional). The Dashboard, Jobs > Google Jobs and Careers pick it up at once; the top bar shows `SerpApi ready`.
8. Careers → Jobs: search, save a result to Jobs, then add an alert for the search.
9. Jobs → Auto-apply:
   - Your answers: confirm work authorization, sponsorship, the privacy attestation, how you heard and your years of experience.
   - New search plan: positions, where to search, minimum match and schedule.
   - Run now, then answer anything listed under Needs you.

## Configuration

| Variable | Default | Purpose |
|---|---|---|
| `AUTO_APPLY_PORT` | `8765` | Loopback port. |
| `AUTO_APPLY_DB_PATH` | `~/.linkedin-mcp/auto_apply/applications.sqlite3` | Primary DB; `accounts.sqlite3`, `accounts/`, `documents/`, `documents.key` live beside it. |
| `OLLAMA_URL` | `http://127.0.0.1:11434` | Local model endpoint. |
| `SERPAPI_API_KEY` | unset | A server-wide SerpApi key for Google Jobs, Careers and API. A key pasted in API > Account takes precedence for that account. |
| `AIRA_DOCUMENT_KEY` | unset | Fernet key override for the document store. |
| `AIRA_SECRET_KEY` | unset | Fernet key override for secrets saved by the API console (the pasted SerpApi key); otherwise `airaapi.key` beside the database. Losing it makes the stored SerpApi key unreadable: paste it again. |
| `AIRA_SERPAPI_BASE_URL` | `https://serpapi.com` | Tests and demos only: point SerpApi calls at a stand-in server. |
| `AUTO_APPLY_LOG_LEVEL` / `AUTO_APPLY_JSON_LOGS` | `INFO` / `0` | Log level; `1` emits JSON lines with correlation IDs. |
| `AIRA_ACCOUNT_URL` | unset | The Aira account (the hosted build, for example `http://127.0.0.1:8780`) that can sign people in here. With `AIRA_ACCOUNT_SECRET` set, the sign-in page offers "Sign in with your Aira account". See [apps.md](apps.md). |
| `AIRA_ACCOUNT_SECRET` | unset | Shared with the account as its `AIRA_CLOUD_LAUNCH_SECRET` (32+ random characters). Signs the launch-token validation call. |
| `AIRA_ACCOUNT_ALLOW_LOCAL` | `0` | `1` lets `AIRA_ACCOUNT_URL` point at a private (non-loopback, non-public) address you trust. |

In-app settings (Settings → Automation / Data): daily application limit (0 = unlimited), email action threshold and auto-apply toggle, time zone, default model, document retention days.

## SerpApi key, API keys and the local `/v1` endpoint

- **Connecting**: API > Account > Validate and connect. The key is checked against SerpApi's free `account.json` first; a rejected key leaves the previous one untouched. It is stored Fernet-encrypted in the account's database (`airaapi_secrets`), the Fernet key being `airaapi.key` beside the databases or `AIRA_SECRET_KEY`. No endpoint returns the key (only `configured`, `source` and the last four characters), and it never appears in logs, stored search records, exports or URLs the browser sees.
- **Replacing or removing**: Replace in the same panel. Remove deletes only the account's own key; if `SERPAPI_API_KEY` is set, that key is used afterwards and cannot be removed from the app (the API answers `409 conflict`). Rotate at SerpApi first, then paste the new key.
- **Lost `airaapi.key` or `AIRA_SECRET_KEY`**: the stored SerpApi key becomes unreadable. Aira says so in Account; paste the key again. Back the file up with the databases.
- **Cost control**: a fresh identical search inside the cache window (API > Account > cache window, default 60 minutes) is served from history for 0 credits. Other levers there: an hourly cap on live searches (0 = off) and the monitor intervals (alerts run at most hourly). Playground, Careers and the Compare action show the cost before running anything that uses more than one search.
- **Retention**: records older than 30 days are deleted, and once there are more than 2,000 the oldest go first; a single stored result is capped at 5 MB (a larger body is returned but not stored). Pinned searches, running async searches and the latest run of each saved search or monitor are never pruned. Limits are settings in API > Account (`airaapi_retention_days` 1 to 3650, `airaapi_max_records` 50 to 100000, `airaapi_result_cap_mb` 1 to 50, `airaapi_cache_ttl_minutes` 0 to 10080, `airaapi_hourly_limit`). Cleanup runs on its own; History also offers bulk delete and "older than" cleanup.
- **API keys for scripts**: API > API keys > Create. The `aira_...` secret is shown once; only its hash is stored. Call `http://127.0.0.1:8765/v1/search.json?engine=google&q=...&api_key=aira_...` (or `Authorization: Bearer`). Every call counts toward the key's hourly limit, uses the same cache, history and usage as the console, and fails with `409` until a SerpApi key is connected unless the answer is already cached. Revoke a key at any time.
- **Monitors and alerts** run only while Aira is running and only for accounts with a key. Each run is one live search; the estimate shown when creating one is searches per month. A monitor's **monthly budget** (0 = no cap) caps its live runs per calendar month (UTC): once it is used up, scheduled runs wait until the 1st and Run now is refused. Raise the budget on the card to resume. **Pause if a run fails** disables the monitor after a failed run and sends a notification.
- **Search inside results**: History > Inside results also searches the titles, snippets and links of stored results. The index is filled as searches are stored. Migration 15 indexes the newest 5,000 older results; older ones stay searchable only by query, label, engine and error.
- **X-Ray** (History > a Google or Google Ads search > X-Ray) reads SerpApi's pixel positions for that search from the Search Archive. This uses no search, and the copy is kept with the search. It needs the search's SerpApi id (cached copies have it), so it fails for searches older than SerpApi's 31-day archive unless they were measured before. Pixel Position is a beta feature at SerpApi.
- **Exports** per search: JSON, CSV, Excel (`.xlsx`), Markdown table, HTML page, cURL.
- **Stand-in server for demos and tests**: `tests/serpapi_stub.py` serves `/search.json`, `/account.json`, `/locations.json` and `/searches/{id}` with fixture-shaped data (labeled `"fixture": true`), magic queries for errors (`__rate_limit__`, `__empty__`, `__search_failed__`) and call counters. Start it (`SerpApiStub().start()`), set `AIRA_SERPAPI_BASE_URL` to its `base_url` and use the key `stub_valid_key_0123456789abcdef`, always with a scratch `AUTO_APPLY_DB_PATH` and a port other than 8765. Never point demos at your real database.

## Aira Jobs (finding jobs without SerpApi)

- **What it is**: API's own engine `aira_jobs`. It searches free public job-board APIs (Himalayas, The Muse,
  Remotive, Jobicy, Arbeitnow) itself, with no SerpApi key and no credits.
- **Where it is used**: Jobs > **Aira Jobs**, Careers > Jobs ("Search with: Aira Jobs"), Auto-apply plans (on by
  default) and `/v1/search.json?engine=aira_jobs`.
- **Politeness**: each board is cached on Aira's side to follow its terms (Himalayas 12 h, Remotive 6 h, The Muse and
  Arbeitnow 3 h, Jobicy 2 h). Repeating a search does not hit the boards. Every result shows its board and links back.
- **Auto-apply on board jobs**: a board page is not a form. Aira follows it to the employer's form only when it leads
  to Greenhouse, Lever, Ashby or SmartRecruiters (often true for The Muse). Others end as "Apply manually" with the
  link. Himalayas pages are never opened by script.
- **Limits**: a smaller, mostly remote and tech set than Google Jobs. Google-only filters (radius, posted within) do
  not apply. Careers reports still need SerpApi.
- **A board is down**: results come from the others, and Careers names the board that did not answer.

## Auto-apply (Jobs > Auto-apply)

- **What runs**: each enabled search plan runs on its schedule while Aira runs.
  - It searches its positions on LinkedIn (needs LinkedIn connected), Google Jobs (one SerpApi search per position
    per run) and the company boards you list.
  - It scores each job against your profile.
  - It applies to the best matches above the plan's minimum, up to "applications per run" and the daily limit
    (Auto-apply header, shared with manual applications; 0 = no limit).
  - Each job is tried once.
- **Pause everything**: Pause all in the Auto-apply header (`auto_apply_paused`). A plan can also be switched off on
  its card.
- **What it never does**:
  - guess an answer: a required question with no matching profile value or confirmed answer holds the job under
    *Needs you*;
  - fill a sensitive question (work authorization, sponsorship, salary, demographics, attestations) without a
    confirmed answer;
  - pass a CAPTCHA or a sign-in;
  - claim success without the site's confirmation.
- **Evidence**: every submitted application has a screenshot and the confirmation page text, encrypted in Documents.
  The job moves to Applied with method `auto` and a 7-day follow-up task.
- **Speed**: with Ollama running, each application first gets a tailored cover letter (about a minute with
  `qwen3.5:4b`). Turn "Write a tailored cover letter" off in a plan for faster runs.
- **Show the browser**: the Auto-apply header switch (`apply_show_browser`) runs Chromium visibly. Chromium must be
  installed (`uv run patchright install chromium`).
- **LinkedIn Easy Apply** is off by default in every plan. LinkedIn's User Agreement does not allow automated
  applications, and accounts that use them can be restricted; turn it on only if you accept that.
- **Unsupported**: Workday and LinkedIn jobs that apply on an unknown external site end as "Apply manually", with the
  link.

## Health, readiness, metrics

- `GET /api/health` → liveness (`{"status":"ok"}`).
- `GET /api/ready` → `200 ready` or `503 degraded` with per-check detail (accounts DB, data directory writable, Ollama optional).
- `GET /api/metrics` (signed in) → request totals, status codes, per-route avg/p95 latency, counters (`jobs_scored`, `emails_classified`), integration health.
- Every response carries `X-Request-ID`; error toasts in the UI show it. Search logs for the ID to find the failing request.

## Database migrations

Schema changes ship as additive migrations in `aira/schema.py` and apply automatically on first connection; the ledger is visible at `GET /api/settings` (`migrations`, `schema_version`). Rollback plan: older code keeps working with newer additive columns; to revert data, stop Aira and restore the pre-upgrade backup of the whole data directory.

## Backup and restore

Stop Aira (and LinkedIn workers) first. Back up the entire data directory together: `applications.sqlite3`(+`-wal`/`-shm`), `accounts.sqlite3`, `accounts/`, `documents/`, `documents.key`, `airaapi.key`. Encrypt backups; documents are useless without the key. Restore the full set to the same base path and restart. `GET /api/account/export` provides a per-account JSON export for portability.

## Troubleshooting

| Symptom | Action |
|---|---|
| "LinkedIn off" in the top bar | Settings → Integrations → Connect LinkedIn; complete sign-in in the opened browser; never bypass verification. Check `accounts/linkedin_profiles/<user>/login.log` and `mcp.log`. |
| "Ollama offline" | Start `ollama serve`, pull the configured model, retry. Scoring and tracking work without Ollama; reviews/drafts do not. |
| Google Jobs "not configured" or `SerpApi off` in the top bar | API > Account > connect a key (no restart needed), or add `SERPAPI_API_KEY` to `.env` and restart Aira. |
| API shows "Check key" or searches fail with `invalid_api_key` | SerpApi rejected the stored key. Paste a current key in API > Account; the old key stays until a new one validates. |
| `402` or "out of searches" | The SerpApi plan is used up (API > Account shows plan and extra credits). Cached results still work. |
| `429` on `/v1` | The Aira key's hourly limit, or a client that sent too many rejected keys (locked out for 5 minutes). Raise the limit in API keys or wait. |
| Thumbnails show a letter tile | The page cannot load remote images, usually an offline machine or a blocked host; results are unaffected. |
| ATS search returns 0 | The board may be empty; check Settings → Integrations → last run, and the identifier (board token / site name). |
| Auto-apply holds every job | Open Jobs > Auto-apply > Your answers and confirm work authorization, sponsorship and the attestation; answer the listed question once with Remember checked. |
| Auto-apply says another automation is busy | An inbox check or another run holds the account's automation slot; try again in a minute. |
| `429 Too many requests` | Wait for the window shown in the message; sign-in lockout is 15 minutes. |
| `503` from `/api/ready` | Data directory not writable or accounts DB unreadable; fix permissions/path. |
| Document download fails with integrity error | The blob was modified or the key changed; restore from backup. |

## Frontend logic tests

The shared API kit (`auto_apply_assistant/views-serp-kit.js`) keeps its pure logic (code snippets, parameter validation, tree search, table flattening, chart geometry, result-section planning) free of the DOM so it runs under plain node:

```
node tests/js/serp_kit.test.mjs
node --check auto_apply_assistant/*.js
```

No packages are needed (`node:assert` only, node 18 or newer). `uv run pytest tests/test_serp_kit_js.py` runs the same file when `node` is on PATH and skips otherwise.

## Stop, upgrade, rollback

Stop with Ctrl+C. Upgrade by pulling the new source and restarting; migrations apply on start. Roll back by checking out the previous revision and restarting; restore the data backup only if data must be reverted.

## Deployment scope

Supported deployment is a single trusted machine on loopback. The repository's Dockerfile packages the MCP server, not Aira; a containerised Aira would still need the visible LinkedIn sign-in browser and a local Ollama, so it is intentionally out of scope.
