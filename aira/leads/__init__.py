"""Aira Leads: find, qualify, enrich and track sales leads.

Sources (each one a separate, honest search):

* **Google Maps** (API ``google_maps``): local businesses with phone, website, address and ratings.
* **Google web** (API ``google``): people and companies found through ``site:linkedin.com`` queries.
* **Hiring signals** (API ``google_jobs``): companies that are hiring for a role you sell into.
* **LinkedIn Sales Navigator**: a search the person built in their own Sales Navigator, read in their own
  signed-in browser (read only; see ``linkedin_mcp_server``).
* **CSV import** and manual entry.

Nothing here sends a message, connects, or invites anyone: Leads finds and organizes. Contact details are
only what a source or a company's own public website shows, and each one records where it came from.
"""
