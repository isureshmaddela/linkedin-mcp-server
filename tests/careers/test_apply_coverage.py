"""What the browser engine is given: the employer's own apply link from Google Jobs, and the packet answers.

* Google lists aggregators (LinkedIn, Indeed, ...) ahead of the employer more often than not; the job's
  application link is now the most direct option: a known application system, then the employer's own site.
* Automatic applications fill from the same packet as the manual path: the approved packet when there is one,
  otherwise the employer's own Greenhouse questions mapped and prefilled from the profile and confirmed answers.
"""

from __future__ import annotations

import pytest
from test_autopilot import DESCRIPTION, _bound_user, server, sign_up  # noqa: F401

import auto_apply_assistant as app
from aira.apply_engine import ApplyEvidence, ApplyOutcome
from linkedin_mcp_server.job_sources import (
    GoogleJobsAdapter,
    apply_link_rank,
    best_apply_link,
)

# --------------------------------------------------------------------------- Google Jobs apply options


def options(*links: str) -> list[dict[str, str]]:
    return [{"title": f"Apply on {i}", "link": link} for i, link in enumerate(links)]


def test_known_application_systems_win_over_aggregators():
    links = options(
        "https://www.linkedin.com/jobs/view/123",
        "https://www.indeed.com/viewjob?jk=abc",
        "https://boards.greenhouse.io/acme/jobs/42",
    )
    assert best_apply_link(links, "Acme") == "https://boards.greenhouse.io/acme/jobs/42"


def test_the_employers_own_site_wins_over_an_unknown_board():
    links = options(
        "https://www.ziprecruiter.com/c/Acme/Job/1",
        "https://jobs.somewhere-else.example/acme-cloud-architect",
        "https://careers.acmerobotics.com/jobs/7",
    )
    assert (
        best_apply_link(links, "Acme Robotics, Inc.")
        == "https://careers.acmerobotics.com/jobs/7"
    )
    assert (
        apply_link_rank("https://careers.acmerobotics.com/jobs/7", "Acme Robotics") == 1
    )
    # a generic word of a company's name is not a match ("Global Solutions" says nothing about a host)
    assert apply_link_rank("https://global.example.net/x", "Global Solutions") == 2


def test_order_is_kept_among_equals_and_bad_input_is_harmless():
    links = options("https://jobs.lever.co/acme/1", "https://jobs.ashbyhq.com/acme/2")
    assert best_apply_link(links, "") == "https://jobs.lever.co/acme/1"
    assert best_apply_link([], "Acme") == ""
    assert best_apply_link("not a list", "Acme") == ""
    assert best_apply_link([{"title": "no link"}, None, {"link": " "}], "Acme") == ""
    workday = (
        "https://acme.wd5.myworkdayjobs.com/en-US/External/job/Austin/Architect_R1"
    )
    assert apply_link_rank(workday) == 0
    assert apply_link_rank("https://uk.linkedin.com/jobs/view/1") == 3


def test_a_google_jobs_posting_applies_on_the_direct_link():
    posting = GoogleJobsAdapter("normalize-only").normalize(
        {
            "job_id": "abc123",
            "title": "Cloud Architect",
            "company_name": "Acme",
            "share_link": "https://www.google.com/search?q=acme&htidocid=abc",
            "apply_options": options(
                "https://www.linkedin.com/jobs/view/9",
                "https://jobs.lever.co/acme/1b2c",
            ),
            "description": "Build things.",
        }
    )
    assert posting.application_url == "https://jobs.lever.co/acme/1b2c"
    assert posting.source_url.startswith("https://www.google.com/")


# --------------------------------------------------------------------------- packet answers in automatic runs


# The shape aira.ats.fetch_greenhouse_questions returns.
GREENHOUSE_QUESTIONS = [
    {
        "label": "First Name",
        "required": True,
        "type": "text",
        "options": [],
        "field_name": "first_name",
    },
    {
        "label": "Why do you want to work at Acme?",
        "required": False,
        "type": "textarea",
        "options": [],
        "field_name": "question_4242",
    },
]


@pytest.fixture
def greenhouse_job(server, monkeypatch):  # noqa: F811
    sign_up(server)
    _bound_user(server)
    calls: list[tuple[str, str]] = []

    def questions(board, job_id):
        calls.append((board, job_id))
        return [dict(q) for q in GREENHOUSE_QUESTIONS]

    monkeypatch.setattr(app, "fetch_greenhouse_questions", questions)
    engine: list = []

    def fake_engine(request):
        engine.append(request)
        return ApplyOutcome(
            status="filled",
            ats="greenhouse",
            questions=[],
            unanswered=[],
            evidence=ApplyEvidence(None, "", "", "", request.application_url, None),
            error="",
            steps=["Filled the form"],
        )

    monkeypatch.setattr(app, "run_apply_engine", fake_engine)
    try:
        job = app._upsert_job(
            "4242",
            source="greenhouse",
            source_url="https://boards.greenhouse.io/acme/jobs/4242",
            application_url="https://boards.greenhouse.io/acme/jobs/4242",
            title="Cloud Architect",
            company="Acme",
            description=DESCRIPTION,
        )
        yield app._autopilot_job(job["job_id"]), calls, engine
    finally:
        app._unbind_user()


def test_automatic_runs_fill_from_the_employers_greenhouse_questions(greenhouse_job):
    job, calls, engine = greenhouse_job
    outcome = app._apply_with_engine(
        job, job["application_url"], "greenhouse", {}, False
    )
    assert outcome["status"] == "filled"
    assert calls == [("acme", "4242")], (
        "the question API was asked, like the manual path"
    )
    fields = {field["provider_field_id"]: field for field in engine[0].fields}
    assert fields["first_name"]["status"] == "prefilled"
    assert fields["first_name"]["value"] == "Jordan"
    # a question Monosage cannot answer is passed along, never answered by guesswork
    assert fields["question_4242"]["status"] != "prefilled"
    assert not fields["question_4242"]["value"]


def test_an_approved_packet_is_used_as_it_is(greenhouse_job):
    job, calls, engine = greenhouse_job
    preparation = app._prepare_application(job["job_id"])
    with app._connect() as connection:
        connection.execute(
            "UPDATE application_preparations SET status = 'approved' WHERE prep_id = ?",
            (preparation["prep_id"],),
        )
        connection.commit()
    calls.clear()
    app._apply_with_engine(job, job["application_url"], "greenhouse", {}, False)
    assert calls == [], "the approved packet already holds the employer's questions"
    assert [f["provider_field_id"] for f in engine[0].fields] == [
        f["provider_field_id"] for f in preparation["fields"]
    ]


def test_a_draft_packet_is_not_used_and_failures_fall_back_to_the_live_form(
    greenhouse_job, monkeypatch
):
    job, calls, engine = greenhouse_job
    app._prepare_application(job["job_id"])  # prepared, never approved
    calls.clear()
    app._apply_with_engine(job, job["application_url"], "greenhouse", {}, False)
    assert calls == [("acme", "4242")], "a draft is not the person's approved answers"

    def broken(board, job_id):
        raise RuntimeError("Greenhouse is down")

    monkeypatch.setattr(app, "fetch_greenhouse_questions", broken)
    engine.clear()
    outcome = app._apply_with_engine(
        job, job["application_url"], "greenhouse", {}, False
    )
    assert outcome["status"] == "filled" and engine[0].fields == []
