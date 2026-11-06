"""Structured interview preparation (Careers F18): STAR stories, likely questions and a company brief.

* **STAR stories** (``star_stories``) are the person's own stories (situation, task, action, result), each linked
  to the skills it shows. :func:`story_drafts` starts drafts from the person's resume bullets that mention a
  skill; nothing is saved until the person edits and saves it.
* **Interview prep** (``interview_prep``, one row per interview) holds the likely questions for that interview
  (from the job's skills, the interview type and the person's gaps), each linked to the stories that answer it
  and with the person's practice notes, plus a company research brief built from the job posting.

:func:`build_prep` is deterministic, so prep works with no model configured. When a model is available the HTTP
layer asks it for more questions and a sharper brief through ``_careers_ai`` and :func:`merge_ai` adds them,
marked as written by AI; the job posting stays untrusted data and the stories stay the person's own.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from typing import Any

from aira import resume_render
from aira.scoring import (
    candidate_skill_set,
    find_skills,
    job_skill_requirements,
    skill_evidenced,
    skill_label,
)

__all__ = [
    "QUESTION_KINDS",
    "build_prep",
    "delete_story",
    "ensure_schema",
    "get_prep",
    "list_stories",
    "merge_ai",
    "save_prep",
    "save_question_notes",
    "save_story",
    "story_drafts",
]

QUESTION_KINDS = (
    "opening",
    "behavioral",
    "technical",
    "gap",
    "role",
    "logistics",
    "closing",
)
STORY_FIELDS = {
    "title": 160,
    "situation": 2000,
    "task": 2000,
    "action": 3000,
    "result": 2000,
}
MAX_STORIES = 200
MAX_SKILLS_PER_STORY = 12
NOTE_LIMIT = 2000
# Lexicon skills that are ways of working rather than tools or methods (aira/scoring.py SKILL_LEXICON).
SOFT_SKILLS = frozenset(
    {
        "leadership", "mentoring", "stakeholder_management", "executive_communication", "communication",
        "cross_functional", "customer_facing", "negotiation", "strategic_thinking", "problem_solving",
        "collaboration", "team_building", "innovation", "ownership", "decision_making", "influence",
        "time_management", "conflict_resolution", "stakeholder", "executive", "strategy", "technology_roadmap",
        "roadmap", "standards", "capability", "transformation", "scale", "governance",
    }
)  # fmt: skip


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_schema(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS star_stories (
            story_id TEXT PRIMARY KEY, title TEXT NOT NULL, situation TEXT NOT NULL DEFAULT '',
            task TEXT NOT NULL DEFAULT '', action TEXT NOT NULL DEFAULT '', result TEXT NOT NULL DEFAULT '',
            skills TEXT NOT NULL DEFAULT '[]', source TEXT NOT NULL DEFAULT 'user',
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )
    connection.execute(
        """CREATE TABLE IF NOT EXISTS interview_prep (
            interview_id TEXT PRIMARY KEY, questions TEXT NOT NULL DEFAULT '[]',
            research TEXT NOT NULL DEFAULT '{}', generated_by TEXT NOT NULL DEFAULT 'rules',
            generated_at TEXT NOT NULL, updated_at TEXT NOT NULL
        )"""
    )


# --------------------------------------------------------------------------- #
# STAR stories
# --------------------------------------------------------------------------- #
def _clean(value: Any, limit: int) -> str:
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", str(value or "")).strip()
    return text[:limit]


def _skills(value: Any) -> list[str]:
    raw = value if isinstance(value, list) else str(value or "").split(",")
    out: list[str] = []
    for item in raw:
        cleaned = _clean(item, 60)
        if cleaned and cleaned.casefold() not in {s.casefold() for s in out}:
            out.append(cleaned)
    return out[:MAX_SKILLS_PER_STORY]


def _story(row: Mapping[str, Any]) -> dict[str, Any]:
    story = dict(row)
    try:
        story["skills"] = json.loads(story.get("skills") or "[]")
    except json.JSONDecodeError:
        story["skills"] = []
    return story


def list_stories(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    ensure_schema(connection)
    rows = connection.execute(
        "SELECT * FROM star_stories ORDER BY updated_at DESC, story_id LIMIT ?",
        (MAX_STORIES,),
    ).fetchall()
    return [_story(row) for row in rows]


def save_story(
    connection: sqlite3.Connection,
    payload: Mapping[str, Any],
    story_id: str | None = None,
) -> dict[str, Any]:
    """Create or update a story. ``title`` and at least one of action or result are required."""
    ensure_schema(connection)
    values = {
        name: _clean(payload.get(name), limit) for name, limit in STORY_FIELDS.items()
    }
    if not values["title"]:
        raise ValueError("Give the story a short title.")
    if not (values["action"] or values["result"]):
        raise ValueError("Write what you did (Action) or what came of it (Result).")
    skills = _skills(payload.get("skills"))
    source = "resume" if payload.get("source") == "resume" else "user"
    moment = _now()
    if story_id:
        found = connection.execute(
            "SELECT story_id FROM star_stories WHERE story_id = ?", (story_id,)
        ).fetchone()
        if found is None:
            raise LookupError("Story not found.")
        connection.execute(
            """UPDATE star_stories SET title = ?, situation = ?, task = ?, action = ?, result = ?, skills = ?,
            updated_at = ? WHERE story_id = ?""",
            (*values.values(), json.dumps(skills), moment, story_id),
        )
    else:
        count = connection.execute("SELECT COUNT(*) FROM star_stories").fetchone()[0]
        if count >= MAX_STORIES:
            raise ValueError(
                f"Keep at most {MAX_STORIES} stories; delete one you no longer use."
            )
        story_id = uuid.uuid4().hex
        connection.execute(
            """INSERT INTO star_stories (story_id, title, situation, task, action, result, skills, source,
            created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (story_id, *values.values(), json.dumps(skills), source, moment, moment),
        )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM star_stories WHERE story_id = ?", (story_id,)
    ).fetchone()
    return _story(row)


def delete_story(connection: sqlite3.Connection, story_id: str) -> bool:
    ensure_schema(connection)
    deleted = connection.execute(
        "DELETE FROM star_stories WHERE story_id = ?", (story_id,)
    ).rowcount
    connection.commit()
    return bool(deleted)


_RESULT_RE = re.compile(
    r"(?:,|;|\s-\s|\u2013|\u2014)?\s*((?:cutting|reducing|saving|increasing|improving|raising|growing|lifting|"
    r"lowering|resulting in|which|so that|leading to)\b.*)$",
    re.IGNORECASE,
)


def story_drafts(
    resume_text: str, skills: Sequence[str] = (), *, limit: int = 8
) -> list[dict[str, Any]]:
    """Draft stories from resume bullets: the bullet is the Action, a trailing outcome clause the Result.

    With ``skills`` (canonical names or labels) only bullets that mention one of them are used. Situation and
    Task stay empty for the person to write: Monosage does not invent the context.
    """
    doc = resume_render.parse_resume(resume_text)
    wanted = {str(skill).casefold() for skill in skills if str(skill).strip()}
    drafts: list[dict[str, Any]] = []
    for section in doc.sections:
        role = ""
        for block in section.blocks:
            if block.kind == "role":
                role = block.text
                continue
            if block.kind != "bullet":
                continue
            text = block.text.strip()
            found = find_skills(text)
            labels = [skill_label(skill) for skill in found]
            if (
                wanted
                and not ({s.casefold() for s in found} | {s.casefold() for s in labels})
                & wanted
            ):
                continue
            if not wanted and not found:
                continue
            outcome = _RESULT_RE.search(text)
            action = text[: outcome.start()].strip(" ,;") if outcome else text
            result = outcome.group(1).strip() if outcome else ""
            drafts.append(
                {
                    "title": (labels[0] + ": " if labels else "")
                    + (action[:70] + ("\u2026" if len(action) > 70 else "")),
                    "situation": f"At {role}." if role else "",
                    "task": "",
                    "action": action,
                    "result": result[:1].upper() + result[1:] if result else "",
                    "skills": labels[:MAX_SKILLS_PER_STORY],
                    "source": "resume",
                }
            )
            if len(drafts) >= limit:
                return drafts
    return drafts


# --------------------------------------------------------------------------- #
# Likely questions and the company brief
# --------------------------------------------------------------------------- #
_TYPE_QUESTIONS: dict[str, tuple[tuple[str, str], ...]] = {
    "recruiter_screen": (
        (
            "logistics",
            "What are your salary expectations? (Decide your range before the call; Monosage never answers this for you.)",
        ),
        ("logistics", "When could you start, and what is your notice period?"),
        ("logistics", "Do you need visa sponsorship to work in this location?"),
        ("role", "What are you looking for in your next role?"),
    ),
    "technical_interview": (
        (
            "technical",
            "Walk me through the architecture of a system you built. What would you change today?",
        ),
        (
            "technical",
            "How do you debug a production problem you cannot reproduce locally?",
        ),
        (
            "technical",
            "How do you decide between building something and using an existing tool or service?",
        ),
    ),
    "manager_interview": (
        (
            "behavioral",
            "Tell me about a time you disagreed with your manager or a stakeholder. What happened?",
        ),
        ("behavioral", "How do you prioritise when everything is urgent?"),
        ("role", "What would you want to achieve in your first 90 days?"),
    ),
    "panel_interview": (
        ("behavioral", "Tell me about a project that failed. What did you learn?"),
        ("behavioral", "Describe a time you worked across teams to deliver something."),
    ),
    "final_interview": (
        ("role", "Why should we hire you over other candidates?"),
        (
            "role",
            "Where do you want to be in three years, and how does this role get you there?",
        ),
    ),
    "assessment": (
        (
            "technical",
            "Explain the trade-offs you made in your assessment and what you would do with more time.",
        ),
    ),
}


def _question_id(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}:{text.casefold()}".encode()).hexdigest()[:12]


def _question(
    kind: str, text: str, *, skill: str = "", source: str = "rules"
) -> dict[str, Any]:
    return {
        "id": _question_id(kind, text),
        "kind": kind,
        "question": text,
        "skill": skill,
        "story_ids": [],
        "notes": "",
        "source": source,
    }


def _link_stories(
    questions: list[dict[str, Any]], stories: Sequence[Mapping[str, Any]]
) -> None:
    for question in questions:
        skill = str(question.get("skill") or "").casefold()
        if not skill:
            continue
        question["story_ids"] = [
            str(story["story_id"])
            for story in stories
            if skill in {str(s).casefold() for s in story.get("skills") or []}
        ][:4]


_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_VALUES_RE = re.compile(
    r"\b(value|values|culture|mission|believe|care about|committed to|diversity|inclusion)\b",
    re.IGNORECASE,
)
_RESPONSIBILITY_HEAD_RE = re.compile(
    r"\b(responsibilit|what you.ll do|what you will do|the role|your role|day to day|you will|key duties|duties)\b",
    re.IGNORECASE,
)
_HEAD_RE = re.compile(r"^\s*(?:#+\s*)?([A-Z][^.!?]{2,60}):?\s*$")


def _posting_parts(description: str) -> tuple[list[str], list[str], list[str]]:
    """``(about sentences, responsibility lines, value sentences)`` from a job posting."""
    lines = [line.strip() for line in str(description or "").splitlines()]
    responsibilities: list[str] = []
    in_duties = False
    prose: list[str] = []
    for line in lines:
        if not line:
            continue
        bullet = re.match(r"^(?:[-*\u2022\u00b7]|\d+[.)])\s+(.*)$", line)
        heading = _HEAD_RE.match(line) if not bullet else None
        if heading:
            in_duties = bool(_RESPONSIBILITY_HEAD_RE.search(heading.group(1)))
            continue
        if bullet:
            if in_duties and len(responsibilities) < 6:
                responsibilities.append(bullet.group(1).strip()[:240])
            continue
        prose.append(line)
    sentences = [
        s.strip()
        for s in _SENTENCE_RE.split(" ".join(prose))
        if 25 <= len(s.strip()) <= 320
    ]
    about = [
        s
        for s in sentences
        if re.search(r"\b(we|our|us|company|team)\b", s, re.IGNORECASE)
    ][:3] or sentences[:2]
    values = [s for s in sentences if _VALUES_RE.search(s)][:3]
    return about, responsibilities, values


def build_prep(
    job: Mapping[str, Any] | None,
    interview: Mapping[str, Any],
    *,
    stories: Sequence[Mapping[str, Any]] = (),
    resume_text: str = "",
    profile: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Likely questions for one interview and a company brief, from the posting, the resume and the stories."""
    job = job or {}
    company = str(
        interview.get("company") or job.get("company") or "the company"
    ).strip()
    role = str(interview.get("role") or job.get("title") or "this role").strip()
    kind = str(interview.get("interview_type") or "other")
    has_posting = bool(job.get("description") or job.get("required_skills"))
    required, preferred = job_skill_requirements(job) if has_posting else ([], [])

    have = candidate_skill_set(profile or {}, resume_text)
    strengths = [s for s in required if skill_evidenced(s, have, resume_text)]
    gaps = [s for s in required if not skill_evidenced(s, have, resume_text)]
    # Ways of working (ownership, mentoring) are asked about as stories; tools and methods as technical questions.
    soft = [s for s in required if s in SOFT_SKILLS]
    hard = [s for s in required if s not in SOFT_SKILLS]
    hard_strengths = [s for s in strengths if s not in SOFT_SKILLS]
    questions: list[dict[str, Any]] = [
        _question("opening", "Tell me about yourself."),
        _question("opening", f"Why do you want to work at {company}?"),
        _question("opening", f"Why are you interested in the {role} role?"),
    ]
    for skill in (hard_strengths or hard)[:4]:
        label = skill_label(skill)
        questions.append(
            _question(
                "behavioral",
                f"Tell me about a time you used {label} to solve a hard problem. What was the result?",
                skill=label,
            )
        )
    for skill in soft[:3]:
        label = skill_label(skill)
        questions.append(
            _question(
                "behavioral",
                f"The team values {label.lower()}. Tell me about a time you showed it.",
                skill=label,
            )
        )
    for skill in hard[:4]:
        label = skill_label(skill)
        questions.append(
            _question(
                "technical",
                f"How have you used {label} in production, and what would you do differently now?",
                skill=label,
            )
        )
    for skill in [s for s in gaps if s not in SOFT_SKILLS][:3]:
        label = skill_label(skill)
        questions.append(
            _question(
                "gap",
                f"This role uses {label}. How would you get up to speed, and what similar work have you done?",
                skill=label,
            )
        )
    for question_kind, text in _TYPE_QUESTIONS.get(kind, ()):
        questions.append(_question(question_kind, text))
    questions.append(_question("closing", "What questions do you have for us?"))
    seen: set[str] = set()
    unique = []
    for question in questions:
        if question["id"] not in seen:
            seen.add(question["id"])
            unique.append(question)
    _link_stories(unique, stories)

    about, responsibilities, values = _posting_parts(str(job.get("description") or ""))
    tech = [skill_label(s) for s in [*required, *preferred]][:14]
    asks = [
        f"What does success look like for the {role} in the first 90 days?",
        "What is the team working on now, and what is the hardest problem it faces?",
        "How are decisions made here, and how is work reviewed?",
        "How do people grow in this team: mentoring, training, promotion?",
    ]
    if values:
        asks.append(
            f"The posting talks about what {company} values. How does that show up day to day?"
        )
    research = {
        "company": company,
        "role": role,
        "summary": " ".join(about)[:700],
        "role_focus": responsibilities,
        "tech": tech,
        "values": values,
        "your_strengths": [skill_label(s) for s in strengths][:8],
        "your_gaps": [skill_label(s) for s in gaps][:8],
        "questions_to_ask": asks,
        "sources": ["The job posting"] if job.get("description") else [],
        "note": ""
        if job.get("description")
        else "Link the interview to a job with a description for a fuller brief.",
    }
    return {"questions": unique, "research": research, "generated_by": "rules"}


def merge_ai(
    prep: dict[str, Any],
    answer: Mapping[str, Any],
    stories: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """Add a model's questions and brief to a deterministic prep, marked ``source: "ai"``."""
    questions = list(prep.get("questions") or [])
    known = {question["id"] for question in questions}
    added = []
    for item in answer.get("questions") or []:
        if not isinstance(item, Mapping):
            continue
        text = _clean(item.get("question"), 400)
        kind = str(item.get("kind") or "role")
        if kind not in QUESTION_KINDS:
            kind = "role"
        if not text:
            continue
        question = _question(
            kind, text, skill=_clean(item.get("skill"), 60), source="ai"
        )
        if question["id"] in known:
            continue
        known.add(question["id"])
        added.append(question)
        if len(added) >= 10:
            break
    _link_stories(added, stories)
    research = dict(prep.get("research") or {})
    brief = (
        answer.get("research") if isinstance(answer.get("research"), Mapping) else {}
    )
    if brief:
        summary = _clean(brief.get("summary"), 900)
        if summary:
            research["ai_summary"] = summary
        for key in ("questions_to_ask", "values", "role_focus"):
            extra = [
                _clean(item, 300) for item in brief.get(key) or [] if _clean(item, 300)
            ]
            if extra:
                research[key] = list(
                    dict.fromkeys([*research.get(key, []), *extra[:5]])
                )
    return {
        **prep,
        "questions": [*questions, *added],
        "research": research,
        "generated_by": "rules+ai",
    }


def _prep_row(row: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if row is None:
        return None
    try:
        questions = json.loads(row["questions"] or "[]")
        research = json.loads(row["research"] or "{}")
    except json.JSONDecodeError:
        questions, research = [], {}
    return {
        "interview_id": row["interview_id"],
        "questions": questions,
        "research": research,
        "generated_by": row["generated_by"],
        "generated_at": row["generated_at"],
        "updated_at": row["updated_at"],
    }


def get_prep(
    connection: sqlite3.Connection, interview_id: str
) -> dict[str, Any] | None:
    ensure_schema(connection)
    return _prep_row(
        connection.execute(
            "SELECT * FROM interview_prep WHERE interview_id = ?", (interview_id,)
        ).fetchone()
    )


def save_prep(
    connection: sqlite3.Connection, interview_id: str, prep: Mapping[str, Any]
) -> dict[str, Any]:
    """Store a (re)built prep. Practice notes already written for the same questions are kept."""
    ensure_schema(connection)
    old = get_prep(connection, interview_id)
    notes = {
        q["id"]: q.get("notes", "")
        for q in (old or {}).get("questions", [])
        if q.get("notes")
    }
    questions = [
        dict(q, notes=notes.get(q["id"], q.get("notes", "")))
        for q in prep.get("questions") or []
    ]
    moment = _now()
    connection.execute(
        """INSERT INTO interview_prep (interview_id, questions, research, generated_by, generated_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(interview_id) DO UPDATE SET questions = excluded.questions,
        research = excluded.research, generated_by = excluded.generated_by, generated_at = excluded.generated_at,
        updated_at = excluded.updated_at""",
        (
            interview_id,
            json.dumps(questions, ensure_ascii=False),
            json.dumps(prep.get("research") or {}, ensure_ascii=False),
            str(prep.get("generated_by") or "rules"),
            moment,
            moment,
        ),
    )
    connection.commit()
    saved = get_prep(connection, interview_id)
    assert saved is not None
    return saved


def save_question_notes(
    connection: sqlite3.Connection, interview_id: str, notes: Mapping[str, Any]
) -> dict[str, Any]:
    """Save the person's practice notes, keyed by question id."""
    prep = get_prep(connection, interview_id)
    if prep is None:
        raise LookupError("Prepare this interview first.")
    questions = prep["questions"]
    known = {q["id"] for q in questions}
    for key, value in notes.items():
        if key not in known:
            raise ValueError("That question is not part of this interview's prep.")
        text = _clean(value, NOTE_LIMIT)
        for question in questions:
            if question["id"] == key:
                question["notes"] = text
    connection.execute(
        "UPDATE interview_prep SET questions = ?, updated_at = ? WHERE interview_id = ?",
        (json.dumps(questions, ensure_ascii=False), _now(), interview_id),
    )
    connection.commit()
    saved = get_prep(connection, interview_id)
    assert saved is not None
    return saved
