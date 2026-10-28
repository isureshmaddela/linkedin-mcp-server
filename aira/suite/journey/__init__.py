"""Life (``#/journey``): one person's goals, the story of their life and the papers that matter.

* **Goals** with a category, a target date and steps; progress comes from the steps (or is set by hand), every
  update is a check-in, and finishing a goal adds it to the timeline.
* **Timeline**: the milestones of a life (school, work, moves, family, health, travel, achievements), private or
  shared with the household.
* **Document vault**: passports, certificates, insurance cards and the like, with issue and expiry dates, the
  document number and a scan, kept encrypted; reminders before something expires.
* **Overview**: active goals, a life score (the average progress of active goals), what is coming up.
* **Habits** with streaks (a habit can add an amount to a goal), and a private **journal**: a mood and a few lines a
  day, with "on this day" memories from earlier years.
* A goal's **public page**: friends cheer, and (adults) **supporters** back it with an amount paid to the owner
  directly; the owner marks each pledge received, which adds it to a money goal.

Everything belongs to the person who wrote it (:func:`aira.suite.life.current`); a shared timeline event is the
only thing other people in the household may read.
"""
