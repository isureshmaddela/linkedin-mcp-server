"""Merge translated chunks (auto_apply_assistant/i18n/work/<lang>-*.json) into i18n/<lang>.json.

    uv run python scripts/i18n_merge.py te hi

Existing translations are kept, chunks win where both have a string, and only strings that are still in the
catalog are written. Prints how many catalog strings are still missing (scripts/i18n_check.py lists them).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

I18N = Path(__file__).resolve().parent.parent / "auto_apply_assistant" / "i18n"


def merge(lang: str) -> int:
    catalog = json.loads((I18N / "catalog.json").read_text(encoding="utf-8"))["strings"]
    wanted = set(catalog)
    target = I18N / f"{lang}.json"
    table: dict[str, str] = (
        json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    )
    for part in sorted((I18N / "work").glob(f"{lang}-*.json")):
        table.update(json.loads(part.read_text(encoding="utf-8")))
    kept = {source: table[source] for source in sorted(table) if source in wanted}
    target.write_text(
        json.dumps(kept, ensure_ascii=False, indent=0, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    missing = [source for source in catalog if source not in kept]
    print(f"{lang}: {len(kept)} translated, {len(missing)} missing")
    return len(missing)


if __name__ == "__main__":
    for code in sys.argv[1:] or ["te", "hi"]:
        merge(code)
