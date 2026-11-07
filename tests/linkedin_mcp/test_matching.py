import pytest

from linkedin_mcp_server.matching import (
    MATCHING_VERSION,
    MatchingWeights,
    score_keyword_evidence,
)


def test_keyword_evidence_returns_weighted_dimensions_and_limitations():
    result = score_keyword_evidence(
        "Cloud architect experienced with Python and reliable platforms.",
        "Cloud Architect",
        "Build Python systems and support reliable data platforms.",
    )

    assert result["version"] == MATCHING_VERSION
    assert result["score"] is not None
    assert result["score_label"].startswith("Evidence combines exact-term coverage")
    assert result["weights"] == {
        "title": 0.2,
        "description": 0.6,
        "location": 0.1,
        "workplace": 0.1,
    }
    title = next(item for item in result["dimensions"] if item["name"] == "title")
    assert title["score"] == 100
    assert title["matched_terms"] == ["architect", "cloud"]
    assert result["limitations"]


def test_keyword_evidence_supports_configurable_weights():
    result = score_keyword_evidence(
        "Cloud architect",
        "Cloud Architect",
        "Python distributed systems",
        weights=MatchingWeights(title=1, description=0, location=0, workplace=0),
    )

    assert result["score"] == 100
    assert result["weights"] == {
        "title": 1,
        "description": 0,
        "location": 0,
        "workplace": 0,
    }


def test_keyword_evidence_marks_missing_inputs_without_fabricating_score():
    result = score_keyword_evidence("", "", "")

    assert result["score"] is None
    assert len(result["missing_inputs"]) == 2
    assert all(dimension["score"] is None for dimension in result["dimensions"])


def test_matching_weights_reject_invalid_values():
    with pytest.raises(ValueError, match="non-negative"):
        MatchingWeights(title=-1, description=1)
    with pytest.raises(ValueError, match="positive"):
        MatchingWeights(title=0, description=0, location=0, workplace=0)


def test_matching_uses_explicit_location_workplace_and_verified_skill_evidence():
    result = score_keyword_evidence(
        "Cloud engineer",
        "Kubernetes Engineer",
        "Build Kubernetes platforms in Seattle.",
        preferences={
            "locations": ["Seattle"],
            "workplace_types": ["remote"],
            "verified_skills": ["Kubernetes"],
        },
        job_location="Remote - Seattle, WA",
        workplace_type="remote",
    )

    dimensions = {dimension["name"]: dimension for dimension in result["dimensions"]}
    assert dimensions["location"]["score"] == 100
    assert dimensions["workplace"]["score"] == 100
    skill_match = next(
        evidence
        for evidence in dimensions["description"]["matched_evidence"]
        if evidence["term"] == "kubernetes"
    )
    assert skill_match["source"] == "verified_candidate_skill"


def test_matching_marks_location_and_workplace_unavailable_instead_of_mismatched():
    resume = "Python architect"
    title = "Python Architect"
    description = "Build Python systems"
    result = score_keyword_evidence(resume, title, description)
    without_preferences = score_keyword_evidence(
        resume,
        title,
        description,
        weights=MatchingWeights(location=0, workplace=0),
    )
    dimensions = {dimension["name"]: dimension for dimension in result["dimensions"]}

    assert result["score"] == without_preferences["score"]
    assert dimensions["location"]["score"] is None
    assert dimensions["workplace"]["score"] is None
