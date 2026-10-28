"""Social: Aira's own social network and messenger, safe for every age.

* **Profile** - a unique @name, a display name, an avatar and a bio, public or private, plus a professional
  profile (headline, experience, skills, open to work).
* **Friends** - friend requests (accept or decline), following public people and pages, blocking (both ways)
  and reporting.
* **Feed** - posts with up to six photos for the public, friends or only me (or a group or page), reactions,
  comments, shares, #tags and @mentions; stories that last 24 hours.
* **Chats** - one-to-one and group chats with photos, read receipts, unread counts, muting and delete for
  everyone within an hour; live through ``since`` polling.
* **Groups** and **Pages** - public or private groups with join requests and admins; pages that people follow,
  linked to a business's public pages.
* **Marketplace** (:mod:`.market`) - adults sell to friends and neighbours; buyers search, save and message the
  seller; a listing is the seller's own (their payment link, their earnings) unless someone who runs the
  account's business sells it for the business (its payment link; the sale goes in the Business hub ledger);
  a listing can be shared as a public page.
* **Notifications** and **Moderation** - a badge on the menu, alerts for new messages, and a queue of reports.

Unlike the other apps, Social keeps its data in one database for the whole platform (:mod:`.db`), so people in
different households (and different organizations, hosted) can befriend and message each other. The person is
:func:`aira.suite.life.current`; their Social account is keyed by the hosted member or the household profile.
Children are private, cannot be found by strangers and receive messages only from friends; a child's new friend
from outside their household waits for a grown-up of the household to say yes.
"""
