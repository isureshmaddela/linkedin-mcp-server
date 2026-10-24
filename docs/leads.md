# Aira Leads

Aira Leads finds, qualifies, enriches and tracks sales leads. It sits next to Careers under **Research** (`g` then `l`, `#/leads`), uses the same Aira API layer and SerpApi key, and keeps everything in your own database.

It finds, organizes and prepares. Email goes out only after a person approves each message, through the person's own mail server; calls are placed by the person; LinkedIn messages and posts are never sent by Aira.

## Where leads come from

| Source | What you get | What it costs | Needs |
| --- | --- | --- | --- |
| **Google Maps** | Local businesses: name, address, phone, website, rating, reviews, category | 1 search per page of about 20 | A SerpApi key (Aira API > Account) |
| **People via Google** | People found through `site:linkedin.com/in` queries: name, title, company, place, profile address | 1 search per page of about 10 | A SerpApi key |
| **Companies via Google** | Companies found through `site:linkedin.com/company` queries | 1 search per page | A SerpApi key |
| **Hiring signals** | Companies hiring for a role you sell into, each with the postings as a buying signal | 1 search per page of about 10 jobs | A SerpApi key |
| **LinkedIn Sales Navigator** | The people of a Sales Navigator search or lead list you built: name, title, company, place, connection degree, tenure, lead and profile addresses | No search credits | Your LinkedIn account connected in Settings > Integrations, with Sales Navigator on it |
| **People asking for a service** | Public posts (Reddit, Quora, LinkedIn posts, anywhere Google indexes) where someone asks for a web designer, marketer, writer or ads help | 1 search per page of about 10 posts | A SerpApi key |
| **CSV import** | Your own list, columns mapped by name (Name, Title, Company, Website, Email, Phone, LinkedIn, Location, Industry, Notes, Tags) | Free | A header row |

Repeated Google searches inside the cache window cost nothing and say so. Every Google search is also recorded in Aira API > History.

### Sales Navigator

Sales Navigator has no public API for individuals, so Aira reads it the way you would: in your own signed-in LinkedIn browser session.

- **Read only.** It opens your search, scrolls, and reads the result rows. It never clicks Connect, Message, InMail, Save or Like, and it does not open each lead's profile.
- **Use the address.** Build the search in Sales Navigator with all your filters, copy the address (it starts with `https://www.linkedin.com/sales/search/people`, or `/sales/lists/people/` for a lead list) and paste it into Leads. Only those two address shapes are accepted.
- **Human pace, small volumes.** 1 to 5 pages of about 25 people per run, with randomized waits between pages. LinkedIn's User Agreement restricts automated access and can restrict an account that uses it: keep volumes low, use it only on your own account, and treat it as your decision and risk.
- **It tells you why it fails.** Not signed in, no Sales Navigator on the account, a security checkpoint or a rate limit each come back as a plain message; leads read before a failure are kept.
- **Layout changes.** LinkedIn changes its pages. The reader finds rows from the structure of their links, not from class names, and keeps each row's raw text lines so a change shows up as odd titles or locations instead of silently wrong data. It was tested against stand-in pages, not the live Sales Navigator; check the first results against what you see in Sales Navigator.

## Qualifying: the ideal customer profile

Settings holds your ICP: target titles, keywords, industries, locations, words and companies to exclude, required contact channels and a minimum rating. Each lead gets a 0 to 100 fit score with the reasons listed (for example "Title matches VP Sales (+30)", "Located in Austin (+15)", "Buying signal: Hiring: SDR (+10)"). With no ICP saved, leads have no score rather than an invented one. Saving the ICP re-scores every saved lead.

## Enriching from the company website

"Enrich from website" reads the lead's own public website (the home page and up to three obvious contact, about or team pages) and lists the emails, phones and social links it publishes, each with the page it came from.

- Only public internet addresses are read: loopback, private and link-local addresses are refused on every redirect, and the connection goes to the address that was checked.
- `robots.txt` is honoured; the fetcher identifies itself as `AiraLeadsBot`.
- Nothing is guessed or pattern-built. An address that is not on the site is not reported.
- A lead's email is set automatically only when it is on the company's own domain; otherwise you pick one ("Use this email"). Role addresses such as `info@` are labelled generic.

## News signals

"Find recent news" looks up headlines about the lead's company (Aira API `google_news`, 1 search, free when cached) and keeps up to three as buying signals on the lead, each linked to its story and showing the outlet and date. They are headlines only: Aira does not read the articles or judge them. A funding, hiring or expansion headline that matches your ICP raises the lead's score; the reason is listed.

## Drafts for LinkedIn and other channels

Besides email, a draft can be a 300-character LinkedIn note, in a friendly, direct or formal tone. Aira never sends a LinkedIn message: copy it and send it yourself, and follow the rules that apply to you for unsolicited messages (CAN-SPAM, GDPR/PECR, CASL and your employer's policy).

## Finding people who need web, marketing, content or ad services

Pick the services you offer (website design and development, digital marketing and SEO, content and copywriting, paid ads, social media management) and Aira looks for three kinds of evidence. Each is a fact you can open and check; nothing is inferred beyond what the source shows.

| Evidence | How Aira finds it | What the lead holds |
| --- | --- | --- |
| **A public request** | A Google search for people asking for it ("looking for", "need a", "can anyone recommend" plus the service's phrases, on Reddit, Quora, LinkedIn posts or anywhere, posted in the past week, month or year) | A `request` lead: the post's title, what it says, badges if it mentions a budget or says it is urgent, its age, and a link to the post. You reply on the post yourself; Aira never does. |
| **A gap in a business's own presence** | Google Maps listings with no website, few reviews (under 15) or a rating under 4.0; after "Enrich", a plain website audit | Need signals on the lead. The audit lists what is visibly missing: no HTTPS, no mobile viewport tag, no meta description, a very short title, no analytics tag, no ad pixels, a footer copyright two or more years old, very little text, no social links, a slow home page. Each finding says which service it points to. |
| **A hiring signal** | Companies posting a job for the role you could do as a service (marketing manager, content writer, web developer, PPC specialist) | A `hiring` signal with the posting's link |

Choose "Services I offer" in Settings and every lead that needs one of them scores higher, with the reason shown ("Needs Website design: No website listed on Google Maps (+20)").

## Reaching them

### Email (a person approves every message)

1. **Sender settings** (Outreach > Sender settings): your name, from address, your own mail server (SMTP host, port, security, username and password, the password write-only and stored encrypted), a postal address for the footer, an optional signature, the offer text and a daily cap (default 20, at most 100). "Send a test email" proves the settings.
2. **Write emails**: select leads and choose "Write emails". Each lead with an email gets a draft written from facts it really has (a hiring signal, a website finding, a gap on its Maps listing) by the model (Ollama locally, or the hosted model), or by a template when no model is running. The model is told to mention only listed observations, never to claim the sender is from the lead's business and never to ask about their prices; a draft that breaks the rule is replaced by the template. Leads that cannot be emailed are listed with the reason.
3. **Review**: drafts wait in the Outbox. Edit, approve, reject or delete. Editing withdraws an approval.
4. **Send approved**: up to ten messages at a time through your own server, one at a time with a pause (default 5 seconds), and never past the daily cap. Every message carries your name, your postal address and a line telling the person how to opt out, and a `List-Unsubscribe` header.

Guards that run both when the draft is written and again at the moment of sending:

- **Do-not-contact list**: emails, domains and phone numbers. Anyone who replies "unsubscribe" or asks not to be called goes on it (a "do not call" outcome in the call log adds the number and email automatically). It is checked before every draft and every send.
- **Regions with strict consent rules**: a lead located in the EU, UK or Canada is refused unless you confirm in the sender settings that you have a lawful basis. This reads the lead's location text; it is a guard, not legal advice.
- **A disqualified lead** is never emailed. **A public post** has no email and is never emailed.
- **Where emails come from**: only an address the lead's source gave, an address its own website publishes, or one you typed. Aira does not guess or pattern-build addresses and does not buy lists.

Aira is a tool for one person writing to a few dozen businesses a day with a human reading each message, not for bulk mailing. You are the sender: follow CAN-SPAM (honest headers, a postal address, a working opt-out honoured promptly), and GDPR/PECR or CASL where they apply.

### Calls (you place them; Aira prepares and records)

**Aira does not dial, plays no recordings and uses no synthetic voice, and there is no auto-dialer.** Two reasons it does not do the part you might expect:

- **Google Voice forbids it.** Its acceptable-use policy prohibits automated calling, scripted or bulk commercial messages and unsolicited marketing calls, and lets Google limit or end the account ([Google Voice policy](https://www.google.com/googlevoice/program-policies.html?hl=en-US)).
- **The law restricts it.** In the US the FCC treats AI-generated voices as "artificial" voices under the TCPA, so marketing calls with one need the called person's prior express written consent ([Orrick analysis of the FCC ruling](https://www.orrick.com/insights/2024/02/Did-the-FCC-Really-Ban-AI-Generated-Voice-Calls-No-But-Restrictions-Apply)); other countries have their own rules (for example India's TRAI telemarketing regulations) and penalties are per call.

What Aira does instead, on each lead's Call panel:

- the number, a `tel:` link, and a link that opens Google Voice's dialler with the number filled in, for you to press Call (your own Google Voice number goes in the sender settings for display and for the talk track);
- whether it is a sensible hour where the business probably is (US addresses; the US rule is 8 am to 9 pm local time), and a warning when the number is on your do-not-contact list;
- a talk track written from the lead's real facts: opener, pitch, three discovery questions, answers to the usual objections, a close and a voicemail, from the model or a template;
- a call log with an outcome (no answer, left a voicemail, spoke, interested, asked me to call back, not interested, wrong number, asked not to be called again), notes and a follow-up date. The lead moves along (a "spoke" outcome marks it replied, "interested" qualified), a wrong number clears the phone, and "asked not to be called again" disqualifies the lead and adds its number and email to the do-not-contact list. Follow-ups that have come due are listed.

If you later want an automated voice agent, that needs a telephony provider you contract with, registered caller identity, and consent from every person it calls; it is a separate decision from this tool.

## Lists, statuses and export

Leads live in lists. A lead is never added twice: it is recognised by its LinkedIn profile, its Sales Navigator lead, its Google place, its company website (outside Maps, where locations of one business share a website) or its name and company. Statuses: new, contacted, replied, qualified, disqualified, customer. Bulk actions move, tag, set status or delete. Export to CSV or XLSX keeps the current filters; a cell that starts with `=`, `+`, `-` or `@` is neutralised so a spreadsheet never runs it.

## Privacy

Leads are other people's personal data. Aira keeps them only in your database, records where each came from (`source`, `source_ref`, signals and enrichment pages), and exports and deletes them with the rest of your data. Collect what you need for a stated purpose, tell people where you got their details when you contact them, and honour requests to stop. In the hosted build the customer is the controller of this data; see `docs/legal/`.

## API

All routes need a session and live under `/api/leads/`. See [api.md](api.md#leads-apileads) for the request and response shapes.
