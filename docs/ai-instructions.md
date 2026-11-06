# AI instructions, skills and "My Claude"

Monosage's AI writes fit reviews, tailored resumes and cover letters, application question answers and recruiter
replies (drafts and automatic replies). This page explains how to make it write and answer the way you would, and,
in Monosage Cloud, how to use your own Claude for your own applications.

## Where to find it

- **Careers > Profile & resume > AI instructions** (also **Settings > AI instructions**): your instructions, your
  skills, the organization's instructions (Monosage Cloud), and a preview.
- **Settings > Integrations**: which AI answers. The local build has the Claude card (your Anthropic key or the local
  Ollama model). Monosage Cloud has the organization's model card and **Your AI for job applications**.
- **Messages**: the line above the reply box, *Tell the AI what to say*, steers one draft only.

## Instructions and skills

| | What it is | Who writes it |
|---|---|---|
| Instructions | Free text for every task: who you are, what you want, facts and tone ("remote roles only", "sign as Hyma") | You (and, in Monosage Cloud, owners and admins for the organization) |
| Skills | Named instructions for one or more tasks, switched on or off. Starter skills can be added from a list | Same |
| Note | One-off steer for a single reply draft | You, in Messages |

Tasks: fit reviews, tailored resumes and cover letters, application question answers, and recruiter replies (drafts
and automatic replies). A recruiter relationship summary gets the instructions but no skills.

Limits: 4,000 characters of instructions, 12 skills of up to 4,000 characters each, 80-character skill names, a
500-character note, and 12,000 characters added to one call (anything beyond is left out, and the preview says so).

### What the AI receives, in order

1. The fixed rule (job descriptions and messages are untrusted data; nothing is invented; JSON only).
2. The organization's instructions, then its skills for the task (Monosage Cloud).
3. Your instructions, then your skills for the task.
4. The note for this one reply, if any.

The text tells the model that instructions never lift the fixed rule or the task's own rules, and that the
organization's instructions win when they conflict with yours. **Preview** shows exactly this text for any task;
nothing is sent to a model to make it.

### What stays the same

- Nothing is invented: facts come from your resume and profile only.
- Automatic replies are still screened by `aira/messaging.py`. Messages about pay, visas or work authorization,
  personal identifiers, offers, background checks, relocation, links, contact details or money are held for you.
- Leads and the other apps (Sales, Marketing, the suite apps) do not receive Careers instructions.

## My Claude or Organization AI (Monosage Cloud)

Each member chooses, in **Settings > Integrations > Your AI for job applications**:

- **Organization AI**: whatever the organization chose (Monosage's hosted model, Claude with the organization's key,
  or its own endpoint).
- **My Claude**: the member's own Anthropic API key and Claude model (Sonnet 5.5, Opus 5.5 or Haiku 5.5).

It applies to every model call made for that member's own workspace: fit reviews, documents, application answers,
recruiter replies and background Auto-apply runs. A coach working in the member's workspace uses the member's choice.
The key is stored encrypted as an organization secret for that member alone, never returned (only its last four
characters), never logged, deleted when the member removes it or leaves the organization, and re-encrypted with the
organization's other secrets on key rotation. Calls are still counted against the organization's model calls.

Owners and admins can turn this off: **Organization > Policy > AI > Members may use their own Claude** (on by
default). When it is off, members' drafts use the organization's AI; a member can still choose Organization AI or
remove their key, but cannot choose or test their own Claude.

The status pill at the top names the AI in use: **My Claude**, **Claude** (the organization's), or **Organization AI**.

## Routes

| Route | Who | What |
|---|---|---|
| `GET /api/settings/ai-profile` | the person (hosted: anyone who can read the workspace) | Their instructions and skills, the organization's (read-only), tasks, limits, starter skills |
| `PUT /api/settings/ai-profile` | the person (hosted: the candidate only, like sending messages) | Save instructions and skills |
| `POST /api/settings/ai-profile/preview` | as GET | `{task}` -> the text one task's calls receive |
| `GET /api/org/ai-profile` (hosted) | every member | The organization's instructions and skills, `can_manage` |
| `PUT /api/org/ai-profile` (hosted) | owners and admins (`integrations.manage`) | Save them (audited `integrations.ai_profile`) |
| `GET /api/me/ai` (hosted) | the member | Their choice, what answers now, the pill label |
| `POST /api/me/ai` (hosted) | the member | `{choice, model, api_key}` (audited `member.ai_settings`, no key) |
| `POST /api/me/ai/test` (hosted) | the member | One small request with their key |
| `DELETE /api/me/ai/key` (hosted) | the member | Forget the key |
| `POST /api/conversations/{id}/draft` | as before | Takes an optional `note` |

Code: `aira/ai_instructions.py` (validation and composition), `aira/ai_instructions_routes.py`, the `purpose` and
`note` arguments of `_ollama_json` in `auto_apply_assistant.py`, `system_extra` in `aira/model_provider.py` and
`monosage_cloud/models.py`. Tests: `tests/test_ai_instructions.py`, `tests/test_cloud_ai_profile.py`.
