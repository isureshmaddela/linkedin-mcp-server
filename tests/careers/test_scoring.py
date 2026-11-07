from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from aira.scoring import (
    CLASSIFICATIONS,
    SCORING_VERSION,
    SKILL_LEXICON,
    ScoringWeights,
    classify,
    detect_seniority,
    extract_job_skills,
    extract_required_years,
    score_job,
)
from linkedin_mcp_server.matching import MATCHING_VERSION, score_keyword_evidence

REQUIRED_PREFERRED_DESCRIPTION = """Senior Platform Engineer
We run a large ML platform for the business.
Requirements:
- 5+ years of experience with Python and K8s
- Strong C++ and AWS (EKS) background
- Experience with Terraform is a plus
Nice to have:
- Go (golang) and Rust
- Python scripting
Benefits:
- Salesforce stock options and great perks
"""


def _dimensions(result: dict) -> dict[str, dict]:
    return {dimension["name"]: dimension for dimension in result["dimensions"]}


# --------------------------------------------------------------------------- #
# Skill extraction
# --------------------------------------------------------------------------- #
def test_extract_job_skills_splits_required_and_preferred_sections():
    required, preferred = extract_job_skills(REQUIRED_PREFERRED_DESCRIPTION)

    assert "python" in required
    assert "kubernetes" in required  # "K8s" alias
    assert "cpp" in required  # "C++" symbol alias
    assert "aws" in required and "eks" in required
    # inline "is a plus" marker demotes the line to preferred
    assert "terraform" in preferred
    assert "go" in preferred and "rust" in preferred
    # mentioned in both sections -> required only
    assert "python" not in preferred
    # "Benefits" section is ignored entirely
    assert "salesforce" not in required and "salesforce" not in preferred


def test_extract_job_skills_handles_symbol_aliases_and_whole_words():
    required, preferred = extract_job_skills(
        "Requirements: C#, .NET, Node.js and JavaScript. Familiarity with Java is a plus."
    )

    assert ("csharp", "dotnet", "node", "javascript") == required
    assert preferred == ("java",)


def test_extract_job_skills_returns_empty_tuples_without_skills():
    assert extract_job_skills("") == ((), ())
    assert extract_job_skills("We are a friendly team.") == ((), ())


def test_skill_lexicon_is_large_unique_and_whole_word():
    assert len(SKILL_LEXICON) >= 180
    seen: dict[str, str] = {}
    for canonical, aliases in SKILL_LEXICON.items():
        assert aliases, canonical
        for alias in aliases:
            key = alias.casefold()
            assert key not in seen, (
                f"alias {alias!r} in both {seen.get(key)} and {canonical}"
            )
            seen[key] = canonical
    required, _ = extract_job_skills("Requirements: JavaScript expertise.")
    assert required == ("javascript",)


# --------------------------------------------------------------------------- #
# Years and seniority parsing
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("5+ years", 5),
        ("At least 7 years of experience", 7),
        ("3-5 years of experience", 3),
        ("3 to 5 years' experience", 3),
        ("Seven years of experience in cloud", 7),
        ("Minimum of 4 years' experience", 4),
        ("10+ years in IT, including 3+ years in architecture", 10),
        ("Founded 20 years ago", None),
        ("", None),
    ],
)
def test_extract_required_years_variants(text: str, expected: int | None):
    assert extract_required_years(text) == expected


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("Senior Software Engineer", "senior"),
        ("Sr. Data Engineer", "senior"),
        ("Staff Engineer", "staff"),
        ("Principal Architect", "principal"),
        ("Solutions Architect", "principal"),
        ("Tech Lead", "lead"),
        ("Engineering Manager", "manager"),
        ("Director of Engineering", "director"),
        ("VP Engineering", "executive"),
        ("Chief Technology Officer", "executive"),
        ("Head of Platform", "executive"),
        ("Software Engineer Intern", "intern"),
        ("Junior Developer", "junior"),
        ("Associate Consultant", "junior"),
        ("Software Engineer", None),
    ],
)
def test_detect_seniority_from_title(title: str, expected: str | None):
    assert detect_seniority(title) == expected


def test_detect_seniority_falls_back_to_description_years():
    assert (
        detect_seniority("Software Engineer", "10+ years of experience required")
        == "senior"
    )
    assert (
        detect_seniority("Software Engineer", "3+ years of experience required") is None
    )


# --------------------------------------------------------------------------- #
# Dimension behaviour: None when missing, never defaulted
# --------------------------------------------------------------------------- #
def test_every_dimension_is_none_without_information():
    result = score_job(profile={}, resume_text="", job={})

    assert result["version"] == SCORING_VERSION
    assert result["score"] is None
    assert result["classification"] == "Stretch"
    assert (
        result["classification_reason"]
        == "Not enough information to score this job yet."
    )
    assert result["hard_blockers"] == []
    assert len(result["dimensions"]) == 10
    for dimension in result["dimensions"]:
        assert dimension["score"] is None, dimension["name"]
        assert dimension["missing_information"], dimension["name"]
    assert "No resume text was provided." in result["missing_inputs"]


def test_required_skills_evidence_sources_and_missing():
    result = score_job(
        profile={"verified_skills": ["Kubernetes"]},
        resume_text="Built services in Python.",
        job={
            "title": "Engineer",
            "description": "Requirements: Python, Kubernetes, Terraform.",
        },
    )
    dimension = _dimensions(result)["required_skills"]

    assert dimension["score"] == pytest.approx(66.67)
    assert dimension["matched"] == ["python", "kubernetes"]
    assert dimension["missing"] == ["terraform"]
    sources = {
        item["fact"].split("'")[1]: item["source"] for item in dimension["evidence"]
    }
    assert sources == {"python": "resume", "kubernetes": "verified_skill"}


def test_explicit_job_skill_lists_take_precedence_over_extraction():
    result = score_job(
        profile={"verified_skills": ["Snowflake"]},
        resume_text="",
        job={
            "title": "Data Engineer",
            "description": "Requirements: Python.",
            "required_skills": ["Snowflake", "dbt"],
            "preferred_skills": ["Airflow"],
        },
    )
    dims = _dimensions(result)

    assert dims["required_skills"]["matched"] == ["snowflake"]
    assert dims["required_skills"]["missing"] == ["dbt"]
    assert dims["preferred_skills"]["score"] == 0.0
    assert dims["preferred_skills"]["missing"] == ["airflow"]


@pytest.mark.parametrize(
    ("years", "expected"),
    [(6, 100.0), (5, 100.0), (4, 75.0), (3, 50.0), (2, 25.0), (None, None)],
)
def test_experience_dimension_bands(years: int | None, expected: float | None):
    result = score_job(
        profile={"years_of_experience": years},
        resume_text="",
        job={
            "title": "Engineer",
            "description": "Requirements: 5+ years of experience.",
        },
    )
    dimension = _dimensions(result)["experience"]

    assert dimension["score"] == expected
    if expected is None:
        assert "years of experience" in dimension["missing_information"]


def test_experience_dimension_none_when_job_states_no_years():
    result = score_job(
        profile={"years_of_experience": 9},
        resume_text="",
        job={"title": "Engineer", "description": "Build things."},
    )
    assert _dimensions(result)["experience"]["score"] is None


@pytest.mark.parametrize(
    ("title", "target", "expected"),
    [
        ("Senior Engineer", "senior", 100.0),
        ("Staff Engineer", "lead", 100.0),  # staff and lead are the same rung
        ("Senior Engineer", "staff", 70.0),
        ("Principal Engineer", "senior", 40.0),
        ("Director of Engineering", "junior", 10.0),
    ],
)
def test_seniority_ladder(title: str, target: str, expected: float):
    result = score_job(
        profile={"target_seniority": target},
        resume_text="",
        job={"title": title, "description": ""},
    )
    assert _dimensions(result)["seniority"]["score"] == expected


def test_seniority_none_when_target_or_job_level_missing():
    no_target = score_job(profile={}, resume_text="", job={"title": "Senior Engineer"})
    no_level = score_job(
        profile={"target_seniority": "senior"},
        resume_text="",
        job={"title": "Engineer"},
    )
    assert _dimensions(no_target)["seniority"]["score"] is None
    assert _dimensions(no_level)["seniority"]["score"] is None


def test_leadership_architecture_coverage_with_sources():
    job = {
        "title": "Enterprise Architect",
        "description": "Own the architecture roadmap and governance for the enterprise.",
    }
    scored = score_job(
        profile={
            "career_profile": "enterprise_architect",
            "verified_skills": ["Governance"],
        },
        resume_text="Defined the architecture roadmap.",
        job=job,
    )
    dimension = _dimensions(scored)["leadership_architecture"]
    assert dimension["score"] == 75.0
    assert set(dimension["matched"]) == {"architecture", "roadmap", "governance"}
    assert dimension["missing"] == ["enterprise"]
    sources = {item["source"] for item in dimension["evidence"]}
    assert sources == {"resume", "verified_skill"}

    without_profile = score_job(profile={}, resume_text="architecture", job=job)
    assert _dimensions(without_profile)["leadership_architecture"]["score"] is None
    no_terms = score_job(
        profile={"career_profile": "cloud_architect"},
        resume_text="architecture",
        job={"title": "x", "description": "Write unit tests."},
    )
    assert _dimensions(no_terms)["leadership_architecture"]["score"] is None


def test_location_dimension_substring_remote_and_missing():
    base_job = {"title": "Engineer", "description": ""}
    match = score_job(
        profile={"locations": ["Seattle"]},
        resume_text="",
        job={**base_job, "location": "Seattle, WA", "workplace_type": "on_site"},
    )
    mismatch = score_job(
        profile={"locations": ["Seattle"]},
        resume_text="",
        job={**base_job, "location": "Austin, TX", "workplace_type": "on_site"},
    )
    remote = score_job(
        profile={"locations": ["Seattle"], "workplace_types": ["remote"]},
        resume_text="",
        job={**base_job, "location": "Austin, TX", "workplace_type": "remote"},
    )
    missing = score_job(
        profile={}, resume_text="", job={**base_job, "location": "Austin, TX"}
    )

    assert _dimensions(match)["location"]["score"] == 100.0
    assert _dimensions(mismatch)["location"]["score"] == 0.0
    assert _dimensions(remote)["location"]["score"] == 100.0
    assert _dimensions(missing)["location"]["score"] is None


def test_workplace_dimension():
    job = {"title": "Engineer", "description": "", "workplace_type": "on-site"}
    mismatch = score_job(
        profile={"workplace_types": ["remote"]}, resume_text="", job=job
    )
    match = score_job(profile={"workplace_types": ["onsite"]}, resume_text="", job=job)
    missing = score_job(
        profile={"workplace_types": ["remote"]},
        resume_text="",
        job={"title": "Engineer"},
    )

    assert _dimensions(mismatch)["workplace"]["score"] == 0.0
    assert _dimensions(match)["workplace"]["score"] == 100.0
    assert _dimensions(missing)["workplace"]["score"] is None


@pytest.mark.parametrize(
    ("job_min", "job_max", "expected"),
    [
        (140000, 170000, 100.0),  # overlap
        (120000, 140000, 60.0),  # max within 10% below the 150k minimum
        (90000, 100000, 20.0),  # far below
        (200000, 220000, 100.0),  # above expectation
    ],
)
def test_salary_dimension_bands(job_min: int, job_max: int, expected: float):
    result = score_job(
        profile={"salary_expectation_min": 150000, "salary_expectation_max": 180000},
        resume_text="",
        job={
            "title": "x",
            "description": "",
            "salary_min": job_min,
            "salary_max": job_max,
        },
    )
    assert _dimensions(result)["salary"]["score"] == expected


def test_salary_dimension_none_for_currency_mismatch_or_missing_inputs():
    mismatch = score_job(
        profile={"salary_expectation_min": 100000, "currency": "EUR"},
        resume_text="",
        job={
            "title": "x",
            "salary_min": 100000,
            "salary_max": 120000,
            "currency": "USD",
        },
    )
    missing = score_job(
        profile={"salary_expectation_min": 100000},
        resume_text="",
        job={"title": "x"},
    )
    dimension = _dimensions(mismatch)["salary"]
    assert dimension["score"] is None
    assert (
        "USD" in dimension["missing_information"]
        and "EUR" in dimension["missing_information"]
    )
    assert _dimensions(missing)["salary"]["score"] is None


# --------------------------------------------------------------------------- #
# Work authorization: explicit statements only
# --------------------------------------------------------------------------- #
def test_sponsorship_hard_blocker():
    blocker = (
        "The posting states it does not offer visa sponsorship, "
        "and your profile says you need sponsorship."
    )
    result = score_job(
        profile={
            "work_authorization": "requires_sponsorship",
            "verified_skills": ["Python"],
        },
        resume_text="Python",
        job={
            "title": "Engineer",
            "description": "Requirements: Python. We are unable to sponsor visas at this time.",
        },
    )
    dimension = _dimensions(result)["work_authorization"]

    assert dimension["score"] == 0.0
    assert result["hard_blockers"] == [blocker]
    assert result["classification"] == "Not Recommended"
    assert result["classification_reason"] == blocker
    assert any(item["source"] == "job" for item in dimension["evidence"])


def test_sponsorship_statement_field_is_honoured_and_authorized_candidate_scores_100():
    result = score_job(
        profile={"work_authorization": "authorized", "sponsorship_required": "no"},
        resume_text="",
        job={
            "title": "x",
            "description": "",
            "sponsorship_statement": "No visa sponsorship.",
        },
    )
    assert _dimensions(result)["work_authorization"]["score"] == 100.0
    assert result["hard_blockers"] == []


def test_citizenship_or_clearance_required_without_profile_value_is_none_not_zero():
    for text in (
        "US citizenship required for this role.",
        "Active Secret clearance required.",
    ):
        result = score_job(
            profile={"work_authorization": "authorized"},
            resume_text="",
            job={"title": "x", "description": text},
        )
        dimension = _dimensions(result)["work_authorization"]
        assert dimension["score"] is None
        assert dimension["missing_information"]
        assert result["hard_blockers"] == []


def test_work_authorization_not_scored_without_restriction_or_when_sponsorship_offered():
    silent = score_job(
        profile={"work_authorization": "authorized"},
        resume_text="",
        job={"title": "x", "description": "Build things."},
    )
    offered = score_job(
        profile={"work_authorization": "requires_sponsorship"},
        resume_text="",
        job={
            "title": "x",
            "description": "Visa sponsorship is available for this role.",
        },
    )
    assert _dimensions(silent)["work_authorization"]["score"] is None
    assert (
        _dimensions(silent)["work_authorization"]["reason"]
        == "The posting does not state a restriction."
    )
    assert _dimensions(offered)["work_authorization"]["score"] is None
    assert offered["hard_blockers"] == []


def test_must_be_authorized_wording_blocks_only_explicit_sponsorship_need():
    job = {"title": "x", "description": "Must be legally authorized to work in the US."}
    blocked = score_job(
        profile={"work_authorization": "requires_sponsorship"}, resume_text="", job=job
    )
    unknown = score_job(profile={}, resume_text="", job=job)
    assert _dimensions(blocked)["work_authorization"]["score"] == 0.0
    assert blocked["hard_blockers"]
    assert _dimensions(unknown)["work_authorization"]["score"] is None
    assert unknown["hard_blockers"] == []


# --------------------------------------------------------------------------- #
# Classification, weights, overall score
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("score", "label"),
    [
        (100, "Recommended"),
        (85, "Recommended"),
        (84.99, "Strong Match"),
        (70, "Strong Match"),
        (69.99, "Good Match"),
        (55, "Good Match"),
        (54.99, "Stretch"),
        (40, "Stretch"),
        (39.99, "Not Recommended"),
        (0, "Not Recommended"),
    ],
)
def test_classify_thresholds(score: float, label: str):
    result_label, reason = classify(score)
    assert result_label == label
    assert label in CLASSIFICATIONS
    assert f"{float(score):.2f}" in reason


def test_classify_none_and_hard_blockers():
    assert classify(None) == (
        "Stretch",
        "Not enough information to score this job yet.",
    )
    assert classify(95, hard_blockers=["Needs sponsorship."]) == (
        "Not Recommended",
        "Needs sponsorship.",
    )
    assert classify(None, hard_blockers=["Blocked."])[0] == "Not Recommended"


def test_weights_validation_and_as_dict():
    with pytest.raises(ValueError, match="non-negative"):
        ScoringWeights(required_skills=-0.1)
    with pytest.raises(ValueError, match="non-negative"):
        ScoringWeights(resume_coverage=float("nan"))
    with pytest.raises(ValueError, match="positive"):
        ScoringWeights(
            required_skills=0,
            preferred_skills=0,
            experience=0,
            seniority=0,
            leadership_architecture=0,
            location=0,
            workplace=0,
            salary=0,
            work_authorization=0,
            resume_coverage=0,
        )
    weights = ScoringWeights().as_dict()
    assert set(weights) == {
        "required_skills",
        "preferred_skills",
        "experience",
        "seniority",
        "leadership_architecture",
        "location",
        "workplace",
        "salary",
        "work_authorization",
        "resume_coverage",
    }
    assert sum(weights.values()) == pytest.approx(1.0)


def test_overall_score_excludes_unscored_dimensions_hand_computed():
    profile = {
        "verified_skills": ["Kubernetes"],
        "years_of_experience": 6,
        "target_seniority": "senior",
        "workplace_types": ["remote"],
    }
    job = {
        "title": "Senior Engineer",
        "description": "Requirements:\n- 5+ years of experience with Python and Kubernetes\n",
        "workplace_type": "remote",
    }
    resume = "Engineer."
    result = score_job(profile=profile, resume_text=resume, job=job)
    dims = _dimensions(result)

    assert (
        dims["required_skills"]["score"] == 50.0
    )  # kubernetes verified, python missing
    assert dims["experience"]["score"] == 100.0
    assert dims["seniority"]["score"] == 100.0
    assert dims["location"]["score"] == 100.0  # remote job + remote preference
    assert dims["workplace"]["score"] == 100.0
    for name in (
        "preferred_skills",
        "leadership_architecture",
        "salary",
        "work_authorization",
    ):
        assert dims[name]["score"] is None, name

    coverage = score_keyword_evidence(
        resume,
        job["title"],
        job["description"],
        preferences={
            "verified_skills": ["Kubernetes"],
            "locations": [],
            "workplace_types": ["remote"],
        },
        job_location="",
        workplace_type="remote",
    )["score"]
    assert coverage is not None
    assert dims["resume_coverage"]["score"] == coverage
    assert result["resume_coverage"]["version"] == MATCHING_VERSION

    weighted = (
        50 * 0.30 + 100 * 0.10 + 100 * 0.10 + 100 * 0.05 + 100 * 0.05 + coverage * 0.15
    )
    scored_weight = 0.30 + 0.10 + 0.10 + 0.05 + 0.05 + 0.15
    assert result["score"] == pytest.approx(
        round(weighted / scored_weight, 2), abs=0.01
    )
    # Treating the unscored dimensions as 0 would divide by the full weight instead.
    assert result["score"] != pytest.approx(weighted / 1.0, abs=0.01)


def test_resume_coverage_dimension_reuses_matching_module():
    result = score_job(
        profile={"verified_skills": ["Kubernetes"]},
        resume_text="Cloud architect",
        job={"title": "Cloud Architect", "description": "Kubernetes platforms"},
    )
    assert result["resume_coverage"]["version"] == MATCHING_VERSION
    dimension = _dimensions(result)["resume_coverage"]
    assert dimension["score"] == result["resume_coverage"]["score"]
    sources = {
        item["fact"].split("'")[1]: item["source"] for item in dimension["evidence"]
    }
    assert sources["kubernetes"] == "verified_skill"
    assert sources["cloud"] == "resume"


def test_inputs_fingerprint_is_deterministic_and_changes_with_resume():
    profile = {"verified_skills": ["Python"]}
    job = {"title": "Engineer", "description": "Requirements: Python."}
    now = datetime(2026, 10, 22, 12, 0, tzinfo=timezone.utc)
    first = score_job(profile=profile, resume_text="Python developer", job=job, now=now)
    second = score_job(
        profile=profile, resume_text="Python developer", job=job, now=now
    )
    changed = score_job(profile=profile, resume_text="Java developer", job=job, now=now)

    assert first["inputs_fingerprint"] == second["inputs_fingerprint"]
    assert len(first["inputs_fingerprint"]) == 64
    assert first["inputs_fingerprint"] != changed["inputs_fingerprint"]
    assert first["computed_at"] == "2026-10-22T12:00:00+00:00"
    assert first == second


def test_output_is_json_serializable():
    result = score_job(
        profile={
            "verified_skills": ["AWS"],
            "years_of_experience": 8,
            "target_seniority": "senior",
            "career_profile": "cloud_architect",
            "locations": ["Seattle"],
            "workplace_types": ["remote", "hybrid"],
            "salary_expectation_min": 150000,
            "salary_expectation_max": 180000,
            "currency": "USD",
            "work_authorization": "authorized",
        },
        resume_text="Cloud architect with AWS, Terraform and roadmap experience.",
        job={
            "title": "Senior Cloud Architect",
            "description": REQUIRED_PREFERRED_DESCRIPTION
            + "\nWe will not sponsor visas.",
            "location": "Seattle, WA",
            "workplace_type": "hybrid",
            "salary_min": 160000,
            "salary_max": 190000,
            "currency": "USD",
            "posted_at": datetime(2026, 10, 1, tzinfo=timezone.utc),
        },
    )
    encoded = json.dumps(result)
    decoded = json.loads(encoded)
    assert decoded["score"] == result["score"]
    assert decoded["classification"] in CLASSIFICATIONS
    assert all(
        0 <= dimension["score"] <= 100
        for dimension in decoded["dimensions"]
        if dimension["score"] is not None
    )
