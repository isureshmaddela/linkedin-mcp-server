# Privacy notice (template)

> **TEMPLATE. Not legal advice, and not reviewed by counsel.** Replace every `[bracketed]` item and have a privacy lawyer review it for each region you serve (GDPR for EU candidates, CCPA for California candidates). Open items for counsel: the LinkedIn position ([automation-and-linkedin.md](automation-and-linkedin.md)), the rules for automated applications in each market, and the [data-processing agreement](dpa.md).

Version `[2026-10-1]`. Effective `[date]`. `[Vendor legal name]`, `[address]`, contact `[privacy email]`.

## Who is responsible for your data

Aira Career Copilot is provided to organizations (an outplacement firm, a staffing agency, a university, an employer) for their candidates. If you use Aira because an organization invited you, **that organization decides why and how your data is used** (the "controller"), and Aira processes it on the organization's behalf (the "processor") under a data-processing agreement. Questions about your data are best sent to your organization first; we will help them answer you. For the data of the organization's own administrators and for billing, Aira is a controller.

## What data is handled

| Data | Examples | Why |
| --- | --- | --- |
| Account | name, email, role, organization, password (stored only as a salted hash), second-factor secret (encrypted), sign-in times | to sign you in and keep the account secure |
| Candidate profile | contact details, location, experience, career preferences, resume and cover-letter text and files, standard answers | to find jobs and prepare applications |
| **Sensitive profile fields** | work authorization, visa or sponsorship needs, demographic information, salary expectations | only to fill an application question that asks for it; entered and confirmed by you; never sent to the AI model; hidden from coaches and administrators unless the organization's policy allows it **and** you consented |
| Job search and application records | jobs found, scores, applications and confirmations, screenshots of submissions, interviews, recruiters, notes, tasks | to run and track your search |
| Messages | LinkedIn conversations you choose to connect | to show and reply to messages; only if you turn LinkedIn on |
| Activity | an audit trail of who did what in your workspace (you, a coach, an administrator, an automatic run) | so you and the organization can see what was done in your name, and for security |
| Technical | request logs with an identifier, IP address, browser details, error reports (credentials and emails are masked in logs) | to run, secure and fix the service |
| Billing (organization contacts) | billing contact email, invoices | to charge for the service |

## What you agreed to

Before automation runs for you, you are shown and asked to accept: that your organization runs applications in your name; what a coach can see and do at the access level they were given; and how sensitive information is handled. Each acceptance is stored with the version of the text and the date, and you can withdraw it (withdrawing the automation consent stops automation for you). You choose separately whether to connect LinkedIn and whether to allow Easy Apply, after reading the risk notice.

## Who can see your data

- **You**: all of it.
- **Your coach**, only for the candidates they are assigned to, at the access level the organization granted (view, act, approve). They cannot see sensitive fields unless policy and your consent allow it, and they cannot enter them. Your LinkedIn messages are yours alone unless the organization's policy lets a coach see or act on them.
- **Organization owners and administrators**: your profile, jobs, applications and documents for oversight, but not sensitive fields (unless policy and consent allow it for an assigned coach), and they cannot edit or submit for you. **Auditors** can read for oversight.
- **Aira operators**: no access to your content by default. An operator can see your organization's workspace only if the owner approves a time-limited request; the organization sees a banner and the access is logged.
- **Service providers (sub-processors)**: hosting and storage, the AI model, email delivery and payment processing, listed in the data-processing agreement `[URL]`. Employers receive what is needed to submit an application you or your organization send.

We do not sell personal data.

## AI

Fit reviews and drafts use an AI model. Prompts contain job text and the profile information needed for the task and never your sensitive fields. Prompts and replies are not written to logs. `[Name the provider and whether it trains on prompts.]`

## How long data is kept

Your organization chooses a retention period within its plan's limit (default 365 days; 30 to 3,650 days possible within the plan). Expired documents are deleted automatically. If you leave an organization, its policy decides whether you receive an export and your workspace is deleted (the default) or the organization keeps it archived. When an organization closes or its subscription ends, its data is read-only and exportable for 30 days, then deleted; we keep only a billing record and a minimal audit stub for legal retention. Audit records are kept for the period of the organization's plan (30 days to 7 years).

## Your rights

Depending on where you live you can ask to access, correct, export, restrict or delete your data, to object to processing, and to withdraw consent. The service provides export and deletion (a candidate can export their own data; administrators can export or delete a workspace) and our operators can handle a request across organizations. Contact your organization or `[privacy email]`. You may complain to your data-protection authority. `[Add region-specific statements: GDPR legal bases, CCPA categories and "no sale" statement, retention by category.]`

## Cookies

Aira sets one session cookie when you sign in (`HttpOnly`, `SameSite=Strict`, `Secure` over HTTPS) and stores display preferences (theme, filters) in your browser. It uses no advertising or tracking cookies.

## Security

Passwords are salted and hashed; documents are encrypted with a key specific to your organization before they are stored; each organization's data is kept separate; access is by role and every action is logged. See [multitenant-security.md](../multitenant-security.md) for the controls. `[State certifications once obtained.]`

## International transfers

The service runs in one region per deployment: `[region]`. `[Describe transfers by sub-processors and the safeguards used.]`

## Changes

We will tell your organization's owner and show a notice in the service before material changes. Contact: `[privacy email]`.
