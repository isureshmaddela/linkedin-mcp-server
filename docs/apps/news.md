# News

The latest headlines every day, in your own language and country. News is one of the everyday (life) apps: every
person in the household (or every member of the organization, hosted) has their own language, topics, words they
follow, sources, saved and read articles.

## What you can do

* **Top stories** for your country, in English, Telugu or Hindi (more languages are a line in
  `aira/suite/news/feeds.py` `LANGUAGES`). English offers India, the United States and the United Kingdom.
* **Topics**: Your country, World, Business, Technology, Entertainment, Sports, Science and Health. Choose which
  ones you follow under Sources & settings.
* **For you**: follow up to 10 words or topics ("Hyderabad metro", "cricket"). Tick **Alert me** and a new headline
  about it shows a reminder (once per article), even while you are on another page.
* **Your own sources**: add the RSS or Atom feed of any newspaper, broadcaster or blog (https only).
* **Save** articles for later, **Read** the original (it opens the news site in a new tab), and **Listen**: a single
  article, or "Listen to today's headlines", which reads a short briefing (written by the writing model in your
  language when one runs, otherwise the five top headlines).
* **Today**: the "Today's headlines" card shows your top five; the menu badge counts new headlines since you last
  opened News.
* **Child-safe mode** is on for children and they cannot turn it off: only Science, Technology, Sports and
  Entertainment, and headlines with upsetting words (in English, Hindi and Telugu) are hidden. Adults can turn it on
  for themselves.

## Where the news comes from

Google News's public RSS feeds (no key, no account): the front page, the topic sections and searches, for the
language and country you choose; plus the feeds you add. Aira only shows the headline, source, time, a short
plain-text summary and (when the site offers an https one) a picture; reading the full story opens the publisher's
own page. Aira does not copy articles.

Feeds are kept for 30 minutes: opening News within that time answers from the account's own copy, and the background
job (every few minutes) fetches again every feed someone follows, so the news is ready when you look. If a site does
not answer, you see the earlier news and "Could not refresh; showing earlier news." Articles older than 14 days are
dropped unless someone saved them.

## Safety

* Only public internet addresses are fetched (checked again on every redirect; the connection goes to the address
  that was checked), https only, at most 3 redirects, 10 seconds and 2 MB per feed.
* Feeds with a document type or entity declarations are refused (no XML entity expansion).
* Summaries are reduced to plain text; links must be http(s), pictures https. Nothing from a feed is ever put into
  the page as HTML.
* Saved articles, read marks, keywords and sources are each person's own; in the hosted build no member sees
  another's, and one organization never sees another's.

## API

| Method | Path | What |
| --- | --- | --- |
| GET | `/api/news/feed?topic=top\|<SECTION>\|foryou\|source:<id>\|all&since=<ISO>` | Articles, newest first; `since` returns only those that arrived later; `now` to send next time. Opening a tab (no `since`) resets the badge. |
| GET / POST | `/api/news/settings` | Language, region, topics, child-safe mode (plus the languages, topics, keywords and sources available). |
| POST, PATCH, DELETE | `/api/news/keywords[/<id>]` | Follow a word (`query`, `alert`), turn its alert on or off, stop following. |
| POST, DELETE | `/api/news/sources[/<id>]` | Add (`url`, optional `title`; it is read once to check it) or remove your own feed. |
| POST, DELETE | `/api/news/articles/<id>/save` | Save or unsave. |
| POST | `/api/news/articles/<id>/read` | Mark read. |
| GET | `/api/news/saved` | Your saved articles. |
| POST | `/api/news/refresh` | Fetch everything you follow now. |
| GET | `/api/news/briefing` | Your top five headlines and a short summary to read aloud. |

Tests: `tests/test_suite_news.py` and `tests/test_suite_news_ui.py`, against `tests/news_stub.py`
(`AIRA_NEWS_GOOGLE_BASE` points at it; `AIRA_NEWS_ALLOW_LOCAL=1` allows plain http to the loopback address, for tests
only).
