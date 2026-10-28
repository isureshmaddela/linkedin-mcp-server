# Academy

Sell courses and teach: for educators, coaching institutes, tutors and course creators. Open it from the app
switcher (or `g` then `4`). In the hosted product an organization turns it on under **Apps**; owners, admins and
coaches manage it, auditors can read it.

## Build a course

1. **Courses > New course**: title, subtitle, description, level, price (0 for free), a cover image link, and
   optionally an **enrolment code** (students must type it: useful for a class or a partner) and a number of
   **seats**.
   Or **Draft with AI**: give a topic and Aira drafts modules and lessons (a template when no writing model runs).
2. In the course, add **modules** and **lessons**: text (blank lines start a paragraph), a video link, minutes and
   a **free preview** flag (anyone can read a preview on the course page). Move modules and lessons with the arrows.
3. Add a **quiz** to any lesson: multiple choice, 2 to 6 options, one right answer, an explanation and a pass
   mark. **Draft from the lesson** writes questions from the lesson text. Quizzes are graded on the server: the
   right options are never sent to the student's browser, and are shown only once the quiz is passed.
4. **Publish**: the **course page** link is made at once; share it. (Courses lists the page of every published
   course; search the list and filter it by status.)

## Students

* On the course page a student enrols with a name and email (and the code, and a cohort when there are open ones).
  A free course starts at once. A paid course waits for payment: the student sees your payment link and
  instructions (Business hub > Business profile) and a reference. Aira does not take card payments.
* Every enrolment has its **own link**, emailed to the student: it is their access to the lessons, progress and
  quizzes. If someone enrols again with the same email, the link is emailed again (it is never shown to whoever
  typed the email).
* **Students** lists everyone with progress, status and last activity; filter, search and download CSV. Open a
  student to **mark paid** (the payment is recorded once in Revenue), **refund** (lessons lock, the payment stops
  counting), move them to a cohort, email their link again, or turn their link off.
* Enrolment is checked and saved in one step, so two people can never take the last seat of a course or a cohort.
  A cohort that has already started is not offered on the course page and cannot be picked there (you can still
  place someone in it by hand).
* A student who completes every lesson and passes every quiz gets a **certificate** with a code and its own
  verification page (printable). Look a code up with `GET /api/academy/certificates?code=...`.

## Keep students engaged

* **Announcements** go to a course's students (or one cohort) through the outbox.
* **Remind inactive students** (Overview) queues a friendly nudge for active students with no activity for 7 days,
  at most once a week each.
* Emails: `enrolment` (with the link and how to pay), `payment_receipt`, `course_completed` (with the
  certificate), `announcement`, `nudge`. They wait for your approval in **Business hub > Outbox** unless you turn on
  automatic sending for that kind under **Email**.

## Reviews, partners and earnings

Students rate the course from their own page after their first lesson; the course page shows the average and the
latest reviews, and **Reviews** lets you hide one. **Partners** earn a commission on the students they send
(a partner code also works in **Enrol a student**), and **Earnings** ranks every course and event from the top
earner to the lowest. See [experiences.md](experiences.md).

## Numbers

Overview shows students, active this week, completion rate, revenue this month, payments waiting, the average
rating and the top courses, with a setup checklist and your catalog page. The Business hub shows the same headline numbers next to your other apps.

## API

Everything the screen does is under `/api/academy/*` (courses, modules, lessons, quizzes, outline and quiz drafts,
students, cohorts, announcements, nudges, overview). Public pages: `academy/course` (action `enrol`, with an optional
partner `ref`), `academy/student` (actions `complete`, `quiz`, `review`), `academy/certificate`, `academy/catalog`
and `academy/partner`.
