"""Service: help customers fast (a helpdesk, like a service cloud).

Cases come from a contact form in the public help centre, from live chat, by email or are entered by hand. Each
case has a priority, due times for the first reply and the solution (SLA, optionally counted in business hours),
a status (new, open, waiting on the customer, solved, closed), an assignee and tags. Replies to the customer go out
through the shared outbox with a link to the case's own page, where the customer replies and, once solved, rates
the help (CSAT); internal notes never leave the business. Saved replies and an AI-suggested reply speed answers
up; the help centre lets customers find answers themselves.

Modules: :mod:`store` (tables ``sv_*``, settings), :mod:`desk` (the rules), :mod:`service_routes` (HTTP,
public pages ``help_center``, ``chat`` and ``case``, the background task, hub checklist and summary).
"""
