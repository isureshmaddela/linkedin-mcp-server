# Monosage: platform review

Date: 2026-11-06, on `main` at `359d684` (every feature branch is merged; the 8780 server runs this code).

This review covers what the system is missing and how to improve it, across Careers, the business apps, the everyday
apps, the hosted platform and the engineering underneath. It continues `docs/careers-maturity-scope.md`, which already
scopes Careers rounds 2 and 3; those items are listed here only by reference.

**Status (2026-11-07):** every P0 item in section 1 is fixed on `feature/platform-hardening`, together with most of
sections 2 to 7 and Razorpay payments (see PROGRESS.md, "Platform hardening and Razorpay"). Still open: P0-4's global
`img-src https:` (Social and Dating accept only uploaded photos, so the tracking attack is closed; the image cache for
other apps is not built), the structural items in section 7 (monolith split, one migration runner, one model client,
front-end bundling), media CDN and HLS, hosted households with parental consent, Social and Videos on Postgres,
passkeys, hosted Finish in browser, and the go-to-market items in round F (live Stripe and Razorpay test mode,
penetration test, legal review).

**Handle with care:** the fork (`isureshmaddela/linkedin-mcp-server`) is public. Publish this file only together with
the fixes it describes, never ahead of them.

How it was reviewed: four independent read-only audits (business apps, everyday apps, hosted platform, engineering),
a direct read of Careers, the GitHub Actions history, and the full test suite on `main`. The top findings were checked
again in the code; two were reproduced against in-memory databases.

## Verdict

The product is very broad (about 20 apps), and each app on its own is careful and well tested. It is ready for demos,
one household, and a small design-partner pilot. It is not yet ready for paying businesses, real families or public
social features, for four reasons that cut across every app:

1. **Money does not move.** Apart from plan billing (Stripe, only ever run against stripe-mock), every "earn" path
   records payments by hand: shop orders, invoices, bookings, courses, analyst subscriptions, creator payouts.
2. **Safety rests on a self-editable age.** A child profile can make itself an adult, so every age gate (Dating, mature
   videos, payouts, real-money Stocks) can be bypassed. The hosted build has no child or household model at all.
3. **Shared plumbing is missing.** There are no notifications outside an open tab, no inbound email, no shared
   customer record, no object storage or CDN for media, and no real-time channel. Each app works around these on its own.
4. **The safety net is off.** CI has never passed on the fork, dependencies carry 82 known vulnerabilities, coverage
   measures only the upstream package, and the monolith keeps growing.

**Recommendation:** pause new apps. Fix the P0 items, then build the shared services in section 2 once; they lift
every app at the same time. After that, pick one or two wedges to take to market (Careers, plus one business bundle
for Indian small businesses: Sites + Commerce + Bookings with UPI/Razorpay), and harden them before widening again.

## 1. Fix first (P0: security, privacy, money correctness)

| # | Problem | Evidence | Fix |
|---|---|---|---|
| P0-1 | A child profile can PATCH its own `age_group` to `adult`. Any adult can reset the owner's PIN without the old one. The unlock token never expires. | `aira/suite/life.py:334-354` (self-update allowed, `_clean` accepts `age_group`/`birthday`), `:378-397`, `:219-223`, `:232-236`; `life.js:85-88` | Only the owner or a guardian changes age or birthday, and age is derived from the birthday. Changing another person's PIN needs the owner's PIN. Unlock tokens expire, with an idle lock. |
| P0-2 | Real personal data (a full name, a Gmail address, a LinkedIn handle) is in a test fixture in the public repo, and it would ship in the sdist. | `tests/test_auto_apply_assistant.py:563-583` | Replace it with `example.com` fixtures. Decide whether to rewrite history (`git filter-repo`). |
| P0-3 | Dating: blocking or unmatching deletes the chat, so a report has no evidence left. | `aira/suite/social/dating.py:906-912` (`_end_match` deletes `so_messages`), `social/service.py:3846-3890` | Copy the chat and profile into the report before ending a match. Keep ended chats for a retention period that only moderators can read. |
| P0-4 | Profile photos may be any `https:` URL, so a dating profile can log every viewer's IP address and swap the image after review. | `social/service.py:1155-1180`; CSP `img-src https:` in `aira/security.py:12` | Accept only uploaded `media:` photos, or fetch an external image once on the server and store it. Drop `https:` from `img-src`. |
| P0-5 | Ad top-ups are credited before payment. Creators earn 55% of money that may never arrive, and a failed payment does not reverse the credit. | `monosage_cloud/billing.py:1318-1366`, `:1966-1976`; up to $10,000 per top-up (`ads/service.py:76`) | Credit only on `invoice.paid` (or charge at once with a PaymentIntent) and claw back on failure. |
| P0-6 | Creator payout details (bank/UPI) are stored as plaintext in one platform-wide SQLite file. | `videos/store.py:32`, `videos/service.py:257-265`, `ads/service.py:2124-2166` | Encrypt them now. Later, use Stripe Connect or Razorpay Route so the platform does not hold them at all. |
| P0-7 | Hosted defaults give everything away: new sign-ups get `all_access` with no end date, and the stand-in billing provider (which activates paid plans for free at a public URL) is the default and is always registered. | `monosage_cloud/config.py:103-104,119-121`; `billing.py:107-109`; `routes/billing.py:164-208` | Refuse to start with `standin` on a non-loopback or https base URL. Default sign-ups to a time-boxed trial. |
| P0-8 | Overage billing skips February. A subscription that renews on the 1st bills January again on 1 March (blocked by the flag), so February is never invoiced. The flag is set before the Stripe call, so a failed call loses the charge, and two overlapping ticks can bill twice. | `monosage_cloud/billing.py:2416` (`month_key(now - 31 * DAY)`), `:2444-2463`, `:2552-2563` | Bill on Stripe's period. Set the flag only after Stripe succeeds, atomically, and add idempotency keys. |
| P0-9 | Stripe webhooks, SCIM and `/oauth/token` share the sign-in brute-force limiter (30 requests per 5 minutes per IP, then a lockout), checked before the signature. A renewal burst locks Stripe out. | `monosage_cloud/gateway.py:129-131,452-458`; `routes/billing.py:108` (`auth="public"`) | Exempt signed machine routes from `auth_limiter` and give them their own limits. |
| P0-10 | Capability tokens are written to the logs: export download links (30 days, a full decrypted export) and invite links. | `monosage_cloud/gateway.py:204-216` logs `request.path`; redaction in `aira/observability.py:14-24` only matches `token=` | Mask token path segments before logging, and in the ingress access log. |
| P0-11 | A password reset does not revoke personal access tokens (up to 365 days) or OAuth refresh tokens. | `monosage_cloud/identity.py:537-556`; `oauth.py:946-1002` | Revoke the member's `oauth_tokens` on password change or reset. |
| P0-12 | Newsletters: an unsubscribe does not cancel mail already queued (reproduced), there is no `List-Unsubscribe` header, opt-in is single, and public forms have no bot check. | `aira/suite/kit.py:1078`; `marketing/service.py:1673,1118`; compare `aira/leads/mail.py:254` | Check suppression at send time, add RFC 8058 one-click headers, use double opt-in, add a honeypot. |
| P0-13 | Anonymous shop orders hold stock forever and email any address typed in: stock exhaustion, and a spam relay once auto-send is on. | `shop/service.py:735-764`, `:22-23`; Shop has no background task | Expire unpaid orders and return the stock. Cap orders per email/phone, add bot protection. |
| P0-14 | The shipped Kubernetes layout runs SQLite in WAL mode from 4 pods over a ReadWriteMany volume. WAL needs shared memory on one host, so this risks corruption. | `deploy/k8s/web.yaml:8`, `worker.yaml:8`, `storage.yaml`; `db.py:141`, `workspace.py:127` | For launch, run one host with `serve --worker`, or Postgres plus one writer per volume. The network policy must also allow 5432. |
| P0-15 | 82 known vulnerabilities in 14 locked packages (pyjwt, authlib, mcp, starlette, cryptography, pypdf, python-multipart). pypdf parses uploaded resumes. `cryptography` and `jwt` are imported but not declared. | Cloud workflow `audit` job; `uv.lock`; `monosage_cloud/sso.py:887` | `uv lock --upgrade`; declare `cryptography` and `pyjwt[crypto]`; make the audit job block merges. |

## 2. Shared services to build once (P1: these lift every app)

Most gaps below repeat in every app because a platform service is missing. Building each one once in `aira/suite/kit.py`
(or a new `aira/platform/`) and wiring the apps to it is the highest-leverage work in the codebase.

1. **Payments for businesses and creators.** One connected-accounts integration (Stripe Connect Express, and Razorpay
   Route plus UPI for India). Payment links carry the amount and the order or invoice reference, and a verified
   webhook calls the existing `mark_paid` functions. Creator payouts, refunds (as negative dated ledger lines),
   partial refunds, KYC and tax forms are handled by the provider. Today: one static `payment_link` per business
   (`kit.py:528`, `public.js:120`).
2. **Notifications.** Platform mail through the job queue with retries (not inline, `monosage_cloud/mail.py:92-98`),
   Web Push with a service worker and a web manifest (installable app), and later WhatsApp/SMS. Medicine reminders,
   stock alerts, helpdesk SLA breaches, booking reminders and Careers follow-ups only show in an open tab today
   (`life.js:240-285`). Missed-dose emails depend on the household having set up SMTP.
3. **Transactional email by default.** Order confirmations, tickets and booking confirmations wait for manual approval
   by default (`kit.py:878`, `auto_send: []`), and the setting is free text of internal ids (`views-hub.js:192`).
   Send transactional kinds automatically, give time-bound messages an expiry (`kit.py:1140`), and retry failures
   (`kit.py:1154`).
4. **Inbound email.** IMAP polling or a provider's inbound webhook, threading replies by the `[reference]` in the
   subject. It feeds helpdesk email-to-ticket, Careers Email updates (F19), Leads reply and bounce detection, and
   recruiter replies.
5. **One customer record.** A suite contacts table keyed by email or phone, which Sales, Marketing, Shop, Bookings,
   Service and Leads point to, with one timeline. Add a "Send to Sales" handoff. Today Marketing form leads have their
   own pipeline with "mark paid", so one customer can be counted twice in revenue.
6. **A correct ledger.** Store a currency on every document, total by currency (`kit.revenue` adds USD and INR
   together, `kit.py:757-800`), record refunds as negative lines in the refund month, and bucket months in the
   business's time zone. Add tax classes, tax-inclusive prices and the GST split (CGST/SGST vs IGST, HSN/SAC codes).
   Today there is one flat rate (`kit.py:525`).
7. **Public reachability for the local build.** Public pages (storefront, booking, invoices, courses) link to
   `http://127.0.0.1:8765` (`kit.py:1202`) and the server refuses non-loopback hosts (`auto_apply_assistant.py:9072`),
   so customers can never open them. Relay public links through Monosage Cloud, or provide a public-address setting
   with tunnel guidance, and warn in the hub.
8. **Media storage.** Object storage behind a CDN, HLS transcoding, stripping location metadata from videos (Social
   photos already strip EXIF, `photos.py:79-158`; videos keep `udta` GPS), and signed URLs that re-check visibility
   and takedowns when served (`videos_routes.py:449-456`). Academy lessons should use these files instead of
   unprotected external links (`academy/service.py:304`).
9. **Real time.** SSE or WebSocket fan-out in place of 1.5-2 second polling (chat, Dating, trivia) and the 20 second
   pulse.
10. **One model client.** Four separate provider paths exist today (`_ollama_json` with `model_provider.claude_json`,
    six clients in `sites/providers.py`, and `monosage_cloud/models.py`). One client gives every app the same
    instructions, skills, token budgets, safety rules for minors and telemetry. Add a per-plan `model_calls` budget:
    free sign-ups can spend the platform's Anthropic key without limit today (`models.py:500-501`).
11. **One erasure job.** "Erase person" running on profile removal, member removal and self-service deletion, covering
    Social, Videos, Health, Life, AI, Planner, third parties (leads, recruiter contacts), `mail_outbox` and billing
    payloads. Today a removed local profile is only archived (`life.py:357-376`).

## 3. Careers

Rounds 2 and 3 in `docs/careers-maturity-scope.md` stand: Finish in browser for CAPTCHA and sign-in holds (F14),
multi-step generic forms (F15), bulk actions (F16), notifications and an installable app (F17), AI research and
interview prep (F18), mailbox sync (F19). Added by this review:

- **Tailored resumes reach employers as plain text.** An approved tailored resume is uploaded as a Helvetica text PDF
  (`aira/resume_file.py:86-91`, `aira/pdf_text.py`), with no headings, layout or bold, and only WinAnsi characters.
  The employer sees a text dump instead of the person's designed resume. Render tailored resumes into a template
  (or into a copy of the uploaded DOCX), and show a preview before upload.
- **Finish in browser can reuse what exists.** The hosted sign-in broker already streams a private Chromium to the
  person over a WebSocket (CDP screencast, input replay, allowlisted navigation, audit; `monosage_cloud/streaming.py`).
  The same broker can carry the one CAPTCHA, sign-in or email-check step that F14 needs, in both builds.
- **Resume intelligence from `docs/requirements.md` is partly built.** Per-job missing skills exist (`aira/scoring.py`).
  An ATS-readiness score, missing-keyword analysis, a change summary per tailored version, a skill-gap view across all
  saved jobs, certification suggestions and STAR stories are not built (interview preparation is a notes box,
  `views-crm.js:62`).
- **Sources for the home market.** There are no Naukri, Foundit, Instahyre or Indeed adapters. India is reached only
  through Google Jobs, which needs a paid SerpApi key. Prefer official partner feeds or employer ATS boards. Do not
  scrape boards whose terms forbid it.
- **LinkedIn in the hosted build.** Running many candidates' LinkedIn sessions from data-centre IPs risks account
  restrictions, and the User Agreement forbids automation (Easy Apply is already opt-in with a warning). The legal
  position is open (`docs/multitenant.md:292,316`). Settle it before LinkedIn features are sold: keep LinkedIn in the
  person's own browser or local build, and keep hosted on boards, ATS forms and email.
- **Unverified live paths** (PROGRESS.md "Known Blockers"): a real LinkedIn reply has never been sent, only 2 real
  SerpApi searches have run, and there is no mailbox or calendar OAuth. Run one supervised live check of each before
  depending on them.

## 4. Business apps (Sales, Marketing and Leads, Service, Commerce, Courses & Events, Sites, Ads)

Beyond section 2:

- **Service:** no email-to-ticket (the "email" channel means typing the case in), and nobody is notified about new
  cases, chats or SLA breaches. The SLA tick only sets `overdue` (`service/desk.py:2254-2290`).
- **Bookings:** availability ignores staff calendars (`bookings/service.py:93-105`). Add Google/Outlook free/busy,
  `.ics` attachments, and deposits once payments exist.
- **Shop:** one flat delivery fee (`shop/store.py:421-422`), no zones or weights, a tracking link typed by hand, no
  returns flow, and refunds that do not restock.
- **Leads:** one email per lead and then it stops. There are no follow-up sequences that stop when a reply comes in,
  and bounces do not feed the do-not-contact list.
- **Sites:** contact forms show a thank-you or open `mailto:`, so submissions are lost (`sites/generator.py:72`).
  "Book" and "Shop" buttons are placeholders because the generator is never given the business's own public links,
  and there are no custom domains. Pass the business's links into the prompt, post forms to a Marketing form, and add
  custom domains with ACME certificates through the hosted proxy.
- **Pricing fit:** business apps are sold per candidate seat with a 5-seat minimum (`docs/pricing.md:69`), a
  recruiting unit that does not fit a one-person shop. Sites is missing from the business apps row (`:130`).
- **Hosted export** leaves out the company's business data: orders, invoices, customers and revenue in
  `_org/api.sqlite3` (`monosage_cloud/lifecycle.py:296-366`).
- **Small defects:** a newsletter just under 20,000 characters saves but can never be queued, because the footer pushes
  it past the limit (`marketing/service.py:1942`, `kit.py:994`). SMTP settings are committed before the host check
  rejects them (`kit.py:963`, `suite_routes.py:270-276`). The SMTP host is resolved twice, which leaves a DNS-rebinding
  window (`leads/mail.py:224-233,272-282`).

## 5. Everyday apps (Social and Dating, Videos, Health, Learn, Play, Planner, News, Life, AI, Stocks)

Beyond sections 1 and 2:

- **Hosted has no age gate or households.** Every hosted member is created as an adult (`life.py:462-472`), no birth
  date is collected at sign-up, and "household" means the whole organization (`family/store.py:191-193`). Build a
  consumer account type with guardian-managed child and teen members and verifiable parental consent before any
  consumer launch (COPPA, GDPR-K, and India's DPDP Act, which requires verifiable parental consent for anyone under 18).
- **Minors beyond the child tier:** adult strangers can friend-request teens (`social/service.py:632-635`), teens can
  post public videos and pick their own rating (`videos/service.py:633`), and automatic moderation is a six-word English
  blocklist (`social/service.py:73`). There is no image scanning or CSAM hash matching. There are no parental controls,
  although Life stages advises parents to use them (`journey/stages.py:483`). The Family public board shows children's
  names, and its link never expires (`family/service.py:1457-1497`).
- **Health data** (`cr_*` tables, life book, end-of-life wishes) is stored as plain text, and in the hosted build it sits
  in the employer organization's database. Missed-dose emails name the medicine. Health facts go to the organization's
  model without a health-specific consent. Guardians cannot manage a young child's medicines.
- **AI and minors:** the prompt only states the age group (`ai/service.py:1241-1246`). There are no crisis or
  self-harm rules and no output filtering for child and teen profiles (AI chat, Learn AI decks, Play AI quizzes).
- **Stocks:** paid buy/sell ideas with targets have only a generic disclaimer. There is no SEBI research-analyst or
  adviser registration field and no disclosure of the analyst's holdings. Quotes come from Yahoo's unofficial
  endpoint with a spoofed User-Agent (`stocks/market.py:6-8,44-46`); redistributing quotes needs a licence.
- **Scale:** Social (including DMs and Dating) and Videos (including the ads money ledger) are one SQLite file each,
  shared by every organization. They are outside tenant encryption, row-level security and `backup-org`
  (`monosage_cloud/hooks.py:89-99`). Search is `LIKE '%..%'` with no full-text index.

## 6. Hosted platform

Beyond section 1:

- **Billing has never touched a real Stripe account** and has no tax handling: no `automatic_tax`, no tax ID
  collection, no pinned `Stripe-Version` (`billing_providers.py:266-566`). Also: upgrades are recorded as paid before
  anyone pays (`billing.py:700-722`); two checkouts can both complete and double-bill (`:548-579`, `:1922`); there are
  no idempotency keys (`billing_providers.py:289-297`); webhook order is not checked (`:1966-1976`); and
  `subscription.updated` ignores portal changes (`:1979-1994`). Run the full lifecycle in Stripe test mode, then turn
  on Stripe Tax.
- **HTTP tier:** the standard-library threaded server with no connection limit or socket timeout, a backlog of 5,
  25 MB bodies read into memory, about 32 MB per scrypt check (`crypto.py:62-72`), and rate-limiter keys that are
  never evicted (`aira/security.py:52-65`). Run it behind a production server with worker, connection and timeout
  limits, and drain cleanly on SIGTERM.
- **Job queue fairness:** the queue reads the first 500 ready jobs, so one organization's backlog hides every other
  organization's jobs (`jobqueue.py:128-131`). All job kinds share one per-org cap. The scheduler opens every
  workspace file on every tick, under a lease that is never renewed (`scheduler.py:42-69`, `lifecycle.py:1052-1072`).
- **SSRF through redirects:** the organization's model endpoint and SSO discovery are checked only when saved, then
  called with `urlopen`, which follows redirects (`models.py:565-580`, `sso.py:218`). Use the pinned connection that
  webhooks already use.
- **Operations:** metrics are per-process JSON behind an operator's MFA cookie (`routes/platform.py:660-667`), with no
  scrape endpoint, tracing, alerting or scheduled backups. `mail_outbox` keeps reset and invite links forever
  (`mail.py:99-114`).
- **Identity:** TOTP is the only second factor (no passkeys). There is no idle session timeout, no sign-up abuse check,
  and no way to recover a sole owner who lost their MFA device and recovery codes.
- **Developer platform:** service apps of every organization share one rate-limit bucket (`oauth.py:990-1001`,
  `gateway.py:403-411`), every bearer request writes `last_used_at`, and third-party apps cannot be used by other
  organizations (`oauth.py:490-492`).
- **Assurance:** no penetration test, DPA or legal review yet.
- **Solid already:** default-deny route policy with a test that fails on an unlisted route, the cross-tenant sweep,
  per-org keys with KMS options, MFA, OIDC/SAML and SCIM, owner-approved support access, the hash-chained audit log,
  signed SSRF-checked webhooks, and digest-pinned containers.

## 7. Engineering

- **CI has never passed on the fork** (15 of 15 `CI` runs failed; the latest on `main`, 2026-11-06, has CI and Cloud
  failing). Causes: `ubuntu-latest` is now 26.04, which Playwright refuses; the CI job never installs Chromium; it
  does not install `--extra cloud`; and pre-commit fails on formatting, `ty`, and the multi-document YAML. Pin
  `ubuntu-24.04`, install Chromium with `--with-deps`, add the extra, and require green checks before merging.
- **Repo identity:** `pyproject.toml` is still `linkedin-scraper-mcp` with upstream metadata, but the wheel includes
  `aira`, `monosage_cloud` and `aira_cloud`. It leaves out `auto_apply_assistant.py`, its static files and
  `docs/legal`, which `monosage_cloud` imports or reads, so an installed wheel cannot run the hosted build.
  `release.yml` would publish on a version bump. README and AGENTS.md are upstream's (AGENTS.md is what CLAUDE.md
  loads), and `README.md:32` says Monosage never submits applications, which is no longer true. Give Monosage its own
  distribution, or split the repository.
- **The monolith:** `auto_apply_assistant.py` has 12,526 lines, 282 functions and a 3,198-line `RequestHandler` with
  161 routes. It holds whole domains (accounts, LinkedIn workers, inbox cache, about 2,000 lines of email apply).
  `monosage_cloud` reaches into its private thread-local internals (`_connect` ×20, `_request_context` ×15). There are
  two route registries, with dispatch by a linear regex scan over about 1,344 routes. Move handlers onto
  `aira_route` per domain, and extract a shared runtime module (connection factory, request context, route table).
- **Hot path:** every authenticated request writes to `accounts.sqlite3`: schema checks, a commit, and a session purge
  (`_auth_connect`, `auto_apply_assistant.py:368,981`). The accounts database is not in WAL mode. Migrate once at
  startup and make the session lookup read-only.
- **Migrations:** three mechanisms, and the versioned one is not immutable: newer versions call each module's live
  `ensure_schema()` (`aira/schema.py:546-595`). There are 7 diverging `_add_columns` copies, and some do not handle the
  duplicate-column race. Use one runner with per-app versions and frozen DDL.
- **Tests:** this review's local run on `main` (Windows, `-n 6`): 3,751 passed, 2 failed, 28 skipped, in 47 minutes.
  Both failures are timeouts under load (`test_app_switcher_ui.py` deep links and `test_cloud_broker.py` time limit),
  and both pass when run alone. Locally the suite is healthy, so CI's failures come from the CI setup. There are
  about 3,700 tests (81 files drive Chromium) with no `browser` or `slow` markers. 60 files read the clock
  and only 11 freeze it. Two tests assume a `D:/linkedin` path, which exposes a real bug: importing a Windows install
  on a Linux host fails (`monosage_cloud/import_local.py:116`). Coverage measures only `linkedin_mcp_server`
  (`.coveragerc`), so Monosage's coverage is unknown. The JS tests do not run in CI.
- **Lint and types:** `ruff check` passes only because no rule set is configured. 31 files are not formatted, and
  pre-commit pins ruff 0.9.10 while the lock has 0.15.12. `ty` reports 1,715 diagnostics (1,688 in tests, 10 in
  production code). PROGRESS.md's "ruff and ty clean" is out of date.
- **Duplication:** three password-hash implementations, four model-provider paths, two SMTP senders, and 16 `now`
  helpers.
- **Front end:** every page loads 46 scripts and 21 stylesheets (3.1 MB of JS, about 0.8 MB gzipped) whatever app is
  open, plus a 2.3-2.5 MB translation table applied by a MutationObserver. Load each app's view on demand, add an
  esbuild bundle with hashed immutable assets, split translations per app, and add axe checks to the browser tests.
- **Hygiene:** `.dockerignore` does not exclude `.aira-cloud/` (1 GB, including `secrets/master.key`), `.aira-test*/`,
  `.env.cloud` or `*.pdf`, so `docker build .` sends them as build context (the images stay clean). Three commits
  ("all changes", "latest chages", "new changes") add 108-177K lines each, which cannot be reviewed. There are 12+
  stale branches.

## 8. Suggested rounds

| Round | Content | Why first |
|---|---|---|
| A: Safety net (days) | P0-2 (PII), P0-15 (dependencies), green CI on `ubuntu-24.04` with Chromium and the cloud extra, coverage for `aira` and `monosage_cloud`, test markers and frozen clocks, `.dockerignore` | Every later change is checked automatically |
| B: P0 fixes (1-2 weeks) | P0-1, P0-3 to P0-14 | Security, privacy and money correctness before real users |
| C: Shared services (3-5 weeks) | Section 2: payments (Stripe Connect and Razorpay/UPI), notifications (queued mail, Web Push, PWA), transactional email by default, ledger currencies and GST, one customer record, inbound email, public reachability | Lifts every app at once |
| D: Careers round 2 | F14 to F18, plus the formatted tailored resume | The core product, already scoped |
| E: Structure (ongoing, alongside) | Split the monolith by domain, one migration runner, one model client, Monosage's own package and README, Social and Videos to Postgres, a production HTTP server | Keeps speed as the code grows |
| F: Go to market | Stripe test-mode lifecycle, then live with tax; penetration test; DPA and privacy policy; LinkedIn position; design-partner pilot on one host | Taking money safely |

Consumer launch (Social, Dating, kids) waits for hosted households with parental consent, real moderation and image
scanning. Until then, keep those apps local or for adults only.

## 9. Corrections to existing docs

- `docs/multitenant-operations.md:19-20` and `docs/multitenant-security.md:24,28` say there is no HSTS, remote sign-in
  streaming or KMS; all three exist (`gateway.py:197-203`, `streaming.py`, `kms.py`).
- `PROGRESS.md:197` says the PostgreSQL adapter is not built; it is (control plane only).
- `docs/architecture.md` says the server module only wires routes; it holds whole domains.
- `README.md:32`: "Aira never submits applications or sends messages for you" is no longer true (Auto-apply, apply by
  email, guarded auto-replies).
- PROGRESS.md's repeated "ruff and ty clean" and "CI" claims do not match the GitHub Actions results.
