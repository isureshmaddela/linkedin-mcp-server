"""Health (app id ``care``): medicines with reminders and refills, caregivers, appointments, readings, a mood diary,
a report for the doctor, an emergency card, and care work (earning by looking after people), for one person at a
time (aira.suite.life).

The id is ``care`` because ``/api/health`` is the server's own health check. Everything here belongs to one
person; another person sees it only if the owner named them a caregiver (read-only). Nothing here is medical
advice: range hints are general and say so.

* ``store`` - tables (``cr_*``) and validation.
* ``service`` - the household time zone, schedules, dose slots and their state, adherence, stock, supply and
  refills, caregivers, missed-dose notices, readings and their hints, the diary, appointments (and their calendar
  files) and the emergency card.
* ``report`` - the report for a doctor's visit and the link that stops working after two weeks.
* ``work`` - care work: clients, visits, statements a client opens and pays, money received in the revenue ledger.
* ``search`` - the search box across the top: one person's records that match, in one request.
* ``care_routes`` - ``/api/care/*``, the public pages (``emergency_card``, ``health_report``, ``care_statement``),
  Today cards and the pulse.
"""
