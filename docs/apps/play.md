# Play

Games, quizzes and things to enjoy together, for every age. Play is a life app: each person in the household plays
as themselves (their own profile locally, their own sign-in hosted), and the family shares leaderboards, challenges,
live games and a watch list. Grown-ups can also earn with it by running paid quiz nights. Open it from the app
switcher or the Play tile on **Today**.

Tabs: **Games**, **Daily challenge**, **Quizzes**, **Live trivia**, **Scores**, **Watch and read**. Pressing the tab
you are on (for example **Games** in the middle of a game) takes you back to its start.

## In the Aira frame

Play lives at `/play`, with its own name and icon, like the other Aira apps.

* **+ New** (above the left menu) offers **Write a quiz** (the quiz editor), **Host live trivia** (the host form),
  **Run a paid quiz night** (the same form with the entry fee first; grown-ups only, like the fee) and **Add to the
  watch list**. Each opens the tab's own form; the address then forgets it, so Back or a reload does not open it
  again.
* **Search** across the top suggests, as you type, games (name or tagline), the quizzes you may play (title, what it
  is about, who wrote it; a private quiz is never shown to anyone else), the watch list (title or maker) and live
  games (title, room code or host), up to six, one of each kind in turn. Choosing one opens it: a game on its level
  and mode, a quiz ready to play, a watch-list entry outlined on the list, a live game's room. **Enter** shows every
  match by kind (`#/play?tab=search&q=...`), with a link to the matching watch list.
* **The left menu** shows the six tabs as folders with their own icons; the page is named after the open one.
  Pressing the folder you are on goes back to its start, like the tab.

## Games

| Game | Levels | How it works |
| --- | --- | --- |
| Memory match | 6, 8 or 12 pairs | Turn two cards at a time. The server shows each card as it is turned, so the board cannot be read in advance. Fewer moves score more. |
| Word scramble | short words (children), everyday, longer | Eight scrambled words; type them all, then check. |
| Quick maths | to 10, to 100 and times tables, bigger sums and division | Ten sums. |
| Trivia | children, easy, mixed, hard | Ten questions from a built-in book of 126 questions in seven subjects. **Previous question** lets you change an answer before the end. |
| Sudoku | 4 by 4 (children), 9 by 9 easy and medium | Every puzzle has exactly one answer. **Check** tells you how many squares to fix and lets you carry on; **Hint** fills one square (it costs 50 points). |
| Word of the day | today's word, a practice word | Six tries to guess a five-letter word. Green is the right place, yellow is in the word, grey is not in it; screen readers hear the same in words. Today's word is one try, the same word for everyone. |

**The right level to start.** Each game opens on a level that suits the person: the children's level for children,
the easiest for seniors, the middle one for everyone else (a grown-up never starts on the children's questions).

**Relaxed or timed.** Relaxed mode has no clock and is the default for children and seniors. Timed mode adds a
speed bonus, measured by the server from when the game started.

**Results that teach.** After trivia, a quiz, maths or a scramble, every question is listed with the right answer,
and what you chose when it was not right ("the answer is Canberra (you chose Melbourne)").

**Fair scores.** Every puzzle is made on the server from a seed, and the browser only sends moves and answers: a
score sent by a browser is ignored, answers that do not fit the puzzle are refused, a finished game cannot be
finished again, and memory cannot be finished without finding every pair.

## Challenges: beat my score

After a game, **Challenge someone** sends the very same puzzle (same letters, sums, cards or questions) to people in
the household. They see it on **Today**, as a pop-up and at the top of **Games**, with **Take it on** (or **Not
now**). When they finish, both see who won ("You beat Pat's 540!"), the challenger gets a pop-up, and the result is
listed under **Challenges**. Today's challenge and today's word cannot be used (everyone already has them), and a
quiz must be shared with the family first.

## Quizzes people write

**Write a quiz**: a name, what it is about, and three to thirty questions, each with two to four answers and a tick
on the right one. **Suggest questions** fills in questions on a topic: from the writing model when it runs (check
the facts), otherwise from the built-in quiz book. Quizzes are shared with the family unless you untick it.

* **Play** it alone (relaxed or timed), checked on the server like every game. The answers never reach anyone but
  the writer; the writer's own games do not count on the quiz's **Family board** or the family leaderboard, and
  are never announced on anyone's **Today** or in the activity feed. A private quiz is never named to anyone else.
* **Host it live** to play it together, or as a paid quiz night.
* Whoever wrote it, or a grown-up (for a shared quiz), can edit or delete it. Making a shared quiz private (or
  deleting it) also withdraws the challenges still waiting on it.

## Daily challenge

One puzzle for everyone each day, without a clock: trivia on Monday, maths on Tuesday, scramble on Wednesday, memory
on Thursday, sudoku on Friday, trivia on Saturday and memory on Sunday (the day follows the business time zone).
Each person gets one try; starting again carries on an unfinished try (Today says "carry on"). The family board
ranks today's scores, and playing any game each day builds a streak.

## Live family trivia

1. **Host live trivia**: choose questions from the quiz book (a subject) or from a family quiz, the number of
   questions and the seconds per question (45 is relaxed). Leave the number empty to play a family quiz in full, or
   8 from the quiz book (the quiz book gives up to 15).
2. Others in the household join from **Live trivia** (or the invitation that pops up for them) or with the
   four-letter code. To invite relatives without an account, the host makes a link (children cannot); relatives
   type their name to join (one phone can add up to 4 names, and a room takes up to 24 guests, so the family always
   gets in). Every room has its own address (`#/play?tab=live&room=...`); someone in the household who opens it
   before joining sees **Join this game**.
   Whoever wrote the quiz a room plays is its **quizmaster**: they know the answers, so they can run the game but
   cannot answer, and they are never named the winner.
3. The host presses **Start** (or **Cancel the game** in the waiting room). Each question runs on the server's clock;
   an answer after the time is refused, a right answer scores 100 plus up to 50 for speed, and the right answer is
   shown for six seconds before the next question (or the host presses **Next question**).
4. Scores update live for everyone. A winner is named when someone scored. Rooms close three hours after they open.

## Quiz nights: earn with Play

A grown-up can run a quiz for a club, a café, a school or a fundraiser and take an entry fee:

1. When hosting, set **Entry fee for guests** (and a **Prize** if there is one). Household members always play free;
   children cannot charge.
2. Share the link. Guests see the fee and prize before joining, and after joining they see how to pay you: the
   business's own payment link and instructions (Business hub > business profile) and a reference such as
   `YACY-B9B313`. Aira does not take card payments.
3. When a guest has paid, press **Mark $4.50 paid** next to their name (**Undo** takes it back). Until then their
   answers do not count. Each payment is recorded once as money received (`suite_revenue`, source `quiz_entry`) and
   shows in the Business hub; undoing it voids it and takes away the points the guest scored while marked paid.
4. The room shows what was taken; **Download the entries** gives a spreadsheet of who played, who paid and their
   scores. **Quiz nights** on the Live trivia tab lists every paid night with its takings.

## Watch and read

Films, shows, books, podcasts and games the family wants to enjoy: who recommended them, a link, each person's own
status (want to, in progress, finished) and rating, and the family's average rating. **Search**, filter by status and
kind, **Edit** a title, **Download the list (CSV)**. **Pick for tonight** suggests something at random from what you
want to enjoy and nobody has finished (better-rated things come up more), with **Plan it together**. **Plan to enjoy
it together** picks a time (not in the past; a couple of minutes' grace for "now") and who is coming; the plan shows on those people's **Today**. Anyone
can add things; whoever added an item, or an adult, can change or remove it.

## Scores

Personal bests by game and level, the play streak, the family leaderboard (by game, or family quizzes), recent games,
and **Download all my games (CSV)**.

## Today and reminders

Play's card on Today shows today's challenge (or your score, or "carry on"), challenges waiting for you, today's
watch-together plans, a new best someone in the family just set, and live games you can join with one tap. The badge
on Play counts live games to join and challenges waiting. Pop-ups: someone opened a live game, someone challenged
you, someone took your challenge.

## One account, hosted or at home

Locally every household profile plays as themselves. In the hosted build the household is the organization: the app
answers `402 app_required` until the organization has Play; every role (owner, admin, coach, candidate, billing,
auditor) can play, write quizzes and host; a quiz kept private is seen by its writer only; organizations never see
each other's games, quizzes or quiz nights.

## API

`/api/play/games`, `/sessions` (start: `{game, level, mode}`, `{daily: true}` or `{quiz, mode}`), `/sessions/{id}`
(resume), `/sessions/{id}/move` (memory `flip`, word `guess`, sudoku `hint`), `/sessions/{id}/finish`, `/daily`,
`/scores?game=` (`&format=csv`), `/challenges` (GET list, POST `{session_id, to: [profile ids]}`),
`/challenges/{id}/play` (POST), `/challenges/{id}` (DELETE: not now), `/quizzes` (list, POST),
`/quizzes/draft` (POST `{topic, count}`, saves nothing), `/quizzes/{id}` (GET with answers for whoever may edit,
PATCH, DELETE), `/quizzes/{id}/board`, `/rooms` (list, host: `title, category, quiz, questions, seconds,
auto_advance, entry_cents, prize`), `/rooms/join` (by code), `/rooms/{id}` (state; `?since=` the last `version`),
`/rooms/{id}/join|start|next|end|answer`, `/rooms/{id}/players/{player}/paid` (POST `{paid}`, host),
`/rooms/{id}/entries` (host; `?format=csv`), `/nights`, `/rooms/{id}/link` (POST, DELETE), `/watch` (list with
`kind`, `status`, `q`, `format=csv`; add), `/watch/pick?kind=&together=1`, `/watch/{id}` (PATCH, DELETE),
`/watch/{id}/status`, `/watch/{id}/plans`, `/watch/plans/{id}` (DELETE), `/search?q=&limit=` (games, quizzes,
watch and rooms that match; up to `limit`, 1 to 50, of each). Public page kind `trivia_room` with actions
`join`, `state` and `answer`.

Code: `aira/suite/play/` (`games.py` puzzles and scoring, `quizzes.py` quizzes people write, `service.py` sessions,
challenges, rooms, quiz nights and the watch list, `store.py` tables `pl_*`, `play_routes.py`),
`auto_apply_assistant/views-play.js`, `public-play.js`, `app-play.css`. Tests: `tests/test_suite_play.py`,
`tests/test_suite_play_extras.py`, `tests/test_suite_play_search.py`, `tests/test_suite_play_ui.py`,
`tests/test_suite_play_extras_ui.py`, `tests/test_suite_play_shell_ui.py`.
