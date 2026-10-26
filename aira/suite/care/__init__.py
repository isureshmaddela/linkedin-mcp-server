"""Health (app id ``care``): medicines with reminders, caregivers, appointments, readings, a mood diary and an
emergency card, for one person at a time (aira.suite.life).

The id is ``care`` because ``/api/health`` is the server's own health check. Everything here belongs to one
person; another person sees it only if the owner named them a caregiver (read-only). Nothing here is medical
advice: range hints are general and say so.

* ``store`` - tables (``cr_*``) and validation.
* ``service`` - schedules, dose slots and their state, adherence, stock, caregivers, missed-dose notices,
  readings and their hints, the diary, appointments and the emergency card.
* ``care_routes`` - ``/api/care/*``, the ``emergency_card`` public page, Today cards and the pulse.
"""
