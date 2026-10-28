# Marketing

Grow an audience and turn it into paying customers. Marketing is an industry app for marketers, creators and every
business that needs customers. Open it from the app switcher (or `g` then `2`); the screen is `#/marketing`. Its
**Leads** section (`#/leads`, `g` then `l`) finds new leads on Google Maps, Google, your own Sales Navigator or a CSV
file and prepares outreach; see [leads.md](../leads.md).

## What you can do

| Tab | What it is for |
| --- | --- |
| **Overview** | Subscribers and their growth, leads this month (and how many wait for a reply), form conversion, clicks, posts scheduled this week, and **what your leads paid this month** (with how much is still open). **Ready to post now** lists scheduled posts whose time has come, with a Copy text button. **Follow-ups due** lists leads you promised to get back to. Then a setup checklist, your best campaign, what is coming up and the newest leads. |
| **Calendar** | Posts for Instagram, Facebook, LinkedIn, X, TikTok, YouTube, email, your blog and WhatsApp on a month or week grid, the posts of that month or week in a list, and **Not scheduled yet**: drafts and ideas without a date (a draft written with AI, a repurposed copy) until you give them one. Each post has a status (idea, draft, scheduled, published), hashtags, an image or video link and a campaign. **Write with AI** drafts a post for a channel within its length limit and tone; **Repurpose** turns one post into drafts for other channels; **30-day plan** fills a month with one idea a day at the time you choose, in your own time zone, rotating your channels and content types. Mark a post published with its link when it is live. |
| **Campaigns** | A goal, dates, budget, spend, UTM tags and results that add up by themselves: clicks on its tracked links, leads from its forms, subscribers who joined through them, paying customers and what they paid, cost per lead and ROI. ROI counts what its leads paid plus any other revenue you enter for sales Aira cannot see. |
| **Links** | Tracked links: share `/p/...` instead of your page address and every visit is counted (per day), then the visitor lands on your https page with UTM tags for your website's analytics. |
| **Forms and leads** | Lead-capture forms with the questions you choose, a thank-you message and an optional email opt-in. Every enquiry from a form or your media kit lands in the **leads inbox**: search it, filter by status, by where it came from (forms or sponsorships) or by follow-ups due, and download what you see as CSV. Open a lead to **reply by email**, set what the customer is worth and a **follow-up date**, **ask for payment** or **mark it paid**. |
| **Bio page** | One link-in-bio page for Instagram, TikTok and your email signature: your picture, a short bio, a featured form button and your links in order, with click counts per link. |
| **Media kit** | Your public "work with me" page for brands: a headline, what you make content about, your audience on each channel (and your email list size if you want), and a **rate card** of packages with prices and delivery times. Brands ask for a sponsorship from the page; each request arrives in the leads inbox with its budget (or the package price) as its value. The tab shows your total reach, requests waiting for a reply and what sponsorships have earned. |
| **Subscribers** | Your email list with tags, sources and opt-in times. Search, filter by status or tag, add people, paste a CSV (email, name, tags) or download the list. Someone who unsubscribed stays unsubscribed: imports and edits never add them back; only their own opt-in on one of your forms does. |
| **Newsletters** | Write to everyone or one tag. **Queue send** (after you confirm) puts one message per subscribed address in Business hub > Outbox, each starting with the person's first name and ending with their own unsubscribe link; at most 500 per send (queue again for the rest). Nothing goes to an unsubscribed address. |

## Earning with Marketing

Marketing follows each lead from first contact to money received:

1. **A lead arrives** from a form, your bio page or your media kit (a brand asking for a sponsorship).
2. **Reply by email** from the lead. Aira starts the message for you (a greeting, a thank-you and your name); it
   waits in Business hub > Outbox until you approve it, and a new lead becomes *contacted*.
3. **Set its value and a follow-up date.** The Overview lists follow-ups that are due until the lead is won or lost.
4. **Ask for payment.** An email with the amount and your own payment link or payment instructions (Business hub >
   Business profile). Aira never takes the money. The lead becomes *won*.
5. **Mark paid** when the money arrives. The amount counts once in the Business hub's revenue (marking it again
   updates it, never doubles it) and in its campaign's ROI. **Mark not paid** (a mistake or a refund), moving the
   lead back to new, contacted or lost, deleting it or setting its value to nothing takes the payment off; the line
   stays in the revenue history, marked void.

Creators earn from brand deals through the **media kit**: share its link in your bio or send it to brands, take
requests, agree, ask for payment and mark paid.

## Reminders

Aira does not post to social networks for you. When a scheduled post's time comes, the background pass (every
few minutes) puts one **"Time to post on ..."** email for you in the outbox, with the text, hashtags and media
link, and the post appears under **Ready to post now** until you mark it published. Each post is reminded once;
moving it to a new time reminds you again then. A post more than two days late is listed but not emailed. If your
business has no email yet, the post waits: it is emailed once you add one (while it is still less than two days
late). To get
these emails without approving each one, add `post_reminder` to automatic sending in Business hub > Email.

## Public pages

| Page | What visitors see |
| --- | --- |
| Tracked link | Nothing: the visit is counted and they are sent straight on to your page. |
| Form | Your form, branded with your business name and colour; on a phone too. A wrong answer shows one clear message at the top of the form. |
| Bio page | Your picture, bio, featured form button and links. |
| Media kit | Your name and picture (from the Bio page), headline, audience numbers, packages and prices, and a sponsorship request form (brand, name, email, package, budget, dates, message). |
| Unsubscribe | A one-click unsubscribe for that subscriber. |

Visitors never see your leads, subscribers, notes or settings. Every request is checked on the server (a package
that is hidden or deleted is refused, amounts must be numbers, a hidden field catches bots). Turning a link off
makes the page unavailable at once.

## Messages Marketing puts in the outbox

| Kind | To | When |
| --- | --- | --- |
| `form_submission` | you | someone sends a form |
| `sponsorship_request` | you | a brand sends a request from your media kit |
| `post_reminder` | you | a scheduled post is due |
| `lead_reply` | the lead | you reply from the lead |
| `payment_request` | the lead | you ask for payment |
| `newsletter` | each subscriber | you queue a newsletter |

## Business hub

The hub shows your subscribers, leads this month, clicks this month and what leads paid this month, and the setup
checklist: add your business details, plan a post, create a tracked link, share a lead form, get your first
subscriber and send your first newsletter. Paid leads appear in the hub's revenue under Marketing.

## One account

In the single-user build Marketing works on your own database. Marketing and Leads hold your customers' names,
emails and messages and your revenue, so a child or teen profile in the household gets "Ask a grown-up" instead
(adults and the account itself use them as usual). Paying a lead again after "Mark not paid" counts the money in
the month it was really paid, in the Business hub and on the Overview alike. In the hosted build it is the organization's
shared data: owners, admins and coaches use it, auditors read it, candidates and billing contacts do not see it,
and its routes answer `402 app_required` until the organization has the Marketing app. Organizations never see
each other's leads, packages or revenue.

## In the Aira frame

Marketing lives at `/marketing`, with its own logo, the search box across the top, **+ New** above the menu and the
launcher, like the other Aira apps. Its two sections, Marketing and Leads, sit in the left menu with their tabs under
them as folders.

* **+ New** offers *Post*, *Campaign*, *Lead form*, *Newsletter* and *Find leads*; each opens that tab with its form
  open (the calendar's new post, the campaign, form and newsletter editors, the Leads search form with the cursor in
  it). The address drops `?new=` straight away, so Back and refresh do not open it again. A child or teen profile,
  and a hosted member who may only read the business's apps, are offered nothing to create; a hosted plan without
  Leads is not offered *Find leads*. The Overview keeps its own **Write with AI** and **New post** buttons, and every
  tab its own create button, for a phone, where the menu is folded away.
* **Search** suggests up to six records as you type, one of each kind in turn: leads from forms and sponsorships,
  saved sales leads, subscribers, posts, campaigns, lead forms, tracked links and newsletters. Choosing one opens it
  in its drawer on its own tab (a sales lead in the Leads tab). **Enter** shows every match on a results page,
  grouped by kind, with links to the same search in the leads inbox, Subscribers and Leads; on the Leads page Enter
  filters the saved leads instead.
* **The menu's folders**: under Marketing, Overview, Calendar, Campaigns, Links, Forms and leads, Bio page, Media kit,
  Subscribers and Newsletters; under Leads, Find, Leads, Outreach, Settings and History, each with its icon. The page
  head names the open folder (for example *Calendar* or *Find leads*) with a line on what it is for.
* On a phone the calendar keeps the month on screen (each post shows its time; the week view becomes a list of days),
  lists keep the columns that matter, and the Leads tab's filters fold behind **More filters**.

## For developers

Code: `aira/suite/marketing/` (tables `mk_*`; `mk_packages` and the added lead columns `value_cents`,
`follow_up_on`, `source`, `package`, `paid_at`, `contacted_at` and `mk_posts.reminded_at` are added to older
databases automatically), `auto_apply_assistant/views-marketing.js`, `auto_apply_assistant/public-marketing.js`,
`auto_apply_assistant/app-marketing.css`; tests `tests/test_suite_marketing.py`, `tests/test_suite_marketing_ui.py`,
`tests/test_suite_marketing_earn.py`, `tests/test_suite_marketing_earn_ui.py`, `tests/test_suite_marketing_fixes.py`
`tests/test_suite_marketing_fixes_ui.py` and `tests/test_suite_marketing_shell_ui.py` (the frame). Amounts typed as text go through `service.amount`, which refuses
"Infinity", "NaN" and huge exponents with a 400 before they reach `kit.cents`. `access.require_grown_up` (used by
every `/api/marketing/*` and `/api/leads/*` route) refuses a child or teen `X-Aira-Profile`. API: `/api/marketing/*`:

* `search` (`?q&limit=1..25`: a few of each kind of record, shaped as its own list returns it)
* `overview`, `posts` (`?from&to&status&channel&campaign_id&unscheduled=1&q`), `posts/{id}/publish|repurpose`,
  `draft`, `plan` (`time_zone`, `hour`, `save`)
* `campaigns`, `links`, `links/{id}/clicks|share`, `forms`, `forms/{id}/link`
* `submissions` (`?form_id&status&source=form|media_kit&due=1&q&format=csv`), `submissions/{id}` (PATCH status,
  notes, `value_cents`, `follow_up_on`), `submissions/{id}/reply` (GET a template, POST to queue),
  `submissions/{id}/payment-request`, `submissions/{id}/paid` (POST mark paid, DELETE mark not paid)
* `subscribers`, `subscribers/import`, `bio`, `bio/links`, `bio/share`, `newsletters`, `newsletters/{id}/queue`
* `media-kit` (GET, POST), `media-kit/packages` (POST, PATCH, DELETE `/{id}`), `media-kit/share` (POST, DELETE)

Public kinds: `go`, `form` (action `submit`), `bio` (action `click`), `unsubscribe` (action `unsubscribe`),
`media_kit` (action `request`). Background task: `marketing` (post reminders). See [suite-sdk.md](../suite-sdk.md).
