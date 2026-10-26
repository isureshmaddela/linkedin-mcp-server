# Learn

A little learning every day, for every age: children practising times tables, adults learning a language,
grandparents brushing up on staying safe online. Each person in the household has their own progress.

## Studying

* **Decks** are sets of flashcards (a front, a back, an optional hint and example). A deck is yours, or shared with
  the household: everyone studies a shared deck with their own progress, and only its maker can change it.
* **Study** shows one card at a time. Think of the answer, press **Show answer** (or Space), then say how it went:
  **Again** (1), **Hard** (2), **Good** (3) or **Easy** (4). Cards come back just before you would forget them
  (spaced repetition, the SM-2 method): a card you knew comes back tomorrow, then in about 6 days, then further
  apart; a card you missed comes back in the same session.
* Each day has limits: new cards (10, 8 for children) and reviews (100). Change them under **Today > Change goal**.

## Goals, streaks and rewards

* **Daily goal**: a number of cards (15, 10 for children) or minutes. Reading can count toward it (2 minutes of
  reading = 1 card for a cards goal).
* **Streak**: days in a row the goal was met. Miss one day and meet the goal the next: this week's **streak freeze**
  keeps the streak (one a week). In the evening (after 18:00 in the business's time zone) Aira reminds you when a
  streak would break.
* **XP and levels**: 5 XP for a card you knew, 2 for one you missed, 2 more for a new card, 20 for meeting the goal,
  5 for each right quiz answer (10 more for a perfect quiz), 3 for each right maths answer, and 1 a minute of reading
  (up to 30 a day). Level 2 starts at 50 XP, level 3 at 200, level 4 at 450.
* **Badges**: first deck, first card, 3, 7 and 30 days in a row, 100 and 1,000 cards, a perfect quiz, 25 maths
  answers, 5 hours of reading, level 5.

## Decks to start with

Starter decks are added in one click (your own copy): times tables (2 to 6, 7 to 12), spelling for children, world
capitals, everyday Spanish and French, healthy living, staying safe online, first aid basics and the solar system.

**Make a deck on any topic**: type a topic, who it is for and how many cards. The writing model writes the cards
(check them). Without a model, Aira uses a matching starter deck when one fits, or a set of questions that guide you
to learn the topic yourself, marked "Written from a template" so you can edit the answers.

## Quizzes and maths

* **Quizzes** are multiple choice, made from a deck (the wrong options are other cards' answers). Answers are checked
  on the server and shown after you hand in.
* **Maths** practice by level: adding and taking away to 20, times tables, dividing, adding fractions, percentages.
  Relaxed has no clock; timed gives a little time for each question (more for children and seniors), and late
  answers are marked but earn no XP. Fractions can be typed as 3/4, 1 1/4 or 0.75.

## Reading and the family

* **Reading**: add books, log minutes (10, 20, 30 or any), mark a book finished.
* **Family**: this week's XP, streaks, cards and levels for everyone in the household (names and numbers only).

## Sharing a deck

An adult can share a deck they made as a page anyone can study in their browser without signing in (nothing they do
is saved). Turn the link off at any time. Children cannot share decks outside the household.

## For developers

`aira/suite/learn/` (`store.py` tables `ln_*`, `service.py` rules, `starters.py` starter decks, `learn_routes.py`
routes under `/api/learn`, Today card, pulse, hub checklist and the public `deck` page), `views-learn.js`,
`public-learn.js`, tests in `tests/test_suite_learn.py` and `tests/test_suite_learn_ui.py`.
