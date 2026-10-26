# Play

Games, quizzes and things to enjoy together, for every age. Play is a life app: each person in the household plays
as themselves (their own profile locally, their own sign-in hosted), and the family shares leaderboards, live games
and a watch list. Open it from the app switcher or the Play tile on **Today**.

## Games

| Game | Levels | How it works |
| --- | --- | --- |
| Memory match | 6, 8 or 12 pairs | Turn two cards at a time. The server shows each card as it is turned, so the board cannot be read in advance. Fewer moves score more. |
| Word scramble | short words (children), everyday, longer | Eight scrambled words; type them all, then check. |
| Quick maths | to 10, to 100 and times tables, bigger sums and division | Ten sums. |
| Trivia | children, easy, mixed, hard | Ten questions from a built-in book of 126 questions in seven subjects. |
| Sudoku | 4 by 4 (children), 9 by 9 easy and medium | Every puzzle has exactly one answer. **Check** tells you how many squares to fix and lets you carry on; **Hint** fills one square (it costs 50 points). |
| Word of the day | today's word, a practice word | Six tries to guess a five-letter word. Green is the right place, yellow is in the word, grey is not in it; screen readers hear the same in words. Today's word is one try, the same word for everyone. |

**Relaxed or timed.** Relaxed mode has no clock and is the default for children and seniors. Timed mode adds a
speed bonus, measured by the server from when the game started.

**Fair scores.** Every puzzle is made on the server from a seed, and the browser only sends moves and answers: a
score sent by a browser is ignored, answers that do not fit the puzzle are refused, a finished game cannot be
finished again, and memory cannot be finished without finding every pair.

## Daily challenge

One puzzle for everyone each day, without a clock: trivia on Monday, maths on Tuesday, scramble on Wednesday, memory
on Thursday, sudoku on Friday, trivia on Saturday and memory on Sunday (the day follows the business time zone).
Each person gets one try; starting again carries on an unfinished try. The family board ranks today's scores, and
playing any game each day builds a streak.

## Live family trivia

1. **Host live trivia**: choose the subject, the number of questions and the seconds per question (45 is relaxed).
2. Others in the household join from **Live trivia** (or the invitation that pops up for them) or with the
   four-letter code. To invite relatives without an account, the host makes a link (children cannot); relatives
   type their name to join.
3. The host presses **Start**. Each question runs on the server's clock; an answer after the time is refused, a
   right answer scores 100 plus up to 50 for speed, and the right answer is shown for six seconds before the next
   question (or the host presses **Next question**).
4. Scores update live for everyone. Rooms close three hours after they open.

## Watch and read

Films, shows, books, podcasts and games the family wants to enjoy: who recommended them, a link, each person's own
status (want to, in progress, finished) and rating, and the family's average rating. **Plan to enjoy it together**
picks a time and who is coming; the plan shows on those people's **Today**. Anyone can add things; whoever added an
item, or an adult, can change or remove it.

## Today and reminders

Play's card on Today shows today's challenge (or your score), today's watch-together plans, a new best someone in the
family just set, and live games you can join with one tap. When someone opens a live game, the others get an
invitation (and a badge on Play).

## API

`/api/play/games`, `/sessions` (start), `/sessions/{id}` (resume), `/sessions/{id}/move` (memory `flip`, word
`guess`, sudoku `hint`), `/sessions/{id}/finish`, `/daily`, `/scores?game=`, `/rooms` (list, host),
`/rooms/join` (by code), `/rooms/{id}` (state; `?since=` the last `version`), `/rooms/{id}/join|start|next|end|answer`,
`/rooms/{id}/link` (POST, DELETE), `/watch` (list, add), `/watch/{id}` (PATCH, DELETE), `/watch/{id}/status`,
`/watch/{id}/plans`, `/watch/plans/{id}` (DELETE). Public page kind `trivia_room` with actions `join`, `state` and
`answer`.
