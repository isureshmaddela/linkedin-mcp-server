"""Play: entertainment for every age, made fair by the server.

* **Brain games** (memory match, word scramble, quick maths, trivia, sudoku, word of the day): each puzzle is made
  on the server from a seed (``games.py``) and checked there. The browser never sends a score, only moves or
  answers, so a score cannot be faked. Relaxed mode has no clock (the default for children and seniors); timed
  mode adds a speed bonus measured by the server.
* **Daily challenge**: one puzzle for everyone each day (seeded by the date), one scored try per person.
* **Scores**: personal bests, a play streak and family leaderboards.
* **Live family trivia**: a person hosts a room with a four-letter code; the household joins (relatives too, through
  a public link); questions run on the server's clock and answers after the time are refused.
* **Watch and read list**: films, shows, books, podcasts and games the household wants to enjoy, each person's own
  status and rating, and "watch together" plans that appear on Today.
"""
