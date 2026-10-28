"""AI (``#/ai``): one assistant across every app the account has, and the agents it works through.

* **Chat** with the master agent: it asks the agent of each app what matters (goals, medicines, chores, the
  family's dates, headlines, the business's numbers) and answers with the writing model, or with built-in
  answers when no model runs. Asked to do something ("add milk to the shopping list", "add a goal: run a 5K"), it
  hands the action to the right agent.
* **Agents**: one per app. Each person decides for each agent whether it is on and how much it may do alone (its
  consent level): read only, small actions alone, every action alone, or ask first. Actions that need consent wait
  in **Approvals** (also once an agent reaches its daily limit of actions alone); every read and action an agent
  takes is in the **Activity** log, and done actions can be undone.
* **Routines** (questions and commands run at a set time), **Memory** (what the person asked Aira to remember,
  used in every answer) and **Earn** (what the apps earned, earning goals and their pace, next steps).

Agents and their reads and actions are in :mod:`aira.suite.ai.agents`; the chat and the consent rules in
:mod:`aira.suite.ai.service`. Everything belongs to the person who asked (:func:`aira.suite.life.current`).
"""
