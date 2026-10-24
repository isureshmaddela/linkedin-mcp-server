"""Pure helpers for the LinkedIn Easy Apply flow (no browser access in here).

The browser-driving half lives in ``extractor.LinkedInExtractor.easy_apply``;
everything that can be reasoned about without a page — the per-locale label
table, the question model, answer resolution, button classification and the
result shape — is here so it can be unit-tested directly.

Locale policy (``AGENTS.md`` Scraping Rules)
--------------------------------------------
Structure first: the apply control is searched *inside the job header* (the
smallest ancestor of the page ``<h1>`` that holds a button); the application
form is the visible ``role="dialog"``; questions are ``input``/``select``/
``textarea``/``fieldset`` elements labelled through ``<label for>``,
``aria-label``, ``aria-labelledby`` or ``<legend>``; the step button is the
last text button in the dialog's ``<footer>``; inline errors are
``role="alert"`` / ``aria-invalid`` / ``aria-describedby`` targets; an
external apply is recognised by *behaviour* (a popup or a navigation away
from ``/jobs/view/``), never by its wording; a ``target="_blank"`` anchor is
external by structure. Contact fields are mapped through ``autocomplete`` and
``type`` attributes before any label text is consulted.

Where LinkedIn offers no structural signal the visible text is the only one:
telling Easy Apply from an external Apply button before clicking, the
"Applied" state, Next / Review / Submit application on the footer button, the
"Application sent" confirmation, the follow-company checkbox, the placeholder
option of a ``<select>``, the contact-step field names and the sensitive
question keywords. Every one of those strings lives in
:data:`EASY_APPLY_LABELS`, keyed by UI locale, and nowhere else.

LOCALE LIMITATION: only ``en`` is populated. The MCP browser is launched with
an en-US locale (see ``BrowserManager``), so this normally holds. In another
LinkedIn UI language the flow degrades safely rather than guessing: with no
recognised apply control it returns ``failed`` (listing the controls it saw)
instead of clicking something; with an unrecognised footer button and
``submit=False`` it stops and returns ``filled`` without clicking; with
``submit=True`` it still advances and only reports ``submitted`` once the
form controls are gone from the dialog or the header shows the applied state.
Add a locale by appending an ``EasyApplyLabels`` entry; nothing else changes.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

QuestionKind = Literal[
    "text",
    "textarea",
    "select",
    "radio",
    "checkbox",
    "file",
    "date",
    "number",
    "email",
    "tel",
    "url",
    "unknown",
]
QuestionStatus = Literal[
    "filled", "missing", "needs_confirmation", "unknown", "unsupported", "skipped"
]
StepAction = Literal["next", "review", "submit", "unknown"]
ApplyKind = Literal["easy_apply", "external_anchor", "apply"]
Status = Literal[
    "submitted", "filled", "needs_review", "external", "already_applied", "failed"
]

#: Canonical profile fields the contact step is filled from.
PROFILE_FIELDS = (
    "first_name",
    "last_name",
    "email",
    "phone",
    "phone_country",
    "city",
    "linkedin_url",
)

#: Statuses that make a *required* question count as unanswered.
UNANSWERED_STATUSES = frozenset({"missing", "needs_confirmation", "unsupported"})

_TEXT_KINDS = frozenset(
    {"text", "textarea", "date", "number", "email", "tel", "url", "unknown"}
)
_MAX_STEPS = 12


@dataclass(frozen=True)
class EasyApplyLabels:
    """Visible strings for one LinkedIn UI locale (see module docstring)."""

    easy_apply: tuple[str, ...]
    """Button text / aria-label fragment of the in-LinkedIn apply control."""
    apply: tuple[str, ...]
    """Leading word of the external apply control ("Apply", "Apply on company website")."""
    applied: tuple[str, ...]
    """Header line that replaces the apply control once an application exists."""
    next: tuple[str, ...]
    review: tuple[str, ...]
    submit: tuple[str, ...]
    application_sent: tuple[str, ...]
    follow_company: tuple[str, ...]
    """Fragment of the review-step "Follow <company>" checkbox label."""
    select_placeholder: tuple[str, ...]
    """Text of the no-choice first option LinkedIn puts in its selects."""
    contact_fields: tuple[tuple[str, str], ...]
    """(label fragment, canonical profile field) pairs for the contact step;
    only consulted after ``autocomplete`` and ``type`` attributes."""
    sensitive: tuple[str, ...]
    """Label fragments that mark a question as sensitive (work authorization,
    visa, compensation, demographics, legal attestations); such questions are
    reported as ``needs_confirmation`` instead of ``missing``."""


EASY_APPLY_LABELS: dict[str, EasyApplyLabels] = {
    "en": EasyApplyLabels(
        easy_apply=("Easy Apply",),
        apply=("Apply",),
        applied=("Applied",),
        next=("Next", "Continue"),
        review=("Review",),
        submit=("Submit application", "Submit"),
        application_sent=(
            "Application sent",
            "Your application was sent",
            "application has been submitted",
        ),
        follow_company=("Follow",),
        select_placeholder=("Select an option", "Select", "Please select"),
        contact_fields=(
            ("first name", "first_name"),
            ("last name", "last_name"),
            ("email", "email"),
            ("phone country code", "phone_country"),
            ("country code", "phone_country"),
            ("mobile phone number", "phone"),
            ("phone number", "phone"),
            ("phone", "phone"),
            ("city", "city"),
            ("location (city)", "city"),
            ("linkedin profile", "linkedin_url"),
            ("linkedin url", "linkedin_url"),
        ),
        sensitive=(
            "authorized to work",
            "authorised to work",
            "work authorization",
            "work authorisation",
            "legally authorized",
            "legally authorised",
            "sponsorship",
            "visa",
            "citizen",
            "right to work",
            "work permit",
            "salary",
            "compensation",
            "pay expectation",
            "desired pay",
            "hourly rate",
            "gender",
            "race",
            "ethnicity",
            "veteran",
            "disability",
            "sexual orientation",
            "date of birth",
            "age ",
            "criminal",
            "background check",
            "drug test",
            "certify",
            "attest",
            "agree to",
            "privacy policy",
            "terms and conditions",
        ),
    ),
}

LOCALE_LIMITATION = (
    "Only the 'en' LinkedIn UI locale is covered by EASY_APPLY_LABELS. In other "
    "locales the tool never guesses: it returns 'failed' when it cannot name the "
    "apply control, stops with 'filled' at an unrecognised footer button when "
    "submit is false, and reports 'submitted' only on structural confirmation."
)


# ---------------------------------------------------------------------------
# Text helpers
# ---------------------------------------------------------------------------


def normalize_text(value: Any) -> str:
    """Collapse whitespace and trim; non-strings become ''."""
    if not isinstance(value, str):
        return ""
    return re.sub(r"\s+", " ", value).strip()


def normalize_label(value: Any) -> str:
    """Comparison key for a question label: whitespace-collapsed, casefolded,
    without a trailing required marker (``*``) or colon."""
    text = normalize_text(value)
    text = re.sub(r"[\s*:]+$", "", text)
    text = re.sub(r"^\*+\s*", "", text)
    return text.casefold()


def _labels(attribute: str) -> tuple[str, ...]:
    """All locales' values for one :class:`EasyApplyLabels` attribute."""
    found: list[str] = []
    for table in EASY_APPLY_LABELS.values():
        found.extend(getattr(table, attribute))
    return tuple(found)


def _word_pattern(label: str) -> re.Pattern[str]:
    return re.compile(rf"(?<!\w){re.escape(label)}(?!\w)", re.IGNORECASE)


def label_in_text(text: Any, labels: tuple[str, ...]) -> bool:
    """True when any label appears in ``text`` as a whole word (case-insensitive)."""
    haystack = normalize_text(text)
    if not haystack:
        return False
    return any(_word_pattern(label).search(haystack) for label in labels)


def label_starts_text(text: Any, labels: tuple[str, ...]) -> bool:
    """True when ``text`` starts with any label as a whole word."""
    haystack = normalize_text(text)
    if not haystack:
        return False
    return any(
        re.match(rf"{re.escape(label)}(?!\w)", haystack, re.IGNORECASE)
        for label in labels
    )


def label_equals(text: Any, labels: tuple[str, ...]) -> bool:
    key = normalize_label(text)
    return bool(key) and any(key == normalize_label(label) for label in labels)


# ---------------------------------------------------------------------------
# Apply control (job header)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ApplyControl:
    kind: ApplyKind
    key: str
    text: str
    href: str = ""


def choose_apply_control(candidates: list[dict[str, Any]]) -> ApplyControl | None:
    """Pick the apply control among the interactive elements of the job header.

    ``candidates`` are the header's visible buttons/anchors in DOM order as
    read by the extractor (``tag``, ``text``, ``aria_label``, ``href``,
    ``target``, ``disabled``, ``expanded``, ``pressed``, ``key``). Menu openers
    (``aria-expanded``) and toggles (``aria-pressed``, e.g. Save) are skipped
    structurally. Among the rest the Easy Apply wording wins, then an anchor
    that opens a new tab (structural external apply), then a control whose
    text starts with the external "Apply" wording. Returns None when nothing
    matches so the caller can report instead of clicking blindly.
    """
    easy = _labels("easy_apply")
    apply = _labels("apply")
    usable = [
        c
        for c in candidates
        if not c.get("disabled") and not c.get("expanded") and not c.get("pressed")
    ]
    for c in usable:
        if label_in_text(c.get("text"), easy) or label_in_text(
            c.get("aria_label"), easy
        ):
            return ApplyControl("easy_apply", c["key"], normalize_text(c.get("text")))
    for c in usable:
        if c.get("tag") == "a" and normalize_text(c.get("target")) == "_blank":
            return ApplyControl(
                "external_anchor",
                c["key"],
                normalize_text(c.get("text")),
                normalize_text(c.get("href")),
            )
    for c in usable:
        if label_starts_text(c.get("text"), apply) or label_starts_text(
            c.get("aria_label"), apply
        ):
            kind: ApplyKind = "external_anchor" if c.get("tag") == "a" else "apply"
            return ApplyControl(
                kind,
                c["key"],
                normalize_text(c.get("text")),
                normalize_text(c.get("href")),
            )
    return None


def header_shows_applied(header_text: str) -> bool:
    """True when a header line (anchors removed) starts with the Applied label.

    Company names are anchors on the job page, so "Applied Materials" never
    reaches this check; the extractor strips anchor text before calling it.
    """
    if not header_text:
        return False
    for label in _labels("applied"):
        if re.search(
            rf"(?:^|\n)\s*{re.escape(label)}(?!\w)", header_text, re.IGNORECASE
        ):
            return True
    return False


def describe_controls(candidates: list[dict[str, Any]]) -> str:
    """Short diagnostic list of header controls for error messages."""
    parts = []
    for c in candidates[:12]:
        text = normalize_text(c.get("text")) or normalize_text(c.get("aria_label"))
        if text:
            parts.append(f"{c.get('tag', '?')}:{text[:40]}")
    return ", ".join(parts) or "none"


# ---------------------------------------------------------------------------
# Questions
# ---------------------------------------------------------------------------


@dataclass
class EasyApplyQuestion:
    """One form question as LinkedIn shows it, plus the hidden control handle."""

    label: str
    kind: str
    required: bool
    options: list[str] = field(default_factory=list)
    value: str | None = None
    status: str = "unknown"
    message: str = ""
    # Internal handles (not exported in the tool result).
    key: str = ""
    option_indices: list[int] = field(default_factory=list)
    control_id: str = ""
    name: str = ""
    autocomplete: str = ""
    input_type: str = ""
    error: str = ""
    combobox: bool = False
    group: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "label": self.label,
            "kind": self.kind,
            "required": self.required,
            "options": list(self.options),
            "value": self.value,
            "status": self.status,
            "message": self.message,
        }


def _is_placeholder_option(index: int, value: str, text: str, disabled: bool) -> bool:
    if disabled or value == "":
        return True
    return index == 0 and label_equals(text, _labels("select_placeholder"))


def questions_from_dom(raw: list[dict[str, Any]]) -> list[EasyApplyQuestion]:
    """Turn the extractor's raw dialog scan into question objects.

    Select placeholders (empty/disabled option, or a first option with the
    per-locale "Select an option" wording) are dropped from ``options`` and
    never count as a value.
    """
    questions: list[EasyApplyQuestion] = []
    for item in raw:
        kind = str(item.get("kind") or "unknown")
        options: list[str] = []
        indices: list[int] = []
        value = item.get("value")
        value = normalize_text(value) if isinstance(value, str) else None
        if kind == "select":
            raw_options = [normalize_text(o) for o in item.get("options", [])]
            raw_values = [str(v) for v in item.get("option_values", [])]
            for index, text in enumerate(raw_options):
                option_value = raw_values[index] if index < len(raw_values) else text
                if _is_placeholder_option(index, option_value, text, False):
                    continue
                options.append(text)
                indices.append(index)
            selected_index = item.get("selected_index")
            if isinstance(selected_index, int) and selected_index >= 0:
                selected_value = str(item.get("selected_value", ""))
                if _is_placeholder_option(
                    selected_index,
                    selected_value,
                    value or "",
                    bool(item.get("selected_disabled")),
                ):
                    value = None
            else:
                value = None
        elif kind in {"radio", "checkbox"}:
            options = [normalize_text(o) for o in item.get("options", [])]
            indices = list(range(len(options)))
        if value == "":
            value = None
        questions.append(
            EasyApplyQuestion(
                label=normalize_text(item.get("label")) or "(unlabelled)",
                kind=kind,
                required=bool(item.get("required")),
                options=options,
                value=value,
                key=str(item.get("key", "")),
                option_indices=indices,
                control_id=str(item.get("control_id", "") or ""),
                name=str(item.get("name", "") or ""),
                autocomplete=str(item.get("autocomplete", "") or ""),
                input_type=str(item.get("input_type", "") or ""),
                error=normalize_text(item.get("error")),
                combobox=bool(item.get("combobox")),
                group=bool(item.get("group")),
            )
        )
    return questions


def has_value(question: EasyApplyQuestion) -> bool:
    """Whether the question currently holds a usable answer."""
    if question.value is None or not question.value.strip():
        return False
    if question.kind == "checkbox" and not question.group:
        return question.value.strip().lower() == "true"
    return True


def is_sensitive(label: str) -> bool:
    key = normalize_label(label)
    return any(fragment.casefold() in key for fragment in _labels("sensitive"))


def is_follow_company(question: EasyApplyQuestion) -> bool:
    """The review-step "Follow <company>" checkbox.

    Structural half: an optional single checkbox whose id/name mentions
    "follow" (LinkedIn's own identifier); text half: the per-locale label.
    """
    if question.kind != "checkbox" or question.group or question.required:
        return False
    handle = f"{question.control_id} {question.name}".casefold()
    if "follow" in handle:
        return True
    return label_starts_text(question.label, _labels("follow_company"))


def truthy(value: str) -> bool:
    return normalize_text(value).casefold() in {
        "yes",
        "y",
        "true",
        "1",
        "on",
        "checked",
        "x",
    }


def lookup_answer(label: str, answers: Mapping[str, str]) -> str | None:
    """Find the user's answer for a label: exact, then normalized match."""
    if not answers:
        return None
    if label in answers and answers[label] is not None:
        return str(answers[label])
    key = normalize_label(label)
    for candidate, value in answers.items():
        if value is not None and normalize_label(candidate) == key:
            return str(value)
    return None


_AUTOCOMPLETE_FIELDS = {
    "given-name": "first_name",
    "family-name": "last_name",
    "email": "email",
    "tel": "phone",
    "tel-national": "phone",
    "tel-country-code": "phone_country",
    "address-level2": "city",
    "url": "linkedin_url",
}


def canonical_contact_field(question: EasyApplyQuestion) -> str | None:
    """Map a contact-step question to a canonical profile field.

    Structural signals first (``autocomplete`` tokens, ``type=email`` /
    ``type=tel``), then the per-locale label fragments.
    """
    tokens = question.autocomplete.casefold().split()
    for token in tokens:
        if token in _AUTOCOMPLETE_FIELDS:
            return _AUTOCOMPLETE_FIELDS[token]
    if question.input_type == "email":
        return "email"
    if question.input_type == "tel":
        return "phone"
    key = normalize_label(question.label)
    for fragment, canonical in _labels_contact_fields():
        if fragment in key:
            return canonical
    return None


def _labels_contact_fields() -> tuple[tuple[str, str], ...]:
    pairs: list[tuple[str, str]] = []
    for table in EASY_APPLY_LABELS.values():
        pairs.extend(
            (frag.casefold(), canonical) for frag, canonical in table.contact_fields
        )
    # Longer fragments first so "phone country code" beats "phone".
    pairs.sort(key=lambda pair: -len(pair[0]))
    return tuple(pairs)


def match_option(
    options: list[str], value: str, *, contains: bool = False
) -> int | None:
    """Index of the option equal to ``value`` (normalized), else ``None``.

    With ``contains=True`` an option that merely contains the value also
    matches (used for phone country codes such as "+1" against
    "United States (+1)"); exact matches still win.
    """
    key = normalize_label(value)
    if not key:
        return None
    for index, option in enumerate(options):
        if normalize_label(option) == key:
            return index
    if contains:
        for index, option in enumerate(options):
            if key in normalize_label(option):
                return index
    return None


@dataclass(frozen=True)
class FillAction:
    """One browser action the extractor performs for a question."""

    key: str
    action: Literal["fill", "select", "radio", "check", "file"]
    value: str = ""
    index: int = -1
    option: str = ""
    checked: bool = False


def _split_multi(value: str) -> list[str]:
    return [part for part in re.split(r"[;,|]", value) if normalize_text(part)]


def plan_fill(
    questions: list[EasyApplyQuestion],
    answers: Mapping[str, str],
    profile: Mapping[str, str],
    resume_path: str | None,
) -> tuple[list[FillAction], set[str]]:
    """Decide what to fill on the current step.

    Returns the actions and the *labels* of questions whose provided answer
    could not be applied (no matching option); those are reported as
    ``missing`` with an explanatory message so nothing is ever guessed. Notes
    for the report are left in ``question.message`` (keyed by label downstream
    because control keys are regenerated on every DOM read).

    Precedence: ``answers`` (by label) always wins and overwrites what
    LinkedIn prefilled; ``profile`` only fills contact fields that are still
    empty (text inputs) or picks the matching option of a contact select
    (LinkedIn's email and phone-country selects only offer verified values).
    """
    actions: list[FillAction] = []
    unmatched: set[str] = set()
    for q in questions:
        answer = lookup_answer(q.label, answers)
        if q.kind == "file":
            if resume_path:
                actions.append(FillAction(q.key, "file", value=resume_path))
            continue
        if answer is None and is_follow_company(q):
            if has_value(q):
                actions.append(FillAction(q.key, "check", checked=False))
                q.message = "Unchecked (follow company)."
            continue
        if answer is None:
            canonical = canonical_contact_field(q)
            profile_value = (
                normalize_text(profile.get(canonical, "")) if canonical else ""
            )
            if not profile_value:
                continue
            if q.kind == "select":
                index = match_option(
                    q.options, profile_value, contains=canonical == "phone_country"
                )
                if index is not None and q.options[index] != (q.value or ""):
                    actions.append(
                        FillAction(q.key, "select", index=q.option_indices[index])
                    )
            elif q.kind in _TEXT_KINDS and q.kind != "unknown" and not has_value(q):
                actions.append(FillAction(q.key, "fill", value=profile_value))
            continue
        if q.kind in _TEXT_KINDS and q.kind != "unknown":
            actions.append(FillAction(q.key, "fill", value=answer))
        elif q.kind == "select":
            index = match_option(q.options, answer)
            if index is None:
                unmatched.add(q.label)
                q.message = f"No option matches {answer!r}. Options: {q.options}"
            else:
                actions.append(
                    FillAction(q.key, "select", index=q.option_indices[index])
                )
        elif q.kind == "radio":
            index = match_option(q.options, answer)
            if index is None:
                unmatched.add(q.label)
                q.message = f"No option matches {answer!r}. Options: {q.options}"
            else:
                actions.append(FillAction(q.key, "radio", option=q.options[index]))
        elif q.kind == "checkbox":
            if q.group:
                wanted = {normalize_label(part) for part in _split_multi(answer)}
                known = {normalize_label(option) for option in q.options}
                if wanted and not wanted <= known:
                    unmatched.add(q.label)
                    q.message = f"No option matches {answer!r}. Options: {q.options}"
                    continue
                for option in q.options:
                    actions.append(
                        FillAction(
                            q.key,
                            "check",
                            option=option,
                            checked=normalize_label(option) in wanted,
                        )
                    )
            else:
                actions.append(FillAction(q.key, "check", checked=truthy(answer)))
        else:
            unmatched.add(q.label)
            q.message = f"Unsupported control kind {q.kind!r}."
    return actions, unmatched


def finalize_statuses(
    questions: list[EasyApplyQuestion],
    unmatched: set[str],
    messages: Mapping[str, str],
) -> None:
    """Assign status/message to each question after the step was filled.

    ``unmatched`` and ``messages`` are keyed by question label (notes from the
    planning pass and the browser actions). LinkedIn's own inline error, when
    present, always wins the message slot and turns the question into one
    that needs review.
    """
    for q in questions:
        note = messages.get(q.label, "")
        if q.label in unmatched:
            q.status = "needs_confirmation" if is_sensitive(q.label) else "missing"
            q.message = note or q.message or "The provided answer could not be applied."
        elif q.kind == "unknown":
            q.status = "unsupported"
            q.message = note or "Unsupported control."
        elif has_value(q):
            q.status = "filled"
            q.message = note
        elif q.required:
            if is_sensitive(q.label):
                q.status = "needs_confirmation"
                q.message = (
                    note or "Sensitive question: a confirmed answer is required."
                )
            else:
                q.status = "missing"
                q.message = note or "Required question without an answer."
        else:
            q.status = "skipped"
            q.message = note or "Optional question left blank."
        if q.error:
            if q.status == "filled" or q.status == "skipped":
                q.status = "needs_confirmation" if is_sensitive(q.label) else "missing"
            q.message = q.error


def unanswered_of(questions: list[EasyApplyQuestion]) -> list[EasyApplyQuestion]:
    return [q for q in questions if q.required and q.status in UNANSWERED_STATUSES]


def step_signature(
    questions: list[EasyApplyQuestion], primary_text: str
) -> tuple[Any, ...]:
    """Identity of a dialog step: its question labels/kinds and footer button."""
    return tuple((q.label, q.kind) for q in questions) + (
        normalize_label(primary_text),
    )


# ---------------------------------------------------------------------------
# Dialog buttons and confirmation
# ---------------------------------------------------------------------------


def classify_step_button(text: str, aria_label: str = "") -> StepAction:
    """Classify the footer's primary button from the per-locale table.

    Submit is checked first so that "Submit application" never falls through
    to a looser match. Unknown wording returns ``unknown`` and the extractor
    decides (see module docstring) whether clicking is safe.
    """
    for candidate in (text, aria_label):
        if label_equals(candidate, _labels("submit")) or label_starts_text(
            candidate, _labels("submit")
        ):
            return "submit"
    for candidate in (text, aria_label):
        if label_equals(candidate, _labels("review")) or label_starts_text(
            candidate, _labels("review")
        ):
            return "review"
    for candidate in (text, aria_label):
        if label_equals(candidate, _labels("next")) or label_starts_text(
            candidate, _labels("next")
        ):
            return "next"
    return "unknown"


def application_sent_labels() -> tuple[str, ...]:
    """Every locale's post-submit confirmation wording (for in-page waits)."""
    return _labels("application_sent")


def dialog_text_confirms_sent(text: str) -> bool:
    """The post-submit dialog wording, per locale table."""
    haystack = normalize_text(text).casefold()
    return any(label.casefold() in haystack for label in application_sent_labels())


def external_url_from_popup(url: str, fallback: str = "") -> str:
    """Prefer the employer URL LinkedIn's externalApply redirect wraps in ``url=``."""
    from urllib.parse import parse_qs, urlparse

    if not url or url == "about:blank":
        return fallback
    parsed = urlparse(url)
    if "/jobs/view/externalApply/" in parsed.path:
        wrapped = parse_qs(parsed.query).get("url")
        if wrapped and wrapped[0]:
            return wrapped[0]
    return url


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------


def build_result(
    status: Status,
    questions: list[EasyApplyQuestion],
    *,
    evidence: dict[str, Any],
    steps: list[str],
    error: str = "",
    external_url: str | None = None,
) -> dict[str, Any]:
    """The tool's return shape (docs/auto-apply-spec.md section 4)."""
    return {
        "status": status,
        "questions": [q.to_dict() for q in questions],
        "unanswered": [q.to_dict() for q in unanswered_of(questions)],
        "external_url": external_url,
        "evidence": {
            "page_text": str(evidence.get("page_text", "")),
            "confirmation_text": str(evidence.get("confirmation_text", "")),
            "screenshot_base64": evidence.get("screenshot_base64"),
            "url": str(evidence.get("url", "")),
        },
        "steps": list(steps),
        "error": error,
    }


def validate_job_id(job_id: Any) -> str:
    value = normalize_text(job_id)
    if not re.fullmatch(r"\d{1,20}", value):
        raise ValueError("Invalid LinkedIn job ID: expected digits only.")
    return value


def validate_resume_path(resume_path: str | None) -> str | None:
    if resume_path is None or not str(resume_path).strip():
        return None
    path = Path(str(resume_path)).expanduser()
    if not path.is_file():
        raise ValueError(f"resume_path does not point to a file: {resume_path}")
    return str(path)


MAX_STEPS = _MAX_STEPS
