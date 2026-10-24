"""Outreach drafts for a lead. Aira writes the words; a person reads, edits and sends them elsewhere.

A draft is never sent by Aira. With a model available (the same local or hosted model that writes cover
letters) the draft is personalised from the lead's own facts and your offer; without one a plain template
fills in the same facts. Either way nothing is invented: it uses only fields the lead actually has.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from aira.leads import store

TONES = ("friendly", "direct", "formal")
CHANNELS = ("email", "linkedin")
LINKEDIN_NOTE_LIMIT = 300  # LinkedIn caps a connection note at 300 characters


def _first_name(lead: Mapping[str, Any]) -> str:
    """A person's first name; empty for a company, which is greeted without one."""
    if lead.get("kind") == "company":
        return ""
    return store.clean(
        lead.get("first_name") or str(lead.get("name") or "").split(" ")[0], 40
    )


def claims_to_be_from(text: str, lead: Mapping[str, Any], sender: str = "") -> bool:
    """True when the text says the *caller* is from the lead's own business (a small model's classic slip).

    "Hello, this is Sam calling from Acme Bakery" is wrong when Acme Bakery is the business being called.
    """
    names = {
        store.clean(lead.get("company"), 80),
        store.clean(lead.get("name"), 80),
    } - {""}
    first = store.clean(sender, 80).split(" ")[0] if sender else ""
    for name in names:
        escaped = re.escape(name)
        patterns = [
            rf"(?:this is|here|calling|speaking|my name is|i am|i'm)[^.!?]{{0,60}}\b(?:from|at|with)\s+{escaped}",
            rf"\bhere\s+from\s+{escaped}",
        ]
        if first:
            patterns.append(
                rf"\b{re.escape(first)}\b[^.!?]{{0,25}}\b(?:from|at|with)\s+{escaped}"
            )
        if any(re.search(pattern, text, re.I) for pattern in patterns):
            return True
    return False


def _greet_a_business_politely(body: str, lead: Mapping[str, Any]) -> str:
    """ "Hi Acme Bakery," becomes "Hi there," when the lead is a business, not a person."""
    if lead.get("kind") == "person":
        return body
    for name in {
        store.clean(lead.get("company"), 80),
        store.clean(lead.get("name"), 80),
    } - {""}:
        body = re.sub(
            rf"^\s*(hi|hello|dear)\s+{re.escape(name)}\s*,",
            r"\1 there,",
            body,
            count=1,
            flags=re.I,
        )
    return body


def _lower(text: str) -> str:
    text = store.clean(text, 160).rstrip(".")
    return text[:1].lower() + text[1:] if text and not text[:2].isupper() else text


def template_draft(
    lead: Mapping[str, Any],
    *,
    offer: str,
    sender: str,
    tone: str,
    channel: str,
    needs: Sequence[tuple[str, str]] = (),
    findings: Sequence[str] = (),
) -> dict[str, str]:
    audited = [
        r.removeprefix("Site audit: ")
        for _s, r in needs
        if r.startswith("Site audit: ")
    ]
    findings = list(findings) or audited
    needs = [(s, r) for s, r in needs if not r.startswith("Site audit: ")]
    first = _first_name(lead)
    company = store.clean(lead.get("company"), 80)
    title = store.clean(lead.get("title"), 80)
    greeting = {
        "friendly": f"Hi {first}," if first else "Hi,",
        "direct": f"{first}," if first else "Hello,",
        "formal": f"Dear {first}," if first else "Hello,",
    }[tone]
    hook = ""
    for signal in lead.get("signals") or []:
        if isinstance(signal, Mapping) and signal.get("type") == "hiring":
            hook = f"I saw {company or 'your team'} is hiring ({store.clean(signal.get('text'), 100).removeprefix('Hiring: ')}). "
            break
    if not hook and findings:
        who = company or "your site"
        hook = f"I took a quick look at the {who} website and noticed {_lower(findings[0])}. "
    if not hook and needs:
        who = company or "your business"
        hook = f"I noticed this about {who}: {_lower(needs[0][1])}. "
    if not hook and company and title:
        hook = f"I came across your work as {title} at {company}. "
    elif not hook and company:
        hook = f"I came across {company}. "
    body_offer = offer or "I would like to share how we might help"
    close = {
        "friendly": "Would a short call next week make sense?",
        "direct": "Open to a 15-minute call?",
        "formal": "Would you be open to a brief conversation?",
    }[tone]
    sign = f"\n\n{sender}" if sender else ""
    if channel == "linkedin":
        note = f"{greeting} {hook}{body_offer}. {close}"
        note = re.sub(r"\s+", " ", note).strip()
        return {"subject": "", "body": note[:LINKEDIN_NOTE_LIMIT], "source": "template"}
    subject = (f"{company}: {body_offer[:50]}" if company else body_offer[:70]).strip(
        ": "
    )
    body = f"{greeting}\n\n{hook}{body_offer}.\n\n{close}{sign}"
    return {"subject": subject[:120], "body": body, "source": "template"}


def draft(
    lead: Mapping[str, Any],
    *,
    offer: str = "",
    sender: str = "",
    tone: str = "friendly",
    channel: str = "email",
    model: Callable[[str], str | None] | None = None,
    needs: Sequence[tuple[str, str]] = (),
    findings: Sequence[str] = (),
) -> dict[str, str]:
    if tone not in TONES:
        raise ValueError(f"tone must be one of: {', '.join(TONES)}.")
    if channel not in CHANNELS:
        raise ValueError(f"channel must be one of: {', '.join(CHANNELS)}.")
    offer = store.clean(offer, 400)
    sender = store.clean(sender, 80)
    fallback = template_draft(
        lead,
        offer=offer,
        sender=sender,
        tone=tone,
        channel=channel,
        needs=needs,
        findings=findings,
    )
    if model is None:
        return fallback
    facts = {
        key: store.clean(lead.get(key), 160)
        for key in ("name", "title", "company", "industry", "location")
        if lead.get(key)
    }
    signals = [
        store.clean(s.get("text"), 160)
        for s in (lead.get("signals") or [])[:3]
        if isinstance(s, Mapping) and s.get("text")
    ]
    limit = (
        f"at most {LINKEDIN_NOTE_LIMIT} characters, no subject line"
        if channel == "linkedin"
        else "under 120 words with a short subject line"
    )
    prompt = (
        f"Write one {tone} {channel} outreach message {limit}. Use ONLY these facts about the recipient and do not "
        f"invent anything else: {facts}. Recent signals: {signals}. "
        f"Observations about their business that you may mention (and nothing else about it): {list(findings) + [r for _s, r in needs]}. "
        "Never claim to have reviewed, used or measured anything that is not in that list. "
        f"What the sender offers: {offer or 'not specified; keep it general'}. "
        "The sender is an independent provider, NOT an employee of the recipient's business: never say the sender is from, "
        "at or with the recipient's company. "
        f"Sign as: {sender or 'the sender (no name given)'}. "
        "Do not promise results, do not use hype. Greet a person by first name; greet a business with 'Hi there', never by its name. End with ONE short yes/no question about whether they would like to hear more or see a short proposal (never a question about their prices, revenue or costs). "
        'Reply as JSON: {"subject": "...", "body": "..."}'
    )
    try:
        reply = model(prompt)
    except Exception:  # noqa: BLE001 - a model that is down must not block a draft
        return fallback
    if not reply:
        return fallback
    import json

    try:
        data = json.loads(reply) if isinstance(reply, str) else reply
    except ValueError:
        return fallback
    if not isinstance(data, dict) or not str(data.get("body") or "").strip():
        return fallback
    if claims_to_be_from(str(data.get("body")), lead, sender):
        return fallback
    data["body"] = _greet_a_business_politely(str(data["body"]), lead)
    body = (
        store.clean(data["body"], 2000)
        if channel == "linkedin"
        else str(data["body"]).strip()[:2000]
    )
    if channel == "linkedin":
        body = body[:LINKEDIN_NOTE_LIMIT]
    return {
        "subject": ""
        if channel == "linkedin"
        else store.clean(data.get("subject"), 120),
        "body": body,
        "source": "model",
    }
