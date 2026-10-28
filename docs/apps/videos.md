# Videos

Watch, upload and earn. Videos is Aira's own video platform: videos, **reels** and **short films** from people
everywhere, your own uploads, and a share of the ads that run on them. It is one catalog for everyone on this
computer (every Aira account) or, in the hosted product, for the members of every organization that has Videos.

Open it from the apps menu. The menu badge counts new notifications on your channel (subscribers, comments,
messages from the moderators, payouts). Today shows new videos from the channels you follow and, when you earn,
what you earned today and this month.

## In the Aira frame

Videos lives at `/videos`, with its own logo and tab icon, like the other apps.

* **+ New** (above the left menu): Upload a video, Upload a reel, Upload a short film (each opens Studio's upload form
  set to that kind, with the keyboard on the file chooser), New playlist, and for adults New brand deal (Earnings >
  Brand deals). A suspended channel is not offered the uploads. On a phone the page keeps its own Upload button.
* **Search** (the box across the top): as you type it suggests up to six matches: channels, videos, reels and short
  films everyone can find, your own videos only you can find (private, link only, scheduled) and your playlists;
  choosing one opens it. Enter shows every result on Home (`?q=`), with Everything / Videos / Reels / Short films,
  the channels, and your own private matches apart with a link to them in Studio.
* **Folders** (the left menu): Home, Reels, Short films, Library, Studio (with the count of new notifications),
  Analytics, Earnings (adults) and Moderation (the household's owner). Each opens `#/videos?tab=<id>` and names the
  page; on a wide screen they stand in for the page's own tab bar, which a phone keeps.

## Watching

* **Home**: from your subscriptions, Popular and New, with category buttons. Search with the box at the top of the
  page (see below).
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
  press Upload: it carries on from where it stopped. The details are checked **before** the file goes up (a time
  that has passed, "Only for my members" with memberships off, a reel's shape), and once the file is up, fixing a
  detail and pressing Upload again does not send it again. A finished file that never became part of a video (a tab
  closed before the last step) is removed after a day.
* **When it goes out**: Now, or **At a time I choose** (your own time, up to a year ahead). Until then the video is
  like a private one: only you can watch it, it is not listed, found or shared, and its page says when it goes out.
  It then appears in Home, New and your subscribers' Today with that time as its date. Under Edit you can move the
  time or empty it to send the video out now; a video that is out cannot be scheduled again. A time that has
  already passed (more than a few minutes ago) is refused with "That time has already passed", never published at
  once: a wrong day or AM for PM does not send out a video you meant to hold back.
* **Only for my members** (adults, shown only once memberships are on; not reels or videos made for children) and **Includes paid
  promotion** (a brand paid you for part of it: viewers see "Includes paid promotion" on the watch page and the
  public page). See Earning.
* **Your videos**: search them (title, description, tags), filter them (Everyone, Link only, Only me, Scheduled, For
  members, Paid promotion, Hidden or taken down) and **Download CSV** of what is shown. Edit a video's details, share
  it, or delete it (its files are removed).

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
adults, not by a child, not for members and already out) can also make a page **anyone can watch** without an account (`/p/<token>`); turn it off
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

Earnings has five parts: **Overview**, **Ads and payouts**, **Memberships**, **Brand deals** and **Support from
viewers**. No money moves through Aira except ad earnings: members, brands and supporters pay the creator directly,
and the creator marks each payment received.

### Overview: all your income

* **This month so far** (every way you earn), **Ads this month, on track for** (this month's ad earnings at the same
  daily pace: an estimate, not a promise), **Members** (how many, and what they bring a month; how many renew this
  month) and **Brand deals to be paid** (agreed or delivered, due by the month's end).
* **Ways to earn**: which of the four are on, with a button to set each up.
* **The last 12 months**: each month's ads, memberships, brand deals and support, and the total, with the **best**
  and **lowest** month and where most of the money comes from. Each currency is shown on its own; nothing is
  converted (so best, lowest and the top source show only when everything is in one currency).
* **Income log**: every payment, newest first (ads one line a day, marked estimated until the month closes), filtered
  by where it came from, and **Download CSV**.

### Memberships

* The creator (an adult who accepted the creator terms, with a payment page or UPI id saved under Support from
  viewers) sets a **monthly price** (1.00 to 1,000.00 in their currency) and **what members get**, and turns it on.
* Videos marked **Only for my members** are listed for everyone with a **For members** badge, but play only for
  members (and the creator). Anyone else sees the picture, the price, what members get and **Join**; they cannot play,
  like or comment on it, no ad is served before it, and it is never shared on a public page.
* An adult **joins for 1, 3, 6 or 12 months**: they are shown the amount, a reference (`MEM-...`), the creator's
  payment page and UPI id, and on a phone **Pay with a UPI app** (it fills in the UPI id, the reference and, in
  rupees, the amount). They can withdraw the payment from Library until it is answered; one payment waits at a time.
* The creator marks it **Received**: the member watches for 30 days a month, counted from today or, for a renewal,
  from when the membership would have ended. **Not received** declines it, or takes the months back if it was marked
  received by mistake (it asks first, since the member loses those months); a payment marked not received by mistake
  can still be marked **Received**. Support from viewers works the same way.
* **A month from members** (here and on the Overview) is what the active members paid, per month and in the
  currency they paid in, not today's price: changing the price later does not change it.
* Children and teens neither join nor sell memberships; a video for members stays locked for them. Turning
  memberships off keeps existing members until their time ends and keeps videos for members editable.
* Library shows **Your memberships** (until when, or a payment waiting); Today tells a member five days before theirs
  ends, and tells the creator when someone is paying to join.

### Brand deals

* A creator's own record of each sponsorship (adults): brand, who to talk to, amount and currency, the video it is
  for, a due date and notes, moving through **Proposed**, **Agreed**, **Delivered**, **Paid** or **Cancelled**.
* Linking a deal to a video marks that video **Includes paid promotion** for viewers. The label a deal turned on goes
  off again when the deal is moved to another video, unlinked, cancelled or deleted (unless another deal still links
  that video); a label you ticked yourself under Edit stays.
* **Mark paid** records the money; **Paid on** says the day it arrived (empty means today, never a day ahead), and
  the income overview, the payment log and the Business hub use that day. Moving a deal back from paid, or deleting
  it, takes the money out. The page shows what waits to be paid, what was paid this year and how
  many deals are **late**; search, filter by status and **Download CSV** (a note is never run as a formula). Today
  reminds the creator of deals due today or late.

### Ads and payouts

See [Ads](ads.md) for the advertiser's side. A creator turns earning on under **Earnings > Ads and payouts**:

* **Who can earn**: an adult (18 or older) who accepted the creator terms, with at least one public video, a channel
  that is not suspended, no copyright strikes and no video hidden by a moderator in the last 30 days.
* Ads then run on the channel's public and unlisted videos (a creator can turn ads off on one video under Edit).
* The page shows **Earned today**, **This month** (estimated), **Ready to be paid** and **Paid to you**, each video's
  views, ads shown, clicks, earnings and **RPM** (earnings per 1,000 views), the months and the payouts.
* Earnings are **estimated** until the month is closed (from the 2nd of the next month) after a check for invalid
  traffic, then **confirmed** and payable. **Ask to be paid** once at least the minimum ($10 unless the platform set
  another) is confirmed, with your bank or UPI details. The platform (hosted) or the computer's owner pays outside
  Aira and marks the payout paid with a reference; you are told.

### The Business hub (this computer)

Everyone in a household can open the account's Business hub, children included. So only the **household owner's**
earnings as a creator join its revenue (paid payouts, support received, membership payments received, paid brand
deals), and its lines never name anyone: "Support from a viewer (Videos)", "Channel membership (Videos)", "Brand
deal (Videos)". Who paid, and which brand, stay on the creator's own Earnings page. Another adult's earnings are their
own and are not in the hub (like a member's in the hosted product, where nothing a member earns is the
organization's revenue). Lines written by an earlier release are tidied the next time Earnings is opened.

## Where the data lives

Videos has one database for the whole server (`videos.sqlite3`, shared with Ads) next to the accounts database on
this computer, or in the data folder of the hosted product, and one media folder. People are identified by their
household profile (this computer) or their organization membership (hosted), never by email address.

Because it sits outside every organization's database, the hosted product handles it explicitly: a person's own
export carries `videos.json` (their channel, videos' details, comments, playlists, subscriptions, earnings and
payouts, support, membership plan and payments, memberships and brand deals; not the files); removing a member erases their channel, videos and files, comments, likes, history and
playlists; deleting an organization erases all of its members' channels and its ads. Money records stay for the
books without the person: impressions on their videos keep the amount advertisers paid, unpaid estimated earnings
end and open payout requests are declined. Tests point Videos at a scratch database and media folder
(`tests/conftest.py`), never the real ones.

## For developers

`aira/suite/videos/` (`db.py` the shared database, `store.py` its tables and `migrate` for columns added later
(`members_only`, `paid_promotion` on `vi_videos`), `media.py` uploads, type checks, the free-space reserve, ranges and
signed addresses, `service.py` the rules (who sees what, scheduling, the members-only lock), `creator.py` analytics,
support, the income overview and the CSVs, `members.py` memberships, `deals.py` brand deals, `videos_routes.py` the
routes), `auto_apply_assistant/views-videos.js`, `app-videos.css`, `public-videos.js`, `copyright.html` and
`videos-copyright.js`; tests in `tests/test_suite_videos*.py` (`_earning.py` and `_earning_ui.py` for memberships,
brand deals, income, scheduling and Studio's filters). A scheduled video's `created_at` is the moment it goes out, so
listing needs only `created_at <= now`. Routes added: `GET /income` (`format=csv`), `GET|PUT /memberships`,
`GET /memberships/mine`, `POST /memberships/<payment>` (received, not_received), `DELETE /memberships/payments/<id>`,
`POST /channels/<id>/join`, `GET|POST /deals` (`status`, `q`, `format=csv`), `PATCH|DELETE /deals/<id>`, and `q`,
`show` and `format=csv` on `GET /studio`. A `<video>` cannot send the person's headers, so the API hands out media addresses
signed for six hours (`/api/videos/media/<file>?e=&s=`), served in ranges of at most 4 MB (206 Partial Content) with
the file's type and `X-Content-Type-Options: nosniff`; the page's policy allows `media-src 'self' blob:` (a picked
file is previewed from a `blob:` address).
