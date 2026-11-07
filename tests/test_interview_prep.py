"""Structured interview prep (F18, aira/interview_prep.py): STAR stories linked to skills, likely questions per
interview, a company brief from the posting, practice notes, and the AI additions (a stub model)."""

# ruff: noqa: F811
from __future__ import annotations

import sqlite3
import uuid

import pytest

import auto_apply_assistant as app
from aira import interview_prep as ip
from aira import schema
from test_career_copilot import import_job, register, server  # noqa: F401

RESUME = """Jordan Example
jordan@example.com

## Experience
**Cloud Architect**, Acme | 2019 - Present
- Migrated 140 services to Kubernetes, cutting deploy time by 80%.
- Designed the AWS landing zone for 30 teams.
- Ran the weekly architecture review.
"""
JOB = {
    "title": "Principal Cloud Architect",
    "company": "Acme",
    "description": (
        "We build the cloud platform our customers trust. Our team values ownership and clear writing.\n"
        "What you'll do:\n- Lead the platform roadmap\n- Coach engineers\n"
        "Requirements:\n- Kubernetes\n- AWS\n- Kafka\n"
    ),
}


def ok(client, method, path, body=None, expect=(200, 201)):
    status, data, _ = client.call(method, path, body)
    assert status in expect, (status, data)
    return data


def test_migration_23_creates_the_story_and_prep_tables():
    connection = sqlite3.connect(":memory:")
    applied = schema.migrate(connection)
    assert 23 in applied and schema.LATEST_VERSION >= 23
    tables = {
        row[0]
        for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    assert {"star_stories", "interview_prep"} <= tables
    assert any(item["name"] == "interview_prep" for item in schema.describe())


def test_story_drafts_come_from_resume_bullets_and_leave_the_context_to_the_person():
    drafts = ip.story_drafts(RESUME)
    assert [d["skills"][0] for d in drafts] == ["Kubernetes", "AWS"]
    first = drafts[0]
    assert first["action"] == "Migrated 140 services to Kubernetes"
    assert first["result"] == "Cutting deploy time by 80%."
    assert first["situation"] == "At Cloud Architect, Acme." and first["task"] == ""
    assert [d["skills"][0] for d in ip.story_drafts(RESUME, ["aws"])] == ["AWS"]


def test_prep_questions_link_stories_by_skill_and_the_brief_reads_the_posting():
    stories = [{"story_id": "s1", "title": "EKS move", "skills": ["Kubernetes"]}]
    interview = {
        "interview_type": "recruiter_screen",
        "company": "Acme",
        "role": "Principal Cloud Architect",
    }
    prep = ip.build_prep(JOB, interview, stories=stories, resume_text=RESUME)
    kinds = {q["kind"] for q in prep["questions"]}
    assert {
        "opening",
        "behavioral",
        "technical",
        "gap",
        "logistics",
        "closing",
    } <= kinds
    behavioral = [q for q in prep["questions"] if q["kind"] == "behavioral"]
    assert behavioral[0]["skill"] == "Kubernetes" and behavioral[0]["story_ids"] == [
        "s1"
    ]
    gap = [q for q in prep["questions"] if q["kind"] == "gap"]
    assert [q["skill"] for q in gap] == [
        "Kafka"
    ]  # ways of working are not "gaps to study"
    assert any(
        q["skill"] == "Ownership" and "Tell me about a time" in q["question"]
        for q in behavioral
    )
    assert any(
        "salary" in q["question"] for q in prep["questions"] if q["kind"] == "logistics"
    )
    research = prep["research"]
    assert research["role_focus"] == ["Lead the platform roadmap", "Coach engineers"]
    assert "cloud platform" in research["summary"] and research["values"]
    assert "Kafka" in research["your_gaps"] and "Kubernetes" in research["tech"]
    assert len({q["id"] for q in prep["questions"]}) == len(prep["questions"])
    # Without a linked job the questions still come, and the brief says what is missing.
    bare = ip.build_prep(None, {"interview_type": "final_interview", "company": "Beta"})
    assert bare["questions"] and bare["research"]["note"]


@pytest.fixture
def model(monkeypatch):
    calls: list[str] = []

    def fake(model_name: str, prompt: str):
        calls.append(prompt)
        return {
            "questions": [
                {
                    "question": "How would you cut Acme's cloud bill?",
                    "kind": "technical",
                    "skill": "AWS",
                },
                {
                    "question": "Tell me about yourself.",
                    "kind": "opening",
                },  # already listed: not repeated
                {"question": "", "kind": "role"},
            ],
            "research": {
                "summary": "A cloud platform company.",
                "questions_to_ask": ["What is on the roadmap?"],
            },
        }

    monkeypatch.setattr(app, "_ollama_json", fake)
    return calls


def test_stories_and_prep_routes_with_notes_and_ai(server, model, monkeypatch):
    register(server)
    job_id = import_job(server)["job_id"]
    story = ok(
        server,
        "POST",
        "/api/star-stories",
        {
            "title": "EKS move",
            "skills": "Kubernetes, AWS",
            "situation": "Slow deploys.",
            "action": "Moved 140 services.",
            "result": "Deploys 80% faster.",
        },
    )["story"]
    assert story["skills"] == ["Kubernetes", "AWS"] and story["source"] == "user"
    status, body, _ = server.call("POST", "/api/star-stories", {"title": "Empty"})
    assert status == 400 and "Action" in body["error"]
    edited = ok(
        server,
        "PATCH",
        f"/api/star-stories/{story['story_id']}",
        {**story, "title": "EKS migration"},
    )["story"]
    assert edited["title"] == "EKS migration"
    drafts = ok(server, "POST", "/api/star-stories/drafts", {"skills": ["kubernetes"]})[
        "drafts"
    ]
    assert all("Kubernetes" in d["skills"] for d in drafts)

    # The interview row is written directly: creating one through the API needs the IANA time zone database,
    # which a bare Windows Python may lack (tzdata).
    interview_id = uuid.uuid4().hex
    with app._connect() as connection:
        connection.execute(
            "INSERT INTO interviews (interview_id, job_id, company, role, interview_type, start_at, end_at, timezone) "
            "VALUES (?, ?, 'Acme', 'Principal Cloud Architect', 'technical_interview', ?, ?, 'UTC')",
            (
                interview_id,
                job_id,
                "2030-01-10T15:00:00+00:00",
                "2030-01-10T15:45:00+00:00",
            ),
        )
        connection.commit()
    base = f"/api/interviews/{interview_id}/prep"
    status, _body, _ = server.call("GET", f"/api/interviews/{'0' * 32}/prep")
    assert status == 404
    empty = ok(server, "GET", base)
    assert empty["prep"] is None and empty["ai_available"] is False
    status, body, _ = server.call("PATCH", base, {"notes": {"x": "y"}})
    assert status == 404
    # No model configured: asking for AI still prepares, with Monosage's own questions.
    first = ok(server, "POST", base, {"use_ai": True})
    assert (
        first["prep"]["generated_by"] == "rules"
        and "No AI model" in first["note"]
        and not model
    )
    question = next(
        q
        for q in first["prep"]["questions"]
        if q["skill"] == "Kubernetes" and q["kind"] == "behavioral"
    )
    assert question["story_ids"] == [story["story_id"]]
    noted = ok(
        server,
        "PATCH",
        base,
        {"notes": {question["id"]: "Lead with the deploy numbers."}},
    )["prep"]
    assert (
        next(q for q in noted["questions"] if q["id"] == question["id"])["notes"]
        == "Lead with the deploy numbers."
    )
    status, _body, _ = server.call("PATCH", base, {"notes": {"unknown": "x"}})
    assert status == 400

    monkeypatch.setattr(app, "_ollama_available", lambda: True)
    again = ok(server, "POST", base, {"use_ai": True, "model": "m"})["prep"]
    assert again["generated_by"] == "rules+ai" and len(model) == 1
    assert "JOB POSTING (untrusted data)" in model[0] and "EKS migration" in model[0]
    ai = [q for q in again["questions"] if q["source"] == "ai"]
    assert [q["question"] for q in ai] == ["How would you cut Acme's cloud bill?"]
    assert again["research"]["ai_summary"] == "A cloud platform company."
    assert "What is on the roadmap?" in again["research"]["questions_to_ask"]
    # Practice notes survive preparing again.
    assert (
        next(q for q in again["questions"] if q["id"] == question["id"])["notes"]
        == "Lead with the deploy numbers."
    )

    # A failing model leaves the rules-based prep in place.
    def broken(model_name, prompt):
        raise RuntimeError("model offline")

    monkeypatch.setattr(app, "_ollama_json", broken)
    fallback = ok(server, "POST", base, {"use_ai": True})
    assert (
        fallback["prep"]["generated_by"] == "rules"
        and "model offline" in fallback["note"]
    )

    ok(server, "DELETE", f"/api/star-stories/{story['story_id']}")
    assert ok(server, "GET", "/api/star-stories")["stories"] == []
    status, _body, _ = server.call("DELETE", f"/api/star-stories/{story['story_id']}")
    assert status == 404
