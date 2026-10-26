"""Videos: watch, upload and earn - a video platform for every age, with ads that pay creators.

* **Channel** - one per person (made on first use): name, picture, about, country and language; it is also the
  person as a viewer (likes, comments, history, subscriptions).
* **Videos** - uploaded in resumable parts and served as uploaded (MP4, MOV, WebM; no transcoding), with a title,
  description, category, tags, a content rating (everyone, teen, mature), who can watch (public, unlisted, private),
  a picture (a frame picked in the browser or an image) and captions (WebVTT). Three kinds: videos, **reels** (tall,
  at most 90 seconds) and **short films** (genre and credits).
* **Watching** - Home (subscriptions, new, popular by a published formula), search, categories, the watch page
  (likes, comments, subscribe, share), the Reels feed, Short films, history, watch later and playlists. A view counts
  once a day per viewer after 30 seconds (or 30% of a short video); the uploader's own views do not count.
* **Safety** - ratings respected for children, teens and easy mode; reports of videos, comments and channels;
  moderation by the household owner here and by Aira's operators hosted; a public copyright notice form with
  takedowns, counter-notices and strikes; limits on uploads, comments and reports.
* **Earning** - ads bought in the Ads app run on monetized channels' videos; the creator's Earnings page shows what
  they earned (estimated, confirmed, payable) and asks for payouts.

Like Social, Videos keeps its data in one database for the whole platform (:mod:`.db`, shared with Ads) and its
files in one media folder (:mod:`.media`), so people in different households and organizations can watch each
other's videos.
"""
