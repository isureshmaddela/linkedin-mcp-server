"""Workday applications: sign in (or create the candidate account), then walk the multi-step flow.

Workday is account-based. Every employer runs its own tenant (``acme.wd5.myworkdayjobs.com``) with its
own candidate accounts, and an application is a sequence of pages: My Information, My Experience (the
resume upload, which Workday parses), Application Questions, Voluntary Disclosures, Self Identify and
Review. :func:`run_workday` drives that flow in patchright through Workday's stable ``data-automation-id``
attributes and reuses :mod:`aira.apply_engine` for everything a page has in common with other forms
(question discovery, value resolution from the profile, confirmed answers and run answers, uploads,
evidence, the submit wait).

Rules on top of the form engine's:

* the account comes from the person (:class:`WorkdayAccount`, decrypted by
  :mod:`aira.workday_accounts`); without one the run holds before a browser starts;
* email verification, a CAPTCHA, a rejected password or an unknown account form hold the run
  ("needs you") with a plain message; nothing is bypassed;
* Voluntary Disclosures and Self Identify are never answered for the person: each question there is
  filled only from an answer they gave for this run or a saved, confirmed standard answer (for example
  "I do not wish to answer"); anything else holds the run, required or not;
* ``submit=False`` walks to Review (Workday saves each page as a draft on the employer's site), takes a
  screenshot and stops; ``submit=True`` presses Submit on Review and waits for the confirmation;
* the password is typed into the password fields only, never logged, and scrubbed from steps and errors.

Locale note: step names ("Voluntary Disclosures", "Review"), the verification notice and the
confirmation wording are matched in English (``_STEP_KINDS``, ``_VERIFY_PHRASES``). The disclosure rule
also applies to any step that asks a demographic question, whatever its name.
"""

from __future__ import annotations

import logging
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from aira.apply_engine import (
    _BLOCKED_STATUSES,
    _GENERIC_SUBMIT,
    POLL_MILLISECONDS,
    UNANSWERED_STATUSES,
    ApplyOutcome,
    ApplyRequest,
    FormQuestion,
    _Adapter,
    _Control,
    _first_line,
    _match_option,
    _Option,
    _Runner,
    _static_outcome,
)
from aira.field_mapping import normalize_label

if TYPE_CHECKING:
    from patchright.sync_api import Browser, Locator

__all__ = [
    "ACCOUNT_STATES",
    "WorkdayAccount",
    "is_workday_url",
    "run_workday",
    "tenant_host",
]

logger = logging.getLogger(__name__)

# What a saved account is known to be, after the last run that used it.
ACCOUNT_STATES = ("new", "active", "verification_needed", "sign_in_failed", "exists")

MAX_STEPS = 12
STEP_SECONDS = 20
SETTLE_MS = 3_000

_NEXT_SELECTORS = (
    "[data-automation-id='bottom-navigation-next-button']",
    "[data-automation-id='pageFooterNextButton']",
)
_AUTH_SELECTORS = (
    "[data-automation-id='signInSubmitButton']",
    "[data-automation-id='createAccountSubmitButton']",
    "input[data-automation-id='password']",
)
_ERROR_SELECTORS = (
    "[data-automation-id='errorMessage']",
    "[data-automation-id='inputAlert']",
    "[data-automation-id='errorBanner']",
    "[role='alert']",
)
_STEP_CONTAINERS = (
    "[data-automation-id='applyFlowPage']",
    "main",
    "body",
)
_VERIFY_PHRASES = (
    "verify your email",
    "verification email",
    "verify your account",
    "confirm your email",
    "activate your account",
    "check your email",
    "check your inbox",
)
_EXISTS_PHRASES = (
    "already exists",
    "already in use",
    "already registered",
    "account with this email",
)
# Step names in English; anything else is "other" and judged by its questions.
_STEP_KINDS = (
    ("review", "review"),
    ("self identify", "self_identify"),
    ("self-identify", "self_identify"),
    ("disability", "self_identify"),
    ("voluntary", "voluntary"),
    ("disclosure", "voluntary"),
    ("experience", "experience"),
    ("my information", "information"),
    ("question", "questions"),
)
_DISCLOSURE_STEPS = frozenset({"voluntary", "self_identify"})
# Demographic questions: a step asking one is a disclosure step whatever it is called.
_DEMOGRAPHIC_FIELDS = frozenset(
    {"gender", "race_ethnicity", "veteran_status", "disability_status", "pronouns"}
)
# On a disclosure step these are the person's identity, not a disclosure, and come from the profile.
_IDENTITY_FIELDS = frozenset(
    {"first_name", "last_name", "full_name", "preferred_name", "email"}
)
_DECLINE_RE = re.compile(
    r"decline|do(?: not|n't) wish|not wish to|prefer not|choose not|do(?: not|n't) want"
    r"|not to (?:answer|disclose|say|self.?identify)|wish not to",
    re.IGNORECASE,
)
_EMPTY_CHOICES = frozenset({"", "select one", "select", "choose one", "none selected"})
_DISCLOSURE_MESSAGE = (
    "Voluntary disclosure: Monosage never answers this for you. Choose your answer "
    "(for example 'I do not wish to answer') and save it to continue."
)
# Workday's data-automation-id values (on the control or its formField-<id> container).
_AID_HINTS: tuple[tuple[str, str], ...] = (
    (r"legalNameSection_firstName", "first_name"),
    (r"legalNameSection_lastName", "last_name"),
    (r"addressSection_addressLine1", "address_line"),
    (r"addressSection_city", "city"),
    (r"addressSection_postalCode", "postal_code"),
    (r"addressSection_countryRegion", "state"),
    (r"countryDropdown|country", "country"),
    (r"phone-number|phoneNumber", "phone"),
    (r"email", "email"),
    (r"linkedinQuestion|linkedin", "linkedin_url"),
    (r"sourceDropdown|source", "how_did_you_hear"),
    (r"gender", "gender"),
    (r"ethnicityDropdown|ethnicity|hispanicOrLatino|race", "race_ethnicity"),
    (r"veteranStatus|veteran", "veteran_status"),
    (r"disabilityStatus|disability", "disability_status"),
    (r"agreementCheckbox|acceptTermsAndAgreements", "legal_attestation"),
    (r"name", "full_name"),  # the Self Identify form's signature name
)

WORKDAY_ADAPTER = _Adapter(
    name="workday",
    form_selectors=(),
    submit_selectors=(*_NEXT_SELECTORS, *_GENERIC_SUBMIT),
    label_selectors=("[data-automation-id='formLabel']",),
    field_hints=(),
    success_phrases=(
        "application submitted",
        "thanks for applying",
        "you have successfully applied",
        "successfully applied",
    ),
    success_url_patterns=(),
)

_CLEAR_JS = r"""
() => document.querySelectorAll("[data-aira-ref], [data-aira-dd], [data-aira-opt]").forEach((el) => {
  el.removeAttribute("data-aira-ref"); el.removeAttribute("data-aira-dd"); el.removeAttribute("data-aira-opt");
})
"""
# For each tagged control: its automation id, what it holds now, and whether it is read-only.
_STATE_JS = r"""
(root) => Array.from(root.querySelectorAll("[data-aira-ref]")).map((el) => {
  const field = el.closest("[data-automation-id^='formField-']");
  let aid = el.getAttribute("data-automation-id") || "";
  if (!aid && field) aid = field.getAttribute("data-automation-id").replace(/^formField-/, "");
  const type = (el.getAttribute("type") || "").toLowerCase();
  const labelOf = (input) => (input.labels && input.labels[0] ? input.labels[0].textContent : input.value) || "";
  let current = "";
  if (type === "radio") {
    const group = el.name ? root.querySelectorAll(`input[type=radio][name="${CSS.escape(el.name)}"]`) : [el];
    for (const radio of group) if (radio.checked) current = labelOf(radio);
  } else if (type === "checkbox") current = el.checked ? "Yes" : "";
  else if (type === "file") current = el.files && el.files.length ? el.files[0].name : "";
  else if (el.tagName === "SELECT") current = el.value ? ((el.options[el.selectedIndex] || {}).text || el.value) : "";
  else current = el.value || "";
  return [el.getAttribute("data-aira-ref"), aid, String(current).replace(/\s+/g, " ").trim(), Boolean(el.readOnly)];
})
"""
# Workday's own dropdowns: a button that opens a listbox of role=option entries.
_DROPDOWNS_JS = r"""
(root) => {
  const clean = (t) => String(t || "").replace(/\s+/g, " ").trim();
  const out = [];
  let n = 0;
  root.querySelectorAll("button[aria-haspopup='listbox']").forEach((button) => {
    const style = getComputedStyle(button);
    const rect = button.getBoundingClientRect();
    if (style.display === "none" || style.visibility === "hidden" || rect.width === 0) return;
    n += 1;
    button.setAttribute("data-aira-dd", String(n));
    const field = button.closest("[data-automation-id^='formField-']") || button.closest("fieldset");
    let label = "";
    const by = button.getAttribute("aria-labelledby");
    if (by) label = clean(by.split(/\s+/).map((id) => {
      const node = document.getElementById(id);
      return node && node !== button ? node.textContent : "";
    }).join(" "));
    if (!label && field) { const node = field.querySelector("label, legend"); if (node) label = clean(node.textContent); }
    if (!label) label = clean(button.getAttribute("aria-label"));
    let aid = button.getAttribute("data-automation-id") || "";
    if (!aid && field) aid = (field.getAttribute("data-automation-id") || "").replace(/^formField-/, "");
    out.push({
      ref: String(n),
      label: label.replace(/[\s*✱:]+$/g, "").trim(),
      required: /[*✱]\s*$/.test(label) || button.getAttribute("aria-required") === "true"
        || /\brequired\b/i.test(button.getAttribute("aria-label") || ""),
      current: clean(button.textContent),
      aid,
      disabled: Boolean(button.disabled),
    });
  });
  return out;
}
"""
_OPTIONS_JS = r"""
() => {
  const out = [];
  let n = 0;
  document.querySelectorAll("[role='option']").forEach((el) => {
    const rect = el.getBoundingClientRect();
    if (rect.width === 0 || rect.height === 0 || getComputedStyle(el).visibility === "hidden") return;
    n += 1;
    el.setAttribute("data-aira-opt", String(n));
    out.push(String(el.textContent || "").replace(/\s+/g, " ").trim());
  });
  return out;
}
"""


@dataclass(slots=True)
class WorkdayAccount:
    """The candidate account for one Workday tenant, decrypted for one run.

    ``create`` asks the runner to create the account (the profile email and a generated password
    saved by :mod:`aira.workday_accounts`) instead of signing in. ``on_result(state, message)`` is
    told what became of the account (one of :data:`ACCOUNT_STATES`) so the store can remember it.
    """

    host: str
    email: str
    password: str = field(repr=False)
    create: bool = False
    on_result: Callable[[str, str], None] | None = field(default=None, repr=False)


def tenant_host(url: str) -> str:
    """The Workday tenant a URL belongs to (its host name, lower case)."""
    return (urlsplit(str(url or "").strip()).hostname or "").casefold().rstrip(".")


def is_workday_url(url: str) -> bool:
    host = tenant_host(url)
    return "myworkdayjobs.com" in host or host.endswith("myworkdaysite.com")


def _step_kind(name: str) -> str:
    lowered = name.casefold()
    for phrase, kind in _STEP_KINDS:
        if phrase in lowered:
            return kind
    return "other"


def _aid_canonical(aid: str) -> str | None:
    for pattern, canonical in _AID_HINTS:
        if aid and re.fullmatch(pattern, aid, re.IGNORECASE):
            return canonical
    return None


def _choose(value: str, options: list[_Option]) -> _Option | None:
    """The option a value names, also matching one "decline" wording to another."""
    option = _match_option(value, options)
    if option is not None:
        return option
    if _DECLINE_RE.search(value):
        declines = [item for item in options if _DECLINE_RE.search(item.label)]
        if len(declines) == 1:
            return declines[0]
    return None


@dataclass(slots=True)
class _Dropdown:
    ref: str
    label: str
    required: bool
    current: str
    aid: str
    disabled: bool


class _WorkdayRunner(_Runner):
    """One Workday application: authentication, then one page of the flow at a time."""

    def __init__(self, request: ApplyRequest, account: WorkdayAccount) -> None:
        super().__init__(request, WORKDAY_ADAPTER)
        self.account = account
        self.disclosure = False
        self.step_name = ""
        self._held: set[int] = set()
        self._state: dict[str, tuple[str, str, bool]] = {}
        self._pressed_at = 0.0

    # -- outcome plumbing (the password never leaves this object) -------------

    def _scrub(self, text: str) -> str:
        secret = self.account.password
        return text.replace(secret, "[redacted]") if secret and text else text

    def step(self, text: str) -> None:
        super().step(self._scrub(text))

    def outcome(
        self, status: str, error: str = "", **evidence_kwargs: Any
    ) -> ApplyOutcome:
        result = super().outcome(status, self._scrub(error), **evidence_kwargs)
        result.steps = [self._scrub(item) for item in result.steps]
        return result

    def unanswered(self) -> list[FormQuestion]:
        return [
            question
            for question in self.questions
            if question.status in UNANSWERED_STATUSES
            and (question.required or id(question) in self._held)
        ]

    def _report(self, state: str, message: str = "") -> None:
        callback = self.account.on_result
        if callback is None:
            return
        try:
            callback(state, self._scrub(message)[:300])
        except Exception:  # noqa: BLE001 - remembering the state must not end the run
            logger.warning(
                "Could not record the Workday account state for %s", self.account.host
            )

    def _hold(self, message: str, label: str) -> ApplyOutcome:
        return self.outcome("needs_review", message, screenshot=self.screenshot(label))

    # -- page helpers ----------------------------------------------------------

    def _settle(self, milliseconds: int = SETTLE_MS) -> None:
        assert self.page is not None
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=milliseconds)
            self.page.wait_for_load_state("networkidle", timeout=milliseconds)
        except Exception:  # noqa: BLE001 - a chatty page is fine; carry on
            pass

    def _visible(self, selector: str) -> Locator | None:
        assert self.page is not None
        try:
            locator = self.page.locator(selector)
            for index in range(min(locator.count(), 6)):
                candidate = locator.nth(index)
                if candidate.is_visible():
                    return candidate
        except Exception:  # noqa: BLE001 - mid-navigation reads legitimately fail
            return None
        return None

    def _press(self, locator: Locator) -> None:
        """Click a Workday button, first marking the document so a page change can be told apart."""
        assert self.page is not None
        try:
            self.page.evaluate("() => { window.__airaMark = true; }")
        except Exception:  # noqa: BLE001
            pass
        self._pressed_at = time.monotonic()
        try:
            locator.click(timeout=5_000)
        except Exception:  # noqa: BLE001 - Workday lays click_filter overlays over its buttons
            locator.dispatch_event("click")

    def _page_state(self) -> str:
        """``loading``, ``new`` (a new document since the last press) or ``same``."""
        assert self.page is not None
        try:
            marked, ready = self.page.evaluate(
                "() => [Boolean(window.__airaMark), document.readyState]"
            )
        except Exception:  # noqa: BLE001 - mid-navigation
            return "loading"
        if ready == "loading":
            return "loading"
        return "same" if marked else "new"

    def _settled_after_press(self) -> str | None:
        """The page state once it is worth reading, ``None`` while it is still changing.

        A form post replaces the document; Workday's own pages change in place, so a page that
        is still the same document is read only after a short grace period.
        """
        state = self._page_state()
        if state == "loading":
            return None
        if state == "same" and time.monotonic() - self._pressed_at < 1.5:
            return None
        return state

    def _in_flow(self) -> bool:
        return any(
            self._visible(selector) is not None
            for selector in (
                "[data-automation-id='progressBar']",
                "[data-automation-id='applyFlowPage']",
                *_NEXT_SELECTORS,
            )
        )

    def _wait_for_flow(self) -> bool:
        """Wait for the sign-in form or an application page (after Apply)."""
        assert self.page is not None
        limit = min(self.deadline, time.monotonic() + STEP_SECONDS)
        while time.monotonic() < limit:
            if self._settled_after_press() is not None and (
                self._auth_visible() or self._in_flow() or self._visible_captcha()
            ):
                return True
            self.page.wait_for_timeout(POLL_MILLISECONDS)
        return False

    def _error_text(self) -> str:
        for selector in _ERROR_SELECTORS:
            found = self._visible(selector)
            if found is None:
                continue
            try:
                text = " ".join(found.inner_text(timeout=1_000).split())
            except Exception:  # noqa: BLE001
                continue
            if text:
                return text[:300]
        return ""

    def _says(self, phrases: tuple[str, ...]) -> bool:
        text = self.page_text().casefold()
        return any(phrase in text for phrase in phrases)

    def _auth_visible(self) -> bool:
        return any(self._visible(selector) is not None for selector in _AUTH_SELECTORS)

    def _checkpoint(self) -> ApplyOutcome | None:
        """Stops that can appear on any page: already applied, a CAPTCHA."""
        if self._says(("already applied", "already submitted an application")):
            self.step("Workday says this job was already applied to")
            return self.outcome(
                "already_applied",
                "The site reports an application already exists for this job.",
                screenshot=self.screenshot("already-applied"),
            )
        captcha = self._visible_captcha()
        if captcha:
            self.step(f"A verification step is on the page ({captcha})")
            return self._hold(
                "Workday shows a CAPTCHA or verification step. Monosage never bypasses these: "
                "open the job, complete it yourself, then run the application again.",
                "captcha",
            )
        return None

    # -- the run -----------------------------------------------------------------

    def _drive(self) -> ApplyOutcome:
        page = self.page
        assert page is not None
        host = urlsplit(self.url).hostname or self.url
        response = page.goto(self.url, wait_until="domcontentloaded")
        if response is not None and response.status >= 400:
            self.step(f"The page answered HTTP {response.status}")
            if response.status in _BLOCKED_STATUSES:
                return self.outcome(
                    "unsupported",
                    f"{host} refused the automated visit (HTTP {response.status}). "
                    "Open the posting and apply yourself.",
                    screenshot=self.screenshot("blocked"),
                )
            return self.outcome(
                "failed",
                f"The application page answered HTTP {response.status}.",
                screenshot=self.screenshot("error"),
            )
        self._settle()
        self.step(f"Opened the Workday site at {host}")
        stop = self._checkpoint()
        if stop is not None:
            return stop
        self._start_application()
        if not self._wait_for_flow():
            stop = self._checkpoint()
            if stop is not None:
                return stop
            self.step("No Workday application or sign-in appeared")
            return self.outcome(
                "unsupported",
                "Monosage could not find the Workday application on this page. Open the "
                "posting and apply yourself.",
                screenshot=self.screenshot("no-form"),
            )

        signed_in = False
        last_step = ""
        repeats = 0
        for _ in range(MAX_STEPS):
            if time.monotonic() > self.deadline:
                break
            stop = self._checkpoint()
            if stop is not None:
                return stop
            if self._auth_visible():
                if signed_in:
                    return self._hold(
                        f"Workday asked to sign in again on {host}. Open the job and sign in yourself.",
                        "sign-in",
                    )
                held = self._authenticate()
                if held is not None:
                    return held
                signed_in = True
                continue
            name, raw = self._step_info()
            signature = f"{page.url}|{raw}"
            repeats = repeats + 1 if raw and signature == last_step else 0
            if repeats >= 2:
                return self.outcome(
                    "failed",
                    f"Workday stayed on '{name}' after Save and Continue.",
                    screenshot=self.screenshot("stuck"),
                )
            last_step = signature
            kind = _step_kind(name)
            if kind == "review" or self._next_caption().casefold() == "submit":
                return self._review(name or "Review")
            held = self._fill_step(name or "this page", kind)
            if held is not None:
                return held
            moved = self._next(name, signature)
            if moved is not None:
                return moved
        self.step("The Workday flow did not reach Review")
        return self.outcome(
            "failed",
            "The Workday application did not reach the Review page in time.",
            screenshot=self.screenshot("timeout"),
        )

    def _start_application(self) -> None:
        """From the job page: Apply, then Apply Manually (the resume is uploaded on My Experience)."""
        assert self.page is not None
        apply = self._visible("[data-automation-id='adventureButton']")
        if apply is not None:
            self._press(apply)
            self.step("Clicked Apply")
            limit = time.monotonic() + 5
            while (
                time.monotonic() < limit
                and self._visible("[data-automation-id='applyManually']") is None
                and not (self._auth_visible() or self._in_flow())
            ):
                self.page.wait_for_timeout(POLL_MILLISECONDS)
        manual = self._visible("[data-automation-id='applyManually']")
        if manual is not None:
            self._press(manual)
            self.step("Chose Apply Manually")

    # -- account -----------------------------------------------------------------

    def _authenticate(self) -> ApplyOutcome | None:
        host = self.account.host
        if self.account.create:
            return self._create_account(host)
        return self._sign_in(host)

    def _fill_secret(self, selector: str, value: str) -> bool:
        target = self._visible(selector)
        if target is None:
            return False
        try:
            target.fill(value)
        except Exception:  # noqa: BLE001 - never echo what was typed
            return False
        return True

    def _await_auth(self, button: str, before_error: str) -> str:
        """Wait after pressing an account button: verify, exists, error, signin or done."""
        assert self.page is not None
        limit = time.monotonic() + STEP_SECONDS
        while time.monotonic() < limit:
            self.page.wait_for_timeout(POLL_MILLISECONDS)
            state = self._settled_after_press()
            if state is None:
                continue
            if self._says(_VERIFY_PHRASES):
                return "verify"
            error = self._error_text()
            if error and (state == "new" or error != before_error):
                return (
                    "exists"
                    if any(p in error.casefold() for p in _EXISTS_PHRASES)
                    else "error"
                )
            if self._visible_captcha():
                return "captcha"
            if self._visible(button) is None:
                if (
                    self._visible("[data-automation-id='signInSubmitButton']")
                    is not None
                ):
                    return "signin"
                if not self._auth_visible():
                    return "done"
        return "timeout"

    def _terms_checkbox(self) -> ApplyOutcome | None:
        """The account form's terms box is a legal attestation: only a confirmed answer ticks it."""
        box = self._visible("[data-automation-id='createAccountCheckbox']")
        if box is None:
            return None
        saved = self.request.standard_answers.get("legal_attestation")
        agreed = (
            isinstance(saved, Mapping)
            and bool(saved.get("confirmed"))
            and normalize_label(str(saved.get("value") or ""))
            in {"yes", "true", "agree", "accept"}
        )
        if agreed:
            box.check()
            self.step("Accepted the account terms (your confirmed legal attestation)")
            return None
        self.questions.append(
            FormQuestion(
                label="Accept the Workday account terms",
                kind="checkbox",
                required=True,
                options=[],
                provider_field_id="createAccountCheckbox",
                canonical_field="legal_attestation",
                value=None,
                status="needs_confirmation",
                message="Creating the account accepts the site's terms. Confirm your legal "
                "attestation answer first.",
            )
        )
        return self._hold(
            f"Creating a Workday account on {self.account.host} accepts its terms. Confirm the "
            "legal attestation answer in Your answers, or create the account yourself.",
            "account-terms",
        )

    def _create_account(self, host: str) -> ApplyOutcome | None:
        if self._visible("[data-automation-id='createAccountSubmitButton']") is None:
            link = self._visible("[data-automation-id='createAccountLink']")
            if link is not None:
                self._press(link)
                self._settle(1_500)
        if self._visible("[data-automation-id='createAccountSubmitButton']") is None:
            self.step("No account form was found; trying to sign in")
            return self._sign_in(host)
        held = self._terms_checkbox()
        if held is not None:
            return held
        email_ok = self._fill_secret(
            "input[data-automation-id='email']", self.account.email
        )
        password_ok = self._fill_secret(
            "input[data-automation-id='password']", self.account.password
        )
        self._fill_secret(
            "input[data-automation-id='verifyPassword']", self.account.password
        )
        if not (email_ok and password_ok):
            return self._hold(
                f"Monosage could not fill the Workday account form on {host}. Create the account "
                "yourself, then save its email and password in Settings.",
                "create-account",
            )
        if self._visible_captcha():
            return self._checkpoint()
        button = "[data-automation-id='createAccountSubmitButton']"
        target = self._visible(button)
        assert target is not None
        before_error = self._error_text()
        self._press(target)
        self.step(f"Asked {host} to create the account for {self.account.email}")
        result = self._await_auth(button, before_error)
        if result == "verify":
            self.step("Workday sent a verification email")
            self._report("verification_needed", "Workday sent a verification email.")
            return self._hold(
                f"Workday created the account on {host} and sent a verification email to "
                f"{self.account.email}. Open it and verify the account, then run the "
                "application again.",
                "verify-email",
            )
        if result == "exists":
            self.step("An account with this email already exists; signing in instead")
            self._report("exists", "An account with this email already exists.")
            self.account.create = False
            if self._visible("[data-automation-id='signInSubmitButton']") is None:
                link = self._visible("[data-automation-id='signInLink']")
                if link is not None:
                    self._press(link)
                    self._settle(1_500)
            return self._sign_in(host, after_exists=True)
        if result == "captcha":
            return self._checkpoint()
        if result == "error":
            error = self._error_text()
            self._report("sign_in_failed", error)
            return self._hold(
                f"Workday did not create the account on {host}: {error}",
                "create-account",
            )
        if result == "timeout":
            return self._hold(
                f"Workday did not answer the account form on {host}.", "create-account"
            )
        self.account.create = False
        if result == "signin":
            self.step("Account created; signing in")
            return self._sign_in(host)
        self.step(f"Created the Workday account for {self.account.email}")
        self._report("active", "Account created.")
        return None

    def _sign_in(self, host: str, *, after_exists: bool = False) -> ApplyOutcome | None:
        button = "[data-automation-id='signInSubmitButton']"
        if self._visible(button) is None:
            link = self._visible("[data-automation-id='signInLink']")
            if link is not None:
                self._press(link)
                self._settle(1_500)
        if self._visible(button) is None:
            return self._hold(
                f"Monosage could not find the Workday sign-in form on {host}. Open the job and sign in yourself.",
                "sign-in",
            )
        email_ok = self._fill_secret(
            "input[data-automation-id='email']", self.account.email
        )
        password_ok = self._fill_secret(
            "input[data-automation-id='password']", self.account.password
        )
        if not (email_ok and password_ok):
            return self._hold(
                f"Monosage could not fill the Workday sign-in form on {host}.",
                "sign-in",
            )
        if self._visible_captcha():
            return self._checkpoint()
        target = self._visible(button)
        assert target is not None
        before_error = self._error_text()
        self._press(target)
        self.step(f"Signed in to {host} as {self.account.email}")
        result = self._await_auth(button, before_error)
        if result == "verify":
            self._report(
                "verification_needed", "Workday asks to verify the email address."
            )
            return self._hold(
                f"Workday asks you to verify {self.account.email} before signing in to {host}. "
                "Open the verification email, then run the application again.",
                "verify-email",
            )
        if result == "captcha":
            return self._checkpoint()
        if result in ("error", "exists", "timeout", "signin"):
            error = self._error_text() or "the sign-in did not go through"
            self.steps[-1] = f"Could not sign in to {host}"
            self._report("sign_in_failed", error)
            if after_exists:
                message = (
                    f"An account for {self.account.email} already exists on {host}, and the saved "
                    "password did not open it. Save that account's password in Settings > "
                    "Workday accounts, then run the application again."
                )
            else:
                message = (
                    f"Workday did not accept the email and password saved for {host} ({error}). "
                    "Update them in Settings > Workday accounts, then run the application again."
                )
            return self._hold(message, "sign-in")
        self._report("active", "Signed in.")
        return None

    # -- one page of the flow ----------------------------------------------------

    def _step_info(self) -> tuple[str, str]:
        """``(name, raw)``: the step's name and the progress text that tells pages apart."""
        assert self.page is not None
        for selector in (
            "[data-automation-id='progressBarActiveStep']",
            "[data-automation-id='applyFlowPage'] h2",
            "h2",
        ):
            found = self._visible(selector)
            if found is None:
                continue
            try:
                raw = found.inner_text(timeout=1_000)
            except Exception:  # noqa: BLE001
                continue
            text = re.sub(r"(?i)(current\s+)?step\s+\d+\s+of\s+\d+", " ", raw)
            lines = [
                " ".join(line.split()) for line in text.splitlines() if line.strip()
            ]
            if lines:
                return lines[-1][:120], " ".join(raw.split())
        return "", ""

    def _next_button(self) -> Locator | None:
        for selector in _NEXT_SELECTORS:
            found = self._visible(selector)
            if found is not None:
                return found
        return None

    def _next_caption(self) -> str:
        button = self._next_button()
        if button is None:
            return ""
        try:
            return " ".join(button.inner_text(timeout=1_000).split())
        except Exception:  # noqa: BLE001
            return ""

    def _container(self) -> Locator:
        assert self.page is not None
        for selector in _STEP_CONTAINERS:
            locator = self.page.locator(selector)
            if locator.count():
                return locator.first
        return self.page.locator("body")

    def _discover(self) -> None:
        assert self.page is not None and self.form is not None
        self.page.evaluate(_CLEAR_JS)
        super()._discover()
        rows = self.form.evaluate(_STATE_JS)
        self._state = {
            str(ref): (str(aid), str(current), bool(ro))
            for ref, aid, current, ro in rows
        }
        for control in self.controls:
            aid = self._state.get(control.ref, ("", "", False))[0]
            hinted = _aid_canonical(aid)
            if hinted:
                control.canonical = hinted
            if (
                control.kind == "file"
                and control.file_role is None
                and aid == "file-upload-input-ref"
            ):
                control.file_role = "resume"

    def _fill_step(self, name: str, kind: str) -> ApplyOutcome | None:
        assert self.page is not None
        self.step(f"Workday step: {name}")
        self.step_name = name
        self.frame = self.page.main_frame
        self.form = self._container()
        self._discover()
        uploads: list[FormQuestion] = []
        files = [control for control in self.controls if control.kind == "file"]
        if files:
            for control in files:
                uploads.append(self._upload_once(control))
            # Workday parses the resume and fills the experience fields; read the page again.
            self._settle()
            self._discover()
        dropdowns = self._dropdowns()
        canonicals = {c.canonical for c in self.controls} | {
            _aid_canonical(d.aid) or self._dropdown_control(d).canonical
            for d in dropdowns
        }
        self.disclosure = kind in _DISCLOSURE_STEPS or (
            kind == "other" and bool(canonicals & _DEMOGRAPHIC_FIELDS)
        )
        if self.disclosure:
            self.step(
                f"{name} is a disclosure step: only your own saved choices are used"
            )
        questions = list(uploads)
        for control in self.controls:
            if control.kind != "file":
                questions.append(self._fill(control))
        for dropdown in dropdowns:
            questions.append(self._fill_dropdown(dropdown))
        self.questions.extend(questions)
        filled = sum(1 for q in questions if q.status == "filled")
        self.step(f"Filled {filled} of {len(questions)} questions on {name}")

        stop = self._checkpoint()
        if stop is not None:
            return stop
        held = [
            q
            for q in questions
            if q.status in UNANSWERED_STATUSES and (q.required or id(q) in self._held)
        ]
        if not held:
            return None
        labels = ", ".join(q.label for q in held[:6])
        self.step(f"{len(held)} question(s) on {name} need you: {labels}")
        if self.disclosure:
            message = (
                f"{name} waits for you: Monosage never answers voluntary disclosure or "
                f"self-identification questions on your behalf. Answer: {labels}."
            )
        else:
            message = f"{len(held)} question(s) on {name} need an answer: {labels}."
        return self._hold(message, "review")

    def _upload_once(self, control: _Control) -> FormQuestion:
        """Upload the resume, unless the page already lists an uploaded file."""
        if control.file_role == "resume" and self._visible(
            "[data-automation-id='file-upload-item'], [data-automation-id='file-upload-successful']"
        ):
            return self._question(
                control, None, "filled", "A file is already attached on Workday."
            )
        return self._upload(control)

    def _next(self, name: str, before: str) -> ApplyOutcome | None:
        assert self.page is not None
        button = self._next_button()
        if button is None:
            self.step(f"No Save and Continue button on {name}")
            return self.outcome(
                "failed",
                f"Workday shows no Save and Continue button on '{name}'.",
                screenshot=self.screenshot("no-next"),
            )
        self.document_status = None
        before_error = self._error_text()
        self._press(button)
        self.step(f"Saved {name} and continued")
        limit = min(self.deadline, time.monotonic() + STEP_SECONDS)
        while time.monotonic() < limit:
            self.page.wait_for_timeout(POLL_MILLISECONDS)
            state = self._settled_after_press()
            if state is None:
                continue
            status = self.document_status
            if status is not None and status >= 400:
                return self.outcome(
                    "failed",
                    f"Workday answered HTTP {status} after '{name}'.",
                    screenshot=self.screenshot("error"),
                )
            error = self._error_text()
            if error and (state == "new" or error != before_error):
                self.step(f"Workday did not accept {name}: {error}")
                return self._hold(f"Workday did not accept '{name}': {error}", "review")
            try:
                url = self.page.url
            except Exception:  # noqa: BLE001
                continue
            if self._auth_visible():
                return None
            current, raw = self._step_info()
            if current and f"{url}|{raw}" != before:
                return None
        return self.outcome(
            "failed",
            f"Workday did not move on from '{name}'.",
            screenshot=self.screenshot("stuck"),
        )

    def _review(self, name: str) -> ApplyOutcome:
        assert self.page is not None
        self.step(f"Reached {name}")
        self.form = self.page.locator("body")
        if not self.request.submit:
            self.step("Stopped at Review before submitting, as asked")
            return self.outcome("filled", "", screenshot=self.screenshot("review"))
        unanswered = self.unanswered()
        if unanswered:
            labels = ", ".join(question.label for question in unanswered[:6])
            return self._hold(f"Questions still need an answer: {labels}.", "review")
        return self._submit()

    # -- values ----------------------------------------------------------------

    def _resolve(self, control: _Control) -> tuple[str | None, str, str]:
        if self.disclosure and control.canonical not in _IDENTITY_FIELDS:
            value, status, message = self._resolve_disclosure(control)
        else:
            value, status, message = super()._resolve(control)
        if status == "filled" and value and control.options:
            option = _choose(value, control.options)
            if option is not None:
                value = option.label
        return value, status, message

    def _resolve_disclosure(self, control: _Control) -> tuple[str | None, str, str]:
        extra = self._extra_answer(control)
        if extra:
            return extra, "filled", "Your answer for this run."
        canonical = control.canonical
        saved = self.request.standard_answers.get(canonical) if canonical else None
        if isinstance(saved, Mapping) and saved.get("confirmed"):
            value = str(saved.get("value") or "").strip()
            if value:
                return value, "filled", "Your saved, confirmed choice."
        return None, "needs_confirmation", _DISCLOSURE_MESSAGE

    def _fill(self, control: _Control) -> FormQuestion:
        aid, current, read_only = self._state.get(control.ref, ("", "", False))
        if read_only:
            status = "filled" if current else "skipped"
            return self._question(
                control, current or None, status, "Read-only on Workday."
            )
        logged = len(self.steps)
        question = super()._fill(control)
        if question.status in UNANSWERED_STATUSES and current:
            keeps = (
                not self.disclosure
                or "date" in aid.casefold()
                or (control.canonical in _IDENTITY_FIELDS)
            )
            if keeps:
                # Workday (its resume parsing, or an earlier visit) filled this in already.
                del self.steps[logged:]
                self.step(f"Kept {control.display_label} as Workday filled it in")
                return self._question(
                    control, current, "filled", "Already filled in on the Workday form."
                )
        if self.disclosure and question.status in UNANSWERED_STATUSES:
            self._held.add(id(question))
        return question

    # -- Workday dropdowns -----------------------------------------------------

    def _dropdowns(self) -> list[_Dropdown]:
        assert self.form is not None
        try:
            rows = self.form.evaluate(_DROPDOWNS_JS)
        except Exception:  # noqa: BLE001
            return []
        return [
            _Dropdown(
                ref=str(row.get("ref") or ""),
                label=str(row.get("label") or ""),
                required=bool(row.get("required")),
                current=str(row.get("current") or ""),
                aid=str(row.get("aid") or ""),
                disabled=bool(row.get("disabled")),
            )
            for row in rows
        ]

    def _dropdown_control(self, dropdown: _Dropdown) -> _Control:
        control = _Control(
            ref=dropdown.ref,
            kind="select",
            grouped=False,
            id="",
            name=dropdown.aid,
            label=dropdown.label,
            required=dropdown.required,
            options=[],
            visible=True,
            disabled=dropdown.disabled,
            placeholder="",
            accept="",
            multiple=False,
        )
        control.canonical = _aid_canonical(dropdown.aid) or self._canonical_for(control)
        return control

    def _open_listbox(self, button: Locator) -> list[str]:
        assert self.page is not None
        self.page.evaluate(
            "() => document.querySelectorAll('[data-aira-opt]').forEach((el) => el.removeAttribute('data-aira-opt'))"
        )
        self._press(button)
        try:
            self.page.locator("[role='option']:visible").first.wait_for(
                state="visible", timeout=3_000
            )
        except Exception:  # noqa: BLE001
            return []
        return [str(text) for text in self.page.evaluate(_OPTIONS_JS)]

    def _close_listbox(self) -> None:
        assert self.page is not None
        try:
            self.page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            pass

    def _fill_dropdown(self, dropdown: _Dropdown) -> FormQuestion:
        assert self.page is not None
        control = self._dropdown_control(dropdown)
        chosen = (
            ""
            if normalize_label(dropdown.current) in _EMPTY_CHOICES
            else dropdown.current
        )
        if control.disabled:
            return self._question(
                control, chosen or None, "skipped", "The dropdown is disabled."
            )
        button = self.page.locator(f"[data-aira-dd='{dropdown.ref}']")
        value, status, message = self._resolve(control)
        try:
            labels = self._open_listbox(button)
        except Exception as error:  # noqa: BLE001
            self.step(
                f"Could not open '{control.display_label}': {_first_line(str(error))}"
            )
            labels = []
        control.options = [
            _Option(label=text, value=text, ref=str(index + 1))
            for index, text in enumerate(labels)
        ]
        if status == "filled" and value:
            option = _choose(value, control.options)
            if option is not None:
                try:
                    self.page.locator(f"[data-aira-opt='{option.ref}']").first.click(
                        timeout=5_000
                    )
                except Exception as error:  # noqa: BLE001
                    self._close_listbox()
                    return self._question(
                        control,
                        None,
                        "unsupported",
                        f"Choosing failed: {_first_line(str(error))}",
                    )
                self.step(f"Chose {option.label} for {control.display_label}")
                return self._question(
                    control, option.label, "filled", message or "Chosen."
                )
            self._close_listbox()
            listed = ", ".join(item.label for item in control.options[:8])
            question = self._question(
                control,
                None,
                "missing",
                f"'{value}' is not one of the choices ({listed}); pick one.",
            )
        else:
            self._close_listbox()
            question = self._question(control, None, status, message)
        if chosen and (not self.disclosure):
            return self._question(
                control, chosen, "filled", "Already chosen on the Workday form."
            )
        if self.disclosure:
            self._held.add(id(question))
        elif dropdown.required and status in UNANSWERED_STATUSES:
            self.step(f"Left '{control.display_label}' empty: {question.message}")
        return question


def run_workday(
    request: ApplyRequest, *, browser: Browser | None = None
) -> ApplyOutcome:
    """Apply on a Workday tenant with the person's account for it (``request.workday_account``).

    Holds before any browser starts when there is no account to use. Same contract as
    :func:`aira.apply_engine.run`: never raises for browser or site problems.
    """
    account = getattr(request, "workday_account", None)
    host = tenant_host(request.application_url)
    if not isinstance(account, WorkdayAccount):
        return _static_outcome(
            "needs_review",
            "workday",
            request,
            f"Workday needs a candidate account for {host}. Add your email and password for it "
            "in Settings > Integrations > Workday accounts, or let Monosage create the account "
            "for you there, then run the application again.",
        )
    if not account.email or not account.password:
        return _static_outcome(
            "needs_review",
            "workday",
            request,
            f"The Workday account saved for {host} has no email or password. Update it in "
            "Settings > Integrations > Workday accounts.",
        )
    runner = _WorkdayRunner(request, account)
    if browser is not None:
        return runner.run_in(browser)
    try:
        from patchright.sync_api import Error as PlaywrightError
        from patchright.sync_api import sync_playwright
    except ImportError:
        return _static_outcome(
            "failed",
            "workday",
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
        outcome = _static_outcome(
            "failed",
            "workday",
            request,
            f"Chromium could not be started: {_first_line(str(error))}",
        )
        outcome.steps = list(runner.steps)
        return outcome
