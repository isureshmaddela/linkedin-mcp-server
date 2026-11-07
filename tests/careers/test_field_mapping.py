from __future__ import annotations

import re

import pytest

from aira.field_mapping import (
    CANONICAL_FIELDS,
    FIELD_TYPES,
    MATCH_KINDS,
    PREFILL_STATUSES,
    SENSITIVITY,
    FieldMapping,
    map_field,
    map_fields,
    normalize_label,
    prefill_fields,
    validate_value,
)

REQUIRED_FIELD_NAMES = {
    "first_name",
    "last_name",
    "full_name",
    "preferred_name",
    "email",
    "phone",
    "address_line",
    "city",
    "state",
    "postal_code",
    "country",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "website",
    "current_company",
    "current_title",
    "years_of_experience",
    "work_authorization",
    "sponsorship_required",
    "visa_status",
    "citizenship",
    "security_clearance",
    "salary_expectation",
    "location_preference",
    "workplace_preference",
    "relocation",
    "availability_date",
    "notice_period",
    "resume",
    "cover_letter",
    "gender",
    "race_ethnicity",
    "veteran_status",
    "disability_status",
    "criminal_history",
    "pronouns",
    "date_of_birth",
    "how_did_you_hear",
    "references",
    "legal_attestation",
    "age_over_18",
}


# --------------------------------------------------------------------------- #
# Canonical field integrity
# --------------------------------------------------------------------------- #
def test_canonical_fields_are_consistent_and_synonyms_are_unique():
    assert REQUIRED_FIELD_NAMES <= set(CANONICAL_FIELDS)
    owners: dict[str, str] = {}
    for name, field in CANONICAL_FIELDS.items():
        assert field.name == name
        assert field.sensitivity in SENSITIVITY
        assert field.field_type in FIELD_TYPES
        if field.sensitivity == "sensitive":
            assert field.requires_confirmation is True, name
        assert 4 <= len(field.synonyms) <= 12, name
        if field.validation is not None:
            re.compile(field.validation)
        for phrase in (field.label, *field.synonyms):
            key = normalize_label(phrase)
            assert key, (name, phrase)
            assert owners.get(key, name) == name, (
                f"{phrase!r} belongs to {owners[key]} and {name}"
            )
            owners[key] = name
        assert len({normalize_label(s) for s in field.synonyms}) == len(
            field.synonyms
        ), name


# --------------------------------------------------------------------------- #
# normalize_label
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("First Name *", "first name"),
        ("Email Address (required)", "email address"),
        ("  Phone:  ", "phone"),
        ("LinkedIn Profile (optional)", "linkedin profile"),
        ("Zip/Postal Code", "zip postal code"),
        ("What's your name?", "whats your name"),
        ("Résumé", "résumé"),
        ("", ""),
    ],
)
def test_normalize_label(label: str, expected: str):
    assert normalize_label(label) == expected


# --------------------------------------------------------------------------- #
# Synonym hits across providers
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("First Name", "first_name"),  # Greenhouse
        ("Last Name", "last_name"),
        ("Full name", "full_name"),  # Lever
        ("Email", "email"),
        ("Phone", "phone"),
        ("Mobile", "phone"),
        ("Resume/CV", "resume"),
        ("Cover Letter", "cover_letter"),
        ("LinkedIn Profile", "linkedin_url"),
        ("GitHub URL", "github_url"),
        ("Website", "website"),
        ("Current company", "current_company"),  # Lever
        ("Current employer", "current_company"),  # iCIMS
        (
            "Are you legally authorized to work in the United States?",
            "work_authorization",
        ),
        (
            "Will you now or in the future require sponsorship for employment visa status?",
            "sponsorship_required",
        ),
        ("Desired salary", "salary_expectation"),
        ("Expected compensation", "salary_expectation"),
        ("Earliest start date", "availability_date"),  # Workday
        ("Do you have a security clearance?", "security_clearance"),
        ("Postal Code", "postal_code"),
        ("Country", "country"),
        ("How did you hear about us?", "how_did_you_hear"),  # SmartRecruiters
        ("Pronouns", "pronouns"),
        ("Notice period", "notice_period"),
        ("Are you willing to relocate?", "relocation"),
        ("Date of Birth", "date_of_birth"),
        ("Please enter your phone number", "phone"),
        ("Which city are you located in?", "city"),
    ],
)
def test_synonym_hits(label: str, expected: str):
    mapping = map_field(label)
    assert mapping.canonical_field == expected
    assert mapping.match_kind in ("exact", "synonym")
    assert mapping.confidence >= 0.85
    assert mapping.sensitivity == CANONICAL_FIELDS[expected].sensitivity
    assert mapping.validation_rule == CANONICAL_FIELDS[expected].validation


def test_token_and_fuzzy_matches_carry_lower_confidence_and_need_confirmation():
    fuzzy = map_field("Phon Number")
    assert fuzzy.canonical_field == "phone"
    assert fuzzy.match_kind == "fuzzy"
    assert fuzzy.confidence == 0.6
    assert fuzzy.requires_confirmation is True

    token = map_field("What is your address")
    assert token.canonical_field == "address_line"
    assert token.match_kind == "token"
    assert token.confidence == 0.7
    assert token.requires_confirmation is True

    assert map_field("Fist Name").canonical_field == "first_name"


def test_unknown_label_maps_to_nothing():
    mapping = map_field("Favorite color", provider_field_id="q_7")
    assert mapping.canonical_field is None
    assert mapping.confidence == 0.0
    assert mapping.match_kind == "none"
    assert mapping.sensitivity == "personal"
    assert mapping.requires_confirmation is True
    assert mapping.validation_rule is None
    assert mapping.provider_field_id == "q_7"
    assert mapping.field_type == "text"
    assert set(MATCH_KINDS) >= {mapping.match_kind}


# --------------------------------------------------------------------------- #
# Sensitive gating
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "label",
    [
        "Will you now or in the future require sponsorship for employment visa status?",
        "Desired salary",
        "Do you have a disability?",
        "Are you a protected veteran?",
    ],
)
def test_sensitive_labels_always_require_confirmation_and_are_never_prefilled(
    label: str,
):
    mapping = map_field(label)
    assert mapping.canonical_field is not None
    assert mapping.sensitivity == "sensitive"
    assert mapping.requires_confirmation is True

    profile = {mapping.canonical_field: "Yes"}
    unconfirmed = {mapping.canonical_field: {"value": "Yes", "confirmed": False}}
    plain = {mapping.canonical_field: "Yes"}
    for answers in ({}, unconfirmed, plain):
        [result] = prefill_fields([mapping], profile, answers)
        assert result.status == "needs_confirmation"
        assert result.value is None
        assert result.value_source is None
        assert "sensitive" in result.message

    confirmed_value = (
        "No" if mapping.canonical_field in ("sponsorship_required",) else "Yes"
    )
    if mapping.canonical_field == "salary_expectation":
        confirmed_value = "150,000 USD"
    [result] = prefill_fields(
        [mapping],
        {},
        {mapping.canonical_field: {"value": confirmed_value, "confirmed": True}},
    )
    assert result.status == "prefilled"
    assert result.value == confirmed_value
    assert result.value_source == "standard_answer"


@pytest.mark.parametrize(
    "label",
    [
        "Current compensation",
        "Salary history",
        "Are you willing to relocate to Austin?",
        "Visa type",
        "Have you ever been convicted of a felony?",
        "Gender",
        "Race",
        "I certify that the information is true",
        "Do you require sponsorship for H-1B?",
        "Do you have an active clearance?",
    ],
)
def test_sensitive_hints_never_map_to_non_sensitive_fields(label: str):
    mapping = map_field(label)
    assert mapping.sensitivity == "sensitive"
    assert mapping.requires_confirmation is True
    if mapping.canonical_field is not None:
        assert CANONICAL_FIELDS[mapping.canonical_field].sensitivity == "sensitive"


def test_sensitive_option_sets_mark_unknown_labels_sensitive():
    mapping = map_field(
        "Status", options=["I am a veteran", "I am not a veteran", "Decline to answer"]
    )
    assert mapping.canonical_field is None
    assert mapping.sensitivity == "sensitive"
    assert mapping.requires_confirmation is True
    assert mapping.field_type == "select"

    yes_no = map_field("Status", options=["Yes", "No"])
    assert yes_no.field_type == "boolean"
    assert yes_no.sensitivity == "personal"


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    ("field", "value", "ok"),
    [
        ("email", "ada@example.com", True),
        ("email", "not-an-email", False),
        ("phone", "+1 (555) 010-2000", True),
        ("phone", "123", False),
        ("linkedin_url", "https://www.linkedin.com/in/ada", True),
        ("linkedin_url", "linkedin.com/in/ada", True),
        ("linkedin_url", "https://example.com/ada", False),
        ("website", "https://ada.dev", True),
        ("website", "ada.dev", False),
        ("years_of_experience", "12", True),
        ("years_of_experience", "twelve", False),
        ("sponsorship_required", "Maybe", False),
        ("sponsorship_required", "no", True),
        ("date_of_birth", "1990-01-31", True),
        ("date_of_birth", "Jan 31", False),
        ("first_name", "", False),
    ],
)
def test_validate_value(field: str, value: str, ok: bool):
    error = validate_value(field, value)
    assert (error is None) is ok, error
    if error is not None:
        assert CANONICAL_FIELDS[field].label in error


def test_validate_value_unknown_field():
    assert validate_value("nope", "x") is not None


def test_prefill_marks_invalid_profile_values():
    mappings = map_fields(
        [
            {"label": "Email", "field_name": "email"},
            {"label": "LinkedIn Profile", "id": "li"},
            {"label": "Phone Number", "id": "ph"},
        ]
    )
    profile = {"email": "bad", "linkedin_url": "https://example.com", "phone": "12"}
    results = prefill_fields(mappings, profile, {})
    assert [item.status for item in results] == ["invalid", "invalid", "invalid"]
    assert [item.value for item in results] == ["bad", "https://example.com", "12"]
    assert all(item.value_source == "profile" for item in results)
    assert all(item.status in PREFILL_STATUSES for item in results)


# --------------------------------------------------------------------------- #
# map_fields and prefill
# --------------------------------------------------------------------------- #
def test_map_fields_preserves_order_ids_and_types():
    fields = [
        {"label": "First Name", "field_name": "first_name"},
        {"label": "Favorite color", "id": "custom_1"},
        {
            "label": "Country",
            "field_name": "country",
            "type": "select",
            "options": ["US", "CA"],
        },
        {"label": "Email Address", "field_name": "email", "type": "email"},
    ]
    mappings = map_fields(fields)
    assert isinstance(mappings[0], FieldMapping)
    assert [m.detected_label for m in mappings] == [f["label"] for f in fields]
    assert [m.provider_field_id for m in mappings] == [
        "first_name",
        "custom_1",
        "country",
        "email",
    ]
    assert [m.canonical_field for m in mappings] == [
        "first_name",
        None,
        "country",
        "email",
    ]
    assert mappings[2].field_type == "select"
    assert mappings[3].field_type == "email"


def test_prefill_statuses_and_full_name_derivation():
    mappings = map_fields(
        [
            {"label": "First Name", "id": "fn"},
            {"label": "Full Name", "id": "name"},
            {"label": "Email", "id": "email"},
            {"label": "Current Company", "id": "co"},
            {"label": "How did you hear about us?", "id": "src"},
            {"label": "Favorite color", "id": "q1"},
            {"label": "Phon Number", "id": "ph"},
            {"label": "Workplace Preference", "id": "wp"},
        ]
    )
    profile = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "email": "ada@example.com",
        "phone": "+1 555 010 2000",
        "workplace_types": ["remote", "hybrid"],
    }
    answers = {"how_did_you_hear": "LinkedIn"}
    results = {
        item.mapping.provider_field_id: item
        for item in prefill_fields(mappings, profile, answers)
    }

    assert results["fn"].status == "prefilled"
    assert results["fn"].value == "Ada" and results["fn"].value_source == "profile"
    assert results["name"].status == "prefilled"
    assert results["name"].value == "Ada Lovelace"
    assert results["email"].status == "prefilled"
    assert results["co"].status == "missing" and results["co"].value is None
    assert results["src"].status == "prefilled"
    assert (
        results["src"].value == "LinkedIn"
        and results["src"].value_source == "standard_answer"
    )
    assert results["q1"].status == "unknown" and results["q1"].value is None
    # a valid value exists but the fuzzy mapping must be confirmed first
    assert results["ph"].status == "needs_confirmation" and results["ph"].value is None
    assert (
        results["wp"].status == "prefilled" and results["wp"].value == "remote, hybrid"
    )
    assert all(
        item.value_source in ("profile", "standard_answer", None)
        for item in results.values()
    )


def test_prefill_prefers_profile_over_standard_answer_for_public_fields():
    [result] = prefill_fields(
        [map_field("Email")],
        {"email": "profile@example.com"},
        {"email": {"value": "answer@example.com", "confirmed": True}},
    )
    assert result.value == "profile@example.com"
    assert result.value_source == "profile"


def test_sensitive_confirmed_but_invalid_answer_is_reported_invalid():
    mapping = map_field(
        "Will you now or in the future require sponsorship for employment visa status?"
    )
    [result] = prefill_fields(
        [mapping], {}, {"sponsorship_required": {"value": "Maybe", "confirmed": True}}
    )
    assert result.status == "invalid"
    assert "must be one of" in result.message
