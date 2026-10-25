# Security and Privacy

## Threat model

Aira runs on a personal machine, bound to `127.0.0.1`. The assets are the user's resume, profile (including optional sensitive fields), application history, documents, recruiter contacts, and the LinkedIn browser session. The main risks are cross-site requests from other local pages, credential leakage into logs or prompts, unauthorized disk access, and automation that acts without consent.

## Controls implemented

| Area | Control |
|---|---|
| Transport | Loopback-only bind; no remote exposure path is provided. |
| Accounts | scrypt password hashes (`n=2^14, r=8, p=1`), random session tokens stored as SHA-256 hashes, 7-day HttpOnly `SameSite=Strict` cookies. |
| Rate limiting | 8 failed sign-ins per 10 minutes per client+email, then 15-minute lockout; 600 API requests/minute per client (`429`). |
| CSRF | Mutations require same-origin (`Origin`/`Sec-Fetch-Site` validation); the SPA also sends `X-Requested-With: aira`. |
| Headers | CSP (`default-src 'self'`, no inline scripts, `frame-ancestors 'none'`), `X-Content-Type-Options: nosniff`, `X-Frame-Options: DENY`, `Referrer-Policy: no-referrer`, `Permissions-Policy`, COOP/CORP, `Cache-Control: no-store`. |
| XSS | The UI builds DOM nodes with `textContent`; no user data is injected as HTML. |
| Injection | All SQL uses parameters; route parameters are regex-constrained; static file serving is restricted to known extensions inside the assets directory. |
| Input validation | Every endpoint validates types, ranges, lengths, enums, URLs, time zones, and file types; documents are content-sniffed (PDF/PNG/JPEG/DOCX/JSON/UTF-8). |
| Encryption at rest | Documents are Fernet-encrypted with a key file created with owner-only permissions (POSIX) beside the database, or `AIRA_DOCUMENT_KEY`. SHA-256 is verified on every read. SQLite databases are not encrypted; use OS disk encryption. |
| Tenant isolation | Each account has its own SQLite database, document directory, and LinkedIn browser profile; document reads check ownership and never reveal other owners' records. |
| PII classification | Profile and ATS fields are classified public / personal / sensitive. Sensitive values are never sent to Ollama prompts (`PROMPT_SAFE_FIELDS`), never auto-filled into application packets without an explicit confirmed standard answer, and never inferred by scoring (dimensions report "missing information" instead). |
| Automation consent | No source exposes a submission adapter; auto-submit cannot be enabled. Email actions above the confidence threshold can be auto-applied only when the user turns it on, and offer/rejection outcomes always require confirmation. LinkedIn replies are sent only after an explicit Send confirmation, or automatically in conversations the user switches to Auto-reply. Automatic replies pass deterministic guardrails (`aira/messaging.py`): they never answer pay, visa or work authorization, personal identifiers, offers or contracts, background checks, or relocation; never include links, contact details, money amounts, or placeholders; wait 6 hours between automatic replies per conversation; stop at a daily limit (default 20) or when paused; send once per conversation state (unique index); and hold whenever Aira cannot tell who wrote the latest message. Held replies notify the user. Every send, hold, and failure is logged in `message_sends` and the audit log. |
| Secrets | `AIRA_DOCUMENT_KEY` and `AIRA_SECRET_KEY` are read from the server environment/.env only and never returned to the browser. The one secret the browser may submit is the SerpApi key (see the next row); it is never returned. `.env` is git-ignored; `.env.example` contains placeholders only. |
| SerpApi key and API keys | The SerpApi key is read from `SERPAPI_API_KEY` or pasted once, validated against `account.json`, stored Fernet-encrypted in `airaapi_secrets` (`airaapi.key` beside the database, or `AIRA_SECRET_KEY`) and scrubbed from client errors, response bodies, stored records, exports and the account cache; endpoints report only `configured`, `source` and `last4`. Redirects to other hosts are refused so the key cannot be forwarded. API keys (`aira_` + 40 random characters) are shown once and stored as SHA-256 hashes; lookups run through a hash index in the accounts database, compare in constant time, honour revocation and a per-key hourly limit, and rejected keys are rate limited per client (a valid key is never refused because of someone else's rejected ones). `/v1` accepts no cookies and sends no CORS headers; the console accepts no API keys. Request-line logging redacts keys however the query spells them (`api_key=`, `api%5Fkey=`, `%61pi_key=`, anything shaped like an Aira key). `ResolvedKey` hides the secret from `repr()`. On Windows the freshly created `airaapi.key` has its inherited access list replaced by owner + SYSTEM only (`icacls`), so a data directory on another drive does not expose it to other local users. |
| Remote images | The CSP allows `img-src 'self' data: blob: https:` so SerpApi result thumbnails and favicons can show. Nothing else is loosened: scripts, styles, fonts, frames and `connect-src` stay `'self'`, images are loaded lazily with `referrerpolicy="no-referrer"`, and only `https:` image URLs are ever rendered. The cost is that viewing a result lets the image host see your IP address; results are never required to render (a letter tile is shown if an image is blocked). Stored HTML from SerpApi's archive is shown in a sandboxed iframe with scripts, forms and event handlers stripped and a deny-all CSP of its own. |
| Logging | Structured logs redact tokens, passwords, keys, and email addresses; request logs carry correlation IDs instead of payloads; error responses give the request ID, not stack traces. |
| Audit | Append-only `audit_logs` for profile, settings, job, application, email, interview, recruiter, document, and account actions, each with actor, source, correlation ID, and bounded detail. |
| Data lifecycle | Full JSON export (`/api/account/export`), document retention setting with purge, soft-delete for jobs/recruiters/documents, and account deletion that removes databases, documents, and the LinkedIn profile. |
| File handling | 10 MB document limit, filename sanitization, atomic writes, malware-scan hook (`ScanHook`) with `not_scanned` status recorded when no scanner is configured. |
| Health | `/api/health`, `/api/ready`, `/api/metrics` for monitoring; readiness fails when the data directory is not writable. |

## Compatibility API (`/v1`) threat model

`/v1/search.json` and friends exist so scripts on the same machine can use Aira's cache and history instead of calling SerpApi directly. It is a second way in, so it is kept narrow.

| Risk | Mitigation |
|---|---|
| A web page in your browser calls `/v1` | Auth is an API key in `api_key=` or `Authorization: Bearer`, never the session cookie, and `/v1` ignores cookies. No CORS headers are sent, so other origins cannot read responses. |
| A browser session reaches key management through `/v1`, or a key reaches the console | Console routes accept only the session; `/v1` routes accept only Aira keys. Each is tested against the other. |
| Guessing or stealing a key | `aira_` plus 40 random URL-safe characters, shown once, stored only as a SHA-256 hash, compared in constant time; rejected keys are rate limited per client; a revoked key stops working immediately; every key has an hourly cap and a last-used time. Treat the secret like a password: query strings can end up in shell history, so prefer the Bearer header. |
| Spending SerpApi credits through a leaked key | The per-key hourly limit counts cached and live calls, the local concurrency cap (4) and optional account-wide hourly cap bound the burst, and the cache answers repeats for free. A leaked Aira key can spend searches but can never read the SerpApi key, which is never returned. |
| The SerpApi key leaking through errors, logs or redirects | Scrubbed in the client; redirects refused; request lines log through the redactor (`api_key=` values removed); the key is absent from records, exports and the database in plaintext (tests assert this over the whole transcript and every database file). |
| Cross-account access | `/v1` binds the request to the key's owner through a hash index in the accounts database, then uses that user's own database and SerpApi key. Deleting an account deletes its keys and index rows. |
| Server-side request forgery | Aira only contacts the SerpApi base URL (`serpapi.com`, or `AIRA_SERPAPI_BASE_URL` for tests); no request parameter chooses a host, and no outgoing webhooks exist. |
| Result content as an attack vector | Results are untrusted data: rendered with `textContent`, links open with `rel="noopener noreferrer"`, CSV export neutralizes formula cells (values, joined lists and column headers), HTML output is sandboxed. Stored pages served by `/v1` or `/searches/{id}.html` carry `Content-Security-Policy: sandbox; form-action 'none'; base-uri 'none'` next to the app policy, so a page opened in a tab cannot run scripts or post a form to Aira with the session cookie. Code samples generated on the server quote shell values with single quotes. |
| DNS rebinding | Every route (the dispatcher checks before routing, so the whole app, not only the API) refuses a request whose `Host` is not a loopback name (`127.0.0.0/8`, `localhost`, `::1`) with 403; extra names go in `AIRA_ALLOWED_HOSTS`. A rebound page is "same-origin" with itself, so the Origin check alone cannot stop it. |
| Rejected-key lockout as a weapon | Every local process and any web page that can fire a GET at `127.0.0.1` shares one client address. Only rejected keys count toward the 30-per-5-minutes lockout and only rejected keys are answered 429 while it lasts; a valid key keeps working. |
| Credit burn and stampedes | Identical cacheable searches that arrive together run once (single flight); the rest wait and read the cache. The per-key hourly limit and the optional account-wide limit count calls still in flight, so a burst cannot overshoot them. The keyless `/locations.json` relay is cached for an hour (256 entries) and limited to 30 uncached lookups a minute per client. |
| Unbounded growth | Cache hits copy the stored body, so the row cap (`airaapi_max_records`) is enforced on every insert (hits and failures too), trimming cache-hit copies before the live record that keeps the cache warm. Async searches nobody collected within two days are pruned. The locations cache keeps 2,000 rows. |
| Hostile stored data | Blobs are inflated with a hard 64 MB ceiling (a zlib bomb is refused with 400, not inflated); absurdly nested upstream JSON is rejected as an upstream error; diffs stop descending after 40 levels, and a monitor run survives an unreadable previous result instead of re-running (and re-billing) every tick. |

## Known gaps

- No TLS or reverse-proxy hardening because the supported deployment is loopback only.
- Registration is open to anything that can reach the loopback port, and all accounts share the one 600 requests/minute limiter keyed by client address, so one page can exhaust it for everyone.
- A request body nested more than ~1,000 levels deep makes `_read_json` raise `RecursionError`, which the dispatcher reports as 502 with Python's message instead of 400.
- The pasted SerpApi key is encrypted at rest, but its Fernet key file (`airaapi.key`) sits beside the databases, so a copy of the whole data directory exposes it; use OS disk encryption and encrypt backups.
- SQLite files and the document key are protected only by filesystem permissions; a dedicated secret manager is not integrated.
- No antivirus scanner is bundled; the scan hook records `not_scanned`.
- Session rotation on privilege change, device management, and password reset flows are not implemented.
- The LinkedIn automation inherits LinkedIn's terms-of-service risk; Aira never bypasses CAPTCHA, MFA, or rate limits, and the user signs in themselves.

## Operational rules

Run Aira only on a trusted machine. Do not tunnel or proxy it to a network. Keep `~/.linkedin-mcp/auto_apply/` (databases, `documents/`, `documents.key`, `accounts/`) out of shared drives and unencrypted backups. Rotate `AIRA_DOCUMENT_KEY` only by re-encrypting documents (export, change key, re-upload); losing the key makes stored documents unreadable.
