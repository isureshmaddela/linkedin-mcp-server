# Plans, entitlements and billing rules

The numbers are the defaults in `aira_cloud/plans.py` (`DEFAULT_PLANS`). They are the scope document's proposal, not a pricing decision: validate them with pricing work. An operator can publish a new plan version without a deploy ([multitenant-operations.md](multitenant-operations.md)); new subscriptions use the new version and existing ones keep theirs until a migration date is scheduled. Prices below are the defaults in US dollars.

Applies to the hosted build only. See [multitenant.md](multitenant.md) for how limits are enforced.

## The plans

| Entitlement | Trial (14 days) | Starter | Team | Enterprise |
| --- | --- | --- | --- | --- |
| Price | free | $14 per candidate seat per month, 5 seats minimum | $11 per candidate seat per month, 25 seats minimum | by contract (sales); activated by an operator |
| Candidate seats | 3 | up to 25 | up to 250 | 5,000 by default, set per contract |
| Coach and admin seats (owner, admin, coach) | 2 | 3 | 25 | 500 by default |
| Applications per candidate per day | 3 | 10 | 25 | 50 |
| Applications per organization per month | 50 | 1,500 | 15,000 | 250,000 |
| Job sources | Aira Jobs, company boards | + Google Jobs (the organization's SerpApi key) | + LinkedIn (the candidate's own session) | same as Team; priority support for new sources is listed as a feature but has no code behind it |
| Automatic submission | review mode only | yes | yes | yes |
| Search plans per candidate | 2 | 5 | 10 | 25 |
| AI cover letters per month | 20 | 500 | 5,000 | 100,000; an organization's own model endpoint |
| API app (console and `/v1`) | every app is on: read-only history | not available (the API app is sold from Team) | add-on, $3 per candidate seat per month: 10,000 searches; `/v1` with 5 keys, 120 requests per minute | included: 250,000 searches; 50 keys, 600 requests per minute |
| Monitors and alerts per candidate | 2 | 10 | 25 | 100 |
| Marketing app, with Leads (lead search incl. people asking for services, lists, enrichment, drafts, call assist) | every app is on | add-on, $5 per candidate seat per month: Google sources (Maps, web, hiring signals, news) with the organization's key; drafts only | add-on, $4 per candidate seat per month; + sending lead email (`leads_outreach`) | included |
| Saved leads per organization | 0 | 2,000 | 20,000 | 200,000 |
| Lead enrichments (company website reads) per month | 0 | 200 | 2,500 | 50,000 |
| Outreach email (draft, approve, send through the candidate's own mail server) | no (drafts only) | drafts only | yes | yes |
| Outreach emails sent per month | 0 | 0 | 1,500 | 25,000 |
| Outreach emails sent per day (the sender's own daily cap, clamped by the plan) | 0 | 0 | 50 | 100 |
| Document storage | 100 MB | 2 GB | 25 GB | 500 GB |
| Retention ceiling (days an organization may keep documents) | 30 | 365 | 1,095 | 2,555 |
| Audit log kept | 30 days | 1 year | 3 years | 7 years |
| Coach scopes | view | view, act | view, act, approve | view, act, approve |
| Sign-in | password, MFA | password, MFA | + SSO (OIDC, SAML), MFA required for owners and admins | + SCIM provisioning |
| Branding (name and logo in email) | no | no | yes | yes |
| Pooled SerpApi searches (platform key) | no | no | 2,000 per month (policy switch) | 50,000 per month |
| Support | community | email | email, 1 business day | named contact |
| Metered overage | no | no | rates below, if enabled | rates below, if enabled |

Notes on rows: MFA is available on every plan; the plan only decides whether it is *required* (owners and admins where SSO is available). The Enterprise features act as follows: `siem_export` (audit poll stream), `legal_hold`, `priority_sources` (queue priority) and `custom_limits` (an operator can override any limit for one organization, see Enterprise below) are all enforced in code; audit export as a file is available on every plan.

## What each entitlement means

| Entitlement | Meaning and enforcement |
| --- | --- |
| Candidate seats | Active candidate members plus pending candidate invitations. The limit is the lower of the plan's maximum and the seats purchased. Inviting past it is `402 plan_required`. |
| Coach and admin seats | Active owners, admins and coaches plus their pending invitations. Billing contacts and auditors take no seat. |
| Applications per candidate per day | Counted per candidate and UTC day when a run starts (a unit is reserved before the browser runs and returned if it did not submit). The effective daily cap is the lowest of this limit, the organization policy's daily cap, and the candidate's own setting. |
| Applications per organization per month | Counted per calendar month (UTC). Reaching it stops automatic applications; manually recorded submissions are always allowed and still counted. |
| Job sources | A plan feature per source (`sources.aira_jobs`, `sources.company_boards`, `sources.google_jobs`, `sources.linkedin`). The organization's policy must also allow the source, and LinkedIn needs the candidate's own acknowledged opt-in. |
| Automatic submission | `automation.submit`. Without it, runs fill the form and wait for a person to submit. With it, the organization policy and the candidate's own search plan must both say automatic. |
| Search plans per candidate | Counted when a plan is created. After a downgrade, an organization with candidates above the new limit has automation paused until they are within it. |
| AI cover letters | Counted per organization and calendar month when a cover letter is generated; refused by the plan and by the organization's policy switch. |
| API console and `/v1` | The API app must be on (see Apps below): every `/api/airaapi/*` and `/v1/*` route is `402 app_required` without it, except the organization's SerpApi key routes, which Careers' Google Jobs needs. Then `api.console_read` shows history; `api.searches` allows live searches in the console; `api.v1` allows the SerpApi-compatible endpoints with organization Aira keys. Live searches count against the monthly allowance; a cached answer is returned to the allowance. `/v1` also has a per-minute limit per organization. Candidates' own job searches are not counted against this allowance. |
| Monitors and alerts | Enabled monitors per candidate. |
| Aira Leads | A section of the Marketing app (see Apps below): without Marketing every `/api/leads/*` route is `402 app_required` naming Marketing. Leads is the organization's shared pipeline: owners, admins and coaches use it, auditors read it, candidates and billing contacts get `403`. A Google source (Maps, web, hiring signals, company news, people asking for services) is `sources.google_jobs`: it needs the plan feature, the organization's policy to allow Google Jobs, and the platform's source switch to be on; it spends the organization's SerpApi key (or the pooled allowance), counts one `searches` per search, and the pooled allowance by the credits spent. LinkedIn Sales Navigator is refused (`403 source_unavailable`): a shared pipeline never reads a person's own LinkedIn session, so a list is imported as a CSV file. The organization's leads count toward `leads_stored`. Sending lead email (approve, send, sender settings) needs the `leads_outreach` feature (Team and up; Marketing on Starter drafts but does not send). Aira never places a call: there is no dialler. |
| Saved leads | Total leads across the organization's workspaces (counted from workspace snapshots, at most a minute stale for other candidates). Saving by hand or importing a CSV that would not fit is `402 plan_required`. A search that saves what it finds (`auto_add`) and has less room than one page of results shows its results without saving them (response header `X-Aira-Leads-Auto-Add`), so the plan is never overshot. |
| Outreach emails | One is counted per message actually sent. A send request carries at most five messages in the hosted build (the pause between them is 2 to 10 seconds), is cut to what is left of the month, and the unused part of its reservation is returned. At the month's limit it is `402 plan_required`; there is no overage price for extra emails, so overage never buys more. The sender's daily cap is clamped on the server to the plan (Team 50, Enterprise 100), at save time and again at send time. Operators can override both limits. |
| Lead enrichments | One is reserved when the website of a lead is about to be read and returned if no page could be read (not public, blocked by robots.txt, down). `402 plan_required` at the monthly limit. |
| Document storage and retention | Total stored bytes across the organization's candidates; uploads that would exceed it are refused. The retention ceiling (the lower of the plan or contract limit and the organization's policy) applies to documents already stored as well as new ones: the daily tick purges anything older, unless a legal hold is in place. |
| Audit log | Rows older than the window are pruned daily; the hash chain stays verifiable. |
| Coach scopes | The highest scope a coach can have: `view` reads, `act` also searches and prepares, `approve` also submits. An assignment above the plan's ceiling is capped at once after a downgrade. |
| SSO, SCIM | Configured by owners and admins (SSO) and by tokens an owner creates (SCIM, Enterprise). |

## Apps

A plan sets seats, limits and the base price; the apps ([apps.md](apps.md), catalog `aira/apps.py`) are turned on per organization. The defaults are `APP_PRICING` in `aira_cloud/plans.py`; `AppSettings.state(plan, app)` (the defaults with the superadmin's changes) is `included`, `addon` or `unavailable`.

| App | Trial | Starter | Team | Enterprise |
| --- | --- | --- | --- | --- |
| Careers | included | included | included | included |
| Developers (API) | included | not available | add-on, $3 per candidate seat per month | included |
| Business apps: Sales, Marketing (with Leads), Service, Commerce, Courses & Events, Ads | included | add-on, $5 per candidate seat per month | add-on, $4 per candidate seat per month | included |
| Everyday apps: Social, Health, Learn, Play, Planner, News, Videos | included | add-on, $2 per candidate seat per month | add-on, $1.50 per candidate seat per month | included |

**Ads spend is separate from the seat price.** An advertiser prepays an ad balance: a top-up (`POST /api/org/ads/topup`,
$5 to $10,000, owners and billing contacts) is one invoice item at the organization's billing provider, billed with
its next invoice, and credited to the balance when the provider accepted it (an `ads_topup` invoice row; a trial
cannot top up). Ads draw on the balance as they are shown or clicked, and pause at zero. The creator of each video an
ad ran on earns 55% of the charge (the superadmin sets 0 to 90% and the minimum payout, $10 by default, under
**Creators & ads**); Aira keeps the rest. Payouts to creators are made outside Aira and recorded with their
reference. Videos and ad files count against the plan's `storage_bytes`. See [apps/ads.md](apps/ads.md).

These are the defaults. The superadmin can change any sold app's price per plan (per seat, included, or not
offered), or switch an app off for every organization, from the platform console's **Apps** page
(`/api/platform/apps`; stored in `platform_apps`, read through `entitlements.AppSettings`). Quotes and the Apps
page use a new price at once; paying subscriptions are rewritten at the provider without proration and pay it from
their next period. An app switched off is closed to everyone (trial, Enterprise and grants too), answers
`403 app_disabled`, and is not billed; data and add-on choices are kept for when it is switched back on. See
[apps.md](apps.md#the-superadmins-catalog-controls).

- **What an add-on costs**: the app's seat price × the plan's candidate seats, every month, on the same subscription and invoice as the plan (one line per app; `GET /api/org/billing` and `GET /api/org/apps` show `monthly_cents` and `addons_monthly_cents`). Turning an app on mid-period is charged now, prorated for the rest of the period (a `proration` invoice), like a seat upgrade. A seat change re-prices every add-on with the plan.
- **Who**: `billing.manage` (owner, billing contact) turns apps on and off (`POST /api/org/apps/<app>/quote|subscribe|unsubscribe|keep`); admins and auditors read the page. Changes are refused while the organization is past due, read-only or cancelled (`402 subscription_inactive`); what was bought keeps working while dunning runs.
- **Turning off** takes effect at the period end (`cancel_at_period_end`); the app works until then and `keep` undoes it. A cancelled plan takes its add-ons with it; reactivating keeps them. A downgrade to a plan that does not sell an add-on (Team → Starter with the API app) cancels that app when the downgrade applies, with a notice on the dashboard and `billing.app_cancelled` in the audit log naming the plan.
- **Gating**: a route an app owns (`route_prefixes` in the catalog) answers `402 app_required` `{app, name, plan_hint, required_plan, current_plan, audience}` until the organization has the app; a candidate's message states the organization's rule and carries no sales prompt. Careers' routes are never gated. The app switcher (`GET /api/apps`, `apps[]` in `GET /api/auth/me`) lists every app with `enabled` and a `reason`.
- **Grants**: a platform administrator can grant an app to a tenant at no charge (`PUT /api/platform/tenants/<id>/apps/<app>` with `status: granted` and a note), also where the plan does not sell it, and revoke it later; both audit logs record `billing.app_granted` / `billing.app_revoked`. A grant cannot replace an app the organization pays for, and the organization cannot turn a grant off.
- **Stripe**: each add-on is a second subscription item (`metadata.app_id`, a product per app) on the organization's subscription, updated with `proration_behavior: create_prorations` when turned on and removed without proration at the period end. The stand-in provider records the same lines.

## Billing rules

- **Upgrade** (higher plan, or more seats): applies immediately; the charge is prorated for the rest of the period and the period does not restart.
- **Downgrade** (lower plan, or fewer seats): scheduled for the end of the period. At that moment the plan changes. If the organization is over any new limit (candidate seats, coach and admin seats, search plans per candidate) automation pauses until it is within them, and resumes by itself when removals bring it within. The owner and billing contacts are emailed.
- **Seats** can be raised up to the plan's maximum; Starter needs at least 5 and Team at least 25. Checkout cannot buy fewer seats than are in use or invited.
- **Trial**: 14 days. A notice goes out three days before it ends. Without a paid plan it becomes `cancelled`: read-only for 30 days (export available), then deleted. An operator can extend a trial by 1 to 90 days.
- **Payment failure**: automation stops immediately; reads and edits continue. After **14 days** from the first failure the organization is read-only. After **60 days** a deletion date is set for 30 days later and a final notice is sent. A successful payment at any time before deletion restores the organization. The owner and billing contacts are emailed at each step.
- **Cancellation**: stops renewal; service continues to the end of the period; then 30 days of read-only export; then deletion. Reactivating before the period ends undoes it; afterwards the organization buys a plan again.
- **Closure**: the owner confirms with their password; an export is created; the subscription is cancelled and the organization is deleted after the 30-day window unless the owner cancels the closure.
- **Overage**: on plans with the `overage` feature (Team, Enterprise), when a metered limit is reached the action is allowed, counted as overage, and priced per unit: applications $0.10 (Team) or $0.08 (Enterprise), AI cover letters $0.03 or $0.02, API searches (console, `/v1`, organization monitors) $0.02 on both, pooled SerpApi searches $0.04 or $0.03. Overage is invoiced once for the previous month. It only applies when switched on for the organization: an owner or billing contact turns it on from the billing page (`POST /api/org/billing/overage`), Enterprise activation switches it on. Starter and Trial always stop at the limit. Jobs-tab searches made with the organization's own SerpApi key are counted but have no limit and no rate.
- **Enterprise**: an operator activates it with seats, per-contract overrides of any limit or feature, a term and an invoice, and marks the invoice paid. There is no self-serve Enterprise checkout. After activation an operator can change the contract limits for that organization (`PUT /api/platform/tenants/<id>/limits` with a reason; `DELETE` returns it to the plan's limits). Only the named limits can be set (seats, applications, searches, cover letters, API and pooled searches, `/v1` keys and rate, monitors, storage, retention, audit days, coach scope); a whole number sets it and `null` removes the cap. Both audit logs record who, what, why and the values before and after. Lowering a limit below current use pauses automation until the organization is within it, like a downgrade.
- **Credits**: an operator can record a credit against an organization; credits are recorded for finance and are not applied automatically to provider invoices.

## States an organization can be in

| State | Automation | Reads | Writes | Exports | How it ends |
| --- | --- | --- | --- | --- | --- |
| `trialing` | yes (review mode only) | yes | yes | yes | pick a plan, or the trial ends |
| `active` | yes | yes | yes | yes | payment fails, cancels, or closes |
| `past_due` | stopped | yes | yes | yes | payment, or 14 days |
| `read_only` | stopped | yes | no | yes | payment, or deletion at day 90 |
| `cancelled` | stopped | yes | no | yes (30 days) | choose a plan again, or deletion |
| `deleted` | no | no | no | no | final; billing record and audit stub kept |

A suspended organization (an operator action, for abuse or contract reasons) is blocked in every state until reinstated.
