"""Check translations against the catalog: every string present, placeholders kept, the right script used.

    uv run python scripts/i18n_check.py te                  # auto_apply_assistant/i18n/te.json vs catalog.json
    uv run python scripts/i18n_check.py te work/chunk-3.json work/te-3.json   # one chunk (paths under i18n/)

Exit code 1 lists the problems. A string that is not words (an identifier, a code) may be kept as it is.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

I18N = Path(__file__).resolve().parent.parent / "auto_apply_assistant" / "i18n"
SCRIPTS = {"te": r"[ఀ-౿]", "hi": r"[ऀ-ॿ]"}
PLACEHOLDER = re.compile(r"\{\d+\}")


def problems(lang: str, sources: list[str], translations: dict[str, str]) -> list[str]:
    script = re.compile(SCRIPTS[lang])
    out = []
    for source in sources:
        if source not in translations:
            out.append(f"missing: {source!r}")
            continue
        target = translations[source]
        if not isinstance(target, str) or not target.strip():
            out.append(f"empty: {source!r}")
            continue
        if sorted(PLACEHOLDER.findall(source)) != sorted(PLACEHOLDER.findall(target)):
            out.append(f"placeholders differ: {source!r} -> {target!r}")
        words = re.sub(r"\{\d+\}", "", source)
        wordy = len(re.findall(r"[A-Za-z]{3,}", words)) >= 1 and not re.fullmatch(
            r"[A-Z0-9 .,:/()+-]+", words.strip()
        )
        if wordy and target != source and not script.search(target):
            out.append(f"not in the {lang} script: {source!r} -> {target!r}")
    extra = set(translations) - set(sources)
    if extra:
        out.append(
            f"{len(extra)} entries are not in the source list (first: {sorted(extra)[0]!r})"
        )
    return out


def main(argv: list[str]) -> int:
    if not argv or argv[0] not in SCRIPTS:
        print(__doc__)
        return 2
    lang = argv[0]
    if len(argv) >= 3:
        sources = json.loads((I18N / argv[1]).read_text(encoding="utf-8"))
        translations = json.loads((I18N / argv[2]).read_text(encoding="utf-8"))
    else:
        sources = json.loads((I18N / "catalog.json").read_text(encoding="utf-8"))[
            "strings"
        ]
        translations = json.loads((I18N / f"{lang}.json").read_text(encoding="utf-8"))
    found = problems(lang, sources, translations)
    for line in found[:50]:
        print(line)
    if found:
        print(f"{len(found)} problems")
        return 1
    print(f"{len(sources)} strings OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
