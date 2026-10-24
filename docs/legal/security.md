# Security and vulnerability disclosure policy (template)

> **TEMPLATE. Not legal advice, and not reviewed by counsel.** Replace every `[bracketed]` item and have counsel review the safe-harbor wording and the response commitments before publishing. The contact address must match `AIRA_CLOUD_SECURITY_CONTACT`, which the service publishes at `/.well-known/security.txt`.

We want to hear about security problems in Aira Career Copilot.

## How to report

Email `[security@your-domain]` (PGP key: `[fingerprint or URL]`). Include what you found, the steps or request that show it, the organizations and accounts you used (your own test accounts), and any request identifiers (`X-Request-ID`). Please do not include real candidates' personal data in a report.

## Scope

In scope: the hosted service at `[production URL]`, its API ([hosted API reference](../hosted-api.md)), the single sign-on and provisioning endpoints, the platform console sign-in, and the Aira-compatible API. Especially welcome: anything that lets one organization reach another's data, a role exceed its permissions, a limit be bypassed, a session or credential be stolen, or a webhook be forged.

Out of scope: the job boards and employers' systems the service visits, LinkedIn, our payment and email providers, social engineering of staff, physical attacks, denial-of-service testing, and findings that need a user to have already compromised their own device.

## Ground rules (safe harbor)

`[Counsel to confirm.]` If you act in good faith under these rules we will not pursue legal action or ask law enforcement to: use only accounts you created (create two test organizations to test isolation); stop and report as soon as you can see data that is not yours, without keeping, copying or sharing it; do not degrade the service for others; do not use automated scanning at high volume; give us reasonable time to fix a problem before telling anyone else.

## What happens next

We aim to acknowledge a report within `[3]` business days, confirm or decline it within `[7]`, and keep you informed until it is fixed. Target fix times by severity (a policy, adjusted as we learn): critical `[3]` days, high `[14]`, medium `[60]`, low in the next release. We credit reporters who want credit. `[Bounty terms, if any.]`

## How we handle problems

Fixes get a regression test; for a problem that could expose data we review the audit logs of the organizations involved, notify them as the data-processing agreement ([dpa.md](dpa.md)) requires, and publish a note once it is safe to. Dependencies are scanned in CI on every change and on a schedule, and findings block release until triaged. Operator access to customer data is limited to owner-approved, time-boxed, logged support grants. More detail is in [multitenant-security.md](../multitenant-security.md).
