"""Call assist: a person makes the call, Aira prepares it and keeps the record.

Aira does **not** dial, and there is no auto-dialer: it never places a call, plays a recording or speaks with a
synthetic voice. Google Voice's policy forbids automated and bulk calling, and calling a number with an
artificial or prerecorded voice for marketing needs the called person's prior written consent in many places
(in the US the FCC treats AI-generated voices as artificial voices under the TCPA). What this module does:

* writes a short talk track (opener, questions, pitch, objection answers, voicemail) from facts the lead really
  has, with the model when one is available and a template otherwise;
* gives the number, a ``tel:`` link and a link that opens Google Voice's dialler with the number filled in, for
  a human to press Call;
* shows whether it is a sensible hour where the lead probably is (the US rule is 8 am to 9 pm local time) and
  warns when the number is on the do-not-contact list;
* logs each call with its outcome and a follow-up date, moves the lead along, and adds the number to the
  do-not-contact list when someone asks not to be called again.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from aira.leads import mail, outreach, store

OUTCOMES = (
    "no_answer",
    "voicemail",
    "spoke",
    "interested",
    "callback",
    "not_interested",
    "wrong_number",
    "do_not_call",
)
OUTCOME_LABELS = {
    "no_answer": "No answer",
    "voicemail": "Left a voicemail",
    "spoke": "Spoke with them",
    "interested": "Interested",
    "callback": "Asked me to call back",
    "not_interested": "Not interested",
    "wrong_number": "Wrong number",
    "do_not_call": "Asked not to be called again",
}
STATUS_AFTER = {
    "no_answer": "contacted",
    "voicemail": "contacted",
    "spoke": "replied",
    "interested": "qualified",
    "callback": "contacted",
    "not_interested": "disqualified",
    "do_not_call": "disqualified",
}
_ORDER = ["new", "contacted", "replied", "qualified", "customer", "disqualified"]
CALL_START_HOUR = 8
CALL_END_HOUR = 21

US_TIMEZONES = {
    **dict.fromkeys(
        "CT DE DC FL GA IN KY MA MD ME MI NC NH NJ NY OH PA RI SC VA VT WV".split(),
        "America/New_York",
    ),
    **dict.fromkeys(
        "AL AR IA IL KS LA MN MO MS ND NE OK SD TN TX WI".split(), "America/Chicago"
    ),
    **dict.fromkeys("AZ CO ID MT NM UT WY".split(), "America/Denver"),
    **dict.fromkeys("CA NV OR WA".split(), "America/Los_Angeles"),
    "AK": "America/Anchorage",
    "HI": "Pacific/Honolulu",
}
_US_STATE_NAMES = {
    "alabama": "AL",
    "alaska": "AK",
    "arizona": "AZ",
    "arkansas": "AR",
    "california": "CA",
    "colorado": "CO",
    "connecticut": "CT",
    "delaware": "DE",
    "florida": "FL",
    "georgia": "GA",
    "hawaii": "HI",
    "idaho": "ID",
    "illinois": "IL",
    "indiana": "IN",
    "iowa": "IA",
    "kansas": "KS",
    "kentucky": "KY",
    "louisiana": "LA",
    "maine": "ME",
    "maryland": "MD",
    "massachusetts": "MA",
    "michigan": "MI",
    "minnesota": "MN",
    "mississippi": "MS",
    "missouri": "MO",
    "montana": "MT",
    "nebraska": "NE",
    "nevada": "NV",
    "new hampshire": "NH",
    "new jersey": "NJ",
    "new mexico": "NM",
    "new york": "NY",
    "north carolina": "NC",
    "north dakota": "ND",
    "ohio": "OH",
    "oklahoma": "OK",
    "oregon": "OR",
    "pennsylvania": "PA",
    "rhode island": "RI",
    "south carolina": "SC",
    "south dakota": "SD",
    "tennessee": "TN",
    "texas": "TX",
    "utah": "UT",
    "vermont": "VT",
    "virginia": "VA",
    "washington": "WA",
    "west virginia": "WV",
    "wisconsin": "WI",
    "wyoming": "WY",
}
_STATE = re.compile(r",\s*([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?\s*(?:,|$)")


def lead_timezone(lead: Mapping[str, Any]) -> str:
    """The IANA time zone a US lead is probably in, from its address ('' when it cannot be told)."""
    place = str(lead.get("location") or "")
    found = _STATE.search(place)
    if found and found.group(1) in US_TIMEZONES:
        return US_TIMEZONES[found.group(1)]
    lowered = place.casefold()
    for name, abbreviation in _US_STATE_NAMES.items():
        if re.search(rf"\b{name}\b", lowered):
            return US_TIMEZONES[abbreviation]
    return ""


def calling_window(
    lead: Mapping[str, Any], now: datetime | None = None
) -> dict[str, Any]:
    moment = now or datetime.now(timezone.utc)
    zone = lead_timezone(lead)
    if not zone:
        return {
            "known": False,
            "ok": None,
            "note": "Monosage cannot tell where this business is. Call between 9 am and 5 pm in its own time zone.",
        }
    try:
        from zoneinfo import ZoneInfo

        local = moment.astimezone(ZoneInfo(zone))
    except Exception:  # noqa: BLE001 - no time zone database: do not guess
        return {
            "known": False,
            "ok": None,
            "note": "Check the local time before you call.",
        }
    ok = CALL_START_HOUR <= local.hour < CALL_END_HOUR
    return {
        "known": True,
        "ok": ok,
        "timezone": zone,
        "local_time": local.strftime("%a %H:%M"),
        "note": (
            f"It is {local.strftime('%H:%M')} there ({zone.split('/')[-1].replace('_', ' ')})."
            + (
                ""
                if ok
                else f" Calls are only appropriate from {CALL_START_HOUR}:00 to {CALL_END_HOUR}:00 local time."
            )
        ),
    }


def dial_info(
    connection: Any,
    lead: Mapping[str, Any],
    settings: Mapping[str, Any],
    now: datetime | None = None,
) -> dict[str, Any]:
    """Everything the person needs to place the call themselves."""
    digits = re.sub(r"[^\d+]", "", str(lead.get("phone") or ""))
    if digits and not digits.startswith("+"):
        digits = (
            ("+1" + digits[-10:])
            if len(re.sub(r"\D", "", digits)) in {10, 11}
            else "+" + digits
        )
    blocked = mail.suppressed_reason(connection, lead) if lead.get("phone") else ""
    return {
        "number": str(lead.get("phone") or ""),
        "e164": digits,
        "tel_url": f"tel:{digits}" if digits else "",
        "google_voice_url": f"https://voice.google.com/u/0/calls?a=nc,{quote(digits, safe='')}"
        if digits
        else "",
        "calling_from": str(settings.get("calling_number") or ""),
        "blocked": blocked,
        "window": calling_window(lead, now),
        "reminders": [
            "You place the call yourself; Monosage never dials.",
            "Say who you are and why you are calling in the first sentence.",
            "If they ask not to be called again, log it as 'Asked not to be called again' and stop.",
            "Check the Do Not Call rules and any registration your country or state requires for sales calls.",
        ],
    }


# ---------------------------------------------------------------------------
# the talk track
# ---------------------------------------------------------------------------


def template_script(
    lead: Mapping[str, Any],
    *,
    offer: str,
    sender: str,
    needs: Sequence[tuple[str, str]],
    findings: Sequence[str],
    calling_from: str = "",
) -> dict[str, Any]:
    first = (
        (lead.get("first_name") or "").strip() if lead.get("kind") == "person" else ""
    )
    company = store.clean(lead.get("company") or lead.get("name"), 80)
    who = sender or "[your name]"
    observation = ""
    if findings:
        observation = f"I had a quick look at your website and noticed {findings[0][:1].lower() + findings[0][1:].rstrip('.')}."
    elif needs:
        observation = f"I noticed this about {company}: {needs[0][1][:1].lower() + needs[0][1][1:].rstrip('.')}."
    pitch = offer or "I help small businesses with their website, marketing and content"
    greeting = f"Hi {first}, " if first else "Hello, "
    return {
        "opener": f"{greeting}this is {who}. I'm calling because {observation or 'I came across ' + company + ' and thought I could help'} Do you have two minutes?".replace(
            "  ", " "
        ),
        "pitch": f"{pitch}. {observation}".strip(),
        "questions": [
            "How do new customers find you today?",
            "What have you tried for your website or marketing, and how did it go?",
            "What would make the next three months a success for you?",
        ],
        "objections": [
            {
                "objection": "Not interested.",
                "response": "Understood, thanks for your time. If it's useful I can send two lines by email instead. Would you like that?",
            },
            {
                "objection": "We already have someone.",
                "response": "That's good to hear. Would it be all right if I keep your details for a second opinion later?",
            },
            {
                "objection": "Send me an email.",
                "response": "Of course. What's the best address, and is there anything in particular you'd like me to cover?",
            },
            {
                "objection": "How much is it?",
                "response": "It depends on the scope. If I understand what you need, I can give you a clear quote after a short chat.",
            },
        ],
        "close": "Would a 15-minute call or a short written proposal be more useful for you?",
        "voicemail": (
            f"Hi, this is {who}"
            + (f" calling from {calling_from}" if calling_from else "")
            + f". {observation + ' ' if observation else ''}I'll send a short email too. If you'd rather I didn't contact you, just say so and I won't."
        ).strip(),
        "source": "template",
    }


def call_script(
    lead: Mapping[str, Any],
    *,
    offer: str = "",
    sender: str = "",
    needs: Sequence[tuple[str, str]] = (),
    findings: Sequence[str] = (),
    calling_from: str = "",
    model: Callable[[str], str | None] | None = None,
) -> dict[str, Any]:
    offer = store.clean(offer, 400)
    sender = store.clean(sender, 80)
    fallback = template_script(
        lead,
        offer=offer,
        sender=sender,
        needs=needs,
        findings=findings,
        calling_from=calling_from,
    )
    if model is None:
        return fallback
    facts = {
        k: store.clean(lead.get(k), 160)
        for k in ("name", "title", "company", "industry", "location")
        if lead.get(k)
    }
    prompt = (
        "Write a short phone talk track for a human to read when calling a small business about a service. "
        f"Use ONLY these facts about them: {facts}. Observations you may mention (nothing else about their business): "
        f"{list(findings) + [r for _s, r in needs]}. Never claim to have reviewed or measured anything that is not in that list. "
        f"What the caller offers: {offer or 'website, marketing and content services'}. Caller name: {sender or 'the caller'}. "
        "The caller is an independent provider, NOT an employee of the business being called: never say the caller is from, "
        "at or with that business. "
        "Be polite and brief, identify the caller in the first sentence, never pressure, accept a no. "
        'Reply as JSON with keys: "opener" (string), "pitch" (string), "questions" (3 strings), '
        '"objections" (list of {"objection","response"}), "close" (string), "voicemail" (string).'
    )
    try:
        reply = model(prompt)
        data = json.loads(reply) if isinstance(reply, str) else reply
    except Exception:  # noqa: BLE001 - a model that is down must not block the script
        return fallback
    if not isinstance(data, dict) or not str(data.get("opener") or "").strip():
        return fallback
    objections = [
        {
            "objection": store.clean(o.get("objection"), 120),
            "response": store.clean(o.get("response"), 300),
        }
        for o in (data.get("objections") or [])[:6]
        if isinstance(o, Mapping) and o.get("objection") and o.get("response")
    ]
    questions = [store.clean(q, 200) for q in (data.get("questions") or [])[:5] if q]

    def spoken(key: str, limit: int) -> str:
        """The model's line, unless it says the caller is from the business being called: then the template's."""
        text = store.clean(data.get(key), limit)
        if not text or outreach.claims_to_be_from(text, lead, sender):
            return str(fallback[key])
        return text

    return {
        "opener": spoken("opener", 500),
        "pitch": spoken("pitch", 500),
        "questions": questions or fallback["questions"],
        "objections": objections or fallback["objections"],
        "close": spoken("close", 300),
        "voicemail": spoken("voicemail", 500),
        "source": "model",
    }


# ---------------------------------------------------------------------------
# the call log
# ---------------------------------------------------------------------------


def log_call(
    connection: Any,
    lead_id: str,
    *,
    outcome: str,
    notes: str = "",
    follow_up_days: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    if outcome not in OUTCOMES:
        raise ValueError(f"outcome must be one of: {', '.join(OUTCOMES)}.")
    lead = store.get_lead(connection, lead_id)
    moment = now or datetime.now(timezone.utc)
    follow_up = ""
    if follow_up_days is not None:
        if not 0 <= int(follow_up_days) <= 365:
            raise ValueError("The follow-up must be 0 to 365 days from now.")
        follow_up = (moment + timedelta(days=int(follow_up_days))).isoformat(
            timespec="seconds"
        )
    elif outcome in {"no_answer", "voicemail", "callback"}:
        follow_up = (
            moment + timedelta(days=2 if outcome != "callback" else 1)
        ).isoformat(timespec="seconds")
    call_id = store.new_id()
    connection.execute(
        "INSERT INTO lead_calls (id, lead_id, outcome, notes, follow_up_at, created_at) VALUES (?, ?, ?, ?, ?, ?)",
        (
            call_id,
            lead_id,
            outcome,
            store.clean(notes, 2000),
            follow_up,
            moment.isoformat(timespec="seconds"),
        ),
    )
    target = STATUS_AFTER.get(outcome)
    if target and (
        target == "disqualified"
        or _ORDER.index(target)
        > _ORDER.index(lead["status"] if lead["status"] in _ORDER else "new")
    ):
        connection.execute(
            "UPDATE leads SET status = ?, updated_at = ? WHERE id = ?",
            (target, store.now(), lead_id),
        )
    if outcome == "wrong_number":
        connection.execute(
            "UPDATE leads SET phone = '', updated_at = ? WHERE id = ?",
            (store.now(), lead_id),
        )
    if outcome == "do_not_call":
        for value in (lead.get("phone"), lead.get("email")):
            if value:
                mail.add_suppression(
                    connection, str(value), "Asked not to be contacted (call log)"
                )
    connection.commit()
    row = connection.execute(
        "SELECT * FROM lead_calls WHERE id = ?", (call_id,)
    ).fetchone()
    return dict(row)


def list_calls(connection: Any, lead_id: str) -> list[dict[str, Any]]:
    store.get_lead(connection, lead_id)
    return [
        {**dict(r), "label": OUTCOME_LABELS.get(r["outcome"], r["outcome"])}
        for r in connection.execute(
            "SELECT * FROM lead_calls WHERE lead_id = ? ORDER BY created_at DESC, rowid DESC",
            (lead_id,),
        )
    ]


def follow_ups_due(
    connection: Any, now: datetime | None = None
) -> list[dict[str, Any]]:
    """Leads whose latest logged call asked for a follow-up that has come due."""
    moment = (now or datetime.now(timezone.utc)).isoformat(timespec="seconds")
    rows = connection.execute(
        """SELECT c.*, l.name AS lead_name, l.company AS lead_company, l.phone AS lead_phone, l.status AS lead_status
           FROM lead_calls c JOIN leads l ON l.id = c.lead_id
           WHERE c.follow_up_at != '' AND c.follow_up_at <= ?
             AND c.id = (SELECT id FROM lead_calls WHERE lead_id = c.lead_id ORDER BY created_at DESC, rowid DESC LIMIT 1)
             AND l.status NOT IN ('disqualified', 'customer')
           ORDER BY c.follow_up_at""",
        (moment,),
    ).fetchall()
    return [
        {**dict(r), "label": OUTCOME_LABELS.get(r["outcome"], r["outcome"])}
        for r in rows
    ]
