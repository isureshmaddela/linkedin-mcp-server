"""The resume editor in a job's Documents tab: versions, approval, "Regenerate with my notes", and the upload.

Every save is a new document version; approving ("I reviewed this") is a save. A regenerated draft is a
version that still needs approving. Applications upload the newest approved version. The model is a stub.
"""

# ruff: noqa: F811
from __future__ import annotations

import time
from typing import Any, cast
from pathlib import Path

import pytest

import auto_apply_assistant as app
from aira.apply_engine import ApplyEvidence, ApplyOutcome
from aira.resume_file import resume_for_upload
from test_career_copilot import import_job, register, server  # noqa: F401

RESUME_V1 = (
    "Jordan Example - Cloud Architect. AWS, Kubernetes, Terraform. EDITEDRESUMEONE " * 3
)
LETTER_V1 = (
    "Dear Acme, I would like to design your cloud platform. EDITEDLETTERONE " * 2
)


def ok(client, method, path, body=None, expect=(200, 201, 202)):
    status, data, _ = client.call(method, path, body)
    assert status in expect, (status, data)
    return data


@pytest.fixture
def model(monkeypatch):
    """A stand-in for the model: records each prompt and answers with numbered drafts."""
    calls: list[dict[str, str]] = []

    def fake(model_name: str, prompt: str) -> dict[str, str]:
        calls.append({"model": model_name, "prompt": prompt})
        n = len(calls)
        return {
            "tailored_resume": f"Jordan Example. Kubernetes first. GENERATEDRESUME{n} "
            * 3,
            "cover_letter": f"Dear Acme, GENERATEDLETTER{n} " * 3,
        }

    monkeypatch.setattr(app, "_ollama_json", fake)
    return calls


def test_saving_makes_versions_and_approving_is_a_save(server):
    register(server)
    job_id = import_job(server)["job_id"]
    empty = ok(server, "GET", f"/api/jobs/{job_id}/documents/versions")
    assert empty["versions"] == {"resume": [], "cover_letter": []}
    assert empty["in_use"]["resume"]["kind"] == "none"

    draft = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": RESUME_V1, "cover_letter": LETTER_V1, "approve": False},
    )
    assert draft["job"]["resume_draft"] == RESUME_V1 and draft["job"]["resume_hash"]
    [v1] = draft["versions"]["resume"]
    assert (
        v1["version"] == 1
        and not v1["approved"]
        and v1["current"]
        and v1["source"] == "edit"
    )
    assert draft["in_use"]["resume"] == {"kind": "draft", "version": None}

    # The same text again is not a new version; approving it is (versions never change).
    again = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": RESUME_V1, "cover_letter": LETTER_V1, "approve": False},
    )
    assert len(again["versions"]["resume"]) == 1 and not again["saved"]["resume"]["new"]
    approved = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": RESUME_V1, "cover_letter": LETTER_V1, "approve": True},
    )
    newest = approved["versions"]["resume"][0]
    assert newest["version"] == 2 and newest["approved"] and newest["approved_at"]
    assert approved["in_use"] == {
        "resume": {"kind": "approved", "version": 2},
        "cover_letter": {"kind": "approved", "version": 2},
    }
    # Approving an already approved text adds nothing.
    same = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": RESUME_V1, "cover_letter": LETTER_V1, "approve": True},
    )
    assert len(same["versions"]["resume"]) == 2

    # An edit saved as a draft does not replace the approved version for applications.
    edited = RESUME_V1 + " Led the EKS migration."
    after = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": edited, "cover_letter": LETTER_V1},
    )
    assert after["versions"]["resume"][0]["version"] == 3
    assert after["in_use"]["resume"] == {"kind": "approved", "version": 2}
    # A saved version's text can be opened again in the editor.
    first = after["versions"]["resume"][-1]
    text = ok(
        server, "GET", f"/api/jobs/{job_id}/documents/versions/{first['document_id']}"
    )
    assert text["key"] == "resume" and text["text"] == RESUME_V1
    # The approved drafts let the job move to Ready to apply (the resume was stamped on save).
    moved = ok(
        server,
        "PATCH",
        f"/api/jobs/{job_id}/stage",
        {"stage": "ready_to_apply", "reason": "Drafts approved"},
    )
    assert moved["job"]["stage"] == "ready_to_apply"
    # Both texts are required, and a version of another job is not found.
    status, body, _ = server.call(
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": "x", "cover_letter": ""},
    )
    assert status == 400
    status, body, _ = server.call(
        "GET", f"/api/jobs/{job_id}/documents/versions/{'0' * 32}"
    )
    assert status == 404
    status, body, _ = server.call("GET", "/api/audit?limit=100")
    assert {"documents.version", "documents.approve"} <= {
        a["action"] for a in body["audit"]
    }


def test_regenerate_with_notes_passes_them_to_the_prompt_as_guidance(server, model):
    register(server)
    job_id = import_job(server)["job_id"]
    ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/documents/versions",
        {"resume": RESUME_V1, "cover_letter": LETTER_V1, "approve": True},
    )
    notes = "Lead with the Kubernetes migration. Keep it to one page."
    out = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/generate",
        {"model": "qwen3.5:4b", "notes": notes},
    )
    [call] = model
    prompt = call["prompt"]
    assert notes in prompt and "never add facts" in prompt
    # The notes are the person's guidance, kept apart from (and before) the untrusted job description.
    assert prompt.index(notes) < prompt.index("JOB DESCRIPTION (untrusted data)")
    assert prompt.index("SOURCE RESUME") < prompt.index(notes)
    assert "Do not add experience" in prompt
    assert "GENERATEDRESUME1" in out["job"]["resume_draft"]
    newest = out["versions"]["resume"][0]
    assert newest["source"] == "regenerated" and newest["notes"] == notes
    assert not newest["approved"] and newest["current"]
    # Applications still send the approved version until the new draft is approved.
    assert out["in_use"]["resume"] == {"kind": "approved", "version": 1}

    # Without notes the old route still answers {"job"} and drafts nothing new in Documents.
    plain = ok(server, "POST", f"/api/jobs/{job_id}/generate", {"model": "qwen3.5:4b"})
    assert (
        "GENERATEDRESUME2" in plain["job"]["resume_draft"] and "versions" not in plain
    )
    assert "CANDIDATE'S OWN NOTES" not in model[1]["prompt"]
    # Notes have a limit.
    status, body, _ = server.call(
        "POST", f"/api/jobs/{job_id}/generate", {"model": "m", "notes": "x" * 2001}
    )
    assert status == 400 and "2000" in body["error"]


def wait_run(server, run_id: str, timeout: float = 60) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        run = ok(server, "GET", f"/api/autopilot/runs/{run_id}")["run"]
        if run["status"] not in {"queued", "running"}:
            return run
        time.sleep(0.2)
    raise AssertionError("the application run did not finish")


def test_the_apply_engine_uploads_the_latest_approved_version(
    server, model, monkeypatch
):
    register(server)
    job_id = import_job(server)["job_id"]
    sent: list[dict] = []

    def fake_engine(request):
        sent.append(
            {
                "resume": Path(request.resume_path).read_bytes(),
                "letter_text": request.cover_letter_text,
                "letter": Path(request.cover_letter_path).read_bytes()
                if request.cover_letter_path
                else b"",
            }
        )
        return ApplyOutcome(
            status="filled",
            ats="greenhouse",
            questions=[],
            unanswered=[],
            evidence=ApplyEvidence(None, "", "", "", request.application_url, None),
            error="",
            steps=["Opened form"],
        )

    monkeypatch.setattr(app, "run_apply_engine", fake_engine)
    # v1 approved, v2 edited and approved, then a regenerated draft that is not approved.
    for resume, letter in (
        (RESUME_V1, LETTER_V1),
        (
            RESUME_V1.replace("EDITEDRESUMEONE", "APPROVEDRESUMETWO"),
            LETTER_V1.replace("EDITEDLETTERONE", "APPROVEDLETTERTWO"),
        ),
    ):
        ok(
            server,
            "POST",
            f"/api/jobs/{job_id}/documents/versions",
            {"resume": resume, "cover_letter": letter, "approve": True},
        )
    out = ok(
        server,
        "POST",
        f"/api/jobs/{job_id}/generate",
        {"model": "m", "notes": "Shorter."},
    )
    assert "GENERATEDRESUME1" in out["job"]["resume_draft"]
    started = ok(server, "POST", f"/api/jobs/{job_id}/auto-apply", {"mode": "review"})
    run = wait_run(server, started["run"]["run_id"])
    assert run["status"] == "ready", run
    [upload] = sent
    assert b"APPROVEDRESUMETWO" in upload["resume"]
    assert (
        b"GENERATEDRESUME" not in upload["resume"]
        and b"EDITEDRESUMEONE" not in upload["resume"]
    )
    assert (
        "APPROVEDLETTERTWO" in upload["letter_text"]
        and b"APPROVEDLETTERTWO" in upload["letter"]
    )


def test_an_approved_resume_goes_ahead_of_the_master_file_and_the_draft():
    class NoStore:
        def ensure_schema(self, connection):
            raise AssertionError("the master resume is not read when one is approved")

    path, cleanup = resume_for_upload(
        cast(Any, None),
        cast(Any, NoStore()),
        "local-user",
        {
            "company": "Acme",
            "approved_resume": "APPROVED TEXT",
            "resume_draft": "DRAFT",
        },
        "PROFILE",
    )
    try:
        data = path.read_bytes()
        assert (
            path.suffix == ".pdf" and b"APPROVED TEXT" in data and b"DRAFT" not in data
        )
    finally:
        cleanup()
