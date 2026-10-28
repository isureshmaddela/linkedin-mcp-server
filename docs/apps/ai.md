# AI

One assistant across all your apps. Open it from the app switcher (Everyday > AI). It has seven tabs: **Chat**,
**Agents**, **Approvals**, **Routines**, **Memory**, **Earn** and **Activity**. Everything in it (conversations,
agent settings, approvals, routines, memories, the log) is yours alone: another person in the household, or another
member of the organization, never sees it.

## Chat

* Ask about your day, goals, medicines, chores, family, learning, news, videos, stocks or business: "Give me my
  morning brief", "How are my goals going?", "Which medicines should I take?", "How are my stocks doing today?",
  "How's my business doing?".
* The **master agent** asks the agent of each app the question is about (all of them for a brief, or when no app's
  words match), then answers from what they found with Aira's writing model (Settings > writing model, a local
  Ollama model or the organization's model hosted). Without a model it answers with the facts themselves.
  It never invents data: if your apps do not say, it says so.
* Ask it to do something:
  * "Add milk and bread to the shopping list", "We need to buy rice" (Planner)
  * "Remind me to call Mum tomorrow at 6pm", "Remember to pay the rent", "Add a task: water the plants" (Planner)
  * "Note: the wifi password is on the router" (a private Planner note)
  * "Add a goal: run a 5K by 2026-12-31", "Set run a 5K to 60%" (Life)
  * "Add an event: moved to Pune on 2023-03-14" (Life)
  * "Add an earning goal: 50000 by 2026-12-31" (Earn)
  * "Remember that I am vegetarian", "Forget that I'm vegetarian", "Forget everything" (Memory)

  These plain commands are understood at once without the model. With the model, other wordings work too, and
  apps that add their own agent (Stocks: "Add Apple to my watchlist", "Buy 2 Apple shares with practice money")
  take part. Each action passes its agent's consent level and daily limit; the answer says what was done, what
  waits for you (with **Approve** and **Reject**) and what was not allowed, and done actions can be **undone**.
  Asking for something your profile cannot use (a child asking for an earning goal) gets a clear "not done".
* Mark an answer **Helpful** or **Not helpful** (press again to take the mark back). The Agents tab shows the share
  of marked answers you found helpful.
* The bar above the chat shows the agent that answered, the tokens the conversation used (an estimate), the average
  answer time and where answers come from. Conversations are listed on the left, with search; **Rename** and
  **Save as text** keep one. The microphone button (where the browser supports speech) fills the message box from
  your voice.

## Agents

One agent per app you have: **LifeAgent** (Life), **PlannerAgent** (Planner), **HealthAgent** (Health),
**FamilyAgent** and **SocialAgent** (Social), **LearnAgent**, **PlayAgent**, **NewsAgent**, **VideosAgent**,
**BusinessAgent** (the business apps' revenue and numbers: only for adults on this computer, and for owners, admins,
coaches and auditors in an organization), **EarnAgent** (what your apps earned, your earning goals, next steps;
same people as BusinessAgent), **MemoryAgent** (what you asked Aira to remember) and the agents other apps add,
such as **StocksAgent** (Stocks). For each you see its operations, success rate and consent level, switch it on or
off, and **Configure** it.

Consent levels:

| Level | What the agent may do |
| --- | --- |
| Read only (`READ_ONLY`) | Read to answer you; never change anything. |
| Small actions alone (`AUTO_MINOR`) | Small, easy-to-undo actions (a task, a note, a shopping item, a goal, a memory) happen at once; bigger ones (a practice trade) wait for you. |
| All actions alone (`AUTO_MAJOR`) | Every action happens at once. |
| Ask me first (`MANUAL_APPROVAL`) | Every action waits in Approvals. |

**Most actions alone in 24 hours** (in Configure): when an agent has taken that many actions on its own in the last
24 hours, its next ones wait in Approvals with the reason, whatever its consent level. 0 means no limit. Actions you
approved do not count. It is a good guard for agents that trade or spend. An agent may bring its own limit (Stocks
does); it applies until you change the number yourself, even if you switched the agent off and on before.

LifeAgent, PlannerAgent, EarnAgent and MemoryAgent start at **Small actions alone**; the agents that only read start
at **Read only**. A switched-off agent is neither read nor allowed to act.

## Approvals and Activity

* **Approvals** lists actions waiting for a yes, with **Approve all** and **Reject all**. Approving runs the action
  then (once, even if you press Approve on Today and here at the same moment); rejecting drops it. They also
  appear on **Today** with an **Approve** button, and the AI menu item shows how many wait.
* **Activity** is the log of every read and action: when, which agent, what (a read names the question it was
  for), the result (done, failed, not allowed, waiting, rejected, undone) and details. Search it, filter by agent,
  result and kind, **Undo** what can be taken back, and **Export CSV** (with the same filters).

## Routines

A question or command Aira runs for you at a time on the days you choose ("Give me my morning brief" every day at
07:00). When it is due and Aira is open, it runs once that day, shows the answer (read aloud in easy mode) and puts
it on **Today**. **Run now** runs it at once with the writing model. Commands in a routine pass the same consent
gate as the chat. Start from the ideas (Morning brief, Weekly goals check, Money check).

## Memory

Things you want Aira to keep in mind for every answer: your diet, your family's names, how you like answers. Add
them in the Memory tab (or say "Remember that ..." in the chat), change or forget each one, search them, or
**Forget everything**. Every question to the writing model carries them; "What do you remember about me?" lists
them. Aira keeps up to 100 memories for each person. "Remember to ..." and "Don't forget the milk" are reminders
(tasks), not memories; "Don't forget that ..." or "Don't forget my ..." is a memory. "Forget everything" in the chat
waits for your yes, and can be undone.

Aira only remembers or forgets when you say so: if the writing model suggests a memory you did not ask for, it waits
in Approvals, whatever MemoryAgent's consent level. Undo on an old "Remember" card does nothing if you changed that
memory since (use **Forget** in the Memory tab).

## Earn

How your apps make money for you:

* What they earned this month, last month and in all, and this month by app (adults and organization staff only).
* **Earning goals** ("Earn $5,000 by December"): each counts what your apps earn from the day it is set. The amount
  is a number above 0 ("50000", "50,000", "$5k", "2 lakh"); anything else ("-50", "1e6", "5 to 10 thousand") is
  refused with a clear message rather than guessed at. A goal with
  a date shows its **pace**: what it needs a week to be reached in time, what your apps earned a week over the last
  four weeks, where that pace ends up by the date, and **On track**, **Behind** or **Past its date**. EarnAgent
  says the same in the chat.
* **Next steps to earn more**: the business apps' setup steps, the ones that bring money in first.
* What each app's agent says you earn (Stocks, Videos and other apps that add an earner).
* **Ways to earn with your apps**, for everyone, children included.

## For developers

`aira/suite/ai/agents.py` holds the agents. An agent that only reads needs nothing more than its app's Today cards
(`life.today`); an agent that acts lists `Action`s with a risk (`minor` or `major`), the arguments the model may fill,
a `run` function that calls the app's service as the person, and optionally an `undo` (`run` returns
`agents.done(sentence, **what_undo_needs)`). An action with `typed_only=True` (MemoryAgent's) runs alone only when
the person typed it as a command; proposed by the writing model, it waits in Approvals.

Another app adds its own agent from its route module, without touching the AI app:

```python
from aira.suite.ai import agents

agents.register(agents.Agent(
    "stocks", "StocksAgent", "stocks", "", "Quotes, movers and your portfolio.",
    ("stock", "shares", "portfolio"), "auto_minor", ACTIONS,
    reader=assistant.facts,       # lines of facts for a question
    parser=assistant.commands,    # optional: plain commands understood without the model
    earner=assistant.earnings,    # optional: lines for the Earn tab
    default_limit=10,             # optional: most actions alone in 24 hours until the person sets their own
))
```

A registered agent gets the same switch, consent levels, daily limit, approvals, activity log and undo as a built-in
one (`tests/test_suite_ai_more.py` and `tests/test_suite_ai_memory.py` check it with fake agents). Set
`brief=False` for an agent that should only be asked when its own words are used.

`aira/suite/ai/service.py` holds the chat, the command parser, the consent gate and the daily limit;
`routines.py` the routines; `memory.py` the memories; `earn.py` the Earn tab and earning-goal pace. Routes are in
`ai_routes.py` (`/api/ai/*`); hosted, they answer `402 app_required` until the organization has the AI app.
