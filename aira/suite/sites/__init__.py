"""Sites: describe a website in plain words and get it built, then edit, preview, publish and deploy it.

The main objects are a **project** (a website: its working files, a stack, a client and a price), its **versions**
(a snapshot of every file after each AI run, manual edit or restore), the **conversation** with the builder and its
**runs** (one model call, streamed into the run row so the page can show the agent's steps and the code as it is
written). Models come from :mod:`aira.suite.sites.providers` (the offline designer, Ollama and LM Studio on this
computer, Claude, Gemini, OpenAI, OpenRouter or any OpenAI-compatible server). A project is previewed and published
through public links whose files are served under a sandboxed policy, and shipped as a ZIP with a deploy kit for
every major cloud, a push to GitHub, or a direct deploy to Netlify or Vercel (:mod:`aira.suite.sites.deploy`).

How people earn with it: build sites for clients, set the price on the project and mark it paid (the Business hub's
revenue ledger), and publish the finished site for them.
"""
