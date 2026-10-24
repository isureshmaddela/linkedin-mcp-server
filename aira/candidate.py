"""Universal candidate profile definitions, validation, and standard answers.

The profile is stored as key/value rows in the user's ``settings`` table under
``application_<field>`` keys (compatible with earlier Aira builds). Sensitive
fields are never inferred: they are stored only when the user enters them and
are surfaced to application preparation only when the user confirms the
matching standard answer.
"""

from __future__ import annotations

import re
import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from aira.field_mapping import CANONICAL_FIELDS

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
PHONE_RE = re.compile(r"^\+?[0-9 ().-]{7,30}$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

CAREER_PROFILES: dict[str, str] = {
    "enterprise_architect": "Enterprise Architect",
    "solution_architect": "Solution Architect",
    "cloud_architect": "Cloud Architect",
    "ai_architect": "AI Architect",
    "principal_architect": "Principal Architect",
    "program_manager": "Program Manager",
}
SENIORITY_LEVELS = (
    "intern",
    "junior",
    "mid",
    "senior",
    "staff",
    "lead",
    "principal",
    "manager",
    "director",
    "executive",
)
WORK_AUTH_OPTIONS = ("authorized", "requires_sponsorship", "unknown")
YES_NO_UNSURE = ("yes", "no", "unsure")
WORKPLACE_OPTIONS = ("remote", "hybrid", "on_site")


@dataclass(frozen=True, slots=True)
class ProfileField:
    name: str
    label: str
    field_type: str  # text | email | phone | url | number | select | date | textarea
    sensitivity: str  # public | personal | sensitive
    max_length: int = 200
    options: tuple[str, ...] = ()
    help: str = ""


PROFILE_FIELDS: tuple[ProfileField, ...] = (
    ProfileField("first_name", "First name", "text", "personal", 80),
    ProfileField("last_name", "Last name", "text", "personal", 80),
    ProfileField("preferred_name", "Preferred name", "text", "personal", 80),
    ProfileField(
        "full_name",
        "Full name",
        "text",
        "personal",
        160,
        help="Used on documents; filled from first and last name when empty.",
    ),
    ProfileField("email", "Contact email", "email", "personal", 254),
    ProfileField("phone", "Phone", "phone", "personal", 40),
    ProfileField("address", "Street address", "text", "personal", 200),
    ProfileField("city", "City", "text", "personal", 100),
    ProfileField("state", "State or region", "text", "personal", 100),
    ProfileField("postal_code", "Postal code", "text", "personal", 20),
    ProfileField("country", "Country", "text", "personal", 100),
    ProfileField(
        "location",
        "Location summary",
        "text",
        "personal",
        200,
        help="Shown on documents, e.g. Seattle, WA.",
    ),
    ProfileField("linkedin_url", "LinkedIn URL", "url", "public", 300),
    ProfileField("github_url", "GitHub URL", "url", "public", 300),
    ProfileField("portfolio_url", "Portfolio URL", "url", "public", 300),
    ProfileField("current_company", "Current company", "text", "personal", 160),
    ProfileField("current_title", "Current title", "text", "personal", 160),
    ProfileField("years_of_experience", "Years of experience", "number", "personal", 3),
    ProfileField(
        "career_profile",
        "Career profile",
        "select",
        "personal",
        40,
        tuple(CAREER_PROFILES),
    ),
    ProfileField(
        "target_seniority",
        "Target seniority",
        "select",
        "personal",
        20,
        SENIORITY_LEVELS,
    ),
    ProfileField(
        "work_authorization",
        "Work authorization",
        "select",
        "sensitive",
        40,
        WORK_AUTH_OPTIONS,
    ),
    ProfileField(
        "sponsorship_required",
        "Sponsorship required",
        "select",
        "sensitive",
        10,
        YES_NO_UNSURE,
    ),
    ProfileField(
        "visa_status",
        "Visa status",
        "text",
        "sensitive",
        120,
        help="Only if you choose to provide it.",
    ),
    ProfileField(
        "citizenship",
        "Citizenship",
        "text",
        "sensitive",
        120,
        help="Only if you choose to provide it.",
    ),
    ProfileField(
        "security_clearance",
        "Security clearance",
        "text",
        "sensitive",
        120,
        help="Only if you choose to provide it.",
    ),
    ProfileField(
        "salary_expectation_min",
        "Salary expectation (minimum)",
        "number",
        "sensitive",
        12,
    ),
    ProfileField(
        "salary_expectation_max",
        "Salary expectation (maximum)",
        "number",
        "sensitive",
        12,
    ),
    ProfileField("salary_currency", "Salary currency", "text", "sensitive", 3),
    ProfileField(
        "relocation", "Open to relocation", "select", "sensitive", 10, YES_NO_UNSURE
    ),
    ProfileField("availability_date", "Earliest start date", "date", "personal", 10),
    ProfileField("notice_period", "Notice period", "text", "personal", 80),
)
PROFILE_FIELD_MAP: dict[str, ProfileField] = {
    field.name: field for field in PROFILE_FIELDS
}
SENSITIVE_FIELDS: frozenset[str] = frozenset(
    f.name for f in PROFILE_FIELDS if f.sensitivity == "sensitive"
)

# Fields safe to include in local-model prompts (contact and document context only).
PROMPT_SAFE_FIELDS: tuple[str, ...] = (
    "full_name",
    "email",
    "phone",
    "location",
    "linkedin_url",
    "github_url",
    "portfolio_url",
    "current_title",
    "current_company",
)


def validate_profile_value(field: ProfileField, raw: Any) -> str:
    """Return the cleaned value or raise ValueError with a plain-language message."""
    value = re.sub(r"\s+", " ", str(raw if raw is not None else "")).strip()
    if not value:
        return ""
    if len(value) > field.max_length:
        raise ValueError(
            f"{field.label} must be {field.max_length} characters or fewer."
        )
    if field.field_type == "email" and not EMAIL_RE.fullmatch(value):
        raise ValueError(
            "Enter a valid application contact email."
            if field.name == "email"
            else f"Enter a valid {field.label.lower()}."
        )
    if field.field_type == "phone" and not PHONE_RE.fullmatch(value):
        raise ValueError(f"Enter a valid {field.label.lower()}.")
    if field.field_type == "url":
        parsed = urlsplit(value)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError(f"{field.label} must start with https://.")
        if field.name == "linkedin_url" and (
            parsed.netloc.lower() not in {"linkedin.com", "www.linkedin.com"}
            or not parsed.path.startswith("/in/")
        ):
            raise ValueError("Enter a valid LinkedIn profile URL.")
    if field.field_type == "number":
        try:
            number = float(value.replace(",", ""))
        except ValueError as error:
            raise ValueError(f"{field.label} must be a number.") from error
        if number < 0 or number > 10_000_000:
            raise ValueError(f"{field.label} is out of range.")
        value = str(int(number)) if number.is_integer() else str(number)
    if field.field_type == "select":
        value = value.lower().replace("-", "_").replace(" ", "_")
        if value not in field.options:
            if field.name == "sponsorship_required":
                raise ValueError("Sponsorship must be yes, no, or unsure.")
            if field.name == "work_authorization":
                raise ValueError(
                    "Select authorized, requires sponsorship, or unknown for work authorization."
                )
            raise ValueError(
                f"{field.label} must be one of: {', '.join(field.options)}."
            )
    if field.field_type == "date" and not DATE_RE.fullmatch(value):
        raise ValueError(f"{field.label} must use YYYY-MM-DD.")
    if field.name == "salary_currency":
        value = value.upper()
    return value


def clean_profile(values: Mapping[str, Any]) -> dict[str, str]:
    cleaned = {
        field.name: validate_profile_value(field, values.get(field.name, ""))
        for field in PROFILE_FIELDS
    }
    if not cleaned["full_name"] and (cleaned["first_name"] or cleaned["last_name"]):
        cleaned["full_name"] = " ".join(
            part for part in (cleaned["first_name"], cleaned["last_name"]) if part
        )
    if not cleaned["location"]:
        cleaned["location"] = ", ".join(
            part for part in (cleaned["city"], cleaned["state"]) if part
        )
    minimum, maximum = (
        cleaned["salary_expectation_min"],
        cleaned["salary_expectation_max"],
    )
    if minimum and maximum and float(minimum) > float(maximum):
        raise ValueError("Minimum salary expectation cannot exceed the maximum.")
    return cleaned


def profile_completeness(profile: Mapping[str, str]) -> dict[str, Any]:
    required = ("full_name", "email", "phone", "location")
    missing = [
        PROFILE_FIELD_MAP[name].label for name in required if not profile.get(name)
    ]
    recommended = (
        "linkedin_url",
        "years_of_experience",
        "career_profile",
        "target_seniority",
        "work_authorization",
    )
    missing_recommended = [
        PROFILE_FIELD_MAP[name].label for name in recommended if not profile.get(name)
    ]
    total = len(required) + len(recommended)
    filled = total - len(missing) - len(missing_recommended)
    return {
        "ready": not missing,
        "missing_required": missing,
        "missing_recommended": missing_recommended,
        "percent": round(100 * filled / total) if total else 0,
    }


def profile_fields_as_dicts() -> list[dict[str, Any]]:
    return [
        {
            "name": field.name,
            "label": field.label,
            "type": field.field_type,
            "sensitivity": field.sensitivity,
            "max_length": field.max_length,
            "options": list(field.options),
            "help": field.help,
        }
        for field in PROFILE_FIELDS
    ]


def scoring_profile(
    profile: Mapping[str, str], matching_preferences: Mapping[str, Any]
) -> dict[str, Any]:
    """Build the profile mapping consumed by aira.scoring.score_job."""

    def number(value: str) -> float | None:
        try:
            return float(value) if value else None
        except ValueError:
            return None

    years = number(profile.get("years_of_experience", ""))
    return {
        "verified_skills": list(matching_preferences.get("verified_skills", [])),
        "years_of_experience": int(years) if years is not None else None,
        "target_seniority": profile.get("target_seniority") or None,
        "career_profile": profile.get("career_profile") or None,
        "locations": list(matching_preferences.get("locations", [])),
        "workplace_types": list(matching_preferences.get("workplace_types", [])),
        "salary_expectation_min": number(profile.get("salary_expectation_min", "")),
        "salary_expectation_max": number(profile.get("salary_expectation_max", "")),
        "currency": profile.get("salary_currency") or None,
        "work_authorization": profile.get("work_authorization", ""),
        "sponsorship_required": profile.get("sponsorship_required", ""),
        "relocation": profile.get("relocation", ""),
        "citizenship": profile.get("citizenship", ""),
        "security_clearance": profile.get("security_clearance", ""),
    }


# ---------------------------------------------------------------------------
# Standard answers
# ---------------------------------------------------------------------------


def list_standard_answers(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        "SELECT canonical_field, value, confirmed, sensitivity, updated_at FROM standard_answers ORDER BY canonical_field"
    ).fetchall()
    return [dict(row) | {"confirmed": bool(row["confirmed"])} for row in rows]


def standard_answers_map(connection: sqlite3.Connection) -> dict[str, dict[str, Any]]:
    return {
        row["canonical_field"]: {"value": row["value"], "confirmed": row["confirmed"]}
        for row in list_standard_answers(connection)
    }


def save_standard_answer(
    connection: sqlite3.Connection,
    *,
    canonical_field: str,
    value: str,
    confirmed: bool,
    sensitivity: str,
) -> dict[str, Any]:
    canonical_field = canonical_field.strip()
    if not re.fullmatch(r"[a-z][a-z0-9_]{1,60}", canonical_field):
        raise ValueError("Standard answer field names must be lowercase identifiers.")
    if canonical_field not in CANONICAL_FIELDS:
        # Only a question Aira can map to an application form has a standard answer.
        raise ValueError(f"Unknown standard answer field: {canonical_field}.")
    value = value.strip()
    if len(value) > 2000:
        raise ValueError("Standard answers must be 2,000 characters or fewer.")
    if sensitivity not in {"public", "personal", "sensitive"}:
        raise ValueError("Sensitivity must be public, personal, or sensitive.")
    if sensitivity == "sensitive" and confirmed and not value:
        raise ValueError("Enter a value before confirming a sensitive answer.")
    connection.execute(
        """INSERT INTO standard_answers (canonical_field, value, confirmed, sensitivity, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(canonical_field) DO UPDATE SET value = excluded.value, confirmed = excluded.confirmed,
            sensitivity = excluded.sensitivity, updated_at = excluded.updated_at""",
        (
            canonical_field,
            value,
            int(bool(confirmed)),
            sensitivity,
            datetime.now(timezone.utc).isoformat(),
        ),
    )
    row = connection.execute(
        "SELECT canonical_field, value, confirmed, sensitivity, updated_at FROM standard_answers WHERE canonical_field = ?",
        (canonical_field,),
    ).fetchone()
    return dict(row) | {"confirmed": bool(row["confirmed"])}


def delete_standard_answer(
    connection: sqlite3.Connection, canonical_field: str
) -> bool:
    return (
        connection.execute(
            "DELETE FROM standard_answers WHERE canonical_field = ?", (canonical_field,)
        ).rowcount
        > 0
    )


def sync_profile_to_answers(
    connection: sqlite3.Connection, profile: Mapping[str, str]
) -> None:
    """Mirror non-sensitive profile values into unconfirmed standard answers.

    Sensitive fields are intentionally not mirrored; the user confirms those
    explicitly in the standard answers editor.
    """
    now = datetime.now(timezone.utc).isoformat()
    for field in PROFILE_FIELDS:
        if field.sensitivity == "sensitive":
            continue
        value = profile.get(field.name, "")
        if not value:
            continue
        connection.execute(
            """INSERT INTO standard_answers (canonical_field, value, confirmed, sensitivity, updated_at)
            VALUES (?, ?, 1, ?, ?)
            ON CONFLICT(canonical_field) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            WHERE standard_answers.sensitivity != 'sensitive'""",
            (field.name, value, field.sensitivity, now),
        )
