"""Keep sensitive profile values from anyone but the candidate.

Work authorization, visa, demographics and salary are entered and confirmed by the candidate only.
A coach, administrator or auditor learns that a field is *set*, never its value, unless the
organization's policy allows it and the candidate consented (and then only for an assigned coach).
The same rule covers answers to held questions, which can carry the same information.
"""

from __future__ import annotations

import copy
import json
from functools import lru_cache
from typing import Any, cast

from aira.candidate import PROFILE_FIELDS
from aira.field_mapping import CANONICAL_FIELDS, map_field

REDACTED = ""


@lru_cache(maxsize=1)
def sensitive_field_names() -> frozenset[str]:
    names = {field.name for field in PROFILE_FIELDS if field.sensitivity == "sensitive"}
    names |= {
        name
        for name, field in CANONICAL_FIELDS.items()
        if field.sensitivity == "sensitive"
    }
    return frozenset(names)


@lru_cache(maxsize=4096)
def label_is_sensitive(label: str) -> bool:
    try:
        canonical = map_field(label).canonical_field
    except Exception:  # noqa: BLE001 - an unmappable label is not known to be sensitive
        return False
    field = CANONICAL_FIELDS.get(canonical) if canonical else None
    return bool(field and field.sensitivity == "sensitive")


def is_sensitive_key(key: str) -> bool:
    return key in sensitive_field_names()


def answers_touch_sensitive(answers: Any) -> list[str]:
    """The labels in a ``{label: value}`` payload that ask for sensitive information."""
    if not isinstance(answers, dict):
        return []
    return [str(label) for label in answers if label_is_sensitive(str(label))]


def _redact_profile(payload: dict[str, Any]) -> dict[str, Any]:
    names = sensitive_field_names()
    marker: dict[str, bool] = {}
    for section in ("candidate_profile", "application_profile"):
        values = payload.get(section)
        if isinstance(values, dict):
            for name in names:
                if name in values:
                    marker[name] = bool(str(values[name] or "").strip())
                    values[name] = REDACTED
    if marker:
        payload["sensitive_set"] = marker
        payload["sensitive_redacted"] = True
    return payload


def _redact_answers(payload: dict[str, Any]) -> dict[str, Any]:
    answers = payload.get("answers")
    if isinstance(answers, list):
        for item in answers:
            if isinstance(item, dict) and (
                item.get("sensitivity") == "sensitive"
                or is_sensitive_key(str(item.get("canonical_field", "")))
            ):
                item["value_set"] = bool(str(item.get("value") or "").strip())
                item["value"] = REDACTED
                item["redacted"] = True
    return payload


def _walk(value: Any) -> Any:
    """Mask sensitive answers wherever a run, a pass or a preparation carries them."""
    if isinstance(value, dict):
        mapping = cast("dict[str, Any]", value)
        for key, inner in list(mapping.items()):
            if key == "answers" and isinstance(inner, dict):
                answers = cast("dict[str, Any]", inner)
                for label in list(answers):
                    if label_is_sensitive(str(label)):
                        answers[label] = REDACTED
            elif key in {
                "questions",
                "unanswered",
                "remembered",
                "fields",
                "custom_answers",
            } and isinstance(inner, list):
                for item in inner:
                    if isinstance(item, dict):
                        entry = cast("dict[str, Any]", item)
                        label = str(entry.get("label") or entry.get("question") or "")
                        canonical = str(entry.get("canonical_field") or "")
                        if label_is_sensitive(label) or is_sensitive_key(canonical):
                            for name in (
                                "value",
                                "answer",
                                "suggested",
                                "suggested_answer",
                            ):
                                if name in entry:
                                    entry[name] = REDACTED
                            entry["redacted"] = True
                    else:
                        _walk(item)
            elif key == "fields" and isinstance(inner, dict):
                fields = cast("dict[str, Any]", inner)
                for name in list(fields):
                    if is_sensitive_key(str(name)) or label_is_sensitive(str(name)):
                        fields[name] = REDACTED
            else:
                _walk(inner)
    elif isinstance(value, list):
        for item in value:
            _walk(item)
    return value


_SETTING_PREFIXES = ("application_", "candidate_", "profile_")


def _setting_is_sensitive(key: str) -> bool:
    name = str(key)
    for prefix in _SETTING_PREFIXES:
        if name.startswith(prefix):
            name = name[len(prefix) :]
            break
    return is_sensitive_key(name)


def _redact_json_text(value: str, column: str = "") -> str:
    """A JSON document stored in a text column, with sensitive answers masked."""
    stripped = value.lstrip()
    if not stripped or stripped[0] not in "{[":
        return value
    try:
        parsed = json.loads(value)
    except ValueError:
        return value
    # a column named like a key the walker knows (``answers``, ``unanswered``...) is that key's value
    masked = _walk(copy.deepcopy({column: parsed}))[column] if column else parsed
    masked = _walk(copy.deepcopy(masked))
    if isinstance(masked, dict):
        masked = _redact_answers(_redact_profile(masked))
    return value if masked == parsed else json.dumps(masked, ensure_ascii=False)


# Tables a candidate's own workspace holds that staff are never given, even in an export.
PRIVATE_TABLES = (
    "leads",
    "lead_lists",
    "lead_keys",
    "lead_searches",
    "lead_runs",
    "lead_settings",  # holds the sender's identity and mail server (the password is a separate encrypted secret)
    "lead_messages",
    "lead_suppressions",
    "lead_calls",
)


def redact_export(data: dict[str, Any]) -> dict[str, Any]:
    """A candidate's account export with sensitive values masked, for anyone who may not see them."""
    tables = data.get("tables")
    if not isinstance(tables, dict):
        return data
    for private in PRIVATE_TABLES:
        # a candidate's leads are third parties' personal data kept for that person; staff never see them
        tables.pop(private, None)
    for name, rows in tables.items():
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            row = cast("dict[str, Any]", row)
            if name == "settings" and _setting_is_sensitive(str(row.get("key", ""))):
                row["value"] = REDACTED
                continue
            if name == "standard_answers" and (
                is_sensitive_key(str(row.get("canonical_field", "")))
                or row.get("sensitivity") == "sensitive"
            ):
                row["value"] = REDACTED
                continue
            for column, value in list(row.items()):
                if isinstance(value, str):
                    row[column] = _redact_json_text(str(value), column)
    data["sensitive_redacted"] = True
    return data


def redact(kind: str, payload: dict[str, Any]) -> dict[str, Any]:
    if kind == "profile_read":
        return _redact_answers(_redact_profile(payload))
    if kind == "runs_read":
        return _walk(payload)  # type: ignore[no-any-return]
    return payload
