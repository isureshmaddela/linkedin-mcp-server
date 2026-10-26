# Marketing

Grow an audience and turn it into customers. Marketing is an industry app for marketers, creators and every
business that needs customers. Open it from the app switcher (or `g` then `6`); the screen is `#/marketing`.

## What you can do

| Tab | What it is for |
| --- | --- |
| **Overview** | Subscribers and their growth, leads this month (and how many wait for a reply), form conversion, clicks, posts scheduled this week, your best campaign, what is coming up and the newest leads. A setup checklist shows what to do first. |
| **Calendar** | Posts for Instagram, Facebook, LinkedIn, X, TikTok, YouTube, email, your blog and WhatsApp on a month or week grid. Each post has a status (idea, draft, scheduled, published), hashtags, an image or video link and a campaign. **Write with AI** drafts a post for a channel within its length limit and tone; **Repurpose** turns one post into drafts for other channels; **30-day plan** fills a month with one idea a day, rotating your channels and content types (tips, behind the scenes, customer stories, offers, questions). Mark a post published with its link when it is live. |
| **Campaigns** | A goal, dates, budget, spend and the revenue it brought (your estimate), with UTM tags. Results add up automatically: clicks on its tracked links, leads from its forms, subscribers who joined through them, cost per lead and ROI. |
| **Links** | Tracked links: share `/p/...` instead of your page address and every visit is counted (per day), then the visitor lands on your https page with UTM tags for your website's analytics. |
| **Forms and leads** | Lead-capture forms with the questions you choose (name, email, phone, company, message, your own question), a thank-you message and an optional email opt-in that adds the person to your list with a tag. Share the form page anywhere; each lead lands in the inbox (new, contacted, won, lost, with notes) and you get an email in the outbox. Download leads as CSV. A hidden field quietly drops bot submissions. |
| **Bio page** | One link-in-bio page for Instagram, TikTok and your email signature: your picture, a short bio, a featured form button and your links in order, with click counts per link. |
| **Subscribers** | Your email list with tags, sources and opt-in times. Add people, paste a CSV (email, name, tags) or download the list. Someone who unsubscribed stays unsubscribed: imports and edits never add them back; only their own opt-in on one of your forms does. |
| **Newsletters** | Write to everyone or one tag. **Queue send** (after you confirm) puts one message per subscribed address in Business hub > Outbox, each starting with the person's first name and ending with their own unsubscribe link; at most 500 per send (queue again for the rest). Nothing goes to an unsubscribed address. Messages leave from your own mail server once you approve them. |

## Public pages

| Page | What visitors see |
| --- | --- |
| Tracked link | Nothing: the visit is counted and they are sent straight on to your page. |
| Form | Your form, branded with your business name and colour; on a phone too. |
| Bio page | Your picture, bio, featured form button and links. |
| Unsubscribe | A one-click unsubscribe for that subscriber. |

Visitors never see your leads, subscribers or settings. Turning a link off makes the page unavailable at once.

## Business hub

The hub shows your subscribers, leads this month and clicks this month, and the setup checklist: add your
business details, plan a post, create a tracked link, share a lead form, get your first subscriber and send your
first newsletter.

## For developers

Code: `aira/suite/marketing/` (tables `mk_*`), `auto_apply_assistant/views-marketing.js`,
`auto_apply_assistant/public-marketing.js`; tests `tests/test_suite_marketing.py` and
`tests/test_suite_marketing_ui.py`. API: `/api/marketing/*` (overview, posts, draft, plan, campaigns, links,
forms, submissions, subscribers, bio, newsletters). See [suite-sdk.md](../suite-sdk.md).
