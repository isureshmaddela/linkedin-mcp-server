"""Deterministic lexical evidence scoring for candidate/job comparisons."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
from collections.abc import Mapping, Sequence
from typing import Any

MATCHING_VERSION = "lexical-evidence-v1"
_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9+#.-]*", re.IGNORECASE)
_STOP_WORDS = frozenset(
    "a an and are as at be by for from has have in into is it of on or our that the their these this to will with you your we they experience skills required preferred ability work role team company candidate including years including strong using across build building develop developing responsible responsibilities qualifications requirements plus knowledge preferred must should can may not but about all any also which who what when where how more most such other some each both than then very able based support design implement manage lead use used using".split()
)


@dataclass(frozen=True, slots=True)
class MatchingWeights:
    title: float = 0.2
    description: float = 0.6
    location: float = 0.1
    workplace: float = 0.1

    def __post_init__(self) -> None:
        values = (self.title, self.description, self.location, self.workplace)
        if any(not math.isfinite(value) or value < 0 for value in values):
            raise ValueError("Matching weights must be finite and non-negative.")
        if sum(values) <= 0:
            raise ValueError("At least one matching weight must be positive.")


def _terms(value: str) -> set[str]:
    return {
        token.rstrip(".-")
        for token in _TOKEN_RE.findall(value.casefold())
        if len(token.rstrip(".-")) >= 2 and token.rstrip(".-") not in _STOP_WORDS
    }


def score_keyword_evidence(
    resume: str,
    title: str,
    description: str,
    *,
    preferences: Mapping[str, Any] | None = None,
    job_location: str = "",
    workplace_type: str | None = None,
    weights: MatchingWeights = MatchingWeights(),
) -> dict[str, Any]:
    """Score exact term coverage without implying semantic or qualification fit."""
    preferences = preferences or {}
    verified_skills = preferences.get("verified_skills", [])
    if not isinstance(verified_skills, Sequence) or isinstance(verified_skills, str):
        verified_skills = []
    profile_terms = _terms(" ".join(str(skill) for skill in verified_skills))
    resume_terms = _terms(resume)
    candidate_terms = resume_terms | profile_terms
    dimensions = []
    weighted_total = 0.0
    available_weight = 0.0

    for name, text, weight in (
        ("title", title, weights.title),
        ("description", description, weights.description),
    ):
        source_terms = _terms(text)
        if not source_terms or weight == 0:
            dimensions.append(
                {
                    "name": name,
                    "score": None,
                    "weight": weight,
                    "matched_terms": [],
                    "missing_terms": [],
                    "missing_information": f"No usable {name} terms were available.",
                }
            )
            continue

        matched = sorted(source_terms & candidate_terms)
        missing = sorted(source_terms - candidate_terms)
        dimension_score = round(100 * len(matched) / len(source_terms), 2)
        weighted_total += dimension_score * weight
        available_weight += weight
        dimensions.append(
            {
                "name": name,
                "score": dimension_score,
                "weight": weight,
                "matched_terms": matched[:30],
                "matched_evidence": [
                    {
                        "term": term,
                        "source": "resume"
                        if term in resume_terms
                        else "verified_candidate_skill",
                    }
                    for term in matched[:30]
                ],
                "missing_terms": missing[:30],
                "missing_information": None,
            }
        )

    locations = preferences.get("locations", [])
    if not isinstance(locations, Sequence) or isinstance(locations, str):
        locations = []
    locations = [
        re.sub(r"\s+", " ", str(value)).strip().casefold() for value in locations
    ]
    locations = [value for value in locations if value]
    normalized_job_location = re.sub(r"\s+", " ", job_location).strip().casefold()
    if weights.location and locations and normalized_job_location:
        matched_locations = [
            value
            for value in locations
            if value in normalized_job_location or normalized_job_location in value
        ]
        location_score = 100.0 if matched_locations else 0.0
        weighted_total += location_score * weights.location
        available_weight += weights.location
        dimensions.append(
            {
                "name": "location",
                "score": location_score,
                "weight": weights.location,
                "matched_terms": matched_locations,
                "missing_terms": [
                    value for value in locations if value not in matched_locations
                ],
                "missing_information": None,
            }
        )
    else:
        dimensions.append(
            {
                "name": "location",
                "score": None,
                "weight": weights.location,
                "matched_terms": [],
                "missing_terms": [],
                "missing_information": "Add a location preference and provide a job location to assess alignment.",
            }
        )

    workplaces = preferences.get("workplace_types", [])
    if not isinstance(workplaces, Sequence) or isinstance(workplaces, str):
        workplaces = []
    workplaces = {
        str(value).strip().casefold() for value in workplaces if str(value).strip()
    }
    normalized_workplace = str(workplace_type or "").strip().casefold()
    if weights.workplace and workplaces and normalized_workplace:
        workplace_score = 100.0 if normalized_workplace in workplaces else 0.0
        weighted_total += workplace_score * weights.workplace
        available_weight += weights.workplace
        dimensions.append(
            {
                "name": "workplace",
                "score": workplace_score,
                "weight": weights.workplace,
                "matched_terms": [normalized_workplace] if workplace_score else [],
                "missing_terms": [] if workplace_score else [normalized_workplace],
                "missing_information": None,
            }
        )
    else:
        dimensions.append(
            {
                "name": "workplace",
                "score": None,
                "weight": weights.workplace,
                "matched_terms": [],
                "missing_terms": [],
                "missing_information": "Add a workplace preference and provide a structured job workplace type to assess alignment.",
            }
        )

    score = round(weighted_total / available_weight, 2) if available_weight else None
    missing_inputs = []
    if not resume_terms:
        if not profile_terms:
            missing_inputs.append(
                "No usable resume or verified-skill terms were available."
            )
    if not title.strip() and not description.strip():
        missing_inputs.append("No job title or description was available.")
    return {
        "version": MATCHING_VERSION,
        "score": score,
        "score_label": "Evidence combines exact-term coverage and explicit preferences; not a semantic fit score.",
        "weights": asdict(weights),
        "dimensions": dimensions,
        "missing_inputs": missing_inputs,
        "limitations": [
            "Exact word matching does not recognize synonyms, context, seniority, or transferable skills.",
            "Missing keywords are not proof that the candidate lacks a skill.",
            "Location uses case-insensitive substring matching; it does not calculate distance or commuting feasibility.",
            "Workplace alignment is scored only when the source provides an explicit structured workplace type.",
            "This score does not assess work authorization, salary, or qualifications.",
        ],
    }
