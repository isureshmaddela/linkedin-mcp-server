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

from aira.suite import public as _public


def _keep_installed_public_index() -> None:
    """Work around a shared bug until aira/suite/public.py is fixed (see docs/apps/experiences.md).

    The hosted build installs its organization index with ``public.set_index`` (aira_cloud.hooks.install), and the
    single-user module (auto_apply_assistant) later calls ``public.set_default_index`` when the gateway imports it.
    That call also made its index current, so on a hosted server started the normal way every new public link was
    indexed in the local accounts database and answered 404. Here ``set_default_index`` still changes the default
    but keeps an index that was installed over the old default, which is what ``set_default_index`` should do.
    """
    original = _public.set_default_index
    if getattr(original, "keeps_installed_index", False):
        return

    def set_default_index(index: _public.Index) -> None:
        installed = _public._index
        overridden = installed is not _public._default_index
        original(index)
        if overridden:
            _public._index = installed

    setattr(set_default_index, "keeps_installed_index", True)  # noqa: B010
    setattr(_public, "set_default_index", set_default_index)  # noqa: B010


_keep_installed_public_index()
