"""Synonym and field-mapping layer between ATS form labels and the canonical profile.

Every mapping is deterministic and explainable: each result records which
matching strategy fired (exact, synonym, token, fuzzy, none) and the
confidence that strategy carries. Sensitive fields (work authorization,
compensation, demographics, legal attestations, criminal history, ...) always
require explicit confirmation and are never prefilled from the profile alone.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Any

__all__ = [
    "CANONICAL_FIELDS",
    "FIELD_TYPES",
    "MATCH_KINDS",
    "PREFILL_STATUSES",
    "SENSITIVITY",
    "CanonicalField",
    "FieldMapping",
    "PrefilledField",
    "map_field",
    "map_fields",
    "normalize_label",
    "prefill_fields",
    "validate_value",
]

SENSITIVITY = ("public", "personal", "sensitive")
FIELD_TYPES = (
    "text",
    "email",
    "phone",
    "url",
    "number",
    "select",
    "boolean",
    "textarea",
    "date",
    "file",
)
MATCH_KINDS = ("exact", "synonym", "token", "fuzzy", "none")
PREFILL_STATUSES = ("prefilled", "needs_confirmation", "missing", "unknown", "invalid")

CONFIDENCE_EXACT = 1.0
CONFIDENCE_SYNONYM = 0.85
CONFIDENCE_TOKEN = 0.7
CONFIDENCE_FUZZY = 0.6
TOKEN_JACCARD_THRESHOLD = 0.6
FUZZY_RATIO_THRESHOLD = 0.8


@dataclass(frozen=True, slots=True)
class CanonicalField:
    """A canonical candidate-profile field and the form labels that map to it."""

    name: str
    label: str
    field_type: str
    sensitivity: str
    requires_confirmation: bool
    synonyms: tuple[str, ...]
    validation: str | None
    options: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FieldMapping:
    """The explainable result of mapping one ATS form label."""

    detected_label: str
    provider_field_id: str
    canonical_field: str | None
    field_type: str
    confidence: float
    sensitivity: str
    requires_confirmation: bool
    validation_rule: str | None
    match_kind: str


@dataclass(frozen=True, slots=True)
class PrefilledField:
    """A mapping joined with the value (if any) that may be used for it."""

    mapping: FieldMapping
    value: str | None
    value_source: str | None
    status: str
    message: str


# --------------------------------------------------------------------------- #
# Validation rules (regular expressions; ``(?i)`` prefixes make them case-free)
# --------------------------------------------------------------------------- #
_NAME_RE = r"^[^\r\n@]{1,100}$"
_SHORT_TEXT_RE = r"^[^\r\n]{1,200}$"
_EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[A-Za-z]{2,}$"
_PHONE_RE = r"^(?=(?:\D*\d){7,15}\D*$)\+?[0-9][0-9 ().-]*$"
_URL_RE = r"^https?://[^\s/]+\.[^\s]+$"
_LINKEDIN_RE = r"(?i)^(https?://)?([a-z]{2,3}\.)?linkedin\.com/(in|pub|company)/[^\s]+$"
_GITHUB_RE = r"(?i)^(https?://)?(www\.)?(github|gitlab)\.com/[^\s]+$"
_POSTAL_RE = r"^[A-Za-z0-9][A-Za-z0-9 -]{2,10}$"
_YEARS_RE = r"^\d{1,2}(\.\d)?$"
_SALARY_PART = r"[^\d]{0,4}\d[\d,.]*\s?[kK]?(\s?[A-Za-z]{3})?"
_SALARY_RE = rf"^{_SALARY_PART}(\s?(-|–|to)\s?{_SALARY_PART})?$"
_DATE_RE = r"^(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})$"
_AVAILABILITY_RE = (
    r"(?i)^(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|immediately|asap|upon offer|negotiable"
    r"|\d{1,2}\s?weeks?( notice)?|\d{1,3}\s?days?( notice)?|\d{1,2}\s?months?( notice)?)$"
)
_YES_NO_RE = r"(?i)^(yes|no)$"
_BOOLEAN_RE = r"(?i)^(yes|no|true|false)$"

_VALIDATION_MESSAGES: dict[str, str] = {
    "email": "must be a valid email address (name@domain.tld).",
    "phone": "must be a phone number with 7 to 15 digits.",
    "url": "must be a valid http(s) URL.",
    "number": "must be a number.",
    "date": "must be an ISO date (YYYY-MM-DD) or MM/DD/YYYY.",
    "boolean": "must be Yes or No.",
}


def _field(
    name: str,
    label: str,
    field_type: str,
    sensitivity: str,
    synonyms: Sequence[str],
    *,
    validation: str | None = None,
    requires_confirmation: bool | None = None,
    options: Sequence[str] = (),
) -> CanonicalField:
    if sensitivity not in SENSITIVITY:
        raise ValueError(f"Unknown sensitivity {sensitivity!r} for field {name!r}.")
    if field_type not in FIELD_TYPES:
        raise ValueError(f"Unknown field type {field_type!r} for field {name!r}.")
    confirm = True if sensitivity == "sensitive" else bool(requires_confirmation)
    return CanonicalField(
        name=name,
        label=label,
        field_type=field_type,
        sensitivity=sensitivity,
        requires_confirmation=confirm,
        synonyms=tuple(synonyms),
        validation=validation,
        options=tuple(options),
    )


_FIELD_LIST: tuple[CanonicalField, ...] = (
    _field(
        "first_name",
        "First Name",
        "text",
        "public",
        (
            "Given Name",
            "Legal First Name",
            "Forename",
            "Candidate First Name",
            "Your First Name",
            "Applicant First Name",
        ),
        validation=_NAME_RE,
    ),
    _field(
        "last_name",
        "Last Name",
        "text",
        "public",
        (
            "Surname",
            "Family Name",
            "Legal Last Name",
            "Candidate Last Name",
            "Your Last Name",
            "Applicant Last Name",
        ),
        validation=_NAME_RE,
    ),
    _field(
        "full_name",
        "Full Name",
        "text",
        "public",
        (
            "Name",
            "Your Name",
            "Legal Name",
            "Full Legal Name",
            "Candidate Name",
            "Applicant Name",
            "First and Last Name",
            "Your Full Name",
        ),
        validation=_NAME_RE,
    ),
    _field(
        "preferred_name",
        "Preferred Name",
        "text",
        "public",
        (
            "Preferred First Name",
            "Nickname",
            "What should we call you",
            "Name you go by",
            "Chosen Name",
            "Known As",
        ),
        validation=_NAME_RE,
    ),
    _field(
        "email",
        "Email",
        "email",
        "public",
        (
            "Email Address",
            "E-mail",
            "E-mail Address",
            "Contact Email",
            "Your Email",
            "Primary Email",
            "Email ID",
            "Personal Email",
        ),
        validation=_EMAIL_RE,
    ),
    _field(
        "phone",
        "Phone",
        "phone",
        "public",
        (
            "Phone Number",
            "Mobile",
            "Mobile Number",
            "Mobile Phone",
            "Telephone",
            "Contact Number",
            "Cell Phone",
            "Primary Phone",
            "Phone Number (Mobile)",
            "Best Contact Number",
        ),
        validation=_PHONE_RE,
    ),
    _field(
        "address_line",
        "Address",
        "text",
        "personal",
        (
            "Street Address",
            "Address Line 1",
            "Address Line",
            "Home Address",
            "Mailing Address",
            "Street",
            "Current Address",
            "Residential Address",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "city",
        "City",
        "text",
        "public",
        (
            "Current City",
            "City of Residence",
            "Town",
            "City/Town",
            "Home City",
            "Location (City)",
            "Location",
            "Current Location City",
            "Which city are you based in",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "state",
        "State",
        "text",
        "public",
        (
            "State/Province",
            "Province",
            "Region",
            "State or Province",
            "Current State",
            "State of Residence",
            "County",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "postal_code",
        "Postal Code",
        "text",
        "personal",
        (
            "Zip Code",
            "Zip",
            "ZIP/Postal Code",
            "Postcode",
            "Zip/Postal",
            "Post Code",
            "Pin Code",
        ),
        validation=_POSTAL_RE,
    ),
    _field(
        "country",
        "Country",
        "text",
        "public",
        (
            "Country of Residence",
            "Current Country",
            "Country/Region",
            "Home Country",
            "Which country do you live in",
            "Country you reside in",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "linkedin_url",
        "LinkedIn URL",
        "url",
        "public",
        (
            "LinkedIn",
            "LinkedIn Profile",
            "LinkedIn Profile URL",
            "LinkedIn Link",
            "LinkedIn Profile Link",
            "Link to LinkedIn",
            "LinkedIn Address",
            "LinkedIn Public Profile",
        ),
        validation=_LINKEDIN_RE,
    ),
    _field(
        "github_url",
        "GitHub URL",
        "url",
        "public",
        (
            "GitHub",
            "GitHub Profile",
            "GitHub Profile URL",
            "GitHub Link",
            "GitHub Username",
            "GitHub/GitLab",
            "Code Repository Link",
        ),
        validation=_GITHUB_RE,
    ),
    _field(
        "portfolio_url",
        "Portfolio URL",
        "url",
        "public",
        (
            "Portfolio",
            "Portfolio Link",
            "Online Portfolio",
            "Portfolio Website",
            "Work Samples",
            "Link to Portfolio",
            "Dribbble/Behance",
        ),
        validation=_URL_RE,
    ),
    _field(
        "website",
        "Website",
        "url",
        "public",
        (
            "Personal Website",
            "Website URL",
            "Personal Site",
            "Blog",
            "Homepage",
            "Other Website",
            "Website/Blog",
            "Other Links",
        ),
        validation=_URL_RE,
    ),
    _field(
        "current_company",
        "Current Company",
        "text",
        "public",
        (
            "Current Employer",
            "Company",
            "Employer",
            "Company Name",
            "Most Recent Employer",
            "Current or Most Recent Company",
            "Present Employer",
            "Current Organization",
            "Where do you currently work",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "current_title",
        "Current Title",
        "text",
        "public",
        (
            "Current Job Title",
            "Job Title",
            "Current Position",
            "Current Role",
            "Most Recent Title",
            "Current or Most Recent Title",
            "Position",
            "Designation",
            "Occupation",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "years_of_experience",
        "Years of Experience",
        "number",
        "public",
        (
            "Total Years of Experience",
            "How many years of experience do you have",
            "Years of Relevant Experience",
            "Years of Professional Experience",
            "Experience (Years)",
            "Total Experience",
            "How many years of relevant experience do you have",
            "Years in Industry",
        ),
        validation=_YEARS_RE,
    ),
    _field(
        "work_authorization",
        "Work Authorization",
        "boolean",
        "sensitive",
        (
            "Are you legally authorized to work in the United States",
            "Are you authorized to work in the US",
            "Are you legally authorized to work in the country where this position is located",
            "Are you authorized to work in the country of employment",
            "Are you eligible to work in the United States",
            "Do you have the right to work in the UK",
            "Are you legally eligible to work in Canada",
            "Authorized to work",
            "Work Eligibility",
            "Right to Work",
            "Employment Eligibility",
        ),
        validation=_YES_NO_RE,
        options=("Yes", "No"),
    ),
    _field(
        "sponsorship_required",
        "Sponsorship Required",
        "boolean",
        "sensitive",
        (
            "Sponsorship",
            "Will you now or in the future require sponsorship for employment visa status",
            "Do you require sponsorship",
            "Do you now or will you in the future require visa sponsorship",
            "Visa Sponsorship",
            "Will you require sponsorship to work",
            "Do you require visa sponsorship to work in the US",
            "Require Sponsorship",
            "Will you now or in the future require immigration sponsorship",
            "Do you need sponsorship for a work visa",
            "Will you require sponsorship now or in the future",
        ),
        validation=_YES_NO_RE,
        options=("Yes", "No"),
    ),
    _field(
        "visa_status",
        "Visa Status",
        "select",
        "sensitive",
        (
            "Current Visa Status",
            "Immigration Status",
            "What is your current visa status",
            "Visa Type",
            "Current Immigration Status",
            "Work Visa Status",
            "Type of Visa",
            "What is your work authorization status",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "citizenship",
        "Citizenship",
        "select",
        "sensitive",
        (
            "Citizenship Status",
            "Country of Citizenship",
            "Are you a US citizen",
            "Are you a United States citizen",
            "Nationality",
            "Are you a citizen or permanent resident",
            "US Citizenship",
            "Citizenship/Residency Status",
            "Are you a US citizen or permanent resident",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "security_clearance",
        "Security Clearance",
        "select",
        "sensitive",
        (
            "Do you have a security clearance",
            "Do you have an active security clearance",
            "Clearance Level",
            "Do you currently hold a security clearance",
            "Active Clearance",
            "Government Clearance",
            "What level of security clearance do you hold",
            "Clearance",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "salary_expectation",
        "Salary Expectation",
        "text",
        "sensitive",
        (
            "Desired Salary",
            "Expected Salary",
            "Expected Compensation",
            "Salary Expectations",
            "Desired Compensation",
            "What are your salary expectations",
            "Salary Requirements",
            "Expected Base Salary",
            "Compensation Expectations",
            "Target Salary",
            "What is your expected salary",
        ),
        validation=_SALARY_RE,
    ),
    _field(
        "location_preference",
        "Location Preference",
        "text",
        "public",
        (
            "Preferred Location",
            "Preferred Work Location",
            "Desired Location",
            "Where would you like to work",
            "Preferred Office Location",
            "Which location are you applying for",
            "Preferred Locations",
            "Office Preference",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "workplace_preference",
        "Workplace Preference",
        "select",
        "public",
        (
            "Work Arrangement",
            "Preferred Work Arrangement",
            "Remote/Hybrid/Onsite",
            "Are you open to remote work",
            "Work Mode",
            "Preferred Work Mode",
            "Remote or Onsite",
            "Are you open to hybrid work",
            "Work Setting Preference",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "relocation",
        "Relocation",
        "boolean",
        "sensitive",
        (
            "Are you willing to relocate",
            "Willing to Relocate",
            "Open to Relocation",
            "Would you be willing to relocate",
            "Are you open to relocating",
            "Relocation Assistance Required",
            "Would you relocate for this role",
            "Are you willing to relocate for this position",
        ),
        validation=_YES_NO_RE,
        options=("Yes", "No"),
    ),
    _field(
        "availability_date",
        "Availability Date",
        "date",
        "public",
        (
            "Earliest Start Date",
            "Available Start Date",
            "Start Date",
            "When can you start",
            "Date Available",
            "Availability",
            "When are you available to start",
            "Earliest Available Start Date",
            "Available From",
        ),
        validation=_AVAILABILITY_RE,
    ),
    _field(
        "notice_period",
        "Notice Period",
        "text",
        "public",
        (
            "What is your notice period",
            "Current Notice Period",
            "Notice Period (in weeks)",
            "How much notice do you need to give",
            "Notice",
            "Required Notice Period",
            "Notice Period with Current Employer",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "resume",
        "Resume",
        "file",
        "public",
        (
            "Resume/CV",
            "CV",
            "Upload Resume",
            "Attach Resume",
            "Upload CV",
            "Resume or CV",
            "Curriculum Vitae",
            "Attach your resume",
            "Resume Upload",
            "Upload your CV",
        ),
        validation=None,
    ),
    _field(
        "cover_letter",
        "Cover Letter",
        "textarea",
        "public",
        (
            "Upload Cover Letter",
            "Attach Cover Letter",
            "Cover Letter Upload",
            "Letter of Motivation",
            "Motivation Letter",
            "Cover Letter or Additional Information",
            "Why do you want to work here",
            "Why are you interested in this role",
            "Additional Information",
        ),
        validation=None,
        requires_confirmation=True,
    ),
    _field(
        "gender",
        "Gender",
        "select",
        "sensitive",
        (
            "Gender Identity",
            "What is your gender",
            "Sex",
            "Gender (Voluntary)",
            "Please select your gender",
            "Gender Self-Identification",
            "What gender do you identify as",
            "Sex/Gender",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "race_ethnicity",
        "Race/Ethnicity",
        "select",
        "sensitive",
        (
            "Race",
            "Ethnicity",
            "Race and Ethnicity",
            "Please identify your race",
            "Ethnic Background",
            "Are you Hispanic or Latino",
            "Please select your ethnicity",
            "Hispanic/Latino",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "veteran_status",
        "Veteran Status",
        "select",
        "sensitive",
        (
            "Are you a veteran",
            "Protected Veteran Status",
            "Are you a protected veteran",
            "Military Status",
            "Veteran Self-Identification",
            "Military Service",
            "Please identify your veteran status",
            "Are you a veteran of the US armed forces",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "disability_status",
        "Disability Status",
        "select",
        "sensitive",
        (
            "Do you have a disability",
            "Voluntary Self-Identification of Disability",
            "Disability",
            "Please indicate your disability status",
            "Do you have a disability or have a history of a disability",
            "Disability Self-Identification",
            "Are you an individual with a disability",
            "Disability Self-ID",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "criminal_history",
        "Criminal History",
        "textarea",
        "sensitive",
        (
            "Have you ever been convicted of a felony",
            "Have you ever been convicted of a crime",
            "Criminal Record",
            "Criminal Background",
            "Have you been convicted of a criminal offense",
            "Felony Conviction",
            "Criminal Conviction",
            "Background Check Disclosure",
            "Have you ever been convicted of a misdemeanor or felony",
        ),
        validation=None,
    ),
    _field(
        "pronouns",
        "Pronouns",
        "text",
        "personal",
        (
            "Preferred Pronouns",
            "What are your pronouns",
            "Your Pronouns",
            "Pronouns you use",
            "Personal Pronouns",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "date_of_birth",
        "Date of Birth",
        "date",
        "sensitive",
        (
            "DOB",
            "Birth Date",
            "Birthday",
            "Birthdate",
            "Your date of birth",
            "Date of Birth (MM/DD/YYYY)",
            "What is your date of birth",
        ),
        validation=_DATE_RE,
    ),
    _field(
        "how_did_you_hear",
        "How did you hear about us",
        "text",
        "public",
        (
            "How did you hear about this job",
            "How did you hear about this position",
            "Referral Source",
            "Source",
            "How did you find us",
            "Where did you hear about this role",
            "How did you learn about this opportunity",
            "Lead Source",
            "How did you hear about this opening",
        ),
        validation=_SHORT_TEXT_RE,
    ),
    _field(
        "references",
        "References",
        "textarea",
        "personal",
        (
            "Professional References",
            "Reference",
            "Please provide references",
            "Reference Contacts",
            "References (Name, Title, Contact)",
            "List of References",
            "Referee Details",
            "Reference Name",
        ),
        validation=None,
        requires_confirmation=True,
    ),
    _field(
        "legal_attestation",
        "Legal Attestation",
        "boolean",
        "sensitive",
        (
            "I certify that the information is accurate",
            "I certify that the information provided is true and complete",
            "Certification",
            "Acknowledgement",
            "I acknowledge",
            "I agree to the terms and conditions",
            "Signature",
            "Electronic Signature",
            "I attest that the above information is correct",
            "By submitting this application I certify",
            "Applicant Certification",
            "Terms and Conditions",
        ),
        validation=_BOOLEAN_RE,
    ),
    _field(
        "age_over_18",
        "Age Over 18",
        "boolean",
        "sensitive",
        (
            "Are you over 18",
            "Are you at least 18 years of age",
            "Are you 18 years of age or older",
            "Age Verification",
            "Are you 18 or older",
            "Are you at least 18 years old",
            "Are you over the age of 18",
            "Minimum Age",
            "Are you 18+",
        ),
        validation=_YES_NO_RE,
        options=("Yes", "No"),
    ),
)

CANONICAL_FIELDS: dict[str, CanonicalField] = {
    field.name: field for field in _FIELD_LIST
}

# --------------------------------------------------------------------------- #
# Label normalization
# --------------------------------------------------------------------------- #
_HINT_PAREN_RE = re.compile(
    r"[(\[{]\s*(?:required|optional|req|opt|mandatory)\s*[)\]}]", re.IGNORECASE
)
_APOSTROPHE_RE = re.compile(r"[’'`]")
_NON_WORD_RE = re.compile(r"[\W_]+")


def normalize_label(label: str) -> str:
    """Casefold, strip punctuation/asterisks/(required)/(optional), collapse spaces."""
    text = str(label or "").casefold()
    text = _HINT_PAREN_RE.sub(" ", text)
    text = text.replace("*", " ")
    text = _APOSTROPHE_RE.sub("", text)
    text = _NON_WORD_RE.sub(" ", text)
    return " ".join(text.split())


_STOP_TOKENS = frozenset(
    "a an the your you please enter provide what is are do of for this to in if any "
    "applicable me my our we us it be or and with that which i have has would will "
    "should can could".split()
)
# Single tokens that are too generic to drive a containment match on their own.
_GENERIC_TOKENS = frozenset(
    "name date type number title status code address profile link other".split()
)


def _content_tokens(text: str) -> frozenset[str]:
    return frozenset(token for token in text.split() if token not in _STOP_TOKENS)


def _containment_eligible(phrase: str) -> bool:
    return len(phrase) >= 4 and phrase not in _GENERIC_TOKENS


def _contains_phrase(haystack: str, needle: str) -> bool:
    return f" {needle} " in f" {haystack} "


def _build_index(fields: Iterable[CanonicalField]) -> tuple[tuple[str, str], ...]:
    entries: list[tuple[str, str]] = []
    seen: dict[str, str] = {}
    for field in fields:
        for phrase in (field.label, *field.synonyms):
            normalized = normalize_label(phrase)
            if not normalized:
                raise ValueError(f"Empty synonym on field {field.name!r}.")
            owner = seen.get(normalized)
            if owner is not None and owner != field.name:
                raise ValueError(
                    f"Synonym {phrase!r} is assigned to both {owner!r} and {field.name!r}."
                )
            if owner is None:
                seen[normalized] = field.name
                entries.append((normalized, field.name))
    return tuple(entries)


_INDEX: tuple[tuple[str, str], ...] = _build_index(_FIELD_LIST)
_SENSITIVE_INDEX: tuple[tuple[str, str], ...] = tuple(
    entry for entry in _INDEX if CANONICAL_FIELDS[entry[1]].sensitivity == "sensitive"
)

_SENSITIVE_HINT_RE = re.compile(
    r"\b(?:sponsor|visa|authoriz|authoris|citizen|clearance|salary|compensation|disab"
    r"|veteran|gender|race|ethnic|convict|felony|relocat|certify|attest|criminal"
    r"|military|date of birth|birth ?date|dob|over 18|18 years)"
)
_SENSITIVE_OPTION_RE = re.compile(
    r"(?:decline to|prefer not|do not wish|dont wish|self ?identif|i am a veteran"
    r"|i am not a veteran|i have a disability|i do not have a disability|hispanic|latino)"
)


def _has_sensitive_hint(label_norm: str, options_norm: Sequence[str]) -> bool:
    if _SENSITIVE_HINT_RE.search(label_norm):
        return True
    options_text = " | ".join(options_norm)
    if not options_text:
        return False
    return bool(
        _SENSITIVE_HINT_RE.search(options_text)
        or _SENSITIVE_OPTION_RE.search(options_text)
    )


def _best_match(
    label_norm: str, candidates: Sequence[tuple[str, str]]
) -> tuple[str | None, float, str]:
    """Return (canonical name, confidence, match_kind) for the label."""
    if not label_norm:
        return None, 0.0, "none"
    for phrase, name in candidates:
        if phrase == label_norm:
            return name, CONFIDENCE_EXACT, "exact"

    longest: tuple[int, str] | None = None
    for phrase, name in candidates:
        if _containment_eligible(phrase) and _contains_phrase(label_norm, phrase):
            if longest is None or len(phrase) > longest[0]:
                longest = (len(phrase), name)
    if longest is not None:
        return longest[1], CONFIDENCE_SYNONYM, "synonym"
    if _containment_eligible(label_norm):
        shortest: tuple[int, str] | None = None
        for phrase, name in candidates:
            if _contains_phrase(phrase, label_norm):
                if shortest is None or len(phrase) < shortest[0]:
                    shortest = (len(phrase), name)
        if shortest is not None:
            return shortest[1], CONFIDENCE_SYNONYM, "synonym"

    label_tokens = _content_tokens(label_norm)
    best_jaccard = 0.0
    best_token_name: str | None = None
    if label_tokens:
        for phrase, name in candidates:
            phrase_tokens = _content_tokens(phrase)
            if not phrase_tokens:
                continue
            jaccard = len(phrase_tokens & label_tokens) / len(
                phrase_tokens | label_tokens
            )
            if jaccard > best_jaccard:
                best_jaccard, best_token_name = jaccard, name
    if best_token_name is not None and best_jaccard >= TOKEN_JACCARD_THRESHOLD:
        return best_token_name, CONFIDENCE_TOKEN, "token"

    best_ratio = 0.0
    best_fuzzy_name: str | None = None
    for phrase, name in candidates:
        ratio = SequenceMatcher(None, label_norm, phrase).ratio()
        if ratio > best_ratio:
            best_ratio, best_fuzzy_name = ratio, name
    if best_fuzzy_name is not None and best_ratio >= FUZZY_RATIO_THRESHOLD:
        return best_fuzzy_name, CONFIDENCE_FUZZY, "fuzzy"
    return None, 0.0, "none"


def _infer_field_type(
    explicit: str | None, options_norm: Sequence[str], field: CanonicalField | None
) -> str:
    if explicit:
        return str(explicit).strip().casefold()
    if options_norm:
        if set(options_norm) <= {"yes", "no"}:
            return "boolean"
        return "select"
    if field is not None:
        return field.field_type
    return "text"


def map_field(
    label: str,
    *,
    provider_field_id: str = "",
    field_type: str | None = None,
    options: Sequence[str] = (),
) -> FieldMapping:
    """Map one ATS form label to a canonical field with an explainable confidence."""
    label_norm = normalize_label(label)
    options_norm = [normalize_label(str(option)) for option in options]
    options_norm = [option for option in options_norm if option]
    hinted = _has_sensitive_hint(label_norm, options_norm)

    name, confidence, kind = _best_match(label_norm, _INDEX)
    if hinted and (name is None or CANONICAL_FIELDS[name].sensitivity != "sensitive"):
        name, confidence, kind = _best_match(label_norm, _SENSITIVE_INDEX)

    if name is None:
        return FieldMapping(
            detected_label=str(label),
            provider_field_id=str(provider_field_id or ""),
            canonical_field=None,
            field_type=_infer_field_type(field_type, options_norm, None),
            confidence=0.0,
            sensitivity="sensitive" if hinted else "personal",
            requires_confirmation=True,
            validation_rule=None,
            match_kind="none",
        )

    field = CANONICAL_FIELDS[name]
    return FieldMapping(
        detected_label=str(label),
        provider_field_id=str(provider_field_id or ""),
        canonical_field=name,
        field_type=_infer_field_type(field_type, options_norm, field),
        confidence=round(confidence, 2),
        sensitivity=field.sensitivity,
        requires_confirmation=field.requires_confirmation
        or confidence < CONFIDENCE_SYNONYM,
        validation_rule=field.validation,
        match_kind=kind,
    )


def map_fields(fields: Sequence[Mapping[str, Any]]) -> list[FieldMapping]:
    """Map a sequence of ``{label, field_name?/id?, type?, options?}`` items in order."""
    mappings: list[FieldMapping] = []
    for item in fields:
        label = str(item.get("label") or item.get("name") or "")
        provider_id = (
            item.get("field_name") or item.get("id") or item.get("field_id") or ""
        )
        raw_type = item.get("type") or item.get("field_type")
        raw_options = item.get("options") or ()
        if isinstance(raw_options, str) or not isinstance(raw_options, Sequence):
            raw_options = ()
        mappings.append(
            map_field(
                label,
                provider_field_id=str(provider_id),
                field_type=str(raw_type) if raw_type else None,
                options=[str(option) for option in raw_options],
            )
        )
    return mappings


# --------------------------------------------------------------------------- #
# Validation and prefill
# --------------------------------------------------------------------------- #
def validate_value(canonical_field: str, value: str) -> str | None:
    """Return an error message when ``value`` is not acceptable for the field."""
    field = CANONICAL_FIELDS.get(canonical_field)
    if field is None:
        return f"Unknown canonical field '{canonical_field}'."
    text = str(value if value is not None else "").strip()
    if not text:
        return f"{field.label} is empty."
    if field.options:
        allowed = {option.casefold() for option in field.options}
        if text.casefold() not in allowed:
            return f"{field.label} must be one of: {', '.join(field.options)}."
    if field.validation and re.fullmatch(field.validation, text) is None:
        detail = _VALIDATION_MESSAGES.get(
            field.field_type, "does not match the expected format."
        )
        return f"{field.label} {detail}"
    return None


_PROFILE_ALIASES: dict[str, tuple[str, ...]] = {
    "email": ("email", "email_address"),
    "phone": ("phone", "phone_number", "mobile"),
    "linkedin_url": ("linkedin_url", "linkedin"),
    "github_url": ("github_url", "github"),
    "portfolio_url": ("portfolio_url", "portfolio"),
    "website": ("website", "personal_website"),
    "current_company": ("current_company", "company"),
    "current_title": ("current_title", "title", "headline"),
    "location_preference": ("location_preference", "locations", "preferred_locations"),
    "workplace_preference": ("workplace_preference", "workplace_types"),
    "address_line": ("address_line", "address", "street_address"),
}


def _stringify(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, str):
        text = value.strip()
        return text or None
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, Mapping):
        return _stringify(value.get("value"))
    if isinstance(value, Sequence):
        parts = [part for part in (_stringify(item) for item in value) if part]
        return ", ".join(parts) or None
    return str(value).strip() or None


def _profile_value(profile: Mapping[str, Any], name: str) -> str | None:
    for key in _PROFILE_ALIASES.get(name, (name,)):
        text = _stringify(profile.get(key))
        if text:
            return text
    if name == "full_name":
        first = _stringify(profile.get("first_name"))
        last = _stringify(profile.get("last_name"))
        if first:
            return " ".join(part for part in (first, last) if part)
    return None


def _standard_answer(
    standard_answers: Mapping[str, Any], name: str
) -> tuple[str | None, bool]:
    raw = standard_answers.get(name)
    if raw is None:
        return None, False
    if isinstance(raw, Mapping):
        return _stringify(raw.get("value")), raw.get("confirmed") is True
    return _stringify(raw), False


def _prefill_one(
    mapping: FieldMapping,
    profile: Mapping[str, Any],
    standard_answers: Mapping[str, Any],
) -> PrefilledField:
    name = mapping.canonical_field
    if name is None or name not in CANONICAL_FIELDS:
        return PrefilledField(
            mapping=mapping,
            value=None,
            value_source=None,
            status="unknown",
            message="No canonical field matched this label; review and answer it manually.",
        )
    field = CANONICAL_FIELDS[name]
    answer_value, confirmed = _standard_answer(standard_answers, name)
    profile_value = _profile_value(profile, name)

    if mapping.sensitivity == "sensitive" or field.sensitivity == "sensitive":
        if answer_value and confirmed:
            error = validate_value(name, answer_value)
            if error:
                return PrefilledField(
                    mapping, answer_value, "standard_answer", "invalid", error
                )
            return PrefilledField(
                mapping,
                answer_value,
                "standard_answer",
                "prefilled",
                f"{field.label} filled from an explicitly confirmed standard answer.",
            )
        if answer_value:
            message = (
                f"{field.label} is sensitive: a saved standard answer exists but is not "
                "confirmed. Confirm it before it can be used."
            )
        elif profile_value:
            message = (
                f"{field.label} is sensitive: profile data is never used without an "
                "explicitly confirmed standard answer."
            )
        else:
            message = (
                f"{field.label} is sensitive and has no confirmed standard answer; "
                "answer it manually."
            )
        return PrefilledField(mapping, None, None, "needs_confirmation", message)

    if profile_value:
        value, source = profile_value, "profile"
    elif answer_value:
        value, source = answer_value, "standard_answer"
    else:
        return PrefilledField(
            mapping,
            None,
            None,
            "missing",
            f"No value for {field.label} is available in the profile or standard answers.",
        )
    error = validate_value(name, value)
    if error:
        return PrefilledField(mapping, value, source, "invalid", error)
    source_label = source.replace("_", " ")
    if mapping.requires_confirmation:
        return PrefilledField(
            mapping,
            None,
            None,
            "needs_confirmation",
            f"A {source_label} value for {field.label} exists, but the {mapping.match_kind} "
            f"match (confidence {mapping.confidence:.2f}) or the field policy requires "
            "confirmation before use.",
        )
    return PrefilledField(
        mapping,
        value,
        source,
        "prefilled",
        f"{field.label} filled from {source_label}.",
    )


def prefill_fields(
    mappings: Sequence[FieldMapping],
    profile: Mapping[str, Any],
    standard_answers: Mapping[str, Any],
) -> list[PrefilledField]:
    """Join mappings with profile/standard-answer values; never auto-answer sensitive ones."""
    return [_prefill_one(mapping, profile, standard_answers) for mapping in mappings]
