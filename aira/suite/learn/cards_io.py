"""Cards in and out of a deck as plain text: paste a list (or a spreadsheet's CSV) to add many cards at once, and
download a deck as CSV (front, back, hint, example) to keep, print or move to another flashcard app.

A pasted list is one card per line, the front and back split by a tab, " = ", " - ", a semicolon or a comma,
optionally followed by a hint and an example. The separator most lines use is read first (on a tie, the earlier in
that list); a line without it is read with whichever of the others it has, so a list may mix them. With a tab,
semicolon or comma the text is read as CSV, so a quoted cell may hold commas, quotes and several lines (the deck
download reads back in unchanged). A first line "front, back" is a header and is skipped. Cards whose front is
already in the deck are skipped, so pasting the same list twice adds nothing.
"""

from __future__ import annotations

import csv
import sqlite3
from collections.abc import Iterator
from typing import Any

from aira.suite import kit
from aira.suite.learn import store

MAX_LINES = 500
MAX_TEXT = 200_000
NL = "\n"
TAB = "\t"
SEPARATORS = (TAB, " = ", " - ", ";", ",")  # on a tie, the earlier one wins
FALLBACKS = (
    *SEPARATORS,
    "=",
)  # for a line without the list's separator ("Spain=Madrid" too)
CSV_SEPARATORS = (TAB, ";", ",")
COLUMNS = ("front", "back", "hint", "example")


def _separator(lines: list[str]) -> str:
    best, best_count = TAB, 0
    for separator in SEPARATORS:
        count = sum(1 for line in lines if separator in line)
        if count > best_count:
            best, best_count = separator, count
    return best


def _unguard(cell: str) -> str:
    """Undo the apostrophe a CSV export puts before a cell that looks like a formula (``'-5`` -> ``-5``)."""
    return cell[1:] if len(cell) > 1 and cell[0] == "'" and cell[1] in "=+-@" else cell


def _split(line: str, separator: str) -> list[str]:
    if separator in CSV_SEPARATORS:
        return next(csv.reader([line], delimiter=separator), [])
    parts = line.split(separator)
    return [parts[0], separator.join(parts[1:])] if len(parts) > 2 else parts


def _records(text: str, separator: str) -> Iterator[tuple[int, str, list[str]]]:
    """``(first line number, the record's text, its cells)`` for each card in the text, blank lines left out."""
    lines = text.split(NL)
    if separator not in CSV_SEPARATORS or '"' not in text:
        for index, line in enumerate(lines):
            if line.strip():
                yield index + 1, line, _split(line, separator)
        return
    # CSV: a quoted cell may run over several lines, so the reader gets the lines with their line ends.
    ended = [line + NL for line in lines[:-1]] + lines[-1:]
    reader = csv.reader(ended, delimiter=separator)
    first = 1
    for cells in reader:
        last = reader.line_num
        raw = NL.join(lines[first - 1 : last])
        if raw.strip():
            yield first, raw, cells
        first = last + 1


def parse(text: str) -> tuple[list[dict[str, str]], list[dict[str, Any]]]:
    """Rows ``{front, back, hint, example}`` and the lines that could not be read (``{line, text, reason}``)."""
    raw = str(text or "")
    if len(raw) > MAX_TEXT:
        raise ValueError("That is too much text at once. Paste at most 500 cards.")
    raw = raw.replace("\r\n", NL).replace("\r", NL)
    filled = [line for line in raw.split(NL) if line.strip()]
    if not filled:
        raise ValueError("Paste at least one card: a front and a back on each line.")
    separator = _separator(filled)
    records = list(_records(raw, separator))
    if len(records) > MAX_LINES:
        raise ValueError(f"Paste at most {MAX_LINES} cards at a time.")
    rows: list[dict[str, str]] = []
    problems: list[dict[str, Any]] = []
    for position, (number, line, cells) in enumerate(records):
        parts = cells
        if len(parts) < 2 and NL not in line:
            # This line uses another separator than most: read it with the one it has.
            for other in FALLBACKS:
                if other != separator and other in line:
                    parts = _split(line, other)
                    break
        parts = [_unguard(part.strip()) for part in parts]
        if position == 0 and [p.lower() for p in parts[:2]] == ["front", "back"]:
            continue  # a header row
        if len(parts) < 2 or not parts[0] or not parts[1]:
            problems.append(
                {
                    "line": number,
                    "text": line.strip()[:80],
                    "reason": "Needs a front and a back.",
                }
            )
            continue
        rows.append(dict(zip(COLUMNS, [*parts[:4], "", ""][:4], strict=True)))
    return rows, problems


def import_cards(
    connection: sqlite3.Connection, profile: dict[str, Any], deck_id: str, text: str
) -> dict[str, Any]:
    deck = store.own_deck(connection, profile, deck_id)
    rows, problems = parse(text)
    have = {
        " ".join(row["front"].lower().split())
        for row in store.cards(connection, deck["id"])
    }
    added = skipped = 0
    for index, row in enumerate(rows):
        key = " ".join(row["front"].lower().split())
        if key in have:
            skipped += 1
            continue
        try:
            store.add_card(connection, deck["id"], row)
        except ValueError as error:
            if "at most 1000 cards" in str(error):
                problems.append({"line": 0, "text": "", "reason": str(error)})
                skipped += len(rows) - index
                break
            problems.append(
                {"line": 0, "text": row["front"][:80], "reason": str(error)}
            )
            continue
        have.add(key)
        added += 1
    if added:
        kit.log(
            connection,
            store.APP,
            "import",
            f"{profile['name']} added {added} card{'s' if added != 1 else ''} to {deck['title']}",
            ref_type="deck",
            ref_id=deck["id"],
        )
    connection.commit()
    return {"added": added, "skipped": skipped, "problems": problems[:50]}


def export_csv(
    connection: sqlite3.Connection, profile: dict[str, Any], deck_id: str
) -> tuple[str, bytes]:
    """A deck this person may study, as CSV (front, back, hint, example)."""
    deck = store.visible_deck(connection, profile, deck_id)
    rows = store.cards(connection, deck["id"])
    name = kit.slug(deck["title"]) or "deck"
    return f"{name}.csv", kit.csv_bytes(rows, COLUMNS)
