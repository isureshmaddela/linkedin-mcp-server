"""Browser-driven application form engine (assisted and automatic applications).

Opens the employer's application page in a real Chromium window driven through
patchright's **sync** API, fills every form control it can account for, and
either stops there (``submit=False``: the user presses Submit after seeing the
filled form) or clicks the form's own submit control and waits for the ATS's
success marker. Callers run :func:`run` in a worker thread; the sync API refuses
to run inside an asyncio event loop.

Hard rules, enforced here:

* nothing is ever invented: a value comes from the approved packet, from the
  user's answers for this run, or from ``aira.field_mapping`` prefill over the
  profile and *confirmed* standard answers; a required question with no source
  stays empty and is reported in ``ApplyOutcome.unanswered``;
* sensitive questions (work authorization, visa, compensation, demographics,
  legal attestations) are filled only from a confirmed standard answer, a
  reviewed packet value or an explicit answer for this run;
* ``submit=True`` refuses to submit while anything required is unanswered;
* success is claimed only when the ATS shows its success wording or URL;
* nothing is clicked except form controls and the form's own submit button;
  CAPTCHAs, verification steps and sign-in walls end the run with
  ``needs_review``; popups are closed; downloads are refused.

Locale note: ATS "already applied" notices and success wording are the only
signals that exist for those states, so they are matched against the explicit
per-language table :data:`CONFIRMATION_PHRASES` (English, Spanish, French,
German, Portuguese, Italian and Dutch) plus locale-independent URL patterns.
Forms in other languages still fill (field mapping goes through adapter
``name``/``id`` hints first), but their confirmation is reported as ``failed``
with the page text as evidence rather than guessed. Add a language by adding an
entry to the table; nothing else changes.
"""

from __future__ import annotations

import re
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from aira.field_mapping import (
    CANONICAL_FIELDS,
    FieldMapping,
    map_field,
    normalize_label,
    prefill_fields,
)

if TYPE_CHECKING:
    from patchright.sync_api import Browser, Frame, Locator, Page

__all__ = [
    "ATS_NAMES",
    "CONFIRMATION_PHRASES",
    "OUTCOME_STATUSES",
    "QUESTION_KINDS",
    "QUESTION_STATUSES",
    "UNANSWERED_STATUSES",
    "ApplyEvidence",
    "ApplyOutcome",
    "ApplyRequest",
    "ConfirmationPhrases",
    "FormQuestion",
    "confirmation_language",
    "detect_ats",
    "extract_confirmation_number",
    "run",
]

ATS_NAMES = (
    "greenhouse",
    "lever",
    "ashby",
    "smartrecruiters",
    "workday",
    "generic",
    "linkedin",
)
# Answers that mean "this site does not let a program in", not "the page is broken".
_BLOCKED_STATUSES = frozenset({401, 403, 429, 451})
OUTCOME_STATUSES = (
    "submitted",
    "filled",
    "needs_review",
    "failed",
    "unsupported",
    "already_applied",
)
QUESTION_KINDS = (
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
)
QUESTION_STATUSES = (
    "filled",
    "missing",
    "needs_confirmation",
    "unknown",
    "unsupported",
    "skipped",
)
# Required questions in these states block a submission.
UNANSWERED_STATUSES = frozenset(
    {"missing", "needs_confirmation", "unknown", "unsupported"}
)

MAX_PAGE_TEXT = 20_000
ACTION_TIMEOUT_SECONDS = 15
SETTLE_SECONDS = 5
POLL_MILLISECONDS = 250
# Finish in browser: steps one session waits on at most (a sign-in, then a CAPTCHA, then a code, ...).
MAX_HANDOFFS = 4


# --------------------------------------------------------------------------- #
# Public data types (the contract in docs/auto-apply-spec.md section 2)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    job_id: str
    source: str
    application_url: str
    fields: list[dict[str, Any]]
    profile: Mapping[str, str]
    standard_answers: Mapping[str, Mapping[str, Any]]
    resume_path: str | None
    cover_letter_path: str | None
    cover_letter_text: str
    submit: bool
    headless: bool = True
    screenshot_dir: str | None = None
    timeout_seconds: int = 120
    extra_answers: Mapping[str, str] = field(default_factory=dict)
    # Workday only: the tenant's candidate account (aira.workday.WorkdayAccount), or None.
    workday_account: Any = None
    # Finish in browser (an aira.handoff.Handoff): a CAPTCHA, sign-in or code step waits for the person in the
    # window instead of ending the run; None holds the run as before.
    handoff: Any = None
    # The cookies and storage to start from (kept by the held run), and where to keep this run's when it is held
    # for a step a person must do, so Finish in browser starts from the same browser state.
    storage_state_path: str | None = None
    keep_state_path: str | None = None


@dataclass(slots=True)
class FormQuestion:
    label: str
    kind: str
    required: bool
    options: list[str]
    provider_field_id: str
    canonical_field: str | None
    value: str | None
    status: str
    message: str


@dataclass(slots=True)
class ApplyEvidence:
    screenshot_path: str | None
    page_text: str
    confirmation_text: str
    confirmation_number: str
    url: str
    submitted_at: str | None


@dataclass(slots=True)
class ApplyOutcome:
    status: str
    ats: str
    questions: list[FormQuestion]
    unanswered: list[FormQuestion]
    evidence: ApplyEvidence
    error: str
    steps: list[str]
    # The step a person must do that held the run ("captcha", "sign_in", "verification"; aira.handoff.HOLD_KINDS),
    # "" for every other outcome. Needs you offers Finish in browser for these.
    hold: str = ""
    # How each Finish in browser wait of this attempt ended ({kind, outcome, seconds}).
    handoffs: list[dict[str, Any]] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# ATS detection and adapters
# --------------------------------------------------------------------------- #


def detect_ats(url: str) -> str:
    """Name the ATS behind ``url`` from its host (``generic`` when unknown)."""
    host = (urlsplit(str(url or "").strip()).hostname or "").casefold()
    if not host:
        return "generic"
    if host.endswith("greenhouse.io"):
        return "greenhouse"
    if host.endswith("lever.co"):
        return "lever"
    if host.endswith("ashbyhq.com"):
        return "ashby"
    if host.endswith("smartrecruiters.com"):
        return "smartrecruiters"
    if "myworkdayjobs.com" in host or host.endswith("myworkdaysite.com"):
        return "workday"
    if host == "linkedin.com" or host.endswith(".linkedin.com"):
        return "linkedin"
    return "generic"


@dataclass(frozen=True, slots=True)
class _Adapter:
    """What the engine knows about one ATS's markup and wording."""

    name: str
    form_selectors: tuple[str, ...]
    submit_selectors: tuple[str, ...]
    label_selectors: tuple[str, ...]
    field_hints: tuple[tuple[str, str], ...]  # (regex over name|id, canonical field)
    success_phrases: tuple[str, ...]
    success_url_patterns: tuple[str, ...]
    apply_path: Callable[[str], str] | None = None

    def apply_url(self, url: str) -> str:
        return self.apply_path(url) if self.apply_path else url


_GENERIC_SUBMIT = (
    "button[type='submit']",
    "input[type='submit']",
    "button:not([type])",
)
_UUID = r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}"


def _lever_apply_path(url: str) -> str:
    parts = urlsplit(url)
    if re.fullmatch(rf"/[^/]+/{_UUID}/?", parts.path or ""):
        return url.split("?", 1)[0].rstrip("/") + "/apply"
    return url


def _ashby_apply_path(url: str) -> str:
    parts = urlsplit(url)
    if re.fullmatch(rf"/[^/]+/{_UUID}/?", parts.path or ""):
        return url.split("?", 1)[0].rstrip("/") + "/application"
    return url


_ADAPTERS: dict[str, _Adapter] = {
    "greenhouse": _Adapter(
        name="greenhouse",
        form_selectors=("#application_form", "form#application-form", "form"),
        submit_selectors=("#submit_app", *_GENERIC_SUBMIT),
        label_selectors=(),
        field_hints=(
            (r"job_application\[first_name\]|^first_name$", "first_name"),
            (r"job_application\[last_name\]|^last_name$", "last_name"),
            (r"job_application\[email\]|^email$", "email"),
            (r"job_application\[phone\]|^phone$", "phone"),
            (r"job_application\[location\]|^(candidate-)?location$", "city"),
            (r"job_application\[resume_text\]|^resume_text$", "resume"),
            (r"job_application\[resume\]|^resume$", "resume"),
            (
                r"job_application\[cover_letter_text\]|^cover_letter_text$",
                "cover_letter",
            ),
            (r"job_application\[cover_letter\]|^cover_letter$", "cover_letter"),
            (r"job_application\[gender\]|^gender$", "gender"),
            (r"job_application\[(hispanic_ethnicity|race)\]", "race_ethnicity"),
            (r"job_application\[veteran_status\]|^veteran_status$", "veteran_status"),
            (
                r"job_application\[disability_status\]|^disability_status$",
                "disability_status",
            ),
        ),
        success_phrases=("thank you for applying", "application has been received"),
        success_url_patterns=(r"/confirmation\b",),
    ),
    "lever": _Adapter(
        name="lever",
        form_selectors=("#application-form", "form.application-form", "form"),
        submit_selectors=(
            "#btn-submit",
            "button.template-btn-submit",
            *_GENERIC_SUBMIT,
        ),
        label_selectors=(".application-label",),
        field_hints=(
            (r"^name$", "full_name"),
            (r"^email$", "email"),
            (r"^phone$", "phone"),
            (r"^org$", "current_company"),
            (r"^location$", "city"),
            (r"(?i)urls\[linkedin\]", "linkedin_url"),
            (r"(?i)urls\[github\]", "github_url"),
            (r"(?i)urls\[portfolio\]", "portfolio_url"),
            (r"(?i)urls\[(other|twitter|website)\]", "website"),
            (r"^comments$", "cover_letter"),
            (r"^resume$", "resume"),
            (r"eeo\[gender\]", "gender"),
            (r"eeo\[race\]", "race_ethnicity"),
            (r"eeo\[veteran\]", "veteran_status"),
            (r"eeo\[disability\]", "disability_status"),
        ),
        success_phrases=("application submitted", "thank you for your application"),
        success_url_patterns=(r"/thanks\b", r"/thank-you\b"),
        apply_path=_lever_apply_path,
    ),
    "ashby": _Adapter(
        name="ashby",
        form_selectors=(
            "form.ashby-application-form",
            "form[class*='application-form']",
            "form",
        ),
        submit_selectors=(
            "button.ashby-application-form-submit-button",
            *_GENERIC_SUBMIT,
        ),
        label_selectors=(".ashby-application-form-question-title",),
        field_hints=(
            (r"^_systemfield_name$", "full_name"),
            (r"^_systemfield_email$", "email"),
            (r"^_systemfield_phone$", "phone"),
            (r"^_systemfield_location$", "city"),
            (r"^_systemfield_resume$", "resume"),
        ),
        success_phrases=("application has been submitted", "thank you for applying"),
        success_url_patterns=(r"application-submitted", r"[?&]submitted="),
        apply_path=_ashby_apply_path,
    ),
    "smartrecruiters": _Adapter(
        name="smartrecruiters",
        form_selectors=("form#st-apply-form", "form[class*='apply']", "form"),
        submit_selectors=_GENERIC_SUBMIT,
        label_selectors=(),
        field_hints=(
            (r"(?i)^firstname$", "first_name"),
            (r"(?i)^lastname$", "last_name"),
            (r"(?i)^email$", "email"),
            (r"(?i)^phone(number)?$", "phone"),
            (r"(?i)linkedin", "linkedin_url"),
            (r"(?i)^resume$", "resume"),
            (r"(?i)privacy|consent", "legal_attestation"),
        ),
        success_phrases=("thank you for applying", "received your application"),
        success_url_patterns=(r"/thank-?you\b", r"/completed\b"),
    ),
    "generic": _Adapter(
        name="generic",
        form_selectors=("form",),
        submit_selectors=_GENERIC_SUBMIT,
        label_selectors=(),
        field_hints=(),
        success_phrases=(),
        success_url_patterns=(),
    ),
}


@dataclass(frozen=True, slots=True)
class ConfirmationPhrases:
    """What an ATS says, in one language, once an application went through or when one already exists."""

    success: tuple[str, ...]
    already_applied: tuple[str, ...]


# The only signal for "submitted" and "already applied" is the page's wording (see the module docstring), so it
# is matched against this explicit table, one entry per language, lower case with straight apostrophes
# (:func:`_phrase_text` folds the page the same way). "Already applied" phrases are whole statements, never a
# bare "already applied?" question, because a match there skips the job before anything is filled.
CONFIRMATION_PHRASES: dict[str, ConfirmationPhrases] = {
    "en": ConfirmationPhrases(
        success=(
            "thank you for applying",
            "thanks for applying",
            "thank you for your application",
            "thank you for submitting",
            "application has been received",
            "application received",
            "application has been submitted",
            "application submitted",
            "application was submitted",
            "we have received your application",
            "we've received your application",
            "we received your application",
            "successfully submitted",
        ),
        already_applied=(
            "already applied",
            "already submitted an application",
            "you've already submitted",
            "you have already submitted",
            "duplicate application",
        ),
    ),
    "es": ConfirmationPhrases(
        success=(
            "gracias por postularte",
            "gracias por postular",
            "gracias por tu solicitud",
            "gracias por su solicitud",
            "gracias por tu candidatura",
            "gracias por su candidatura",
            "hemos recibido tu solicitud",
            "hemos recibido su solicitud",
            "hemos recibido tu candidatura",
            "hemos recibido su candidatura",
            "tu solicitud ha sido enviada",
            "su solicitud ha sido enviada",
            "solicitud enviada con éxito",
            "candidatura enviada con éxito",
        ),
        already_applied=(
            "ya te has postulado",
            "ya se ha postulado",
            "ya has enviado una solicitud",
            "ya ha enviado una solicitud",
            "ya has aplicado a esta",
            "solicitud duplicada",
        ),
    ),
    "fr": ConfirmationPhrases(
        success=(
            "merci pour votre candidature",
            "merci d'avoir postulé",
            "votre candidature a été envoyée",
            "votre candidature a bien été envoyée",
            "votre candidature a bien été reçue",
            "nous avons bien reçu votre candidature",
            "nous avons reçu votre candidature",
            "candidature envoyée avec succès",
        ),
        already_applied=(
            "vous avez déjà postulé",
            "vous avez déjà envoyé une candidature",
            "vous avez déjà soumis une candidature",
            "candidature en double",
        ),
    ),
    "de": ConfirmationPhrases(
        success=(
            "vielen dank für ihre bewerbung",
            "danke für ihre bewerbung",
            "vielen dank für deine bewerbung",
            "danke für deine bewerbung",
            "ihre bewerbung wurde erfolgreich übermittelt",
            "ihre bewerbung wurde übermittelt",
            "ihre bewerbung wurde erfolgreich versendet",
            "ihre bewerbung ist bei uns eingegangen",
            "wir haben ihre bewerbung erhalten",
            "deine bewerbung wurde übermittelt",
        ),
        already_applied=(
            "sie haben sich bereits beworben",
            "sie haben sich bereits auf diese stelle beworben",
            "du hast dich bereits beworben",
            "du hast dich bereits auf diese stelle beworben",
        ),
    ),
    "pt": ConfirmationPhrases(
        success=(
            "obrigado por se candidatar",
            "obrigada por se candidatar",
            "obrigado pela sua candidatura",
            "agradecemos a sua candidatura",
            "agradecemos sua candidatura",
            "a sua candidatura foi enviada",
            "sua candidatura foi enviada",
            "recebemos a sua candidatura",
            "recebemos sua candidatura",
            "candidatura enviada com sucesso",
        ),
        already_applied=(
            "você já se candidatou",
            "voce ja se candidatou",
            "já se candidatou a esta vaga",
            "você já enviou uma candidatura",
        ),
    ),
    "it": ConfirmationPhrases(
        success=(
            "grazie per la tua candidatura",
            "grazie per la sua candidatura",
            "grazie per esserti candidato",
            "grazie per esserti candidata",
            "abbiamo ricevuto la tua candidatura",
            "abbiamo ricevuto la sua candidatura",
            "la tua candidatura è stata inviata",
            "la sua candidatura è stata inviata",
            "candidatura inviata con successo",
        ),
        already_applied=(
            "ti sei già candidato",
            "ti sei già candidata",
            "si è già candidato",
            "hai già inviato una candidatura",
        ),
    ),
    "nl": ConfirmationPhrases(
        success=(
            "bedankt voor je sollicitatie",
            "bedankt voor uw sollicitatie",
            "dank voor je sollicitatie",
            "dank voor uw sollicitatie",
            "we hebben je sollicitatie ontvangen",
            "wij hebben je sollicitatie ontvangen",
            "wij hebben uw sollicitatie ontvangen",
            "je sollicitatie is verzonden",
            "uw sollicitatie is verzonden",
            "je sollicitatie is succesvol verstuurd",
        ),
        already_applied=(
            "je hebt al gesolliciteerd",
            "u heeft al gesolliciteerd",
            "u hebt al gesolliciteerd",
            "je hebt al op deze vacature gesolliciteerd",
        ),
    ),
}
# Every language's wording, as the engine matches it (an ATS's own phrases are added per adapter).
_SUCCESS_PHRASES = tuple(
    phrase for table in CONFIRMATION_PHRASES.values() for phrase in table.success
)
_ALREADY_APPLIED_PHRASES = tuple(
    phrase
    for table in CONFIRMATION_PHRASES.values()
    for phrase in table.already_applied
)
_APOSTROPHES = str.maketrans({"’": "'", "‘": "'", "ʼ": "'", "`": "'"})


def _phrase_text(text: str) -> str:
    """Page text folded the way :data:`CONFIRMATION_PHRASES` is written: lower case, straight apostrophes,
    single spaces (a non-breaking space between words still matches)."""
    return " ".join(str(text or "").translate(_APOSTROPHES).casefold().split())


def confirmation_language(text: str) -> tuple[str, str] | None:
    """``(language, "success" | "already_applied")`` for the first phrase found in ``text``, or None."""
    folded = _phrase_text(text)
    for language, table in CONFIRMATION_PHRASES.items():
        for phrase in table.already_applied:
            if phrase in folded:
                return language, "already_applied"
        for phrase in table.success:
            if phrase in folded:
                return language, "success"
    return None


_CAPTCHA_SELECTORS = (
    "iframe[src*='recaptcha']",
    "iframe[src*='hcaptcha']",
    "iframe[src*='turnstile']",
    "iframe[src*='challenges.cloudflare.com']",
    "iframe[title*='captcha' i]",
    ".g-recaptcha",
    ".h-captcha",
    ".cf-turnstile",
    "[data-sitekey]",
    "#captcha",
    "[id*='captcha' i]",
    "[class*='captcha' i]",
)
# Where a solved CAPTCHA leaves its answer (reCAPTCHA, hCaptcha, Turnstile): a value here means the person did it.
_CAPTCHA_ANSWER_SELECTORS = (
    "textarea[name='g-recaptcha-response']",
    "textarea[name='h-captcha-response']",
    "input[name='cf-turnstile-response']",
)
# A step that asks for a code the site sent to the person's email or phone. Monosage never reads those messages.
_VERIFICATION_SELECTORS = (
    "input[autocomplete='one-time-code']",
    "input[name='otp' i]",
    "input[id='otp' i]",
    "input[name^='otp_' i]",
    "input[name^='otp-' i]",
    "input[name*='verification' i][name*='code' i]",
    "input[id*='verification' i][id*='code' i]",
)
# What each hold asks of the person, and what the run says when Finish in browser ends without it.
_HANDOFF_ENDINGS = {
    "gave_up": "You stopped Finish in browser.",
    "timed_out": "Finish in browser timed out: the step was not done within {minutes} minutes, so the window closed.",
    "closed": "The browser window was closed before the step was done.",
}
_BASE_LABEL_SELECTORS = (
    "label",
    "legend",
    "[class*='label']",
    "[class*='question-title']",
    "[class*='question__title']",
    "[class*='field-label']",
)
_CONTAINER_SELECTORS = (
    "li",
    "fieldset",
    "[role='group']",
    "[role='radiogroup']",
    ".field",
    ".application-question",
    "[class*='field-entry']",
    "[class*='question']",
    "[class*='form-group']",
    "[class*='form-field']",
)
_RESUME_RE = re.compile(r"resume|\bcv\b|curriculum", re.IGNORECASE)
_COVER_RE = re.compile(r"cover", re.IGNORECASE)
_YES = frozenset({"yes", "y", "true", "1", "agree", "accept", "checked"})
_NO = frozenset({"no", "n", "false", "0"})
# The label before a confirmation number, in the languages of CONFIRMATION_PHRASES (English first).
_CONFIRMATION_LABELS = (
    "reference|confirmation|application|tracking|ticket|case",
    "referencia|confirmación|confirmacion|solicitud",  # es
    "référence|reference|confirmation|candidature",  # fr
    "referenznummer|referenz|bewerbungsnummer|vorgangsnummer|bestätigungsnummer",  # de
    "referência|referencia|confirmação|confirmacao|protocolo",  # pt
    "riferimento|conferma|candidatura",  # it
    "referentienummer|referentie|kenmerk|bevestigingsnummer",  # nl
)
_CONFIRMATION_RE = re.compile(
    rf"(?:{'|'.join(_CONFIRMATION_LABELS)})\s*"
    r"(?:number|no\.?|id|code|#|número|numero|numéro|nummer|n\.?º|nº|n°)?\s*(?:is|:|#)?\s*#?\s*"
    r"([A-Za-z0-9][A-Za-z0-9-]{3,})",
    re.IGNORECASE,
)

# Runs inside the page: tags every control with data-aira-ref and describes it.
_DISCOVER_JS = r"""
(form, config) => {
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();
  const strip = (t) => clean(t)
    .replace(/[\s*✱]+$/g, "")
    .replace(/\s*\((?:required|optional)\)\s*$/i, "")
    .replace(/[\s*✱:]+$/g, "")
    .trim();
  const textOf = (node) => clean(node.innerText !== undefined ? node.innerText : node.textContent);
  const labelSelector = config.labelSelectors.join(", ");
  const containerSelector = config.containerSelectors.join(", ");
  const captchaSelector = config.captchaSelectors.join(", ");
  const skipTypes = new Set(["hidden", "submit", "button", "reset", "image", "password"]);
  const idText = (ids) => clean(String(ids).split(/\s+/).map((id) => {
    const node = document.getElementById(id);
    return node ? textOf(node) : "";
  }).join(" "));
  const isVisible = (el) => {
    if (el.type === "file") return true;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") return false;
    const rect = el.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const ownLabel = (el) => {
    const aria = el.getAttribute("aria-label");
    if (aria && clean(aria)) return clean(aria);
    const by = el.getAttribute("aria-labelledby");
    if (by) { const text = idText(by); if (text) return text; }
    if (el.labels && el.labels.length) {
      const text = clean(Array.from(el.labels).map(textOf).join(" "));
      if (text) return text;
    }
    return "";
  };
  const groupLabel = (inputs) => {
    const optionLabels = new Set();
    inputs.forEach((input) => { if (input.labels) Array.from(input.labels).forEach((l) => optionLabels.add(l)); });
    let node = inputs[0].parentElement;
    let steps = 0;
    while (node && node !== form.parentElement && steps < 8) {
      if (inputs.every((input) => node.contains(input))) {
        const by = node.getAttribute("aria-labelledby");
        if (by) { const text = idText(by); if (text) return text; }
        const aria = node.getAttribute("aria-label");
        if (aria && clean(aria)) return clean(aria);
        for (const candidate of node.querySelectorAll(labelSelector)) {
          if (optionLabels.has(candidate)) continue;
          if (candidate.tagName === "LABEL" && candidate.control && !inputs.includes(candidate.control)) continue;
          if (candidate.tagName === "LABEL" && candidate.querySelector("input, select, textarea")) continue;
          if (candidate.closest(captchaSelector)) continue;
          const text = textOf(candidate);
          if (text) return text;
        }
        if (node.matches(containerSelector) && node !== inputs[0].parentElement) {
          // A recognised question container without a label: stop climbing.
          const heading = node.querySelector("h1, h2, h3, h4, h5, h6, p, span, div");
          if (heading && !heading.querySelector("input, select, textarea")) {
            const text = textOf(heading);
            if (text) return text;
          }
        }
      }
      node = node.parentElement;
      steps += 1;
    }
    return "";
  };
  const kindOf = (el) => {
    const tag = el.tagName.toLowerCase();
    if (tag === "select") return "select";
    if (tag === "textarea") return "textarea";
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (["text", "search"].includes(type)) return "text";
    if (["email", "tel", "url", "number", "date", "radio", "checkbox", "file"].includes(type)) return type;
    return "unknown";
  };
  const selectOptions = (el) => Array.from(el.options)
    .filter((option) => !option.disabled && option.value !== "")
    .map((option) => ({ label: clean(option.textContent), value: option.value, ref: "" }));
  const controls = Array.from(form.querySelectorAll("input, select, textarea")).filter((el) => {
    const type = (el.getAttribute("type") || "text").toLowerCase();
    if (el.tagName === "INPUT" && skipTypes.has(type)) return false;
    if (captchaSelector && el.closest(captchaSelector)) return false;
    return true;
  });
  let counter = 0;
  controls.forEach((el) => { counter += 1; el.setAttribute("data-aira-ref", String(counter)); });
  const seenGroups = new Set();
  const questions = [];
  for (const el of controls) {
    const kind = kindOf(el);
    const name = el.getAttribute("name") || "";
    let members = [el];
    if ((kind === "radio" || kind === "checkbox") && name) {
      const sameName = controls.filter((other) => other.getAttribute("name") === name && kindOf(other) === kind);
      if (kind === "radio" || sameName.length > 1) {
        if (seenGroups.has(kind + ":" + name)) continue;
        seenGroups.add(kind + ":" + name);
        members = sameName;
      }
    }
    const grouped = members.length > 1 || kind === "radio";
    let rawLabel = grouped ? groupLabel(members) : ownLabel(el);
    if (!rawLabel && !grouped) rawLabel = groupLabel([el]);
    if (!rawLabel) rawLabel = clean(el.getAttribute("placeholder") || "");
    let options = [];
    if (kind === "select") options = selectOptions(el);
    if (grouped) options = members.map((input) => ({
      label: strip(ownLabel(input)) || clean(input.value),
      value: input.value,
      ref: input.getAttribute("data-aira-ref"),
    }));
    const required = members.some((input) => input.required || input.getAttribute("aria-required") === "true")
      || /[*✱]\s*$/.test(rawLabel);
    questions.push({
      ref: el.getAttribute("data-aira-ref"),
      kind: grouped && kind === "checkbox" ? "checkbox" : kind,
      grouped,
      id: el.id || "",
      name,
      label: strip(rawLabel),
      required,
      options,
      visible: members.some(isVisible),
      disabled: members.every((input) => input.disabled),
      placeholder: clean(el.getAttribute("placeholder") || ""),
      accept: el.getAttribute("accept") || "",
      multiple: Boolean(el.multiple),
    });
  }
  return questions;
}
"""
_FORMS_JS = r"""
() => Array.from(document.querySelectorAll("form")).map((form, index) => {
  form.setAttribute("data-aira-form", String(index));
  return {
    index,
    controls: form.querySelectorAll(
      "input:not([type=hidden]):not([type=submit]):not([type=button]):not([type=reset]), select, textarea"
    ).length,
  };
})
"""
_CAPTCHA_JS = r"""
(selector) => {
  for (const el of document.querySelectorAll(selector)) {
    if (el.classList.contains("grecaptcha-badge")) continue;
    const style = getComputedStyle(el);
    if (style.display === "none" || style.visibility === "hidden") continue;
    const rect = el.getBoundingClientRect();
    if (rect.width < 40 || rect.height < 40) continue;
    return String(el.getAttribute("title") || el.className || el.id || el.tagName).slice(0, 80);
  }
  return "";
}
"""
_ANSWERED_JS = r"""
(selector) => Array.from(document.querySelectorAll(selector)).some((el) => String(el.value || "").trim().length > 0)
"""


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


@dataclass(slots=True)
class _Option:
    label: str
    value: str
    ref: str


@dataclass(slots=True)
class _Control:
    """One question on the live form (a control or a radio/checkbox group)."""

    ref: str
    kind: str
    grouped: bool
    id: str
    name: str
    label: str
    required: bool
    options: list[_Option]
    visible: bool
    disabled: bool
    placeholder: str
    accept: str
    multiple: bool
    canonical: str | None = None
    file_role: str | None = None

    @property
    def provider_field_id(self) -> str:
        return self.name or self.id

    @property
    def display_label(self) -> str:
        return self.label or self.placeholder or self.provider_field_id or self.kind


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", str(text or "")).strip("-")[:40] or "job"


def _first_line(text: str) -> str:
    return (
        str(text or "").strip().splitlines()[0][:300] if str(text or "").strip() else ""
    )


def _contains_phrase(haystack: str, needle: str) -> bool:
    return f" {needle} " in f" {haystack} "


def _moved_on(before: str, after: str) -> bool:
    """Whether the page left ``before`` for another address (the query and fragment do not count)."""
    if not before or not after:
        return False
    old, new = urlsplit(before), urlsplit(after)
    return (old.scheme, old.netloc, old.path.rstrip("/")) != (
        new.scheme,
        new.netloc,
        new.path.rstrip("/"),
    )


def extract_confirmation_number(text: str) -> str:
    """The first reference-like token near "confirmation"/"reference"/"application" wording."""
    for match in _CONFIRMATION_RE.finditer(str(text or "")):
        token = match.group(1).strip("-")
        if any(char.isdigit() for char in token) and len(token) >= 4:
            return token
    return ""


def _iso_date(value: str) -> str | None:
    text = value.strip()
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if match:
        month, day, year = match.groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"
    return None


def _numeric(value: str) -> str | None:
    text = value.replace(",", "").strip()
    if re.fullmatch(r"[-+]?\d+(\.\d+)?", text):
        return text
    match = re.search(r"\d+(\.\d+)?", text)
    return match.group(0) if match else None


def _match_option(value: str, options: Sequence[_Option]) -> _Option | None:
    """Pick the option the value names; ``None`` rather than a guess."""
    wanted = normalize_label(value)
    if not wanted:
        return None
    for option in options:
        if wanted in (normalize_label(option.label), normalize_label(option.value)):
            return option
    if wanted in _YES or wanted in _NO:
        target = _YES if wanted in _YES else _NO
        for option in options:
            if (
                normalize_label(option.label) in target
                or normalize_label(option.value) in target
            ):
                return option
    partial = [
        option
        for option in options
        if _contains_phrase(normalize_label(option.label), wanted)
        or _contains_phrase(wanted, normalize_label(option.label))
    ]
    return partial[0] if len(partial) == 1 else None


def _split_values(value: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[;,]", value)]
    return [part for part in parts if part] or [value.strip()]


def _file_role(control: _Control) -> str | None:
    haystack = " ".join((control.name, control.id, control.label, control.placeholder))
    if _COVER_RE.search(haystack):
        return "cover_letter"
    if _RESUME_RE.search(haystack):
        return "resume"
    if control.canonical in ("resume", "cover_letter"):
        return control.canonical
    return None


def _mapping_for(control: _Control, canonical: str) -> FieldMapping:
    definition = CANONICAL_FIELDS[canonical]
    return FieldMapping(
        detected_label=control.display_label,
        provider_field_id=control.provider_field_id,
        canonical_field=canonical,
        field_type=definition.field_type,
        confidence=1.0,
        sensitivity=definition.sensitivity,
        requires_confirmation=definition.requires_confirmation,
        validation_rule=definition.validation,
        match_kind="exact",
    )


_MAPPING_TYPES = {
    "text": "text",
    "textarea": "textarea",
    "select": "select",
    "file": "file",
    "date": "date",
    "number": "number",
    "email": "email",
    "tel": "phone",
    "url": "url",
}


# --------------------------------------------------------------------------- #
# The run
# --------------------------------------------------------------------------- #


class _Runner:
    """One application attempt: owns the page, the steps log and the outcome."""

    def __init__(self, request: ApplyRequest, adapter: _Adapter) -> None:
        self.request = request
        self.adapter = adapter
        self.steps: list[str] = []
        self.questions: list[FormQuestion] = []
        self.page: Page | None = None
        self.frame: Frame | None = None
        self.form: Locator | None = None
        self.controls: list[_Control] = []
        self.deadline = time.monotonic() + max(5, int(request.timeout_seconds))
        self.document_status: int | None = None
        self.cleanups: list[Callable[[], None]] = []
        self.url = adapter.apply_url(request.application_url)
        self.handoffs: list[dict[str, Any]] = []

    # -- outcome plumbing --------------------------------------------------

    def step(self, text: str) -> None:
        self.steps.append(text)

    def unanswered(self) -> list[FormQuestion]:
        return [
            question
            for question in self.questions
            if question.required and question.status in UNANSWERED_STATUSES
        ]

    def page_text(self) -> str:
        if self.page is None:
            return ""
        try:
            return self.page.inner_text("body", timeout=2_000)[:MAX_PAGE_TEXT]
        except Exception:  # noqa: BLE001 - mid-navigation reads legitimately fail
            return ""

    def screenshot(self, label: str) -> str | None:
        directory = self.request.screenshot_dir
        if not directory or self.page is None:
            return None
        try:
            folder = Path(directory)
            folder.mkdir(parents=True, exist_ok=True)
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
            path = folder / f"{_slug(self.request.job_id)}-{stamp}-{label}.png"
            self.page.screenshot(path=str(path), full_page=True)
            self.step("Took a screenshot of the page")
            return str(path)
        except Exception as error:  # noqa: BLE001 - evidence is best effort
            self.step(f"Could not take a screenshot: {_first_line(str(error))}")
            return None

    def evidence(
        self,
        *,
        screenshot: str | None,
        confirmation_text: str = "",
        confirmation_number: str = "",
        submitted_at: str | None = None,
    ) -> ApplyEvidence:
        url = self.url
        if self.page is not None:
            try:
                url = self.page.url
            except Exception:  # noqa: BLE001
                pass
        return ApplyEvidence(
            screenshot_path=screenshot,
            page_text=self.page_text(),
            confirmation_text=confirmation_text,
            confirmation_number=confirmation_number,
            url=url,
            submitted_at=submitted_at,
        )

    def outcome(
        self, status: str, error: str = "", **evidence_kwargs: Any
    ) -> ApplyOutcome:
        screenshot = evidence_kwargs.pop("screenshot", None)
        hold = str(evidence_kwargs.pop("hold", "") or "")
        return ApplyOutcome(
            status=status,
            ats=self.adapter.name,
            questions=list(self.questions),
            unanswered=self.unanswered(),
            evidence=self.evidence(screenshot=screenshot, **evidence_kwargs),
            error=error,
            steps=list(self.steps),
            hold=hold if status == "needs_review" else "",
            handoffs=list(self.handoffs),
        )

    # -- Finish in browser (aira.handoff) ------------------------------------

    def _step_done_check(self, kind: str) -> Callable[[], bool]:
        """Whether the page shows the person did the step: it is gone, answered, or the page moved on."""
        assert self.page is not None
        page = self.page
        try:
            start_url = page.url
        except Exception:  # noqa: BLE001
            start_url = ""

        def check() -> bool:
            from aira.handoff import HandoffClosed

            if page.is_closed():
                raise HandoffClosed
            try:
                url = page.url
            except Exception:  # noqa: BLE001 - mid-navigation
                return False
            if _moved_on(start_url, url):
                return True
            if self._confirmation_marker():
                return True
            return not self._step_present(kind)

        return check

    def _step_present(self, kind: str) -> bool:
        """Whether the page still shows the step a person must do."""
        if kind == "captcha":
            return bool(self._visible_captcha()) and not self._captcha_answered()
        if kind == "sign_in":
            return self._password_wall()
        if kind == "verification":
            return self._verification_step()
        return True

    def _handed_over(self, held: ApplyOutcome) -> bool:
        """Finish in browser: wait for the person on the step ``held`` is for.

        True when the step is done (the page shows it, or the person said so): the run carries on from here. False
        when there is no session or the person gave up, the time ran out or the window was closed; ``held`` then
        says so and is the result.
        """
        handoff = self.request.handoff
        if handoff is None or not held.hold or self.page is None:
            return False
        if len(self.handoffs) >= MAX_HANDOFFS:
            return False
        page = self.page
        self.step("Waiting for you to finish this step in the browser window")
        result = handoff.wait(
            held.hold,
            held.error,
            self._step_done_check(held.hold),
            lambda milliseconds: page.wait_for_timeout(milliseconds),
        )
        seconds = int((handoff.waits[-1] if getattr(handoff, "waits", None) else {}).get("seconds", 0))
        self.handoffs.append({"kind": held.hold, "outcome": result, "seconds": seconds})
        if result in ("cleared", "done"):
            self.step(
                "You finished the step; Monosage carries on"
                if result == "cleared"
                else "You said the step is done; Monosage carries on"
            )
            # The time spent waiting for the person does not count against the run's own time limit.
            self.deadline = time.monotonic() + max(5, int(self.request.timeout_seconds))
            try:
                page.wait_for_load_state("domcontentloaded", timeout=SETTLE_SECONDS * 1000)
            except Exception:  # noqa: BLE001
                pass
            return True
        minutes = max(1, round(float(getattr(handoff, "timeout_seconds", 900)) / 60))
        ending = _HANDOFF_ENDINGS.get(result, "").format(minutes=minutes)
        self.step(ending or "Finish in browser ended")
        held.error = f"{ending} {held.error}".strip()
        held.steps = list(self.steps)
        held.handoffs = list(self.handoffs)
        return False

    def _after_person(self, form_url: str) -> ApplyOutcome | None:
        """After the person's step on the form: None to carry on, or what they did themselves.

        The person may have pressed Submit in the window. Then the confirmation is the result; when the page moved
        on without one, nothing is pressed again (it may have been sent).
        """
        page = self.page
        assert page is not None
        marker = self._confirmation_marker()
        if marker:
            self.step("You submitted the form in the browser window")
            return self._submitted_outcome(marker)
        try:
            url = page.url
        except Exception:  # noqa: BLE001
            url = ""
        try:
            form_gone = self.form is None or self.form.count() == 0
        except Exception:  # noqa: BLE001 - the document was replaced
            form_gone = True
        if not _moved_on(form_url, url) and not form_gone:
            return None
        limit = time.monotonic() + SETTLE_SECONDS * 2
        while time.monotonic() < limit:
            page.wait_for_timeout(POLL_MILLISECONDS)
            marker = self._confirmation_marker()
            if marker:
                self.step("You submitted the form in the browser window")
                return self._submitted_outcome(marker)
        self.step(
            "No confirmation appeared after the page moved on in the browser window"
        )
        return self.outcome(
            "failed",
            "The page moved on while you finished the step, and Monosage found no confirmation. "
            "Check the site, then choose I applied or Try again.",
            screenshot=self.screenshot("moved-on"),
        )

    def _captcha_answered(self) -> bool:
        selector = ", ".join(_CAPTCHA_ANSWER_SELECTORS)
        for frame in self._frames():
            try:
                if frame.evaluate(_ANSWERED_JS, selector):
                    return True
            except Exception:  # noqa: BLE001
                continue
        return False

    def _verification_step(self) -> bool:
        selector = ", ".join(f"{item}:visible" for item in _VERIFICATION_SELECTORS)
        for frame in self._frames():
            try:
                if frame.locator(selector).count():
                    return True
            except Exception:  # noqa: BLE001 - detached frames
                continue
        return False

    def _confirmation_marker(self) -> str:
        """The success wording or URL the ATS shows once an application went through, or ""."""
        if self.page is None:
            return ""
        try:
            url = self.page.url
        except Exception:  # noqa: BLE001
            url = ""
        lowered = _phrase_text(self.page_text())
        success_phrases = _SUCCESS_PHRASES + self.adapter.success_phrases
        marker = next((p for p in success_phrases if p in lowered), "")
        if marker:
            return marker
        return next(
            (
                f"URL {pattern}"
                for pattern in self.adapter.success_url_patterns
                if re.search(pattern, url, re.IGNORECASE)
            ),
            "",
        )

    def _submitted_outcome(self, marker: str) -> ApplyOutcome:
        text = self.page_text()
        submitted_at = _now_iso()
        number = extract_confirmation_number(text)
        self.step(f"Confirmation appeared ({marker})")
        if number:
            self.step(f"Confirmation number {number}")
        screenshot = self.screenshot("submitted")
        return self.outcome(
            "submitted",
            "",
            screenshot=screenshot,
            confirmation_text=" ".join(text.split())[:2_000],
            confirmation_number=number,
            submitted_at=submitted_at,
        )

    def _keep_state(self, context: Any, result: ApplyOutcome) -> None:
        """Keep this run's cookies and storage for Finish in browser when it is held for a person's step."""
        path = self.request.keep_state_path
        if not path or not result.hold:
            return
        try:
            target = Path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            context.storage_state(path=str(target))
            try:
                target.chmod(0o600)
            except OSError:
                pass
        except Exception as error:  # noqa: BLE001 - Finish in browser still works from a fresh browser
            self.step(f"Could not keep the browser state: {_first_line(str(error))}")

    # -- browser session ---------------------------------------------------

    def run_in(self, browser: Browser) -> ApplyOutcome:
        from patchright.sync_api import Error as PlaywrightError
        from patchright.sync_api import TimeoutError as PlaywrightTimeout

        options: dict[str, Any] = {
            "accept_downloads": False,
            "viewport": {"width": 1280, "height": 1800},
        }
        state = self.request.storage_state_path
        if state and Path(state).is_file():
            options["storage_state"] = state  # the held run's cookies and storage (Finish in browser)
        if self.request.handoff is not None:
            options["viewport"] = {"width": 1280, "height": 900}  # a window the person can see whole
        context = browser.new_context(**options)
        page = context.new_page()
        # Never work in a second tab: anything that opens one is closed at once.
        # Registered after the main page exists, because "page" also fires for it.
        context.on("page", lambda popup: popup.close() if popup != page else None)
        page.set_default_timeout(
            min(ACTION_TIMEOUT_SECONDS, max(2, int(self.request.timeout_seconds)))
            * 1000
        )
        page.on("response", self._track_response)
        self.page = page
        try:
            result = self._drive()
            self._keep_state(context, result)
            return result
        except PlaywrightTimeout as error:
            self.step("The browser timed out")
            return self.outcome(
                "failed",
                f"The form did not respond in time: {_first_line(str(error))}",
                screenshot=self.screenshot("timeout"),
            )
        except PlaywrightError as error:
            if (
                self.request.handoff is not None
                and page.is_closed()
                and not any(step.startswith("Clicked ") for step in self.steps)
            ):
                # The person closed the Finish in browser window: the run waits for them again.
                self.step("The browser window was closed")
                return self.outcome(
                    "needs_review",
                    _HANDOFF_ENDINGS["closed"],
                    hold=self.handoffs[-1]["kind"] if self.handoffs else "",
                )
            self.step("The browser reported an error")
            return self.outcome(
                "failed",
                f"Browser error: {_first_line(str(error))}",
                screenshot=self.screenshot("error"),
            )
        finally:
            for cleanup in self.cleanups:
                cleanup()
            try:
                context.close()
            except Exception:  # noqa: BLE001 - the browser may already be gone
                pass

    def _track_response(self, response: Any) -> None:
        try:
            if (
                response.request.resource_type == "document"
                and self.page is not None
                and response.frame == self.page.main_frame
            ):
                self.document_status = int(response.status)
        except Exception:  # noqa: BLE001 - a detached frame is not an error here
            pass

    def _drive(self) -> ApplyOutcome:
        page = self.page
        assert page is not None
        host = urlsplit(self.url).hostname or self.url
        response = page.goto(self.url, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            self.step(f"The page answered HTTP {response.status}")
            if response.status in _BLOCKED_STATUSES:
                # The site refuses automated visitors (or wants a sign-in first). Aira does not work around
                # that: say so plainly and leave the application to the person.
                return self.outcome(
                    "unsupported",
                    f"{host} refused the automated visit (HTTP {response.status}). "
                    "Sites like this block automated applications; open the posting and apply yourself.",
                    screenshot=self.screenshot("blocked"),
                )
            return self.outcome(
                "failed",
                f"The application page answered HTTP {response.status}.",
                screenshot=self.screenshot("error"),
            )
        try:
            page.wait_for_load_state("networkidle", timeout=SETTLE_SECONDS * 1000)
        except Exception:  # noqa: BLE001 - a chatty page is fine; carry on
            pass
        self.step(f"Opened the {self.adapter.name} form at {host}")

        text = _phrase_text(self.page_text())
        if any(phrase in text for phrase in _ALREADY_APPLIED_PHRASES):
            self.step("The page says this job was already applied to")
            return self.outcome(
                "already_applied",
                "The site reports an application already exists for this job.",
                screenshot=self.screenshot("already-applied"),
            )
        if self._password_wall():
            self.step("The page asks for a sign-in")
            held = self.outcome(
                "needs_review",
                "The page asks for a sign-in. Monosage never signs in to employer sites; "
                "sign in yourself (Finish in browser), or apply on the site.",
                screenshot=self.screenshot("sign-in"),
                hold="sign_in",
            )
            if not self._handed_over(held):
                return held
            if self._password_wall():
                self.step("The page still asks for a sign-in")
                return self.outcome(
                    "needs_review",
                    "The page still asks for a sign-in. Sign in on the site, then try again.",
                    screenshot=self.screenshot("sign-in"),
                    hold="sign_in",
                )
            marker = self._confirmation_marker()
            if marker:
                return self._submitted_outcome(marker)
        if self._verification_step():
            self.step("The page asks for a code sent to your email or phone")
            held = self.outcome(
                "needs_review",
                "The site asks for a code it sent to your email or phone. Monosage never reads your "
                "messages; enter the code yourself (Finish in browser), or apply on the site.",
                screenshot=self.screenshot("verification"),
                hold="verification",
            )
            if not self._handed_over(held):
                return held
            if self._verification_step():
                return self.outcome(
                    "needs_review",
                    "The page still asks for the code. Enter it on the site, then try again.",
                    screenshot=self.screenshot("verification"),
                    hold="verification",
                )
        if not self._locate_form():
            self.step("No application form was found on the page")
            return self.outcome(
                "unsupported",
                "No application form was found on this page. The site may need an "
                "Apply click, a sign-in or a different URL.",
                screenshot=self.screenshot("no-form"),
            )

        self._discover()
        self._fill_all()
        self.step(
            f"Filled {sum(1 for q in self.questions if q.status == 'filled')} of "
            f"{len(self.questions)} questions"
        )

        captcha = self._visible_captcha()
        if captcha:
            self.step(f"A verification step is on the form ({captcha})")
            held = self.outcome(
                "needs_review",
                "A CAPTCHA or verification step is on the form. Monosage never bypasses "
                "these; complete it yourself (Finish in browser) and Monosage submits.",
                screenshot=self.screenshot("captcha"),
                hold="captcha",
            )
            # Questions nobody answered come first: the person would otherwise do the CAPTCHA for nothing.
            if self.request.handoff is None or self.unanswered():
                return held
            form_url = page.url
            if not self._handed_over(held):
                return held
            moved = self._after_person(form_url)
            if moved is not None:
                return moved
        unanswered = self.unanswered()
        if unanswered:
            labels = ", ".join(question.label for question in unanswered[:6])
            self.step(
                f"{len(unanswered)} required question(s) have no answer: {labels}"
            )
            return self.outcome(
                "needs_review",
                f"{len(unanswered)} required question(s) need an answer: {labels}.",
                screenshot=self.screenshot("review"),
            )
        if not self.request.submit:
            self.step("Stopped before submitting, as asked")
            return self.outcome("filled", "", screenshot=self.screenshot("filled"))
        return self._submit()

    # -- page inspection ---------------------------------------------------

    def _frames(self) -> list[Frame]:
        assert self.page is not None
        frames = [self.page.main_frame]
        frames.extend(f for f in self.page.frames if f != self.page.main_frame)
        return frames

    def _password_wall(self) -> bool:
        for frame in self._frames():
            try:
                if frame.locator("input[type='password']:visible").count():
                    return True
            except Exception:  # noqa: BLE001 - detached frames
                continue
        return False

    def _locate_form(self) -> bool:
        """Pick the application form: the adapter's selector, else the busiest ``<form>``."""
        for frame in self._frames():
            try:
                forms = frame.evaluate(_FORMS_JS)
            except Exception:  # noqa: BLE001 - cross-origin or detached frame
                continue
            for selector in self.adapter.form_selectors:
                if selector == "form":
                    continue
                locator = frame.locator(selector)
                if locator.count():
                    self.frame, self.form = frame, locator.first
                    return True
            candidates = [form for form in forms if form["controls"] >= 2]
            if candidates:
                best = max(candidates, key=lambda form: form["controls"])
                self.frame = frame
                self.form = frame.locator(f"form[data-aira-form='{best['index']}']")
                return True
        # Some React forms have no <form>: use the body when it carries real controls.
        for frame in self._frames():
            try:
                count = frame.locator(
                    "input:not([type=hidden]):not([type=submit]), select, textarea"
                ).count()
            except Exception:  # noqa: BLE001
                continue
            if count >= 3:
                self.frame, self.form = frame, frame.locator("body")
                return True
        return False

    def _visible_captcha(self) -> str:
        selector = ", ".join(_CAPTCHA_SELECTORS)
        for frame in self._frames():
            try:
                found = frame.evaluate(_CAPTCHA_JS, selector)
            except Exception:  # noqa: BLE001
                continue
            if found:
                return str(found)
        return ""

    def _discover(self) -> None:
        assert self.form is not None
        raw = self.form.evaluate(
            _DISCOVER_JS,
            {
                "labelSelectors": list(
                    _BASE_LABEL_SELECTORS + self.adapter.label_selectors
                ),
                "containerSelectors": list(_CONTAINER_SELECTORS),
                "captchaSelectors": list(_CAPTCHA_SELECTORS),
            },
        )
        controls: list[_Control] = []
        for item in raw:
            control = _Control(
                ref=str(item.get("ref") or ""),
                kind=str(item.get("kind") or "unknown"),
                grouped=bool(item.get("grouped")),
                id=str(item.get("id") or ""),
                name=str(item.get("name") or ""),
                label=str(item.get("label") or ""),
                required=bool(item.get("required")),
                options=[
                    _Option(
                        label=str(option.get("label") or ""),
                        value=str(option.get("value") or ""),
                        ref=str(option.get("ref") or ""),
                    )
                    for option in item.get("options") or []
                ],
                visible=bool(item.get("visible")),
                disabled=bool(item.get("disabled")),
                placeholder=str(item.get("placeholder") or ""),
                accept=str(item.get("accept") or ""),
                multiple=bool(item.get("multiple")),
            )
            control.canonical = self._canonical_for(control)
            if control.kind == "file":
                control.file_role = _file_role(control)
            controls.append(control)
        self.controls = controls
        self.step(f"Found {len(controls)} questions on the form")

    def _canonical_for(self, control: _Control) -> str | None:
        for pattern, canonical in self.adapter.field_hints:
            if any(
                re.search(pattern, key) for key in (control.name, control.id) if key
            ):
                return canonical
        packet = self._packet_field(control)
        if packet is not None and packet.get("canonical_field") in CANONICAL_FIELDS:
            return str(packet["canonical_field"])
        if not control.label:
            return None
        mapping = map_field(
            control.label,
            provider_field_id=control.provider_field_id,
            field_type=_MAPPING_TYPES.get(control.kind),
            options=[option.label for option in control.options],
        )
        return mapping.canonical_field

    def _packet_field(self, control: _Control) -> dict[str, Any] | None:
        label_norm = normalize_label(control.label)
        for item in self.request.fields:
            if not isinstance(item, Mapping):
                continue
            provider_id = str(item.get("provider_field_id") or "")
            if provider_id and provider_id in (control.name, control.id):
                return dict(item)
            if (
                label_norm
                and normalize_label(str(item.get("label") or "")) == label_norm
            ):
                return dict(item)
        return None

    def _extra_answer(self, control: _Control) -> str | None:
        label_norm = normalize_label(control.label)
        for key, value in self.request.extra_answers.items():
            text = str(value if value is not None else "").strip()
            if not text:
                continue
            if key in (control.name, control.id) or (
                label_norm and normalize_label(str(key)) == label_norm
            ):
                return text
        return None

    # -- value resolution --------------------------------------------------

    def _resolve(self, control: _Control) -> tuple[str | None, str, str]:
        """Return ``(value, status, message)`` for a non-file control without touching the page."""
        packet = self._packet_field(control)
        if packet is not None and packet.get("field_type") != "file":
            value = str(packet.get("value") or "").strip()
            if value and packet.get("status") == "prefilled":
                return value, "filled", "From the approved application packet."
        extra = self._extra_answer(control)
        if extra:
            return extra, "filled", "Answered by you for this run."

        canonical = control.canonical
        if canonical == "cover_letter" and control.kind in ("textarea", "text"):
            if self._upload_planned("cover_letter"):
                return None, "skipped", "Cover letter attached as a file instead."
            if self.request.cover_letter_text.strip():
                return (
                    self.request.cover_letter_text.strip(),
                    "filled",
                    "Cover letter text from the approved packet.",
                )
        if canonical == "resume" and control.kind in ("textarea", "text"):
            if self._upload_planned("resume"):
                return None, "skipped", "Resume attached as a file instead."
            return (
                None,
                "missing",
                "The form wants resume text, but only a file is available.",
            )

        if canonical is None:
            return (
                None,
                "unknown",
                "Monosage could not match this question to anything it knows; answer it in review.",
            )
        mapping = _mapping_for(control, canonical)
        prefilled = prefill_fields(
            [mapping], self.request.profile, self.request.standard_answers
        )[0]
        if prefilled.status == "prefilled" and prefilled.value:
            return prefilled.value, "filled", prefilled.message
        status = {
            "needs_confirmation": "needs_confirmation",
            "missing": "missing",
            "invalid": "missing",
            "unknown": "unknown",
        }.get(prefilled.status, "missing")
        return None, status, prefilled.message

    def _upload_planned(self, role: str) -> bool:
        if not any(c.kind == "file" and c.file_role == role for c in self.controls):
            return False
        path = (
            self.request.resume_path
            if role == "resume"
            else self.request.cover_letter_path
        )
        return bool(path and Path(path).is_file())

    # -- filling -----------------------------------------------------------

    def _fill_all(self) -> None:
        for control in self.controls:
            if control.kind == "file":
                question = self._upload(control)
            else:
                question = self._fill(control)
            self.questions.append(question)

    def _question(
        self, control: _Control, value: str | None, status: str, message: str
    ) -> FormQuestion:
        return FormQuestion(
            label=control.display_label,
            kind=control.kind,
            required=control.required,
            options=[option.label for option in control.options],
            provider_field_id=control.provider_field_id,
            canonical_field=control.canonical,
            value=value,
            status=status,
            message=message,
        )

    def _locator(self, ref: str) -> Locator:
        assert self.form is not None
        return self.form.locator(f"[data-aira-ref='{ref}']")

    def _fill(self, control: _Control) -> FormQuestion:
        label = control.display_label
        if control.kind == "unknown":
            return self._question(
                control,
                None,
                "unsupported",
                "Monosage cannot operate this kind of control.",
            )
        if control.disabled:
            return self._question(control, None, "skipped", "The control is disabled.")
        if not control.visible:
            status = "unsupported" if control.required else "skipped"
            return self._question(control, None, status, "The control is not visible.")

        value, status, message = self._resolve(control)
        if status != "filled" or value is None:
            if control.required and status in UNANSWERED_STATUSES:
                self.step(f"Left '{label}' empty: {message}")
            return self._question(control, None, status, message)

        try:
            if control.kind == "select":
                return self._fill_select(control, value)
            if control.kind == "radio":
                return self._fill_radio(control, value)
            if control.kind == "checkbox":
                return self._fill_checkbox(control, value)
            return self._fill_text(control, value)
        except Exception as error:  # noqa: BLE001 - one bad control must not end the run
            self.step(f"Could not fill '{label}': {_first_line(str(error))}")
            return self._question(
                control,
                None,
                "unsupported",
                f"Filling failed: {_first_line(str(error))}",
            )

    def _fill_text(self, control: _Control, value: str) -> FormQuestion:
        text = value
        if control.kind == "date":
            iso = _iso_date(value)
            if iso is None:
                return self._question(
                    control,
                    None,
                    "missing",
                    f"The form wants a calendar date; '{value}' is not one.",
                )
            text = iso
        elif control.kind == "number":
            number = _numeric(value)
            if number is None:
                return self._question(
                    control,
                    None,
                    "missing",
                    f"The form wants a number; '{value}' is not one.",
                )
            text = number
        if control.kind != "textarea":
            text = " ".join(text.split())
        locator = self._locator(control.ref)
        locator.fill(text)
        self.step(f"Filled {control.display_label}")
        return self._question(control, text, "filled", "Filled.")

    def _fill_select(self, control: _Control, value: str) -> FormQuestion:
        values = _split_values(value) if control.multiple else [value]
        chosen: list[_Option] = []
        for part in values:
            option = _match_option(part, control.options)
            if option is not None and option not in chosen:
                chosen.append(option)
        if not chosen:
            options = ", ".join(option.label for option in control.options[:8])
            return self._question(
                control,
                None,
                "missing",
                f"'{value}' is not one of the choices ({options}); pick one in review.",
            )
        locator = self._locator(control.ref)
        locator.select_option(value=[option.value for option in chosen])
        picked = ", ".join(option.label for option in chosen)
        self.step(f"Selected {picked} for {control.display_label}")
        return self._question(control, picked, "filled", "Selected.")

    def _fill_radio(self, control: _Control, value: str) -> FormQuestion:
        option = _match_option(value, control.options)
        if option is None or not option.ref:
            options = ", ".join(option.label for option in control.options[:8])
            return self._question(
                control,
                None,
                "missing",
                f"'{value}' is not one of the choices ({options}); pick one in review.",
            )
        self._locator(option.ref).check()
        self.step(f"Chose {option.label} for {control.display_label}")
        return self._question(control, option.label, "filled", "Chosen.")

    def _fill_checkbox(self, control: _Control, value: str) -> FormQuestion:
        if control.grouped:
            chosen: list[_Option] = []
            for part in _split_values(value):
                option = _match_option(part, control.options)
                if option is not None and option.ref and option not in chosen:
                    chosen.append(option)
            if not chosen:
                return self._question(
                    control, None, "missing", f"'{value}' matches none of the choices."
                )
            for option in chosen:
                self._locator(option.ref).check()
            picked = ", ".join(option.label for option in chosen)
            self.step(f"Checked {picked} for {control.display_label}")
            return self._question(control, picked, "filled", "Checked.")
        if normalize_label(value) in _YES:
            self._locator(control.ref).check()
            self.step(f"Checked {control.display_label}")
            return self._question(control, "Yes", "filled", "Checked.")
        status = "missing" if control.required else "skipped"
        return self._question(
            control,
            None,
            status,
            f"The answer is '{value}', so the box was left unchecked.",
        )

    def _upload(self, control: _Control) -> FormQuestion:
        label = control.display_label
        role = control.file_role
        path: str | None = None
        note = ""
        if role == "resume":
            path = self.request.resume_path
            if not path or not Path(path).is_file():
                return self._question(
                    control, None, "missing", "No resume file is available to upload."
                )
        elif role == "cover_letter":
            path = self.request.cover_letter_path
            if not path or not Path(path).is_file():
                text = self.request.cover_letter_text.strip()
                if text and any(
                    c.kind in ("textarea", "text") and c.canonical == "cover_letter"
                    for c in self.controls
                ):
                    return self._question(
                        control, None, "skipped", "Cover letter pasted as text instead."
                    )
                if text:
                    path = self._pdf_from_text(text, "Cover Letter")
                    note = " (PDF built from the cover letter text)"
                else:
                    status = "missing" if control.required else "skipped"
                    return self._question(
                        control, None, status, "No cover letter is available to upload."
                    )
        else:
            return self._question(
                control,
                None,
                "unknown",
                "Monosage does not know which document this upload wants.",
            )
        if control.disabled:
            return self._question(control, None, "skipped", "The upload is disabled.")
        try:
            self._locator(control.ref).set_input_files(path)
        except Exception as error:  # noqa: BLE001
            self.step(f"Could not upload for '{label}': {_first_line(str(error))}")
            return self._question(
                control,
                None,
                "unsupported",
                f"Upload failed: {_first_line(str(error))}",
            )
        name = Path(path).name
        self.step(f"Uploaded {name} for {label}{note}")
        return self._question(control, name, "filled", f"Uploaded {name}{note}.")

    def _pdf_from_text(self, text: str, title: str) -> str:
        from aira.pdf_text import text_to_pdf

        directory = Path(tempfile.mkdtemp(prefix="aira-apply-"))
        path = directory / f"{title}.pdf"
        path.write_bytes(text_to_pdf(text, title))

        def cleanup() -> None:
            import shutil

            shutil.rmtree(directory, ignore_errors=True)

        self.cleanups.append(cleanup)
        return str(path)

    # -- submit --------------------------------------------------------------

    def _submit_control(self) -> Locator | None:
        assert self.form is not None
        for selector in self.adapter.submit_selectors:
            locator = self.form.locator(selector)
            for index in range(locator.count()):
                candidate = locator.nth(index)
                try:
                    if candidate.is_visible() and candidate.is_enabled():
                        return candidate
                except Exception:  # noqa: BLE001
                    continue
        return None

    def _submit(self) -> ApplyOutcome:
        page = self.page
        assert page is not None
        button = self._submit_control()
        if button is None:
            self.step("No submit button was found on the form")
            return self.outcome(
                "failed",
                "The form has no submit button Monosage can press.",
                screenshot=self.screenshot("no-submit"),
            )
        try:
            caption = " ".join(
                (
                    button.inner_text(timeout=1_000)
                    or button.get_attribute("value")
                    or "Submit"
                ).split()
            )
        except Exception:  # noqa: BLE001
            caption = "Submit"
        self.document_status = None
        button.click()
        self.step(f"Clicked {caption or 'Submit'}")

        success_phrases = _SUCCESS_PHRASES + self.adapter.success_phrases
        while True:
            status = self.document_status
            if status is not None and status >= 400:
                self.step(f"The site answered HTTP {status} after submit")
                return self.outcome(
                    "failed",
                    f"The site answered HTTP {status} after submitting; the application "
                    "was not confirmed.",
                    screenshot=self.screenshot("submit-failed"),
                )
            try:
                url = page.url
            except Exception:  # noqa: BLE001
                url = ""
            text = self.page_text()
            lowered = _phrase_text(text)
            marker = next((p for p in success_phrases if p in lowered), "")
            if not marker:
                marker = next(
                    (
                        f"URL {pattern}"
                        for pattern in self.adapter.success_url_patterns
                        if re.search(pattern, url, re.IGNORECASE)
                    ),
                    "",
                )
            if marker and text:
                submitted_at = _now_iso()
                number = extract_confirmation_number(text)
                self.step(f"Confirmation appeared ({marker})")
                if number:
                    self.step(f"Confirmation number {number}")
                screenshot = self.screenshot("submitted")
                return self.outcome(
                    "submitted",
                    "",
                    screenshot=screenshot,
                    confirmation_text=" ".join(text.split())[:2_000],
                    confirmation_number=number,
                    submitted_at=submitted_at,
                )
            if time.monotonic() > self.deadline:
                break
            page.wait_for_timeout(POLL_MILLISECONDS)
        self.step("No confirmation appeared before the time limit")
        return self.outcome(
            "failed",
            "No confirmation appeared after submitting. Check the site before trying "
            "again; the page text is kept as evidence.",
            screenshot=self.screenshot("no-confirmation"),
        )


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _static_outcome(
    status: str, ats: str, request: ApplyRequest, error: str
) -> ApplyOutcome:
    return ApplyOutcome(
        status=status,
        ats=ats,
        questions=[],
        unanswered=[],
        evidence=ApplyEvidence(
            screenshot_path=None,
            page_text="",
            confirmation_text="",
            confirmation_number="",
            url=request.application_url,
            submitted_at=None,
        ),
        error=error,
        steps=[],
    )


def _resolve_ats(request: ApplyRequest) -> str:
    ats = detect_ats(request.application_url)
    source = str(request.source or "").strip().casefold()
    if ats == "generic" and source in _ADAPTERS:
        # The host is not one of the known boards (an embedded board, a custom
        # domain or a test server), but the job source says which ATS this is.
        return source
    return ats


def run(request: ApplyRequest, *, browser: Browser | None = None) -> ApplyOutcome:
    """Fill (and with ``submit=True`` submit) the application form for ``request``.

    Blocks for up to ``request.timeout_seconds``; call it from a worker thread,
    never from inside an asyncio event loop. A pre-launched patchright
    ``browser`` can be passed to reuse one Chromium across sequential runs (each
    run still gets a fresh context); otherwise one is launched and closed here.
    Never raises for browser or site problems: those come back as ``failed``.
    """
    if not str(request.application_url or "").strip():
        return _static_outcome(
            "failed", "generic", request, "The job has no application URL."
        )
    ats = _resolve_ats(request)
    if ats == "workday" or (
        ats == "generic" and str(request.source or "").strip().casefold() == "workday"
    ):
        # Account-based and multi-step: its own runner (aira/workday.py).
        from aira.workday import run_workday

        return run_workday(request, browser=browser)
    if ats == "linkedin":
        return _static_outcome(
            "unsupported",
            ats,
            request,
            "LinkedIn jobs go through the LinkedIn Easy Apply tool, not the form engine.",
        )
    adapter = _ADAPTERS[ats]
    runner = _Runner(request, adapter)
    if browser is not None:
        return runner.run_in(browser)
    try:
        from patchright.sync_api import Error as PlaywrightError
        from patchright.sync_api import sync_playwright
    except ImportError:
        return _static_outcome(
            "failed",
            ats,
            request,
            "patchright is not installed; run 'uv sync' and 'uv run patchright install chromium'.",
        )
    try:
        with sync_playwright() as playwright:
            launched = playwright.chromium.launch(headless=request.headless)
            try:
                return runner.run_in(launched)
            finally:
                launched.close()
    except PlaywrightError as error:
        runner.step("Chromium could not be started")
        return ApplyOutcome(
            status="failed",
            ats=ats,
            questions=[],
            unanswered=[],
            evidence=ApplyEvidence(None, "", "", "", request.application_url, None),
            error=f"Chromium could not be started: {_first_line(str(error))}",
            steps=list(runner.steps),
        )
