# Ads

Advertise on Videos, and pay the creators whose videos your ads run on. Ads is a business app: in the hosted product
an organization's owners, admins and coaches run its campaigns (auditors read them; candidates and billing contacts
do not see the app); on this computer the account is the advertiser.

Ads spends the account's prepaid money, so on this computer only a grown-up's household profile runs it, and a
profile with a PIN must be unlocked first (every Ads route answers `profile_locked` until the PIN is typed). A child's
or a teen's profile is refused (`grown_up_only`: the screen says why and offers **Switch person**), even
though the account is the same.

## How the money moves

| Step | Who | What is recorded | What is charged |
| --- | --- | --- | --- |
| Add money (hosted) | an owner or billing contact | a top-up line on the ad balance, an `ads_topup` invoice row, `billing.ads_topup` in the organization's audit log | one invoice item at the organization's own billing provider (Stripe, or the stand-in), billed with its next invoice; the balance is credited as soon as the provider accepted it. A trial or an organization without a paid subscription cannot top up. |
| Add money (this computer) | the owner of the computer's first account | a payment line with a reference (cash, a transfer, a payment link) | nothing: Aira does not take payments; the owner records money they received |
| An ad counts | the viewer's browser and the server | an impression row: cost, the creator's share, the day and month | the cost is drawn from the balance at once |
| The month closes | automatically from the 2nd of the next month, or the platform | invalid traffic refunded, the creators' estimated earnings confirmed | refunds go back to the balances |
| A payout | the creator asks; the platform (hosted) or the computer's owner pays | the payout, its reference and who marked it paid | paid outside Aira (bank transfer, UPI); Aira only records it |

Aira never takes card details. The platform keeps what is left of each charge after the creator's share (55% to the
creator unless the platform sets another, 0 to 90%).

## Campaigns

**New campaign**: a name, what you pay for, your bid, a total and a daily budget, optional start and end dates, your
page (https only), and who sees it:

* **Views** are bought per 1,000 impressions (CPM, $0.50 to $100); **clicks** per click (CPC, $0.05 to $50).
* **Budgets**: at least $5 in total and $1 a day; the daily budget is spent evenly through the day (at most an hour
  ahead of the clock). A campaign ends when its total is spent or its end date passes.
* **Targeting**: video categories, the video's language, the viewer's country (from their channel). Empty means all.
* **Frequency cap**: the most times one person sees the campaign in a day (3 unless you choose up to 20).
* **Family-safe**: only family-safe campaigns run for teens and on videos made for children; the reviewer checks the
  claim. **Nobody in the child age group is ever shown an ad.** Videos rated for adults get only campaigns that
  allow them.

* **Tracking tags**: on unless you turn them off. A click goes to your page with `utm_source=aira`,
  `utm_medium=video_ad`, `utm_campaign=<the campaign's name>` and `utm_content=<preroll, banner or reel>` added (tags
  your link already has are kept), so your shop or website analytics shows which visits and orders came from which
  campaign.

Start, pause, edit, copy, end or delete a campaign from its page. Without money it waits ("Paused: the balance is used
up") and starts by itself after a top-up. **Start** appears once the campaign has an ad.

* **Editing a running campaign** keeps everything you did not change (an edit may send just the name or the budget).
  A new page address, or newly saying the campaign is family-safe, sends its ads back to review: reviewers approved
  them for the old page and audience. Ads already shown keep sending their clicks to the page they were reviewed
  for, however long ago they were shown: a click link never leads to a page nobody reviewed. Lowering the total
  budget below what is spent ends the campaign.
* **Start again**: a campaign that ended by itself (its total budget was spent, or its end date passed) can be edited
  and started again: raise the budget or move the end date (or clear it), then **Start again**. A campaign you ended
  yourself stays ended; copy it instead. An end date in the past is refused.
* **Copy** makes a draft with the same settings and the same ads (they keep their review, since the page and the
  audience are the same), with nothing spent. A start date still to come is kept (with or without an end date); dates
  already passed are dropped. Changing the copy's video or picture leaves the original's file alone.
* **Delete** removes the campaign, its ads and the sales recorded for it, so they no longer count in Reports.

The **Campaigns** list has a search (name or page) and a status filter, and **Download CSV** (every campaign with its
settings, spend, impressions, clicks, orders, sales and return on ad spend).

## What to expect (the forecast)

The campaign form shows, while you fill it in, what the campaign can expect each day; a campaign's page shows the same
for its current settings:

* views a day on matching videos in the last week (videos that can carry ads, on channels that earn, by people the
  campaign may be shown to: never children, teens only for family-safe campaigns, adult-rated videos only when
  allowed, never the creator's own views);
* how often your ad would win against the other advertisers bidding for those views now (other campaigns that are
  running, approved and have money, whose targeting overlaps yours), and so how many times a day it would show;
* clicks a day (at the click rate of every ad in the last 30 days, smoothed towards 1% while there are few), the cost
  a day, how long the total budget and your balance last, and what creators earn from it;
* when the daily budget is what limits it, it says so;
* **the bid that would win most often**, with **Use this bid**. Other advertisers' bids are private: the forecast
  compares yours with theirs rounded up to a coarse step (50 cents per 1,000 up to $10, $1 up to $50, $5 above), so
  the suggestion is the step just above the highest competing bid (or the lowest bid when nobody competes) and never
  shows anyone's exact bid.

It says how sure it is (a rough, fair or good guess, from how much viewing there was). Nothing is promised: real
results move with what people watch. Half-typed targeting (like "I" on the way to "IN"), or a bid that is empty,
not a number, or outside the range, shows what to fix instead of a forecast.

## Sales and the return on ad spend

Aira does not see your shop, so you record the sales a campaign brought on its page (**Record sales**: the day, the
number of orders, what they were worth in US dollars, a note); the tracking tags make them easy to find in your
analytics. A sale cannot be in the future or before the campaign was made. The campaign page and **Reports** then
show orders, sales, **sales for each $1 spent** (the return on ad spend), the cost of one order and what is left
after the ads. Reports list campaigns best return first. Remove a line you recorded by mistake. Each recorded sale is
in the Business hub's activity.

## Ads (creatives)

Each campaign holds up to 20 ads, uploaded like videos (resumable parts):

* **Video before a video**: up to 3 minutes; viewers can skip it after 5 seconds; a button with your call to action.
* **Banner on the watch page**: a JPEG, PNG or WebP picture with a headline, text and button.
* **Sponsored reel**: a tall video of up to a minute, shown between reels.

A file of the wrong kind (a video chosen for a banner, say) is refused as soon as its first part arrives
(`422 wrong_file`), so the message appears at once.

Every ad is **reviewed before it runs** (hosted: Aira's operators in the console; this computer: its owner). A
change to the text or the file sends it for review again. A rejected ad shows the reviewer's note.

## Which ad is shown, and what counts

For each slot of each playback, the eligible ads are approved ones in active campaigns that are in their dates,
match the targeting, have balance and budget left, are within today's pace and have not reached their frequency cap
for this viewer today, on a video of a channel that earns. The **highest effective bid** wins: a CPM bid as it is; a
CPC bid times the campaign's click rate so far (smoothed: `(clicks + 1) / (impressions + 50)`) times 1,000. Ties are
broken at random. Advertisers pay their own bid (first price).

An impression **counts** when the ad played, or was on screen, for 2 seconds (the browser reports it and the server
checks that 2 seconds passed since it was served, within 30 minutes). **It is not counted, not charged and earns
nothing** when the viewer is in the advertiser's own account, in the creator's own account (their own videos),
past the frequency cap, or past 60 counted impressions an hour from one viewer. A **click** goes through a signed
redirect (`/api/videos/ads/go/<impression>?s=`); a CPC click is charged once per impression and once per viewer per
campaign a day, and only after the impression counted. At the month's close, more than 20 counted impressions from
one viewer to one channel in a day are refunded. A **clawback** (the platform, or the computer's owner) turns a
channel's counted impressions for a month invalid: advertisers are refunded and the earnings removed.

## Reports

Impressions, clicks, click rate (CTR), spend and effective cost per 1,000 impressions (eCPM), and the sales you
recorded: per campaign, per ad and per day for the last 7, 30 or 90 days, for every campaign or one, a spend chart,
the ads that were shown but not charged and why, and **Download CSV** (per day, with orders and sales). The
Business hub shows the balance and how long it lasts, this month's spend, impressions and clicks, and a setup
checklist.

## The balance

The Overview and Balance tabs say how long the balance lasts at the last 7 days' spending. When it is less than 3
days, the Overview's "What needs doing" says so and the hub's balance card turns amber. The Overview also asks for
your business name when it is not set (viewers see it on your ads).

## The superadmin (hosted)

The platform console's **Creators & ads** page (`#/platform/creators`): gross ad revenue, creator earnings and
platform revenue, estimated earnings, payouts waiting and paid, refunds and advertisers' balances; the ad review
queue with each ad's media and landing page (support and platform admins approve or reject); and, for platform
admins only, the creators' share and the minimum payout, payout requests (mark paid with the reference, or decline
with a reason), closing a month and clawbacks. Every action is in the platform audit log (`ads.review_*`,
`ads.settings`, `ads.payout_*`, `ads.month_close`, `ads.clawback`). On this computer the same work is the **Admin**
tab of Ads, for the owner of the computer's first account, from an unlocked profile; the hosted build refuses those routes
(`403 not_available`).

## Limits and what is not built

* Ad money is in US dollars, whatever currency the business profile uses.
* Public video pages (`/p/<token>`) show no ads, so their views earn nothing.
* No automated payouts: payments are made outside Aira and recorded.
* Targeting is by category, language and country only; there is no profiling of viewers.
* Sales are recorded by the advertiser (no conversion pixel); the forecast is an estimate from last week.
* Invalid-traffic rules are the simple ones above (no device fingerprinting or bot detection beyond them).

## In the Aira frame

Ads lives at `/ads` with its own logo, the search box across the top, **+ New** above its menu and the launcher.

* **+ New** offers **New campaign** (the campaign form with its forecast), **New ad** (the open campaign's *Add an
  ad* form; from anywhere else it first asks which campaign, and offers to make one when there is none), **Record
  sales** (the open campaign's sales form, or asks which campaign) and, for the computer's owner, **Record a
  payment** (Admin), or in the hosted build, for owners and billing contacts, **Top up the balance**. A child's or a
  teen's profile and a read-only hosted role (an auditor) get no **+ New**, and the page hides its create and edit
  buttons for them too. On a phone, where the menu is folded away, the page keeps its own **New campaign** button.
* **Search** ("Search campaigns and ads") suggests campaigns (by name, page address or category) and ads (by
  headline, text or button) as you type; a campaign opens its page, an ad opens its campaign's page with the ad
  shown and focused. **Enter** opens Campaigns with the words in its own search: the matching campaigns and an
  *Ads that match* list. The ads come from `GET /api/ads/creatives` (every ad of the advertiser's campaigns with its
  campaign's name, without media).
* **Menu folders**: Overview, Campaigns, Reports, Balance and, for the computer's owner, Admin; each opens that tab
  (`#/ads?tab=<id>`). The page is named after the open folder, or after the campaign on a campaign's page.

## For developers

`aira/suite/ads/` (`store.py` the tables in the shared Videos database, money in micros of a dollar: new things go in
new tables, `ad_campaign_options` (tracking tags), `ad_sales` and `ad_campaign_pages` (the pages a campaign sent
clicks to before its page changed, and until when, so an old click goes to the page of its time), because the Videos database runs the statements on
every first connection and cannot add columns; `service.py`
the rules; `ads_routes.py` the advertiser's routes and the local admin routes with `HOSTED_RULES` "deny"); serving,
counting and clicks are under `/api/videos/ads/*` (`aira/suite/videos/videos_routes.py`) so every Videos member can
see ads without their organization having Ads; hosted money and moderation routes in `aira_cloud/routes/creators.py`;
the top-up in `aira_cloud/billing.py` (`charge_ads_topup`). UI: `views-ads.js`, the console pages in
`cloud-platform.js`. Advertiser routes besides campaigns and ads: `POST /api/ads/estimate` (a read in the hosted
build, so auditors may run it), `POST /api/ads/campaigns/<id>/duplicate`, `GET|POST /api/ads/campaigns/<id>/sales`,
`DELETE /api/ads/sales/<id>`, `GET /api/ads/creatives` (the search box's list), `GET /api/ads/campaigns?format=csv`, `GET /api/ads/reports?campaign=<id>&format=csv`.
Styles: `app-ads.css` (scoped to `ads-*` classes). Tests: `tests/test_suite_ads.py`, `tests/test_suite_ads_more.py`, `tests/test_suite_ads_fixes.py`,
`tests/test_suite_ads_search.py`, `tests/test_suite_ads_shell_ui.py` (the frame, in a browser), `tests/test_suite_videos_ui.py`.
