"""Marketing: grow an audience and turn it into customers.

The main objects:

* **Posts** on a content calendar, per channel (Instagram, Facebook, LinkedIn, X, TikTok, YouTube, email, blog,
  WhatsApp), drafted by the writing model within each channel's length limit, repurposed across channels and
  planned 30 days at a time.
* **Campaigns** with a goal, dates, budget and spend, UTM tags and results (clicks, leads, subscribers, revenue
  entered by the business) with ROI and cost per lead.
* **Tracked links**: a public ``/p/<token>`` link that counts a click per visit and sends the visitor on to the
  business's own https destination with UTM tags.
* **Lead-capture forms** with a public page, a submissions inbox and an optional sign-up to the list.
* A **link-in-bio** page (one per business) with click counts per link.
* **Subscribers** (import, export, tags, unsubscribe pages) and **newsletters** queued to the shared outbox,
  one message per subscribed address, each with its own unsubscribe link.
"""
