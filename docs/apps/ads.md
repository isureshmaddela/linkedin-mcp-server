# Ads

Advertise on Videos, and pay the creators whose videos your ads run on. Ads is a business app: in the hosted product
an organization's owners, admins and coaches run its campaigns (auditors read them; candidates and billing contacts
do not see the app); on this computer the account is the advertiser.

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

Start, pause, edit, end or delete a campaign from its page. Without money it waits ("Paused: the balance is used
up") and starts by itself after a top-up.

## Ads (creatives)

Each campaign holds up to 20 ads, uploaded like videos (resumable parts):

* **Video before a video**: up to 3 minutes; viewers can skip it after 5 seconds; a button with your call to action.
* **Banner on the watch page**: a JPEG, PNG or WebP picture with a headline, text and button.
* **Sponsored reel**: a tall video of up to a minute, shown between reels.

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

Impressions, clicks, click rate (CTR), spend and effective cost per 1,000 impressions (eCPM): per ad and per day for
the last 7, 30 or 90 days, a spend chart, the ads that were shown but not charged and why, and **Download CSV**. The
Business hub shows the balance, this month's spend, impressions and clicks, and a setup checklist.

## The superadmin (hosted)

The platform console's **Creators & ads** page (`#/platform/creators`): gross ad revenue, creator earnings and
platform revenue, estimated earnings, payouts waiting and paid, refunds and advertisers' balances; the ad review
queue with each ad's media and landing page (support and platform admins approve or reject); and, for platform
admins only, the creators' share and the minimum payout, payout requests (mark paid with the reference, or decline
with a reason), closing a month and clawbacks. Every action is in the platform audit log (`ads.review_*`,
`ads.settings`, `ads.payout_*`, `ads.month_close`, `ads.clawback`). On this computer the same work is the **Admin**
tab of Ads, for the owner of the computer's first account; the hosted build refuses those routes
(`403 not_available`).

## Limits and what is not built

* Ad money is in US dollars, whatever currency the business profile uses.
* Public video pages (`/p/<token>`) show no ads, so their views earn nothing.
* No automated payouts: payments are made outside Aira and recorded.
* Targeting is by category, language and country only; there is no profiling of viewers.
* Invalid-traffic rules are the simple ones above (no device fingerprinting or bot detection beyond them).

## For developers

`aira/suite/ads/` (`store.py` the tables in the shared Videos database, money in micros of a dollar; `service.py`
the rules; `ads_routes.py` the advertiser's routes and the local admin routes with `HOSTED_RULES` "deny"); serving,
counting and clicks are under `/api/videos/ads/*` (`aira/suite/videos/videos_routes.py`) so every Videos member can
see ads without their organization having Ads; hosted money and moderation routes in `aira_cloud/routes/creators.py`;
the top-up in `aira_cloud/billing.py` (`charge_ads_topup`). UI: `views-ads.js`, the console pages in
`cloud-platform.js`. Tests: `tests/test_suite_ads.py`, `tests/test_suite_videos_ui.py`.
