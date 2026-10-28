"""The screens in English, Telugu and Hindi (aira/i18n.py, auto_apply_assistant/i18n.js, i18n/*.json)."""

# ruff: noqa: F811  (the imported fixtures are used by name as test arguments)

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from aira import i18n

ROOT = Path(__file__).resolve().parent.parent
I18N = ROOT / "auto_apply_assistant" / "i18n"
PLACEHOLDER = re.compile(r"\{\d+\}")


def menu_text(page, href: str) -> str:
    """The text of a profile-menu link (the account's pages live there, not in the left menu)."""
    page.click("#accountBtn")
    page.wait_for_selector("#accountMenu:not([hidden])")
    text = page.locator(f"#accountMenu a[href='{href}']").inner_text().strip()
    page.keyboard.press("Escape")
    return text


def test_languages_and_normalizing():
    assert i18n.CODES == ("en", "te", "hi")
    assert i18n.normalize("te-IN") == "te" and i18n.normalize("HI") == "hi"
    assert i18n.normalize("fr") == "en" and i18n.normalize(None) == "en"
    assert i18n.BY_CODE["te"].native == "తెలుగు" and i18n.BY_CODE["hi"].native == "हिन्दी"


def test_the_catalog_is_up_to_date_with_the_code():
    result = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "i18n_extract.py"), "--check"],
        capture_output=True,
        text=True,
        cwd=ROOT,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("lang", ["te", "hi"])
def test_every_visible_string_is_translated_and_keeps_its_placeholders(lang):
    catalog = json.loads((I18N / "catalog.json").read_text(encoding="utf-8"))["strings"]
    path = I18N / f"{lang}.json"
    if not path.exists():
        pytest.skip(f"{path.name} not merged yet")
    table = json.loads(path.read_text(encoding="utf-8"))
    missing = [s for s in catalog if s not in table]
    assert len(missing) <= len(catalog) // 100, missing[
        :20
    ]  # at most 1% waiting for translation
    script = re.compile({"te": r"[ఀ-౿]", "hi": r"[ऀ-ॿ]"}[lang])
    for source, target in table.items():
        assert sorted(PLACEHOLDER.findall(source)) == sorted(
            PLACEHOLDER.findall(target)
        ), (source, target)
    translated = sum(1 for target in table.values() if script.search(target))
    assert translated >= len(table) * 0.85
    for word in ("Today", "Family", "Health", "Save", "Cancel", "Sign in", "Settings"):
        assert script.search(table.get(word, "")), word


def test_a_person_keeps_their_language(tmp_path):
    import sqlite3

    from aira.suite import kit, life

    kit.forget_schema()
    connection = sqlite3.connect(tmp_path / "x.sqlite3")
    connection.row_factory = sqlite3.Row

    class Req:
        user = {"email": "pat@home.example"}

        def header(self, name, default=""):
            return default

    me = life.current(Req(), connection)
    assert life.save_prefs(connection, me, {"language": "te"})["language"] == "te"
    with pytest.raises(ValueError):
        life.save_prefs(connection, me, {"language": "xx"})
    assert life.current(Req(), connection)["prefs"]["language"] == "te"


# ---------------------------------------------------------------------------
# the browser
# ---------------------------------------------------------------------------

patchright = pytest.importorskip("patchright.sync_api")
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, signed_in_page  # noqa: E402, F401

SAMPLE = {
    "Today": "ఈరోజు",
    "Family": "కుటుంబం",
    "Display and reading": "ప్రదర్శన మరియు చదవడం",
    "{0} cards to practise": "సాధన చేయడానికి {0} కార్డులు",
    "paid": "చెల్లించారు",
    "Status: {0}": "స్థితి: {0}",
    "Good morning": "శుభోదయం",
    "{0} of {1}": "{1}లో {0}",
    "{0} application{1} sent today": "ఈరోజు {0} దరఖాస్తులు{1} పంపారు",
}


def test_the_screens_follow_the_chosen_language(browser, env):
    context, page, problems = signed_in_page(browser, env)
    page.route(
        "**/static/i18n/te.json",
        lambda route: route.fulfill(
            status=200,
            content_type="application/json",
            body=json.dumps(SAMPLE, ensure_ascii=False),
        ),
    )
    page.goto(env.base + "/#/today")
    page.wait_for_selector(".life-hero h1", timeout=20000)
    assert menu_text(page, "#/today") == "Today"
    page.click("#langBtn")
    page.click(".lang-menu button[lang='te']")
    page.wait_for_function("document.documentElement.lang === 'te'")
    assert menu_text(page, "#/today") == "ఈరోజు"
    assert page.locator("#langBtn .lang-name").inner_text() == "తెలుగు"
    # patterns put the number (and a known word) into the translation; unknown text stays as written
    assert (
        page.evaluate("Aira.i18n.t('12 cards to practise')", isolated_context=False)
        == "సాధన చేయడానికి 12 కార్డులు"
    )
    assert (
        page.evaluate("Aira.i18n.t('Status: paid')", isolated_context=False)
        == "స్థితి: చెల్లించారు"
    )
    assert (
        page.evaluate("Aira.i18n.t('Mia sent a photo')", isolated_context=False)
        == "Mia sent a photo"
    )
    # a pattern with little fixed text only applies to numbers, never to an ordinary sentence
    assert page.evaluate("Aira.i18n.t('3 of 10')", isolated_context=False) == "10లో 3"
    assert (
        page.evaluate("Aira.i18n.t('Out of stock')", isolated_context=False)
        == "Out of stock"
    )
    # an English plural ending is not carried into the translation
    assert (
        page.evaluate(
            "Aira.i18n.t('3 applications sent today')", isolated_context=False
        )
        == "ఈరోజు 3 దరఖాస్తులు పంపారు"
    )
    # new content is translated as it appears; translate="no" is left alone
    page.evaluate(
        """() => { const box = document.createElement('div'); box.id = 'probe';
        box.innerHTML = '<span id=a>Family</span><span id=b translate=no>Family</span><input id=c placeholder="Today">';
        document.getElementById('main').append(box); }"""
    )
    page.wait_for_function("document.getElementById('a').textContent === 'కుటుంబం'")
    assert page.locator("#b").inner_text() == "Family"
    assert page.locator("#c").get_attribute("placeholder") == "ఈరోజు"
    # dates and numbers use the language too
    assert re.search(
        r"[ఀ-౿]",
        page.evaluate(
            "new Date(2026, 9, 25).toLocaleDateString(undefined, {month: 'long'})",
            isolated_context=False,
        ),
    )
    # the choice is kept on this browser and with the person's profile
    page.reload()
    page.wait_for_function("document.documentElement.lang === 'te'")
    me = context.request.get(env.base + "/api/life/me").json()
    assert me["profile"]["prefs"]["language"] == "te"
    # back to English restores the original text
    page.click("#langBtn")
    page.click(".lang-menu button[lang='en']")
    page.wait_for_function("document.documentElement.lang === 'en'")
    assert menu_text(page, "#/today") == "Today"
    assert not [p for p in problems if "status of 401" not in p], problems
    context.close()
