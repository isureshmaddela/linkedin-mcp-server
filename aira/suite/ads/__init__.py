"""Ads: businesses pay to advertise on Videos, and creators earn from the ads on their videos.

* **Balance** - prepaid. Hosted, a top-up is billed through the organization's own subscription (an invoice item at
  its billing provider) and credited when billed; on this computer the owner records a payment received.
* **Campaigns** - an objective (views at a CPM, clicks at a CPC), total and daily budgets, a schedule, targeting
  (video categories, language, country) and brand safety (family-safe, mature videos allowed or not).
* **Ads** - a skippable video before a video (skip after 5 seconds), a banner on the watch page, a sponsored reel;
  each is reviewed before it runs (by Aira's operators hosted, by the computer's owner here).
* **Reports** - impressions, clicks, CTR, spend and effective CPM per ad and per day, with a CSV export.
* **Earnings** - each counted impression or click credits the video's creator a share (55% unless the platform sets
  another): estimated, then confirmed when the month closes after the invalid-traffic review, then payable.

The data lives in the platform-wide Videos database (:mod:`aira.suite.videos.db`); the rules are in
:mod:`.service`.
"""
