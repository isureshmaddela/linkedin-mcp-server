# News

The latest headlines every day, in your own language and country, and a newsletter you can earn from. News is one
of the everyday (life) apps: every person in the household (or every member of the organization, hosted) has their
own language, topics, words they follow, sources, hidden sources, saved and read articles, and their own newsletter.

## What you can do

* **Top stories** for your country, in English, Telugu or Hindi (more languages are a line in
  `aira/suite/news/feeds.py` `LANGUAGES`). English offers India, the United States and the United Kingdom.
* **Topics**: Your country, World, Business, Technology, Entertainment, Sports, Science and Health. Choose which
  ones you follow under Sources & settings.
* **For you**: follow up to 10 words or topics ("Hyderabad metro", "cricket"). Tick **Alert me** and a new headline
  about it shows a reminder (once per article), even while you are on another page.
* **Search** any news (a word, a place, a person) without following it; **Follow this** adds it to For you.
* **Hide a source** you do not want to read (the **Hide ...** button on any headline). Hidden sources are listed
  under Sources & settings, each with **Show again**.
* **Your own sources**: add the RSS or Atom feed of any newspaper, broadcaster or blog (https only). Children cannot
  add sources (ask an adult).
* **Save** articles for later, **Read** the original (it opens the news site in a new tab), **Share** it (the
  phone's share sheet, or the link is copied), and **Listen**: a single article, or "Listen to today's headlines",
  which reads a short briefing (written by the writing model in your language when one runs, otherwise the five top
  headlines). Saved articles download as a spreadsheet (CSV).
* The same story reaching you through two feeds (two topics, or a topic and a word you follow) shows once.
* **Today**: the "Today's headlines" card shows your top five; the menu badge counts new headlines since you last
  opened News. A newsletter writer also sees a "Your newsletter" card: readers waiting for their payment to be
  confirmed, and their readers and what they earned this month (the Business hub lists only the business apps, so
  the writer's numbers show here).
* **Child-safe mode** is on for children and they cannot turn it off: only Science, Technology, Sports and
  Entertainment, and headlines whose title or summary has upsetting words (in English, Hindi and Telugu) are hidden.
  Search, followed words (For you) and their alerts look only in those sections, never in all the news, and the
  Search tab says so. Adults can turn it on for themselves.
* **The same story once**: headlines that differ only in case, spaces or punctuation are one story. Hindi and
  Telugu vowel signs count as letters, so "दिल की बात" and "दाल की बात" are two stories.

## Earn: your newsletter

Adults can run a newsletter from the **My newsletter** tab (children and teenagers do not see it):

1. **Start** it with a name, a line on what it is about and a monthly price (leave it empty or 0 for a free one).
   A price that cannot be read ("fifty", "Rs fifty") is an error in the form, never a free newsletter; when changing
   the price of a paid newsletter, an empty price is an error too (type 0 to make it free).
   Ideas: a local digest (what happened in your town this week), a trade digest (crop prices, rules and tenders for
   farmers or shopkeepers), a study digest (current affairs for exam candidates).
2. **Pick stories**: press **Add to my newsletter** on any headline (Top stories, Topics, For you, Search, Saved) and
   add a note on why it matters. Under Next issue you can edit notes, move stories up or down and remove them (up
   to 20 stories an issue).
3. **Publish**: give the issue a title and an opening (or press **Suggest a title and opening**: the writing model
   when one runs, a plain template otherwise) and press **Publish this issue**. It appears on the newsletter's page
   and is emailed to every reader who should get it: every reader of a free newsletter, paid-up readers of a paid
   one. Emails go through the Business hub outbox and wait for approval unless the kind `newsletter_issue` is set to
   send automatically. An issue can be deleted from the page later (emails already sent stay sent).
4. **Share the page** (Create link; the first issue makes it if you have not). If you **Turn off** the page it
   stays off: publishing an issue or confirming a payment never brings it back (issues still go by email, without a
   page address), until you create a new link. Visitors see the newsletter, who writes it and the latest 12 issues: every
   story of a free newsletter, the first story of each issue of a paid one ("2 more stories for subscribers").
5. **Readers subscribe** on the page with their name and email.
   * Free: they are in, and a welcome email carries their code.
   * Paid: they get the amount and a reference, and the business's payment link and instructions (Business hub
     settings) show how to pay. Aira does not take card payments: readers pay you directly.
6. **Confirm payments**: when the money arrives, press **Payment received** next to the reader. That month (30 days,
   added to any time still left) goes into the revenue ledger (Business hub, "Earned from newsletters this month"),
   and the reader gets a new access code by email (the code is also shown to you once, to pass on yourself). Each
   further month is **Another month paid**. **Undo last payment** takes the last month out of the ledger. A reader
   whose month ran out stops getting issues and can pay again with the same reference.
7. With their code, a reader opens every story on the page (the browser remembers it), and with their email and code
   they **stop the newsletter** themselves. Someone still waiting to pay withdraws their request with their email
   and payment reference. You can **Remove** a reader too (their payments stay in your records; undoing a removed
   reader's payment keeps them removed). On the page each form shows its own message under its buttons, and the next
   attempt replaces it.

Readers are listed with filters (waiting for payment, paid, free, stopped), a search by name, email or reference,
and a CSV download. The tab shows paid readers, readers waiting for payment, and what you earned this month and in
total. The readers CSV follows the filter and the search. When a free newsletter gets a price, Aira asks first,
then emails every free reader (kind `newsletter_price`) the new price and how to keep reading; issues go to paid-up
readers only, the free readers stay listed ("Free readers who have not paid") and one who subscribes again is asked
to pay. When a paid newsletter becomes free (Aira asks first), readers waiting for payment become free readers, each
with a welcome email.

## Where the news comes from

Google News's public RSS feeds (no key, no account): the front page, the topic sections and searches, for the
language and country you choose; plus the feeds you add. Aira only shows the headline, source, time, a short
plain-text summary and (when the site offers an https one) a picture; reading the full story opens the publisher's
own page. Aira does not copy articles; a newsletter lists headlines and links with your own notes.

Feeds are kept for 30 minutes: opening News within that time answers from the account's own copy, and the background
job (every few minutes) fetches again every feed someone follows, so the news is ready when you look. If a site does
not answer, you see the earlier news and "Could not refresh; showing earlier news." Articles older than 14 days are
dropped unless someone saved them; a one-off search nobody follows is dropped after a day.

## Safety and privacy

* Only public internet addresses are fetched (checked again on every redirect; the connection goes to the address
  that was checked), https only, at most 3 redirects, 10 seconds and 2 MB per feed.
* Feeds with a document type or entity declarations are refused (no XML entity expansion).
* Summaries are reduced to plain text; links must be http(s), pictures https (a picture that does not load is
  removed). Nothing from a feed is ever put into the page as HTML.
* Saved articles, read marks, keywords, sources, hidden sources and newsletters are each person's own; in the hosted
  build no member sees another's, and one organization never sees another's.
* The newsletter's public page shows only the newsletter, the writer's name and published issues. Visitors never see
  other readers; asking again with the same email gives the same answer. Codes are stored hashed. At most 300
  readers can wait for payment confirmation at once, and at most 50 new readers or requests a day (so the page
  cannot be used to mail strangers); the platform also rate-limits visitors.

## API

| Method | Path | What |
| --- | --- | --- |
| GET | `/api/news/feed?topic=top\|<SECTION>\|foryou\|source:<id>\|all&since=<ISO>` | Articles, newest first; `since` returns only those that arrived later; `now` to send next time. Opening a tab (no `since`) resets the badge. |
| GET | `/api/news/search?q=` | Any news about `q` (at most 60 characters) in your language and country; `following` says whether you follow it. |
| GET / POST | `/api/news/settings` | Language, region, topics, child-safe mode (plus the languages, topics, keywords, sources and hidden sources, and what you may do: `can_add_sources`, `can_publish`, `has_newsletter`). |
| POST, PATCH, DELETE | `/api/news/keywords[/<id>]` | Follow a word (`query`, `alert`), turn its alert on or off, stop following. |
| POST, DELETE | `/api/news/sources[/<id>]` | Add (`url`, optional `title`; it is read once to check it) or remove your own feed. 403 for children. |
| POST, DELETE | `/api/news/muted[/<id>]` | Hide a source (`source`, its name as the cards show it) or show it again. |
| POST, DELETE | `/api/news/articles/<id>/save` | Save or unsave. |
| POST | `/api/news/articles/<id>/read` | Mark read. |
| GET | `/api/news/saved[?format=csv]` | Your saved articles (CSV: title, source, link, published_at, saved_at, summary). |
| POST | `/api/news/refresh` | Fetch everything you follow now. |
| GET | `/api/news/briefing` | Your top five headlines and a short summary to read aloud. |
| GET / POST | `/api/news/newsletter` | Your newsletter (picks, issues, numbers, link, `page_off` when you turned the page off) / start it or change `name`, `about`, `price_cents` (a month; 0 is free; empty or unreadable is 400). The POST answer adds `freed` (waiting readers let in) and `told` (free readers emailed about a new price). 403 for children and teenagers. |
| POST, DELETE | `/api/news/newsletter/link` | Create or turn off the public page. |
| POST, PATCH, DELETE | `/api/news/newsletter/picks[/<id>]` | Add an article to the next issue (`article_id`, `note`), change its `note` or `move` it (`up`, `down`), remove it. |
| POST | `/api/news/newsletter/suggest` | A title and an opening for the next issue (changes nothing). |
| POST, DELETE | `/api/news/newsletter/issues[/<id>]` | Publish the next issue (`title`, `intro`), or take one off the page. |
| GET | `/api/news/newsletter/subscribers?status=&q=[&format=csv]` | Your readers. |
| POST | `/api/news/newsletter/subscribers/<id>/paid` | A month's payment arrived: revenue recorded, 30 more days, a new code emailed (and returned once). |
| POST | `/api/news/newsletter/subscribers/<id>/undo` | Undo the last payment. |
| DELETE | `/api/news/newsletter/subscribers/<id>` | Remove a reader. |

Public page (kind `newsletter`): `GET /api/public/<token>` (the page), `POST /api/public/<token>/subscribe`
(`name`, `email`), `POST /api/public/<token>/unsubscribe` (`email`, `code`; the payment reference for a reader
still waiting to pay), `GET /api/public/<token>/issues` with
the code in the `Aira-Secret` header (every story, for a paid-up reader).

Email kinds (Business hub > Outbox): `newsletter_welcome`, `newsletter_access`, `newsletter_issue`,
`newsletter_price`. Revenue: source
type `newsletter_payment`, one line per month paid.

Tests: `tests/test_suite_news.py`, `tests/test_suite_news_features.py` (fixes, search, hidden sources, CSV, the
newsletter on its own, on the single-user server with an anonymous visitor, and hosted),
`tests/test_suite_news_fixes.py` (the second round of fixes: the page that stays off, prices, child-safe search,
Hindi and Telugu headlines, Today numbers, withdrawing, the daily cap, the readers CSV) and
`tests/test_suite_news_ui.py` (two browser journeys, including a visitor on a phone who subscribes and pays), against
`tests/news_stub.py` (`AIRA_NEWS_GOOGLE_BASE` points at it; `AIRA_NEWS_ALLOW_LOCAL=1` allows plain http to the
loopback address only, for tests).
