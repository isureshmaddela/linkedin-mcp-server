"""News: the latest headlines every day, in each person's own language and country, and a newsletter to earn from.

Headlines come from Google News's public RSS feeds (top stories, topics, keyword searches and one-off searches, no
key needed) and from RSS or Atom feeds a person adds. Feeds are fetched safely (public addresses only, size and time
capped, no DTDs) and cached in the account's database; a feed is refreshed when it is older than 30 minutes, and the
background task keeps followed feeds fresh. Each person (a life profile) has their own language, region, topics,
keywords, sources, hidden sources, saved and read articles; children get a child-safe mode.

An adult can run a newsletter (free or paid monthly): they pick stories with a note, publish issues to a public page
and to their readers by email (through the outbox), and confirm readers' payments, which go into the revenue ledger.

Modules: :mod:`.feeds` (languages, topics, URLs, fetching and parsing), :mod:`.store` (tables),
:mod:`.service` (the reading rules), :mod:`.letter` (the newsletter), :mod:`.news_routes` (``/api/news/*``, the
newsletter's public page, Today cards, pulse, hub numbers, background task).
"""
