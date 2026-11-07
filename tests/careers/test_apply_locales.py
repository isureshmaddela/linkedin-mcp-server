"""Confirmation and "already applied" wording in seven languages (aira.apply_engine.CONFIRMATION_PHRASES).

The wording is the only signal for those states, so it lives in one explicit per-language table. These tests
check the table itself, the text folding it relies on, and real engine runs in headless Chromium against the
stand-in ATS answering in Spanish and German.
"""

from __future__ import annotations

import pytest
from ats_stub import REFERENCE_PREFIX, AtsStub
from test_apply_engine import browser, request, resume  # noqa: F401

from aira import apply_engine
from aira.apply_engine import (
    CONFIRMATION_PHRASES,
    _phrase_text,
    confirmation_language,
    run,
)

LANGUAGES = ("en", "es", "fr", "de", "pt", "it", "nl")
SAMPLES = {
    "en": (
        "Thank you for applying! We'll be in touch.",
        "You have already submitted an application for this role.",
    ),
    "es": (
        "¡Gracias por tu solicitud! Te contactaremos pronto.",
        "Ya te has postulado a esta oferta.",
    ),
    "fr": (
        "Merci d’avoir postulé. Nous revenons vers vous.",
        "Vous avez déjà postulé à cette offre.",
    ),
    "de": (
        "Vielen Dank für Ihre Bewerbung.",
        "Sie haben sich bereits auf diese Stelle beworben.",
    ),
    "pt": ("Obrigado por se candidatar!", "Você já se candidatou a esta vaga."),
    "it": (
        "Grazie per la tua candidatura.",
        "Ti sei già candidato per questa posizione.",
    ),
    "nl": (
        "Bedankt voor je sollicitatie!",
        "Je hebt al gesolliciteerd op deze vacature.",
    ),
}


def test_every_language_has_both_kinds_of_wording_in_folded_form():
    assert set(CONFIRMATION_PHRASES) == set(LANGUAGES)
    for language, table in CONFIRMATION_PHRASES.items():
        assert table.success and table.already_applied, language
        for phrase in (*table.success, *table.already_applied):
            # written the way page text is folded, or it could never match
            assert phrase == _phrase_text(phrase), (language, phrase)
    # the engine matches every language at once
    assert set(apply_engine._SUCCESS_PHRASES) >= set(CONFIRMATION_PHRASES["de"].success)
    assert set(apply_engine._ALREADY_APPLIED_PHRASES) >= set(
        CONFIRMATION_PHRASES["fr"].already_applied
    )


@pytest.mark.parametrize("language", LANGUAGES)
def test_each_language_is_recognized(language):
    success, already = SAMPLES[language]
    assert confirmation_language(success) == (language, "success")
    assert confirmation_language(already) == (language, "already_applied")


def test_folding_handles_curly_apostrophes_case_and_odd_spaces():
    assert _phrase_text("Merci D’avoir POSTULÉ") == "merci d'avoir postulé"
    assert confirmation_language("THANK YOU FOR APPLYING") == ("en", "success")


@pytest.mark.parametrize(
    "text",
    [
        "Haben Sie sich bereits beworben? Melden Sie sich an.",  # a sign-in prompt, not a notice
        "¿Ya tienes una cuenta? Inicia sesión para postularte.",
        "Bewerben Sie sich jetzt für diese Stelle.",
        "Envía tu solicitud antes del viernes.",
        "Postulez dès maintenant.",
    ],
)
def test_prompts_and_calls_to_action_are_not_confirmations(text):
    assert confirmation_language(text) is None


# --------------------------------------------------------------------------- the engine, in a real browser


@pytest.fixture(scope="module")
def stub():
    server = AtsStub().start()
    yield server
    server.stop()


@pytest.mark.parametrize(
    ("ats", "variant"), [("generic", "es_marker"), ("lever", "de_marker")]
)
def test_a_confirmation_in_another_language_counts_as_submitted(
    stub,
    browser,  # noqa: F811
    resume,  # noqa: F811
    ats,
    variant,
):
    stub.reset()
    outcome = run(request(stub, resume, ats, variant=variant), browser=browser)
    assert outcome.status == "submitted", (outcome.error, outcome.steps)
    assert len(stub.submissions) == 1
    assert outcome.evidence.confirmation_number.startswith(REFERENCE_PREFIX[ats])


def test_a_german_already_applied_notice_stops_before_filling(
    stub,
    browser,  # noqa: F811
    resume,  # noqa: F811
):
    stub.reset()
    outcome = run(
        request(stub, resume, "greenhouse", variant="de_applied"), browser=browser
    )
    assert outcome.status == "already_applied", outcome.steps
    assert stub.submissions == []
