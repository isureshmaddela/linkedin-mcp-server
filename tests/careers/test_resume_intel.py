"""Resume intelligence (aira/resume_intel.py) and its routes: ATS readiness, missing keywords, the change summary
of a tailored version, the skill-gap view across jobs and certification suggestions. The model is a stub."""

# ruff: noqa: F811
from __future__ import annotations

import io

import pytest
from pypdf import PdfReader

import auto_apply_assistant as app
from aira import resume_intel as ri
from test_career_copilot import import_job, register, server  # noqa: F401

MASTER = """Jordan Example
jordan@example.com | +1 555 010 2030 | Seattle, WA | linkedin.com/in/jordan-example

## Experience
**Platform Engineer**, Acme | 2019 - Present
- Ran Kubernetes clusters for 40 services.
- Wrote Terraform for AWS networking.

## Education
B.Sc. Computer Science, University of Washington | 2016

## Skills
Kubernetes, Terraform, AWS, Python
"""
JOB = {
    "title": "Senior Platform Engineer",
    "description": "Requirements:\n- Kubernetes\n- Terraform\n- Kafka\n- Python\nNice to have:\n- GCP\n- Python",
}


def ok(client, method, path, body=None, expect=(200, 201)):
    status, data, _ = client.call(method, path, body)
    assert status in expect, (status, data)
    return data


def test_ats_readiness_scores_each_check_and_lists_the_fixes():
    report = ri.ats_readiness(MASTER, job=JOB)
    checks = {item["id"]: item for item in report["checks"]}
    assert set(checks) == {"structure", "headings", "contact", "keywords", "length"}
    assert checks["headings"]["passed"] and checks["contact"]["passed"]
    assert checks["keywords"]["points"] < 30 and "Kafka" in checks["keywords"]["fix"]
    assert (
        not checks["length"]["passed"] and "350 to 900 words" in checks["length"]["fix"]
    )
    assert report["fixes"] and 0 < report["score"] < 100 and report["grade"]
    # Without a job the keyword check is left out of the total instead of counting as zero.
    alone = ri.ats_readiness(MASTER)
    assert "keywords" not in {item["id"] for item in alone["checks"]}
    assert not alone["keywords_scored"]
    bare = ri.ats_readiness("Did some work.\nMore work.")
    by_id = {item["id"]: item for item in bare["checks"]}
    assert (
        not by_id["contact"]["passed"] and "an email address" in by_id["contact"]["fix"]
    )
    assert not by_id["headings"]["passed"] and bare["score"] < 40


def test_missing_keywords_say_whether_the_master_resume_already_has_them():
    tailored = MASTER.replace("Python", "Go")
    out = ri.missing_keywords(tailored, JOB, master_text=MASTER)
    required = {item["label"]: item for item in out["required"]}
    assert set(required) == {"Kafka", "Python"}
    assert required["Python"]["in_master"] and not required["Kafka"]["in_master"]
    assert "bring it into this version" in required["Python"]["advice"]
    assert [item["label"] for item in out["preferred"]] == ["Google Cloud"]
    assert out["counts"] == {"required": 4, "preferred": 1}


def test_the_change_summary_flags_claims_the_master_resume_does_not_make():
    tailored = MASTER.replace(
        "Ran Kubernetes clusters for 40 services.",
        "Ran Kubernetes clusters for 40 services, cutting cost 30% with Kafka.",
    ).replace("## Education", "## Certifications\nCKA 2023\n\n## Education")
    summary = ri.change_summary(MASTER, tailored)
    # The new certification line is a claim too (CKA reads as a Kubernetes certification).
    assert summary["skills"]["new"] == ["Kafka", "Kubernetes certification"]
    assert summary["new_figures"] == ["30%"]
    assert summary["sections"]["added"] == ["Certifications"]
    assert summary["lines"]["reworded"] >= 1 and summary["has_master"]
    same = ri.change_summary(MASTER, MASTER)
    assert same["similarity"] == 1.0 and not same["skills"]["new"]


def test_skill_gaps_rank_the_skills_that_unlock_jobs_and_suggest_certifications():
    jobs = [
        {
            "job_id": "a",
            "title": "Platform Engineer",
            "company": "Acme",
            **{k: JOB[k] for k in ("description",)},
        },
        {
            "job_id": "b",
            "title": "SRE",
            "company": "Beta",
            "description": "Requirements:\n- Kafka\n- Kubernetes",
        },
        {
            "job_id": "c",
            "title": "Data Engineer",
            "company": "Gamma",
            "description": "Requirements:\n- Kafka\n- Spark\n- Snowflake",
        },
        {"job_id": "d", "title": "No skills", "description": "A friendly team."},
    ]
    out = ri.skill_gaps(jobs, resume_text=MASTER)
    assert out["jobs_considered"] == 3 and out["jobs_without_gaps"] == 0
    first = out["skills"][0]
    assert first["label"] == "Kafka" and first["required"] == 3
    assert {job["job_id"] for job in first["unlocks"]} == {"a", "b"}
    assert first["certifications"][0]["issuer"] == "Confluent"
    assert "Kubernetes" in out["your_skills"]
    certs = ri.certification_suggestions(["kubernetes", "aws", "kubernetes"])
    assert [c["name"] for c in certs][:2] == [
        "Certified Kubernetes Administrator (CKA)",
        "Certified Kubernetes Application Developer (CKAD)",
    ]
    assert len({c["name"] for c in certs}) == len(certs)


@pytest.fixture
def model(monkeypatch):
    calls: list[str] = []

    def fake(model_name: str, prompt: str):
        calls.append(prompt)
        return {
            "certifications": [
                {
                    "name": "Confluent Certified Developer for Apache Kafka",
                    "issuer": "Confluent",
                    "skills": ["Kafka"],
                },
                {
                    "name": "Kafka Streams Specialist",
                    "issuer": "Example Body",
                    "skills": ["Kafka"],
                    "why": "Streams.",
                },
            ]
        }

    monkeypatch.setattr(app, "_ollama_json", fake)
    return calls


def test_routes_check_the_editor_text_and_draw_the_preview(server):
    register(server)
    job_id = import_job(server)["job_id"]
    check = ok(server, "POST", f"/api/jobs/{job_id}/documents/check", {"text": MASTER})
    assert set(check) == {"ats", "keywords", "changes"}
    assert check["ats"]["keywords_scored"] and check["changes"]["has_master"]
    preview = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/preview",
        {"doc": "resume", "text": MASTER},
    )
    assert preview["page_count"] == 1 and preview["outline"]["name"] == "Jordan Example"
    assert preview["templates"]["docx"]["available"] is False
    texts = [item["text"] for item in preview["pages"][0] if item["t"] == "text"]
    assert "EXPERIENCE" in texts
    letter = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/preview",
        {"doc": "cover_letter", "text": "Dear Acme,\n\nHello."},
    )
    # The letter's header comes from the profile (first and last name).
    assert "Jordan Example" in [item.get("text") for item in letter["pages"][0]]
    status, data, headers = server.call(
        "POST",
        f"/api/jobs/{job_id}/documents/file",
        {"doc": "resume", "text": MASTER},
        raw=True,
    )
    assert status == 200 and headers["Content-Type"] == "application/pdf"
    assert "Resume - Acme.pdf" in headers["Content-Disposition"]
    assert (
        "Platform Engineer, Acme" in PdfReader(io.BytesIO(data)).pages[0].extract_text()
    )
    status, body, _ = server.call(
        "POST",
        f"/api/jobs/{job_id}/documents/file",
        {"doc": "resume", "text": MASTER, "format": "docx"},
    )
    assert status == 400 and "Word" in body["error"]
    # Approving in Word needs a Word master resume; PDF approval records the format.
    status, body, _ = server.call(
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {
            "resume": MASTER,
            "cover_letter": "Dear Acme",
            "approve": True,
            "format": "docx",
        },
    )
    assert status == 400
    saved = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": MASTER, "cover_letter": "Dear Acme", "approve": True},
    )
    assert saved["approved"]["resume"]["upload_format"] == "pdf"
    status, body, _ = server.call(
        "POST", f"/api/jobs/{job_id}/documents/preview", {"doc": "notes", "text": "x"}
    )
    assert status == 400


def test_routes_list_skill_gaps_and_certifications_with_and_without_ai(
    server, model, monkeypatch
):
    register(server)
    import_job(server)
    ok(
        server,
        "POST",
        "/api/jobs/import",
        {
            "manual": {
                "title": "Streaming Engineer",
                "company": "Beta",
                "description": "Requirements:\n- Kafka\n- Kubernetes",
            }
        },
    )
    gaps = ok(server, "GET", "/api/resume-intel/skill-gaps")
    assert gaps["scope"] == "active" and gaps["jobs_considered"] == 2
    kafka = next(item for item in gaps["skills"] if item["skill"] == "kafka")
    assert kafka["unlocks"][0]["title"] == "Streaming Engineer"
    plain = ok(
        server,
        "POST",
        "/api/resume-intel/certifications",
        {"skills": ["kafka"], "use_ai": True},
    )
    assert (
        plain["curated"]
        and plain["ai"] == []
        and "No AI model" in plain["note"]
        and not model
    )
    monkeypatch.setattr(app, "_ollama_available", lambda: True)
    both = ok(
        server,
        "POST",
        "/api/resume-intel/certifications",
        {"skills": ["kafka"], "use_ai": True, "model": "m"},
    )
    assert [item["name"] for item in both["ai"]] == [
        "Kafka Streams Specialist"
    ]  # the curated one is not repeated
    assert both["ai"][0]["source"] == "ai" and "Kafka" in model[0]
    status, body, _ = server.call(
        "POST", "/api/resume-intel/certifications", {"skills": ["<script>"]}
    )
    assert status == 400
