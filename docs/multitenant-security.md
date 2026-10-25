# Hosted build: security controls, threat model and assurance plan

**Template status.** This maps the scope document's security requirements to the code that implements them, lists what is not implemented, and gives test and audit plans. It is not a certification, a penetration-test report or legal advice. The local build's controls are in [security.md](security.md); the hosted build carries them over (CSP and security headers, same-origin checks, rate limits, redaction, append-only audit, encrypted documents, sensitive-field exclusion from model prompts) and adds the items below.

See also [multitenant.md](multitenant.md) (design and gaps) and [multitenant-operations.md](multitenant-operations.md) (running it).

## 1. Isolation model

Three layers, from outermost to innermost:

1. **Identity.** Every request resolves to a session, and a session to one member of one organization (or to an operator with an approved support grant). The organization is read from the session, never from a path, query or body. See `aira_cloud/gateway.py`, `identity.py`.
2. **Authorization.** `Authorizer.require` (`rbac.py`) checks a permission for a named candidate. A candidate that is not in the caller's organization is a `404` for every role, so identifiers cannot be probed. A coach's access is checked against `coach_assignments` on every call and capped by the plan.
3. **Data separation.** Each candidate's data is a separate SQLite database in the tenant's own directory; documents are encrypted with the organization's own data key under the tenant's prefix; the organization's API workspace is separate again. A request binds exactly one workspace (`workspace.bind`), and the shared domain code only ever sees that one connection. Hooks the domain code calls (documents, key, model, limits) read the bound tenant and raise if none is bound, so they fail closed.

Database-enforced second check (PostgreSQL only): every table with an organization column has row-level security, forced, with a policy comparing it to the organization of the request or job; the application connects as a role that is neither owner nor `BYPASSRLS` and cannot turn the policies off (tested). Code with no organization yet (sign-in, webhooks, the scheduler) uses a separate platform role. On the default SQLite control plane services filter by `org_id` on every query, so isolation there rests on application code and on the test suite.

A test, `tests/test_cloud_isolation.py`, has tenant A call every route of the hosted API with tenant B's identifiers (in the path, the `X-Aira-Candidate` header, the query string and the body) and fails if any call returns B's data or changes B's state. It also fails if a route is neither exercised nor in a named exempt category with a reason, so a new route cannot be added silently. `tests/test_cloud_tenancy.py` checks that deleting an organization leaves no row keyed by its `org_id` in any table outside the retained set.

## 2. Controls from the scope document, and the code behind them

| Area | Requirement (scope) | Implemented in | Status |
| --- | --- | --- | --- |
| Tenant isolation | `org_id` on every tenant table, set from the session; row-level security as a second check; an automated test calling every route as tenant A with tenant B's ids and expecting 404 | `schema.py`, `gateway.py`, `rbac.py`, `workspace.py`; `tests/test_cloud_isolation.py` | `org_id` and the route sweep done; row-level security built for PostgreSQL (`tests/test_cloud_postgres.py`), **not available on SQLite** |
| Secrets | per-organization document keys and SerpApi keys in a secret manager (KMS-backed); no secrets in environment files; rotation without downtime | `crypto.py` (`SecretManager`, `MasterKeyProvider`, `LocalMasterKey`), `hooks.py`, `storage.py` | per-organization keys and encrypted organization secrets done; **KMS provider not built** (a local file or one environment variable holds the master key); rotation re-encrypts control-plane secrets but **not document blobs or the SerpApi key stored in the API workspace** |
| Sign-in | MFA on all plans, required for owners and admins on Team and Enterprise; SSO on Team and Enterprise; session rotation on role change; device sign-out | `identity.py` (TOTP, recovery codes, `session_epoch`, session list and revoke), `sso.py`, `sso_routes.py`, `gateway.check_session_gates`; `tests/test_cloud_identity.py`, `tests/test_cloud_sso.py` | done; the SSO pieces are as strong as their tests (signature wrapping, replay and DOCTYPE cases are covered in `test_cloud_sso.py`) |
| Rate limits | per organization and per member, not per IP; `/v1` limits per Aira key | `gateway.py` (client, organization and member limiters; an operator can lower a tenant's limit), `v1.py` (per-plan per-minute limit; the key's own hourly limit from the API) | done; counters are in memory per process, so with several replicas each enforces its own window |
| Data residency | one region per deployment; the region is part of the organization record | `organizations.region` set from `AIRA_CLOUD_REGION` at sign-up | done |
| Encryption | TLS 1.2+ in transit; documents and backups encrypted at rest; volume encryption | `gateway.make_server` (TLS 1.2 minimum when a certificate is configured), `documents` encrypted by Fernet with the organization key | documents done; **TLS and volume/backup encryption are deployment responsibilities**; no HSTS header is set by the app |
| Privacy | data-processing agreement per organization, records of processing, data-subject requests within retention rules; GDPR and CCPA | `lifecycle.data_subject_request`, exports, deletion, retention; [legal/dpa.md](legal/dpa.md), [legal/privacy.md](legal/privacy.md) | tooling done; **agreements are templates awaiting counsel**; records of processing are yours to keep |
| Consent | recorded at onboarding with a version and date: automation in the candidate's name, coach access at the granted scope, the sensitive-field policy | `tenancy.record_consent`, table `consents` (kinds `automation`, `coach_access`, `sensitive_policy`, `sensitive_access`), `CONSENT_VERSION`; the gate refuses automation without the current consents | done |
| Sensitive fields | never returned to a coach without policy and consent, never sent to the model, never filled without a confirmed answer | `redaction.py` (masking on profile, answers and runs), `route_hooks.py` (writes by anyone but the candidate are refused; a save from a masked view keeps stored values), `rbac.can_see_sensitive`; prompts exclude sensitive fields in the shared code (`aira/candidate.py`) | done; verified at the permission level in tests, and by the route hooks |
| Automation ethics | no guessed answers, no CAPTCHA bypass, no sign-in on employer sites, evidence stored for every submission | unchanged from the local build (`aira/apply_engine.py`, `aira/autopilot.py`); every submission is audited with actor and candidate (`hooks.on_submission`) | carried over; stated in [legal/automation-and-linkedin.md](legal/automation-and-linkedin.md) |
| LinkedIn | Easy Apply and inbox automation off by default, enabled per candidate with a stated risk notice, restrictable by plan or removable | `policy.py` (`LINKEDIN_NOTICE`), `route_hooks.linkedin_pre`, `gate.source_block`, plan feature `sources.linkedin`, flags `sources.linkedin` and `linkedin.easy_apply` (kill switches), table `workspaces.linkedin_opt_in_at` and `easy_apply_opt_in_at` | done; the legal position is open (counsel) |
| Job boards | follow each board's published terms, request API keys, respect rate limits across tenants | unchanged Aira Jobs adapters (`aira/airaapi/jobboards.py`); a platform-wide kill switch per source (`blocked_sources`) | per-board terms carried over; **platform-level board API keys and cross-tenant rate budgeting not built** |
| Logging and retention | no credentials, tokens or document contents in logs; `org_id` and correlation id on every line; log retention 90 days; audit retention per plan | `aira/observability.py` (redaction, `org_id` field), `audit.scrub` (credential-shaped keys removed from audit details), `audit.prune` (plan audit window); `gateway.py` logs method, path, status only | done except **log retention, which is deployment configuration** |
| Vulnerability management | dependency scanning in CI, a disclosure address, patch SLAs by severity | `.github/workflows/cloud.yml` (`pip-audit`), `/.well-known/security.txt` (`AIRA_CLOUD_SECURITY_CONTACT`), [legal/security.md](legal/security.md) | scanning and disclosure file done; SLAs are a policy in [legal/security.md](legal/security.md) and need an owner |
| Certifications | SOC 2 Type I within 12 months of launch | section 6 below | **not started**; the plan is below |

## 3. Other controls worth knowing

- **Passwords**: scrypt (N=2^15, r=8, p=1) with per-password salt, stored with their parameters; minimum 12 characters, a small blocklist, no email name; unknown email addresses take the same time as known ones.
- **Sessions**: random 256-bit tokens, stored as SHA-256; cookies `HttpOnly`, `SameSite=Strict`, `Secure` over https; 7-day absolute lifetime (operators 12 hours); a password change, reset or role change ends all sessions.
- **Lockout**: eight failed password or second-factor attempts lock an account for 15 minutes; an MFA failure streak also ends the session.
- **Webhooks**: HMAC-SHA256 over `timestamp.payload`, five-minute tolerance, constant-time comparison, any one of several signatures accepted during rotation, replay stopped by the event id.
- **Invitations and links**: single-use random tokens stored as hashes; verification and reset links last 24 hours, invitations 7 days; a used or expired link answers `410`.
- **Support access**: needs the owner's approval, at most 24 hours, shows a banner to the tenant, and is written to both audit logs. Operator MFA is mandatory. A grant never carries billing, consent, LinkedIn or sensitive-field permissions.
- **Audit**: append-only by trigger (updates always refused; deletes refused except during retention pruning and organization deletion), each row hashed with its predecessor (`audit.verify`). The platform audit log is separate and has no plan-based pruning (keep it seven years).
- **Exports and downloads**: organization exports require an export permission and expire in 30 days; a departed candidate's one-link download is a random token valid 30 days.
- **SSRF**: SSO discovery and token endpoints must be https to a public host (private, loopback, link-local and reserved addresses are refused, with a development opt-in).
- **Content security**: the shared CSP forbids inline script; the hosted pages add no inline script; the stand-in checkout page uses plain forms.

## 4. Threat model

Assets: candidates' personal data (profiles, resumes, answers, messages), tenants' documents and keys, credentials and sessions, the billing relationship, the platform's own operator access.

| Threat | Example | Mitigations | Residual risk |
| --- | --- | --- | --- |
| Cross-tenant data access | a bug returns another tenant's record; a guessable id | session-derived tenant, per-candidate databases, 404 for foreign ids, route sweep test, tenant-keyed documents | no database-level enforcement; a new route that forgets the pattern is caught only by the sweep test |
| Privilege escalation inside a tenant | a coach submits without `approve`; a coach reads sensitive fields; an admin edits a candidate | permission matrix in code, plan caps on coach scope, hooks for sensitive writes and automatic submission, policy and consent checks | the matrix is only as right as `rbac.py`; review it with every role change |
| Account takeover | credential stuffing, a stolen session, phishing | lockout, scrypt, MFA (required for privileged roles on SSO plans), session epoch, device sign-out, SSO | MFA is optional for candidates and for owners of plans without SSO |
| Operator abuse or compromise | an operator reads tenant data | no tenant data in the platform console, consent-gated time-boxed grants, mandatory operator MFA, separate cookie and audit, no sensitive permissions via support | a compromised platform admin can suspend or delete tenants and edit plans |
| Billing fraud or tampering | a forged webhook upgrades a plan | signed webhooks with a tolerance window, idempotent processing, server-side prices | the stand-in provider must never be exposed in production |
| Automation abuse | one tenant floods the queue; an employer is spammed | per-organization concurrency and rate limits, daily caps per candidate, review mode default, operator rate limit and source kill switch | abuse inside a tenant's own caps is the tenant's policy |
| LinkedIn enforcement | accounts restricted | off by default, per-candidate acknowledged opt-in, kill switches | residual risk to candidates who opt in |
| Secrets exposure | a leaked master key or backup | per-organization keys, documents encrypted before storage, secrets never logged | one master key protects every organization's keys until a KMS is used |
| Data loss | disk failure, accidental deletion | backups (operator-run), per-tenant restore procedure, deletion needs closure and a window | no automated backup; the restore drill is unrehearsed at scale |
| Injection and parsing | SAML XML attacks, SSRF, CSV formulas | signed-assertion verification with `signxml`, DOCTYPE refusal, https-only public IdP URLs, formula neutralising inherited from the API export | keep `signxml` and `lxml` patched |
| Denial of service | a flood of requests | per-client, per-organization and per-member rate limits, body size cap (25 MB), per-tenant error budgets | in-memory limiter per process; no WAF |
| Insider and supply chain | a malicious dependency | `uv.lock` pins, dependency audit in CI | the audit runs only where CI runs |

## 5. Penetration-test scope and checklist

**Scope**: the hosted web tier (`serve`) and worker, the control-plane API ([hosted-api.md](hosted-api.md)), the candidate-workspace routes through the gateway, the Aira `/v1` compatible API with organization keys, SSO (OIDC, SAML) and SCIM, the platform console, billing webhooks, exports, and the deployment (container, secrets handling, proxy). Out of scope: the job boards and ATS sites the product visits, the billing provider, LinkedIn.

Provide the tester two organizations on different plans, every role in each, an operator with a support grant, a stand-in billing environment (never real payment details) and a staging identity provider.

Checklist:

1. **Isolation**: repeat the route sweep by hand with tenant B ids in every position (path, header, query, body, cookies); try mass-assignment of `org_id`; confirm object-storage and file paths cannot be traversed (`../` in document names, export tokens).
2. **Authentication**: lockout and timing; session fixation; cookie flags; password reset and invitation token entropy, reuse and expiry; MFA bypass (skipping the verify step, replaying codes, recovery-code reuse); SSO: signature wrapping, replay, audience and recipient checks, `InResponseTo`, unsigned assertions, XXE, open redirect in `RelayState`; SCIM token handling and cross-organization writes.
3. **Authorization**: walk the matrix in [multitenant.md](multitenant.md) for every role against every route; coach scope escalation (`view` → `act` → `approve`); a coach changing sensitive fields or answers; support grant expiry and scope; an admin acting on owners.
4. **Business logic**: plan limits (race two concurrent applications at the last unit), downgrade and over-limit pausing, resubscribing after cancel, trial extension, read-only enforcement on every `writes` route, export while read-only.
5. **Billing**: forged and replayed webhooks, wrong secret, old timestamp, tampered body, event for another organization.
6. **Input handling**: uploads (type sniffing, size, malicious PDFs and Word files), JSON depth, huge bodies, SSRF through SSO and model endpoint URLs, CSV and spreadsheet export injection, HTML in names shown in the UI (stored XSS), header injection.
7. **Secrets and crypto**: master key handling, key files' permissions, secrets in logs, error messages and exports, document decryption with the wrong organization's key.
8. **Deployment**: TLS configuration, security headers (add HSTS), container user and capabilities, network policy, metadata endpoints, dependency CVEs.
9. **Platform console**: operator MFA, separation from tenant cookies, tenant-data exposure through health and audit views.

Close every high and critical finding before the pilot. Record findings, fixes and retest evidence; they are SOC 2 evidence too.

## 6. Vulnerability management and disclosure

- The disclosure policy is [legal/security.md](legal/security.md); `/.well-known/security.txt` publishes the contact set in `AIRA_CLOUD_SECURITY_CONTACT`.
- CI runs `pip-audit` through `uv` (see `.github/workflows/cloud.yml`) on every change and on a schedule; a finding blocks release until triaged.
- Suggested patch targets (a policy to adopt, not something the code enforces): critical within 3 days, high within 14, medium within 60, low in the next release.
- Dependencies are pinned in `uv.lock` and updated through the repository's existing dependency automation.

## 7. SOC 2 readiness plan

Target: SOC 2 Type I within 12 months of launch, then Type II. The Trust Services Criteria below map to evidence the system already produces; the right-hand column is what is still to be written or collected.

| Criterion area | Evidence the system produces | To do |
| --- | --- | --- |
| CC1 control environment, CC2 communication | [legal/](legal/terms.md) documents, this document set | policies (information security, access, change, incident, vendor, HR), roles and responsibilities, training records |
| CC3 risk assessment | threat model (section 4) | a dated risk register with owners and review cadence |
| CC4 monitoring | `/api/platform/health`, per-tenant error budgets, `run_outcomes`, audit chains | alert rules, an on-call and review log |
| CC5 control activities, CC8 change management | reviewed pull requests, CI results (lint, type check, tests, dependency audit), numbered migrations | a change policy, an approval record per release, a rollback test |
| CC6 logical and physical access | role matrix, MFA, session controls, support-access records (both audit logs), operator accounts, key handling | quarterly access reviews, off-boarding checklist, KMS-backed keys, hosting provider's physical controls (inherited, collect their report) |
| CC7 system operations | structured logs with correlation ids, queue and worker health, incident playbooks in [multitenant-operations.md](multitenant-operations.md) | incident log, post-incident reviews, log retention and shipping, vulnerability scan records |
| CC9 risk mitigation (vendors) | sub-processor list in the DPA template | vendor reviews (model provider, billing, email, hosting) |
| A1 availability | readiness endpoint, backups procedure, restore procedure | tested backups and a recorded restore drill, capacity monitoring |
| C1 confidentiality | per-organization encryption, tenant isolation, deletion and export tooling, retention | data classification, key-management policy, evidence of deletion requests |
| P (privacy), if in scope | consent records, data-subject request tooling, retention and deletion | privacy notice review, records of processing, DPA execution per customer |

Start by collecting what already exists: the audit-log export for a sample organization (`/api/org/audit/export` and `audit/verify`), the platform audit log, CI run history, the route-sweep test results, and a completed restore drill.

## Internal adversarial review (2026-10-24)

An internal, read-only review stood in for a penetration test (it is not one: no external tester, no production-like environment). It confirmed defects with small proofs of concept; each was fixed and has a regression test.

| Finding | Severity | Fix | Test |
| --- | --- | --- | --- |
| A password-only session could replace an enrolled TOTP secret (`mfa/begin` and `mfa/enable`), defeating MFA for members and operators | critical | both calls need a session that has already given the current factor when MFA is enabled | `test_cloud_identity::test_a_password_alone_cannot_replace_an_enrolled_second_factor` |
| SCIM could change an owner's or admin's `userName`, then password reset took the account | high | provisioning cannot change the sign-in address of owners and admins (`403`, `scimType: mutability`) | `test_cloud_hardening::test_scim_cannot_reroute_an_owner_or_admin_sign_in_address` |
| Candidate and organization exports contained sensitive profile values unmasked, for staff who may not see them | high | exports apply the same redaction as the API unless the requester may see sensitive values (the candidate's own export and the departure export to the candidate are complete); automatic application runs, plans, custom answers and message tables were also missing from the export and were added | `test_cloud_tenancy::test_exports_mask_sensitive_values_for_staff_but_not_for_the_candidate` |
| A bring-your-own model endpoint could point at loopback or internal addresses (server-side request forgery) | medium | same public-https check as SSO URLs; `AIRA_CLOUD_ALLOW_LOCAL_URLS=1` is a development opt-in. The host is resolved when saved; block internal ranges at the network too to cover DNS rebinding | `test_cloud_hardening::test_organization_supplied_urls_must_be_public_https` |
| A client-supplied `X-Forwarded-For` prefix evaded rate limits and lockout | medium | the client address is the entry `AIRA_CLOUD_TRUSTED_PROXY_HOPS` from the right | `test_cloud_hardening::test_a_forged_forwarded_for_prefix_does_not_change_the_client_address` |
| CEF audit lines could be split by a newline in a field | low | line breaks are removed in every CEF field | `test_cloud_hardening::test_cef_lines_cannot_be_split_by_a_newline_in_a_field` |

Not reviewed: the worker's job-context binding, the careers route handlers, and LinkedIn worker-agent forwarding beyond its signing. Treat those as open until an external test covers them.

### Second pass (2026-10-24)

| Finding | Severity | Fix | Test |
| --- | --- | --- | --- |
| Staff, and operators acting for staff, could list and download a candidate's own complete export (and departure and data-subject exports), undoing the masking | high | exports that carry a candidate's sensitive values in full are visible only to that candidate (departure and data-subject exports go to the person by emailed token); staff see and download only masked exports | `test_cloud_hardening::test_staff_cannot_list_or_download_a_candidates_complete_export` |
| An admin support grant could invite members, mint keys, change roles and reset MFA, leaving access behind after the grant ended | high | `members.manage`, `integrations.manage` and `candidates.manage` are never available through a support grant | `test_cloud_platform::test_a_support_grant_cannot_create_access_that_outlives_it` |
| The remote sign-in gateway used the master agent secret, so a compromised gateway could sign agent requests for any tenant | medium | the broker has its own `AIRA_CLOUD_BROKER_SECRET` (empty = its routes are off) and every signature carries a purpose label | `test_cloud_agent::test_remote_sign_in_link_is_short_lived_single_use_and_the_candidates_own` |
| Agent requests could be replayed after 60 seconds of clock skew, or against a second process | medium | seen nonces live in the control plane and are kept until their timestamp expires | `test_cloud_agent::test_a_signed_request_cannot_be_replayed_inside_the_window_by_another_verifier` |
| Restore trusted table names, row `org_id` values and file paths in the archive (an owner could be inserted into another organization; zip slip) | medium | table allowlist, every row must carry the manifest's organization, paths must stay inside staging, `org_id` must be 32 hex characters, and the manifest is signed with a key kept in the control plane (`--allow-unsigned` for an archive made elsewhere, still subject to the other checks) | `test_cloud_backup::test_a_backup_with_forged_contents_cannot_reach_another_organization`, `::test_an_archive_from_another_deployment_is_refused_unless_trusted` |
| Careers reports, alert runs and scheduled monitors were metered as one search whatever they cost | low | metered by the live credits they used | `test_cloud_hardening::test_careers_reports_and_alert_runs_meter_the_credits_they_used` |
| The shared rate limiters were mutated per request, so one organization's limit could apply to another thread | low | the limit is passed per call; the limiter is never changed | `test_cloud_hardening::test_one_organizations_rate_limit_never_changes_another_threads_limit` |
| One slow agent call made every tenant on that host lose LinkedIn session pinning | low | a host is forgotten only when its health check also fails | covered by `test_cloud_agent` dead-worker test |
| `autopilot.exec` ("run now") skipped the start-time gate check | low | gated again when a worker takes the job | `test_cloud_worker::test_a_run_now_job_is_gated_again_when_a_worker_picks_it_up` |
| The organization's own API monitors were never scheduled | low | a `monitors.org` job runs them with the organization's key and meters `api_searches` | `test_cloud_integrations::test_the_organizations_own_aira_api_monitors_run_on_schedule` |


## Aira Leads in the hosted build

Leads are other people's personal data, kept in the workspace of the candidate (or user) who collected them.

- **Who can read them.** Only the workspace owner and a coach assigned with `act` or `approve` scope (`leads.read`, `leads.write`). Owners, admins, auditors, billing contacts, `view` coaches and operators on a support grant never see lead contents (`leads.*` are not in any of their permission sets, and every route is `candidate` scope, so another organization's lead id is a 404). Owners and admins can delete a workspace, which removes the leads with it, but an export requested by staff leaves the lead tables out (`redaction.PRIVATE_TABLES`); only the candidate's own export contains them, and a departing candidate takes them in their hand-over export.
- **Server-side fetches.** Enrichment reads a lead's company website from the server: it connects only to addresses that resolve to public internet ranges, checks every redirect, connects to the address it checked, honours `robots.txt`, identifies itself, and caps time and size. The hosted app discards `AIRA_LEADS_ALLOW_LOCAL` at start-up, so a misconfigured host cannot turn it into an internal port scanner. Each read is metered (`lead_enrichments`).
- **LinkedIn.** Sales Navigator is read in the candidate's own browser session on the worker host that holds it, only with the plan feature, the organization's LinkedIn policy, the candidate's opt-in and the platform kill switch, and it is read-only (no connect, message or InMail).
- **Cost.** Google lead searches spend the organization's SerpApi key (or the pooled allowance, metered by credits); the stored-lead and enrichment limits bound storage and abuse.
- **Outbound email.** Sending is the candidate's alone (`leads.outreach` is candidate-only: a coach with act scope may draft, edit, reject and prepare calls, but cannot approve, send, change the mail server or sender identity, or remove a do-not-contact entry; an edit withdraws an approval). The SMTP password is encrypted under the organization's data key in the candidate's workspace (never in the control database, a backup, an export, an audit entry or a log line; tests scan for the plaintext). A hosted build refuses mail servers that are not public addresses, when the settings are saved and again at send time, and `AIRA_LEADS_ALLOW_LOCAL` cannot relax it. The daily cap is clamped to the plan on the server; a send request carries at most five messages with a 2 to 10 second pause, so one request is at most about 140 seconds even with a failing mail server (give that route a proxy timeout of 150 seconds or more). Every send, approval, draft batch and settings change is audited as counts and the mail server's host, never message text or a password. Operators can stop all outbound lead email at once with the `leads_outreach` source switch.
- **Calls.** Aira never places or automates a call: no dialler, no recorded or synthetic voice. It prepares a script, shows the number and calling hours, opens Google Voice's dialler for a person to press Call, and keeps the call log and the do-not-contact list.
- **Not done.** No suppression list across candidates or organizations; no automatic check of a prospect's region or lawful basis; no per-lead consent record. The customer owns those obligations (see the DPA template, section 3A).
