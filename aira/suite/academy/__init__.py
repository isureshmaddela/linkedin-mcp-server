"""Academy: sell courses and teach (educators, coaching institutes, course creators).

Main objects:

* **Courses** (``ac_courses``) with a price (0 = free), an optional enrolment code and capacity, published to a
  public course page customers enrol from.
* **Modules and lessons** (``ac_modules``, ``ac_lessons``), ordered, with text, an optional video and a
  free-preview flag; a lesson may carry a **quiz** (``ac_questions``) graded on the server.
* **Enrolments** (``ac_enrolments``): one student in one course, with its own public link (the student's access),
  progress (``ac_progress``), quiz attempts (``ac_attempts``) and, once everything is complete and every quiz
  passed, a **certificate** anyone can verify from its own link.
* **Cohorts** (``ac_cohorts``) group students who start together; **announcements** (``ac_announcements``) go
  to a course's students through the shared outbox.

Money is recorded in the suite's revenue ledger when the business marks an enrolment paid (customers pay
through the business's own payment link); a refund voids it.
"""
