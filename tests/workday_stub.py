"""A stand-in Workday tenant for the Workday runner's tests (local only, never the network).

It serves the candidate flow with Workday's ``data-automation-id`` attributes:

* ``GET  /job``                 the posting: ``adventureButton`` (Apply) opens ``applyManually``
* ``GET  /apply``               sign in (``signInSubmitButton``) or, through ``createAccountLink``,
                                create an account (``email``, ``password``, ``verifyPassword``,
                                ``createAccountCheckbox``, ``createAccountSubmitButton``)
* ``POST /auth/signin``, ``POST /auth/create``
* ``GET/POST /apply/step/<n>``  My Information, My Experience (``file-upload-input-ref``),
                                Application Questions, Voluntary Disclosures, Self Identify, Review;
                                ``bottom-navigation-next-button`` posts the page and moves on
* ``POST /apply/submit``        records the application and shows the confirmation

Workday's own dropdowns are buttons with ``aria-haspopup=listbox`` that open ``role=option`` lists.
Switches on the instance: ``verify_on_create`` (a new account must verify its email),
``captcha_on_signin``, ``extra_question`` (a required question nobody can map on Application
Questions) and ``already_applied``. ``accounts`` maps email to ``{"password", "verified"}``;
``pages`` records every posted step, ``submissions`` every submitted application.
"""

from __future__ import annotations

import html
import secrets
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

from ats_stub import UploadedFile, _parse_body

COMPANY = "Example Corp"
EXTRA_QUESTION = "Which of our products have you used?"
STEP_NAMES = (
    "My Information",
    "My Experience",
    "Application Questions",
    "Voluntary Disclosures",
    "Self Identify",
    "Review",
)
DECLINE = "I do not wish to answer"
DISABILITY_OPTIONS = (
    "Yes, I have a disability (or previously had a disability)",
    "No, I do not have a disability and have not had one in the past",
    "I do not want to answer",
)

_SCRIPT = """
<script>
document.addEventListener("click", (event) => {
  const button = event.target.closest("button[aria-haspopup='listbox']");
  if (button) {
    const list = document.getElementById(button.getAttribute("aria-controls"));
    const open = list.hidden;
    document.querySelectorAll("[role='listbox']").forEach((other) => { other.hidden = true; });
    list.hidden = !open;
    button.setAttribute("aria-expanded", String(open));
    return;
  }
  const option = event.target.closest("[role='option']");
  if (option) {
    const list = option.closest("[role='listbox']");
    const owner = document.querySelector(`[aria-controls='${list.id}']`);
    owner.textContent = option.textContent;
    document.getElementById(owner.dataset.input).value = option.textContent;
    list.hidden = true;
  }
  const toggle = event.target.closest("[data-toggle]");
  if (toggle) {
    document.getElementById("signInForm").hidden = toggle.dataset.toggle !== "signin";
    document.getElementById("createForm").hidden = toggle.dataset.toggle !== "create";
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") document.querySelectorAll("[role='listbox']").forEach((list) => { list.hidden = true; });
});
</script>
"""
_CAPTCHA = (
    '<div class="g-recaptcha" data-sitekey="stub" style="width:304px;height:78px;'
    'border:1px solid #ccc">I am not a robot</div>'
)


def _page(title: str, body: str) -> bytes:
    return (
        f'<!doctype html><html lang="en"><head><meta charset="utf-8"><title>{title}</title></head>'
        f'<body style="font-family:Arial;padding:24px">{body}{_SCRIPT}</body></html>'
    ).encode("utf-8")


def _text(
    aid: str,
    label: str,
    *,
    required: bool = False,
    value: str = "",
    kind: str = "text",
    readonly: bool = False,
) -> str:
    star = '<abbr title="required">*</abbr>' if required else ""
    extra = (" required" if required else "") + (" readonly" if readonly else "")
    return (
        f'<div data-automation-id="formField-{aid}"><label data-automation-id="formLabel" for="{aid}">'
        f'{label}{star}</label><input type="{kind}" id="{aid}" name="{aid}" data-automation-id="{aid}"'
        f' value="{html.escape(value)}"{extra}></div>'
    )


def _dropdown(
    aid: str, label: str, options: tuple[str, ...], *, required: bool = True
) -> str:
    star = '<abbr title="required">*</abbr>' if required else ""
    items = "".join(
        f'<li role="option" data-automation-id="promptOption">{html.escape(o)}</li>'
        for o in options
    )
    return (
        f'<div data-automation-id="formField-{aid}"><label id="label-{aid}">{label}{star}</label>'
        f'<button type="button" aria-haspopup="listbox" aria-controls="list-{aid}" aria-labelledby="label-{aid}"'
        f' data-automation-id="{aid}" data-input="value-{aid}"{' aria-required="true"' if required else ""}>Select One</button>'
        f'<input type="hidden" id="value-{aid}" name="{aid}">'
        f'<ul role="listbox" id="list-{aid}" hidden>{items}</ul></div>'
    )


def _radios(aid: str, label: str, options: tuple[str, ...]) -> str:
    items = "".join(
        f'<label><input type="radio" name="{aid}" value="{html.escape(o)}" required> {html.escape(o)}</label><br>'
        for o in options
    )
    return (
        f'<fieldset data-automation-id="formField-{aid}"><legend>{label}<abbr title="required">*</abbr></legend>'
        f"{items}</fieldset>"
    )


@dataclass(slots=True)
class Application:
    email: str
    pages: dict[int, dict[str, list[str]]] = field(default_factory=dict)
    files: dict[str, UploadedFile] = field(default_factory=dict)


class WorkdayStub:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self.base_url = ""
        self.reset()

    # -- lifecycle ------------------------------------------------------------

    def reset(self) -> None:
        self.accounts: dict[str, dict[str, Any]] = {}
        self.sessions: dict[str, str] = {}
        self.applications: dict[str, Application] = {}
        self.submissions: list[Application] = []
        self.requests: list[tuple[str, str]] = []
        self.auth_posts: list[dict[str, list[str]]] = []
        self.verify_on_create = False
        self.captcha_on_signin = False
        self.extra_question = False
        self.already_applied = False

    def start(self) -> WorkdayStub:
        stub = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *_args: Any) -> None:
                return

            def do_GET(self) -> None:
                stub._handle(self, b"")

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                stub._handle(self, self.rfile.read(length) if length else b"")

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self.base_url = f"http://127.0.0.1:{self._server.server_address[1]}"
        server = self._server
        threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
        ).start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    @property
    def job_url(self) -> str:
        return f"{self.base_url}/job"

    def add_account(self, email: str, password: str, *, verified: bool = True) -> None:
        self.accounts[email] = {"password": password, "verified": verified}

    def posted_steps(self) -> list[int]:
        return sorted({n for app in self.applications.values() for n in app.pages})

    # -- pages ----------------------------------------------------------------

    def _auth_page(self, error: str = "", mode: str = "signin") -> bytes:
        banner = (
            f'<div data-automation-id="errorMessage" role="alert">{html.escape(error)}</div>'
            if error
            else ""
        )
        captcha = _CAPTCHA if self.captcha_on_signin else ""
        sign_in = (
            f'<form id="signInForm" method="post" action="/auth/signin"{" hidden" if mode != "signin" else ""}>'
            "<h2>Sign In</h2>"
            '<label for="si-email">Email Address</label><input id="si-email" type="email" name="email" data-automation-id="email">'
            '<label for="si-password">Password</label><input id="si-password" type="password" name="password" data-automation-id="password">'
            f"{captcha}"
            '<button type="submit" data-automation-id="signInSubmitButton">Sign In</button>'
            '<button type="button" data-automation-id="createAccountLink" data-toggle="create">Create Account</button>'
            "</form>"
        )
        create = (
            f'<form id="createForm" method="post" action="/auth/create"{" hidden" if mode != "create" else ""}>'
            "<h2>Create Account</h2>"
            '<label for="ca-email">Email Address</label><input id="ca-email" type="email" name="email" data-automation-id="email">'
            '<label for="ca-password">Password</label><input id="ca-password" type="password" name="password" data-automation-id="password">'
            '<label for="ca-verify">Verify New Password</label><input id="ca-verify" type="password" name="verifyPassword" data-automation-id="verifyPassword">'
            '<label><input type="checkbox" name="terms" value="yes" data-automation-id="createAccountCheckbox"> '
            "I agree to the candidate privacy terms</label>"
            '<button type="submit" data-automation-id="createAccountSubmitButton">Create Account</button>'
            '<button type="button" data-automation-id="signInLink" data-toggle="signin">Sign In</button>'
            "</form>"
        )
        return _page(f"Apply - {COMPANY}", banner + sign_in + create)

    def _progress(self, step: int) -> str:
        items = []
        for index, name in enumerate(STEP_NAMES, start=1):
            if index == step:
                items.append(
                    f'<li data-automation-id="progressBarActiveStep"><span>current step {index} of '
                    f"{len(STEP_NAMES)}</span><br><label>{name}</label></li>"
                )
            else:
                items.append(
                    f'<li data-automation-id="progressBarInactiveStep">{name}</li>'
                )
        return f'<ol data-automation-id="progressBar">{"".join(items)}</ol>'

    def _step_fields(self, step: int, email: str) -> str:
        if step == 1:
            return (
                _dropdown(
                    "sourceDropdown",
                    "How Did You Hear About Us?",
                    ("LinkedIn", "Company Website", "Referral"),
                )
                + _dropdown(
                    "countryDropdown", "Country", ("Canada", "India", "United States")
                )
                + _text("legalNameSection_firstName", "First Name", required=True)
                + _text("legalNameSection_lastName", "Last Name", required=True)
                + _text("addressSection_addressLine1", "Address Line 1")
                + _text("addressSection_city", "City", required=True)
                + _text("email", "Email Address", value=email, readonly=True)
                + _dropdown(
                    "phone-device-type", "Phone Device Type", ("Mobile", "Home", "Work")
                )
                + _text("phone-number", "Phone Number", required=True, kind="tel")
            )
        if step == 2:
            return (
                '<div data-automation-id="formField-resume"><label for="resume-upload">Upload a file (5MB max)'
                '<abbr title="required">*</abbr></label><input type="file" id="resume-upload" name="resume"'
                ' data-automation-id="file-upload-input-ref" required></div>'
                # Workday's resume parsing fills this in; nothing in the profile names it.
                + _text(
                    "jobTitle",
                    "Most Recent Job Title",
                    required=True,
                    value="Cloud Architect",
                )
                + _text("linkedinQuestion", "LinkedIn")
            )
        if step == 3:
            extra = ""
            if self.extra_question:
                extra = (
                    '<div data-automation-id="formField-products"><label for="products">'
                    f'{EXTRA_QUESTION}<abbr title="required">*</abbr></label>'
                    '<textarea id="products" name="products" required></textarea></div>'
                )
            return (
                _dropdown(
                    "workAuthorization",
                    "Are you legally authorized to work in the United States?",
                    ("Yes", "No"),
                )
                + _dropdown(
                    "sponsorship",
                    "Will you now or in the future require sponsorship for employment visa status?",
                    ("Yes", "No"),
                )
                + extra
            )
        if step == 4:
            return (
                '<div data-automation-id="formField-gender"><label for="gender">Gender<abbr title="required">*</abbr></label>'
                '<select id="gender" name="gender" data-automation-id="gender" required><option value="">Select One</option>'
                f"<option>Female</option><option>Male</option><option>{DECLINE}</option></select></div>"
                + _dropdown(
                    "ethnicityDropdown",
                    "Ethnicity",
                    ("Hispanic or Latino", "White", "Asian", DECLINE),
                )
                + _radios(
                    "veteranStatus",
                    "Veteran Status",
                    (
                        "I am a protected veteran",
                        "I am not a protected veteran",
                        "I do not wish to self-identify",
                    ),
                )
                + '<div data-automation-id="formField-agreementCheckbox"><label><input type="checkbox" name="agreement" value="yes"'
                ' data-automation-id="agreementCheckbox" required> I consent to the terms and conditions'
                '<abbr title="required">*</abbr></label></div>'
            )
        if step == 5:
            return _text("name", "Name", required=True) + _radios(
                "disabilityStatus",
                "Please check one of the boxes below",
                DISABILITY_OPTIONS,
            )
        return ""

    _REQUIRED = {
        1: (
            "sourceDropdown",
            "countryDropdown",
            "legalNameSection_firstName",
            "legalNameSection_lastName",
            "addressSection_city",
            "phone-device-type",
            "phone-number",
        ),
        2: ("jobTitle",),
        3: ("workAuthorization", "sponsorship"),
        4: ("gender", "ethnicityDropdown", "veteranStatus", "agreement"),
        5: ("name", "disabilityStatus"),
    }

    def _step_page(self, step: int, email: str, error: str = "") -> bytes:
        banner = (
            f'<div data-automation-id="errorBanner" role="alert">{html.escape(error)}</div>'
            if error
            else ""
        )
        if self.already_applied and step == 1:
            body = "<h2>You've already applied for this job.</h2><p>Check your candidate home for updates.</p>"
            return _page("Already applied", body)
        if step == 6:
            app = self.applications.get(email)
            summary = "".join(
                f"<li>{html.escape(name)}: {html.escape(', '.join(values))}</li>"
                for page in (app.pages.values() if app else ())
                for name, values in page.items()
            )
            content = (
                f"<h2>Review</h2><ul>{summary}</ul>"
                '<form method="post" action="/apply/submit">'
                '<button type="submit" data-automation-id="bottom-navigation-next-button">Submit</button></form>'
            )
        else:
            content = (
                f"<h2>{STEP_NAMES[step - 1]}</h2>"
                f'<form method="post" action="/apply/step/{step}" enctype="multipart/form-data" novalidate>'
                f"{self._step_fields(step, email)}"
                '<button type="submit" data-automation-id="bottom-navigation-next-button">Save and Continue</button>'
                "</form>"
            )
        body = f'{self._progress(step)}<div data-automation-id="applyFlowPage">{banner}{content}</div>'
        return _page(f"{STEP_NAMES[step - 1]} - {COMPANY}", body)

    # -- requests -------------------------------------------------------------

    def _session(self, handler: BaseHTTPRequestHandler) -> str:
        cookie = handler.headers.get("Cookie", "")
        for part in cookie.split(";"):
            name, _, value = part.strip().partition("=")
            if name == "wdsession" and value in self.sessions:
                return self.sessions[value]
        return ""

    def _login(self, email: str) -> dict[str, str]:
        token = secrets.token_hex(8)
        self.sessions[token] = email
        self.applications.setdefault(email, Application(email))
        return {
            "Set-Cookie": f"wdsession={token}; Path=/; HttpOnly",
            "Location": "/apply/step/1",
        }

    def _handle(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        path = urlsplit(handler.path).path
        with self._lock:
            self.requests.append((handler.command, path))
            status, data, headers = self._route(handler, path, body)
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        for name, value in headers.items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(data)

    def _route(
        self, handler: BaseHTTPRequestHandler, path: str, body: bytes
    ) -> tuple[int, bytes, dict[str, str]]:
        method = handler.command
        email = self._session(handler)
        if path == "/job" and method == "GET":
            return (
                200,
                _page(
                    f"Cloud Architect - {COMPANY}",
                    "<h1>Cloud Architect</h1><p>Austin, TX</p>"
                    '<button type="button" data-automation-id="adventureButton" '
                    "onclick=\"document.getElementById('applyMenu').hidden=false\">Apply</button>"
                    '<div id="applyMenu" hidden><a data-automation-id="applyManually" href="/apply">Apply Manually</a></div>',
                ),
                {},
            )
        if path == "/apply" and method == "GET":
            if email:
                return 303, b"", {"Location": "/apply/step/1"}
            return 200, self._auth_page(), {}
        if path in ("/auth/signin", "/auth/create") and method == "POST":
            fields, _ = _parse_body(handler.headers.get("Content-Type", ""), body)
            self.auth_posts.append(fields)
            given = (fields.get("email") or [""])[0]
            password = (fields.get("password") or [""])[0]
            if path == "/auth/signin":
                account = self.accounts.get(given)
                if account is None or account["password"] != password:
                    return 200, self._auth_page("Wrong email address or password."), {}
                if not account["verified"]:
                    return (
                        200,
                        _page(
                            "Verify",
                            "<h2>Verify your email</h2><p>We sent a verification email. Verify your account to continue.</p>",
                        ),
                        {},
                    )
                return 303, b"", self._login(given)
            if given in self.accounts:
                return (
                    200,
                    self._auth_page(
                        "An account with this email already exists.", mode="create"
                    ),
                    {},
                )
            if password != (fields.get("verifyPassword") or [""])[0]:
                return (
                    200,
                    self._auth_page("The passwords do not match.", mode="create"),
                    {},
                )
            if not fields.get("terms"):
                return (
                    200,
                    self._auth_page(
                        "Accept the terms to create an account.", mode="create"
                    ),
                    {},
                )
            self.accounts[given] = {
                "password": password,
                "verified": not self.verify_on_create,
            }
            if self.verify_on_create:
                return (
                    200,
                    _page(
                        "Verify",
                        "<h2>Account created</h2><p>We sent a verification email to you. Verify your email to sign in.</p>",
                    ),
                    {},
                )
            return 303, b"", self._login(given)
        if path.startswith("/apply/step/"):
            if not email:
                return 303, b"", {"Location": "/apply"}
            try:
                step = int(path.rsplit("/", 1)[1])
            except ValueError:
                return 404, _page("Not found", "<h1>Not found</h1>"), {}
            if not 1 <= step <= 6:
                return 404, _page("Not found", "<h1>Not found</h1>"), {}
            if method == "GET":
                return 200, self._step_page(step, email), {}
            fields, files = _parse_body(handler.headers.get("Content-Type", ""), body)
            missing = [
                name
                for name in self._REQUIRED.get(step, ())
                if not (fields.get(name) or [""])[0].strip()
            ]
            if (
                step == 2
                and "resume" not in files
                and not self.applications[email].files
            ):
                missing.append("resume")
            if (
                step == 3
                and self.extra_question
                and not (fields.get("products") or [""])[0].strip()
            ):
                missing.append("products")
            if missing:
                return (
                    200,
                    self._step_page(
                        step, email, f"Errors found: {', '.join(missing)} is required."
                    ),
                    {},
                )
            application = self.applications[email]
            application.pages[step] = fields
            application.files.update(files)
            return 303, b"", {"Location": f"/apply/step/{step + 1}"}
        if path == "/apply/submit" and method == "POST" and email:
            application = self.applications[email]
            self.submissions.append(application)
            reference = f"WD-{10000 + len(self.submissions)}"
            return (
                200,
                _page(
                    "Application Submitted",
                    f"<h2>Application Submitted</h2><p>Thanks for applying to {COMPANY}! "
                    f"Your reference number is {reference}.</p>",
                ),
                {},
            )
        return 404, _page("Not found", "<h1>Not found</h1>"), {}
