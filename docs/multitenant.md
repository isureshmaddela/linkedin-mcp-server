# Aira hosted (multi-tenant) build: as-built specification

This describes what `aira_cloud/` does today. It is written from the code, not from the plan. The plan is the scope document ("Aira Career Copilot — Multi-Tenant SaaS Scope"); where the code differs, [Differences from the scope document and known gaps](#differences-from-the-scope-document-and-known-gaps) says so.

The local single-user build (`auto_apply_assistant.py`, `127.0.0.1:8765`) is unchanged and keeps its own documentation ([architecture.md](architecture.md), [api.md](api.md), [security.md](security.md), [runbook.md](runbook.md)). The hosted build reuses its domain code and replaces the shell around it.

Related documents: [multitenant-operations.md](multitenant-operations.md) (running it), [multitenant-security.md](multitenant-security.md) (controls and threat model), [pricing.md](pricing.md) (plans), [hosted-api.md](hosted-api.md) (API reference), [legal/](legal/terms.md) (templates, not reviewed by counsel).

## 1. What it is

A service that many organizations (outplacement firms, staffing agencies, universities, employer career centres) use at once. Each organization is a tenant with its own members, roles, dashboard, policy and data. The organization's employees or clients are the candidates; its administrators and coaches run and oversee their job search and automatic applications. Plans decide what each organization may use, and the limits are enforced by the server on every request and in every background job.

The product logic is the existing domain layer in `aira/` (job search, scoring, form engine, applications, interviews, documents, Aira API). The hosted shell adds: identity and tenancy, role-based access, a subscription and entitlement layer, an organization dashboard, a platform console, a tenant-fair queue with workers, and an HTTP gateway that binds each request to one organization before any domain code runs.

## 2. Architecture as built

```
 browser (SPA: app.js + views-*.js + cloud-*.js)          SerpApi-compatible clients (Aira key)
        |                                                         |
        +------------------------+--------------------------------+
                                 v
                 aira_cloud.gateway.Gateway   (pure HttpRequest -> HttpResponse)
                 host check, rate limits, session, gates, policy, entitlements, tenant bind
                   |                     |                       |
        control-plane routes      local routes via shim     /v1 via organization key
        aira_cloud/routes/*       aira_cloud/policy.py      aira_cloud/v1.py
                   |                     |
                   v                     v
        control plane DB          candidate workspace (SQLite, one per candidate)
        control.sqlite3           tenants/<org_id>/<candidate_id>/workspace.sqlite3
                   ^                     ^
                   |                     |  aira/hosting.py hooks (documents, key, model, limits, ...)
        queue_jobs, leases                |
                   v                     |
        worker pool (aira_cloud.worker) --+--> shared domain code in aira/ and auto_apply_assistant.py
        scheduler.py, lifecycle.tick()
```

| Piece | File | Role |
| --- | --- | --- |
| Configuration and clock | `aira_cloud/config.py` | Environment-driven settings; an injectable `Clock` so lifecycle tests can move time |
| Control-plane database | `aira_cloud/db.py`, `schema.py` | SQLite (WAL), numbered migrations, `org_id` on every tenant table |
| Secrets | `aira_cloud/crypto.py` | Per-organization data keys wrapped by a master key; passwords (scrypt), TOTP, recovery codes |
| Identity | `aira_cloud/identity.py` | Organization sign-up, email verification, sign-in, sessions, MFA, invitations, password reset, operators |
| Authorization | `aira_cloud/rbac.py` | Roles, the permission matrix, coach scopes, the 404-for-foreign-ids rule |
| Tenancy | `aira_cloud/tenancy.py` | Policy, members, seats, coach assignments, pause switches, consent, onboarding, offboarding |
| Plans and limits | `aira_cloud/plans.py`, `entitlements.py` | Plan catalog, `Entitlements`, feature flags, atomic usage counters, `402 plan_required` |
| Billing | `aira_cloud/billing.py`, `billing_providers.py` | Subscription state machine, checkout, proration, dunning, webhooks; stand-in and Stripe providers |
| Lifecycle | `aira_cloud/lifecycle.py` | Over-limit tracking, exports, closure, deletion, data-subject requests, the scheduler tick |
| Dashboard | `aira_cloud/dashboard.py` | Organization, coach and candidate dashboards, the held-question queue |
| Platform console | `aira_cloud/platform_admin.py` | Tenants, plans, flags, support access, announcements, health, abuse controls |
| Workspaces | `aira_cloud/workspace.py` | Paths, binding a thread to a candidate, snapshots, export, deletion |
| Queue and workers | `aira_cloud/jobqueue.py`, `worker.py`, `scheduler.py`, `gate.py` | Tenant-fair leasing, job handlers, due-work discovery, the run-start gate |
| Seams in shared code | `aira/hosting.py`, `aira_cloud/hooks.py` | Hooks the domain code consults; all `None` in the local build |
| Gateway | `aira_cloud/gateway.py`, `webio.py`, `policy.py`, `route_hooks.py`, `redaction.py`, `v1.py` | The request pipeline |
| Model gateway | `aira_cloud/models.py` | Hosted model, Ollama, or an organization's own endpoint |
| Storage | `aira_cloud/storage.py` | Local disk or S3-compatible object storage for encrypted documents |
| Mail | `aira_cloud/mail.py` | Outbox in the database; SMTP when configured |
| Audit | `aira_cloud/audit.py` | Hash-chained append-only logs, per organization and for the platform |
| Entry point | `aira_cloud/__main__.py`, `demo.py` | `serve`, `worker`, `tick`, `migrate`, `operator`, `seed-demo`, `check` |

One process can be the web tier, a worker, or both (`serve --worker`). The web tier is stateless apart from in-memory rate-limit counters and the 60-second entitlement cache, so several replicas can share one control-plane database and one tenants directory, subject to the limits of SQLite (see the gaps section).

## 3. Tenancy model

| Entity | Stored in | Notes |
| --- | --- | --- |
| Organization | `organizations` | name, slug, region, status (`active`, `suspended`, `deleted`), policy and branding JSON, pause state, closure request |
| Member | `members` | one organization per member; the same email in two organizations is two members, and a sign-in that matches both asks which one (after the password is proven). Unique per organization only among members not removed |
| Candidate workspace | `workspaces` + a SQLite file | one per candidate member |
| Coach assignment | `coach_assignments` | coach, candidate, scope `view`, `act` or `approve` |
| Subscription | `subscriptions`, `subscription_events` | plan and version, seats, period, state, overrides, over-limit list |
| Usage | `usage_counters` (per organization and calendar month), `usage_daily` (per candidate and day) | |
| Audit | `audit_logs` (organization), `platform_audit_logs` | hash chained, append only |

### On disk

```
<data root>/control.sqlite3                      control plane
<data root>/secrets/master.key                   local master key (0600) unless AIRA_CLOUD_MASTER_KEY is set
<data root>/tenants/<org_id>/<candidate_id>/workspace.sqlite3
<data root>/tenants/<org_id>/<candidate_id>/<document_id>.bin      encrypted document (local storage)
<data root>/tenants/<org_id>/<candidate_id>/linkedin/profile/      the candidate's browser profile
<data root>/tenants/<org_id>/_org/api.sqlite3    the organization's Aira API workspace (history, cache, keys)
<data root>/tenants/<org_id>/_exports/<id>.zip   exports
```

With S3 storage, document objects are `tenant/<org_id>/<candidate_id>/<document_id>.bin`. One organization is one directory tree (plus its object prefix), so it can be exported or deleted as a unit.

### Isolation rules and where they are enforced

1. `org_id` is a mandatory column on every tenant table (`schema.py`); services take it from the authenticated principal, never from a body. A test in `tests/test_cloud_tenancy.py` deletes an organization and checks that no row with its `org_id` survives in any table outside the retained set, discovering tables from the schema rather than from a list.
2. A candidate-level read or write goes through `Authorizer.require(principal, permission, candidate_id=...)`. A candidate that does not exist in the principal's organization is `404 not_found` for every role, so identifiers cannot be probed across tenants; one that exists but is not permitted is `403`.
3. Documents are encrypted with the organization's own data key and stored under the tenant prefix (`workspace.py`, `storage.py`, `crypto.SecretManager`).
4. Background work is queued with `org_id` and `candidate_id`; the worker binds that tenant before touching anything (`worker.py`), and re-checks the gate when the job starts.
5. LinkedIn browser profiles live under the candidate's own directory (`ControlWorkerRegistry` in `workspace.py`), never shared.
6. Deleting an organization (`lifecycle.delete_org`) removes its workspaces, documents, keys, browser profiles, queue items and every table row with its `org_id`, and keeps the billing record (`invoices`, `billing_events`, `billing_credits`, `subscription_events`, the `subscriptions` and `organizations` tombstone rows) and an `audit_stubs` row for seven years.

On PostgreSQL (`AIRA_CLOUD_DB=postgres`) the database enforces a second line of defence: row-level security on every table with an organization column, under a non-owner role, with the organization taken from the request or job scope (see [multitenant-operations.md](multitenant-operations.md#postgresql-control-plane)). On SQLite, the default, isolation of control-plane rows rests on the services and the tests.

## 4. Roles and permissions

Seven roles exist. Six belong to an organization (`owner`, `admin`, `coach`, `candidate`, `billing`, `auditor`); platform operators (`support`, `platform_admin`) live outside any organization. Permissions are named strings checked by `Authorizer` in `rbac.py`; the UI only hides what the API would refuse.

| Permission | Owner | Admin | Coach | Candidate | Billing | Auditor |
| --- | --- | --- | --- | --- | --- | --- |
| `org.settings` (policy, branding) | yes | yes | | | | |
| `billing.manage` | yes | | | | yes | |
| `usage.read` | yes | yes | | | yes | yes |
| `members.manage` | yes | yes (not owners) | | | | |
| `coaches.assign` | yes | yes | | | | |
| `automation.pause.org` | yes | yes | | | | |
| `integrations.manage` / `integrations.read` | yes | yes | | | | read |
| `dashboard.read` | yes | yes | own candidates | own | usage only | yes |
| `export.org` | yes | yes | | | | yes |
| `audit.read` | yes | yes | | | | yes |
| `support.approve`, `org.close` | yes | | | | | |
| Candidate level: `candidates.read`, `documents.read` | all | all | assigned, from `view` | own | | all |
| `candidates.write`, `search.run`, `held.answer` | | | assigned, from `act` | own | | |
| `automation.pause.candidate` | yes | yes | assigned, from `act` | own | | |
| `applications.submit` | | | assigned, `approve` | own | | |
| `linkedin.manage`, `export.own`, `sensitive.*`, `consent.manage` | | | | own only | | |
| `messages.read` / `messages.send` | | | only if policy `coach_messages` is `view` / `act` | own | | |

A coach's effective scope is the assignment scope capped by the plan's `coach_max_scope`; a downgrade narrows existing assignments at once. A support grant gives an operator the granted role (`auditor` or `admin`) in that tenant but never `billing.manage`, `support.approve`, `org.close`, `linkedin.manage`, anything sensitive, or messages.

Sensitive profile fields (work authorization, visa, demographics, salary) are entered and confirmed by the candidate only. A coach, administrator or auditor learns that a field is set, never its value, unless organization policy `coach_sees_sensitive` is on **and** the candidate granted the `sensitive_access` consent **and** the viewer is an assigned coach (`rbac.can_see_sensitive`, `redaction.py`). Even then a coach cannot write them (`route_hooks.profile_write_inside`, `answers_write_pre`, `run_answer_inside`).

## 5. Plans and entitlements

[pricing.md](pricing.md) lists every plan and limit. Enforcement:

1. The subscription resolves to an `Entitlements` object (plan version limits, plus per-organization overrides, plus operator feature flags, plus the limits implied by the subscription state) and is cached per organization for 60 seconds (`EntitlementService.resolve`); changes made through the services invalidate it at once in the same process.
2. Route policy names a feature; a missing one is `402 plan_required` naming the cheapest paid plan that includes it (`EntitlementService.require_feature`).
3. Counted limits (applications per candidate per day and per organization per month, cover letters, API searches) are incremented with one conditional `UPDATE` inside one transaction (`UsageService.reserve`), so two concurrent actions cannot both take the last unit. An application reserves its unit before the browser runs and gives it back unless the run submitted. Storage, seats, search plans, monitors and Aira API keys are checked against current counts.
4. Background jobs re-check when they run: `Gate.automation_block` and `Gate.decide` run at job start and again inside the apply step, so a downgrade, pause, lapsed payment or revoked consent takes effect without waiting for a reschedule.
5. Operators can override a plan per organization (`subscriptions.overrides`), publish new plan versions, schedule migrations, and turn features on or off per plan, per organization or by percentage. A global flag switched off is a kill switch that overrides everything.

## 6. Subscription lifecycle

States: `trialing`, `active`, `past_due`, `read_only`, `cancelled`, `deleted`. Allowed moves are fixed in `billing.TRANSITIONS`. Every move writes `subscription_events` (and the organization audit log) with a trigger type: `person`, `webhook`, `scheduler` or `operator`.

| Event | Effect |
| --- | --- |
| Sign-up | organization, owner, `trialing` for 14 days; the owner must verify their email first |
| Checkout completed (webhook) | `active` at once; plan, seats, period and provider ids written; entitlements applied |
| Upgrade or more seats | applied at once; prorated charge (quote shows it); the period does not restart |
| Downgrade or fewer seats | scheduled for the period end; at that time the plan changes and, if the organization is over the new limits, automation stops until it is within them (`over_limits`); it resumes by itself when removals or deletions bring it within |
| Payment failed (webhook) | `past_due`; automation stops at once; reads and writes stay open; owner and billing contacts are emailed |
| 14 days after the first failure | `read_only` (data readable and exportable, no changes); owner and billing contacts emailed |
| 60 days after the first failure | deletion is scheduled for 30 days later; one last notice |
| Deletion date | organization deleted (see section 13) |
| Payment received | back to `active`; dunning fields cleared |
| Cancel | service continues to the period end; then `cancelled` with a 30-day export window; then deletion |
| Trial ends without a plan | `cancelled`, a 30-day window, then deletion |
| Enterprise | an operator activates it with seats, overrides and an invoice; there is no self-serve checkout |

Webhooks are verified (Stripe's `t=<unix>,v1=<hmac_sha256>` scheme, five-minute tolerance), recorded by event id and processed once. The stand-in provider signs its own events the same way, so the code path is the same in development and production.

Billing periods are 30 days. Usage counters use calendar months (UTC).

## 7. The request pipeline

`Gateway.handle` (`gateway.py`) runs, in order: host check (`AIRA_CLOUD_ALLOWED_HOSTS`, loopback always allowed) → client rate limit → route match (control-plane routes first, then the local build's routes) → same-origin check for mutating requests → session (`aira_session` for members, `aira_operator` for operators; an operator reaches a tenant only through an approved support grant) → verified-email and MFA gates → organization and member rate limits (an operator can lower an organization's limit) → subscription state (`writes` routes are refused while read-only) → permission for the named candidate → plan feature → organization policy (source allowed; LinkedIn allowed and the candidate's own opt-in) → per-kind hook (limits, metering, sensitive-field rules) → bind the tenant → run the handler → redact → meter → audit. Errors are mapped to one JSON envelope; unexpected failures return a request id, not internals.

Local build routes (171) run through a shim subclass of the local `RequestHandler` against one bound workspace. `aira_cloud/policy.py` lists every one with a scope, permission, plan feature and kind; a route with no entry is denied, and `tests/test_cloud_*.py` check that none is missing. Aira API console routes bind the organization's own API workspace (`_org`), shared by all its candidates.

The tenant binding is a thread-local (`context.py`) plus the local module's own request context. Hooks read it, so with no tenant bound a hook raises instead of falling back to a default.

## 8. Seams in the shared code

`aira/hosting.py` holds one optional callable per decision a host needs to change. Each is `None` in the local build.

| Hook | Hosted behaviour (`aira_cloud/hooks.py`) |
| --- | --- |
| `worker_registry` | LinkedIn worker pid, port and profile directory stored in `workspaces`, profile under the candidate's directory |
| `documents` | the organization's blob backend (local or S3) and its data key (current key first, retired keys still readable) |
| `settings_overlay` | the effective daily limit is the lowest of the candidate's, the organization policy's and the plan's; retention is capped by policy and plan; LinkedIn auto-reply is forced off unless allowed; headless browser; hosted model name |
| `serpapi_key` | the organization's own key from its Aira API workspace, else a platform pooled key when the plan and policy allow; the key's source is returned so a pooled key is never shown as the organization's |
| `secret_key` | the organization's data key for secrets held in its databases |
| `model_json` | the model gateway |
| `wrap_services` | `Gate.wrap_services`: pause, source and plan checks per search, daily and monthly remaining, the two-switch submit rule, reservation and release of application units, outcome recording |
| `submission_listeners` | counts applications not already reserved, writes an audit entry with actor and candidate, refreshes the dashboard snapshot |
| `start_background`, `automation_status`, `progress` | user-triggered runs become queue jobs (`autopilot.exec`) with a serializable description of the work; the Auto-apply screen reads queue state |
| `key_index` | organization Aira API keys are indexed in the control plane |
| `max_plans` | lifts the local limit of 10 search plans to the plan's entitlement |

Other small changes to shared code: `aira/documents.py` accepts a blob backend and a `MultiFernet`; `aira/autopilot.py` and `auto_apply_assistant.py` call the hooks above; `service.resolve_key` consults `serpapi_key`; the `app.js` shell calls `Aira.cloud` hooks when present. The local suite runs unchanged against these edits.

## 9. Automation: queue, workers, gate

- **Queue** (`jobqueue.py`): `queue_jobs` rows carry `org_id` and `candidate_id`. Leasing picks the waiting job of the organization with the fewest jobs running (oldest wait breaking ties), caps concurrency per organization (`per_org_concurrency`, 2 by default; an operator rate limit lowers it to 1), and allows one automation job per candidate at a time. Failures retry with backoff (30 s, 2 min, 10 min) up to three attempts, then `dead`; a job whose worker vanished returns to the queue when its lease expires.
- **Scheduler** (`scheduler.py`, run from `lifecycle.tick`): reads each active workspace read-only, enqueues `autopilot.due`, `schedules.due` (needs a connected LinkedIn session) and `monitors.due`, and skips organizations and candidates the gate would refuse.
- **Worker** (`worker.py`): leases, binds the tenant, runs the shared pipeline, records the outcome. A job the gate refuses finishes `skipped`, not failed. Handlers: `autopilot.due`, `autopilot.exec` (a user's "run now", answer, submit or apply), `schedules.due`, `monitors.due`.
- **Gate** (`gate.py`): refuses automation when the organization is suspended or rate limited, the subscription is past due or worse, the organization is over its plan limits, automation is paused for the organization or the candidate, the workspace is not active, or the candidate has not given the current automation, coach-access and sensitive-policy consents.
- **Two switches for automatic submission:** the organization policy must say `auto` and the plan must include `automation.submit` (and no region rule requires review), and the candidate's own search plan must be in `apply` mode (only the candidate, or a coach with `approve`, can set that). Otherwise the form is filled and the run waits as `ready` for a person to submit.

## 10. Dashboard and controls

`GET /api/org/dashboard` returns the panels the caller's role may see: plan and usage (seats, applications against the limit, searches, API searches, cover letters, storage, days to renewal, cost per application), pipeline by stage with the 30-day change, automation today by source with live queue state, needs attention (held questions older than 24 hours, failed runs, a missing resume, no coach, LinkedIn expected but unconnected), coach workload (candidates, open items, median time to answer a held question), outcomes (interviews and offers per 100 applications by source and by resume version), and the last 50 audit events. Candidate stages are `onboarding`, `searching`, `applying`, `interviewing`, `offers`, `placed`, computed from a snapshot of the workspace (refreshed at most every 60 seconds) and logged on change. Coaches and candidates get their own dashboards and a held-question queue.

Controls: policy (review or automatic submission, daily cap per candidate, allowed sources, LinkedIn and Easy Apply, cover letters, retention, candidate-data policy on departure, whether a coach may see sensitive values or messages, MFA for staff, region review list, pooled SerpApi), pause for the organization or one candidate with a reason shown on the dashboard, members and invitations, coach assignments and bulk reassignment, integrations (SerpApi key through the Aira API console, Aira API keys with per-key limits, the organization's model endpoint on Enterprise, SSO), billing, and data (export the organization or one candidate, delete a candidate workspace, request closure). Every policy change is validated against the plan (`402 plan_required` when it exceeds it) and audited with before and after values.

## 11. Platform console

Operators sign in separately (`/api/platform/auth/login`, cookie `aira_operator`) and must use MFA. Roles: `support` (read tenants, request access, announcements, health) and `platform_admin` (everything: suspend, plans, flags, billing actions, abuse controls, operators, data-subject requests).

Support access: an operator requests access with a reason; the organization owner approves for at most 24 hours (or denies); the operator then switches their session into the tenant with the granted role (`auditor` or `admin`); tenants see a banner while a grant is active; the owner can end it early; expiry is enforced both at request time and by the scheduler. Each step is written to the tenant's audit log and to the platform audit log. Platform actions are in `platform_audit_logs`, kept separate from tenant logs. A tenant's detail view shows metadata, usage and operator actions, never candidate data.

Health: queue depth, worker count and utilisation, apply success rate by ATS, search error rates by source, model latency (p50, p95), per-tenant error budgets (a tenant is flagged above a 25% failure rate over 24 hours with at least ten events), blocked sources.

## 12. Identity

- **Sign-up**: organization name, email, password (12 to 1,024 characters, not trivially weak, not containing the email name). Creates the organization, owner and trial; sends a single-use verification link (24 hours). Nothing but verification and sign-out works until it is used.
- **Sessions**: server-side rows, token stored hashed, 7 days (operators 12 hours). A role change, password change or reset bumps the member's session epoch, which ends every earlier session. A person can list sessions and sign out other devices.
- **Lockout**: eight failed sign-ins lock the account for 15 minutes; unknown emails take the same time as known ones.
- **MFA**: TOTP (RFC 6238) with replay protection and ten recovery codes. It is available on every plan. It is required for owners and admins on plans with `auth.sso` (Team, Enterprise) and for all staff when the organization policy sets `require_mfa_staff`; such a person is held at a setup gate until enrolled, and cannot turn it off. Operators always need it. An administrator can reset a member's MFA.
- **Invitations**: single use, 7 days, pending invitations count against seats, resend issues a new link. An administrator cannot invite or change an owner.
- **SSO and SCIM**: OIDC, SAML and SCIM 2.0 live in `aira_cloud/sso.py` and `sso_routes.py` (Team and Enterprise for SSO, Enterprise for SCIM). The routes the file registers are listed in [hosted-api.md](hosted-api.md#single-sign-on-and-scim). An organization with SSO enforced refuses password sign-in for everyone except owners (a break-glass path).

## 13. Data lifecycle

- **Export**: an organization export is a zip with `organization.json` (members, assignments, policy, invoices, departed-candidate aggregates), `audit.jsonl` and one zip per candidate; a candidate export is that candidate's tables as JSON plus every live document, decrypted. Exports are kept 30 days.
- **Offboarding a candidate**: sessions end, plans are disabled, the LinkedIn worker is stopped. With policy `candidate_keeps` (the default) the candidate gets a one-link download (30 days), the workspace is deleted, and only aggregate counts remain (`departed_candidates`). With `organization_keeps` the workspace is archived (read-only). Removing the last owner is refused.
- **Closure**: the owner confirms with their password; an export is created; the subscription is cancelled; deletion happens after the 30-day window; closure can be cancelled inside it.
- **Deletion** removes workspaces, documents (and object-storage prefixes), keys, secrets, browser profiles, queue items, sessions and every row keyed by `org_id`, then records the audit stub and a platform audit entry.
- **Retention**: the effective document retention is the lowest of the candidate's setting, organization policy and plan; expired documents are purged daily by the tick. Audit rows older than the plan's audit window are pruned, and the chain stays verifiable (an anchor hash is kept).
- **Audit**: append-only (triggers refuse update and delete except during pruning and deletion), each row hashed with the previous row's hash; `GET /api/org/audit/verify` recomputes the chain.
- **Data-subject requests**: an operator can export or erase one person across organizations (`lifecycle.data_subject_request`); a sole owner is skipped (close the organization instead).

## 14. Observability

Structured logs carry `correlation_id` and `org_id` (`AIRA_CLOUD_JSON_LOGS=1`); credentials and emails are redacted from log text and audit details. `GET /api/health` and `/api/ready`; `/api/platform/metrics` (process and per-tenant counters); `/api/platform/health`. Per-unit outcomes (`run_outcomes`) feed success rates, error budgets and cost per application (browser seconds, model calls, searches per application on the dashboard). There is no trace export and no alerting integration; see gaps.

## 15. Testing

`tests/test_cloud_*.py` with helpers in `tests/cloud_support.py` (an isolated control plane, a fake clock, one member of every role, and a browser that calls the gateway directly). They cover identity and MFA, the permission matrix and coach scopes, tenancy, policy validation, pause rules, consent, offboarding and deletion, audit chain and secret rotation, and the full billing lifecycle (checkout, proration, downgrade, dunning at 14 and 60 days, trial end, cancellation, overage, operator actions, webhook signatures). `tests/test_cloud_isolation.py` has one tenant call every route with another tenant's identifiers in every position and fails if anything leaks, or if a route is not accounted for. `tests/test_cloud_sso.py` covers OIDC and SAML sign-in (including signature wrapping, replay and DOCTYPE cases), JIT provisioning, group mapping, enforcement and SCIM. `tests/test_cloud_legal.py` serves and sanitizes the `/legal` pages, checks that the documentation links resolve and that [hosted-api.md](hosted-api.md) matches the routes. The local suite must stay green against the same repository; run it before every release.

## 16. Resolved ambiguities

The scope document is inconsistent in a few places. The code follows these readings.

1. **Staff seats.** "Coach and admin seats" are consumed by `owner`, `admin` and `coach` members and pending invitations for them. Billing contacts and auditors take no seat.
2. **MFA by plan.** The plan table lists MFA only from Starter; the security section says MFA is available on all plans. The security section wins: MFA is available everywhere, and *required* for owners and admins on plans with SSO.
3. **Coach `view` scope cannot pause.** The matrix lets an assigned coach pause a candidate; the acceptance criteria say a `view` coach cannot change anything. Pausing needs `act`.
4. **Billing contact sees usage only.** It holds `usage.read` and `billing.manage`, not `dashboard.read`; it gets the usage panel, not the organization dashboard.
5. **End of a trial or subscription.** A trial that ends without a plan, or a cancelled subscription, becomes `cancelled`: data is read-only and exportable for 30 days, then the organization is deleted.
6. **Counters use calendar months (UTC)**, not the billing period, so "applications this month" resets on the first of the month for every organization. Billing periods are 30 days.
7. **60 days of non-payment** schedules deletion; it executes 30 days after that (`deletion_grace_days`), so an unpaid organization is deleted about 90 days after its first failure.
8. **Where the candidate-data policy lives.** The scope calls it a per-plan setting; as built it is an organization policy (`candidate_data_policy`, default `candidate_keeps`).
9. **Candidate job searches are not capped by the API allowance.** `api_searches_per_month` limits the Aira API console and `/v1`; a candidate searching in Jobs is counted (`searches`) but not limited, so a trial candidate can use Aira Jobs.
10. **Messages and LinkedIn.** The inbox, replies and schedules use the candidate's own LinkedIn session, so they are the candidate's alone; an assigned coach can read (`view`) or act (`act`) only when the organization policy `coach_messages` allows it.

## Aira Leads in the hosted build

Aira Leads (`/api/leads/*`, [aira/leads/](../aira/leads/)) runs inside each candidate's workspace like Careers does. In the hosted shell it adds:

- the `leads` plan feature (Starter and up) and two limits, `leads_stored` and `lead_enrichments_per_month` ([pricing.md](pricing.md)); plan versions published before they existed pick up the feature and limits from the shipped plan;
- permissions `leads.read` and `leads.write` at `act` level: the candidate and a coach with `act` or `approve` scope; nobody else (see [multitenant-security.md](multitenant-security.md));
- route hooks (`aira_cloud/route_hooks.py`): source gates (Google lead searches need `sources.google_jobs` and a policy that allows it; Sales Navigator needs every LinkedIn gate), metering (`searches`, pooled allowance by credits, `lead_enrichments`), stored-lead capacity checks, and forwarding of Sales Navigator reads to the worker host that holds the candidate's LinkedIn session (also for a saved Sales Navigator search, decided once the workspace is bound);
- counts in the organization dashboard (`plan_usage.leads`, including outreach emails sent) and in each workspace snapshot;
- **outreach** (`leads_outreach` feature, Team and up; limits `lead_emails_per_month` and `lead_email_daily_cap`): permission `leads.outreach` held only by the candidate (approve, send, sender settings, test message, removing a do-not-contact entry); the send hook (`leads_outreach_send`) checks the kill switch and the automation gates (suspended, past due, read-only, paused, over limits), reserves the batch against the month's allowance and returns what was not sent; the platform kill switch is the source name `leads_outreach` (`POST /api/platform/sources/block`) and stops every send and test message while drafting and reading carry on;
- lead tables, including the outbox, the do-not-contact list, the call log and the sender settings, in the candidate's own export (staff exports leave them out: `redaction.PRIVATE_TABLES`), and removal with the workspace.

## Differences from the scope document and known gaps

Stated plainly so nobody plans around something that does not exist.

**Not built**

- **PostgreSQL: what is and is not proven.** The control plane runs on PostgreSQL 16 with row-level security, an application role that is neither owner nor `BYPASSRLS`, a platform role for cross-organization code, PL/pgSQL audit guards, and a SQLite-to-PostgreSQL copy command. The whole hosted-shell suite (`tests/test_cloud_*.py`) was run against a real PostgreSQL 16 in Docker, and `tests/test_cloud_postgres.py` proves: no tenant means no rows; a missing `WHERE` returns only the caller's organization; cross-organization writes are refused or touch nothing; the application role cannot disable policies, switch roles, create objects or read platform tables; the table owner is bound by the policies too; every table is classified; audit rows cannot be updated or deleted; usage counters stay exact under 16 threads; the job lease never double-leases or exceeds a cap under 8 workers and skips a locked row instead of waiting. **Not tested:** PgBouncer or other transaction poolers, read replicas, failover, PostgreSQL versions other than 16, and load (writers are serialized by one advisory lock outside the hot paths named in the operations guide). Global tables (plans, flags, `cloud_kv`) carry no policy, and a thread that never enters a tenant scope runs as the platform role. Row-level security protects rows, not columns. Workspaces remain one SQLite file per candidate on a shared volume.
- **KMS in production.** Master-key custody is a setting (`AIRA_CLOUD_KMS=local|aws|vault`, `aira_cloud/kms.py`): AWS KMS (`Encrypt`, `Decrypt`, `GenerateDataKey`, Signature Version 4) and Vault transit are built and tested against stand-ins that check request signatures and prove the KMS only ever sees data keys (`tests/test_cloud_kms.py`). Not exercised against a real AWS account or Vault server, and AWS instance-role credentials are not fetched (inject them into the environment). `python -m aira_cloud rewrap-keys` moves every organization key between providers in one transaction.
- **Master-key rotation** is the `rewrap-keys` command above (with `--verify-only`); per-organization data-key rotation is `rotate-keys` (documents and secrets re-encrypted by a worker job).
- **Remote sign-in is a broker, not a full remote desktop.** Worker agents (`python -m aira_cloud worker --agent-port ...`) hold the LinkedIn sessions and the gateway forwards LinkedIn-bound routes to them. `python -m aira_cloud broker` (`aira_cloud/streaming.py`) answers the one-time sign-in link: it validates the token with the web tier, starts Chromium on the candidate's profile on that host, streams the page as JPEG frames over a WebSocket and replays mouse, wheel, key and pasted-text events, ending when the session cookie appears. It shows one browser page, not a desktop (no address bar, no other windows, no downloads), only LinkedIn's hosts and an operator allowlist can be navigated to, and sign-in with Google or Apple needs their hosts added to `AIRA_CLOUD_BROKER_ALLOWED_HOSTS` and a pop-up the broker does not show. It has run against a stub login site with real browsers; it has not run against LinkedIn's live sign-in, which may add checkpoints (captcha, device checks) that behave differently in a headless browser. Keystrokes pass through the broker process on their way to the browser; it does not log or keep them, but it is inside the trust boundary you operate.
- **Pooled SerpApi metering is built.** Searches made with the platform's key (`use_pooled_serpapi`) are counted as `pooled_searches`, limited by `pooled_searches_per_month` (Team 2,000, Enterprise 50,000, none on Starter and Trial), refused with `402 plan_required` at the limit, and billed per search past it when overage is on. Only credits actually spent count: a cache hit is free. Autopilot runs check the allowance before each search rather than reserving, so concurrent runs can overshoot by a few searches.
- **Overage** covers applications, AI cover letters, Aira API searches (console, `/v1` and organization monitors) and pooled SerpApi searches. Team and Enterprise owners turn it on (`POST /api/org/billing/overage`); it is invoiced once for the previous month. Jobs-tab searches with an organization's own key are counted (`searches`) but have no limit and no rate.
- **Scheduled backups.** `python -m aira_cloud backup-org` / `restore-org` and the platform download produce and restore one organization's archive (the restore drill is a test: one tenant restored, the others byte-identical); running them on a schedule and keeping the archives off the host is yours to arrange.
- **Deployment artifacts beyond what was run.** The Docker stack, the Kubernetes manifests (on kind), Terraform validation and checkov are run and have scripts (see "What was run, and how to rerun it" in [multitenant-operations.md](multitenant-operations.md)). Not run: `terraform apply`, a managed Kubernetes service with ReadWriteMany storage and an ingress controller, autoscaling on real metrics. The GitHub workflow that repeats the runs has not executed on GitHub yet.
- **Production billing.** The Stripe adapter's requests are validated against Stripe's OpenAPI specification by stripe-mock and its webhook normalizer reads the shapes stripe-mock returns; it has not talked to a Stripe account, test mode or live. Stripe subscription schedules are not used: a downgrade is applied at period end by the tick, which is close but not identical.
- **Certification and external review.** No penetration test, SOC 2 audit, DPA review or counsel review has happened. The legal documents in `docs/legal/` are templates.
- **Operational signals.** No distributed tracing, no alert rules, no log shipping beyond the SIEM poll stream (`GET /api/org/audit/stream`, JSON lines or CEF, Enterprise); log retention (90 days in the scope) is a deployment setting. HSTS is sent on https requests.
- **Replacing Ollama.** A hosted model (Anthropic or an OpenAI-compatible endpoint) and per-organization endpoints are supported by `models.py` but were not run against a live provider here.

**Differences in how things are done**

- The candidate-data policy is per organization, not per plan (section 16).
- `GET /api/org/audit/export` is available to anyone with `audit.read` on every plan; the SIEM poll stream (`/api/org/audit/stream`) is Enterprise.
- The single-user local build and the hosted build share one repository and one test suite; there is no separate package for the hosted shell beyond `aira_cloud/`.

**Open decisions from the scope document, as decided in code**

| Decision | As built |
| --- | --- |
| Who owns candidate data when a member leaves | per-organization policy, default: the candidate keeps it |
| SerpApi key model | bring-your-own key (Starter and up, entered in the Aira API console); pooled key for Team and Enterprise behind a policy switch; Aira Jobs is free for all |
| LinkedIn automation | off by default; needs the plan feature, organization policy and the candidate's own acknowledged opt-in; a platform kill switch can turn it off for everyone |
| Database layout | control-plane SQLite plus one SQLite per candidate (see gaps) |
| AI model | model gateway: hosted or Ollama by default, an organization's own endpoint on Enterprise |
| Billing provider | Stripe for self-serve, operator-activated invoicing for Enterprise; a stand-in provider for development |
| Candidate identity | per organization (email per organization) |

## Legal review still needed (scope section "Security, compliance and legal")

1. The LinkedIn position: the User Agreement forbids automated access; the hosted product keeps Easy Apply and inbox automation off by default and behind an acknowledged opt-in, but counsel should confirm whether to keep, restrict by plan or remove it. See [legal/automation-and-linkedin.md](legal/automation-and-linkedin.md).
2. The terms for automated applications in each market, because some jurisdictions regulate automated decision-making about employment; review mode is a first-class mode and the policy can require it (including by region list).
3. The data-processing agreement template, [legal/dpa.md](legal/dpa.md).
