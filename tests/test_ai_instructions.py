"""Standing AI instructions and skills (aira/ai_instructions.py) and reopening or restoring jobs, in the local build.

The person writes instructions for every Careers task and skills for one or more tasks; each Careers model call adds
the ones for its task after the fixed rule, whichever model answers (Claude or the local Ollama), and calls that are
not Careers tasks (Leads, the suite apps) get nothing. Claude is the loopback stand-in of tests/test_model_provider.py.
"""

# ruff: noqa: F811
from __future__ import annotations

import json

import pytest
from test_model_provider import KEY, api, env, use_claude  # noqa: F401

import auto_apply_assistant as app
from aira import ai_instructions, model_provider

ROUTE = "/api/settings/ai-profile"


def ask(model: str, prompt: str, purpose: str = "", note: str = ""):
    """One model call as the app makes it (env.as_user passes positional arguments only)."""
    if not purpose:
        return app._ollama_json(model, prompt)
    return app._careers_ai(purpose, model, prompt, note=note)


PROFILE = {
    "instructions": "I want remote senior cloud architect roles. Sign my replies as Hyma.",
    "skills": [
        {
            "name": "Warm replies",
            "tasks": ["replies"],
            "instructions": "Reply in two to four friendly sentences.",
        },
        {
            "name": "Short letters",
            "tasks": ["documents"],
            "instructions": "Cover letters under 250 words.",
            "enabled": False,
        },
    ],
}


# -- the module ------------------------------------------------------------------------------


def test_a_profile_is_cleaned_and_checked():
    clean = ai_instructions.validate_profile(
        {
            "instructions": "  Remote only.\x00\r\nNo agencies.  ",
            "skills": [
                {
                    "name": "Replies",
                    "tasks": ["replies", "replies"],
                    "instructions": "Be brief.",
                }
            ],
        }
    )
    assert clean["instructions"] == "Remote only.\nNo agencies."
    [skill] = clean["skills"]
    assert skill["tasks"] == ["replies"] and skill["enabled"] is True
    assert len(skill["id"]) == 12 and int(skill["id"], 16) >= 0

    too_long = "x" * (ai_instructions.INSTRUCTIONS_LIMIT + 1)
    for bad, words in (
        ({"instructions": too_long}, "under"),
        ({"instructions": 5}, "as text"),
        ({"skills": "nope"}, "list"),
        (
            {"skills": [{"name": "A", "tasks": ["poems"], "instructions": "x"}]},
            "unknown task",
        ),
        (
            {"skills": [{"name": "A", "tasks": [], "instructions": "x"}]},
            "at least one task",
        ),
        ({"skills": [{"name": "", "tasks": ["replies"], "instructions": "x"}]}, "name"),
        (
            {"skills": [{"name": "A", "tasks": ["replies"], "instructions": ""}]},
            "instructions",
        ),
        (
            {
                "skills": [
                    {"name": "Same", "tasks": ["replies"], "instructions": "x"},
                    {"name": "same", "tasks": ["answers"], "instructions": "y"},
                ]
            },
            "different name",
        ),
        (
            {
                "skills": [
                    {"name": f"S{i}", "tasks": ["replies"], "instructions": "x"}
                    for i in range(ai_instructions.MAX_SKILLS + 1)
                ]
            },
            "or fewer",
        ),
        ([], "object"),
    ):
        with pytest.raises(ValueError, match=words):
            ai_instructions.validate_profile(bad)

    # a copied skill that kept its id gets a new one
    twins = ai_instructions.validate_profile(
        {
            "skills": [
                {
                    "id": "a" * 12,
                    "name": "One",
                    "tasks": ["replies"],
                    "instructions": "x",
                },
                {
                    "id": "a" * 12,
                    "name": "Two",
                    "tasks": ["replies"],
                    "instructions": "y",
                },
            ]
        }
    )
    assert len({skill["id"] for skill in twins["skills"]}) == 2
    assert ai_instructions.load("not json") == ai_instructions.empty_profile()


def test_compose_adds_only_what_applies_to_the_task():
    person = ai_instructions.validate_profile(PROFILE)
    organization = ai_instructions.validate_profile(
        {
            "instructions": "Never name a candidate's current employer.",
            "skills": [
                {
                    "name": "Program tone",
                    "tasks": ["replies", "answers"],
                    "instructions": "Professional.",
                }
            ],
        }
    )
    assert ai_instructions.compose("replies") == ""
    replies = ai_instructions.compose(
        "replies", organization, person, note="Ask about the team."
    )
    assert replies.startswith("STANDING INSTRUCTIONS.")
    assert (
        "follow the organization's" in replies
        and "never allow inventing facts" in replies
    )
    order = [
        "THE ORGANIZATION'S INSTRUCTIONS",
        'THE ORGANIZATION\'S SKILL "Program tone"',
        "THE PERSON'S OWN INSTRUCTIONS",
        'SKILL "Warm replies"',
        "THE PERSON'S NOTE FOR THIS ONE REPLY",
    ]
    positions = [replies.index(heading) for heading in order]
    assert positions == sorted(positions)
    assert "Short letters" not in replies  # another task's skill
    documents = ai_instructions.compose("documents", None, person)
    assert "Cover letters under 250 words" not in documents  # switched off
    assert "Sign my replies as Hyma" in documents
    general = ai_instructions.compose("general", organization, person)
    assert (
        "SKILL" not in general and "Never name" in general
    )  # instructions only, no task skills
    assert ai_instructions.compose("made-up", None, person) == general.replace(
        general[: general.index("THE PERSON'S")], ai_instructions.PREAMBLE + "\n\n"
    )
    with pytest.raises(ValueError, match="note"):
        ai_instructions.compose("replies", None, person, note="x" * 501)

    # very long instructions are cut whole, with a line that says so
    many = ai_instructions.validate_profile(
        {
            "instructions": "i" * 4000,
            "skills": [
                {"name": f"Skill {n}", "tasks": ["replies"], "instructions": "s" * 4000}
                for n in range(4)
            ],
        }
    )
    text = ai_instructions.compose("replies", None, many)
    assert len(text) <= ai_instructions.COMPOSED_LIMIT + 60
    assert "more instructions left out" in text


# -- the routes --------------------------------------------------------------------------------


def test_the_person_saves_previews_and_reads_their_instructions(env, api):
    first = api.ok("GET", ROUTE)
    assert first["personal"] == {"instructions": "", "skills": [], "can_edit": True}
    assert first["organization"] is None and first["hosted"] is False
    assert [task["id"] for task in first["tasks"]] == list(ai_instructions.TASKS)
    assert (
        first["templates"] and first["limits"]["skills"] == ai_instructions.MAX_SKILLS
    )

    saved = api.ok("PUT", ROUTE, PROFILE)
    assert saved["personal"]["instructions"] == PROFILE["instructions"]
    assert [s["name"] for s in saved["personal"]["skills"]] == [
        "Warm replies",
        "Short letters",
    ]
    assert api.ok("GET", ROUTE)["personal"] == saved["personal"]

    preview = api.ok("POST", f"{ROUTE}/preview", {"task": "replies"})
    assert preview["rule"] == model_provider.SYSTEM_RULE
    assert "Warm replies" in preview["text"] and "Hyma" in preview["text"]
    assert preview["chars"] == len(preview["text"])
    assert api.call("POST", f"{ROUTE}/preview", {"task": "poems"})[0] == 400

    # a bad profile is refused and the saved one stays
    status, body = api.call(
        "PUT",
        ROUTE,
        {"skills": [{"name": "x", "tasks": ["nope"], "instructions": "y"}]},
    )
    assert status == 400 and "unknown task" in body["error"]
    assert api.ok("GET", ROUTE)["personal"] == saved["personal"]
    # clearing everything is allowed
    assert (
        api.ok("PUT", ROUTE, {"instructions": "", "skills": []})["personal"]["skills"]
        == []
    )


def test_claude_receives_the_instructions_for_its_task_only(env, api):
    use_claude(api)
    api.ok("PUT", ROUTE, PROFILE)

    env.as_user(ask, "qwen3.5:4b", "Draft a reply.", "replies")
    system = env.stub.requests[-1]["body"]["system"]
    assert system.startswith(model_provider.SYSTEM_RULE)
    assert (
        "STANDING INSTRUCTIONS" in system
        and "Warm replies" in system
        and "Hyma" in system
    )

    env.as_user(ask, "qwen3.5:4b", "Score fit.", "fit_review")
    system = env.stub.requests[-1]["body"]["system"]
    assert "Hyma" in system and "Warm replies" not in system

    # Leads and the suite apps call without a purpose: only the fixed rule
    env.as_user(ask, "qwen3.5:4b", "Score this lead.")
    assert env.stub.requests[-1]["body"]["system"] == model_provider.SYSTEM_RULE

    # a one-off note reaches only that call
    env.as_user(
        ask,
        "qwen3.5:4b",
        "Draft a reply.",
        "replies",
        "Say I am interested.",
    )
    assert "Say I am interested." in env.stub.requests[-1]["body"]["system"]
    env.as_user(ask, "qwen3.5:4b", "Draft a reply.", "replies")
    assert "Say I am interested." not in env.stub.requests[-1]["body"]["system"]
    # the key never travels with the instructions
    assert KEY not in json.dumps(api.ok("GET", ROUTE))


def test_the_local_model_receives_them_too(env, api):
    api.ok("PUT", ROUTE, PROFILE)
    env.as_user(ask, "qwen3.5:4b", "Write the documents.", "documents")
    [chat] = env.ollama.chats
    system = chat["messages"][0]
    assert system["role"] == "system"
    assert system["content"].startswith(model_provider.SYSTEM_RULE)
    assert "Hyma" in system["content"] and "Short letters" not in system["content"]
    env.as_user(ask, "qwen3.5:4b", "Plain call.")
    assert env.ollama.chats[-1]["messages"][0]["content"] == model_provider.SYSTEM_RULE


def test_a_reply_note_that_is_too_long_is_refused_before_anything_runs(env, api):
    status, body = api.call(
        "POST",
        "/api/conversations/abc123/draft",
        {"note": "x" * (ai_instructions.NOTE_LIMIT + 1)},
    )
    assert status == 400 and "note" in body["error"]


# -- reopening an application and restoring an archived job -----------------------------------


def _manual_job(api) -> str:
    added = api.ok(
        "POST",
        "/api/jobs/import",
        {"manual": {"title": "Cloud Architect", "company": "Acme"}},
        expect=201,
    )
    return added["job"]["job_id"]


def test_a_closed_application_reopens_where_it_was(env, api):
    job_id = _manual_job(api)
    for stage in ("saved", "needs_review", "applying", "applied", "recruiter_screen"):
        api.ok("PATCH", f"/api/jobs/{job_id}/stage", {"stage": stage})
    api.ok("PATCH", f"/api/jobs/{job_id}/stage", {"stage": "rejected"})
    # rejected was a dead end before; a plain stage change still is
    assert (
        api.call("PATCH", f"/api/jobs/{job_id}/stage", {"stage": "applied"})[0] == 400
    )

    reopened = api.ok("POST", f"/api/jobs/{job_id}/reopen", {})["job"]
    assert reopened["stage"] == "recruiter_screen"
    with env.as_user(app._connect) as connection:  # the account's own database
        rows = connection.execute(
            "SELECT previous_status, new_status, reason FROM application_events WHERE job_id = ? ORDER BY occurred_at, rowid",
            (job_id,),
        ).fetchall()
    assert [tuple(row)[:2] for row in rows][-2:] == [
        ("recruiter_screen", "rejected"),
        ("rejected", "recruiter_screen"),
    ]
    assert rows[-1][2].startswith("Reopened")
    # an open application cannot be "reopened"
    status, body = api.call("POST", f"/api/jobs/{job_id}/reopen", {})
    assert status == 400 and "reopened" in body["error"]
    assert api.call("POST", "/api/jobs/no-such-job/reopen", {})[0] == 404


def test_an_archived_job_is_listed_and_restored(env, api):
    job_id = _manual_job(api)
    api.ok("POST", f"/api/jobs/{job_id}/archive", {})
    assert job_id not in [
        j["job_id"] for j in api.ok("GET", "/api/jobs?view=all")["jobs"]
    ]
    archived = api.ok("GET", "/api/jobs?view=all&archived=only")["jobs"]
    assert [j["job_id"] for j in archived] == [job_id]

    restored = api.ok("POST", f"/api/jobs/{job_id}/restore", {})
    assert restored["restored"] is True and restored["job"]["job_id"] == job_id
    assert job_id in [j["job_id"] for j in api.ok("GET", "/api/jobs?view=all")["jobs"]]
    assert api.ok("GET", "/api/jobs?view=all&archived=only")["jobs"] == []
    assert api.ok("POST", f"/api/jobs/{job_id}/restore", {})["restored"] is False
    assert api.call("POST", "/api/jobs/no-such-job/restore", {})[0] == 404
