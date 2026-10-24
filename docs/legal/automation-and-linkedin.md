# Automation rules and the LinkedIn notice (template)

> **TEMPLATE. Not legal advice, and not reviewed by counsel.** This states what the product does today. Two of the three open legal items live here: (1) the **LinkedIn position**, namely whether to keep LinkedIn features at all, restrict them by plan, or remove them; (2) the **terms for automated applications in each market**, because some jurisdictions regulate automated decision-making about employment and may require human review, notice or the right to object. The third open item is the [data-processing agreement](dpa.md). Do not publish the text of this page as legal advice or as a promise until counsel has reviewed it.

## Rules the automation follows

These are enforced in code (the form engine in `aira/apply_engine.py`, the run pipeline in `aira/autopilot.py`, and the hosted gate in `aira_cloud/gate.py`). They apply to every organization and cannot be switched off by a plan or a policy.

1. **No guessed answers.** The service answers an application question only from information the candidate entered and confirmed, or from an answer the candidate gave to a held question (and chose to remember). A question it cannot answer is held for the candidate; it is never filled with a guess.
2. **Sensitive information is the candidate's own.** Work authorization, visa or sponsorship, demographic information and salary are entered and confirmed by the candidate only. They are never sent to the AI model, never filled without a confirmed answer, and are masked from coaches, administrators and auditors unless the organization's policy and the candidate's consent allow a coach to see them (a coach still cannot enter them).
3. **No CAPTCHA bypass.** A form that needs one is not submitted.
4. **No sign-in on employer sites.** The service does not log in to an employer's or recruiting platform's account on a candidate's behalf; a job that needs an account is reported as one to apply to manually.
5. **Evidence for every submission.** Each submission stores an encrypted screenshot and the confirmation text, and an audit entry with the actor (a person or the automatic run) and the candidate.
6. **Two switches for automatic submission.** The organization's policy and plan must allow it, and the candidate (or a coach with approve rights) must turn it on in the candidate's own search plan. Otherwise the form is filled and waits for a person to submit (review mode).
7. **Limits.** Applications per candidate per day and per organization per month are enforced when a run starts, not only when it was scheduled. A paused organization or candidate, an unpaid or over-limit subscription, a suspended organization, or missing candidate consent stops automation at once.
8. **Candidates can see and stop it.** A candidate sees what was done in their name, can pause or resume their own automation (unless an administrator paused it), and can withdraw the automation consent, which stops it.
9. **Review mode is first-class.** An organization can require human review for all candidates, and can list regions where review is required (the candidate's location is matched against the list). `[Counsel: whether regional review rules need a stronger mechanism per market.]`
10. **Job boards.** Aira Jobs uses boards' published APIs with their terms (credit and link back, no republication, polite caching). The platform can switch a source off for every organization when an employer system changes or a board's terms require it.

## LinkedIn

### What the product does with LinkedIn

When a candidate connects LinkedIn, the service uses the **candidate's own LinkedIn session**, in a browser profile kept for that candidate only, to search and read jobs, to read and reply to messages if the candidate turns that on, and, if the candidate separately turns on Easy Apply, to submit Easy Apply applications.

### Defaults

- Off for every organization until its policy sets "LinkedIn allowed" **and** its plan includes LinkedIn (Team and Enterprise).
- Off for every candidate until the candidate connects LinkedIn after acknowledging the notice below. Easy Apply is a further, separate opt-in and also needs organization policy.
- The platform can switch off LinkedIn entirely, or only Easy Apply, for all organizations with a feature flag, with no deployment. Only the candidate signs in to LinkedIn.

### The notice shown to a candidate (the text the service returns until it is acknowledged)

> LinkedIn's User Agreement forbids automated access. Connecting LinkedIn lets Aira search, read and (if you enable it) apply and reply through your own session, which can lead LinkedIn to restrict your account. Send acknowledge: true to confirm you understand.

The API refuses to connect LinkedIn until the request carries `acknowledge: true`, and the interface is expected to show this text as a confirmation the candidate must accept. The acknowledgement is recorded (`linkedin.opt_in` in the organization's audit log, with the time) and Easy Apply's opt-in is recorded separately. `[Counsel to review and, if kept, finalize the wording, including whether to require a signed acknowledgement from the organization on Enterprise.]`

### Open decision for counsel

| Option | Effect |
| --- | --- |
| Keep, opt-in per candidate (as built) | the product works without LinkedIn; risk sits with candidates who opt in |
| Restrict by plan | already limited to Team and Enterprise; could be limited to Enterprise with a signed acknowledgement |
| Remove | switch the flag off, delete the policy option, remove the plan feature |

Risk controls already in place: off by default, per-candidate opt-in, plan gating, a global kill switch, no attempt to bypass any LinkedIn control, and an operator playbook for restrictions ([multitenant-operations.md](../multitenant-operations.md)). Note that the hosted build has no remote sign-in streaming: LinkedIn sessions run on the host that runs the service (see [multitenant.md](../multitenant.md), known gaps).

## Automated decisions about employment

The service automates *applying*, not *deciding*: it does not rank, reject or screen people on behalf of an employer. It scores jobs for a candidate with an explainable match and an organization chooses what to submit. Nonetheless, organizations in some markets may have duties (notice, human review, the right to contest) when they use automation in connection with employment. `[Counsel to list the markets, the duties, and the product settings that satisfy them; the organization policy can already require review mode and region lists.]`

## Sales Navigator (Aira Leads)

Aira Leads can read a Sales Navigator people search or lead list that the person built, in their own signed-in browser, through the same session and the same hosted gates as the rest of the LinkedIn automation (plan feature, organization policy, the candidate's acknowledged opt-in, the platform kill switch). It only reads: it never connects, messages, sends InMail or saves anyone. The LinkedIn and Sales Navigator terms still forbid automated access and LinkedIn can restrict the account; the same open question for counsel applies, with one addition: the people on those pages are third parties, so the lead-data terms in the [DPA template](dpa.md#3a-prospect-data-collected-with-aira-leads) apply too. Volumes are capped (five pages of about 25 people per run, paced at human speed). Aira Leads never sends a LinkedIn message or connection request for anyone; a LinkedIn note is a draft to copy.

## Email and calls from Aira Leads

On Team and Enterprise a candidate can send commercial email to leads through their own SMTP account after approving each message, and any user can keep a call log. These are the customer's own marketing activities, subject to CAN-SPAM, GDPR and PECR, CASL and local rules, and to the terms of their mail provider (large providers restrict bulk or unsolicited mail). Aira has no dialler and no automated or synthetic-voice calling: Google Voice's policy forbids automated and bulk calling, and in the US the FCC treats AI-generated voices as artificial voices that need the called person's prior written consent for marketing calls. See the [DPA template](dpa.md#3a-prospect-data-collected-with-aira-leads).
