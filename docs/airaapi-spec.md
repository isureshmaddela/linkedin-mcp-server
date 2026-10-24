# Aira API and Careers: design spec

Status: build contract for the "Aira API" console (a SerpApi dashboard clone that is better where it can be),
the SerpApi-compatible local API, and the "Careers" workspace. Backend, frontend and tests are all built against
this file. Where this file and the code disagree after the build, update this file.

## 1. Product

Aira gains two top-level sections beside Dashboard, Jobs, Applications, Interviews, Recruiters, Documents,
Analytics and Settings:

| Section | Route | What it is |
|---|---|---|
| **Aira API** | `#/airaapi` | A console equivalent to the SerpApi dashboard, covering every SerpApi engine: Overview (usage, plan, throughput), Playground, Engines catalog, Search history and archive, Locations, Account and API key, Status, Saved searches and monitors, Aira API keys, Docs. |
| **Careers** | `#/careers` | Career workspaces powered by those engines: Job search (Google Jobs with every filter), Company intelligence, Salary and market, Interview prep, Onsite travel, Learning. Results import straight into Aira Jobs, Recruiters, Interviews. |

Aira also exposes a **SerpApi-compatible local endpoint** (`/v1/search.json` etc.) guarded by Aira API keys, so
scripts can call Aira instead of SerpApi and get caching, history and usage accounting for free.

The SerpApi key is a secret. It comes from `SERPAPI_API_KEY` in the environment or is pasted once in
Aira API > Account, validated against `account.json`, and stored Fernet-encrypted (key file next to
`documents.key`). It is never returned by any endpoint (only `configured`, `source`, `last4`), never logged, never
stored in search records and never put in URLs the browser sees.

## 2. Engine catalog

`aira/airaapi/catalog/*.json` (shipped as package data; add to `pyproject.toml` build includes). Files:
`platform.json` (dashboard pages, common parameters, errors, plans, etc.), `google_core.json`,
`google_scholar_finance_trends.json`, `google_maps_local_travel.json`, `google_shopping_jobs.json`,
`other_web_engines.json`, `marketplaces_social_reviews.json`, optional `extra_*.json`. Engine entry shape is
described in the research prompt: `id, name, category, family, description, docs_url, endpoint, params[],
requires_any_of, pagination, result_keys, result_item_fields, aira_use, verified`.
`catalog.py` merges all files, validates them (unique ids, param types, enum options) at import and exposes:

```python
def engines() -> dict[str, Engine]
def engine(engine_id: str) -> Engine            # raises LookupError
def common_parameters() -> list[dict]
def validate_params(engine_id, params) -> tuple[dict, list[str]]   # (clean params, error messages)
def credit_cost(engine_id, options) -> int       # 1 per SerpApi search; document any engine-specific rule from platform.json
```

## 3. Backend modules (`aira/airaapi/`)

| Module | Responsibility |
|---|---|
| `catalog.py` | Load and validate catalog JSON (above). |
| `client.py` | `SerpApiClient(api_key, base_url=None, timeout=60)`; base URL overridable with env `AIRA_SERPAPI_BASE_URL` (used by tests and the stand-in server). Methods: `search(engine, params, options) -> RawResponse`, `account()`, `locations(q, limit)`, `archive(search_id, fmt)`, `status()`. Retries once on network errors and 5xx, never on 4xx. Maps SerpApi errors to `SerpApiError(code, message, http_status, retryable)`. Strips the key from every log and exception text. `urllib` only (no new dependency). |
| `store.py` | SQLite access (functions take a `sqlite3.Connection`). Tables below. Result bodies stored zlib-compressed. Retention and size caps (default: keep 30 days or 2,000 records, result cap 5 MB each, configurable via settings). |
| `service.py` | Business logic: `run_search`, cache lookup, history, diff, export, saved searches, monitors, usage rollups, overview. Pure functions over a connection plus a `client` argument so tests inject a fake. |
| `careers.py` | Job search normalization and Careers workspaces (section 6). |
| `compat.py` | The `/v1/*` SerpApi-compatible API (section 5). |
| `http.py` | Tiny framework the glue uses: `Request`, `Raw`, `@aira_route(method, pattern, auth="session"|"api_key"|"none")`, `AIRA_ROUTES`. |
| `routes.py` | All route handlers (sections 4 and 5), imported by the glue. |

### 3.1 Request/response framework (`http.py`)

```python
@dataclass
class Request:
    match: re.Match[str]; payload: dict; query: dict[str, list[str]]; user: dict | None
    headers: Mapping[str, str]; client: str
    connect: Callable[[], sqlite3.Connection]          # the user's database (per-user, migrated)
    setting: Callable[[str], str]                      # app_* settings
    secret_store: SecretStore                          # encrypt/decrypt small secrets with the Fernet key
    def q(self, name, default="") -> str; def qint(self, name, default, lo, hi) -> int

@dataclass
class Raw:  # for downloads
    status: int; body: bytes; content_type: str; filename: str | None = None

Handler = Callable[[Request], tuple[int, dict] | Raw]
```

Handlers raise `ValueError` (400), `LookupError` (404), `PermissionError` (403), `RuntimeError` (502) exactly like
the existing handlers; `SerpApiError` subclasses `RuntimeError` but carries its own HTTP status mapping:
SerpApi rejected the key -> `{"error","code":"invalid_api_key"}` (status 422 on console routes and 502 on `/v1`, never
401: the browser app treats any 401 as "session ended", and on `/v1` 401 means the caller's Aira key is bad), out of
searches -> 402, rate limited -> 429, other -> 502. A bad Aira key is 401 with `code: "invalid_aira_key"`. See section 9.

Glue in `auto_apply_assistant.py` (a short block placed after the `RequestHandler` class, because it installs methods on it): for every entry in
`AIRA_ROUTES` it appends `(method, compiled_pattern, name, auth_flag)` to `ROUTES` and installs a bound-method
wrapper on `RequestHandler` that builds `Request`, calls the handler, and sends JSON or `Raw`. `auth="api_key"`
routes use `auth=False` in `ROUTES` and the wrapper authenticates the Aira API key itself (binding the owning user
with `_bind_user`). Standard rate limit, CSRF and error envelope stay in `_dispatch`.

### 3.2 Tables (additive migration 12 `airaapi` in `aira/schema.py`; 9 to 11 belong to the messenger work)

```
airaapi_searches(id TEXT PK, serpapi_id TEXT, engine TEXT, params_json TEXT, params_hash TEXT,
  status TEXT,            -- ok | error | running
  source TEXT,            -- playground | careers | api | monitor | intel | rerun
  label TEXT, created_at TEXT, duration_ms INTEGER, http_status INTEGER, error TEXT,
  result_count INTEGER, result_bytes INTEGER, result_blob BLOB, html_blob BLOB,
  cache_hit INTEGER DEFAULT 0, credits INTEGER DEFAULT 1, pinned INTEGER DEFAULT 0)
airaapi_saved(id TEXT PK, name TEXT, engine TEXT, params_json TEXT, tags TEXT, created_at TEXT,
  last_run_at TEXT, last_search_id TEXT, pinned INTEGER)
airaapi_monitors(id TEXT PK, saved_id TEXT, interval_minutes INTEGER, enabled INTEGER, next_run_at TEXT,
  last_run_at TEXT, last_search_id TEXT, notify_on_change INTEGER, last_change_summary TEXT)
airaapi_usage(day TEXT, engine TEXT, source TEXT, searches INTEGER, cached INTEGER, errors INTEGER,
  credits INTEGER, PRIMARY KEY(day, engine, source))
airaapi_secrets(name TEXT PK, ciphertext BLOB, updated_at TEXT)
airaapi_api_keys(id TEXT PK, name TEXT, prefix TEXT, key_hash TEXT UNIQUE, created_at TEXT,
  last_used_at TEXT, revoked_at TEXT, hourly_limit INTEGER)
```
`airaapi_api_keys` lookups for `/v1` calls must work before a user is known: the glue keeps a small
`airaapi_key_index(key_hash PK, user_id)` table in the accounts database (`AUTH_DB_PATH`), written when a key is
created or revoked. Keys look like `aira_` + 40 URL-safe random characters; only a SHA-256 hash is stored; the
plaintext is shown once at creation.

### 3.3 Search semantics (`service.run_search`)

```python
def run_search(connection, client, engine_id, params, *, options=None, source="playground",
               label="", now=None) -> dict   # the record described in 4.2
```
1. `validate_params` (required params, `requires_any_of`, enum membership, type coercion). Unknown params are
   rejected with a helpful message unless `options.allow_unknown` is true (advanced users).
2. Cache: key = sha256(engine + sorted params). If a non-error record with the same hash exists newer than the
   cache TTL (setting `airaapi_cache_ttl_minutes`, default 60; `no_cache=true` skips) return it with
   `cache_hit=true` and `credits=0` and do not call SerpApi. Cache hits are shown as such everywhere.
3. Local throttle: setting `airaapi_hourly_limit` (default 0 = off) and a per-process concurrency cap of 4.
4. Call the client, store the record (also on error, with `status="error"`), update `airaapi_usage`.
5. `options`: `no_cache`, `output` (`json`|`html`), `async` (store `running`, poll via archive), `zero_trace`,
   `json_restrictor`, `allow_unknown`.

## 4. Session-authenticated routes (`/api/airaapi/*`)

All JSON. Records use ISO-8601 UTC strings. Errors use the standard `{"error": "..."}` envelope (plus `code`).

### 4.1 Overview, account, status, key

```
GET  /api/airaapi/overview
 -> { configured: bool, key: {source: "env"|"app"|null, last4: "ab12"|null},
      account: {plan_name, searches_per_month, plan_searches_left, this_month_usage, extra_credits,
                total_searches_left, hourly_rate_limit, this_hour_searches, last_hour_searches,
                account_email, account_status} | null,    // from account.json, cached 60s, null if not configured/unreachable
      account_error: str | null,
      usage: { today: n, last_7_days: n, last_30_days: n,
               by_day: [{day, searches, cached, errors}], by_engine: [{engine, name, searches, errors, avg_ms}],
               by_source: [{source, searches}], cache_hit_rate: 0..1, avg_ms: n, error_rate: 0..1,
               credits_used_30d: n },
      recent: [record-summary x10], monitors: {active, due_next}, saved: n, engines: n, categories: [{id,label,count}] }

GET  /api/airaapi/account            -> same `account` object plus `fetched_at`; 409 `{error, code:"not_configured"}` when no key
POST /api/airaapi/key  {api_key}     -> validates with account.json first; 200 {configured:true, source:"app", last4, account}
DELETE /api/airaapi/key              -> removes the app-stored key (env key cannot be removed: 409)
GET  /api/airaapi/status
 -> { local: {version, uptime_s, db_ok}, serpapi: {reachable: bool, latency_ms, checked_at, error},
      throttle: {hourly_limit, used_this_hour}, queue: {running, async_pending} }
```

### 4.2 Engines

```
GET /api/airaapi/engines?category=&family=&q=
 -> { engines: [{id,name,family,category,description,docs_url,param_count,required:[names],pagination,aira_use}],
      categories: [{id,label,count}], families: [{id,count}], total }
GET /api/airaapi/engines/{engine_id}
 -> full catalog entry + { common_parameters: [...], examples: {curl, python, javascript, ruby, php, go, java, csharp, aira_v1: "..."} }
```
Examples are generated from the engine's required params with placeholder values and `api_key=YOUR_AIRA_API_KEY`
against the local `/v1/search.json`, and also a "direct to SerpApi" variant.

### 4.3 Search and history

```
POST /api/airaapi/search  {engine, params: {...}, options?: {...}, label?: str, save_as?: str}
 -> 200 record
record = { id, serpapi_id, engine, engine_name, params: {...}, status, source, label, created_at, duration_ms,
           http_status, error, result_count, result_bytes, cache_hit, credits, pinned,
           result: {...SerpApi JSON...} | null,          // included by POST /search and GET /searches/{id}
           html_available: bool }
GET  /api/airaapi/searches?engine=&status=&source=&q=&pinned=&from=&to=&limit=25&offset=0
 -> { items: [record-summary (no result)], total, limit, offset }
GET  /api/airaapi/searches/{id}                -> record with result; ?html=1 adds `html` (string) when stored
DELETE /api/airaapi/searches/{id}              -> {ok:true}
POST /api/airaapi/searches/{id}/rerun  {options?} -> record (source "rerun", never served from cache unless unchanged within TTL and `use_cache`)
PATCH /api/airaapi/searches/{id}  {pinned?, label?}
GET  /api/airaapi/searches/{id}/export?format=json|csv|html|curl   -> Raw download (csv flattens the main result list)
GET  /api/airaapi/diff?a=ID&b=ID               -> { added: [...], removed: [...], changed: [{path, before, after}], summary }
DELETE /api/airaapi/searches?before=ISO&engine=   -> bulk delete {deleted: n}
```

### 4.4 Locations

```
GET /api/airaapi/locations?q=austin&limit=10
 -> { locations: [{id, google_id, google_parent_id, name, canonical_name, country_code, target_type, reach, gps:[lon,lat]}] }
```
Backed by SerpApi `locations.json` (free, not counted) with a 24 h local cache; works without an API key.

### 4.5 Saved searches and monitors

```
GET/POST /api/airaapi/saved   {name, engine, params, tags?}          -> {saved:[...]} / saved
PATCH/DELETE /api/airaapi/saved/{id}
POST /api/airaapi/saved/{id}/run                                      -> record
GET/POST /api/airaapi/monitors {saved_id, interval_minutes>=15, notify_on_change}
PATCH/DELETE /api/airaapi/monitors/{id}
POST /api/airaapi/monitors/{id}/run                                   -> {record, diff}
```
A monitor re-runs its saved search on schedule (hook `service.run_due_monitors(...)` called from the existing
scheduler loop), computes the diff against the previous run and raises an in-app notification when the main result
list changed. Costs are shown before enabling (searches per month estimate).

### 4.6 Aira API keys and docs

```
GET  /api/airaapi/keys    -> {keys: [{id,name,prefix,created_at,last_used_at,revoked_at,hourly_limit,used_this_hour}]}
POST /api/airaapi/keys {name, hourly_limit?} -> {key: {...}, secret: "aira_..."}    // secret returned exactly once
DELETE /api/airaapi/keys/{id}                  -> revoke
GET  /api/airaapi/docs    -> {base_url, auth, endpoints: [...], parameters: [...], errors: [...], snippets: {...}}
```

## 5. SerpApi-compatible local API (`auth="api_key"`)

`GET /v1/search.json` (alias `/v1/search`) accepts exactly SerpApi's query parameters (`engine`, `q`, ..., `api_key`
= Aira API key; `Authorization: Bearer aira_...` also accepted). It runs `service.run_search(source="api")` with the
owner's stored SerpApi key and returns the SerpApi JSON body with `search_metadata.aira = {id, cache_hit}` added.
Also `/v1/account.json`, `/v1/locations.json`, `/v1/searches.json` (list), `/v1/searches/{id}.json`,
`/v1/engines.json`. Errors use SerpApi's shape `{"error": "..."}` with the right HTTP status (401 bad key, 429
over the key's hourly limit, 400 bad parameters). Constant-time key comparison, per-key hourly limit, `last_used_at`
updated. No CORS headers (loopback only).

## 6. Careers (`/api/careers/*`, session auth)

```
GET  /api/careers/overview   -> { configured, saved_searches:[...], alerts:[...], recent_intel:[...], playbooks:[{id,title,engines:[...],description}] }
POST /api/careers/jobs/search {q, location?, gl?, hl?, chips?: [..], ltype?: 1, lrad?: km, uds?: str, next_page_token?: str,
                               remote?: bool, posted_within?: "today"|"3days"|"week"|"month", employment_type?: str, save_as?: str}
 -> { jobs: [Job], next_page_token, filters: [{name, parameter, options:[{text,value}]}], record_id, cache_hit }
Job = { key, title, company, location, via, posted_at, schedule_type, work_from_home: bool, salary: {text,min,max,currency,period}|null,
        description, highlights: [{title, items:[...]}], apply_options: [{title, link}], share_link, thumbnail, google_job_id,
        qualifications: [...], already_in_aira: bool, aira_job_key: str|null, match: {score, classification}|null }
POST /api/careers/jobs/import {job: Job}   -> { job: <Aira job row>, created: bool }   // goes through the existing job import + scoring path; duplicate-safe
POST /api/careers/company-intel {company, role?, location?}  -> Report
POST /api/careers/salary        {role, location?, company?}  -> Report
POST /api/careers/trends        {terms: [..<=5], geo?, window?} -> Report
POST /api/careers/interview-prep{company, role}              -> Report
POST /api/careers/travel        {kind:"flights"|"hotels", ...engine params} -> Report
POST /api/careers/learn         {skill, level?}               -> Report
Report = { id, kind, title, generated_at, sections: [{id, title, engine, status:"ok"|"error"|"empty", record_id, summary, items:[{title,subtitle,url,thumbnail,meta:{}}], error}],
           credits, cache_hits }
GET/POST /api/careers/alerts   {saved_search + interval}  -> job alert = saved Google Jobs search + monitor that lists NEW jobs, notifies, optional auto-import into Jobs
```
Each Report section is one engine call recorded in history with `source="intel"`, run concurrently (max 4) with
partial failure allowed. A report shows the credit cost before it runs (UI) and after (response).

## 7. Frontend

New files in `auto_apply_assistant/` loaded from `index.html` after `views-settings.js`:
`views-serp-kit.js` (shared components), `views-airaapi.js`, `views-playground.js`, `views-careers.js`.
CSS goes into `app.css` in clearly delimited blocks (`/* ==== Aira API ==== */`, `/* ==== Careers ==== */`).
Register views as the other modules do (`Aira.views.airaapi = ...`, `Aira.views.careers = ...`), add NAV entries and
icons in `app.js`, shortcuts `g` then `p` (Aira API) and `g` then `c` (Careers), and the CSP stays free of inline script.

### 7.1 Shared kit: `Aira.serp` (in `views-serp-kit.js`)

```js
Aira.serp = {
  paramForm(engine, initial, {onChange, compact}) -> { node, getValues(): object, validate(): string[], setValues(obj) }
      // auto-generated from the catalog entry: groups, required markers, enums as select/segmented, booleans, number steppers,
      // location picker bound to /api/airaapi/locations, date inputs, csv chip inputs, help text, "advanced" collapsed group
  optionsForm(initial) -> { node, getValues() }               // no_cache, output, async, zero_trace, json_restrictor
  jsonTree(value, {maxDepth, search}) -> Node                  // collapsible, searchable, copy path/value, virtualized for large arrays
  renderResult(record, {mode}) -> Node                         // mode: "visual"|"json"|"table"|"html"|"raw"; visual picks a renderer per result key:
      // organic_results, ai_overview, answer_box, knowledge_graph, related_questions, top_stories/news_results, jobs_results,
      // shopping_results, local_results/places, images_results, video_results, flights, hotels/properties, maps results,
      // finance, trends charts, scholar articles, patents, play/app store apps, reviews; unknown keys fall back to jsonTree
  codeSnippets(engine, params, {baseUrl}) -> { curl, python, javascript, ruby, php, go, java, csharp }
  engineBadge(engine) -> Node;  creditChip(record) -> Node;  statusBadge(record) -> Node
  locationPicker({value, onPick}) -> Node
  confirmCost(n, label) -> Promise<boolean>                    // shown before multi-search actions
}
```
Visual renderers escape everything (textContent only); links get `rel="noopener noreferrer"` and `target="_blank"`
(external result links are the point of a search console, unlike the Messages tab); images use `loading="lazy"`
and `referrerpolicy="no-referrer"`; remote image URLs are shown only for `https:`.

Implemented behaviour and additions beyond the signatures above (the kit is the reference when this list and a view disagree):

* `engine` arguments accept a catalog entry (`GET /api/airaapi/engines/{id}`), a summary row or, where only a name is needed, an engine id.
* `paramForm(entry, initial, {onChange, onSubmit, compact})` also returns `reset()`, `focusFirst()`, `getWarnings()` and `entry`.
  `getValues()` returns typed, non-empty params (booleans only when they differ from the engine default, dates in the engine's
  own format, csv as a comma string, unknown keys passed through). `setValues()` never fires `onChange`. Enter in a text field and
  Ctrl+Enter anywhere call `onSubmit` and dispatch a bubbling `sk-submit` event. Validation mirrors the backend: required, `requires_any_of`
  groups (a required parameter that is one alternative of a group is satisfied by its siblings), types, enum membership (skipped for
  `options_truncated`), csv membership, dates; "cannot be combined with" help text becomes a non-blocking warning.
* `optionsForm` also offers `allow_unknown` and `setValues(obj)`.
* `renderResult(record, {mode, snippets})` accepts `mode` `"visual"|"json"|"table"|"html"|"raw"|"metadata"|"snippets"`. Records without a body
  (error, running, pruned) get an explanation instead; `html` loads `GET /api/airaapi/searches/{id}?html=1` on demand when `html_available`
  and shows the page in `<iframe sandbox="" srcdoc>` after scripts, forms and event handlers are stripped and a deny-all CSP meta is added.
* Extra exports for the console views: `resultViewer(record, {mode, onMode, snippets, modes})` -> `{node, setMode, setRecord, mode}` (tabbed
  view that remembers the mode), `snippetPanel(engine, params, {baseUrl})` -> node with `update(engine, params)` (Aira or SerpApi-direct
  target), `codeBlock`, `lineChart`, `markdownNode`, `section`, `stars`, `segmented`, `toggleSwitch`, `copyText`, `copyButton`, `downloadText`,
  `kvList`, `thumb`, `chip`, `humanize` and `logic` (the pure functions the node tests cover).
* Needs from the glue: the CSP `img-src` must include `https:` or remote thumbnails fall back to a letter tile (the kit handles the block
  gracefully); the `/static/` handler must serve `views-serp-kit.js` (it does for any `.js` in the static directory).
* Tests: `node tests/js/serp_kit.test.mjs` (no dependencies; also run by `tests/test_serp_kit_js.py` when node is on PATH).

### 7.2 Aira API console (`views-airaapi.js`, `views-playground.js`)

Sub-navigation (`ui.tabs`, URL `#/airaapi?tab=`): `overview` (default), `playground`, `engines`, `history`,
`locations`, `monitors`, `account`, `keys`, `docs`, `status`.

* **Overview**: hero with plan and searches left (ring gauge), this-hour throughput, 30-day usage chart (bars,
  cached vs live vs errors), top engines, error/cache rates, recent searches, quick-start card when no key,
  engine-family shortcuts. Cloned from SerpApi's dashboard but with Aira-specific panels (saved searches,
  monitors, Careers shortcuts).
* **Playground**: engine picker (searchable, grouped by family, keyboard friendly), generated parameter form,
  options, live code snippets in 8 languages, Run (Ctrl+Enter), result tabs (Visual, JSON, Table, HTML,
  Raw, Metadata, Snippets), cost chip, cache indicator, save, pin, compare with previous run (diff view),
  share/export, history rail with re-run, URL-state (`#/airaapi?tab=playground&engine=google_jobs&q=...`).
* **Engines**: filterable catalog cards by family/category with param counts, docs link, "Open in Playground".
* **History / Archive**: filters, search, table with status/cache/duration/credits, detail drawer with every
  result view, rerun, diff two selected, export, bulk delete, pin.
* **Locations**: search box against locations API with map-free table, copy canonical name, "Use in Playground".
* **Monitors/Saved**: saved searches, monitor creation with cost estimate, last change summary.
* **Account**: connect/replace/remove the SerpApi key, plan and usage meters, extra credits, link to SerpApi billing
  (external, opens serpapi.com in a new tab: that is the product). **Keys**: create/revoke Aira API keys, usage.
* **Docs**: generated API reference of `/v1`, auth, params, errors, per-engine pages from the catalog with
  copyable snippets, plus a "migrate from SerpApi" guide (change base URL and key).
* **Status**: local server, SerpApi reachability and latency, throttle, queue.

Playground as built (`views-playground.js`, `Aira.renderPlayground(content, route)`):

* Layout: engine bar (searchable picker, favorites and recents as chips), then a request panel (Parameters, Options, Code, History, Saved tabs, Run bar with cost chip) beside the result panel (header with status, credits, cache state, toolbar, then `Aira.serp.resultViewer`). Stacked below 1100 px with a sticky Run bar.
* Defaults: last used engine, else `google_jobs`; `q` is prefilled from the profile role only for `google_jobs`; each engine remembers its last parameters in `localStorage` (`aira.playground.*`). `Fill example` writes values from the catalog; identifier and token fields get `REPLACE_WITH_*` markers.
* URL grammar (`Aira.playgroundLink(engine, params, options)` builds it; the page keeps it current with `history.replaceState`): `#/airaapi?tab=playground&engine=ID&<param>=<value>&o.<option>=<value>&record=<search id>`. Parameters whose names collide with `tab`, `engine`, `record`, `run` or start with `p.`/`o.` are written as `p.<name>`. `record` reopens a stored result after a reload; share links omit it. Opening a link never runs a search.
* Run sends `POST /api/airaapi/search`; errors keep `code` and `record_id` so a failed run is shown from its stored record plus an action (connect key, update key, billing). `Run fresh` and `Re-run` skip the cache; loading a run's request back never re-enables `no_cache`.
* Cost chip: 1 credit (`max_pages` for `extract` in crawl mode, confirmed first), or `Likely cached` when an identical live run is inside the cache window. Monitors always run live, so the monitor dialog estimates `30 days / interval` searches a month against the plan.
* Compare uses `GET /api/airaapi/diff` between the open result and any earlier JSON run of the same engine (same parameters preferred).
* Shortcuts: Ctrl/Cmd+Enter run, Ctrl/Cmd+K or `e` engine picker, Alt+1 to 5 left tabs; picker: arrows, Enter, Alt+F favorite, Esc.

As built (`views-airaapi.js`, CSS block `/* ==== Aira API console`, classes prefixed `aa-`; the file is the reference
when this list and a view disagree):

* **Plug-in contract.** `Aira.airaapi = {TABS, tabs, link(tab, query), playgroundLink(engine, params, options),
  loadEngines(), openRecord(id, siblingIds)}`. A tab is `async (content, ctx)` with `ctx = {route, query, tab, content,
  setQuery(patch)}`; `setQuery` rewrites the hash with `history.replaceState` so filters do not re-render the page. The
  Playground tab hands a child node to `Aira.renderPlayground(node, route)` (views-playground.js); `playgroundLink`
  defers to `Aira.playgroundLink`. The script loads after `views-serp-kit.js` and before `views-playground.js`.
* **Deep links.** `#/airaapi?tab=history&search={id}` opens the detail drawer (monitor notifications use it);
  `tab=docs&engine={id}` or `&page={start|auth|endpoints|parameters|output|errors|async|billing|sdks|migrate|coverage|ref-gl|ref-hl|ref-domain}`;
  `tab=engines&category=&family=&q=&hide=1`; `tab=history&q=&engine=&status=&source=&cache=0|1&pinned=0|1&from=&to=`.
* **Added route.** `GET /api/airaapi/platform` returns the platform reference sections (`dashboard_pages`, `plans`,
  `billing_rules`, `sdks`, `metadata_fields`, `account_api`, `locations_api`, `search_archive_api`, `status_api`,
  `async_and_webhooks`, `ui_ideas`) for the Docs and Account pages. Research `sources` stay on the server.
* **ApiError.** `app.js` `ApiError` now also carries `code` and `data` (the parsed error body), so views can react to
  `not_configured`, `invalid_api_key`, `rate_limited` and similar codes.
* **History.** Sorting is applied to the rows of the loaded page (the list route has no sort parameter); the page size
  goes up to 200. "Find by search id" opens an Aira id directly and scans stored records for a SerpApi id. Full-text
  search covers parameters, labels, engines and errors, not stored response bodies.
* **Not built, on purpose.** Outgoing webhooks (a local app posting to arbitrary URLs is a data-leak risk; async searches
  finish as a toast and monitors raise in-app notifications), XLSX export, `json_with_pixel_position`, SerpApi team seats,
  per-monitor budget caps. The Docs tab lists the status of every SerpApi dashboard page and every `ui_ideas` entry.

### 7.3 Careers (`views-careers.js`)

Tabs (`#/careers?tab=`): `jobs` (default), `company`, `salary`, `market`, `interview`, `travel`, `learn`, `alerts`.
Job search = best-in-class Google Jobs experience: query + location + radius + remote + posted-within +
employment type + dynamic filter chips from the response, infinite "load more" via `next_page_token`, job cards with
salary badge and apply options, a side drawer with description and highlights, "Save to Aira" / "Open application
page" (external link to the employer is the point here) / "Add alert". Company, Salary, Market, Interview, Travel
and Learn tabs render their `Report` sections as cards with "open full result in Playground" links and a cost
estimate shown before running. Everything degrades gracefully when no key is configured (clear setup panel, no
dead buttons).

## 8. Non-goals and honesty rules

* No scraping of serpapi.com and no use of the user's SerpApi login; only the documented API with the user's API key.
* Never fabricate search results. Without a key the console shows setup state; tests use a stand-in SerpApi server
  (`AIRA_SERPAPI_BASE_URL`) with recorded-shape fixtures that live under `tests/fixtures/serpapi/` and are labeled as fixtures.
* The Aira API key protects the local compat endpoint only; the server still binds to 127.0.0.1.

## 9. As built: backend deviations and additions

The backend follows sections 2 to 5. Where it differs or adds, this is the contract the frontend and Careers code use.

**Keys and secrets.** An account's own pasted SerpApi key takes precedence over `SERPAPI_API_KEY` (per-account beats
server-wide); `DELETE /api/airaapi/key` removes only the account's key and answers `409 {code:"conflict"}` when only the
environment key exists. `SecretStore` lives in `store.py` (`put/get/delete/exists`, ciphertext in `airaapi_secrets`); its
Fernet key is `airaapi.key` beside the database or `AIRA_SECRET_KEY`. The existing Google Jobs source and `/api/sources`,
`/api/status` use the same resolved key (`_serpapi_key()` in the glue). Aira API key failures use `code: "invalid_aira_key"`.

**Tables.** `airaapi_searches` gains `note` (empty-result message), `output` (`json|html|md`), `options_json`,
`api_key_id` (the Aira key that made an `/v1` call; drives per-key hourly counting) and `result_truncated` (body larger
than the result cap was returned but not stored). `airaapi_cache(key, value, expires_at)` holds the 60 s account cache,
the 24 h locations cache, the 30 s reachability probe and the prune marker. The accounts database gets
`airaapi_key_index(key_hash, user_id)`. Cache hits are stored as new records (`cache_hit = 1`, `credits = 0`, blobs
copied) and never extend the cache window.

**Catalog.** Singleton requirement groups whose parameters share a `requires_one_of` label (`yandex_images`
`text`/`url`, `naver_ai_overview`) are merged into one "at least one of" group; singleton groups on individually required
parameters (`google_maps_autocomplete`, `google_local_services`) stay required together. Enums with `options_truncated`
or no options accept free text; `google_sports` has no documented result keys. `validate_params(engine, params,
allow_unknown=False)` rejects `api_key`, `engine`, `output`, `async`, `no_cache`, `zero_trace` and `json_restrictor` as
parameters (they are run options), never injects defaults and rejects `location` together with `uule`. `credit_cost(engine,
options=None, params=None)`: 1, except `extract` in `crawl` mode which is `max_pages`.

**Service.** `run_search(..., limits=None, api_key_id="")`; options also accept `allow_unknown` and `use_cache` (rerun).
`output` may be `json`, `html` or `md`. Failures raise `SerpApiError` after recording the attempt (`error.record_id`);
console routes answer `{error, code, record_id}`. Extra helpers: `get_search`, `list_searches`, `update_search_meta`,
`delete_search`, `bulk_delete`, `refresh_running` (async polling), `rerun`, `diff_results/diff_searches`,
`export_search` (json, csv, xlsx, md, html, curl), `apply_retention`, saved/monitor CRUD, `run_monitor`, `run_due_monitors`,
`resolve_key`, `set_app_key`, `remove_app_key`, `cached_account`, `cached_locations`, `overview`, `usage_overview`.
Settings (all in `/api/settings`): `airaapi_cache_ttl_minutes` (0 to 10080, default 60), `airaapi_hourly_limit` (0 = off),
`airaapi_retention_days` (1 to 3650, default 30), `airaapi_max_records` (50 to 100000, default 2000),
`airaapi_result_cap_mb` (1 to 50, default 5).

**Routes added to section 4.** `GET /api/airaapi/searches` also takes `cache=0|1`; `DELETE /api/airaapi/searches`
takes `all=1` and `include_pinned=1` and refuses to run without `before`, `engine` or `all=1`;
`GET /api/airaapi/account` and `/status` take `refresh=1`; `GET /api/airaapi/searches/{id}?html=1` fetches the page from
the free Search Archive when it is not stored; `POST` create routes answer `201`; `DELETE /api/airaapi/keys/{id}`
answers `{ok, key}`. Record fields beyond 4.3: `options`, `note`, `output`, `result_truncated`, `main_result_key`.
Engine summaries carry `unlisted`, `deprecated` and `requires_any_of`. `examples` in engine detail has `curl, python,
javascript, ruby, php, go, java, csharp, aira_v1, params` plus `serpapi{...same languages, url}` for the direct call.
Monitor notifications link to `#/airaapi?tab=history&search={record_id}`.

**/v1.** Adds `/v1/searches/{id}.html`, `/v1/engines.json?full=1`, `allow_unknown=true`, and answers `output=html|md`
with the raw page. Search calls count toward the key's hourly limit whether cached or live.

**Server.** `Request` also carries `key_index` (add/remove key hashes in the accounts database), `api_key` (the
authenticated key row, never the secret) and `audit`. Monitors run from `_scheduler_loop` through
`_run_due_airaapi_monitors()` (one worker per account, skipped without a key). `log_message` now redacts `api_key=` in
request lines. Tests: `tests/test_airaapi_{catalog,client,store,service,routes}.py`, stand-in `tests/serpapi_stub.py` and
`tests/fixtures/serpapi/`.

## 10. As built: Careers backend

The Careers backend follows section 6. Where it differs or adds, this is the contract the Careers frontend uses.

**Modules.** `aira/airaapi/careers.py` (job normalization, duplicate detection, import, search, alerts, the
monitor hook, schema), `aira/airaapi/careers_intel.py` (the six report kinds, extractors, estimate, stored reports,
overview) and `aira/airaapi/careers_routes.py` (the `/api/careers/*` routes, loaded through `ROUTE_MODULES`). Tables are
created by migration 13 `careers` (`careers_alerts`, `careers_alert_jobs`, `careers_reports`).

**Job.** The spec's fields plus `posted_at_iso` (the relative `posted_at` converted against the time Google served the
results, rounded to the day), `salary.annual_min/annual_max` (only when the text states a period), `benefits`
(`health_insurance`, `dental_coverage`, `paid_time_off`), `extensions`, `importable` (false when the posting has no
usable URL, so the UI can disable Save) and `qualifications` taken from the "Qualifications" highlight block. `key` is
the same id the Jobs-tab Google source gives the posting. `match` is `{score, classification}` from a read-only score
preview, or `null` when the profile has no resume or the posting has no description.

**Search.** `POST /api/careers/jobs/search` maps `remote` to `ltype=1`, `posted_within` to `chips=date_posted:*` and
`employment_type` to `chips=employment_type:*`. Google deprecated both parameters, so the response carries `applied`
(each friendly filter and the parameter it became) and `warnings`; the dynamic `filters` (Google's `uds` tokens, or `chips`
values) are the supported refinement. The response also has `params`, `credits`, `note` (SerpApi's "no results" message) and
`save_error`. `dry_run` returns `{credits, cache_hit, params, applied, warnings}`.

**Import.** `POST /api/careers/jobs/import` returns `{job, created, duplicate_of, matched_by}`. `matched_by` is
`source` (same Google record), `url` (the share link or any apply link is a known posting URL; the importer is given the known URL
so both records merge into one job) or `content` (same title, company, location and description). The posting is rebuilt from
Google's fields: the client's `key`, `already_in_aira` and `match` are ignored. Salaries are stored annualized.

**Jobs tab.** `_search_google_jobs` routes the existing adapter through `careers.shared_search` (history source `careers`,
label `Jobs tab: ...`), so the Jobs-tab search and Careers share history, usage and cache. `GoogleJobsAdapter` gained
`use_json_getter`; its behaviour without it is unchanged.

**Reports.** Section ids: company-intel `overview, news, jobs, employees, offices`; salary `estimates, market,
company_pay (only with a company), community`; trends `interest, related_queries, related_topics, news`; interview-prep
`questions, experiences, videos, news, posting`; travel `flights` or `hotels`; learn `courses, videos, demand, advice`.
Sections add `params`, `why`, `cache_hit`, `credits`, `code` and, where computed, `stats` (jobs: listings, listings_with_pay,
annualized, currency, min/max/median annual) or `price_insights` (flights). Trend items carry `average`, `peak`, `latest`,
`change_pct` (mean of the last quarter of the window against the first, computed) and `points` for a sparkline. Worker
threads open their own connections to the same database file; a database that cannot be shared (in-memory) runs
sections one at a time.

**Alerts.** Minimum interval 60 minutes (one search per run), up to 10 alerts. Creating an alert creates the saved search
and the monitor; deleting it removes both; deleting either from the Aira API console is handled (the alert disappears with its
saved search, or lists as off and is recreated by `PATCH {"enabled": true}`). `service.run_monitor` gained a hook list
(`register_monitor_hook`): a hook that returns a summary takes over the run, which replaces the generic "monitor changed"
notification. `new_jobs` rows keep the Job as found; `already_in_aira` and `aira_job_key` are recomputed on read.

**Server.** `auto_apply_assistant.py` installs a `JobGateway` (`import_posting`, `preview_matches`) with
`careers.set_gateway`, adds the three tables to the account export and routes the Jobs-tab Google source through
`careers.shared_search`. Tests: `tests/test_careers.py` (102: parsing, normalization, mapping, duplicates, import, alerts,
the monitor hook, concurrency cap, partial failure, estimates, every report kind, and HTTP journeys including the scheduler path).

## 11. As built: Careers frontend

`views-careers.js` (CSS block `/* ==== Careers`, classes prefixed `cr-`) registers `Aira.views.careers` and `Aira.careers = {TABS, link(tab, query), openJob(job, ctx)}`. It loads after `views-playground.js`. Every tab starts from `GET /api/careers/overview` (setup state, saved searches, alert counts, playbooks). The NAV entry, the `g` then `c` shortcut and the CSP `img-src https:` change are described in section 12.

* **Deep links.** `#/careers?tab=jobs&q=&location=&remote=1&posted=today|3days|week|month&type=full_time|part_time|contract|internship&radius=` prefills the search and never runs it. `tab=company|salary|market|interview|travel|learn` accept the field names of their form (`company`, `role`, `location`, `terms`, `geo`, `window`, `skill`, `level`, `kind`, ...). `tab=alerts&alert={id}` opens that alert's new jobs (monitor notifications link there).
* **Jobs.** Friendly filters are sent as `remote`, `posted_within`, `employment_type`, `lrad` and `gl` (taken from the picked location's country code, cleared when the text is edited). Filters returned with the results are single-select for `uds` tokens and one-per-prefix for `chips`; a chip that maps to a friendly control moves that control instead. Choosing one re-runs the search at once. The cost chip comes from `dry_run` (debounced), and the last search comes back without a click only when that dry run says it is cached. Saved searches use `/api/airaapi/saved` (tag `careers`, engine `google_jobs`) with the same parameter mapping the server applies, so saving is free and works before any search.
* **Import.** Save to Aira sends the whole Job to `POST /api/careers/jobs/import` and writes `already_in_aira` and `aira_job_key` back; bulk save runs one import at a time and reports created, merged and failed counts.
* **Reports.** The plan comes from `dry_run` (rows with credits and cache state); the person can untick sections (sent as `sections`); more than 3 searches asks `Aira.serp.confirmCost` first. A failed section can be retried alone (`sections: [id]`, the result replaces it in the open report only). Previous reports come from `GET /api/careers/reports?kind=`. Links out: `Aira.airaapi.playgroundLink`, `Aira.airaapi.openRecord`, the Jobs tab, the alert dialog, and small recruiter and interview dialogs that post to `/api/recruiters` and `/api/interviews`. The company report also lists what Aira already holds for that company (`/api/jobs?q=`, `/api/recruiters?company=`).
* **Trend chart.** `items[].meta.points` are downsampled and carry no dates, so the x axis labels are spread evenly across the chosen window and the chart says so.
* **Alerts.** Create, edit, pause or resume (`PATCH enabled`), run now (one search), review and dismiss new jobs, open the search in Jobs or the Playground, delete.

## 12. As built: integration

* **Navigation.** `app.js` has one `NAV` list (`id, label, icon, group, key, quick`) that drives the sidebar (groups Workspace, Research, Account; the list scrolls on short screens), the phone bottom bar, the `g` then `<key>` chords and the shortcuts help. `Aira API` is `g p`, `Careers` is `g c`. The bottom bar keeps the four `quick` items; everything else is behind More, which opens the sidebar as a sheet, shows as the current place when the open section is not in the bar (`aria-current`), reports `aria-expanded`, shows a dot when a hidden section has a badge, and closes on Escape or after a link is chosen. `Aira.renderNav(counts)` merges counts, so the Careers widget can set the Careers badge (new alert jobs) without clearing the Dashboard's. Views set their own crumbs and `document.title` (`<tab> · Aira API · Aira`).
* **Dashboard.** `views-widgets.js` adds `Aira.dashboardWidgets()`, two panels appended below the existing grid and filled after the page draws: Aira API (searches today and in 30 days, cache hit rate, searches left, 14-day bars, monitor count, setup card without a key) and Careers (role search that opens `#/careers?tab=jobs&q=`, alerts with new counts, saved searches, recent reports, report shortcuts). A failed widget shows its own error and retry. "Updates" rows that carry an in-app `link` (monitor changes, alerts, messages) now open it and mark that notification read.
* **Key state.** One resolver (`service.resolve_key`) feeds `/api/sources`, `/api/status`, the Jobs-tab Google source, Careers, the console and `/v1`. The top bar has a `SerpApi ready/off` pill (links to Aira API > Account) that refreshes when a key is saved or removed. Settings > Integrations shows a "Google Jobs and Aira API" card from `GET /api/airaapi/overview` (connected with plan and searches left, no key, key rejected, key unusable) with Manage key, Open Aira API and Open Careers. The Jobs view reads `/api/sources` on every visit, links the not-connected hint to Aira API > Account, and the Google Jobs tab explains a missing key instead of showing an empty list.
* **CSP.** `img-src 'self' data: blob: https:` (`aira/security.py`) so result thumbnails render; everything else is unchanged.
* **Contrast.** The app tokens gained `--on-brand` and `--mark-ink` so text on the brand fills (buttons, hero, selected tabs, toasts, logo tile) is dark in the dark theme instead of white on mint; `.sk-count` uses `--ink-2`. A scripted audit of the landing state of every page at 1440 px in both themes (text on flat backgrounds; gradients, drawers and dialogs were not measured) found no text below WCAG AA.
* **Tests.** `tests/test_integration_shell.py` (navigation, chords, script order, widget and Settings wiring, CSP, and the key-state journey over HTTP).

## 13. As built: SerpApi dashboard parity (2026-10-23)

Four dashboard features that were partial or missing are now built. Migration 15 (`airaapi_parity`) adds the storage.

**Search inside stored results.** `airaapi_search_text(id, body)` is an FTS5 table (`unicode61 remove_diacritics 2`); if
SQLite lacks FTS5 it is a plain table searched with `LIKE`. `store.result_text()` collects the readable strings of a
result (titles, snippets, links, names) and skips `search_metadata`, `search_parameters`, pagination, `rectangle` and
`data:` URIs, capped at 200,000 characters. Every stored JSON result is indexed when it is stored: live runs, cache-hit
copies and finished async searches. Deletes, bulk deletes and pruning remove the index rows. The migration backfills the
newest 5,000 stored results. `GET /api/airaapi/searches?q=...&in_results=1` matches `q` against the index as well as the
metadata. User text never reaches FTS syntax: every word is quoted, all words must match, and the last one may be a
prefix. Matching records carry `match_excerpt`, with matched words between U+0002 and U+0003 (`store.SNIPPET_OPEN/CLOSE`).
The browser turns those into `<mark>` text nodes and never parses the excerpt as HTML. History has an "Inside results"
switch beside the search box, remembered per browser and kept in the URL as `inside=1`.

**XLSX and Markdown export.** `GET /api/airaapi/searches/{id}/export?format=xlsx|md`. Both use `service.result_table()`,
the same flattening as CSV. `aira/airaapi/xlsx.py` is a standard-library writer: one sheet, a bold frozen header,
inline strings (a leading `=` stays text, so no CSV-style prefix is added), and plain numbers written as numeric cells.
The Markdown export is a heading, the parameters and a table that keeps the 8 most-filled columns, with cells capped at
160 characters and pipes escaped.

**Monitor budgets and pause on error.** `airaapi_monitors` gains `monthly_budget` (0 = no cap, at most 100,000),
`budget_month`, `month_runs` and `pause_on_error`. Only runs that reach SerpApi count; failed runs do not. Once the
budget for the calendar month (UTC) is used up:
- a scheduled run is skipped and moved to the first of the next month (`run_due_monitors` reports `skipped: true`, and
  the summary reads "Monthly budget reached: ...");
- `POST /monitors/{id}/run` answers 400.

Raising the budget brings a waiting monitor back to its normal schedule. With `pause_on_error`, a failed run disables the
monitor and sends a "Monitor paused" notification. Monitor views add `monthly_budget`, `month_runs`, `budget_remaining`,
`budget_exhausted`, `budget_resets_at`, `pause_on_error` and `unbudgeted_monthly_searches`; `estimated_monthly_searches`
is capped by the budget. The card shows a budget meter, an editable budget, a "Budget used" state and the pause switch.

**X-Ray.** `GET /api/airaapi/searches/{id}/xray[?refresh=1]`, for successful `google` and `google_ads` searches that have
a SerpApi id. It reads `/searches/{serpapi_id}.json_with_pixel_position` from the Search Archive once, which uses no
search, and keeps it in `airaapi_searches.pixel_blob`. Later calls need no key. The response has:
- `viewport {width, height, page_height}`, `time_taken`, `error`;
- `parents` (from `pixel_position_information.parent_rectangles`);
- `elements` (`path`, `section`, `label`, `rect {x, y, width, height, hidden}`, up to 8 scalar `fields`), sorted top to
  bottom and capped at 500;
- `cached` and `html_available`.

Rectangles with non-numeric or non-finite values are dropped. In History detail, the X-Ray button opens a map of the page
at its real size, scaled to fit, with one colour per section. The map can be shown over the stored HTML page (sandboxed,
no scripts or network). A list with filter, arrow keys and per-section legend switches sits beside it. The detail panel
shows the JSON path (copyable), section, position, size, whether the element is above the fold, the `json_restrictor`
path and the element's fields. The stand-in server answers `.json_with_pixel_position` through `with_pixel_positions()`
and counts calls in `pixel_calls`.

**Still not built, on purpose.** Outgoing webhooks: a local app that posts data to arbitrary URLs is a data-leak risk. SerpApi team seats, plan changes,
credit purchases, profile, password and 2FA belong to the SerpApi account itself. Live `output=json_with_pixel_position`
from the Playground or `/v1` is also not built, because X-Ray reads pixel positions from the archive.

Tests: `tests/test_airaapi_parity.py`.

## 14. As built: Aira Jobs, Aira API's own engine (2026-10-23)

Aira API was a console and cache over SerpApi: every live search needed a SerpApi key. It now has one engine of its
own, **`aira_jobs`**, which Aira answers itself from free public job-board APIs. It needs no key and costs no credits.

**Catalog.** Engines carry a `provider`: `serpapi` (the 136 catalog engines) or `aira`. `aira_jobs` is defined in
`catalog/aira_engines.json` with `endpoint: "local"`. Its parameters are `q` (required), `location`, `country`,
`remote_only`, `employment_type`, `boards` (csv) and `num` (1 to 100, default 40). `credit_cost()` is 0 for `aira`
engines, and engine summaries include `provider`. `catalog.is_local()` and `catalog.local_engine_ids()` tell the two
apart.

**Service.** `_execute_search` answers local engines with `jobboards.LocalJobsClient(connection)` whatever client is
passed, so a search needs no key. It is still recorded, cached, indexed for History search and counted in usage, at 0
credits. The account-wide hourly cap, which protects the SerpApi plan, ignores local engines through
`count_live_since(exclude_engines=...)`; an Aira key's own hourly limit still counts every call.
`/v1/search.json?engine=aira_jobs` works with an Aira key and no SerpApi key. Output must be JSON, and async is
refused.

**Boards** (`aira/airaapi/jobboards.py`). How each board is searched, and the terms Aira follows:

| Board | How Aira searches it | Cache | Terms followed |
|---|---|---|---|
| Himalayas | `/jobs/api/search?q=&country=&employment_type=` | 12 h (its data refreshes daily) | apply through `applicationLink`; credit shown |
| The Muse | `/api/public/jobs` by categories mapped from the query, plus a "City, ST" location; 3 pages at most | 3 h | credit shown |
| Remotive | the whole feed, filtered locally | 6 h (at most 4 fetches a day) | credit and link back; not republished |
| Jobicy | `?tag=` with the longest query word | 2 h (a sync at most hourly) | credit; apply links go to Jobicy |
| Arbeitnow | page 1 of the feed (300 newest), filtered locally | 3 h | "do not abuse"; link back |

Fetches go through `airaapi_cache`, one at a time per board, with Aira's user agent, a 20 s timeout and an 8 MB cap.
Every key word of `q` must appear in the title. `remote_only`, the employment type and the location are filtered
locally:
- a city matches the job's place, or its country;
- a remote job matches when it is open to that country or worldwide.

Duplicates across boards are dropped, and results are newest first. Each item is in `google_jobs` shape and also
carries `aira_source`, `attribution` ("Jobs from Himalayas (himalayas.app)") and `posted_at_iso`. Ids are
`<board>-<id>`. One board failing is reported in `search_information.boards[].error`; all failing raises
`upstream_error`.

**Where it is used:**
- **Jobs**: a source `aira_jobs` (group `aira`, always connected) and an **Aira Jobs** tab. When LinkedIn is not
  connected it is the default source. A search runs through Aira API (history label "Jobs tab: ..."), and postings are
  imported with source `aira_jobs` via `jobboards.posting_from_item`; the description ends with the board credit.
- **Careers > Jobs**: a "Search with" switch, **Aira Jobs (free)** or **Google Jobs**. It defaults to Aira Jobs
  without a key, and the key panel appears only for Google. `careers.search_jobs(provider=...)`:
  - `build_aira_params` maps the form (Google-only filters are reported as ignored);
  - results show which boards answered, with links;
  - imports keep source `aira_jobs`, and duplicates are found by board id or URL;
  - saved searches keep their engine.

  "Add alert" in Aira mode explains that alerts need Google, and points to Auto-apply's review mode for free scheduled
  searches.
- **Auto-apply**: plan source `aira_jobs`, on by default in new plans. Board pages are not application forms. Before
  applying, `resolve_application_url` opens the board's job page (never Himalayas, which refuses scripted visits) and
  takes the employer's Greenhouse, Lever, Ashby or SmartRecruiters link. Otherwise the job ends as "Apply manually"
  with the board link.
- **Aira API console**: the engine card shows "Aira, free, no key"; Playground, History ("Save jobs to Aira"),
  monitors and `/v1` work with it.

**Not covered**: Google Jobs coverage (Aira Jobs is a different and smaller set of listings, mostly remote and tech).
Careers' company, salary, market, travel and learning reports are SerpApi-only. Google-specific filters (radius,
posted within, chips) do not apply.

Tests: `tests/test_jobboards.py` (recorded-shape board payloads, no network).
