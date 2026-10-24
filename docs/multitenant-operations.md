# Running the hosted build: operations runbook

**Template status.** This is an operating guide written from the code. Nothing in it has been rehearsed in a production environment; where a step is a procedure rather than something the code does for you, it says so. The local single-user build is operated with [runbook.md](runbook.md).

See [multitenant.md](multitenant.md) for what the system does and its known gaps, and [multitenant-security.md](multitenant-security.md) for controls.

## 1. Topologies

| Topology | Use for | Notes |
| --- | --- | --- |
| **One process** `python -m aira_cloud serve --worker` | development, demos, a pilot | web tier, worker pool and scheduler together. This is the only layout in which LinkedIn automation works end to end (see below) |
| **Web and worker separate** `serve` plus `worker` on the same host or on hosts sharing the data volume | a small production | the worker needs Chromium; the web tier does not. Both must see the same `<data root>` and the same `control.sqlite3` |
| **Several web replicas** behind a proxy | availability | stateless apart from in-memory rate-limit counters and the 60-second entitlement cache. All replicas need the same data volume and a file system whose locking SQLite can rely on. A managed database is the intended answer for more than this; it is not built |

Constraints to plan around:

- The control plane is one SQLite file in WAL mode: one writer at a time. Size workers and replicas for short write transactions; do not put `control.sqlite3` on a network file system whose locks are not reliable.
- Workspaces are SQLite files under `tenants/`; workers and web replicas that serve the same organization must share that tree.
- LinkedIn automation uses the candidate's browser profile on the host that runs the process which starts it. There is no remote sign-in streaming and no forwarding of LinkedIn requests to the worker holding a session. Keep LinkedIn off (the default), or run web and worker as one process or on one host and volume.
- TLS: either terminate at a proxy (set `AIRA_CLOUD_TRUST_PROXY=1`, `AIRA_CLOUD_BASE_URL=https://...`) or set `AIRA_CLOUD_TLS_CERT` and `AIRA_CLOUD_TLS_KEY` (the server then speaks TLS 1.2 or later itself). The app sets no `Strict-Transport-Security` header; add it at the proxy.

## 2. Configuration reference

Read once at start (`aira_cloud/config.py`; a few more are read by `__main__.py` and `app.py`). Durations and limits that are not listed here (session lifetime 7 days, operator session 12 hours, invite 7 days, lockout after 8 failures for 15 minutes, trial 14 days, read-only after 14 days past due, deletion scheduled at 60 days, export window 30 days, deletion grace 30 days, entitlement cache 60 seconds) are code defaults in `CloudConfig`, not environment variables.

### Service

| Variable | Default | Meaning |
| --- | --- | --- |
| `AIRA_CLOUD_DATA` | `~/.aira-cloud` | data root: `control.sqlite3`, `secrets/`, `tenants/` |
| `AIRA_CLOUD_HOST` | `127.0.0.1` | bind address (`0.0.0.0` in a container) |
| `AIRA_CLOUD_PORT` | `8780` | bind port |
| `AIRA_CLOUD_BASE_URL` | `http://127.0.0.1:8780` | public URL; used in email links, checkout redirects and to decide whether cookies are `Secure` (https) |
| `AIRA_CLOUD_ALLOWED_HOSTS` | empty | comma-separated host names the service answers to. Loopback names are always allowed; any other `Host` is refused with `403 bad_host`, so set this to your public host |
| `AIRA_CLOUD_REGION` | `local-1` | stored on each organization (one region per deployment) |
| `AIRA_CLOUD_SECURITY_CONTACT` | `security@aira.invalid` | contact in `/.well-known/security.txt` |
| `AIRA_CLOUD_TRUST_PROXY` | off | use the `X-Forwarded-For` address `AIRA_CLOUD_TRUSTED_PROXY_HOPS` entries from the right as the client address for rate limits. Turn on only behind a proxy that sets it |
| `AIRA_CLOUD_TRUSTED_PROXY_HOPS` | 1 | how many proxies in front of the app append to `X-Forwarded-For`; entries further left are client-supplied and ignored |
| `AIRA_CLOUD_ALLOW_LOCAL_URLS` | off | development only: let organization-supplied URLs (SSO, model endpoint) point at loopback http. Never in production |
| `AIRA_CLOUD_TLS_CERT`, `AIRA_CLOUD_TLS_KEY` | empty | serve TLS directly |
| `AIRA_CLOUD_LOG_LEVEL` | `INFO` | log level |
| `AIRA_CLOUD_JSON_LOGS` | `0` | `1` for JSON logs with `correlation_id` and `org_id` |

### Secrets and operators

| Variable | Meaning |
| --- | --- |
| `AIRA_CLOUD_MASTER_KEY` | a Fernet key that wraps every organization's data key when `AIRA_CLOUD_KMS=local` (the default). If unset, a key is generated at `<data root>/secrets/master.key` (mode 0600). Back it up with the data: without it the organization keys, and so the documents, cannot be read. Not used with a KMS |
| `AIRA_CLOUD_KMS` | `local` (default), `aws` (AWS KMS) or `vault` (HashiCorp Vault transit). With a KMS the master key never leaves it; the app sends it only data keys (never documents or secrets). If the KMS cannot answer, work that needs an organization's key fails with `503 key_provider_unavailable`; there is no fallback to a local key. A bad setting stops the process at start-up |
| `AIRA_CLOUD_KMS_KEY_ID` | AWS KMS key id, ARN or alias (`alias/aira-master`), or the Vault transit key name |
| `AIRA_CLOUD_KMS_ENDPOINT` | AWS KMS endpoint (default `https://kms.<region>.amazonaws.com`, set it for a VPC endpoint or a stand-in); with Vault, the address if `AIRA_CLOUD_KMS_VAULT_ADDR` is empty |
| `AIRA_CLOUD_KMS_REGION` | AWS region for signing KMS requests (default `us-east-1`) |
| `AIRA_CLOUD_KMS_ACCESS_KEY`, `AIRA_CLOUD_KMS_SECRET_KEY`, `AIRA_CLOUD_KMS_SESSION_TOKEN` | AWS credentials for KMS (fall back to `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_SESSION_TOKEN`). Grant only `kms:Encrypt`, `kms:Decrypt` and `kms:GenerateDataKey` on the one key. Instance-role credentials are not fetched: inject short-lived ones into the environment |
| `AIRA_CLOUD_KMS_VAULT_ADDR`, `AIRA_CLOUD_KMS_VAULT_TOKEN`, `AIRA_CLOUD_KMS_VAULT_MOUNT`, `AIRA_CLOUD_KMS_VAULT_NAMESPACE` | Vault address, token (policy: `update` on `<mount>/encrypt/<key>`, `<mount>/decrypt/<key>` and `<mount>/datakey/plaintext/<key>`), transit mount (default `transit`) and optional enterprise namespace |
| `AIRA_CLOUD_KMS_CACHE_SECONDS` | how long an unwrapped organization key stays in memory before the KMS is asked again (default 300; memory only, nothing is written to disk) |
| `AIRA_CLOUD_OPERATOR_EMAIL`, `AIRA_CLOUD_OPERATOR_PASSWORD` | create the first platform admin on start if no operator exists. Remove them after the first start |
| `AIRA_POOLED_SERPAPI_KEY` | the platform's own SerpApi key, offered to Team and Enterprise organizations that enable "pooled" searches. Never set `SERPAPI_API_KEY` on a hosted deployment: the process removes it from its environment at start (unless `AIRA_CLOUD_KEEP_ENV_SERPAPI` is set) so a tenant cannot inherit a host key |

### Billing

| Variable | Meaning |
| --- | --- |
| `AIRA_CLOUD_BILLING` | `standin` (default; development) or `stripe` |
| `STRIPE_SECRET_KEY` | API key used to create customers, checkout sessions, subscription changes and portal sessions |
| `STRIPE_WEBHOOK_SECRET` | endpoint signing secret; webhooks are refused without it |
| `STRIPE_API_BASE` | default `https://api.stripe.com`; point at a stub in tests |

### Mail

| Variable | Meaning |
| --- | --- |
| `AIRA_CLOUD_SMTP_URL` | `smtp://user:pass@host:587` (STARTTLS) or `smtps://user:pass@host:465`. Empty: messages are only stored in `mail_outbox` with status `captured` and nothing is sent |
| `AIRA_CLOUD_MAIL_FROM` | the From header |

### Model

| Variable | Meaning |
| --- | --- |
| `AIRA_CLOUD_MODEL_PROVIDER` | `ollama` (default), `anthropic` or `openai` (an OpenAI-compatible endpoint) |
| `AIRA_CLOUD_MODEL_URL` | base URL (Ollama default `http://127.0.0.1:11434`; Anthropic default `https://api.anthropic.com`) |
| `AIRA_CLOUD_MODEL_KEY` | API key for `anthropic` or `openai` |
| `AIRA_CLOUD_MODEL_NAME` | model name; for `anthropic` the default is `claude-sonnet-5-5` |

An Enterprise organization can replace all of this with its own endpoint (`PUT /api/org/integrations/model`), stored as an encrypted organization secret.

### Document storage

| Variable | Meaning |
| --- | --- |
| `AIRA_CLOUD_STORAGE` | `local` (default; under the data root) or `s3` |
| `AIRA_CLOUD_S3_ENDPOINT`, `AIRA_CLOUD_S3_BUCKET`, `AIRA_CLOUD_S3_REGION` (default `us-east-1`), `AIRA_CLOUD_S3_ACCESS_KEY`, `AIRA_CLOUD_S3_SECRET_KEY` | an S3-compatible store with path-style addressing and Signature Version 4 (AWS S3, SeaweedFS, others; the Docker stack runs against SeaweedFS). All five are required when `s3` is chosen |

Documents are encrypted with the organization's data key before they reach either backend, so the object store never sees plaintext. Objects are `tenant/<org_id>/<candidate_id>/<document_id>.bin`. Give the credentials write, read, delete and list on that bucket only.

### Other seams

`AIRA_SERPAPI_BASE_URL` (the local build's switch) points SerpApi calls at a stub or proxy; `AIRA_CLOUD_ALLOW_LOCAL_IDP=1` lets SSO accept a loopback identity provider (development only).

## 3. First start

```bash
uv sync --no-dev
export AIRA_CLOUD_DATA=/var/lib/aira
export AIRA_CLOUD_BASE_URL=https://aira.example.com
export AIRA_CLOUD_ALLOWED_HOSTS=aira.example.com
python -m aira_cloud migrate                       # creates control.sqlite3 at the current schema
python -m aira_cloud operator --email you@vendor.example --role platform_admin
python -m aira_cloud check                         # schema version, region, queue
python -m aira_cloud serve --worker                # web + worker + scheduler
```

`operator` prompts for a password (12 or more characters) unless `--password` or `AIRA_CLOUD_OPERATOR_PASSWORD` is given. Sign in at `/#/platform`, then set up MFA: an operator session is refused every platform action until the second factor is given. Create a `support` operator for anyone who should not change plans, flags or billing.

`python -m aira_cloud seed-demo` creates three demo organizations with a known password. It refuses to run when the base URL is https and not loopback. Never run it on a real deployment.

`/api/health` is liveness; `/api/ready` checks the schema version, that the data directory is writable and reports queue depth and workers (503 when not ready).

## 4. Scheduling

Everything that depends on time runs in `lifecycle.tick()` (billing transitions, trial notices, plan-version migrations, support-access expiry, retention, session and export cleanup, and enqueueing due work). The worker's heartbeat thread calls it about every 10 seconds, and `python -m aira_cloud tick` runs one pass. A lease in `cloud_kv` makes it safe to have several workers or a CronJob calling it: only one pass runs at a time. Run at least one worker, or a cron job calling `tick` every minute, in every deployment; without it trials never end, dunning never advances and no scheduled plan runs.

## 5. Worker sizing

- A worker process runs `--threads` (default 8) job threads. Each apply job drives one Chromium for the duration of one application; budget about 400 to 600 MB of memory per concurrent browser and one CPU core per two or three.
- Concurrency per organization is capped at 2 (`per_org_concurrency` in code) so one tenant cannot occupy the pool; an operator rate limit lowers it to 1. A candidate runs one automation job at a time.
- Capacity needed ≈ (applications per hour across tenants) × (average minutes per application ÷ 60). Watch `/api/platform/health`: `workers.utilisation` and `queue.oldest_wait_seconds`. If the oldest wait grows while utilisation is near 1, add worker threads or processes.
- The acceptance target in the scope is 50 candidates across 5 organizations running at once with no tenant waiting on another. The fair lease and per-organization cap are built for that; a load test at that size has not been run. Run one in staging before launch.
- Scale on queue depth, not CPU alone: Kubernetes autoscaling on CPU lags a burst of due plans. See the note in `deploy/k8s/worker.yaml`.

## 6. Backups and restore

What to back up together, at the same moment (or with the application stopped):

1. `control.sqlite3` (use `sqlite3 control.sqlite3 ".backup backup.sqlite3"` or Python's `Connection.backup`; a plain file copy while writers run can be inconsistent because of the WAL).
2. The `tenants/` tree (or the object-store bucket with versioning).
3. `secrets/master.key`, or wherever `AIRA_CLOUD_MASTER_KEY` is kept, stored separately from the data it protects.

Backups contain personal data and encrypted documents; encrypt them at rest and restrict access. There is no built-in backup job.

### Restoring one organization without touching others

Workspaces are self-contained directories, and every control-plane row that belongs to an organization carries its `org_id`, so one tenant can be restored alone. The steps below were run against a scratch deployment (two organizations, the first one's files and rows destroyed, restored from a backup; the audit chain verified and the second organization was unchanged). They have not been run in staging with production-sized data; do that as the restore drill the scope requires.

1. Suspend the organization so nothing writes while you work (platform console, or `POST /api/platform/tenants/{id}/suspend`), and stop any worker that might hold its jobs.
2. Restore its files from the backup: copy `tenants/<org_id>/` back (or restore the `tenant/<org_id>/` prefix in the bucket).
3. Restore its rows. Take a consistent copy of the backup database, then for every table in the list below run, inside one transaction with `PRAGMA foreign_keys = OFF`:
   `DELETE FROM main.<table> WHERE org_id = :org; INSERT INTO main.<table> (<columns>) SELECT <columns> FROM backup.<table> WHERE org_id = :org;`
   after `ATTACH DATABASE '<backup copy>' AS backup`. Set `cloud_kv` key `audit_maintenance` to `1` for the duration (the audit log refuses deletes otherwise) and back to `0` afterwards. Also restore `airaapi_key_index` rows where `user_id = :org`.
   Tables: `members`, `invites`, `email_tokens`, `workspaces`, `candidate_stage_log`, `coach_assignments`, `consents`, `usage_counters`, `usage_daily`, `audit_logs`, `support_access`, `sso_configs`, `sso_identities`, `sso_states`, `scim_tokens`, `org_keys`, `org_secrets`, `org_notices`, `departed_candidates`, `exports`.
   Leave `organizations`, `subscriptions` and the billing tables alone unless they were lost too (they survive deletion of the tenant's data). Do not restore `sessions`, `queue_jobs`, `mail_outbox` or `run_outcomes`: people sign in again, and replaying old jobs would repeat work.
4. Run `PRAGMA foreign_key_check`, then `GET /api/org/audit/verify` as an owner: `ok: true` means the audit chain survived the restore.
5. Reinstate the organization and tell the owner what window of data was lost.

A whole-deployment restore is the same files restored together; start the service and check `/api/ready`.

To prove the drill, keep a record of: backup time, time to restore, the audit verification result, and a sign-in by an owner and a candidate of the restored tenant.

## 7. Key rotation

- **Passwords and sessions**: users change passwords themselves; `POST /api/auth/password/change` and a reset end every other session. An administrator ends someone's sessions by changing their role or removing them.
- **Organization data key**: `SecretManager.rotate(org_id)` adds a new key and re-encrypts that organization's control-plane secrets (SSO client secret, model endpoint, MFA secrets). New documents use the new key; old documents stay readable because retired keys are kept. Documents and the secrets stored in the organization's Aira API workspace are re-encrypted by a worker job (`python -m aira_cloud rotate-keys --org <id>` or the platform route); retired keys are kept until that finishes.
- **Master key** (moving between the local file, AWS KMS and Vault, or to a new KMS key): `python -m aira_cloud rewrap-keys`. Data keys do not change, so documents and secrets are not touched. Procedure:
  1. Set the *new* provider in the normal variables (`AIRA_CLOUD_KMS`, `AIRA_CLOUD_KMS_KEY_ID`, ...) and the *old* one with the same names and an `_OLD` suffix: `AIRA_CLOUD_KMS_OLD` (default `local`), `AIRA_CLOUD_MASTER_KEY_OLD` (or leave it unset to read `<data root>/secrets/master.key`), `AIRA_CLOUD_KMS_KEY_ID_OLD`, `AIRA_CLOUD_KMS_ENDPOINT_OLD`, `AIRA_CLOUD_KMS_REGION_OLD`, `AIRA_CLOUD_KMS_ACCESS_KEY_OLD`, `AIRA_CLOUD_KMS_SECRET_KEY_OLD`, `AIRA_CLOUD_KMS_SESSION_TOKEN_OLD`, `AIRA_CLOUD_KMS_VAULT_ADDR_OLD`, `AIRA_CLOUD_KMS_VAULT_TOKEN_OLD`, `AIRA_CLOUD_KMS_VAULT_MOUNT_OLD`, `AIRA_CLOUD_KMS_VAULT_NAMESPACE_OLD`.
  2. `python -m aira_cloud rewrap-keys --verify-only` proves the old key opens every organization key and the new one wraps and unwraps it again, changing nothing.
  3. Stop the web tier and workers (or accept that they briefly read through the old provider), back up the control database, then run `python -m aira_cloud rewrap-keys`. Every provider call happens first; the database is then updated in one transaction, and only rows still holding the blob that was read. A failure at any point changes nothing.
  4. Start everything with only the new provider configured. Keep the old master key until a restore drill has passed against a backup taken after the change: older backups still hold keys wrapped by the old one.
  The command writes a `platform.keys_rewrapped` entry to the platform audit log. It is a command and not a platform route on purpose: it needs credentials for the old provider, which the serving process should not hold.
- **Webhook secret** (`STRIPE_WEBHOOK_SECRET`): Stripe can sign with two secrets during rotation, and the verifier accepts any matching `v1` signature in the header. Roll the secret in Stripe, update the environment, restart.
- **Stand-in webhook secret** is generated and kept in `cloud_kv` (development only).

## 8. Billing provider setup (Stripe)

1. Set `AIRA_CLOUD_BILLING=stripe`, `STRIPE_SECRET_KEY` and `STRIPE_WEBHOOK_SECRET`.
2. In Stripe, add a webhook endpoint at `https://<your host>/api/billing/webhook/stripe` for these events: `checkout.session.completed`, `invoice.paid`, `invoice.payment_failed`, `customer.subscription.updated`, `customer.subscription.deleted`. Other events are accepted and ignored.
3. Plans are priced by the shell (per-seat prices from the plan catalog) and sent to Stripe as `price_data` at checkout, so you do not need to create Products or Prices in Stripe. Use test mode first. A downgrade is applied by the scheduler at period end by updating the subscription without proration; Stripe subscription schedules are not used.
4. Enterprise is not self-serve: an operator calls Activate Enterprise (seats, overrides, an invoice) and marks the invoice paid when it is.
5. Reconcile with `POST /api/platform/tenants/{id}/reconcile` (compares status with Stripe's).

The provider's retries drive dunning: each failed attempt sends `invoice.payment_failed`; the first one starts the 14-day and 60-day clocks and later ones change nothing. A successful `invoice.paid` clears them.

The stand-in provider (`AIRA_CLOUD_BILLING=standin`) serves a checkout page at `/billing/standin/checkout/<id>` and a billing portal that simulates failures and recoveries. It moves no money and must not be used in production: the checkout page is public and the session id is its only protection.

## 9. Mail

Without `AIRA_CLOUD_SMTP_URL` nothing is sent: every message is stored in `mail_outbox` (status `captured`), so sign-up verification, invitations and reset links do not work for real people. Set it, send a test invitation, and watch `mail_outbox.status` (`sent` or `failed` with the error). Sender domain authentication (SPF, DKIM, DMARC) is yours to set up. Team and Enterprise messages carry the organization's name and logo.

## 10. Routine operations

- **Retention**: the tick purges expired documents (policy and plan retention) and prunes audit rows past the plan's window daily. Nothing else to schedule.
- **Plan changes**: publish a new plan version (`PUT /api/platform/plans/{id}`); new subscriptions use it, existing ones keep theirs until you schedule a migration (`POST .../migrate`).
- **Feature flags**: `PUT /api/platform/flags` per plan, per organization or by percentage. A global flag switched off is a kill switch.
- **Upgrades and rollback**: stop workers (running jobs finish or their leases expire and they retry), back up, deploy the new code, start. Control-plane migrations are numbered and additive (`cloud_migrations`) and run at start; `python -m aira_cloud migrate` applies them without serving. Workspace databases migrate themselves on first connect. Because migrations are additive, rolling back the code leaves a newer schema in place; older code ignores new tables and columns, but test a rollback in staging first. To roll back for real, restore the pre-upgrade backup.

## 11. Incident playbooks

**A queue that is not moving.** `/api/platform/queue` (or the health page): `depth`, `oldest_wait_seconds`, workers. No workers listed: the worker process is down, or cannot reach the database; check logs and `/api/ready`. Workers busy but waiting growing: add threads. `dead` jobs: read `queue_jobs.error`. Jobs `skipped` are the gate refusing (pause, payment, consent, limits), which is correct. A leased job whose worker died returns to the queue when its lease (15 minutes) expires, up to three attempts.

**A suspected tenant isolation problem.** Treat it as a security incident. (1) Suspend the affected organizations and rate-limit or block the route if you can identify it. (2) Preserve evidence: `X-Request-ID`s from the report, structured logs (`org_id`, `correlation_id`), the audit logs (`/api/platform/tenants/{id}/audit`), and `GET /api/platform/audit`. (3) Verify audit chains for the organizations involved. (4) Reproduce with two scratch organizations; the test suite's helpers (`tests/cloud_support.py`) make that quick. (5) Notify the affected organizations' owners and, where personal data was exposed, follow the notification duties in the data-processing agreement ([legal/dpa.md](legal/dpa.md)) and applicable law. Fix, add a regression test, and review whether similar routes need the same change.

**Payment webhooks failing.** Symptoms: organizations paid but still `trialing`, or failures not reflected. Check Stripe's delivery log for the endpoint; `billing_events` shows received events and any `error`; a 401 means the signature secret or the host clock is off (the verifier allows five minutes). Fix the cause and have Stripe resend: processing is idempotent by event id, and an event that errored is processed again on retry. Use `reconcile` to compare state and `mark invoice paid` for an Enterprise invoice settled outside Stripe.

**A source breaks or an employer system changes its form.** Platform console, Sources: block the source (`POST /api/platform/sources/block` with the source name and a reason). Every organization's searches and applications on it are refused with that reason within seconds; searches in progress finish. Unblock after the adapter is fixed. Watch `apply_success_by_ats` and `source_error_rates` for the trigger.

**LinkedIn risk.** If LinkedIn restricts accounts or counsel advises: set the global flag `sources.linkedin` off (every organization loses the feature immediately and the UI explains why), or `linkedin.easy_apply` off to stop only applications, or block the `linkedin` source. Tell owners; candidates can disconnect at any time. Do not attempt to work around a LinkedIn control.

**Cold-outreach complaints or a mail-reputation problem.** Block the source `leads_outreach` (`POST /api/platform/sources/block`, name `leads_outreach`, with a reason). Every organization's outreach send, test send and approval is refused with that reason within seconds; drafting, lead search and the call log keep working. Unblock when resolved. The same refusal applies on its own when an organization is suspended, past due, read-only or paused, or the candidate is paused. Outreach goes out through each candidate's own SMTP account (host must be public; the password is encrypted under the organization's key), so deliverability and complaints stay with that candidate. Size the reverse proxy for the send route (`POST /api/leads/outbox/send`): a batch is at most 5 messages with a 2–10 second pause between them, so allow a read timeout of at least 150 seconds.

**Support access.** An operator never reads tenant data without a grant. To end one early the owner revokes it in the organization's Support page, or an operator with `platform.tenants.manage` suspends the tenant. Every enter and leave is in both audit logs.

**Accidental deletion.** Deletion removes data; recovery is the restore in section 6. A deleted candidate's workspace has no tombstone copy.

**Account takeover suspicion.** Change the member's role or remove them (ends sessions); an owner or admin can reset their MFA; review `/api/org/audit?actor=<member id>`.

## 12. Checklist before go-live

- [ ] `AIRA_CLOUD_BASE_URL` is https, `AIRA_CLOUD_ALLOWED_HOSTS` set, TLS terminates in front, HSTS added at the proxy
- [ ] Master key stored outside the data volume and backed up; backups tested with the section 6 drill
- [ ] First operator created, MFA enrolled, bootstrap variables removed
- [ ] Stripe in test mode end to end (checkout, failure, recovery, downgrade, cancel), then live
- [ ] SMTP verified by a real invitation
- [ ] At least one worker (or a `tick` cron) running; load test at 50 candidates across 5 organizations
- [ ] `LinkedIn` left off, or the acknowledgement text reviewed by counsel
- [ ] Legal templates in [legal/](legal/terms.md) reviewed by counsel; the three open items listed in [multitenant.md](multitenant.md) closed
- [ ] Penetration test scoped per [multitenant-security.md](multitenant-security.md) and high or critical findings closed

## Worker agents and remote sign-in settings

| Variable | Meaning |
|---|---|
| `AIRA_CLOUD_AGENT_SECRET` | Shared secret (32+ characters) that signs gateway-to-agent requests. Seen request nonces are kept in the control plane until their timestamp expires, so a replay against any process is refused. Agent traffic is plain http: keep it on a private network or terminate TLS in front of the agent. |
| `AIRA_CLOUD_BROKER_SECRET` | The remote sign-in broker's own secret (32+ characters, never the agent secret). Signs its validate and event calls, with a purpose label so a broker signature is useless against an agent and the reverse. Empty switches those two routes off. |
| `AIRA_CLOUD_AGENT_HOST` / `AIRA_CLOUD_AGENT_PORT` | Where a worker host's agent listens. |
| `AIRA_CLOUD_AGENT_URL` | The address the gateway uses to reach that agent; published in the worker heartbeat. |
| `AIRA_CLOUD_REMOTE_LOGIN_URL` | The sign-in broker's public address with a `{token}` placeholder, for example `https://login.example.com/s/{token}`. Empty means sign-in opens on the worker host itself. |

## The sign-in broker

`python -m aira_cloud broker` is a small standalone service (WebSocket plus one page) that lets a candidate sign in to LinkedIn in the private browser Aira runs for them, from their own computer or phone.

Run it **on the worker host that holds the candidate's LinkedIn profile** (same machine and volume; it opens the profile directory itself, so the worker's own browser for that candidate must not be running at the same moment). Put TLS in front of it (`wss://` needs `https://`), and set `AIRA_CLOUD_REMOTE_LOGIN_URL` on the web tier to `https://<broker host>/s/{token}`. It needs `AIRA_CLOUD_BROKER_SECRET` (the same value on the web tier; the broker refuses to start without it) and `AIRA_CLOUD_BROKER_APP_URL`, the address of the web tier it can reach (default `AIRA_CLOUD_BASE_URL`). With several worker hosts, give each its own broker and route links to the host that holds the profile (the link's host is the one in the issued URL, so use one broker per host behind a router keyed on the candidate, or run a single worker host for LinkedIn).

| Variable | Default | Meaning |
|---|---|---|
| `AIRA_CLOUD_BROKER_HOST` / `AIRA_CLOUD_BROKER_PORT` | `127.0.0.1` / `8790` | where the broker listens |
| `AIRA_CLOUD_BROKER_APP_URL` | `AIRA_CLOUD_BASE_URL` | the web tier the broker calls to validate links and report starts and ends |
| `AIRA_CLOUD_BROKER_SESSION_SECONDS` | `600` | longest a sign-in window stays open |
| `AIRA_CLOUD_BROKER_MAX_SESSIONS` | `4` | browsers open at once on this host; a full host tells the person to try again without using their link up |
| `AIRA_CLOUD_BROKER_START_URL` | `https://www.linkedin.com/login` | the page the private browser opens first |
| `AIRA_CLOUD_BROKER_ALLOWED_HOSTS` | empty | extra hosts the browser may navigate to (comma separated); `linkedin.com` and its subdomains and the start URL's host are always allowed |
| `AIRA_CLOUD_BROKER_SIGNED_IN_COOKIE` / `AIRA_CLOUD_BROKER_SIGNED_IN_DOMAIN` | `li_at` / `linkedin.com` | the cookie whose appearance means the person is signed in (change only for a test site) |

What it guarantees and what it does not:

- The viewer receives pixels and short status messages only; cookies and storage never leave the browser, and the broker sets no cookie of its own.
- One window per candidate (a newer link replaces or revokes the older one), at most `AIRA_CLOUD_BROKER_MAX_SESSIONS` per host, and every window ends at the time limit, when the person leaves or cancels, when the organization is suspended or the candidate removed, or when a newer link is issued.
- The organization's audit log records `auth.remote_login_started` and `auth.remote_login_finished` (with the outcome: `signed_in`, `abandoned`, `cancelled`, `timeout`, `revoked`, `replaced`, `shutdown`, `error`).
- On success the profile is left exactly as the local `--login` flow leaves it (cookie export and source-state file), so the worker starts a session from it.
- Keystrokes cross the broker process. It does not log or keep them; do not run it at debug log levels in production, and say in your privacy notice that a private browser operated by you receives what the person types.
- Not covered: sign-in methods that open a pop-up (Google, Apple), LinkedIn checkpoints that need a real display or a physical device, and more than one broker host per candidate.

## PostgreSQL control plane

The control plane (organizations, members, plans, usage, audit, queue) runs on SQLite by default and on PostgreSQL when `AIRA_CLOUD_DB=postgres`. Per-candidate workspaces stay SQLite files and documents stay files or objects; only the control plane moves. PostgreSQL 16 is what the tests ran against.

| Variable | Meaning |
|---|---|
| `AIRA_CLOUD_DB` | `sqlite` (default) or `postgres` |
| `AIRA_CLOUD_DATABASE_URL` | connection URL for the **application role** `aira_app`: not the table owner, no `BYPASSRLS`. Tenant requests and jobs use it |
| `AIRA_CLOUD_DATABASE_PLATFORM_URL` | connection URL for the **platform role** `aira_platform` (`BYPASSRLS`): sign-in, sign-up, webhooks, the scheduler tick, queue leasing, operator actions, backups. If empty the application role is used for everything and row-level security cannot protect tenant requests (a warning is logged) |
| `AIRA_CLOUD_DATABASE_ADMIN_URL` | connection URL for the **owner role** `aira_owner`: applies migrations, guards, grants and policies at start-up and with `python -m aira_cloud migrate`. If empty the process only checks that the schema is current and refuses to start otherwise |
| `AIRA_CLOUD_DATABASE_POOL_SIZE` | connections per pool (tenant and platform each have one), default 24 |

### One-time setup (as a PostgreSQL superuser)

```sql
CREATE ROLE aira_owner    LOGIN NOBYPASSRLS PASSWORD '...';
CREATE ROLE aira_app      LOGIN NOBYPASSRLS PASSWORD '...';
CREATE ROLE aira_platform LOGIN BYPASSRLS   PASSWORD '...';
CREATE DATABASE aira OWNER aira_owner;
```

The role names are fixed. Then run `python -m aira_cloud migrate` with the three URLs set. It creates the tables, the audit-log guards (PL/pgSQL triggers that refuse `UPDATE` and, outside maintenance, `DELETE`), grants, and for **every table with an organization column** `ENABLE` and `FORCE ROW LEVEL SECURITY` with one policy: `org_id = current_setting('app.org_id', true)`. Setup is idempotent and runs again whenever a migration adds tables.

### What runs under row-level security

- **Tenant scope** (`aira_app`, `app.org_id` set per transaction): every authenticated member request (the gateway enters the caller's organization once the session is known), every worker job (`worker.py`), and everything inside `workspaces.bind`, `bind_org_api` and the `/v1` API. A statement there that forgets its `org_id` filter returns only that organization's rows; a connection with no `app.org_id` sees nothing.
- **Platform scope** (`aira_platform`): code that has no organization yet or legitimately spans organizations (sign-up, sign-in and session lookup, password reset, SSO callbacks, billing webhooks, the scheduler tick, queue leasing, the operator console, backups, key re-wrap, retention). `db.platform_scope()` is the explicit, greppable escape for the rare case inside tenant code. An operator action recorded from tenant code (for example an owner approving support access) is written in its own platform transaction.
- **Not under a policy**: global tables (`plans`, `plan_versions`, `feature_flags`, `announcements`, `blocked_sources`, `worker_heartbeats`, `cloud_kv`, `cloud_migrations`). **Platform-only tables** (`operators`, `platform_audit_logs`, `data_subject_requests`, `billing_events`, `plan_migrations`) are granted to `aira_platform` alone.

`tests/test_cloud_postgres.py::test_every_table_is_classified_for_row_level_security` fails when a new table is neither global, platform-only nor under a policy.

### Moving an existing installation

Stop the web tier and workers, then, with the three URLs set and an empty target database:

```
python -m aira_cloud migrate-to-postgres --verify-only    # compare row counts only
python -m aira_cloud migrate-to-postgres                   # copy control.sqlite3 into PostgreSQL
```

The copy keeps identity sequences ahead of the copied rows and compares counts per table; the SQLite file is left untouched (keep it until you are satisfied). Workspaces and documents do not move.

### Backups

`pg_dump --format=custom` of the whole database is the disaster-recovery backup. The per-organization archive (`backup-org`, `restore-org`) works on PostgreSQL as well and restores only that organization's rows; the audit chain is part of both. Test a `pg_restore` into a scratch database before relying on it.

### Concurrency

SQLite serializes writers. On PostgreSQL `write()` takes one transaction-level advisory lock for the same effect, except in the places designed for concurrency: usage counters (atomic conditional `UPDATE`), the job lease (`FOR UPDATE SKIP LOCKED` plus a non-blocking per-organization lock, so workers never wait for or deadlock with each other), and audit-chain extension (a per-organization lock). Everything else on the write path is as serialized as it was on SQLite; a busy installation will want those paths widened one at a time, each with a test.

## What was run, and how to rerun it

Nothing here is a paper claim: each item below was executed (Windows 11, Docker Desktop 29, kind 0.33 on Kubernetes, Terraform 1.9, 2026-10-24) and has a script or test that repeats it.

| Item | Command | Result |
| --- | --- | --- |
| Images and stack | `python deploy/smoke.py` | Builds both images, starts web, worker, an S3-compatible store and a mail sink, then checks 31 things: readiness, sign-up, verification mail, TOTP, invitation, a document stored encrypted in the object store and downloaded byte for byte, an operator-queued job run by the worker container (not the web container), key rotation, `backup-org` and `restore-org` in the container, and sign-in plus the document after `docker compose restart`. 31 of 31 passed. |
| Image hygiene | `docker image inspect` (in CI) | Both images run as uid 10001, have healthchecks, contain no secret-like environment variables or `.env` files, and nothing under `/app` is world-writable. Sizes: web about 135 MB, worker about 535 MB of layers (Chromium). |
| Kubernetes | `python deploy/k8s_smoke.py` (a cluster with the images loaded; `deploy/k8s-overlays/kind` for a single-node test cluster) | On kind: the manifests apply with the `restricted` Pod Security level enforced, pods become Ready with an allowed-host list set, the service answers, a rolling update completes, killing a web pod leaves the service answering while a replacement becomes Ready, the worker starts and is not restarted by its liveness probe, the tick CronJob completes. 11 of 11 passed. |
| Terraform | `terraform init -backend=false`, `validate`, `fmt -check`; checkov; tflint (see `.github/workflows/cloud.yml`) | All pass (checkov: 78 passed, 8 skipped with a written reason each). `plan` and `apply` were **not** run: they need AWS credentials. |
| Stripe adapter | `uv run python -m pytest tests/test_cloud_stripe_mock.py` | Every request the adapter makes (customers, checkout sessions, subscription changes with and without proration, cancel at once and at period end, billing portal, invoice items) is validated by `stripe/stripe-mock` against Stripe's OpenAPI specification, and the webhook normalizer reads the object shapes stripe-mock returns. Point the adapter at any Stripe-compatible API with `STRIPE_API_BASE`. Not run against a Stripe account (no keys). |
| OIDC and SAML | `uv run python -m pytest tests/test_cloud_keycloak.py` | Real Keycloak 26 in Docker, real Chromium filling Keycloak's login form: OIDC (discovery, PKCE, client secret, group claim to role, linking on second sign-in, wrong secret refused) and SAML (signature check against Keycloak's key, mapped `mail`, `displayName` and `memberOf` attributes, just-in-time provisioning). Not run against Okta, Entra ID or Google. |

Bugs these runs found and fixed: subscription changes sent `product_data`, which Stripe rejects on an existing subscription (a product is now created first); the Stripe event normalizer assumed the older API shapes for an invoice's subscription and for a subscription's billing period; the health and readiness endpoints answered 403 to Kubernetes probes when an allowed-host list was set, because probes address pods by IP (those two endpoints no longer check the host); the compose file referenced MinIO images that are no longer published (it now uses SeaweedFS, and `python -m aira_cloud init-storage` creates the bucket); Terraform lacked a KMS key policy and access logging (added); the Kubernetes NetworkPolicy blocked pods from reaching one another (added `same-namespace`) and the worker had no liveness probe (added).

Not done: a Kubernetes cluster other than kind (managed ReadWriteMany storage, an ingress controller, autoscaling on real metrics), `terraform apply`, Stripe with live or test-mode keys, a real enterprise IdP.

