# Videos

Watch, upload and earn. Videos is Aira's own video platform: videos, **reels** and **short films** from people
everywhere, your own uploads, and a share of the ads that run on them. It is one catalog for everyone on this
computer (every Aira account) or, in the hosted product, for the members of every organization that has Videos.

Open it from the apps menu. The menu badge counts new notifications on your channel (subscribers, comments,
messages from the moderators, payouts). Today shows new videos from the channels you follow and, when you earn,
what you earned today and this month.

## Watching

* **Home**: from your subscriptions, Popular and New, with category buttons and a search box (videos and channels).
* **Popular** is worked out in the open: `(views in the last 7 days + 2 × likes + 3 × comments + 1) ÷ (hours since
  upload + 2)^1.2`. Nothing is paid for and nothing is personal. Unlisted and private videos are never listed.
* **The watch page**: the player (seeking works; captions when the uploader added them), Like, Subscribe, Watch
  later, Add to playlist, Share and Report, the description, tags and a short film's credits, comments, and Up next.
* **Reels**: tall videos one at a time. They start muted (Sound on to hear them); move with the mouse wheel, a swipe,
  the arrow keys (or J and K), or the Previous and Next buttons.
* **Short films**: films up to an hour, by genre.
* **Library**: your history (clear it any time), Watch later, your playlists and the channels you follow.
* A **view** counts once a day per person, after 30 seconds (or 30% of a shorter video), measured on the server.
  Nobody's views of their own videos count.

## Uploading

Studio > **Upload a video**. Choose the file, then the kind (Video, Reel or Short film), title, description,
category, tags, who it suits (For everyone, Teens and older, Adults only), who can watch it (Everyone, Anyone with
the link, Only me), its language and whether it is made for children.

* **Formats**: files are kept as uploaded, nothing is converted. MP4 (H.264 with AAC) plays in every modern browser;
  WebM plays in Chrome, Edge, Firefox and recent Safari; MOV plays in Safari and usually in Chrome; Matroska (MKV) is
  accepted but may not play, so convert it to MP4 or WebM. Files are checked by their content, not their name.
* **Size**: up to 2 GB a video on this computer (`AIRA_VIDEO_MAX_BYTES` changes it); pictures 10 MB, captions 1 MB.
  Hosted, videos count against the organization's storage on its plan (with its documents).
* **Reels** are tall (portrait) and at most 90 seconds. **Short films** have a genre and credits (one per line, like
  `Director: Asha Rao`).
* **The picture**: move the slider to the moment you want and press **Use this frame** (the frame is drawn in your
  browser), or upload a JPEG, PNG or WebP picture. Without either, a frame from the start is used.
* **Captions**: a WebVTT file (`.vtt`).
* **Resuming**: files go up in 8 MB parts. If the connection drops or the tab closes, choose the same file again and
  press Upload: it carries on from where it stopped.
* Edit a video's details, share it, or delete it (its files are removed) under **Your videos**.

### Where the files are kept (and disk space)

On this computer the files go to the folder named by `AIRA_MEDIA_DIR`, or the `media` folder next to Aira's
databases. Aira refuses an upload that would leave less than 2 GB free on that drive
(`AIRA_MEDIA_RESERVE_BYTES` changes the reserve) and the message says to set `AIRA_MEDIA_DIR` to a folder on a
drive with more space. **On a computer whose system drive is nearly full, set `AIRA_MEDIA_DIR` before uploading**
(for example `AIRA_MEDIA_DIR=D:\AiraMedia`) and restart Aira. In the hosted product the files are in the `media`
folder of its data root (or `AIRA_MEDIA_DIR`); they are not put in the encrypted document store, which holds whole
files in memory.

## Sharing

**Share** copies the address for people who use Aira. The uploader of a public or unlisted video (not rated for
adults, not by a child) can also make a page **anyone can watch** without an account (`/p/<token>`); turn it off
at any time, and it turns off by itself when the video is made private or rated for adults. Public pages show no
ads; their views count once a day per visitor.

## Safety for every age

* **Children** see only videos for everyone, upload only private videos, do not comment, are never shown ads and
  their channels are not shown to others. **Teens**, people in easy mode, and anyone who turns on **restricted
  mode** (Studio > Your channel) never see videos for adults.
* **Report** a video, comment or channel. Comments are plain text, words on the blocked list become stars, and
  uploads (30 a day), comments (60 an hour) and reports (30 an hour) are limited.
* **Moderation**: on this computer each household's owner sees the reports their household filed or that are about
  their household's people (hide, restore, suspend someone of their own household, dismiss); the owner of the
  computer's first account sees every report and handles copyright notices. In the hosted product moderation is
  Aira's: the platform console's **Videos moderation** page (support and platform admins), every action in the
  platform audit log (`videos.moderate`, `videos.hide`, `videos.restore`, `videos.suspend`, `videos.copyright_*`).

## Copyright

* Anyone can send a **copyright notice** without an account at `/videos/copyright` (also linked under every public
  video): the video, their name and email, who they act for, the work that was copied, two statements (good faith;
  accurate and authorised) and a typed signature. Ten notices an hour per sender.
* A moderator **takes the video down** (it plays for nobody but its uploader, who is told) or **rejects** the notice.
  A takedown is a **strike** for 90 days; a channel with three strikes cannot upload and cannot earn.
* The uploader (an adult) can send a **counter-notice** from Studio > Copyright (their name, why the video should
  come back, a good-faith statement). The moderator then **puts it back** (the strike ends) or **keeps it down**.

## Earning

See [Ads](ads.md) for the advertiser's side. A creator turns earning on under **Earnings**:

* **Who can earn**: an adult (18 or older) who accepted the creator terms, with at least one public video, a channel
  that is not suspended, no copyright strikes and no video hidden by a moderator in the last 30 days.
* Ads then run on the channel's public and unlisted videos (a creator can turn ads off on one video under Edit).
* The page shows **Earned today**, **This month** (estimated), **Ready to be paid** and **Paid to you**, each video's
  views, ads shown, clicks, earnings and **RPM** (earnings per 1,000 views), the months and the payouts.
* Earnings are **estimated** until the month is closed (from the 2nd of the next month) after a check for invalid
  traffic, then **confirmed** and payable. **Ask to be paid** once at least the minimum ($10 unless the platform set
  another) is confirmed, with your bank or UPI details. The platform (hosted) or the computer's owner pays outside
  Aira and marks the payout paid with a reference; you are told. On this computer a paid payout also joins the
  account's revenue in the Business hub.

## Where the data lives

Videos has one database for the whole server (`videos.sqlite3`, shared with Ads) next to the accounts database on
this computer, or in the data folder of the hosted product, and one media folder. People are identified by their
household profile (this computer) or their organization membership (hosted), never by email address.

Because it sits outside every organization's database, the hosted product handles it explicitly: a person's own
export carries `videos.json` (their channel, videos' details, comments, playlists, subscriptions, earnings and
payouts; not the files); removing a member erases their channel, videos and files, comments, likes, history and
playlists; deleting an organization erases all of its members' channels and its ads. Money records stay for the
books without the person: impressions on their videos keep the amount advertisers paid, unpaid estimated earnings
end and open payout requests are declined. Tests point Videos at a scratch database and media folder
(`tests/conftest.py`), never the real ones.

## For developers

`aira/suite/videos/` (`db.py` the shared database, `media.py` uploads, type checks, the free-space reserve, ranges and
signed addresses, `service.py` the rules, `videos_routes.py` the routes), `auto_apply_assistant/views-videos.js`,
`public-videos.js`, `copyright.html` and `videos-copyright.js`; tests in `tests/test_suite_videos.py` and
`tests/test_suite_videos_ui.py`. A `<video>` cannot send the person's headers, so the API hands out media addresses
signed for six hours (`/api/videos/media/<file>?e=&s=`), served in ranges of at most 4 MB (206 Partial Content) with
the file's type and `X-Content-Type-Options: nosniff`; the page's policy allows `media-src 'self' blob:` (a picked
file is previewed from a `blob:` address).
