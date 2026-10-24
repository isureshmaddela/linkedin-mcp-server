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
| Aira API console | read-only history | 1,000 searches per month, no `/v1` | 10,000 searches; `/v1` with 5 keys, 120 requests per minute | 250,000 searches; 50 keys, 600 requests per minute |
| Monitors and alerts per candidate | 2 | 10 | 25 | 100 |
| Aira Leads (lead search incl. people asking for services, lists, enrichment, drafts, call assist) | no | yes: Google sources (Maps, web, hiring signals, news) with the organization's key | + LinkedIn Sales Navigator (the candidate's own session, needs the LinkedIn feature) | same as Team |
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
| Aira API console and `/v1` | `api.console_read` shows history; `api.searches` allows live searches in the console; `api.v1` allows the SerpApi-compatible endpoints with organization Aira keys. Live searches count against the monthly allowance; a cached answer is returned to the allowance. `/v1` also has a per-minute limit per organization. Candidates' own job searches are not counted against this allowance. |
| Monitors and alerts | Enabled monitors per candidate. |
| Aira Leads | The `leads` feature (Starter and up) opens `/api/leads/*` in a candidate's own workspace. Who may use it: the candidate, and a coach assigned with `act` or `approve` scope (permissions `leads.read` and `leads.write`); a `view` coach, owners, admins, auditors and billing contacts do not read leads (they are third parties' contact details; an owner or admin can delete a candidate's whole workspace, but staff exports leave the lead tables out). A Google source (Maps, web, hiring signals, company news) is `sources.google_jobs`: it needs the plan feature, the organization's policy to allow Google Jobs, and the platform's source switch to be on; it spends the organization's SerpApi key (or the pooled allowance), counts one `searches` per search, and the pooled allowance by the credits spent. Sales Navigator is LinkedIn automation: plan feature `sources.linkedin`, policy `linkedin_allowed`, the candidate's own acknowledged opt-in and the kill switch, and it is read on the worker host that holds the candidate's session. A dry run reads and spends nothing. Finding people who ask for services (`google_intent`) is a Google search like the others. **Outreach**: drafting emails, editing, rejecting, the do-not-contact list, call scripts, the call log and follow-ups use `leads.read` and `leads.write` (the candidate and a coach with `act` or `approve` scope); approving a message, sending, changing the sender identity or mail server, sending the test message and removing a do-not-contact entry use `leads.outreach`, which **only the candidate holds** (a coach never sends from a candidate's mail account, and editing a message withdraws its approval). Approving, sending and the sender settings need the `leads_outreach` feature (Team and up): `402 plan_required` names the plan. Aira never places a call: there is no dialler. |
| Saved leads | Total leads across the organization's workspaces (counted from workspace snapshots, at most a minute stale for other candidates). Saving by hand or importing a CSV that would not fit is `402 plan_required`. A search that saves what it finds (`auto_add`) and has less room than one page of results shows its results without saving them (response header `X-Aira-Leads-Auto-Add`), so the plan is never overshot. |
| Outreach emails | One is counted per message actually sent. A send request carries at most five messages in the hosted build (the pause between them is 2 to 10 seconds), is cut to what is left of the month, and the unused part of its reservation is returned. At the month's limit it is `402 plan_required`; there is no overage price for extra emails, so overage never buys more. The sender's daily cap is clamped on the server to the plan (Team 50, Enterprise 100), at save time and again at send time. Operators can override both limits. |
| Lead enrichments | One is reserved when the website of a lead is about to be read and returned if no page could be read (not public, blocked by robots.txt, down). `402 plan_required` at the monthly limit. |
| Document storage and retention | Total stored bytes across the organization's candidates; uploads that would exceed it are refused. The retention ceiling (the lower of the plan or contract limit and the organization's policy) applies to documents already stored as well as new ones: the daily tick purges anything older, unless a legal hold is in place. |
| Audit log | Rows older than the window are pruned daily; the hash chain stays verifiable. |
| Coach scopes | The highest scope a coach can have: `view` reads, `act` also searches and prepares, `approve` also submits. An assignment above the plan's ceiling is capped at once after a downgrade. |
| SSO, SCIM | Configured by owners and admins (SSO) and by tokens an owner creates (SCIM, Enterprise). |

## Billing rules

- **Upgrade** (higher plan, or more seats): applies immediately; the charge is prorated for the rest of the period and the period does not restart.
- **Downgrade** (lower plan, or fewer seats): scheduled for the end of the period. At that moment the plan changes. If the organization is over any new limit (candidate seats, coach and admin seats, search plans per candidate) automation pauses until it is within them, and resumes by itself when removals bring it within. The owner and billing contacts are emailed.
- **Seats** can be raised up to the plan's maximum; Starter needs at least 5 and Team at least 25. Checkout cannot buy fewer seats than are in use or invited.
- **Trial**: 14 days. A notice goes out three days before it ends. Without a paid plan it becomes `cancelled`: read-only for 30 days (export available), then deleted. An operator can extend a trial by 1 to 90 days.
- **Payment failure**: automation stops immediately; reads and edits continue. After **14 days** from the first failure the organization is read-only. After **60 days** a deletion date is set for 30 days later and a final notice is sent. A successful payment at any time before deletion restores the organization. The owner and billing contacts are emailed at each step.
- **Cancellation**: stops renewal; service continues to the end of the period; then 30 days of read-only export; then deletion. Reactivating before the period ends undoes it; afterwards the organization buys a plan again.
- **Closure**: the owner confirms with their password; an export is created; the subscription is cancelled and the organization is deleted after the 30-day window unless the owner cancels the closure.
- **Overage**: on plans with the `overage` feature (Team, Enterprise), when a metered limit is reached the action is allowed, counted as overage, and priced per unit: applications $0.10 (Team) or $0.08 (Enterprise), AI cover letters $0.03 or $0.02, Aira API searches (console, `/v1`, organization monitors) $0.02 on both, pooled SerpApi searches $0.04 or $0.03. Overage is invoiced once for the previous month. It only applies when switched on for the organization: an owner or billing contact turns it on from the billing page (`POST /api/org/billing/overage`), Enterprise activation switches it on. Starter and Trial always stop at the limit. Jobs-tab searches made with the organization's own SerpApi key are counted but have no limit and no rate.
- **Enterprise**: an operator activates it with seats, per-contract overrides of any limit or feature, a term and an invoice, and marks the invoice paid. There is no self-serve Enterprise checkout. After activation an operator can change the contract limits for that organization (`PUT /api/platform/tenants/<id>/limits` with a reason; `DELETE` returns it to the plan's limits). Only the named limits can be set (seats, applications, searches, cover letters, Aira API and pooled searches, `/v1` keys and rate, monitors, storage, retention, audit days, coach scope); a whole number sets it and `null` removes the cap. Both audit logs record who, what, why and the values before and after. Lowering a limit below current use pauses automation until the organization is within it, like a downgrade.
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
