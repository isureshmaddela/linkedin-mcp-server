# Hosted API reference

The control-plane API of the hosted build (`aira_cloud/routes/`), and how the local build's candidate-workspace routes are exposed through the gateway. Generated from the route table; regenerate when routes change.

Applies to the hosted build only. The local single-user build keeps its own API, described in [api.md](api.md).

## Conventions

- JSON in, JSON out, UTF-8. A mutating request needs the same-origin check: an `Origin` equal to `Host`, or the header `X-Requested-With: aira`.
- **Sessions** are cookies: `aira_session` for members and `aira_operator` for platform operators. They are separate sign-ins and never interchangeable. Both are `HttpOnly` and `SameSite=Strict`, and `Secure` when the base URL is https.
- **Identity comes from the session.** The organization is never taken from a request body or path. A coach, administrator or auditor names a candidate with the `X-Aira-Candidate` header (or `?candidate=`); a candidate's own workspace is implied.
- Every response carries `X-Request-ID`. Send one (4 to 64 letters, digits and hyphens) to correlate your own logs.
- Errors are `{"error": "message", "code": "machine_code", ...extra}`.

| Status | Meaning |
| --- | --- |
| 400 | `invalid`: the request could not be used (the message says why) |
| 401 | `unauthorized`; also `mfa_required` (give the second factor) |
| 402 | `plan_required` (a feature or counted limit the plan does not allow, see below), `app_required` (the organization has not turned on the app that owns the route, see below) or `subscription_inactive` (past due, read-only, cancelled, suspended, or over plan limits) |
| 403 | `forbidden` (permission), or a specific code: `email_unverified`, `mfa_enrollment_required`, `sso_required`, `bad_host`, `cross_origin`, `candidate_required`, `not_your_workspace`, `linkedin_off`, `source_blocked`, `sensitive_field`, `sensitive_answer`, `not_available`, `mfa_required_by_plan` |
| 404 | `not_found`. It is also what an identifier of **another organization** returns, for every role, so identifiers cannot be probed |
| 409 | `conflict` (for example `org_required` at sign-in, an email that is already a member, the last owner) |
| 410 | `gone` (a used or expired link) |
| 423 | `locked` (too many failed sign-ins) |
| 429 | `rate_limited`, with `retry_after`. Limits apply per client address, per organization and per member |
| 502, 503 | an upstream (model, billing provider) failed, or is not configured |

### `402 plan_required`

```json
{"error": "The Starter plan allows 10 applications per candidate per day, and it has been reached. Upgrade to Team for a higher limit.",
 "code": "plan_required", "limit": "apps_per_candidate_day", "used": 10, "limit_value": 10,
 "scope": "candidate_day", "required_plan": "team", "current_plan": "starter", "audience": "staff"}
```

A missing feature carries `feature` instead of `limit` (for example `sources.linkedin`). `required_plan` is the cheapest paid plan that includes it, or null when none does or the limit is organization policy. `audience` is `candidate` for a candidate: the message then states the organization's rule and `required_plan` is null, so a candidate never sees a sales prompt.

### `402 app_required`

```json
{"error": "Leads is not turned on for your organization. Turn it on under Apps (billed per candidate seat on your Team plan).",
 "code": "app_required", "app": "leads", "name": "Leads", "plan_hint": "Turn it on under Apps (billed per candidate seat on your Team plan).",
 "required_plan": null, "current_plan": "team", "audience": "staff"}
```

Every route an app owns (`route_prefixes` in `aira/apps.py`: `/api/leads/*` for Leads, `/api/airaapi/*` and `/v1/*` for API) answers this until the organization has the app: included in its plan, bought under Apps (`POST /api/org/apps/<app>/subscribe`), or granted by an operator. `required_plan` names the cheapest plan that sells the app when the current one does not (the API app on Starter). Careers' routes and the organization's SerpApi key routes are never gated. A candidate's message states the organization's rule and carries no sales prompt.

### Read-only organizations

A route marked **writes** is refused with `402 subscription_inactive` while the subscription is `read_only` or `cancelled`. Reads, exports and billing keep working. A `deleted` or suspended organization refuses everything.

### Gates

Routes marked *no gate* work while a person has not yet verified their email or given (or enrolled) a second factor. All other member routes answer `403 email_unverified`, `401 mfa_required` or `403 mfa_enrollment_required` until that is done. *no verified-email gate* and *no MFA gate* relax one of the two.

## Sign-up, sign-in, sessions, MFA, passwords, invitations and the candidate's own consent

| Method | Path | Auth | Flags | Body and query | What it does, and who may call it |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/auth/me` | member session | no gate |  | The signed-in person: role, organization, plan, features, permissions, rules, assigned candidates (coach), consent (candidate). **Who:** any member (works before email verification and before the second factor). |
| GET | `/api/cloud/config` | none |  |  | Deployment facts for the sign-in screen: region, billing provider, trial days, public plans. |
| POST | `/api/auth/signup-org` | none |  | body: `email`, `name`, `organization`, `password` | Create an organization, its owner and a 14-day trial; sets the session cookie; sends the verification email. **Who:** rate limited. |
| POST | `/api/auth/login` | none |  | body: `email`, `org`, `password` | Email and password (and `org` slug when the email is in several organizations; `409 org_required` lists them). Sets the session cookie. `mfa_required` in the answer means the second factor is still due. **Who:** rate limited; 8 failures lock the account for 15 minutes. |
| POST | `/api/auth/logout` | none |  |  | End the session and clear the cookie. |
| POST | `/api/auth/verify-email` | none |  | body: `token` | Confirm an email address from the link token. Single use, 24 hours. |
| POST | `/api/auth/resend-verification` | member session | no gate |  | Send the verification email again. |
| GET | `/api/auth/mfa` | member session | no gate |  | Whether MFA is enabled, required for this role on this plan, verified in this session, and may be turned off. |
| POST | `/api/auth/mfa/begin` | member session | no gate |  | Start TOTP setup; returns the secret and an `otpauth://` URI. |
| POST | `/api/auth/mfa/enable` | member session | no gate | body: `code` | Confirm setup with a code; returns ten one-time recovery codes (shown once). |
| POST | `/api/auth/mfa/verify` | member session | no gate | body: `code` | Give the second factor (a TOTP code or a recovery code) for this session. A code cannot be replayed. |
| POST | `/api/auth/mfa/disable` | member session |  | body: `code`, `password` | Turn MFA off (password and a current code). Refused with `403 mfa_required_by_plan` for owners and admins on plans with SSO. |
| POST | `/api/auth/password/forgot` | none |  | body: `email`, `org` | Request a reset link. Always answers the same. |
| POST | `/api/auth/password/reset` | none |  | body: `password`, `token` | Set a new password from the link token; ends every session. |
| POST | `/api/auth/password/change` | member session | no verified-email gate | body: `current`, `password` | Change the password (current password required); ends every other session and returns a new cookie. |
| GET | `/api/auth/sessions` | member session | no verified-email gate |  | List the person's active sessions (device sign-out). |
| DELETE | `/api/auth/sessions/{id}` | member session | no verified-email gate |  | Revoke one session. |
| POST | `/api/auth/sessions/sign-out-others` | member session | no verified-email gate |  | Revoke every session except the current one. |
| PATCH | `/api/auth/profile` | member session | no verified-email gate | body: `name` | Change the display name. |
| GET | `/api/invites/{token}` | none |  |  | Organization name, role and email an invitation is for (does not consume it). |
| POST | `/api/invites/{token}/accept` | none |  | body: `name`, `password` | Accept an invitation: choose a password; creates the member (and a candidate's workspace) and signs them in. Single use, 7 days. |
| GET | `/api/me/consent` | member session | no verified-email gate |  | The candidate's consent state: version, what is granted, whether the required ones are complete. **Who:** candidate. |
| POST | `/api/me/consent` | member session | writes | body: `kinds`, `version` | Grant consents (`kinds`: automation, coach_access, sensitive_policy, sensitive_access). **Who:** candidate. |
| DELETE | `/api/me/consent/{kind}` | member session | writes |  | Revoke one consent. Revoking `automation` stops automation for the candidate. **Who:** candidate. |
| GET | `/api/me/onboarding` | member session |  |  | The candidate's onboarding checklist. **Who:** candidate. |
| POST | `/api/me/easy-apply` | member session | writes | body: `acknowledge`, `enable` | Turn LinkedIn Easy Apply on (with `acknowledge: true`) or off for the candidate's own workspace. **Who:** candidate. |
| GET | `/api/launch/local` | member session |  | query: `return` | Open the local installation (`AIRA_CLOUD_LOCAL_APP_URL`) as this candidate: a one-time launch token (60 s, single use, stored hashed, bound to the return address) and a `302` to `<return>#/launch?token=...`; `Accept: application/json` gets `{redirect, expires_in}`. `return` must be on the local installation's origin. `409 not_configured` without `AIRA_CLOUD_LOCAL_APP_URL` and `AIRA_CLOUD_LAUNCH_SECRET`; five per minute per member. **Who:** candidates only (staff, operators and acting coaches: 403); not while the organization is suspended, past due or read-only. |
| POST | `/api/internal/launch/validate` | none | no CSRF check | body: `token` | The local installation asks who a launch token signs in: `{email, name, org_id, org_name, org_slug, member_id, role, app, apps[]}`, once (`410` afterwards or when expired, `404` unknown, `410` when the member was removed). Signed with `AIRA_CLOUD_LAUNCH_SECRET`, purpose `launch`, the agent signing scheme (timestamp, nonce, body); audited as `auth.launch_local`. **Who:** server to server (signature), no session. |

## The organization: dashboard, members, invitations, candidates, coaches, pause switches, audit, integrations, support access, data

| Method | Path | Auth | Flags | Body and query | What it does, and who may call it |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/org` | member session |  |  | Organization, policy, branding, entitlements, pause state; billing summary for those who may see it. |
| PATCH | `/api/org/policy` | member session | writes |  | Change policy. Validated against the plan: `402 plan_required` when a setting exceeds it. Audited with before and after. **Who:** `org.settings`: owner, admin. |
| PUT | `/api/org/branding` | member session | writes | body: `logo_url`, `name` | Logo (https only) and display name. Team and Enterprise. **Who:** `org.settings`. |
| GET | `/api/org/dashboard` | member session |  |  | The dashboard for the caller's role: organization (plan and usage, pipeline, automation today, needs attention, coach workload, outcomes, audit), coach, candidate, or usage only (billing contact). **Who:** any role that has a dashboard. |
| GET | `/api/org/banners` | member session | no verified-email gate |  | Paused, trial, billing state, over-limits, closure, active support access and platform announcements. |
| GET | `/api/org/usage` | member session |  |  | Plan and usage panel (seats, applications, searches, API searches, cover letters, storage, history). **Who:** `usage.read`. |
| GET | `/api/org/notices` | member session |  |  | Organization notices (payment, limits, support requests). **Who:** owner, admin, billing contact. |
| POST | `/api/org/notices/read` | member session |  |  | Mark notices read. |
| GET | `/api/org/members` | member session |  | query: `limit`, `offset`, `q`, `role` | Members (a coach sees only assigned candidates) and seat usage. **Who:** `members.manage` or `candidates.list`. |
| PATCH | `/api/org/members/{id}` | member session | writes | body: `role` | Change a member's role (`role`). Ends their sessions. A candidate cannot change role; the last owner cannot be demoted. **Who:** `members.manage`; owner changes need an owner. |
| DELETE | `/api/org/members/{id}` | member session | writes |  | Remove a member. For a candidate, applies the candidate-data policy (export and delete, or archive). The last owner cannot be removed. **Who:** `members.manage`. |
| POST | `/api/org/members/{id}/mfa-reset` | member session | writes |  | Clear a member's MFA after a lost device; ends their sessions. **Who:** `members.manage`. |
| GET | `/api/org/invites` | member session |  | query: `all` | Pending invitations (`all=1` for history). **Who:** `members.manage`. |
| POST | `/api/org/invites` | member session | writes | body: `coach_id`, `coach_scope`, `email`, `emails`, `role` | Invite one or several people (`email` or `emails`, `role`, optional `coach_id` and `coach_scope` for a candidate). Pending invitations count against seats. With several addresses the answer lists `invites` and `failed`. **Who:** `members.manage`. |
| POST | `/api/org/invites/{id}/resend` | member session | writes |  | New single-use link and a new 7-day expiry. **Who:** `members.manage`. |
| DELETE | `/api/org/invites/{id}` | member session | writes |  | Withdraw an invitation. **Who:** `members.manage`. |
| GET | `/api/org/candidates` | member session |  | query: `attention`, `coach`, `limit`, `offset`, `q`, `stage` | Candidates with stage, held and ready counts, coaches and attention rules. A coach sees only assigned candidates. **Who:** `candidates.list`: owner, admin, coach, auditor. |
| GET | `/api/org/candidates/{id}` | member session |  |  | One candidate: stage, pause, LinkedIn state, snapshot, onboarding, consent, coaches, attention, queue, and whether the caller may see sensitive values. **Who:** `candidates.read` for that candidate. |
| POST | `/api/org/candidates/{id}/pause` | member session | writes |  | Pause automation for the candidate (`reason` required, shown on the dashboard). **Who:** `automation.pause.candidate`; a coach needs `act`. |
| POST | `/api/org/candidates/{id}/resume` | member session | writes |  | Resume. A candidate or coach cannot resume what an owner or admin paused. **Who:** `automation.pause.candidate`. |
| DELETE | `/api/org/candidates/{id}/workspace` | member session | writes |  | Delete the candidate's workspace (database, documents, browser profile) and end their membership. **Who:** `candidates.manage`: owner, admin. |
| GET | `/api/org/assignments` | member session |  | query: `candidate`, `coach` | Coach assignments with the effective scope (assignment capped by the plan). **Who:** owner, admin, auditor see all; a coach or candidate sees their own. |
| PUT | `/api/org/assignments` | member session | writes | body: `candidate_id`, `coach_id`, `scope` | Assign or change a coach's scope for a candidate (`coach_id`, `candidate_id`, `scope`: view, act, approve). `402` when the plan's coach scopes do not include it. **Who:** `coaches.assign`: owner, admin. |
| DELETE | `/api/org/assignments/{id}/{id}` | member session | writes |  | Remove an assignment (coach id, then candidate id). **Who:** `coaches.assign`. |
| POST | `/api/org/assignments/reassign` | member session | writes | body: `from_coach`, `scope`, `to_coach` | Move every candidate from one coach to another (`from_coach`, `to_coach`, optional `scope`). **Who:** `coaches.assign`. |
| GET | `/api/org/queue` | member session |  | query: `candidate` | Held questions and applications waiting for approval across the candidates the caller may act for. **Who:** owner, admin, auditor, coach, candidate (own). |
| POST | `/api/org/automation/pause` | member session | writes |  | Pause all automation for the organization (`reason` required). **Who:** `automation.pause.org`: owner, admin. |
| POST | `/api/org/automation/resume` | member session | writes |  | Resume organization automation. **Who:** `automation.pause.org`. |
| GET | `/api/org/audit` | member session |  | query: `action`, `actor`, `before`, `candidate`, `limit`, `since`, `until` | Audit events (filters `candidate`, `actor`, `action`, `since`, `until`, `before`, `limit`). **Who:** `audit.read`: owner, admin, auditor. |
| GET | `/api/org/audit/verify` | member session |  |  | Recompute the hash chain; reports the first broken row. **Who:** `audit.read`. |
| GET | `/api/org/audit/export` | member session |  |  | All events as JSON lines. **Who:** `audit.read`. |
| GET | `/api/org/audit/stream` | member session |  | query: `after`, `format`, `limit` | Poll the audit log for a SIEM: events after a cursor as JSON lines or CEF (Enterprise). |
| GET | `/api/me/activity` | member session | no verified-email gate | query: `limit` | What was done in the candidate's name: actor, role, action, time. **Who:** candidate. |
| GET | `/api/org/integrations` | member session |  |  | SerpApi key status (never the key), model endpoint, SSO and SCIM state, Aira API key counts. **Who:** `integrations.read`: owner, admin, auditor. |
| PUT | `/api/org/integrations/model` | member session | writes | body: `api_key`, `model`, `url` | Set the organization's own OpenAI-compatible model endpoint (`url`, `api_key`, `model`; Enterprise). **Who:** `integrations.manage`. |
| DELETE | `/api/org/integrations/model` | member session | writes |  | Remove it. **Who:** `integrations.manage`. |
| GET | `/api/org/support` | member session |  |  | Platform support-access requests for this organization. **Who:** `support.approve`: owner. |
| POST | `/api/org/support/{id}/approve` | member session | writes | body: `hours` | Approve a request for up to 24 hours (`hours`). **Who:** owner. |
| POST | `/api/org/support/{id}/deny` | member session | writes |  | Deny a request. **Who:** owner. |
| POST | `/api/org/support/{id}/revoke` | member session |  |  | End a grant early. **Who:** owner. |
| GET | `/api/org/exports` | member session |  |  | Unexpired exports (a candidate sees their own). **Who:** owner, admin, auditor; candidate for their own. |
| POST | `/api/org/exports` | member session |  |  | Create an export: with `candidate_id` one candidate (a candidate exports themselves), without it the whole organization (JSON plus documents). **Who:** `export.org` or `export.own`. |
| GET | `/api/org/exports/{id}/download` | member session |  |  | Download an export archive (zip). **Who:** as above. |
| GET | `/api/exports/download/{token}` | none |  |  | One-link download for a departed candidate; valid 30 days. **Who:** the token is the capability. |
| POST | `/api/org/closure` | member session |  | body: `password` | Request organization closure (`password`); creates an export; deletion after the retention window. **Who:** `org.close`: owner. |
| DELETE | `/api/org/closure` | member session |  |  | Cancel closure inside the window. **Who:** owner. |

## Billing: plans, quotes, checkout, plan changes, provider webhook, stand-in checkout

| Method | Path | Auth | Flags | Body and query | What it does, and who may call it |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/billing/plans` | none |  |  | Public plans with seat price, minimum seats, limits, features and overage rates. |
| GET | `/api/org/billing` | member session |  |  | Subscription summary, plans, and (for owners and billing contacts) invoices. **Who:** owner and billing contact in full; admin and auditor see the usage summary. |
| POST | `/api/org/billing/quote` | member session |  |  | Monthly price for a plan and seat count, and the prorated charge if upgrading now. **Who:** `billing.manage`: owner, billing contact. |
| POST | `/api/org/billing/checkout` | member session |  |  | Start hosted checkout; returns the provider's `checkout_url`. **Who:** `billing.manage`. |
| POST | `/api/org/billing/change-plan` | member session |  |  | Upgrade or add seats now (prorated); downgrade or remove seats at period end (`scheduled: true`, with `would_exceed`). **Who:** `billing.manage`. |
| POST | `/api/org/billing/cancel-change` | member session |  |  | Cancel a scheduled downgrade. **Who:** `billing.manage`. |
| POST | `/api/org/billing/overage` | member session |  | body: `enabled` | Turn metered overage on or off (Team and Enterprise). |
| POST | `/api/org/billing/cancel` | member session |  |  | Stop renewal; service continues to the period end, then a 30-day export window. **Who:** `billing.manage`. |
| POST | `/api/org/billing/reactivate` | member session |  |  | Undo a cancellation that has not taken effect yet. After it has, use checkout. **Who:** `billing.manage`. |
| POST | `/api/org/billing/portal` | member session |  |  | The provider's billing-portal address. **Who:** `billing.manage`. |
| POST | `/api/billing/webhook/{name}` | none | no CSRF check |  | Provider webhook (`stripe` or `standin`). Verified by signature (`Stripe-Signature` or `X-Aira-Signature`), processed once per event id, effective synchronously. **Who:** signature required. |
| GET | `/billing/standin/checkout/{checkout}` | none |  | query: `declined` | Stand-in hosted checkout page (only when `AIRA_CLOUD_BILLING=standin`). |
| POST | `/billing/standin/checkout/{checkout}/pay` | none |  |  | Complete the stand-in checkout: emits signed `checkout.completed` and `invoice.paid` events; redirects to the success address. **Who:** the checkout id is the capability. |
| POST | `/billing/standin/checkout/{checkout}/decline` | none |  |  | Simulate a declined card; redirects back to the page. |
| POST | `/billing/standin/checkout/{checkout}/cancel` | none |  |  | Cancel the stand-in checkout; redirects to the cancel address. |
| GET | `/billing/standin/portal/{customer}` | none |  |  | Stand-in billing portal with buttons that simulate payment failure and recovery. |
| POST | `/billing/standin/portal/{customer}/fail` | none |  |  | Emit a signed `invoice.payment_failed` for the organization that owns the customer. |
| POST | `/billing/standin/portal/{customer}/recover` | none |  |  | Emit a signed `invoice.paid` (payment recovered). |

## Apps: the catalog, and the per-app subscriptions on top of the plan

| Method | Path | Auth | Flags | Body and query | What it does, and who may call it |
| --- | --- | --- | --- | --- | --- |
| GET | `/api/apps` | member session |  |  | The app switcher: every app in the catalog with `enabled` and a `reason` when it is off, the account's address and the local installation (`AIRA_CLOUD_LOCAL_APP_URL`). **Who:** any member. |
| GET | `/api/org/apps` | member session |  |  | The Apps page: each app with how the plan treats it (`included`, `addon`, `unavailable`), its status (`included`, `granted`, `active`, `cancelling`, `off`, `unavailable`), the price per candidate seat and what it costs this organization; `monthly_cents` and `addons_monthly_cents`. **Who:** owner and billing contact (`can_manage`); admin and auditor read. |
| POST | `/api/org/apps/{value}/quote` | member session |  |  | What turning an app on costs: the monthly line and the prorated charge for the rest of this period. `402 plan_required` when the plan does not sell the app. **Who:** `billing.manage`. |
| POST | `/api/org/apps/{value}/subscribe` | member session | writes |  | Turn an app on: billed per candidate seat with the plan, prorated now; `409 included|already_subscribed|granted|not_subscribed`. Audited as `billing.app_subscribed`. **Who:** `billing.manage`. |
| POST | `/api/org/apps/{value}/unsubscribe` | member session | writes |  | Turn an app off at the period end; it keeps working until then. **Who:** `billing.manage`. |
| POST | `/api/org/apps/{value}/keep` | member session | writes |  | Undo a cancellation that has not taken effect yet. **Who:** `billing.manage`. |
| GET | `/api/platform/tenants/{id}/apps` | operator session |  |  | A tenant's apps as its Apps page shows them. **Who:** operator (`platform.tenants.read`). |
| PUT | `/api/platform/tenants/{id}/apps/{value}` | operator session |  | body: `note`, `status` | Grant an app to a tenant at no charge (`status: granted`, with a `note`), also where the plan does not sell it, or take the grant back (`revoked`). Both audit logs record it. **Who:** `platform.billing.manage`. |

## Platform console (operator session)

| Method | Path | Auth | Flags | Body and query | What it does, and who may call it |
| --- | --- | --- | --- | --- | --- |
| POST | `/api/platform/auth/login` | none |  | body: `email`, `password` | Operator email and password; sets the operator cookie (separate from tenant sessions). **Who:** rate limited. |
| POST | `/api/platform/auth/logout` | none |  |  | End the operator session. |
| GET | `/api/platform/me` | operator session | no MFA gate |  | Operator identity, MFA state, active support grant. |
| POST | `/api/platform/auth/mfa/begin` | operator session | no MFA gate |  | Start operator MFA setup. |
| POST | `/api/platform/auth/mfa/enable` | operator session | no MFA gate | body: `code` | Confirm operator MFA. |
| POST | `/api/platform/auth/mfa/verify` | operator session | no MFA gate | body: `code` | Give the second factor for this operator session. |
| GET | `/api/platform/tenants` | operator session |  | query: `limit`, `offset`, `plan`, `q`, `status` | Organizations with plan, status, seats, usage and health flags. **Who:** `platform.tenants.read`. |
| GET | `/api/platform/tenants/{id}` | operator session |  |  | One tenant: policy, billing, usage, support grants, operator audit, queue. No candidate data. **Who:** `platform.tenants.read`. |
| POST | `/api/platform/tenants/{id}/suspend` | operator session |  |  | Suspend an organization (`reason` required); ends member sessions. **Who:** `platform.tenants.manage`: platform admin. |
| POST | `/api/platform/tenants/{id}/reinstate` | operator session |  |  | Reinstate. **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/schedule-deletion` | operator session |  | body: `days` | Schedule deletion (`days`, default the grace period). **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/cancel-deletion` | operator session |  |  | Cancel scheduled deletion. **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/rate-limit` | operator session |  | body: `hours`, `per_minute` | Rate-limit a tenant (`per_minute`, `hours`). **Who:** `platform.abuse.manage`: platform admin. |
| DELETE | `/api/platform/tenants/{id}/rate-limit` | operator session |  |  | Clear the limit. **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/extend-trial` | operator session |  | body: `days` | Extend a trial (`days` 1 to 90); reopens a cancelled trial. **Who:** `platform.billing.manage`: platform admin. |
| GET | `/api/platform/tenants/{id}/limits` | operator session |  |  | The plan's limits, this organization's overrides, and what applies (Enterprise contracts). |
| PUT | `/api/platform/tenants/{id}/limits` | operator session |  | body: `limits`, `replace` | Override plan limits for one organization; ``replace`` drops overrides not listed. Needs a reason. |
| DELETE | `/api/platform/tenants/{id}/limits` | operator session |  | query: `reason` | Remove every override: the organization goes back to its plan's limits. |
| POST | `/api/platform/tenants/{id}/credit` | operator session |  | body: `amount_cents` | Record a billing credit (`amount_cents`, `reason`). **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/activate-enterprise` | operator session |  | body: `invoice_cents`, `overrides`, `period_days`, `reference`, `seats` | Activate Enterprise (`seats`, `overrides`, `period_days`, `invoice_cents`, `reference`). **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/invoice-paid` | operator session |  | body: `invoice_id` | Mark an invoice paid (`invoice_id`); restores a past-due organization. **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/reconcile` | operator session |  |  | Compare the stored subscription with the provider's. **Who:** platform admin. |
| POST | `/api/platform/tenants/{id}/rotate-keys` | operator session |  |  | Queue a key rotation for the organization (a worker re-encrypts documents and secrets). |
| POST | `/api/platform/tenants/{id}/backup` | operator session |  |  | Download one organization's backup archive (platform admins only; recorded in the platform audit). |
| GET | `/api/platform/tenants/{id}/audit` | operator session |  |  | Export the tenant's audit log. **Who:** `platform.abuse.manage`. |
| POST | `/api/platform/tenants/{id}/support-request` | operator session |  | body: `role` | Ask the owner for time-boxed access (`reason`, `role` auditor or admin). **Who:** `platform.support.request`: support, platform admin. |
| GET | `/api/platform/plans` | operator session |  |  | Plans with versions, subscription counts and scheduled migrations. **Who:** `platform.tenants.read`. |
| PUT | `/api/platform/plans/{name}` | operator session |  | body: `entitlements`, `pricing` | Publish a new plan version (`entitlements`, `pricing`); existing subscriptions stay on theirs. **Who:** `platform.plans.manage`: platform admin. |
| POST | `/api/platform/plans/{name}/migrate` | operator session |  | body: `effective_at`, `version` | Schedule moving subscriptions to a plan version (`version`, `effective_at`). **Who:** platform admin. |
| GET | `/api/platform/flags` | operator session |  |  | Feature flags. **Who:** `platform.tenants.read`. |
| PUT | `/api/platform/flags` | operator session |  | body: `enabled`, `flag`, `percent`, `scope_id`, `scope_type` | Set a flag: `flag`, `scope_type` (global, plan, org), `scope_id`, `enabled`, `percent`. A global flag switched off is a kill switch. **Who:** `platform.flags.manage`: platform admin. |
| DELETE | `/api/platform/flags` | operator session |  | query: `flag`, `scope_id`, `scope_type` | Delete a flag scope (`flag`, `scope_type`, `scope_id` as query). **Who:** platform admin. |
| GET | `/api/platform/support` | operator session |  |  | The operator's support grants. |
| POST | `/api/platform/support/{id}/enter` | operator session |  |  | Switch the operator session into the tenant with the granted role. **Who:** an approved, unexpired grant. |
| POST | `/api/platform/support/leave` | operator session |  |  | Leave the tenant. |
| POST | `/api/platform/support/{id}/end` | operator session |  |  | End a grant. |
| GET | `/api/platform/announcements` | operator session |  |  | Announcements. **Who:** `platform.announcements.manage`. |
| POST | `/api/platform/announcements` | operator session |  | body: `body`, `hours`, `scope_id`, `scope_type`, `severity`, `title` | Announce to all, a plan or one organization (`title`, `body`, `scope_type`, `scope_id`, `severity`, `hours`). **Who:** support, platform admin. |
| DELETE | `/api/platform/announcements/{id}` | operator session |  |  | Cancel an announcement. **Who:** support, platform admin. |
| GET | `/api/platform/health` | operator session |  |  | Queue, workers, apply success by ATS, source error rates, model latency, tenant error budgets, blocked sources. **Who:** `platform.health.read`. |
| GET | `/api/platform/metrics` | operator session |  |  | Process metrics and per-tenant request counters. **Who:** `platform.health.read`. |
| GET | `/api/platform/queue` | operator session |  |  | Queue statistics by status, kind and organization; workers. **Who:** `platform.health.read`. |
| GET | `/api/platform/audit` | operator session |  | query: `before`, `limit`, `org` | Platform audit log. **Who:** `platform.audit.read`. |
| GET | `/api/platform/audit/verify` | operator session |  |  | Verify the platform audit chain. **Who:** `platform.audit.read`. |
| POST | `/api/platform/sources/block` | operator session |  | body: `reason`, `source` | Switch a job source off for every organization (`source`, `reason`). **Who:** `platform.abuse.manage`: platform admin. |
| DELETE | `/api/platform/sources/{source}` | operator session |  |  | Switch it back on. **Who:** platform admin. |
| POST | `/api/platform/dsr` | operator session |  | body: `email`, `kind` | Handle an access, export or delete request for one email across tenants (`kind`, `email`). **Who:** platform admin. |
| GET | `/api/platform/operators` | operator session |  |  | Operators. **Who:** `platform.operators.manage`: platform admin. |
| POST | `/api/platform/operators` | operator session |  | body: `email`, `name`, `password`, `role` | Create an operator (`email`, `password`, `role`, `name`). **Who:** platform admin. |
| PATCH | `/api/platform/operators/{id}` | operator session |  | body: `status` | Enable or disable an operator (`status`). **Who:** platform admin. |

## Web interface, health, disclosure and legal pages

| Method | Path | Auth | Flags | Body and query | What it does, and who may call it |
| --- | --- | --- | --- | --- | --- |
| GET | `/` | none |  |  | The web interface with the hosted scripts injected. |
| GET | `/static/{file}` | none |  |  | Static assets for the interface. |
| GET | `/api/health` | none |  |  | Liveness. |
| GET | `/api/ready` | none |  |  | Readiness: schema version, data directory, queue depth and workers. 503 when degraded. |
| GET | `/.well-known/security.txt` | none |  |  | RFC 9116 disclosure file (contact from `AIRA_CLOUD_SECURITY_CONTACT`). |
| GET | `/api/orgs/{org}/branding` | none |  |  | Logo (https only) and display name. Team and Enterprise. **Who:** `org.settings`. |
| POST | `/api/internal/remote-login/validate` | none | no CSRF check | body: `token` | The remote-desktop gateway asks which candidate's browser a sign-in token opens (signed with the broker secret; once). |
| POST | `/api/internal/remote-login/event` | none | no CSRF check | body: `event`, `outcome`, `token` | The sign-in broker reports a session's start, its heartbeats and how it ended (signed with the broker secret). |
| GET | `/robots.txt` | none |  |  | Disallows `/api/` for crawlers. |
| GET | `/legal` | none |  |  | Index of the legal pages (templates). |
| GET | `/legal/{name}` | none |  |  | A legal page rendered from `docs/legal` (`terms`, `privacy`, `dpa`, `security`, `automation-and-linkedin`). |

## Single sign-on and SCIM

Registered in `aira_cloud/sso_routes.py`. See the SSO section of [multitenant.md](multitenant.md). Routes registered when this document was generated:

Configuration (`/api/org/sso...`) is for owners and admins (`sso.manage`); SSO needs a plan with `auth.sso` (Team, Enterprise) and SCIM tokens need `auth.scim` (Enterprise). The `/api/sso/...` routes are the sign-in handshake with the identity provider (the SAML assertion consumer takes the provider's POST, so it skips the same-origin check and relies on the signed response and one-time relay state). The `/scim/v2/` routes authenticate with an organization's SCIM bearer token and answer in SCIM envelopes, errors included. Details and the exact checks are in `aira_cloud/sso.py` and `sso_routes.py`.

| Method | Path | Auth | Body and query |
| --- | --- | --- | --- |
| GET | `/api/org/sso` | member session |  |
| POST | `/api/org/sso` | member session | body: `attribute_map`, `client_id`, `client_secret`, `default_role`, `domains`, `enforced`, `group_role_map`, `groups_claim`, `idp_certificate`, `idp_entity_id`, `issuer`, `protocol`, `sso_url` |
| DELETE | `/api/org/sso` | member session |  |
| GET | `/api/org/sso/scim-tokens` | member session |  |
| POST | `/api/org/sso/scim-tokens` | member session | body: `label` |
| DELETE | `/api/org/sso/scim-tokens/{id}` | member session |  |
| POST | `/api/sso/discover` | none (sign-in handshake) | body: `email` |
| GET | `/api/sso/{org}/start` | none (sign-in handshake) |  |
| GET | `/api/sso/oidc/callback` | none (sign-in handshake) | query: `code`, `error`, `state` |
| GET | `/api/org/sso/saml-metadata` | member session |  |
| GET | `/api/sso/{org}/saml/metadata` | none (sign-in handshake) |  |
| POST | `/api/sso/{org}/saml/acs` | none (sign-in handshake) |  |
| GET | `/scim/v2/ServiceProviderConfig` | SCIM bearer token |  |
| GET | `/scim/v2/Users` | SCIM bearer token |  |
| POST | `/scim/v2/Users` | SCIM bearer token |  |
| GET | `/scim/v2/Users/{id}` | SCIM bearer token |  |
| PUT | `/scim/v2/Users/{id}` | SCIM bearer token |  |
| PATCH | `/scim/v2/Users/{id}` | SCIM bearer token |  |
| DELETE | `/scim/v2/Users/{id}` | SCIM bearer token |  |
| GET | `/scim/v2/Groups` | SCIM bearer token |  |
| POST | `/scim/v2/Groups` | SCIM bearer token |  |
| GET | `/scim/v2/Groups/{id}` | SCIM bearer token |  |
| PATCH | `/scim/v2/Groups/{id}` | SCIM bearer token |  |
| DELETE | `/scim/v2/Groups/{id}` | SCIM bearer token |  |

## Candidate workspace routes (the local build's API through the gateway)

These are the local build's routes ([api.md](api.md) documents their bodies and answers). The gateway runs them unchanged against one candidate's workspace after it has checked, in order: the session; verified email and second factor; the subscription state (`writes` while read-only); the **permission for that candidate**; the plan feature; organization policy (source allowed, LinkedIn allowed, the candidate's own opt-in). Then it binds the workspace, runs the handler, masks sensitive values for callers who may not see them, meters usage and writes an audit entry naming the actor and the candidate. `POLICY` in `aira_cloud/policy.py` is the source of truth. It defaults to deny: a route with no entry is refused, and a test fails if one is missing.

Which workspace: a candidate's own, or `X-Aira-Candidate: <member id>` for staff. Naming a candidate of another organization is a `404`; naming one in your organization that you may not act for is a `403`.

### Candidate workspace, reads (`candidates.read`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/api/linkedin/status` | `api_linkedin_status` |  |
| GET | `/api/status` | `api_status` |  |
| GET | `/api/dashboard` | `api_dashboard` |  |
| GET | `/api/sources` | `api_sources` |  |
| GET | `/api/settings` | `api_settings` |  |
| GET | `/api/audit` | `api_audit` |  |
| GET | `/api/notifications` | `api_notifications` |  |
| GET | `/api/profile` | `api_profile` | sensitive values masked |
| GET | `/api/profile/answers` | `api_answers` | sensitive values masked |
| GET | `/api/jobs` | `api_jobs` |  |
| GET | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})` | `api_job_detail` |  |
| GET | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/events` | `api_job_events` |  |
| GET | `/api/applications/queue` | `api_applications_queue` |  |
| GET | `/api/autopilot` | `api_autopilot` | sensitive answers masked |
| GET | `/api/autopilot/plans/([0-9a-f]{32})/passes` | `api_autopilot_plan_passes` |  |
| GET | `/api/autopilot/runs` | `api_autopilot_runs` | sensitive answers masked |
| GET | `/api/autopilot/runs/([0-9a-f]{32})` | `api_autopilot_run` | sensitive answers masked |
| GET | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/preparation` | `api_job_preparation` | sensitive answers masked |
| GET | `/api/interviews` | `api_interviews` |  |
| GET | `/api/interviews/([0-9a-f]{32})` | `api_interview` |  |
| GET | `/api/interviews/([0-9a-f]{32})/calendar.ics` | `api_interview_ics` |  |
| GET | `/api/interviews/calendar.ics` | `api_interviews_ics` |  |
| GET | `/api/recruiters` | `api_recruiters` |  |
| GET | `/api/recruiters/([0-9a-f]{32})` | `api_recruiter` |  |
| GET | `/api/tasks` | `api_tasks` |  |
| GET | `/api/email` | `api_email` |  |
| GET | `/api/analytics` | `api_analytics` |  |
| GET | `/api/automation` | `api_automation` |  |
| GET | `/api/activity` | `api_activity` |  |
| GET | `/api/careers/overview` | `airaapi__careers_routes__overview` |  |
| GET | `/api/careers/records/([0-9a-f]{32})/jobs` | `airaapi__careers_routes__record_jobs` |  |
| GET | `/api/careers/reports` | `airaapi__careers_routes__reports_list` |  |
| GET | `/api/careers/reports/([0-9a-f]{32})` | `airaapi__careers_routes__report_detail` |  |
| GET | `/api/careers/alerts` | `airaapi__careers_routes__alerts_list` |  |
| GET | `/api/careers/alerts/([0-9a-f]{32})` | `airaapi__careers_routes__alert_detail` |  |

### Candidate workspace, documents (`documents.read`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/api/documents` | `api_documents` |  |
| GET | `/api/documents/([0-9a-f]{32})/download` | `api_document_download` |  |
| GET | `/api/documents/([0-9a-f]{32})/versions` | `api_document_versions` |  |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/documents/snapshot` | `api_job_documents_snapshot` |  |

### Candidate workspace, edits (`candidates.write`, a coach needs `act`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| POST | `/api/profile` | `api_profile_save` | sensitive fields only by the candidate; others keep the stored values |
| POST | `/api/resume/extract` | `api_resume_extract` |  |
| POST | `/api/profile/answers` | `api_answers_save` | sensitive answers only by the candidate |
| DELETE | `/api/profile/answers/([a-z][a-z0-9_]{1,60})` | `api_answers_delete` | sensitive answers only by the candidate |
| POST | `/api/settings` | `api_settings_save` |  |
| POST | `/api/notifications/read` | `api_notifications_read` |  |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/save` | `api_job_save` |  |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/archive` | `api_job_archive` |  |
| PATCH | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/stage` | `api_job_stage` |  |
| PATCH | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/documents` | `api_job_documents` |  |
| PATCH | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/notes` | `api_job_notes` |  |
| POST | `/api/interviews` | `api_interviews_create` |  |
| PATCH | `/api/interviews/([0-9a-f]{32})` | `api_interview_update` |  |
| DELETE | `/api/interviews/([0-9a-f]{32})` | `api_interview_delete` |  |
| POST | `/api/recruiters` | `api_recruiters_create` |  |
| PATCH | `/api/recruiters/([0-9a-f]{32})` | `api_recruiter_update` |  |
| DELETE | `/api/recruiters/([0-9a-f]{32})` | `api_recruiter_delete` |  |
| POST | `/api/recruiters/([0-9a-f]{32})/interactions` | `api_recruiter_interaction` |  |
| DELETE | `/api/interactions/([0-9a-f]{32})` | `api_interaction_delete` |  |
| POST | `/api/tasks` | `api_tasks_create` |  |
| PATCH | `/api/tasks/([0-9a-f]{32})` | `api_task_update` |  |
| DELETE | `/api/tasks/([0-9a-f]{32})` | `api_task_delete` |  |
| POST | `/api/documents` | `api_documents_upload` | storage limit |
| DELETE | `/api/documents/([0-9a-f]{32})` | `api_document_delete` |  |
| POST | `/api/email/import` | `api_email_import` |  |
| POST | `/api/email/classify` | `api_email_classify` |  |
| POST | `/api/email/([0-9a-f]{32})/apply` | `api_email_apply` |  |
| PATCH | `/api/email/([0-9a-f]{32})` | `api_email_update` |  |
| DELETE | `/api/email/([0-9a-f]{32})` | `api_email_delete` |  |
| DELETE | `/api/autopilot/answers/([a-z0-9%]{1,720})` | `api_autopilot_answer_delete` |  |
| DELETE | `/api/careers/reports/([0-9a-f]{32})` | `airaapi__careers_routes__report_delete` |  |

### Searches, drafts and preparation (`search.run`, a coach needs `act`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| POST | `/api/jobs/search` | `api_jobs_search` | source check; counted as a search |
| POST | `/api/jobs/import` | `api_jobs_import` |  |
| POST | `/api/jobs/rescore` | `api_jobs_rescore` |  |
| POST | `/api/search` | `api_search_linkedin` | source check; counted as a search |
| POST | `/api/search/greenhouse` | `api_search_greenhouse` | source check; counted as a search |
| POST | `/api/search/google` | `api_search_google` | source check; counted as a search |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/details` | `api_job_details` | source check; counted as a search |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/score` | `api_job_score` |  |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/evaluate` | `api_job_evaluate` | model gateway call |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/generate` | `api_job_generate` | AI cover-letter limit and policy |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/prepare` | `api_job_prepare` |  |
| PATCH | `/api/preparations/([0-9a-f]{32})` | `api_preparation_review` |  |
| POST | `/api/autopilot/settings` | `api_autopilot_settings` |  |
| POST | `/api/autopilot/plans` | `api_autopilot_plan_create` | search-plan limit; `mode: apply` needs submit permission |
| PATCH | `/api/autopilot/plans/([0-9a-f]{32})` | `api_autopilot_plan_update` | same checks as create |
| DELETE | `/api/autopilot/plans/([0-9a-f]{32})` | `api_autopilot_plan_delete` |  |
| POST | `/api/autopilot/plans/([0-9a-f]{32})/run` | `api_autopilot_plan_run` | gate check (pause, subscription, consent) |
| POST | `/api/autopilot/runs/([0-9a-f]{32})/skip` | `api_autopilot_run_skip` |  |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/auto-apply` | `api_job_auto_apply` | `mode: apply` needs submit permission; filled and held when policy requires review |
| POST | `/api/careers/jobs/search` | `airaapi__careers_routes__jobs_search` | source check; counted as a search |
| POST | `/api/careers/jobs/import` | `airaapi__careers_routes__jobs_import` |  |
| POST | `/api/careers/(company-intel\|salary\|trends\|interview-prep\|travel\|learn)` | `airaapi__careers_routes__report_run` | source check; counted as a search |
| POST | `/api/careers/alerts` | `airaapi__careers_routes__alerts_create` | monitor limit |
| PATCH | `/api/careers/alerts/([0-9a-f]{32})` | `airaapi__careers_routes__alert_update` |  |
| DELETE | `/api/careers/alerts/([0-9a-f]{32})` | `airaapi__careers_routes__alert_delete` |  |
| POST | `/api/careers/alerts/([0-9a-f]{32})/run` | `airaapi__careers_routes__alert_run` | source check; counted as a search |
| POST | `/api/careers/alerts/([0-9a-f]{32})/dismiss` | `airaapi__careers_routes__alert_dismiss` |  |

### Held questions (`held.answer`, a coach needs `act`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| POST | `/api/autopilot/runs/([0-9a-f]{32})/answer` | `api_autopilot_run_answer` | sensitive questions are the candidate's alone; time to answer is audited |

### Approve and submit (`applications.submit`, a coach needs `approve`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| POST | `/api/autopilot/runs/([0-9a-f]{32})/submit` | `api_autopilot_run_submit` | gate check (pause, subscription, consent) |
| POST | `/api/preparations/([0-9a-f]{32})/approve` | `api_preparation_approve` |  |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/apply` | `api_job_apply` |  |
| POST | `/api/jobs/([A-Za-z0-9][A-Za-z0-9_.:-]{0,255})/submit` | `api_job_submit` | counted as an application |

### LinkedIn session, inbox, messages and schedules (the candidate's own; a coach only when policy allows messages)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| POST | `/api/linkedin/connect` | `api_linkedin_connect` | feature `sources.linkedin`; requires `acknowledge: true`; records the opt-in |
| POST | `/api/linkedin/restart` | `api_linkedin_restart` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| GET | `/api/inbox` | `api_inbox` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| GET | `/api/inbox/sync` | `api_inbox_sync_status` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| GET | `/api/conversations/([A-Za-z0-9_+=-]{1,128})` | `api_conversation` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| GET | `/api/messages/policies` | `api_message_policies` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| GET | `/api/messages/auto-reply` | `api_auto_reply_status` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| POST | `/api/inbox/sync` | `api_inbox_sync` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| DELETE | `/api/inbox/cache` | `api_inbox_cache_clear` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| POST | `/api/messages/auto-reply/run` | `api_auto_reply_run` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in; gate check (pause, subscription, consent) |
| POST | `/api/conversations/([A-Za-z0-9_+=-]{1,128})/send` | `api_conversation_send` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| PATCH | `/api/conversations/([A-Za-z0-9_+=-]{1,128})/policy` | `api_conversation_policy` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| POST | `/api/conversations/([A-Za-z0-9_+=-]{1,128})/recruiter` | `api_conversation_recruiter` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| POST | `/api/conversations/([A-Za-z0-9_+=-]{1,128})/draft` | `api_conversation_draft` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in; model gateway call |
| PATCH | `/api/conversations/([A-Za-z0-9_+=-]{1,128})/draft` | `api_conversation_draft_save` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| POST | `/api/schedules` | `api_schedules_create` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| POST | `/api/schedules/(\d+)/run` | `api_schedule_run` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in; gate check (pause, subscription, consent) |
| PATCH | `/api/schedules/(\d+)` | `api_schedule_update` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |
| DELETE | `/api/schedules/(\d+)` | `api_schedule_delete` | feature `sources.linkedin`; LinkedIn: plan, policy and the candidate's opt-in |

### Candidate export (`export.own`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/api/account/export` | `api_account_export` | audited |

### Aira Leads (`leads.read`, `leads.write`; the candidate and a coach with `act` or `approve` scope; plan feature `leads`)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/api/leads/overview` | `airaapi__leads_routes__overview` | feature `leads` |
| GET | `/api/leads/icp` | `airaapi__leads_routes__icp_get` | feature `leads` |
| GET | `/api/leads/lists` | `airaapi__leads_routes__lists_get` | feature `leads` |
| GET | `/api/leads/searches` | `airaapi__leads_routes__searches_list` | feature `leads` |
| GET | `/api/leads/items` | `airaapi__leads_routes__items_list` | feature `leads` |
| GET | `/api/leads/items/([0-9a-f]{32})` | `airaapi__leads_routes__item_get` | feature `leads` |
| GET | `/api/leads/export` | `airaapi__leads_routes__export` | feature `leads` |
| POST | `/api/leads/icp` | `airaapi__leads_routes__icp_save` | feature `leads`; refreshes the lead count |
| POST | `/api/leads/lists` | `airaapi__leads_routes__lists_create` | feature `leads` |
| PATCH | `/api/leads/lists/([0-9a-f]{32})` | `airaapi__leads_routes__lists_update` | feature `leads` |
| DELETE | `/api/leads/lists/([0-9a-f]{32})` | `airaapi__leads_routes__lists_delete` | feature `leads`; refreshes the lead count |
| POST | `/api/leads/search` | `airaapi__leads_routes__search` | feature `leads`; source gate (Google: plan, policy, kill switch; Sales Navigator: every LinkedIn gate, read on the worker that holds the session); counted as a search; a Google search with the pooled key spends the pooled allowance |
| POST | `/api/leads/searches` | `airaapi__leads_routes__searches_create` | feature `leads` |
| DELETE | `/api/leads/searches/([0-9a-f]{32})` | `airaapi__leads_routes__searches_delete` | feature `leads` |
| POST | `/api/leads/searches/([0-9a-f]{32})/run` | `airaapi__leads_routes__searches_run` | feature `leads`; source gate (Google: plan, policy, kill switch; Sales Navigator: every LinkedIn gate, read on the worker that holds the session); counted as a search; a Google search with the pooled key spends the pooled allowance |
| POST | `/api/leads/items` | `airaapi__leads_routes__items_save` | feature `leads`; stored-lead limit |
| POST | `/api/leads/items/import-csv` | `airaapi__leads_routes__items_import_csv` | feature `leads`; stored-lead limit |
| POST | `/api/leads/items/bulk` | `airaapi__leads_routes__items_bulk` | feature `leads`; refreshes the lead count |
| PATCH | `/api/leads/items/([0-9a-f]{32})` | `airaapi__leads_routes__item_update` | feature `leads` |
| DELETE | `/api/leads/items/([0-9a-f]{32})` | `airaapi__leads_routes__item_delete` | feature `leads`; refreshes the lead count |
| POST | `/api/leads/items/([0-9a-f]{32})/enrich` | `airaapi__leads_routes__item_enrich` | feature `leads`; monthly enrichment limit; reads public websites only |
| POST | `/api/leads/items/([0-9a-f]{32})/draft` | `airaapi__leads_routes__item_draft` | feature `leads` |
| GET | `/api/leads/services` | `airaapi__leads_routes__services_get` | feature `leads` |
| GET | `/api/leads/outbox` | `airaapi__leads_routes__outbox_list` | feature `leads` |
| POST | `/api/leads/outbox/drafts` | `airaapi__leads_routes__outbox_drafts` | feature `leads`; audited as counts only |
| PATCH | `/api/leads/outbox/([0-9a-f]{32})` | `airaapi__leads_routes__outbox_update` | feature `leads` |
| DELETE | `/api/leads/outbox/([0-9a-f]{32})` | `airaapi__leads_routes__outbox_delete` | feature `leads` |
| POST | `/api/leads/outbox/reject` | `airaapi__leads_routes__outbox_reject` | feature `leads`; audited as counts only |
| GET | `/api/leads/suppressions` | `airaapi__leads_routes__suppressions_list` | feature `leads` |
| POST | `/api/leads/suppressions` | `airaapi__leads_routes__suppressions_add` | feature `leads` |
| GET | `/api/leads/items/([0-9a-f]{32})/dial` | `airaapi__leads_routes__item_dial` | feature `leads` |
| POST | `/api/leads/items/([0-9a-f]{32})/call-script` | `airaapi__leads_routes__item_call_script` | feature `leads` |
| GET | `/api/leads/items/([0-9a-f]{32})/calls` | `airaapi__leads_routes__item_calls` | feature `leads` |
| POST | `/api/leads/items/([0-9a-f]{32})/calls` | `airaapi__leads_routes__item_call_log` | feature `leads` |
| GET | `/api/leads/follow-ups` | `airaapi__leads_routes__follow_ups` | feature `leads` |
| POST | `/api/leads/items/([0-9a-f]{32})/signals` | `airaapi__leads_routes__item_signals` | feature `leads`; source gate (Google: plan, policy, kill switch; Sales Navigator: every LinkedIn gate, read on the worker that holds the session); counted as a search; a Google search with the pooled key spends the pooled allowance |

### Aira Leads outreach (`leads.outreach`: the candidate only; sending, approving and the sender settings need the `leads_outreach` feature, Team and up)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/api/leads/outreach/settings` | `airaapi__leads_routes__outreach_settings_get` | feature `leads` |
| POST | `/api/leads/outreach/settings` | `airaapi__leads_routes__outreach_settings_save` | feature `leads_outreach`; the daily cap is clamped to the plan and the pause to 2 to 10 seconds; the mail server must be a public address; audited as host and cap, never the password |
| POST | `/api/leads/outreach/test` | `airaapi__leads_routes__outreach_test` | feature `leads_outreach`; kill switch and automation gates; one message to the sender's own address |
| POST | `/api/leads/outbox/approve` | `airaapi__leads_routes__outbox_approve` | feature `leads_outreach`; audited as counts only |
| POST | `/api/leads/outbox/send` | `airaapi__leads_routes__outbox_send` | feature `leads_outreach`; kill switch and automation gates; at most five messages per request, cut to the month's allowance (`lead_emails_per_month`, no overage); counts messages actually sent |
| POST | `/api/leads/suppressions/remove` | `airaapi__leads_routes__suppressions_remove` | feature `leads` |

### Organization Aira API console (owner and admin)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/api/airaapi/overview` | `airaapi__routes__overview` | feature `api.console_read` |
| GET | `/api/airaapi/account` | `airaapi__routes__account` | feature `api.console_read` |
| GET | `/api/airaapi/status` | `airaapi__routes__status` | feature `api.console_read` |
| GET | `/api/airaapi/engines` | `airaapi__routes__engines` | feature `api.console_read` |
| GET | `/api/airaapi/engines/([a-z][a-z0-9_]{0,63})` | `airaapi__routes__engine_detail` | feature `api.console_read` |
| GET | `/api/airaapi/searches` | `airaapi__routes__searches` | feature `api.console_read` |
| GET | `/api/airaapi/searches/([0-9a-f]{32})` | `airaapi__routes__search_detail` | feature `api.console_read` |
| GET | `/api/airaapi/searches/([0-9a-f]{32})/export` | `airaapi__routes__search_export` | feature `api.console_read` |
| GET | `/api/airaapi/searches/([0-9a-f]{32})/xray` | `airaapi__routes__search_xray` | feature `api.console_read` |
| GET | `/api/airaapi/diff` | `airaapi__routes__diff` | feature `api.console_read` |
| GET | `/api/airaapi/locations` | `airaapi__routes__locations` | feature `api.console_read` |
| GET | `/api/airaapi/saved` | `airaapi__routes__saved_list` | feature `api.console_read` |
| GET | `/api/airaapi/monitors` | `airaapi__routes__monitors_list` | feature `api.console_read` |
| GET | `/api/airaapi/keys` | `airaapi__routes__keys_list` | feature `api.console_read` |
| GET | `/api/airaapi/docs` | `airaapi__routes__docs` | feature `api.console_read` |
| GET | `/api/airaapi/platform` | `airaapi__routes__platform_reference` | feature `api.console_read` |
| POST | `/api/airaapi/key` | `airaapi__routes__set_key` | feature `sources.google_jobs` |
| DELETE | `/api/airaapi/key` | `airaapi__routes__remove_key` |  |
| POST | `/api/airaapi/search` | `airaapi__routes__search` | feature `api.searches`; counted against the monthly API allowance |
| DELETE | `/api/airaapi/searches` | `airaapi__routes__delete_searches` | feature `api.searches` |
| PATCH | `/api/airaapi/searches/([0-9a-f]{32})` | `airaapi__routes__search_update` | feature `api.searches` |
| DELETE | `/api/airaapi/searches/([0-9a-f]{32})` | `airaapi__routes__search_delete` | feature `api.searches` |
| POST | `/api/airaapi/searches/([0-9a-f]{32})/rerun` | `airaapi__routes__search_rerun` | feature `api.searches`; counted against the monthly API allowance |
| POST | `/api/airaapi/saved` | `airaapi__routes__saved_create` | feature `api.searches` |
| PATCH | `/api/airaapi/saved/([0-9a-f]{32})` | `airaapi__routes__saved_update` | feature `api.searches` |
| DELETE | `/api/airaapi/saved/([0-9a-f]{32})` | `airaapi__routes__saved_delete` | feature `api.searches` |
| POST | `/api/airaapi/saved/([0-9a-f]{32})/run` | `airaapi__routes__saved_run` | feature `api.searches`; counted against the monthly API allowance |
| POST | `/api/airaapi/monitors` | `airaapi__routes__monitors_create` | feature `api.searches` |
| PATCH | `/api/airaapi/monitors/([0-9a-f]{32})` | `airaapi__routes__monitors_update` | feature `api.searches` |
| DELETE | `/api/airaapi/monitors/([0-9a-f]{32})` | `airaapi__routes__monitors_delete` | feature `api.searches` |
| POST | `/api/airaapi/monitors/([0-9a-f]{32})/run` | `airaapi__routes__monitors_run` | feature `api.searches`; counted against the monthly API allowance |
| POST | `/api/airaapi/keys` | `airaapi__routes__keys_create` | feature `api.v1`; limited by the plan's key count |
| DELETE | `/api/airaapi/keys/([0-9a-f]{32})` | `airaapi__routes__keys_revoke` | feature `api.v1` |

### SerpApi-compatible API (an organization Aira key, not a session)

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `(?:/v1)?/search(?:\.(json\|html))?` | `airaapi__routes__v1_search` | feature `api.v1`; per-plan rate limit; counted against the monthly API allowance |
| GET | `(?:/v1)?/account(?:\.json)?` | `airaapi__routes__v1_account` | feature `api.v1` |
| GET | `(?:/v1)?/locations(?:\.json)?` | `airaapi__routes__v1_locations` | feature `api.v1` |
| GET | `/v1/searches\.json` | `airaapi__routes__v1_searches` | feature `api.v1` |
| GET | `(?:/v1)?/searches/([A-Za-z0-9_-]{1,80})(?:\.(json\|html))?` | `airaapi__routes__v1_search_detail` | feature `api.v1` |
| GET | `/v1/engines(?:\.json)?` | `airaapi__routes__v1_engines` | feature `api.v1` |

### Replaced or not available in the hosted product

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/api/apps` | `api_apps` |  |
| GET | `/api/auth/me` | `api_auth_me` |  |
| POST | `/api/auth/register` | `api_auth_register` |  |
| POST | `/api/auth/login` | `api_auth_login` |  |
| POST | `/api/auth/launch` | `api_auth_launch` |  |
| GET | `/api/auth/account-link` | `api_auth_account_link` |  |
| POST | `/api/auth/logout` | `api_auth_logout` |  |
| POST | `/api/account/delete` | `api_account_delete` |  |

### Static, health and metrics

| Method | Path | Handler | Extra rule |
| --- | --- | --- | --- |
| GET | `/` | `ui_index` |  |
| GET | `/static/([A-Za-z0-9_.-]+)` | `ui_static` |  |
| GET | `/api/health` | `api_health` |  |
| GET | `/api/ready` | `api_ready` |  |
| GET | `/api/metrics` | `api_metrics` |  |
