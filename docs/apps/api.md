# Developers

The developer app (catalog id `api`, a core app). It has two sections in the left menu:

* **API** (`#/airaapi`): a console for 136 search engines behind your own SerpApi key, a SerpApi-compatible
  `/v1` endpoint for your scripts, and two ways to earn money with it (selling search access and selling rank
  tracking). Build contract: [airaapi-spec.md](../airaapi-spec.md).
* **Connected apps** (`#/developers`, hosted Aira only): OAuth 2.0 apps, personal access tokens and signed webhooks
  for an organization's data. Contract: [developers.md](../developers.md). The single-user build says it is part of
  the hosted Aira.

Code: `aira/airaapi/` (except the Careers and Leads modules), `auto_apply_assistant/views-airaapi.js`,
`views-playground.js`, `views-serp-kit.js`, `views-developers.js`, `app-api.css`, and on the hosted side
`aira_cloud/oauth.py`, `aira_cloud/webhooks.py`, `aira_cloud/routes/developers.py`.

## In the Aira frame

Developers lives at `/developers` with its own logo, like the other apps (`auto_apply_assistant/shell.js`).

* **+ New** offers New search (the Playground, with the request form focused), New saved search, New monitor, New
  rank project and New API key, each opening the console's own form (`#/airaapi?tab=<tab>&new=<kind>`; the page drops
  `new=` once the form is open). Rank projects and API keys are left out for a child or teen profile, and in the
  hosted build everything that changes data needs `integrations.manage`; the hosted build adds New access token
  (`#/developers?tab=tokens&new=token`, which also takes `new=app` and `new=webhook`).
* **Search across the top** suggests rank projects (name, website, client, keywords), saved searches, stored searches
  (the server's own History search) and engines; choosing one opens the project, the saved request or engine in the
  Playground, or the stored search over History. Enter opens History filtered by the words; with no match it offers
  Find engines.
* **Menu folders**: the twelve console tabs below API, each with an icon, opening `#/airaapi?tab=<id>`; Connected
  apps (hosted) shows its six tabs the same way. On wider screens the folders replace the tab strip under the
  heading, which stays on phones. The heading names the open tab, with a line on what it is for. In the single-user
  build Connected apps points to the API keys and docs instead.

## The API console tabs

| Tab | What it is for |
| --- | --- |
| Overview | Plan and searches left, this hour's throughput, 30-day chart, top engines, cache and error rates, recent searches, cost estimator. |
| Playground | Pick an engine, fill the generated form, run it (Ctrl+Enter), see the result as cards, JSON, a table, HTML or code in 8 languages, save, monitor, compare, share. |
| Engines | The catalog, filtered by provider and category. |
| History | Every stored search: filters, text search inside results, compare two runs, export, pin, re-run, bulk delete. |
| Locations | Canonical location names for the `location` parameter (free, no key needed). |
| Monitors & saved | Saved searches and the monitors that re-run them on a schedule with a monthly budget. Rank-tracker keywords are counted in the monthly burn but managed on their own tab. |
| Rank tracker | Where a website shows up on Google for its keywords, over time, with a client report. |
| API keys | Keys for `/v1`, each with an hourly limit and, optionally, client terms (who it is for, a price, a monthly allowance, the engines it may use). |
| Earnings | What clients owe you this month (keys and rank-tracking fees) against what the searches cost you, with a statement per client. |
| Account | Connect, replace or remove the SerpApi key; local limits and retention. |
| Docs, Status | The `/v1` reference and migration guide; SerpApi reachability, throttle and queue. |

## How people earn with it

**Sell search access.** Create an API key and open "Selling access to a client?". Give it the client's name, a price
per 1,000 searches, a monthly allowance (0 for none) and, if you like, the only engines it may use. The client calls
`/v1/search.json` exactly like SerpApi. Aira:

* refuses an engine the key may not use (`403 engine_not_allowed`) and stops the key at its allowance for the month
  (`429 quota_exceeded`, it starts again on the 1st), both before any search is spent;
* counts every call per key and day in `airaapi_key_usage` (never pruned, so statements outlive the history window):
  successful calls are billable, cached answers included (they cost you nothing: that is your margin), failed calls
  are counted but never billed;
* shows the month's searches, the amount to bill, your cost and your profit on the **Earnings** tab, and downloads
  a **statement** for each client as a web page (print it to PDF) or CSV;
* keeps clients apart. A key with terms (a client name, a price, an allowance or an engine list) is a **client key**:
  `/v1/searches.json` lists only the searches made with it, `/searches/{id}.json` finds only those (404 for anything
  else, including your Playground and rank checks), and `/account.json` answers with the key's own allowance and
  usage (`plan_name: "API access for <client>"`, `searches_per_month`, `plan_searches_left`, `this_month_usage`,
  `allowed_engines`) instead of your SerpApi account. A key without terms is your own and reaches the whole account,
  like a SerpApi key.

**Sell rank tracking.** A rank project is one website (its domain), up to 25 keywords, where to search from
(location, country, language, device), how often (by hand, daily or weekly), the client's name and the monthly fee.
Each check reads Google's first page for each keyword and records the position of the website's first organic
result (subdomains count; look-alike domains do not), the page that ranked, and up to three domains above it. The
project page shows the position, the change since the last check, the best position, a trend line and a link to the
stored result; **Client report** downloads a self-contained web page (or CSV) with the top-3 and first-page counts,
the average position and every keyword. A notification is sent when a keyword enters or leaves the results, reaches
the top 3, or moves 3 places or more (switchable per project).

Set **Your cost per search** (what your SerpApi plan charges you; 75 for 5,000 searches is 0.015), the currency and
your business name (printed on statements and reports) on the Earnings tab.

How a month is added up: a rank project's fee is billed for every month from the one it was created in to the one it
was deleted in. Its checks are counted in a monthly ledger (`airaapi_rank_usage`) that keeps them when a keyword is
removed or the project is deleted; a deleted project stays (hidden, `deleted_at`) only for this. A project with no
client and no fee is your own site: its checks are shown as a cost note (`totals.own_checks`, `own_cost`), not as a
client. A client key is listed for the months it existed (created to revoked) or had usage. The month picker offers
every month from the first client key, project or usage up to this one; a month that has not started is refused.

### How rank checks run

Every keyword is an ordinary saved Google search with its own monitor (tagged `rank-tracker`), so scheduling,
the hosted worker's credit accounting and History work as for any monitor. A monitor hook
(`aira.airaapi.rank.process_monitor_run`) turns each finished run into a row of `airaapi_rank_checks`. The generic
Monitors tab and the API cannot delete or change those saved searches or monitors (`400`: change the project
instead), the Playground leaves them out of its Saved list, and they do not use up the 25-monitor limit: rank
tracking has its own limits (20 projects, 25 keywords per project, 60 keywords in all; an edit that swaps keywords
counts what it removes, and a keyword typed with different capitals keeps its history and takes the new spelling).
Checks always run live, so each one is one search; the dialog shows the monthly estimate before you start.

### Who may use it

Keys, client prices, rank tracking and earnings are the account holder's. In the single-user build a household
shares one sign-in, so these routes (`/keys`, `/key`, `/earnings*`, `/rank*`) answer `403 adult_only` for a child or
teen profile picked in the browser (`X-Aira-Profile`), and `403 profile_required` when the account has several
profiles and the request names none (the browser then shows the picker). In the hosted build the organization's
roles decide: reads need `integrations.read` (owner, admin, auditor), changes `integrations.manage` (owner, admin).

## Routes added for earning

All under `/api/airaapi` (session), so the hosted gateway answers `402 app_required` until the organization has the
Developers app.

| Method | Path | Body or query | Notes |
| --- | --- | --- | --- |
| POST | `/keys` | `{name, hourly_limit, client_name?, price_per_1000?, monthly_quota?, allowed_engines?}` | `allowed_engines` is a list or a comma string of engine ids. |
| PATCH | `/keys/{id}` | any of the fields above | Audited as `airaapi.apikey.update`. |
| GET | `/keys` | | Each key also has `client_name, price_per_1000, monthly_quota, allowed_engines, used_this_month, quota_left`. |
| GET | `/earnings?month=YYYY-MM` | | `{month, settings, keys[], rank[] (each with deleted), totals{billed, cost, margin, calls, cached, checks, clients, own_checks, own_cost}, by_day[], months[], cost_known}` |
| PUT | `/earnings/settings` | `{currency, cost_per_search, business_name}` | Three-letter currency; cost 0 to 100. |
| GET | `/earnings/statement/{key_id}?month=&format=csv\|html` | | Download. CSV cells that start with `= + - @` are written as text. |
| GET, POST | `/rank` | `{domain, keywords (list or lines), name?, client_name?, location?, gl?, hl?, device?, interval_minutes: 0\|1440\|10080, monthly_fee?, notify?}` | `201` with the project. |
| GET, PATCH, DELETE | `/rank/{id}` | PATCH takes any create field; `keywords` replaces the list (kept keywords keep their history; a new domain clears it) | DELETE hides the project and removes its keywords; what it billed stays on Earnings. |
| POST | `/rank/{id}/run` | `{keyword_id?}` | Checks every keyword (or one) now: `{project, results[], credits, failed}`. `409 not_configured` without a SerpApi key. |
| GET | `/rank/{id}/report?format=html\|csv` | | The client report. |

`POST /saved` also takes `from_search_id`: saving the result you are looking at records that run as the saved
search's last run instead of "Never run".

## Data

New tables, created by `store.ensure_schema` for new databases and lazily by `store.ensure_extras` for databases
already past migration 15: `airaapi_key_usage(day, key_id, engine, calls, cached, errors, credits)`,
`airaapi_prefs(name, value)`, `airaapi_rank_projects` (with `deleted_at`), `airaapi_rank_keywords`,
`airaapi_rank_checks`, `airaapi_rank_usage(project_id, month, checks)` (filled from the existing checks when it is
first created); new columns on `airaapi_api_keys`: `client_name, price_per_1000, monthly_quota, allowed_engines`.

## One account

The local build: one sign-in opens Developers with every other app; the console data is per account. The hosted
build: the console runs on the organization's workspace, routes under
`/api/airaapi` and `/v1` answer `402 app_required` until `subscribe_app(org.owner, "api")`, and organizations never
see each other's keys, searches, projects or earnings. The gateway refuses any local route without a rule in
`aira_cloud.policy.POLICY`; the rules for the rank tracker, Earnings and `PATCH /keys/{id}` are registered by
`aira_cloud/routes/developers.py` (`developers_rules`) until policy.py lists them. A by-hand rank check
(`POST /rank/{id}/run`) is metered like one API search there; scheduled checks are metered per search by the worker.

## Tests

`tests/test_airaapi_*.py` (catalog, client, compat, console assets, parity, routes, service, store, and
`test_airaapi_rank_earnings.py` for the rank tracker, client keys and earnings), `tests/test_suite_api_shell_ui.py` (+ New, the top search and the menu folders in a browser), `tests/test_serp_kit_js.py` (the
kit and Playground logic in node) and `tests/test_cloud_oauth.py` (Connected apps). Everything runs against the
stand-in SerpApi server `tests/serpapi_stub.py`; nothing calls SerpApi.
