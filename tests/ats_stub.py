"""A stand-in applicant tracking system for form-engine tests (never talks to the network).

Start it and point ``aira.apply_engine.run`` at one of its forms::

    stub = AtsStub()
    stub.start()
    request = ApplyRequest(..., source="greenhouse", application_url=stub.form_url("greenhouse"))
    outcome = run(request)
    assert stub.submissions[-1].fields["job_application[first_name]"] == ["Hyma"]
    stub.stop()

Pages are served from ``tests/fixtures/ats_forms/<ats>.html`` and are modelled on
the real application forms of each provider:

===================  =============================================================
``greenhouse``       boards.greenhouse.io: ``#application_form``, ``job_application[...]``
                     names, ``#resume`` file input with ``data-field``, paste
                     textareas, ``question_<id>`` custom questions (select, radio
                     group, checkbox, textarea) and the EEOC block.
``lever``            jobs.lever.co/<company>/<id>/apply: ``.application-label``
                     divs (no ``<label>``), ``name``, ``email``, ``phone``, ``org``,
                     ``urls[LinkedIn]``, ``resume``, ``comments``, ``cards[<id>][fieldN]``
                     custom questions and ``eeo[...]`` selects.
``ashby``            jobs.ashbyhq.com React form: ``_systemfield_*`` ids, labelled
                     inputs with UUID ids, a hidden-style file input, a
                     ``role=radiogroup`` and custom questions.
``smartrecruiters``  a single-page approximation of the SmartRecruiters apply
                     form (``firstName``, ``lastName``, ``email``, ``phoneNumber``,
                     ``resume``, privacy consent).
``generic``          a plain careers-site form with ``<label for>``, a
                     ``<fieldset><legend>`` radio group and a date input.
===================  =============================================================

Routes: ``GET /<ats>/apply`` (the form), ``POST /<ats>/submit`` (records the
posted fields and files, then answers a confirmation page or redirects to one),
``GET /<ats>/confirmation`` and ``GET /<ats>/thanks`` (confirmation pages reached
by redirect), ``GET /captcha-frame`` (what the CAPTCHA placeholder embeds).

``?variant=`` on the form URL changes the page; the form carries it to
``/submit`` in a hidden field:

===================  =============================================================
``applied``          the form is replaced by the provider's "already applied" notice
``unknown_required`` adds a required custom question Aira cannot map
                     (``UNKNOWN_QUESTION``; field name in ``UNKNOWN_FIELD[ats]``)
``sensitive``        adds a required sponsorship question (``SENSITIVE_QUESTION``;
                     field name in ``SENSITIVE_FIELD[ats]``) that may only be
                     answered from a confirmed standard answer
``fail_submit``      ``/submit`` answers HTTP 500
``no_marker``        ``/submit`` answers 200 with a neutral page and no success wording
``captcha``          a visible reCAPTCHA-style placeholder sits above the submit button
===================  =============================================================

``stub.submissions`` holds one :class:`Submission` per accepted POST (fields as
``{name: [values]}``, files as ``{name: UploadedFile}`` with the raw bytes) and
``stub.requests`` every ``(method, path)`` seen. ``stub.reset()`` clears both.
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "ats_forms"
ATS_NAMES = ("greenhouse", "lever", "ashby", "smartrecruiters", "generic")
VARIANTS = (
    "",
    "applied",
    "unknown_required",
    "sensitive",
    "fail_submit",
    "no_marker",
    "captcha",
)
UNKNOWN_QUESTION = "Which of our products have you used, and what would you improve?"
SENSITIVE_QUESTION = (
    "Will you now or in the future require sponsorship for employment visa status?"
)
_LEVER_CARD = "cards[9a1f3c5e-7b2d-4e8f-a1c3-5d7e9f1b3a5c]"
UNKNOWN_FIELD = {
    "greenhouse": "question_9001",
    "lever": f"{_LEVER_CARD}[field2]",
    "ashby": "f5a6b7c8-d9e0-4f1a-2b3c-4d5e6f7a8b9c",
    "smartrecruiters": "productFeedback",
    "generic": "product_feedback",
}
SENSITIVE_FIELD = {
    "greenhouse": "question_9002",
    "lever": f"{_LEVER_CARD}[field3]",
    "ashby": "a6b7c8d9-e0f1-4a2b-3c4d-5e6f7a8b9c0d",
    "smartrecruiters": "visaSponsorship",
    "generic": "sponsorship",
}
REFERENCE_PREFIX = {
    "greenhouse": "GH",
    "lever": "LV",
    "ashby": "AS",
    "smartrecruiters": "SR",
    "generic": "GEN",
}
COMPANY = "Example Corp"

_CAPTCHA_HTML = (
    '<div class="g-recaptcha" data-sitekey="stub-site-key" style="margin:16px 0">'
    '<iframe title="reCAPTCHA" src="/captcha-frame" width="304" height="78"'
    ' role="presentation" style="border:0"></iframe></div>'
)
_CAPTCHA_FRAME = (
    '<!doctype html><html><body style="font-family:Arial;border:1px solid #c1c1c1;'
    'margin:0;padding:12px"><label><input type="checkbox" disabled> I\'m not a robot'
    "</label></body></html>"
)

_ALREADY_APPLIED = {
    "greenhouse": (
        '<div class="already-applied"><p>You\'ve already applied to this job. '
        "We'll be in touch if your qualifications match the role.</p></div>"
    ),
    "lever": (
        '<div class="already-applied"><h4>You have already applied for this '
        "position.</h4><p>Our team will review your application.</p></div>"
    ),
    "ashby": (
        '<div class="ashby-already-applied">You\'ve already applied to this job. '
        "You can't submit another application.</div>"
    ),
    "smartrecruiters": (
        '<div class="already-applied"><p>You have already applied to this job. '
        "Check your email for updates.</p></div>"
    ),
    "generic": (
        '<p class="notice">Our records show you have already applied for this '
        "role. Thank you for your interest.</p>"
    ),
}

# Markup for the extra questions, in each provider's own style.
_TEXTAREA = {
    "greenhouse": (
        '<div class="field"><label for="{name}">{label}<span class="asterisk">*'
        '</span></label><textarea id="{name}" name="{name}" required></textarea></div>'
    ),
    "lever": (
        '<li class="application-question custom-question"><div class="application-label">'
        '{label} <span class="required">✱</span></div><div class="application-field">'
        '<textarea name="{name}" required></textarea></div></li>'
    ),
    "ashby": (
        '<div class="ashby-application-form-field-entry"><label '
        'class="ashby-application-form-question-title" for="{name}">{label} '
        '<span class="ashby-application-form-required-indicator">*</span></label>'
        '<textarea id="{name}" name="{name}" required></textarea></div>'
    ),
    "smartrecruiters": (
        '<div class="st-field"><label for="{name}">{label} *</label>'
        '<textarea id="{name}" name="{name}" required></textarea></div>'
    ),
    "generic": (
        '<p><label for="{name}">{label} *</label>'
        '<textarea id="{name}" name="{name}" required></textarea></p>'
    ),
}
_YES_NO = {
    "greenhouse": (
        '<div class="field"><label>{label}<span class="asterisk">*</span></label>'
        '<div class="radio-group"><input type="radio" id="{name}_0" name="{name}" '
        'value="1" required><label for="{name}_0">Yes</label>'
        '<input type="radio" id="{name}_1" name="{name}" value="0" required>'
        '<label for="{name}_1">No</label></div></div>'
    ),
    "lever": (
        '<li class="application-question custom-question"><div class="application-label">'
        '{label} <span class="required">✱</span></div><div class="application-field">'
        '<select name="{name}" required><option value="">Select...</option>'
        '<option value="Yes">Yes</option><option value="No">No</option></select></div></li>'
    ),
    "ashby": (
        '<div class="ashby-application-form-field-entry"><label '
        'class="ashby-application-form-question-title" for="{name}">{label} '
        '<span class="ashby-application-form-required-indicator">*</span></label>'
        '<select id="{name}" name="{name}" required><option value="">Select...</option>'
        "<option>Yes</option><option>No</option></select></div>"
    ),
    "smartrecruiters": (
        '<div class="st-field"><label for="{name}">{label} *</label>'
        '<select id="{name}" name="{name}" required><option value="">Select</option>'
        '<option value="true">Yes</option><option value="false">No</option></select></div>'
    ),
    "generic": (
        "<fieldset><legend>{label} *</legend>"
        '<label><input type="radio" name="{name}" value="yes" required> Yes</label>'
        '<label><input type="radio" name="{name}" value="no"> No</label></fieldset>'
    ),
}

_CONFIRMATION = {
    "greenhouse": (
        "<h1>Thank you for applying to {company}!</h1><p>Your application has been "
        "received. Reference number: <strong>{ref}</strong>.</p>"
    ),
    "lever": (
        "<h3>Application submitted</h3><p>Thank you for your application. "
        "We'll be in touch. Your confirmation ID is {ref}.</p>"
    ),
    "ashby": (
        "<h2>Your application has been submitted!</h2><p>Thanks for applying to "
        "{company}. Confirmation #{ref}.</p>"
    ),
    "smartrecruiters": (
        "<h1>Thank you for applying!</h1><p>We received your application. "
        "Application ID: {ref}</p>"
    ),
    "generic": (
        "<h1>Thanks!</h1><p>We have received your application. Confirmation "
        "number: {ref}.</p>"
    ),
}
_REDIRECT_PATH = {"greenhouse": "confirmation", "lever": "thanks"}


@dataclass(slots=True)
class UploadedFile:
    """One file part of a multipart POST."""

    field: str
    filename: str
    data: bytes

    @property
    def size(self) -> int:
        return len(self.data)

    @property
    def head(self) -> bytes:
        return self.data[:8]


@dataclass(slots=True)
class Submission:
    """One accepted application POST."""

    ats: str
    variant: str
    reference: str
    fields: dict[str, list[str]] = field(default_factory=dict)
    files: dict[str, UploadedFile] = field(default_factory=dict)

    def value(self, name: str) -> str:
        """The single value of a field (``""`` when absent)."""
        values = self.fields.get(name) or [""]
        return values[0]


def _parse_body(
    content_type: str, body: bytes
) -> tuple[dict[str, list[str]], dict[str, UploadedFile]]:
    fields: dict[str, list[str]] = {}
    files: dict[str, UploadedFile] = {}
    if content_type.startswith("multipart/form-data"):
        match = re.search(r'boundary="?([^";]+)"?', content_type)
        if match is None:
            return fields, files
        boundary = b"--" + match.group(1).encode("utf-8")
        for chunk in body.split(boundary)[1:]:
            if chunk.startswith(b"--"):
                break  # closing boundary
            part = chunk[2:] if chunk.startswith(b"\r\n") else chunk
            split = part.find(b"\r\n\r\n")
            if split < 0:
                continue
            headers = part[:split].decode("utf-8", "replace")
            content = part[split + 4 :]
            if content.endswith(b"\r\n"):
                content = content[:-2]
            name_match = re.search(r'name="([^"]*)"', headers)
            if name_match is None:
                continue
            name = name_match.group(1)
            filename_match = re.search(r'filename="([^"]*)"', headers)
            if filename_match is not None:
                if filename_match.group(1):
                    files[name] = UploadedFile(name, filename_match.group(1), content)
                continue
            fields.setdefault(name, []).append(content.decode("utf-8", "replace"))
    elif content_type.startswith("application/x-www-form-urlencoded"):
        fields = parse_qs(body.decode("utf-8", "replace"), keep_blank_values=True)
    return fields, files


def _page(title: str, body: str) -> bytes:
    return (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><title>'
        f'{title}</title></head><body style="font-family:Arial;padding:32px">'
        f"{body}</body></html>"
    ).encode("utf-8")


class AtsStub:
    def __init__(self) -> None:
        self.submissions: list[Submission] = []
        self.requests: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._server: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None
        self.base_url = ""
        self._pages = {
            name: (FIXTURE_DIR / f"{name}.html").read_text(encoding="utf-8")
            for name in ATS_NAMES
        }

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> AtsStub:
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
        self._thread = threading.Thread(
            target=lambda: server.serve_forever(poll_interval=0.05), daemon=True
        )
        self._thread.start()
        return self

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

    def __enter__(self) -> AtsStub:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.stop()

    # -- inspection -----------------------------------------------------------

    def form_url(self, ats: str, variant: str = "") -> str:
        if ats not in ATS_NAMES:
            raise ValueError(f"Unknown ATS {ats!r}; expected one of {ATS_NAMES}.")
        if variant not in VARIANTS:
            raise ValueError(
                f"Unknown variant {variant!r}; expected one of {VARIANTS}."
            )
        suffix = f"?variant={variant}" if variant else ""
        return f"{self.base_url}/{ats}/apply{suffix}"

    def reset(self) -> None:
        with self._lock:
            self.submissions.clear()
            self.requests.clear()

    # -- pages ----------------------------------------------------------------

    def form_page(self, ats: str, variant: str) -> str:
        """The form HTML for ``ats`` with the variant applied."""
        html = self._pages[ats]
        if variant == "applied":
            return re.sub(
                r"<form\b.*?</form>", _ALREADY_APPLIED[ats], html, count=1, flags=re.S
            )
        extra = ""
        if variant == "unknown_required":
            extra = _TEXTAREA[ats].format(
                name=UNKNOWN_FIELD[ats], label=UNKNOWN_QUESTION
            )
        elif variant == "sensitive":
            extra = _YES_NO[ats].format(
                name=SENSITIVE_FIELD[ats], label=SENSITIVE_QUESTION
            )
        html = html.replace("<!-- stub:extra-questions -->", extra)
        html = html.replace(
            "<!-- stub:captcha -->", _CAPTCHA_HTML if variant == "captcha" else ""
        )
        hidden = f'<input type="hidden" name="variant" value="{variant}">'
        return re.sub(r"(<form\b[^>]*>)", lambda m: m.group(1) + hidden, html, count=1)

    def confirmation_page(self, ats: str, reference: str) -> bytes:
        body = _CONFIRMATION[ats].format(company=COMPANY, ref=reference)
        return _page(f"Application received - {COMPANY}", body)

    # -- request handling ---------------------------------------------------

    def _handle(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        parts = urlsplit(handler.path)
        query = {name: values[0] for name, values in parse_qs(parts.query).items()}
        with self._lock:
            self.requests.append((handler.command, parts.path))
        segments = [segment for segment in parts.path.split("/") if segment]
        status, data, headers = 404, _page("Not found", "<h1>Not found</h1>"), {}
        if parts.path == "/captcha-frame":
            status, data = 200, _CAPTCHA_FRAME.encode("utf-8")
        elif parts.path in ("/blocked", "/rate-limited"):
            # A site that refuses automated visitors, as job boards with bot protection do
            status = 403 if parts.path == "/blocked" else 429
            data = _page("Access denied", "<h1>Access denied</h1>")
        elif len(segments) == 2 and segments[0] in ATS_NAMES:
            ats, action = segments
            if action == "apply" and handler.command == "GET":
                variant = query.get("variant", "")
                if variant in VARIANTS:
                    status = 200
                    data = self.form_page(ats, variant).encode("utf-8")
            elif action in ("confirmation", "thanks") and handler.command == "GET":
                status, data = 200, self.confirmation_page(ats, query.get("ref", ""))
            elif action == "submit" and handler.command == "POST":
                status, data, headers = self._submit(ats, handler, body)
        handler.send_response(status)
        handler.send_header("Content-Type", "text/html; charset=utf-8")
        handler.send_header("Content-Length", str(len(data)))
        for name, value in headers.items():
            handler.send_header(name, value)
        handler.end_headers()
        handler.wfile.write(data)

    def _submit(
        self, ats: str, handler: BaseHTTPRequestHandler, body: bytes
    ) -> tuple[int, bytes, dict[str, str]]:
        fields, files = _parse_body(handler.headers.get("Content-Type", ""), body)
        variant = (fields.pop("variant", None) or [""])[0]
        if variant == "fail_submit":
            return (
                500,
                _page(
                    "Error",
                    "<h1>Something went wrong</h1><p>Our servers hit a snag. "
                    "Please try again later.</p>",
                ),
                {},
            )
        with self._lock:
            reference = f"{REFERENCE_PREFIX[ats]}-{10001 + len(self.submissions)}"
            self.submissions.append(Submission(ats, variant, reference, fields, files))
        if variant == "no_marker":
            return (
                200,
                _page(
                    "Processing",
                    "<h1>Processing</h1><p>Please wait while we process your "
                    "request.</p>",
                ),
                {},
            )
        redirect = _REDIRECT_PATH.get(ats)
        if redirect:
            return 303, b"", {"Location": f"/{ats}/{redirect}?ref={reference}"}
        return 200, self.confirmation_page(ats, reference), {}
