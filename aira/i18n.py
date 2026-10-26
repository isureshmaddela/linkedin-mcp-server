"""Languages the app is offered in.

The screens are translated in the browser (``auto_apply_assistant/i18n.js``) from one catalog of English source
strings (``auto_apply_assistant/i18n/catalog.json``, made by ``scripts/i18n_extract.py``) and one file per
language (``auto_apply_assistant/i18n/<code>.json``: English source -> translation; ``{0}``, ``{1}`` stand for
numbers or names). Adding a language is adding its file and a line here.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Language:
    code: str  # BCP 47 language (the file name and <html lang>)
    name: str  # in English
    native: str  # in the language itself (what the picker shows)
    locale: str  # dates and numbers
    direction: str = "ltr"


LANGUAGES: tuple[Language, ...] = (
    Language("en", "English", "English", "en-IN"),
    Language("te", "Telugu", "తెలుగు", "te-IN"),
    Language("hi", "Hindi", "हिन्दी", "hi-IN"),
)
BY_CODE = {language.code: language for language in LANGUAGES}
CODES = tuple(BY_CODE)
DEFAULT = "en"


def normalize(code: object) -> str:
    """A supported language code for ``code`` (``"te-IN"`` -> ``"te"``), or the default."""
    text = str(code or "").strip().lower().replace("_", "-")
    if text in BY_CODE:
        return text
    base = text.split("-", 1)[0]
    return base if base in BY_CODE else DEFAULT
