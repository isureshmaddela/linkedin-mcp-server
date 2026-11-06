"""Bulk actions (F16): one action on many jobs (save, archive, restore, stage, score) and on many Needs you items
(skip, try again). Each item answers for itself, so one that cannot change does not stop the rest."""

# ruff: noqa: F811
from __future__ import annotations

import auto_apply_assistant as app
from aira import autopilot
from test_career_copilot import import_job, register, server  # noqa: F401


def ok(client, method, path, body=None, expect=(200, 201, 202)):
    status, data, _ = client.call(method, path, body)
    assert status in expect, (status, data)
    return data


def _manual(client, title: str, description: str = "") -> str:
    out = ok(
        client,
        "POST",
        "/api/jobs/import",
        {"manual": {"title": title, "company": "Beta", "description": description}},
    )
    return out["job"]["job_id"]


def test_bulk_job_actions_report_per_job(server):
    register(server)
    first = import_job(server)["job_id"]
    second = _manual(server, "Streaming Engineer", "Requirements:\n- Kafka")
    third = _manual(server, "No description yet")
    ids = [first, second, third]

    saved = ok(server, "POST", "/api/jobs/bulk", {"action": "save", "job_ids": ids})
    assert saved["done"] == 3 and saved["failed"] == 0
    listed = ok(server, "GET", "/api/jobs?view=saved")
    assert {job["job_id"] for job in listed["jobs"]} == set(ids)

    scored = ok(server, "POST", "/api/jobs/bulk", {"action": "score", "job_ids": ids})
    assert scored["done"] == 2 and scored["failed"] == 1
    assert next(r for r in scored["results"] if r["job_id"] == third)[
        "error"
    ].startswith("Load the job description")

    moved = ok(
        server,
        "POST",
        "/api/jobs/bulk",
        {"action": "stage", "stage": "needs_review", "job_ids": [first, second]},
    )
    assert moved["done"] == 2
    assert ok(server, "GET", f"/api/jobs/{first}")["job"]["stage"] == "needs_review"
    # A move the pipeline does not allow fails for that job only.
    odd = ok(
        server,
        "POST",
        "/api/jobs/bulk",
        {"action": "stage", "stage": "offer", "job_ids": [first, third]},
    )
    assert odd["failed"] >= 1 and all(
        "error" in r for r in odd["results"] if not r["ok"]
    )

    archived = ok(
        server,
        "POST",
        "/api/jobs/bulk",
        {"action": "archive", "job_ids": [second, third]},
    )
    assert archived["done"] == 2
    assert {
        job["job_id"] for job in ok(server, "GET", "/api/jobs?view=all")["jobs"]
    } == {first}
    restored = ok(
        server,
        "POST",
        "/api/jobs/bulk",
        {"action": "restore", "job_ids": [second, third]},
    )
    assert restored["done"] == 2
    assert len(ok(server, "GET", "/api/jobs?view=all")["jobs"]) == 3

    unknown = ok(
        server,
        "POST",
        "/api/jobs/bulk",
        {"action": "unsave", "job_ids": ["nope", first]},
    )
    assert unknown["results"][0] == {
        "job_id": "nope",
        "ok": False,
        "error": "Job not found.",
    }

    for body, message in (
        ({"action": "explode", "job_ids": ids}, "Choose save"),
        ({"action": "save", "job_ids": []}, "at least one"),
        ({"action": "save", "job_ids": [f"j{i}" for i in range(201)]}, "at most 200"),
        ({"action": "stage", "stage": "nowhere", "job_ids": ids}, "status"),
        ({"action": "save", "job_ids": ["../etc"]}, "not valid"),
    ):
        status, data, _ = server.call("POST", "/api/jobs/bulk", body)
        assert status == 400 and message in data["error"], (body, data)


def test_bulk_needs_you_skips_and_retries_each_run(server, monkeypatch):
    register(server)
    first = import_job(server)["job_id"]
    second = _manual(server, "Second job", "Requirements:\n- Kafka")
    with app._connect() as connection:
        runs = []
        for job_id, status in ((first, "interrupted"), (second, "needs_review")):
            run = autopilot.create_run(
                connection, job={"job_id": job_id}, mode="review", trigger="manual"
            )
            autopilot.update_run(connection, run["run_id"], {"status": status})
            runs.append(run["run_id"])
    started: list[str] = []
    monkeypatch.setattr(
        app,
        "_autopilot_start",
        lambda user, label, work, spec=None: started.append(label) or {"started": True},
    )

    retried = ok(
        server, "POST", "/api/autopilot/runs/bulk", {"action": "retry", "run_ids": runs}
    )
    assert retried["done"] == 1 and retried["failed"] == 1
    assert "Only an interrupted or failed" in retried["results"][1]["error"]
    assert started == ["Trying the application again"]

    skipped = ok(
        server,
        "POST",
        "/api/autopilot/runs/bulk",
        {"action": "skip", "run_ids": [runs[1]]},
    )
    assert skipped["done"] == 1
    with app._connect() as connection:
        assert autopilot.get_run(connection, runs[1])["status"] == "skipped"
    status, data, _ = server.call(
        "POST", "/api/autopilot/runs/bulk", {"action": "submit", "run_ids": runs}
    )
    assert status == 400
