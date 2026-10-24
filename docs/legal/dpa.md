# Data Processing Agreement (template)

> **TEMPLATE. Not legal advice, and not reviewed by counsel.** This is a starting structure for a data-processing agreement between `[Vendor legal name]` ("Processor", "Aira") and a customer Organization ("Controller"). It must be reviewed and completed by counsel, adapted to GDPR, UK GDPR, CCPA/CPRA and any other law that applies to each customer, and signed per customer. **This is one of the three open legal items** in [../multitenant.md](../multitenant.md); the others are the LinkedIn position ([automation-and-linkedin.md](automation-and-linkedin.md)) and the rules for automated applications in each market.

Parties, date, order form reference: `[to complete]`. Forms part of the [Terms of Service](terms.md).

## 1. Subject matter, nature and duration

The Processor provides the Aira Career Copilot service to the Controller. It processes personal data on the Controller's behalf to run job search, application preparation and tracking, interview and document management, messaging assistance and reporting for the Controller's candidates. Processing lasts for the subscription term plus the retention and export periods in section 10.

## 2. Roles

The Controller (the Organization) determines the purposes and means of processing candidate data and instructs the Processor through the service's configuration (plan, policy, roles, assignments) and through its documented instructions. The Processor is a controller only for operating its own accounts, billing and security (see the [privacy notice](privacy.md)). `[Counsel to confirm the role allocation, including joint-controller risk where the Organization acts for a candidate.]`

## 3. Data and data subjects

- **Data subjects**: the Controller's candidates, and its staff (owners, admins, coaches, billing contacts, auditors); third parties named in candidates' records (recruiters, hiring managers); and, where the Controller uses Aira Leads, the **prospects** whose business contact details the Controller collects (see section 3A).
- **Categories**: identification and contact data; resumes and employment history; job-search and application records and confirmations; interview and recruiter details; messages the candidate connects; account, authentication and audit data; usage and technical data.
- **Special categories and sensitive data**: the service handles work-authorization, visa, demographic and salary information that the candidate chooses to enter. Such fields are entered and confirmed by the candidate, excluded from AI prompts, and hidden from other roles unless the Controller's policy and the candidate's consent allow otherwise. `[Counsel: whether any of this is special-category data in each market.]`

## 3A. Prospect data collected with Aira Leads

Aira Leads lets a user find and organize sales leads. The people and businesses in a lead list are third parties to Aira and, usually, to the user too: they have not signed up to anything. The terms below apply to that data.

- **Roles.** The Controller (the Organization, through its users) decides whom to search for and why; it is the controller of its lead data and answers to those people. The Processor stores and processes the data on the Controller's instructions only. `[Counsel to confirm, including where an individual user, not the Organization, is the controller.]`
- **What is collected.** Names, job titles, employers, locations and public business contact details (a company website, phone, email) that a source publishes: Google Maps business listings, public search snippets of LinkedIn pages, job postings, a company's own public website pages, a LinkedIn Sales Navigator search the user ran in their own account, or a CSV the user imported. Every lead records its source. Aira does not guess or construct email addresses and does not buy or merge third-party contact databases.
- **Purposes.** B2B prospecting by the Controller. Aira does not use lead data for its own marketing or to train models.
- **Transparency and lawful basis are the Controller's.** The Controller must have a lawful basis for contacting a prospect (for example legitimate interest for business contact in some regions, consent in others), tell prospects where their data came from when the law requires it, and follow the rules for unsolicited commercial email and calls in each market (CAN-SPAM, PECR, GDPR and local equivalents). `[Counsel: region-by-region guidance for the customer-facing documentation.]`
- **Outbound email and calls.** On plans that include outreach, a user may send commercial email to leads through their own mail server and may log calls they make themselves. **The user, for the Controller, is the sender**: Aira processes the messages, the outbox, the call log and the do-not-contact list on the Controller's instructions and sends nothing on its own. Every approved message goes out one at a time through the user's own SMTP account, with their name, a postal address and an opt-out line, up to a daily cap. **Aira never dials a number**: it has no dialler and uses no recorded or synthetic voice; a person places any call. The Controller is responsible for CAN-SPAM, GDPR and PECR, CASL, telemarketing and do-not-call rules and the terms of the mail and phone services it uses. `[Counsel to confirm.]`
- **Objections and deletion.** The Controller handles requests from prospects. The product supports them: a lead can be deleted individually, in bulk or by deleting its list; deletion removes the lead and its identity keys, and a deleted workspace removes every lead in it. Each candidate workspace has a do-not-contact list (emails, domains, phone numbers) that is checked before every send and call and that a logged "asked not to be called" outcome feeds; Aira keeps no list across users or organizations, so a prospect who objected to one user can be found again by another.
- **LinkedIn data.** Reading Sales Navigator runs through the user's own signed-in LinkedIn session and is bound by the LinkedIn terms the user accepted; see [automation-and-linkedin.md](automation-and-linkedin.md). Aira reads, never contacts.
- **Export, retention and hold.** Lead tables are part of the owning user's workspace: they are included in candidate and organization exports, follow the workspace's retention and deletion, and are kept (not deleted) while a legal hold applies.

## 4. Processor obligations

1. Process only on the Controller's documented instructions (including as expressed by the plan, policy and role configuration), unless required by law, in which case tell the Controller first if allowed.
2. Ensure everyone authorized to process the data is bound by confidentiality. Operators have no access to tenant content except through the support-access flow: an owner-approved, time-limited (at most 24 hours), logged grant, with a banner shown to the Controller.
3. Implement the security measures in Annex A.
4. Engage sub-processors only under section 6.
5. Assist the Controller with data-subject requests (section 7), security, breach notification, impact assessments and consultation with authorities, taking account of the nature of the processing.
6. At the Controller's choice, delete or return the data at the end of the service (section 10).
7. Make available the information needed to show compliance and allow audits (section 9).
8. Tell the Controller if an instruction appears to infringe applicable law.

## 5. Controller obligations

The Controller has a lawful basis for the processing and for the instructions it gives; informs candidates and obtains any consent or provides any human review that the law of its markets requires for automated applications and automated decision-making about employment; configures roles, assignments and policy appropriately; and keeps its members' access current.

## 6. Sub-processors

The Controller gives general authorization for the sub-processors listed here. The Processor will give `[30]` days' notice of additions or replacements; the Controller may object on reasonable grounds, and if the objection is not resolved may terminate the affected service. The Processor remains responsible for its sub-processors and imposes equivalent obligations.

| Sub-processor | Purpose | Location | Data | Safeguards |
| --- | --- | --- | --- | --- |
| `[hosting and compute provider]` | running the service and workers | `[region]` | all | `[certifications, DPA]` |
| `[object storage provider]` | encrypted document storage | `[region]` | documents (encrypted with the organization's key before upload) | `[ ]` |
| `[AI model provider]` | fit reviews, drafts | `[region]` | prompts: job text and non-sensitive profile content | `[no training on customer data]` |
| `[email delivery provider]` | transactional email | `[region]` | recipient address, message text | `[ ]` |
| `[payment provider]` | subscription billing | `[region]` | billing contact, payment data (held by the provider) | `[ ]` |
| `[error monitoring or log provider, if any]` | operations | `[region]` | logs with identifiers; credentials and emails masked | `[ ]` |

Job boards and employers' application systems that the service visits at the Controller's candidates' direction are recipients of data the candidate or Controller chooses to send, not sub-processors.

## 7. Data-subject requests

The service provides the tools the Controller needs: a candidate can export their own data (JSON plus documents); owners and administrators can export or delete a candidate's workspace and export the whole organization; offboarding a candidate follows the Controller's candidate-data policy (export and delete by default); consent can be withdrawn by the candidate. The Processor's operators can access, export or erase a person's data across organizations on documented request. The Processor forwards requests it receives from a Controller's data subjects to the Controller within `[5]` business days and does not respond to them itself unless instructed.

## 8. Personal-data breach

The Processor notifies the Controller without undue delay and within `[48]` hours of becoming aware of a breach affecting the Controller's personal data, with the information then available (nature, categories and approximate numbers, likely consequences, measures taken), and keeps the Controller informed as the facts develop. Audit logs (hash-chained, per organization) and request identifiers support the investigation. `[Counsel to align the period with contractual and statutory duties.]`

## 9. Audits

On reasonable notice and no more than once a year (or after a breach), the Controller may audit the Processor's compliance, in practice by reviewing independent reports (`[SOC 2 report, once available]`), the security documentation ([multitenant-security.md](../multitenant-security.md)), and, where necessary, a scoped on-site or remote audit under confidentiality. Audits must not give access to other customers' data.

## 10. Retention, return and deletion

- During the term the Controller sets retention for documents within its plan (30 to 3,650 days, default 365); the service purges expired documents automatically. Audit records are kept for the plan's window (30 days to 7 years).
- When a candidate leaves, the Controller's policy applies (export to the candidate and delete, or archive).
- When the subscription ends (cancelled, trial ended, or closure requested), data becomes read-only and exportable for **30 days**, after which the organization is deleted. For non-payment, the organization becomes read-only after 14 days, deletion is scheduled after 60 days and takes place 30 days later.
- Deletion removes workspaces, documents, encryption keys, browser profiles, queued jobs and all other organization data from production systems. The Processor retains only the billing record and a minimal audit stub (organization identifier, a hash of its name, dates, event count) for legal retention (`[seven]` years). Backups containing the data expire on the backup schedule, `[no later than 35 days]`.

## 11. International transfers

`[Counsel to complete: transfer mechanism (SCCs, UK addendum, adequacy), transfer impact assessments, region of processing. The service runs in one region per deployment.]`

## 12. Liability, term and governing law

`[Counsel to complete. Usually subject to the liability terms of the Terms of Service.]`

## Annex A: security measures

Described in detail in [multitenant-security.md](../multitenant-security.md); summarized here. Counsel and security should confirm each statement is accurate for the deployment at signing.

| Area | Measure |
| --- | --- |
| Access control | role-based permissions checked on every request and background job; coach access checked against assignments and capped by plan; sensitive fields restricted to the candidate; multi-factor authentication available to all and required for owners and admins on plans with single sign-on; single sign-on (OIDC, SAML) and provisioning (SCIM) on higher plans; session invalidation on role or password change; account lockout |
| Tenant separation | every record keyed by organization; a separate database per candidate in the organization's own directory; documents under the organization's prefix; a test that calls every route with another tenant's identifiers |
| Encryption | documents encrypted with an organization-specific key before storage; secrets encrypted per organization; keys wrapped by a master key `[held in a KMS]`; TLS 1.2 or later in transit; `[volume and backup encryption]` |
| Logging and audit | append-only, hash-chained audit logs of administrative and automation actions (actor and candidate recorded); platform audit log for operator actions kept seven years; request logs with correlation and organization identifiers, credentials and emails masked |
| Support access | owner-approved, at most 24 hours, banner shown, fully logged; never includes billing, consent, LinkedIn or sensitive-field permissions |
| Resilience | `[backup schedule, tested restores, one-tenant restore procedure, capacity monitoring]` |
| Development | code review, automated tests and dependency scanning in CI, pinned dependencies, disclosure policy ([security.md](security.md)) |
| Personnel | `[background checks, training, confidentiality commitments]` |
| Incident response | playbooks and an on-call process `[to complete]`; breach notification per section 8 |
