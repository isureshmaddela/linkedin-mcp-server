"""Core extraction engine using innerText instead of DOM selectors."""

from __future__ import annotations

import asyncio
import base64
from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import random
import re
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import parse_qs, quote_plus, urljoin, urlparse

from patchright.async_api import Page, TimeoutError as PlaywrightTimeoutError

from linkedin_mcp_server.core import (
    detect_auth_barrier,
    detect_auth_barrier_quick,
    resolve_remember_me_prompt,
)
from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    LinkedInScraperException,
    SalesNavigatorAccessError,
)
from linkedin_mcp_server.debug_trace import record_page_trace
from linkedin_mcp_server.debug_utils import stabilize_navigation
from linkedin_mcp_server.error_diagnostics import build_issue_diagnostics
from linkedin_mcp_server.core.utils import (
    detect_rate_limit,
    handle_modal_close,
    scroll_job_sidebar,
    scroll_to_bottom,
)
from linkedin_mcp_server.scraping.connection import ActionSignals
from linkedin_mcp_server.scraping import easy_apply as _ea
from linkedin_mcp_server.scraping import sales_navigator as _sn
from linkedin_mcp_server.scraping.link_metadata import (
    Reference,
    build_references,
    dedupe_references,
)

from .fields import COMPANY_SECTIONS, PERSON_SECTIONS

if TYPE_CHECKING:
    from linkedin_mcp_server.callbacks import ProgressCallback

logger = logging.getLogger(__name__)

WaitUntil = Literal["commit", "domcontentloaded", "load", "networkidle"]

# Delay between page navigations to avoid rate limiting
_NAV_DELAY = 2.0

# Backoff before retrying a rate-limited page
_RATE_LIMIT_RETRY_DELAY = 5.0

# Returned as section text when LinkedIn rate-limits the page
_RATE_LIMITED_MSG = "[Rate limited] LinkedIn blocked this section. Try again later or request fewer sections."

# LinkedIn shows 25 results per page
_PAGE_SIZE = 25

# Sales Navigator reading: randomized pause between result pages (seconds), how long to wait for the first
# lead row, and the scroll loop that makes a lazily rendered page of ~25 rows appear in full.
_SALES_NAV_PACE_SECONDS = (1.5, 3.5)
_SALES_NAV_RESULT_WAIT_MS = 8000
_SALES_NAV_SCROLL_PAUSE_SECONDS = 0.6
_SALES_NAV_SCROLL_FRACTION = 0.8
_SALES_NAV_MAX_SCROLL_STEPS = 24

# Normalization maps for job search filters
_DATE_POSTED_MAP = {
    "past_hour": "r3600",
    "past_24_hours": "r86400",
    "past_week": "r604800",
    "past_month": "r2592000",
}

_EXPERIENCE_LEVEL_MAP = {
    "internship": "1",
    "entry": "2",
    "associate": "3",
    "mid_senior": "4",
    "director": "5",
    "executive": "6",
}

_JOB_TYPE_MAP = {
    "full_time": "F",
    "part_time": "P",
    "contract": "C",
    "temporary": "T",
    "volunteer": "V",
    "internship": "I",
    "other": "O",
}

_WORK_TYPE_MAP = {"on_site": "1", "remote": "2", "hybrid": "3"}

_SORT_BY_MAP = {"date": "DD", "relevance": "R"}

# Valid tokens for the people-search ``network`` facet.
# LinkedIn accepts "F" (1st-degree), "S" (2nd-degree), "O" (3rd-degree and beyond).
_NETWORK_TOKENS = ("F", "S", "O")

_DIALOG_SELECTOR = 'dialog[open], [role="dialog"]'
_DIALOG_TEXTAREA_SELECTOR = '[role="dialog"] textarea, dialog textarea'

# Origin used by the Easy Apply flow. A module constant (rather than an
# inline literal) so the stand-in LinkedIn server in tests can be swapped in
# without the extractor ever contacting linkedin.com.
_LINKEDIN_ORIGIN = "https://www.linkedin.com"

# Easy Apply timing. The dialog/popup budgets mirror the other action tools;
# the submit budget is longer because LinkedIn posts the application before
# rendering the confirmation dialog.
_EASY_APPLY_OBSERVE_SECONDS = 8.0
_EASY_APPLY_DIALOG_TIMEOUT_MS = 10000
_EASY_APPLY_SUBMIT_TIMEOUT_MS = 20000
_EASY_APPLY_ADVANCE_WAIT_SECONDS = 1.5
_EASY_APPLY_KEY_ATTR = "data-easy-apply-key"
_EASY_APPLY_HEADER_WAIT_SECONDS = 15.0
_EASY_APPLY_HEADER_POLL_SECONDS = 0.5

# Shared JS prologue for the Easy Apply probes: whitespace normalisation and
# a visibility test that ignores controls LinkedIn hides inside collapsed
# steps (a [hidden] ancestor or display:none) but keeps visually-hidden
# custom radios/checkboxes and file inputs, which keep a 1px box.
_EASY_APPLY_JS_PROLOGUE = r"""
    const normalize = value => (value || '').replace(/\s+/g, ' ').trim();
    const textOf = node => normalize(node ? (node.innerText || node.textContent) : '');
    const isShown = el => {
        if (!el || el.hidden) return false;
        const style = window.getComputedStyle(el);
        if (style.display === 'none' || style.visibility === 'hidden') return false;
        const rect = el.getBoundingClientRect();
        return rect.width > 0 || rect.height > 0;
    };
    const inDisplayedTree = (el, stop) => {
        let node = el.parentElement;
        while (node && node !== stop) {
            if (node.hidden) return false;
            if (window.getComputedStyle(node).display === 'none') return false;
            node = node.parentElement;
        }
        return true;
    };
    const visibleDialogs = () => Array.from(
        document.querySelectorAll('dialog[open], [role="dialog"]')
    ).filter(isShown);
"""

# Job header probe: find the <h1> (job title), walk up to the smallest
# ancestor that contains a visible <button> (the top card with the action
# row), mark every interactive element there with a key attribute and return
# their structural facts. The header text is returned without anchor text so
# "Applied" detection cannot trip over a company name link.
_EASY_APPLY_HEADER_JS = (
    "({ keyAttr }) => {"
    + _EASY_APPLY_JS_PROLOGUE
    + r"""
    document.querySelectorAll('[' + keyAttr + ']').forEach(el => {
        el.removeAttribute(keyAttr);
        el.removeAttribute(keyAttr + '-option');
    });
    const main = document.querySelector('main') || document.body;
    // The job title: an <h1> when LinkedIn uses one, else a level-1 heading, else the element
    // that shows exactly the title the tab carries ("Title | Company | LinkedIn").
    const findTitleElement = () => {
        const headings = [
            main.querySelector('h1'),
            document.querySelector('h1'),
            document.querySelector('[role="heading"][aria-level="1"]'),
        ];
        for (const heading of headings) {
            if (heading && isShown(heading) && textOf(heading)) return heading;
        }
        const title = normalize(document.title)
            .replace(/^\(\d+\)\s*/, '')
            .split(/\s+[|\u2013\u2014-]\s+/)[0]
            .toLowerCase();
        if (!title) return null;
        const sameText = el => isShown(el) && textOf(el).toLowerCase() === title;
        const chrome = el => Boolean(el.closest('nav, header, [role="banner"], [role="navigation"], footer'));
        let match = Array.from(main.querySelectorAll('*')).find(el => sameText(el) && !chrome(el));
        if (!match) return null;
        for (;;) {
            const inner = Array.from(match.children).find(sameText);
            if (!inner) return match;
            match = inner;
        }
    };
    const h1 = findTitleElement();
    if (!h1) return null;
    const interactive = 'button, a[href], [role="button"]';
    const controlsOf = root => Array.from(root.querySelectorAll(interactive))
        .filter(el => !h1.contains(el) && isShown(el));
    let root = h1.parentElement;
    while (root && root !== main) {
        if (controlsOf(root).some(el => el.tagName === 'BUTTON' || el.getAttribute('role') === 'button')) break;
        root = root.parentElement;
    }
    if (!root) root = main;
    const controls = controlsOf(root).slice(0, 40).map((el, index) => {
        const key = 'apply' + index;
        el.setAttribute(keyAttr, key);
        return {
            key,
            tag: el.tagName.toLowerCase(),
            text: textOf(el),
            aria_label: normalize(el.getAttribute('aria-label')),
            href: el.href || el.getAttribute('href') || '',
            target: el.getAttribute('target') || '',
            disabled: Boolean(el.disabled) || el.getAttribute('aria-disabled') === 'true',
            expanded: el.hasAttribute('aria-expanded'),
            pressed: el.hasAttribute('aria-pressed'),
        };
    });
    const lines = [];
    root.querySelectorAll('*').forEach(el => {
        if (el.children.length || el.closest('a') || !isShown(el)) return;
        const text = textOf(el);
        if (text) lines.push(text);
    });
    return { title: textOf(h1), text: lines.join('\n'), controls };
}"""
)

# Dialog probe: read every question of the visible (topmost) dialog with its
# label, kind, required marker, options, current value and inline error, mark
# each control with a key attribute for later actions, and report the footer's
# primary button (last text button), the dismiss control (icon-only labelled
# button) and the progress bar.
_EASY_APPLY_DIALOG_JS = (
    "({ keyAttr }) => {"
    + _EASY_APPLY_JS_PROLOGUE
    + r"""
    const dialogs = visibleDialogs();
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) return null;
    document.querySelectorAll('[' + keyAttr + ']').forEach(el => {
        el.removeAttribute(keyAttr);
        el.removeAttribute(keyAttr + '-option');
    });
    const controlSelector = 'input, select, textarea';
    const SKIP = new Set(['hidden', 'submit', 'button', 'reset', 'image']);
    const typeOf = el => {
        const tag = el.tagName.toLowerCase();
        if (tag !== 'input') return tag;
        return (el.getAttribute('type') || 'text').toLowerCase();
    };
    const controls = Array.from(dialog.querySelectorAll(controlSelector)).filter(el => {
        const type = typeOf(el);
        if (SKIP.has(type)) return false;
        if (!inDisplayedTree(el, dialog)) return false;
        if (type === 'file' || type === 'radio' || type === 'checkbox') return true;
        return isShown(el);
    });
    const labelledBy = el => (el.getAttribute('aria-labelledby') || '')
        .split(/\s+/).filter(Boolean)
        .map(id => textOf(document.getElementById(id))).filter(Boolean).join(' ');
    const explicitLabel = el => {
        if (el.id) {
            const label = dialog.querySelector('label[for="' + CSS.escape(el.id) + '"]');
            if (label) return textOf(label);
        }
        const wrapping = el.closest('label');
        return wrapping ? textOf(wrapping) : '';
    };
    const groupLabel = el => {
        const group = el.closest('fieldset, [role="radiogroup"], [role="group"]');
        if (!group) return '';
        const legend = group.querySelector('legend');
        if (legend) return textOf(legend);
        return labelledBy(group) || normalize(group.getAttribute('aria-label'));
    };
    const precedingText = el => {
        let node = el;
        for (let depth = 0; node && node !== dialog && depth < 4; depth += 1) {
            let sibling = node.previousElementSibling;
            while (sibling) {
                if (!sibling.querySelector(controlSelector) && !sibling.matches(controlSelector)) {
                    const text = textOf(sibling);
                    if (text) return text;
                }
                sibling = sibling.previousElementSibling;
            }
            node = node.parentElement;
        }
        return '';
    };
    const labelOf = el => labelledBy(el)
        || normalize(el.getAttribute('aria-label'))
        || explicitLabel(el)
        || groupLabel(el)
        || normalize(el.getAttribute('placeholder'))
        || precedingText(el)
        || normalize(el.getAttribute('name'));
    const requiredOf = (el, label) => Boolean(el.required)
        || el.getAttribute('aria-required') === 'true'
        || /\*\s*$/.test(label || '');
    const sameGroup = (a, b) => a.name && a.name === b.name && typeOf(a) === typeOf(b);
    const containerOf = el => {
        let container = el.parentElement;
        let node = el.parentElement;
        while (node && node !== dialog) {
            const foreign = Array.from(node.querySelectorAll(controlSelector))
                .some(other => other !== el && !sameGroup(other, el));
            if (foreign) break;
            container = node;
            node = node.parentElement;
        }
        return container || el.parentElement || dialog;
    };
    const alertIn = container => {
        const alert = Array.from(container.querySelectorAll('[role="alert"]')).find(isShown);
        return textOf(alert);
    };
    const errorOf = el => {
        const ids = (el.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
        for (const id of ids) {
            const node = document.getElementById(id);
            if (!node || !isShown(node)) continue;
            const flagged = node.getAttribute('role') === 'alert'
                || node.hasAttribute('aria-live')
                || el.getAttribute('aria-invalid') === 'true';
            if (flagged && textOf(node)) return textOf(node);
        }
        const alert = alertIn(containerOf(el));
        if (alert) return alert;
        if (el.getAttribute('aria-invalid') === 'true') {
            return 'LinkedIn marked this answer as missing or invalid.';
        }
        return '';
    };
    const questions = [];
    const groups = new Map();
    let counter = 0;
    controls.forEach(el => {
        const type = typeOf(el);
        const isGroup = type === 'radio' || (type === 'checkbox' && el.name
            && dialog.querySelectorAll('input[type="checkbox"][name="' + CSS.escape(el.name) + '"]').length > 1);
        if (isGroup) {
            const groupKey = type + ':' + (el.name || ('anon' + counter));
            const optionLabel = explicitLabel(el) || normalize(el.getAttribute('aria-label')) || normalize(el.value);
            let question = groups.get(groupKey);
            if (!question) {
                const key = 'q' + (counter++);
                question = {
                    key, label: groupLabel(el) || precedingText(el) || optionLabel, kind: type,
                    required: false, options: [], option_values: [], checked: [],
                    control_id: el.id || '', name: el.name || '',
                    autocomplete: el.getAttribute('autocomplete') || '', input_type: type,
                    error: '', combobox: false, group: true,
                };
                groups.set(groupKey, question);
                questions.push(question);
            }
            el.setAttribute(keyAttr, question.key);
            el.setAttribute(keyAttr + '-option', optionLabel);
            question.options.push(optionLabel);
            question.option_values.push(el.value || '');
            if (el.checked) question.checked.push(optionLabel);
            question.required = question.required || requiredOf(el, question.label);
            question.error = question.error || errorOf(el);
            return;
        }
        const key = 'q' + (counter++);
        el.setAttribute(keyAttr, key);
        const label = labelOf(el);
        const question = {
            key, label, kind: 'unknown', required: requiredOf(el, label),
            options: [], option_values: [], value: null,
            control_id: el.id || '', name: el.name || '',
            autocomplete: el.getAttribute('autocomplete') || '', input_type: type,
            error: errorOf(el),
            combobox: el.getAttribute('role') === 'combobox'
                || Boolean(el.getAttribute('aria-autocomplete'))
                || Boolean(el.getAttribute('list')),
            group: false,
        };
        if (type === 'select') {
            question.kind = 'select';
            Array.from(el.options).forEach(option => {
                question.options.push(textOf(option) || normalize(option.value));
                question.option_values.push(option.value);
            });
            const selected = el.options[el.selectedIndex];
            question.value = selected ? (textOf(selected) || normalize(selected.value)) : null;
            question.selected_index = el.selectedIndex;
            question.selected_value = selected ? selected.value : '';
            question.selected_disabled = selected ? Boolean(selected.disabled) : false;
        } else if (type === 'textarea') {
            question.kind = 'textarea';
            question.value = el.value || null;
        } else if (type === 'checkbox') {
            question.kind = 'checkbox';
            question.value = el.checked ? 'true' : 'false';
        } else if (type === 'file') {
            question.kind = 'file';
            question.value = el.files && el.files.length ? el.files[0].name : null;
        } else {
            const known = ['text', 'email', 'tel', 'url', 'number', 'date', 'search'];
            question.kind = known.includes(type) ? (type === 'search' ? 'text' : type) : 'unknown';
            question.value = el.value || null;
        }
        questions.push(question);
    });
    questions.forEach(question => {
        if (question.group) {
            question.value = question.checked.length ? question.checked.join(', ') : null;
            delete question.checked;
        }
    });
    const footer = dialog.querySelector('footer') || dialog;
    const buttons = Array.from(footer.querySelectorAll('button, [role="button"]'))
        .filter(b => isShown(b) && !b.disabled && b.getAttribute('aria-disabled') !== 'true');
    const textButtons = buttons.filter(b => textOf(b));
    const primary = textButtons[textButtons.length - 1] || null;
    if (primary) primary.setAttribute(keyAttr, 'primary');
    const dismiss = Array.from(dialog.querySelectorAll('button'))
        .find(b => isShown(b) && b.getAttribute('aria-label') && !textOf(b));
    if (dismiss) dismiss.setAttribute(keyAttr, 'dismiss');
    const progressEl = dialog.querySelector('progress, [role="progressbar"]');
    let progress = null;
    if (progressEl) {
        const value = parseFloat(progressEl.getAttribute('value') ?? progressEl.getAttribute('aria-valuenow'));
        const max = parseFloat(progressEl.getAttribute('max') ?? progressEl.getAttribute('aria-valuemax') ?? '100');
        if (!Number.isNaN(value) && max > 0) progress = value / max;
    }
    const heading = dialog.querySelector('h1, h2, h3');
    return {
        title: textOf(heading),
        text: normalize(dialog.innerText),
        questions,
        primary: primary ? { text: textOf(primary), aria_label: normalize(primary.getAttribute('aria-label')) } : null,
        progress,
        control_count: controls.length,
        has_progress: Boolean(progressEl),
        button_texts: textButtons.map(textOf),
    };
}"""
)

# Post-submit probe used with wait_for_function: resolves when the dialog is
# gone, when its text carries the per-locale confirmation wording, or when
# (structurally) the form controls and the progress bar have disappeared.
_EASY_APPLY_POST_SUBMIT_JS = (
    "({ sentLabels, previousText }) => {"
    + _EASY_APPLY_JS_PROLOGUE
    + r"""
    const dialogs = visibleDialogs();
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) return { state: 'closed', text: '' };
    const text = normalize(dialog.innerText);
    const lower = text.toLowerCase();
    if (sentLabels.some(label => lower.includes(label.toLowerCase()))) {
        return { state: 'sent', text };
    }
    const controls = dialog.querySelectorAll(
        'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea'
    ).length;
    const progress = dialog.querySelector('progress, [role="progressbar"]');
    if (controls === 0 && !progress && text && text !== previousText) {
        return { state: 'sent_structural', text };
    }
    return false;
}"""
)

_EASY_APPLY_VISIBLE_DIALOG_JS = (
    "() => {"
    + _EASY_APPLY_JS_PROLOGUE
    + r"""
    const dialogs = visibleDialogs();
    const dialog = dialogs[dialogs.length - 1];
    if (!dialog) return null;
    return {
        text: normalize(dialog.innerText),
        control_count: dialog.querySelectorAll(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), select, textarea'
        ).length,
    };
}"""
)

_EASY_APPLY_CLICK_KEY_JS = r"""({ keyAttr, key }) => {
    const el = document.querySelector('[' + keyAttr + '="' + key + '"]');
    if (!el) return false;
    el.click();
    return true;
}"""

# Radios and checkboxes: LinkedIn renders custom-styled controls whose <input>
# is visually hidden, so a native click through the element is the reliable
# path (it toggles the state and fires the click/change events React listens
# to). ``option`` selects a member of a group by its label.
_EASY_APPLY_SET_CHOICE_JS = r"""({ keyAttr, key, option, checked }) => {
    const members = Array.from(document.querySelectorAll('[' + keyAttr + '="' + key + '"]'));
    const target = option
        ? members.find(el => el.getAttribute(keyAttr + '-option') === option)
        : members[0];
    if (!target) return false;
    if (target.type === 'radio') {
        if (!target.checked) target.click();
        return target.checked;
    }
    if (Boolean(target.checked) !== Boolean(checked)) target.click();
    return Boolean(target.checked) === Boolean(checked);
}"""

# Fallback for fill(): set the value through the native setter so React's
# value tracker notices, then dispatch input/change.
_EASY_APPLY_SET_VALUE_JS = r"""({ keyAttr, key, value }) => {
    const el = document.querySelector('[' + keyAttr + '="' + key + '"]');
    if (!el) return false;
    const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    el.focus();
    if (descriptor && descriptor.set) descriptor.set.call(el, value); else el.value = value;
    el.dispatchEvent(new Event('input', { bubbles: true }));
    el.dispatchEvent(new Event('change', { bubbles: true }));
    return true;
}"""

# Typeahead inputs (role=combobox): pick the suggestion that starts with the
# typed value, else the first suggestion, so the value is one LinkedIn knows.
_EASY_APPLY_PICK_OPTION_JS = (
    "({ value }) => {"
    + _EASY_APPLY_JS_PROLOGUE
    + r"""
    const options = Array.from(document.querySelectorAll('[role="option"]')).filter(isShown);
    if (!options.length) return null;
    const wanted = normalize(value).toLowerCase();
    const match = options.find(o => textOf(o).toLowerCase().startsWith(wanted)) || options[0];
    match.click();
    return textOf(match);
}"""
)

_EASY_APPLY_EVIDENCE_TEXT_JS = (
    "() => {"
    + _EASY_APPLY_JS_PROLOGUE
    + r"""
    const parts = visibleDialogs().map(d => d.innerText || '');
    const main = document.querySelector('main') || document.body;
    parts.push(main ? (main.innerText || '') : '');
    return parts.join('\n\n');
}"""
)

_MESSAGING_COMPOSE_LINK_SELECTOR = 'main a[href*="/messaging/compose/"]'
_MESSAGING_COMPOSE_SELECTOR = (
    'div[role="textbox"][contenteditable="true"][aria-label*="Write a message"]'
)
_MESSAGING_COMPOSE_FALLBACK_SELECTORS = (
    _MESSAGING_COMPOSE_SELECTOR,
    'main div[role="textbox"][contenteditable="true"]',
    'main [contenteditable="true"][aria-label*="message"]',
)
_MESSAGING_ENABLED_SEND_SELECTOR = (
    'button[type="submit"]:not([disabled]), '
    'button[aria-label*="Send"]:not([disabled]), '
    'button[aria-label*="send"]:not([disabled])'
)
_MESSAGING_RECIPIENT_PICKER_SELECTOR = (
    'input[placeholder*="Type a name"], '
    'input[aria-label*="Type a name"], '
    'input[placeholder*="multiple names"]'
)
_MESSAGING_CLOSE_SELECTOR = (
    'button[aria-label*="Close your draft conversation"], '
    'button[aria-label="Dismiss"], '
    'button[aria-label*="Dismiss"], '
    'button[aria-label*="Close"]'
)

# Shared JS function that walks up from any /messaging/compose/ anchor
# inside <main> to find the smallest ancestor that satisfies the
# action-root predicate (>=2 interactive children, >=1 button). This is
# the top-card action row regardless of LinkedIn's class names.
#
# Inlined into both _ACTION_SIGNALS_JS and _OPEN_MORE_BUTTON_JS so a
# single change to the heuristic propagates to both call sites.
_FIND_ACTION_ROOT_FN_JS = r"""
function findActionRoot(main) {
  const composeAnchors = main.querySelectorAll('a[href*="/messaging/compose/"]');
  for (const a of composeAnchors) {
    let el = a.parentElement;
    while (el && el !== main) {
      const interactive = el.querySelectorAll('button, a').length;
      const buttons = el.querySelectorAll('button').length;
      if (interactive >= 2 && buttons >= 1) {
        return el;
      }
      el = el.parentElement;
    }
  }
  return null;
}
"""

# Locale-independent connection-state probe. Returns four booleans;
# per AGENTS.md Scraping Rules, every signal is based on URL patterns
# or ARIA-attribute *presence* — never on label text values.
#
# - hasInvite: vanityName-scoped invite anchor anywhere in document.
#   Searches document (not main) so a post-More-menu reread sees
#   portal-rendered menu items. The vanityName parameter is unique to
#   the target user, so document-wide search has no false-positive risk.
# - hasComposeInActionRoot: any /messaging/compose/ anchor exists inside
#   the action root. Scoped to main (not document) to avoid the More
#   menu's "Send profile in a message" anchor, which is a compose URL
#   but lives outside the action area.
# - hasEditIntro: edit-intro URL exists, only rendered on own profile.
# - hasLabeledActionButton: at least one <button[aria-label]> inside the
#   action root. Primary action buttons (Follow / Connect /
#   Save in Sales Navigator) carry aria-label for screen readers; the
#   profile More button uses aria-expanded instead and is not counted.
# - hasLabeledActionAnchor: at least one <a[aria-label]> inside the
#   action root. LinkedIn renders the Pending state as an anchor (linking
#   back to the profile URL) carrying aria-label like "Pending, click to
#   withdraw…". The Message anchor has only aria-disabled, so a labeled
#   anchor is the locale-independent Pending signal.
#
# The username is CSS-escaped before interpolation into attribute
# selectors to defend against malformed inputs containing characters
# that would otherwise break the selector syntax (quotes, brackets).
_ACTION_SIGNALS_JS = (
    r"""
((username) => {
"""
    + _FIND_ACTION_ROOT_FN_JS
    + r"""
  const main = document.querySelector('main');
  if (!main) return null;

  const safe = CSS.escape(username);
  const inviteSel = `a[href*="/preload/custom-invite/?vanityName=${safe}"]`;
  const editSel = `a[href*="/in/${safe}/edit/intro/"]`;

  const hasInvite = !!document.querySelector(inviteSel);
  const hasEditIntro = !!main.querySelector(editSel);

  const actionRoot = findActionRoot(main);

  let hasComposeInActionRoot = false;
  let hasLabeledActionButton = false;
  let hasLabeledActionAnchor = false;
  if (actionRoot) {
    hasComposeInActionRoot =
      !!actionRoot.querySelector('a[href*="/messaging/compose/"]');
    for (const b of actionRoot.querySelectorAll('button')) {
      if (b.hasAttribute('aria-label')) {
        hasLabeledActionButton = true;
        break;
      }
    }
    for (const a of actionRoot.querySelectorAll('a')) {
      if (a.hasAttribute('aria-label')) {
        hasLabeledActionAnchor = true;
        break;
      }
    }
  }

  return {
    hasInvite,
    hasComposeInActionRoot,
    hasEditIntro,
    hasLabeledActionButton,
    hasLabeledActionAnchor,
  };
})
"""
)

# Open the profile's More button, located inside the action root via the
# aria-expanded attribute. The aria-expanded attribute uniquely identifies
# the menu opener without text labels (the More button has no aria-label,
# while Follow/Connect/Pending buttons do — the inverse pattern). Returns
# true iff the click landed; the caller waits for [role='menu'] visibility
# before re-scanning signals.
_OPEN_MORE_BUTTON_JS = (
    r"""
(() => {
"""
    + _FIND_ACTION_ROOT_FN_JS
    + r"""
  const main = document.querySelector('main');
  if (!main) return false;
  const actionRoot = findActionRoot(main);
  if (!actionRoot) return false;
  const moreBtn = actionRoot.querySelector('button[aria-expanded]');
  if (!moreBtn) return false;
  moreBtn.click();
  return true;
})
"""
)


def _connection_result(
    url: str,
    status: str,
    message: str,
    *,
    note_sent: bool = False,
    profile: str = "",
) -> dict[str, Any]:
    """Build a structured response for a profile connection attempt."""
    result: dict[str, Any] = {
        "url": url,
        "status": status,
        "message": message,
        "note_sent": note_sent,
    }
    if profile:
        result["profile"] = profile
    return result


def _normalize_csv(value: str, mapping: dict[str, str]) -> str:
    """Normalize a comma-separated filter value using the provided mapping."""
    parts = [v.strip() for v in value.split(",")]
    return ",".join(mapping.get(p, p) for p in parts)


def _encode_list_facet(values: list[str]) -> str:
    """Encode a list of string values for a LinkedIn people-search list facet.

    LinkedIn's people-search URL uses JSON-list encoded facets of the form
    ``["A","B"]``. This helper URL-encodes the rendered JSON so the final URL
    contains e.g. ``%5B%22F%22%5D`` for ``["F"]``.
    """
    return quote_plus(json.dumps(values, separators=(",", ":")))


# Patterns that mark the start of LinkedIn page chrome (sidebar/footer).
# Everything from the earliest match onwards is stripped.
_NOISE_MARKERS: list[re.Pattern[str]] = [
    # Footer nav links: "About" immediately followed by "Accessibility" or "Talent Solutions"
    re.compile(r"^About\n+(?:Accessibility|Talent Solutions)", re.MULTILINE),
    # Sidebar profile recommendations
    re.compile(r"^More profiles for you$", re.MULTILINE),
    # Sidebar premium upsell
    re.compile(r"^Explore premium profiles$", re.MULTILINE),
    # InMail upsell in contact info overlay
    re.compile(r"^Get up to .+ replies when you message with InMail$", re.MULTILINE),
    # Footer nav clusters in profile/posts pages
    re.compile(
        r"^(?:Careers|Privacy & Terms|Questions\?|Select language)\n+"
        r"(?:Privacy & Terms|Questions\?|Select language|Advertising|Ad Choices|"
        r"[A-Za-z]+ \([A-Za-z]+\))",
        re.MULTILINE,
    ),
]

_NOISE_LINES: list[re.Pattern[str]] = [
    re.compile(r"^(?:Play|Pause|Playback speed|Turn fullscreen on|Fullscreen)$"),
    re.compile(r"^(?:Show captions|Close modal window|Media player modal window)$"),
    re.compile(r"^(?:Loaded:.*|Remaining time.*|Stream Type.*)$"),
]


@dataclass
class ExtractedSection:
    """Text and compact references extracted from a loaded LinkedIn section."""

    text: str
    references: list[Reference]
    error: dict[str, Any] | None = None


_FEED_RSC_MARKER = "sduiid=com.linkedin.sdui.pagers.feed.mainFeed"
# Matches a LinkedIn post permalink in either plain or JSON-escaped form
# (the initial /feed/ HTML embeds the RSC flight data with \u002f for slashes,
# while paginated responses use plain slashes). Captures the slug portion so
# we can rebuild a canonical URL regardless of the source encoding.
_POST_SLUG_URL_RE = re.compile(
    r"linkedin\.com(?:\\u002[fF]|/)posts(?:\\u002[fF]|/)"
    r"(?P<slug>[A-Za-z0-9_-]+?-(?:ugcPost|activity|share)-\d+-[A-Za-z0-9_-]+)"
)
_FEED_DOCUMENT_URLS = {
    "https://www.linkedin.com/feed",
    "https://www.linkedin.com/feed/",
}


def _is_feed_payload_response(url: str) -> bool:
    """True if the response URL is one that carries `postSlugUrl` fields."""
    if _FEED_RSC_MARKER in url:
        return True
    return url.split("?", 1)[0] in _FEED_DOCUMENT_URLS


def _build_feed_references(
    raw_references: list[Any],
    captured_urls: list[str],
) -> list[Reference]:
    """Compose feed references from DOM anchors + SDUI captures.

    The feed page renders many anchors that are not post permalinks:
    sidebar widgets, profile cards, employer logos, etc. Mixing them
    into ``references["feed"]`` blurs the contract and competes with
    SDUI permalinks for the per-section cap. We keep only the
    ``feed_post`` slice from the DOM:

    - DOM anchors → ``feed_post`` entries with ``/feed/update/<urn>/``
      URLs (whatever ``classify_link`` recognises).
    - SDUI captures → ``feed_post`` entries with ``/posts/<slug>`` URLs
      for permalinks that the DOM does not surface as an anchor.

    Both are deduped on exact URL string. The two shapes pointing at
    the same underlying post will *not* collapse — ``dedupe_references``
    matches strings, not URNs. Both are valid LinkedIn permalinks, so
    consumers should treat ``feed_post`` as polymorphic on URL form;
    URN-based equivalence is left to the consumer.
    """
    refs = [
        ref
        for ref in build_references(raw_references, "feed")
        if ref["kind"] == "feed_post"
    ]
    existing = {r["url"] for r in refs}
    for sdui_url in captured_urls:
        # AGENTS.md mandates relative paths for LinkedIn references.
        # The SDUI capture carries fully-qualified URLs like
        # https://www.linkedin.com/posts/<slug>; strip the host so the
        # relative-path convention holds. ``classify_link`` does not
        # currently route ``/posts/<slug>`` paths to any kind, so we
        # bypass it for this fallback append.
        parsed = urlparse(sdui_url)
        if not parsed.path.startswith("/posts/"):
            continue
        relative = parsed.path
        if relative in existing:
            continue
        refs.append({"kind": "feed_post", "url": relative, "context": "feed"})
        existing.add(relative)
    # Cap kept in sync with _REFERENCE_CAPS["feed"] in link_metadata.py;
    # changing one without the other will drop or duplicate entries
    # silently. Matches get_feed's num_posts ceiling (Field(ge=1, le=50)).
    return dedupe_references(refs, cap=50)


async def _drain_listener_tasks(pending: list[asyncio.Task[None]]) -> None:
    """Bounded teardown for fire-and-forget response listener tasks.

    The feed scroll loop appends a read task per matching response;
    those tasks must finish (or be cancelled) before we leave the
    extractor or the event loop's "Task exception was never retrieved"
    warnings will surface unrelated errors. The caps below let a stuck
    ``resp.body()`` call burn at most three seconds of teardown budget.
    """
    if not pending:
        return
    _done, leftover = await asyncio.wait(pending, timeout=2.0)
    for task in leftover:
        task.cancel()
    try:
        await asyncio.wait_for(
            asyncio.gather(*pending, return_exceptions=True),
            timeout=1.0,
        )
    except asyncio.TimeoutError:
        logger.warning(
            "SDUI feed listener tasks did not drain after cancel; leaking %d task(s)",
            sum(1 for t in pending if not t.done()),
        )


class FilterValidationError(ValueError):
    """Invalid ``search_people`` filter input (network token / URN shape).

    Subclassing ``ValueError`` keeps backward-compatible behaviour for
    direct extractor callers (``pytest.raises(ValueError)`` matches), while
    letting the MCP tool wrapper catch this case precisely and surface the
    actionable message past ``mask_error_details``.
    """


def strip_linkedin_noise(text: str) -> str:
    """Remove LinkedIn page chrome (footer, sidebar recommendations) from innerText.

    Finds the earliest occurrence of any known noise marker and truncates there.
    """
    cleaned = _truncate_linkedin_noise(text)
    return _filter_linkedin_noise_lines(cleaned)


def _filter_linkedin_noise_lines(text: str) -> str:
    """Remove known media/control noise lines from already-truncated content."""
    filtered_lines = [
        line
        for line in text.splitlines()
        if not any(pattern.match(line.strip()) for pattern in _NOISE_LINES)
    ]
    return "\n".join(filtered_lines).strip()


def _truncate_linkedin_noise(text: str) -> str:
    """Trim known LinkedIn chrome blocks before any per-line noise filtering."""
    earliest = len(text)
    for pattern in _NOISE_MARKERS:
        match = pattern.search(text)
        if match and match.start() < earliest:
            earliest = match.start()

    return text[:earliest].strip()


class LinkedInExtractor:
    """Extracts LinkedIn page content via navigate-scroll-innerText pattern."""

    def __init__(self, page: Page):
        self._page = page

    @staticmethod
    def _normalize_body_marker(value: Any) -> str:
        """Compress body text into a short, single-line diagnostic marker."""
        if not isinstance(value, str):
            return ""
        return re.sub(r"\s+", " ", value).strip()[:200]

    @staticmethod
    def _single_section_result(
        url: str,
        section_name: str,
        text: str,
        references: list[Reference] | None = None,
    ) -> dict[str, Any]:
        """Build a standard single-section scraping response."""
        result: dict[str, Any] = {"url": url, "sections": {}}
        if text:
            result["sections"][section_name] = text
            if references:
                result["references"] = {section_name: references}
        return result

    @staticmethod
    def _message_action_result(
        url: str,
        status: str,
        message: str,
        *,
        recipient_selected: bool = False,
        sent: bool = False,
    ) -> dict[str, Any]:
        """Build a structured response for the send_message tool."""
        return {
            "url": url,
            "status": status,
            "message": message,
            "recipient_selected": recipient_selected,
            "sent": sent,
        }

    async def _log_navigation_failure(
        self,
        target_url: str,
        wait_until: str,
        navigation_error: Exception,
        hops: list[str],
    ) -> None:
        """Emit structured diagnostics for a failed target navigation."""
        try:
            title = await self._page.title()
        except Exception:
            title = ""

        try:
            auth_barrier = await detect_auth_barrier(self._page)
        except Exception:
            auth_barrier = None

        try:
            remember_me_visible = (
                await self._page.locator("#rememberme-div").count()
            ) > 0
        except Exception:
            remember_me_visible = False

        try:
            body_marker = self._normalize_body_marker(
                await self._page.evaluate("() => document.body?.innerText || ''")
            )
        except Exception:
            body_marker = ""

        logger.warning(
            "Navigation to %s failed (wait_until=%s, error=%s). "
            "current_url=%s title=%r auth_barrier=%s remember_me=%s hops=%s body_marker=%r",
            target_url,
            wait_until,
            navigation_error,
            self._page.url,
            title,
            auth_barrier,
            remember_me_visible,
            hops,
            body_marker,
        )

    async def _raise_if_auth_barrier(
        self,
        url: str,
        *,
        navigation_error: Exception | None = None,
    ) -> None:
        """Raise an auth error when LinkedIn shows login/account-picker UI."""
        barrier = await detect_auth_barrier(self._page)
        if not barrier:
            return

        logger.warning("Authentication barrier detected on %s: %s", url, barrier)
        message = (
            "LinkedIn requires interactive re-authentication. "
            "Run with --login and complete the account selection/sign-in flow."
        )
        if navigation_error is not None:
            raise AuthenticationError(message) from navigation_error
        raise AuthenticationError(message)

    async def _goto_with_auth_checks(
        self,
        url: str,
        *,
        wait_until: WaitUntil = "domcontentloaded",
        allow_remember_me: bool = True,
    ) -> None:
        """Navigate to a LinkedIn page and fail fast on auth barriers."""
        hops: list[str] = []
        listener_registered = False

        def record_navigation(frame: Any) -> None:
            if frame != self._page.main_frame:
                return
            frame_url = getattr(frame, "url", "")
            if frame_url and (not hops or hops[-1] != frame_url):
                hops.append(frame_url)

        def unregister_navigation_listener() -> None:
            nonlocal listener_registered
            if not listener_registered:
                return
            self._page.remove_listener("framenavigated", record_navigation)
            listener_registered = False

        self._page.on("framenavigated", record_navigation)
        listener_registered = True
        try:
            await record_page_trace(
                self._page,
                "extractor-before-goto",
                extra={"target_url": url, "wait_until": wait_until},
            )
            try:
                await self._page.goto(url, wait_until=wait_until, timeout=30000)
                await stabilize_navigation(f"goto {url}", logger)
                await record_page_trace(
                    self._page,
                    "extractor-after-goto",
                    extra={"target_url": url, "wait_until": wait_until},
                )
            except Exception as exc:
                if allow_remember_me and await resolve_remember_me_prompt(self._page):
                    await stabilize_navigation(
                        f"remember-me resolution for {url}", logger
                    )
                    await record_page_trace(
                        self._page,
                        "extractor-navigation-error-before-remember-me-retry",
                        extra={
                            "target_url": url,
                            "wait_until": wait_until,
                            "error": f"{type(exc).__name__}: {exc}",
                            "hops": hops,
                        },
                    )
                    await record_page_trace(
                        self._page,
                        "extractor-after-remember-me",
                        extra={
                            "target_url": url,
                            "error": f"{type(exc).__name__}: {exc}",
                        },
                    )
                    unregister_navigation_listener()
                    await self._goto_with_auth_checks(
                        url,
                        wait_until=wait_until,
                        allow_remember_me=False,
                    )
                    return
                await record_page_trace(
                    self._page,
                    "extractor-navigation-error",
                    extra={
                        "target_url": url,
                        "wait_until": wait_until,
                        "error": f"{type(exc).__name__}: {exc}",
                        "hops": hops,
                    },
                )
                await self._log_navigation_failure(url, wait_until, exc, hops)
                await self._raise_if_auth_barrier(url, navigation_error=exc)
                raise

            barrier = await detect_auth_barrier_quick(self._page)
            if not barrier:
                return

            if allow_remember_me and await resolve_remember_me_prompt(self._page):
                await stabilize_navigation(f"remember-me retry for {url}", logger)
                await record_page_trace(
                    self._page,
                    "extractor-after-remember-me-retry",
                    extra={"target_url": url, "barrier": barrier},
                )
                unregister_navigation_listener()
                await self._goto_with_auth_checks(
                    url,
                    wait_until=wait_until,
                    allow_remember_me=False,
                )
                return

            await record_page_trace(
                self._page,
                "extractor-auth-barrier",
                extra={"target_url": url, "barrier": barrier},
            )
            logger.warning("Authentication barrier detected on %s: %s", url, barrier)
            raise AuthenticationError(
                "LinkedIn requires interactive re-authentication. "
                "Run with --login and complete the account selection/sign-in flow."
            )
        finally:
            unregister_navigation_listener()

    async def _navigate_to_page(self, url: str) -> None:
        """Navigate to a LinkedIn page and fail fast on auth barriers."""
        logger.debug("_navigate_to_page: target=%s", url)
        await self._goto_with_auth_checks(url)

    # ------------------------------------------------------------------
    # Generic browser helpers for LLM-driven connection flow
    # ------------------------------------------------------------------

    async def get_page_text(self) -> str:
        """Extract innerText from the main content area of the current page."""
        text = await self._page.evaluate(
            "() => (document.querySelector('main') || document.body).innerText || ''"
        )
        return strip_linkedin_noise(text) if isinstance(text, str) else ""

    async def click_button_by_text(
        self, text: str, *, scope: str = "main", timeout: int = 5000
    ) -> bool:
        """Click the first button/link whose visible text is exactly *text*.

        Uses a regex filter for exact matching to avoid substring false
        positives (e.g. "Connect" matching "connections").
        Returns True if clicked, False if no match found.
        """
        matches = (
            self._page.locator(scope)
            .locator("button, a, [role='button']")
            .filter(has_text=re.compile(rf"^{re.escape(text)}$"))
        )
        count = await matches.count()
        logger.debug("click_button_by_text(%r): %d matches in %s", text, count, scope)
        if count == 0:
            return False
        target = matches.first
        try:
            await target.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            logger.debug("Scroll failed for button '%s'", text, exc_info=True)
        try:
            await target.click(timeout=timeout)
            return True
        except Exception:
            logger.debug("Click failed for button '%s'", text, exc_info=True)
            return False

    async def _dialog_is_open(self, *, timeout: int = 1000) -> bool:
        """Return whether a dialog is currently open (structural check)."""
        locator = self._page.locator(_DIALOG_SELECTOR)
        try:
            if await locator.count() == 0:
                return False
            await locator.first.wait_for(state="visible", timeout=timeout)
            return True
        except Exception:
            return False

    async def _click_dialog_primary_button(self, *, timeout: int = 5000) -> bool:
        """Click the last (primary/Send) button in the open dialog.

        LinkedIn consistently places the primary action as the last button.
        Returns False (rather than raising) when the click is intercepted or
        times out, so callers can fall back to a keyboard submit.
        """
        buttons = self._page.locator(
            f"{_DIALOG_SELECTOR} button, {_DIALOG_SELECTOR} [role='button']"
        )
        count = await buttons.count()
        if count == 0:
            return False
        try:
            await buttons.nth(count - 1).click(timeout=timeout)
            return True
        except Exception:
            logger.debug("Primary dialog button click failed", exc_info=True)
            return False

    async def _fill_dialog_textarea(self, value: str, *, timeout: int = 5000) -> bool:
        """Fill the first textarea inside the open dialog (structural)."""
        locator = self._page.locator(_DIALOG_TEXTAREA_SELECTOR).first
        try:
            if await self._page.locator(_DIALOG_TEXTAREA_SELECTOR).count() == 0:
                return False
            await locator.fill(value, timeout=timeout)
            return True
        except Exception:
            return False

    async def _dismiss_dialog(self) -> None:
        """Dismiss any open dialog via Escape key (structural)."""
        await self._page.keyboard.press("Escape")
        try:
            await self._page.wait_for_selector(
                _DIALOG_SELECTOR, state="hidden", timeout=3000
            )
        except PlaywrightTimeoutError:
            pass

    async def _open_more_menu(self) -> bool:
        """Open the profile's More (three-dot) menu in a locale-independent way.

        Locates the More button structurally as ``actionRoot
        button[aria-expanded]`` — the action-root walk discriminates the
        profile More button from any other More-labelled buttons elsewhere
        on the page (notably the video-player More on profiles with
        background videos), and ``aria-expanded`` distinguishes the menu
        opener from primary action buttons (which carry ``aria-label``
        instead). Returns True iff the click landed and a ``[role='menu']``
        became visible. The caller is expected to follow up with
        ``_read_action_signals`` to scan the now-rendered menu items for
        the vanityName invite anchor; this helper does not classify menu
        contents itself.
        """
        try:
            clicked = await self._page.evaluate(_OPEN_MORE_BUTTON_JS)
        except Exception:
            logger.debug("More button click via JS failed", exc_info=True)
            return False
        if not clicked:
            return False
        try:
            await self._page.wait_for_selector("[role='menu']", timeout=3000)
            return True
        except PlaywrightTimeoutError:
            logger.debug("More menu did not appear after click")
            return False

    async def _locator_is_visible(self, selector: str, *, timeout: int = 2000) -> bool:
        """Return whether the first matching locator is visible."""
        locator = self._page.locator(selector)
        try:
            if await locator.count() == 0:
                return False
        except Exception:
            return False

        first = locator.first
        try:
            await first.wait_for(state="visible", timeout=timeout)
            return True
        except PlaywrightTimeoutError:
            return False
        except Exception:
            try:
                return bool(await first.is_visible())
            except Exception:
                return False

    async def _click_first(self, selector: str, *, timeout: int = 5000) -> None:
        """Click the first visible locator that matches a selector."""
        target = self._page.locator(selector).first
        try:
            await target.scroll_into_view_if_needed(timeout=timeout)
        except Exception:
            logger.debug("Could not scroll %s into view", selector, exc_info=True)
        await target.click(timeout=timeout)

    async def _wait_for_main_text(
        self,
        *,
        minimum_length: int = 100,
        timeout: int = 10000,
        log_context: str,
    ) -> None:
        """Wait for main content to populate enough text to scrape."""
        try:
            await self._page.wait_for_function(
                """({ minimumLength }) => {
                    const main = document.querySelector('main');
                    if (!main) return false;
                    return main.innerText.length > minimumLength;
                }""",
                arg={"minimumLength": minimum_length},
                timeout=timeout,
            )
        except PlaywrightTimeoutError:
            logger.debug("%s content did not appear", log_context)

    async def _scroll_main_scrollable_region(
        self,
        *,
        position: Literal["top", "bottom"],
        attempts: int,
        pause_time: float = 0.5,
    ) -> None:
        """Scroll the largest scrollable region inside main when one exists."""
        for _ in range(attempts):
            await self._page.evaluate(
                """({ position }) => {
                    const main = document.querySelector('main');
                    if (!main) return false;

                    const isScrollable = element => {
                        const style = window.getComputedStyle(element);
                        return (
                            (style.overflowY === 'auto' || style.overflowY === 'scroll') &&
                            element.scrollHeight > element.clientHeight + 20
                        );
                    };

                    const candidates = [main, ...main.querySelectorAll('*')].filter(isScrollable);
                    const target = candidates.sort(
                        (left, right) => right.scrollHeight - left.scrollHeight
                    )[0] || main;
                    target.scrollTop = position === 'top' ? 0 : target.scrollHeight;
                    return true;
                }""",
                {"position": position},
            )
            await asyncio.sleep(pause_time)

    async def extract_feed(
        self,
        num_posts: int = 10,
    ) -> ExtractedSection:
        """Scrape the LinkedIn home feed, scrolling until *num_posts* are loaded."""
        try:
            return await self._extract_feed_once(num_posts)
        except LinkedInScraperException:
            raise
        except Exception as e:
            logger.warning("Failed to extract feed: %s", e)
            return ExtractedSection(
                text="",
                references=[],
                error=build_issue_diagnostics(e, context="extract_feed"),
            )

    async def _extract_feed_once(
        self,
        num_posts: int,
    ) -> ExtractedSection:
        """Single attempt: navigate, scroll until post count, extract."""
        url = "https://www.linkedin.com/feed/"

        # Post permalinks live in the SDUI pagination response (field:
        # "postSlugUrl"). The initial /feed/ HTML embeds the same data in
        # an RSC flight payload. Listen for both during the whole scroll
        # loop. ``seen_urls`` doubles as the locale-independent scroll
        # progress signal, replacing the previous "Feed post" innerText
        # marker that broke on non-English UIs.
        captured_urls: list[str] = []
        seen_urls: set[str] = set()
        pending_reads: list[asyncio.Task[None]] = []

        def _handle_response(resp: Any) -> None:
            if not _is_feed_payload_response(resp.url):
                return

            async def _read() -> None:
                try:
                    body = await resp.body()
                except Exception:
                    return
                if not body:
                    return
                text = body.decode("utf-8", errors="replace")
                for match in _POST_SLUG_URL_RE.finditer(text):
                    post_url = f"https://www.linkedin.com/posts/{match.group('slug')}"
                    if post_url not in seen_urls:
                        seen_urls.add(post_url)
                        captured_urls.append(post_url)

            pending_reads.append(asyncio.create_task(_read()))

        self._page.on("response", _handle_response)
        try:
            return await self._extract_feed_body(
                url, num_posts, captured_urls, pending_reads
            )
        finally:
            try:
                self._page.remove_listener("response", _handle_response)
            except Exception:
                pass
            await _drain_listener_tasks(pending_reads)

    async def _extract_feed_body(
        self,
        url: str,
        num_posts: int,
        captured_urls: list[str],
        pending_reads: list[asyncio.Task[None]],
    ) -> ExtractedSection:
        await self._navigate_to_page(url)
        await detect_rate_limit(self._page)

        try:
            await self._page.wait_for_selector("main")
        except PlaywrightTimeoutError:
            logger.debug("No <main> element found on %s", url)

        await handle_modal_close(self._page)

        try:
            await self._page.wait_for_function(
                """() => {
                    const main = document.querySelector('main');
                    if (!main) return false;
                    return main.innerText.length > 200;
                }""",
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            logger.debug("Feed content did not appear on %s", url)

        # The feed has its own scroll container — window.scrollTo is a no-op.
        # mouse.wheel over the viewport center triggers the real scroll.
        _MAX_SCROLLS = 12
        _MAX_STALE = 3
        _BATCH_WAIT = 6.0
        _WHEEL_DELTA = 2000
        _IN_LOOP_DRAIN_TIMEOUT = 1.0
        stale_count = 0

        viewport = self._page.viewport_size or {"width": 1280, "height": 720}
        cx, cy = viewport["width"] // 2, viewport["height"] // 2
        await self._page.mouse.move(cx, cy)

        for i in range(_MAX_SCROLLS):
            count = len(captured_urls)
            logger.debug("Feed scroll %d: %d permalinks captured", i, count)
            if count >= num_posts:
                break

            await self._page.mouse.wheel(0, _WHEEL_DELTA)

            new_count = count
            for _ in range(int(_BATCH_WAIT)):
                await asyncio.sleep(1.0)
                # Drain in-flight response reads so captured_urls reflects
                # everything Playwright already delivered. Without this,
                # the count comparison races: the wheel fires a network
                # response, the listener creates a read task, and the loop
                # sleeps and re-checks before _read() finishes appending —
                # producing false-stale verdicts.
                if pending_reads:
                    done, _still = await asyncio.wait(
                        pending_reads, timeout=_IN_LOOP_DRAIN_TIMEOUT
                    )
                    if done:
                        # Surface unexpected exceptions. _read() catches
                        # expected playwright errors, but a parser bug
                        # would otherwise vanish into the loop. Log them
                        # rather than raising so a single bad response
                        # doesn't abort the whole scroll session.
                        for result in await asyncio.gather(
                            *done, return_exceptions=True
                        ):
                            if isinstance(result, BaseException):
                                logger.warning(
                                    "Unhandled error in feed _read task: %r",
                                    result,
                                )
                    pending_reads[:] = [t for t in pending_reads if not t.done()]
                new_count = len(captured_urls)
                if new_count > count:
                    break

            if new_count > count:
                stale_count = 0
            else:
                stale_count += 1
                logger.debug(
                    "Feed stale scroll %d/%d (still at %d permalinks)",
                    stale_count,
                    _MAX_STALE,
                    new_count,
                )
                if stale_count >= _MAX_STALE:
                    logger.debug("Feed stopped producing new posts")
                    break

        # Give any in-flight response reads a beat to finish recording URLs.
        await asyncio.sleep(0.2)

        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]

        if not raw:
            return ExtractedSection(text="", references=[])
        truncated = _truncate_linkedin_noise(raw)
        if not truncated and raw.strip():
            logger.warning(
                "Page %s returned only LinkedIn chrome (likely rate-limited)", url
            )
            return ExtractedSection(text=_RATE_LIMITED_MSG, references=[])
        cleaned = _filter_linkedin_noise_lines(truncated)
        return ExtractedSection(
            text=cleaned,
            references=_build_feed_references(raw_result["references"], captured_urls),
        )

    async def extract_page(
        self,
        url: str,
        section_name: str,
        max_scrolls: int | None = None,
    ) -> ExtractedSection:
        """Navigate to a URL, scroll to load lazy content, and extract innerText.

        Retries once after a backoff when the page returns only LinkedIn chrome
        (sidebar/footer noise with no actual content), which indicates a soft
        rate limit.

        Raises LinkedInScraperException subclasses (rate limit, auth, etc.).
        Returns _RATE_LIMITED_MSG sentinel when soft-rate-limited after retry.
        Returns empty string for unexpected non-domain failures (error isolation).
        """
        try:
            result = await self._extract_page_once(url, section_name, max_scrolls)
            if result.text != _RATE_LIMITED_MSG:
                return result

            # Retry once after backoff
            logger.info("Retrying %s after %.0fs backoff", url, _RATE_LIMIT_RETRY_DELAY)
            await asyncio.sleep(_RATE_LIMIT_RETRY_DELAY)
            return await self._extract_page_once(url, section_name, max_scrolls)

        except LinkedInScraperException:
            raise
        except Exception as e:
            logger.warning("Failed to extract page %s: %s", url, e)
            return ExtractedSection(
                text="",
                references=[],
                error=build_issue_diagnostics(
                    e,
                    context="extract_page",
                    target_url=url,
                    section_name=section_name,
                ),
            )

    async def _extract_page_once(
        self,
        url: str,
        section_name: str,
        max_scrolls: int | None = None,
    ) -> ExtractedSection:
        """Single attempt to navigate, scroll, and extract innerText."""
        await self._navigate_to_page(url)
        return await self._extract_loaded_section(url, section_name, max_scrolls)

    async def _extract_loaded_section(
        self,
        url: str,
        section_name: str,
        max_scrolls: int | None = None,
    ) -> ExtractedSection:
        """Run the post-navigation extraction pipeline on the current page.

        Assumes ``self._page`` already points at ``url`` (or its post-redirect
        equivalent). Performs rate-limit detection, modal dismissal, lazy-load
        scrolling, innerText extraction, noise truncation, and reference
        building — everything ``_extract_page_once`` does after the goto.
        """
        await detect_rate_limit(self._page)

        # Wait for main content to render
        try:
            await self._page.wait_for_selector("main")
        except PlaywrightTimeoutError:
            logger.debug("No <main> element found on %s", url)

        # Dismiss any modals blocking content
        await handle_modal_close(self._page)

        # Activity feed pages lazy-load post content after the tab header
        is_activity = "/recent-activity/" in url
        if is_activity:
            try:
                await self._page.wait_for_function(
                    """() => {
                        const main = document.querySelector('main');
                        if (!main) return false;
                        return main.innerText.length > 200;
                    }""",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                logger.debug("Activity feed content did not appear on %s", url)

        # Search results pages load a placeholder first then fill in results
        # via JavaScript. Wait for actual content before extracting.
        is_search = "/search/results/" in url
        if is_search:
            try:
                await self._page.wait_for_function(
                    """() => {
                        const main = document.querySelector('main');
                        if (!main) return false;
                        return main.innerText.length > 100;
                    }""",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                logger.debug("Search results content did not appear on %s", url)

        # Company people pages (/company/<slug>/people/) initially render only
        # the company header in <main>; the employee listing hydrates later
        # via JS. Wait until at least one /in/ profile anchor appears inside
        # <main> so innerText extraction sees the actual list. Use a 5s
        # timeout instead of the 10s pattern shared with is_search/is_details
        # — empty/restricted listings are common here (small companies,
        # privacy settings) and a full 10s wait per call adds up.
        is_company_people = "/company/" in url and "/people/" in url
        if is_company_people:
            try:
                await self._page.wait_for_function(
                    """() => {
                        const main = document.querySelector('main');
                        if (!main) return false;
                        return main.querySelectorAll('a[href*="/in/"]').length > 0;
                    }""",
                    timeout=5000,
                )
            except PlaywrightTimeoutError:
                logger.debug("Company people listing did not appear on %s", url)

        # Profile detail pages (/details/experience/, /details/education/, etc.)
        # initially render sidebar recommendations into <main> while the section
        # panel loads asynchronously. Wait until the panel replaces the sidebar.
        # The sidebar placeholder starts with "Load more" or "More profiles for you".
        is_details = "/details/" in url
        if is_details:
            try:
                await self._page.wait_for_function(
                    """() => {
                        const main = document.querySelector('main');
                        if (!main) return false;
                        const text = main.innerText.trimStart();
                        return !text.startsWith('Load more')
                            && !text.startsWith('More profiles for you')
                            && !text.startsWith('Explore premium profiles');
                    }""",
                    timeout=10000,
                )
            except PlaywrightTimeoutError:
                logger.debug("Detail section content did not appear on %s", url)

        # Detail pages paginate with a "Show more" button inside <main>, not scroll.
        # Click it until it disappears or the budget runs out.
        if is_details:
            max_clicks = max_scrolls if max_scrolls is not None else 5
            for i in range(max_clicks):
                button = self._page.locator("main button").filter(
                    has_text=re.compile(r"^Show (more|all)\b", re.IGNORECASE)
                )
                try:
                    if await button.count() == 0:
                        logger.debug("No 'Show more' button after %d clicks", i)
                        break
                    target = button.first
                    if not await target.is_visible():
                        break
                    await target.scroll_into_view_if_needed(timeout=2000)
                    await target.click(timeout=2000)
                    await asyncio.sleep(1.0)
                except PlaywrightTimeoutError:
                    logger.debug("Show more click timed out after %d clicks", i)
                    break
                except Exception as e:
                    logger.debug("Show more click failed: %s", e)
                    break

        # Scroll to trigger lazy loading
        if is_activity:
            scrolls = max_scrolls if max_scrolls is not None else 10
            await scroll_to_bottom(self._page, pause_time=1.0, max_scrolls=scrolls)
        else:
            scrolls = max_scrolls if max_scrolls is not None else 5
            await scroll_to_bottom(self._page, pause_time=0.5, max_scrolls=scrolls)

        # Extract text from main content area
        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]

        if not raw:
            return ExtractedSection(text="", references=[])
        truncated = _truncate_linkedin_noise(raw)
        if not truncated and raw.strip():
            logger.warning(
                "Page %s returned only LinkedIn chrome (likely rate-limited)", url
            )
            return ExtractedSection(text=_RATE_LIMITED_MSG, references=[])
        cleaned = _filter_linkedin_noise_lines(truncated)
        return ExtractedSection(
            text=cleaned,
            references=build_references(raw_result["references"], section_name),
        )

    async def _extract_overlay(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Extract content from an overlay/modal page (e.g. contact info).

        LinkedIn renders contact info as a native <dialog> element.
        Falls back to `<main>` if no dialog is found.

        Retries once after a backoff when the overlay returns only LinkedIn
        chrome (noise), mirroring `extract_page` behavior.
        """
        try:
            result = await self._extract_overlay_once(url, section_name)
            if result.text != _RATE_LIMITED_MSG:
                return result

            logger.info(
                "Retrying overlay %s after %.0fs backoff",
                url,
                _RATE_LIMIT_RETRY_DELAY,
            )
            await asyncio.sleep(_RATE_LIMIT_RETRY_DELAY)
            return await self._extract_overlay_once(url, section_name)

        except LinkedInScraperException:
            raise
        except Exception as e:
            logger.warning("Failed to extract overlay %s: %s", url, e)
            return ExtractedSection(
                text="",
                references=[],
                error=build_issue_diagnostics(
                    e,
                    context="extract_overlay",
                    target_url=url,
                    section_name=section_name,
                ),
            )

    async def _extract_overlay_once(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Single attempt to extract content from an overlay/modal page."""
        await self._navigate_to_page(url)
        await detect_rate_limit(self._page)

        # Wait for the dialog/modal to render (LinkedIn uses native <dialog>)
        try:
            await self._page.wait_for_selector("dialog[open], .artdeco-modal__content")
        except PlaywrightTimeoutError:
            logger.debug("No modal overlay found on %s, falling back to main", url)

        # NOTE: Do NOT call handle_modal_close() here — the contact-info
        # overlay *is* a dialog/modal. Dismissing it would destroy the
        # content before the JS evaluation below can read it.

        raw_result = await self._extract_root_content(
            ["dialog[open]", ".artdeco-modal__content", "main"],
        )
        raw = raw_result["text"]

        if not raw:
            return ExtractedSection(text="", references=[])
        truncated = _truncate_linkedin_noise(raw)
        if not truncated and raw.strip():
            logger.warning(
                "Overlay %s returned only LinkedIn chrome (likely rate-limited)",
                url,
            )
            return ExtractedSection(text=_RATE_LIMITED_MSG, references=[])
        cleaned = _filter_linkedin_noise_lines(truncated)
        return ExtractedSection(
            text=cleaned,
            references=build_references(raw_result["references"], section_name),
        )

    async def scrape_person(
        self,
        username: str,
        requested: set[str],
        callbacks: ProgressCallback | None = None,
        max_scrolls: int | None = None,
        *,
        main_profile_already_loaded: bool = False,
    ) -> dict[str, Any]:
        """Scrape a person profile with configurable sections.

        When ``main_profile_already_loaded`` is True and ``self._page`` is on
        the exact profile root for ``username``, the ``main_profile`` section
        is extracted from the current page without re-navigating. Falls back
        to ``extract_page`` if the URL drifts or the reuse path returns the
        soft-rate-limit sentinel (preserving the retry semantics of
        ``extract_page``).

        Returns:
            {url, sections: {name: text}, profile_urn?: str}
        """
        requested = requested | {"main_profile"}
        base_url = f"https://www.linkedin.com/in/{username}"
        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        profile_urn: str | None = None

        requested_ordered = [
            (name, suffix, is_overlay)
            for name, (suffix, is_overlay) in PERSON_SECTIONS.items()
            if name in requested
        ]
        total = len(requested_ordered)

        if callbacks:
            await callbacks.on_start("person profile", base_url)

        try:
            for i, (section_name, suffix, is_overlay) in enumerate(requested_ordered):
                if i > 0:
                    await asyncio.sleep(_NAV_DELAY)

                url = base_url + suffix
                try:
                    can_reuse_main = (
                        section_name == "main_profile"
                        and main_profile_already_loaded
                        and urlparse(self._page.url).path.rstrip("/")
                        == f"/in/{username}"
                    )
                    if can_reuse_main:
                        extracted = await self._extract_loaded_section(
                            url,
                            section_name=section_name,
                            max_scrolls=max_scrolls,
                        )
                        if extracted.text == _RATE_LIMITED_MSG:
                            logger.info(
                                "Reuse path soft-rate-limited; falling back "
                                "to extract_page for retry parity"
                            )
                            extracted = await self.extract_page(
                                url,
                                section_name=section_name,
                                max_scrolls=max_scrolls,
                            )
                    elif is_overlay:
                        extracted = await self._extract_overlay(
                            url, section_name=section_name
                        )
                    else:
                        extracted = await self.extract_page(
                            url,
                            section_name=section_name,
                            max_scrolls=max_scrolls,
                        )

                    if extracted.text and extracted.text != _RATE_LIMITED_MSG:
                        sections[section_name] = extracted.text
                        if extracted.references:
                            references[section_name] = extracted.references
                    elif extracted.error:
                        section_errors[section_name] = extracted.error

                    if section_name == "main_profile" and profile_urn is None:
                        profile_urn = await self._extract_profile_urn()
                except LinkedInScraperException:
                    raise
                except Exception as e:
                    logger.warning("Error scraping section %s: %s", section_name, e)
                    section_errors[section_name] = build_issue_diagnostics(
                        e,
                        context="scrape_person",
                        target_url=url,
                        section_name=section_name,
                    )

                # "Scraped" = processed/attempted, not necessarily successful.
                # Per-section failures are captured in section_errors.
                if callbacks:
                    percent = round((i + 1) / total * 95)
                    await callbacks.on_progress(
                        f"Scraped {section_name} ({i + 1}/{total})", percent
                    )
        except LinkedInScraperException as e:
            if callbacks:
                await callbacks.on_error(e)
            raise

        result: dict[str, Any] = {
            "url": f"{base_url}/",
            "sections": sections,
        }
        if profile_urn:
            result["profile_urn"] = profile_urn
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors

        if callbacks:
            await callbacks.on_complete("person profile", result)

        return result

    async def get_my_profile(
        self,
        sections: set[str] | None = None,
        callbacks: ProgressCallback | None = None,
        max_scrolls: int | None = None,
    ) -> dict[str, Any]:
        """Scrape the authenticated user's own LinkedIn profile.

        Navigates to /in/me/ and resolves the redirect to obtain the real
        username before scraping, so result["url"] reflects the actual profile
        URL rather than /in/me/.

        Returns:
            {url, sections: {name: text}}
        """
        await self._navigate_to_page("https://www.linkedin.com/in/me/")
        real_url = self._page.url  # post-redirect, e.g. /in/johndoe/
        match = re.search(r"/in/([^/?#]+)", real_url)
        username = match.group(1) if match else "me"
        logger.debug("get_my_profile resolved username=%r from %s", username, real_url)

        return await self.scrape_person(
            username,
            sections if sections is not None else {"main_profile"},
            callbacks=callbacks,
            max_scrolls=max_scrolls,
            main_profile_already_loaded=True,
        )

    async def _read_action_signals(self, username: str) -> ActionSignals:
        """Read locale-independent structural signals for a profile's
        relationship state.

        Detection uses URL patterns and ARIA attribute presence only — never
        text values — per the AGENTS.md Scraping Rules. The vanityName invite
        anchor is searched document-wide because LinkedIn renders the More
        menu's contents in a portal-mounted ``[role='menu']`` outside ``<main>``;
        the URL is uniquely scoped to the target user, so document-wide
        search introduces no false positives. The compose anchor used for
        action-root discovery is scoped to ``<main>`` to avoid the
        portal-rendered "Send profile in a message" anchor that appears
        inside the More menu after click.
        """
        data = await self._page.evaluate(_ACTION_SIGNALS_JS, username)
        if not isinstance(data, dict):
            return ActionSignals(
                has_invite_anchor=False,
                has_compose_anchor_in_action_root=False,
                has_edit_intro_anchor=False,
                has_labeled_action_button=False,
                has_labeled_action_anchor=False,
            )
        return ActionSignals(
            has_invite_anchor=bool(data.get("hasInvite")),
            has_compose_anchor_in_action_root=bool(data.get("hasComposeInActionRoot")),
            has_edit_intro_anchor=bool(data.get("hasEditIntro")),
            has_labeled_action_button=bool(data.get("hasLabeledActionButton")),
            has_labeled_action_anchor=bool(data.get("hasLabeledActionAnchor")),
        )

    async def _submit_invite_dialog(self, note: str | None) -> tuple[bool, bool]:
        """Submit the invite dialog opened by the custom-invite deeplink.

        Returns (submitted, note_sent). All interaction uses structural
        selectors and positional indexing — no localized text matching.
        Owns dialog cleanup: the dialog is dismissed on every failure path,
        callers must not dismiss again.
        """
        if not await self._dialog_is_open(timeout=5000):
            return False, False

        note_sent = False
        if note:
            textarea_count = await self._page.locator(_DIALOG_TEXTAREA_SELECTOR).count()
            if textarea_count == 0:
                # Reveal the note textarea via the secondary action. The note
                # layout exposes three buttons (dismiss, secondary, primary);
                # require all three before clicking the second-to-last so we
                # never click dismiss on a no-note layout.
                buttons = self._page.locator(
                    f"{_DIALOG_SELECTOR} button, {_DIALOG_SELECTOR} [role='button']"
                )
                btn_count = await buttons.count()
                if btn_count >= 3:
                    await buttons.nth(btn_count - 2).click()
                    try:
                        await self._page.wait_for_selector(
                            _DIALOG_TEXTAREA_SELECTOR,
                            state="visible",
                            timeout=3000,
                        )
                    except PlaywrightTimeoutError:
                        logger.debug("Note textarea did not appear")

            note_sent = await self._fill_dialog_textarea(note)
            if not note_sent:
                await self._dismiss_dialog()
                return False, False

        sent = await self._click_dialog_primary_button()
        if not sent:
            # Fallback: focus the primary button positionally so a subsequent
            # Enter targets it instead of a focused textarea (where Enter
            # would just insert a newline).
            buttons = self._page.locator(
                f"{_DIALOG_SELECTOR} button, {_DIALOG_SELECTOR} [role='button']"
            )
            btn_count = await buttons.count()
            if btn_count > 0:
                try:
                    await buttons.nth(btn_count - 1).focus()
                    await self._page.keyboard.press("Enter")
                    sent = not await self._dialog_is_open(timeout=2000)
                except Exception:
                    logger.debug("Keyboard submit fallback failed", exc_info=True)
            if not sent:
                await self._dismiss_dialog()
                return False, note_sent

        try:
            await self._page.wait_for_selector(
                _DIALOG_SELECTOR, state="hidden", timeout=5000
            )
        except PlaywrightTimeoutError:
            logger.debug("Invite dialog did not close after submit")

        return True, note_sent

    async def connect_with_person(
        self,
        username: str,
        *,
        note: str | None = None,
    ) -> dict[str, Any]:
        """Send a LinkedIn connection request or accept an incoming one.

        Detection is locale-independent: classification uses URL patterns
        (vanityName invite anchor, edit-intro anchor) and ARIA-attribute
        presence on top-card buttons (`aria-label` for primary actions,
        `aria-expanded` for the More-menu opener). The deeplink-submit
        path is gated strictly on `has_invite_anchor=True` *after* the
        optional More-menu retry, so Pending and follow-only profiles
        cannot trigger a write. Sending itself uses the
        ``/preload/custom-invite/?vanityName=`` deeplink, which works
        whether the user-visible Connect button is in the action bar
        or buried under the More menu.
        """
        from linkedin_mcp_server.scraping.connection import detect_connection_state

        url = f"https://www.linkedin.com/in/{username}/"

        profile = await self.scrape_person(username, {"main_profile"})
        page_text = profile.get("sections", {}).get("main_profile", "")
        if not page_text:
            return _connection_result(
                url, "unavailable", "Could not read profile page."
            )

        signals = await self._read_action_signals(username)
        state = detect_connection_state(page_text, signals)
        logger.info(
            "Connection signals for %s: state=%s signals=%s", username, state, signals
        )

        if state == "self_profile":
            return _connection_result(
                url,
                "connect_unavailable",
                "Cannot send a connection request to your own profile.",
                profile=page_text,
            )
        if state == "already_connected":
            return _connection_result(
                url,
                "already_connected",
                "You are already connected with this profile.",
                profile=page_text,
            )
        if state == "pending":
            return _connection_result(
                url,
                "pending",
                "A connection request is already pending for this profile.",
                profile=page_text,
            )

        if state == "incoming_request":
            # TODO(locale): replace text-based Accept click with a
            # structural identifier — needs a live probe against a real
            # incoming-request profile (we have none to test against).
            # Tracked as a documented escape-hatch per AGENTS.md.
            clicked = await self.click_button_by_text("Accept", scope="main")
            if not clicked:
                return _connection_result(
                    url,
                    "send_failed",
                    "Could not find or click the Accept button.",
                    profile=page_text,
                )
            verified = await self.scrape_person(username, {"main_profile"})
            verified_text = verified.get("sections", {}).get("main_profile", "")
            verified_signals = await self._read_action_signals(username)
            verified_state = detect_connection_state(verified_text, verified_signals)
            if verified_state != "already_connected":
                return _connection_result(
                    url,
                    "send_failed",
                    "Accepted, but the profile did not transition to 1st-degree.",
                    profile=verified_text or page_text,
                )
            return _connection_result(
                url,
                "accepted",
                "Connection request accepted.",
                profile=verified_text,
            )

        # Follow-only profiles may have Connect hidden under the More menu
        # (high-follower / creator-mode profiles). Try opening it and
        # re-reading signals; if the vanityName invite anchor surfaces in
        # the menu, we can proceed with the deeplink. (The
        # has_invite_anchor=False guard is implicit: detect_connection_state
        # only returns "follow_only" after the has_invite_anchor branch
        # has already failed, so reaching this branch already implies it.)
        if state == "follow_only":
            opened = await self._open_more_menu()
            if opened:
                signals = await self._read_action_signals(username)
                # Close the menu before any subsequent navigation so it
                # doesn't intercept the upcoming page transition.
                try:
                    await self._page.keyboard.press("Escape")
                except Exception:
                    logger.debug("Escape after More-menu reread failed", exc_info=True)
                logger.info("Post-More signals for %s: signals=%s", username, signals)

        # Write-gate: the deeplink fires only when we have a vanityName
        # invite anchor at this point. A `follow_only` outcome with no
        # invite anchor (Pending profile, restricted profile, or
        # genuinely follow-only) returns connect_unavailable without
        # navigating to the invite URL — protects against accidental
        # re-invitation of Pending profiles.
        if not signals.has_invite_anchor:
            return _connection_result(
                url,
                "connect_unavailable",
                "LinkedIn did not expose a usable Connect action for this profile.",
                profile=page_text,
            )

        invite_url = (
            "https://www.linkedin.com/preload/custom-invite/"
            f"?vanityName={quote_plus(username)}"
        )
        await self._navigate_to_page(invite_url)

        submitted, note_sent = await self._submit_invite_dialog(note)
        if not submitted:
            return _connection_result(
                url,
                "connect_unavailable",
                "LinkedIn did not open a usable invite dialog for this profile.",
                profile=page_text,
            )

        verified = await self.scrape_person(username, {"main_profile"})
        verified_text = verified.get("sections", {}).get("main_profile", "")
        verified_signals = await self._read_action_signals(username)
        verified_state = detect_connection_state(verified_text, verified_signals)

        if verified_signals.has_invite_anchor:
            return _connection_result(
                url,
                "send_failed",
                "Submitted the invite dialog but the profile still exposes Connect.",
                note_sent=note_sent,
                profile=verified_text or page_text,
            )

        return _connection_result(
            url,
            "connected",
            "Connection request sent."
            + (f" State after send: {verified_state}." if verified_state else ""),
            note_sent=note_sent,
            profile=verified_text or page_text,
        )

    async def _extract_profile_urn(self) -> str | None:
        """Extract the recipient profile URN from the messaging compose link.

        The compose button on a person's profile contains a recipient URN in its
        href query string. This URN is more reliable than username for messaging.
        Returns None when no compose button is present (e.g. not a 1st-degree
        connection or viewing own profile).
        """
        href: str | None = await self._page.evaluate(
            """() => {
                const anchor = document.querySelector(
                    'main a[href*="/messaging/compose/"]'
                );
                if (!anchor) return null;
                return anchor.getAttribute('href') || anchor.href || null;
            }"""
        )
        if not isinstance(href, str) or not href.strip():
            return None
        params = parse_qs(urlparse(href.strip()).query)
        recipient = params.get("recipient", [None])[0]
        return recipient if isinstance(recipient, str) and recipient else None

    async def get_sidebar_profiles(self, username: str) -> dict[str, Any]:
        """Extract profile links from sidebar sections on a LinkedIn profile page.

        Scrapes "More profiles for you", "Explore premium profiles", and
        "People you may know" sidebar sections. Follows each "Show all" link to
        collect the full list; skips any section whose "Show all" URL contains or
        redirects to /premium.

        Returns:
            Dict with url and sidebar_profiles mapping section key to list of
            /in/username/ paths. Sections absent from the page are omitted.
        """
        url = f"https://www.linkedin.com/in/{username}/"
        await self._navigate_to_page(url)
        await detect_rate_limit(self._page)

        try:
            await self._page.wait_for_selector("main", timeout=5000)
        except PlaywrightTimeoutError:
            logger.debug("No <main> element found on %s", url)

        await handle_modal_close(self._page)

        sidebar_data: dict[str, Any] = await self._page.evaluate(
            """() => {
                const SIDEBAR_SECTIONS = [
                    "More profiles for you",
                    "Explore premium profiles",
                    "People you may know"
                ];
                const normalize = text => (text || '').replace(/\\s+/g, ' ').trim();
                const slugify = text => text.toLowerCase().replace(/\\s+/g, '_');
                const extractProfilePath = href => {
                    if (!href) return null;
                    const idx = href.indexOf('/in/');
                    if (idx === -1) return null;
                    const rest = href.slice(idx + 4);
                    const end = rest.search(/[/?#]/);
                    const username = end === -1 ? rest : rest.slice(0, end);
                    return username ? '/in/' + username + '/' : null;
                };

                const sections = {};
                const showAllUrls = {};

                const headings = Array.from(document.querySelectorAll('h1, h2, h3'));
                for (const heading of headings) {
                    const headingText = normalize(
                        heading.innerText || heading.textContent
                    );
                    if (!SIDEBAR_SECTIONS.includes(headingText)) continue;

                    const sectionKey = slugify(headingText);

                    // Walk up to find a section/aside container (max 5 levels)
                    let container = heading.parentElement;
                    let foundSection = false;
                    for (let depth = 0; container && depth < 5; depth++) {
                        const tag = container.tagName.toLowerCase();
                        if (tag === 'section' || tag === 'aside') { foundSection = true; break; }
                        container = container.parentElement;
                    }
                    if (!container || !foundSection) continue;

                    // Collect /in/ profile links, deduplicated
                    const seen = new Set();
                    const profileLinks = [];
                    for (const a of container.querySelectorAll('a[href*="/in/"]')) {
                        const path = extractProfilePath(a.getAttribute('href'));
                        if (path && !seen.has(path)) {
                            seen.add(path);
                            profileLinks.push(path);
                        }
                    }

                    // Find "Show all" / "See all" anchor within container
                    let showAll = null;
                    for (const a of container.querySelectorAll('a')) {
                        const text = normalize(
                            a.innerText || a.textContent
                        ).toLowerCase();
                        if (text.startsWith('show all') || text.startsWith('see all')) {
                            showAll = a.href || a.getAttribute('href');
                            break;
                        }
                    }

                    sections[sectionKey] = profileLinks;
                    if (showAll) showAllUrls[sectionKey] = showAll;
                }

                return { sections, showAllUrls };
            }"""
        )

        sidebar_profiles: dict[str, list[str]] = dict(sidebar_data.get("sections", {}))
        show_all_urls: dict[str, str] = dict(sidebar_data.get("showAllUrls", {}))

        first_show_all = True
        for section_key, show_all_url in show_all_urls.items():
            if "/premium" in show_all_url:
                continue

            if not first_show_all:
                await asyncio.sleep(_NAV_DELAY)
            first_show_all = False

            try:
                await self._navigate_to_page(show_all_url)
            except Exception:
                logger.debug(
                    "Failed to navigate to Show all for section %s: %s",
                    section_key,
                    show_all_url,
                )
                continue

            if "/premium" in self._page.url:
                logger.debug(
                    "Show all for section %s redirected to premium, skipping",
                    section_key,
                )
                continue

            await detect_rate_limit(self._page)

            try:
                await self._page.wait_for_selector("main")
            except PlaywrightTimeoutError:
                logger.debug("No <main> on Show all page for section %s", section_key)

            await handle_modal_close(self._page)

            expanded_links: list[str] = await self._page.evaluate(
                """() => {
                    const extractProfilePath = href => {
                        if (!href) return null;
                        const idx = href.indexOf('/in/');
                        if (idx === -1) return null;
                        const rest = href.slice(idx + 4);
                        const end = rest.search(/[/?#]/);
                        const username = end === -1 ? rest : rest.slice(0, end);
                        return username ? '/in/' + username + '/' : null;
                    };
                    const seen = new Set();
                    const links = [];
                    for (const a of document.querySelectorAll(
                        'main a[href*="/in/"]'
                    )) {
                        const path = extractProfilePath(a.getAttribute('href'));
                        if (path && !seen.has(path)) {
                            seen.add(path);
                            links.push(path);
                        }
                    }
                    return links;
                }"""
            )

            # Merge: sidebar links first, then show_all expansion, deduped
            existing = sidebar_profiles.get(section_key, [])
            seen_paths: set[str] = set(existing)
            merged = list(existing)
            for link in expanded_links:
                if link not in seen_paths:
                    seen_paths.add(link)
                    merged.append(link)
            sidebar_profiles[section_key] = merged

        return {
            "url": url,
            "sidebar_profiles": sidebar_profiles,
        }

    async def _resolve_message_compose_href(self) -> str | None:
        """Return the direct recipient-specific compose URL from a profile page."""
        href = await self._page.evaluate(
            """(selector) => {
                const isVisible = element =>
                    !!(
                        element &&
                        (element.offsetWidth ||
                            element.offsetHeight ||
                            element.getClientRects().length)
                    );

                const anchor = Array.from(
                    document.querySelectorAll(selector)
                ).find(isVisible);
                if (!anchor) return null;
                return anchor.getAttribute('href') || anchor.href || null;
            }""",
            _MESSAGING_COMPOSE_LINK_SELECTOR,
        )
        if not isinstance(href, str) or not href.strip():
            return None
        return urljoin("https://www.linkedin.com", href.strip())

    async def _read_profile_display_name(self) -> str | None:
        """Read the visible profile name from the current person page."""
        display_name = await self._page.evaluate(
            """() => {
                const heading = document.querySelector('main h1');
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                if (heading) {
                    const headingText = normalize(
                        heading.innerText || heading.textContent || ''
                    );
                    if (headingText) return headingText;
                }

                const main = document.querySelector('main');
                if (!main) return '';
                const lines = (main.innerText || '')
                    .split('\\n')
                    .map(normalize)
                    .filter(Boolean);
                return lines[0] || '';
            }"""
        )
        if not isinstance(display_name, str):
            return None
        display_name = display_name.strip()
        return display_name or None

    async def _wait_for_message_surface(
        self,
    ) -> Literal["composer", "recipient_picker"] | None:
        """Wait for either the recipient picker or the real composer to appear.

        The recipient-picker probe uses a short 2 s cap so we fall through
        quickly to the composer check, which uses the page-level default
        (``BrowserConfig.default_timeout``, configurable via ``--timeout``).
        """
        if await self._locator_is_visible(
            _MESSAGING_RECIPIENT_PICKER_SELECTOR, timeout=2000
        ):
            return "recipient_picker"
        if await self._wait_for_message_composer():
            return "composer"
        return None

    async def _select_message_recipient(self, *candidates: str) -> bool:
        """Select the intended recipient from LinkedIn's New message picker."""
        normalized_candidates = [value.strip() for value in candidates if value.strip()]
        if not normalized_candidates:
            return False

        selected = await self._page.evaluate(
            """({ candidates }) => {
                const normalize = value =>
                    (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const isVisible = element =>
                    !!(
                        element &&
                        (element.offsetWidth || element.offsetHeight || element.getClientRects().length)
                    );
                const pickerInput = Array.from(document.querySelectorAll('input')).find(
                    element =>
                        isVisible(element) &&
                        /type a name|multiple names/i.test(
                            `${element.placeholder || ''} ${
                                element.getAttribute('aria-label') || ''
                            }`
                        )
                );
                const pickerRoot =
                    pickerInput?.closest('section, dialog, [role="dialog"], aside, div') ||
                    document.body;
                const rows = Array.from(
                    pickerRoot.querySelectorAll(
                        '[role="option"], [role="listitem"], li, button, a, div'
                    )
                ).filter(element => {
                    if (!isVisible(element)) return false;
                    const text = normalize(element.innerText || element.textContent);
                    return text.length > 0 && text !== 'new message';
                });

                for (const candidate of candidates.map(normalize)) {
                    const exact = rows.find(element =>
                        normalize(element.innerText || element.textContent) === candidate
                    );
                    if (exact) {
                        exact.click();
                        return true;
                    }
                }

                for (const candidate of candidates.map(normalize)) {
                    const partial = rows.find(element =>
                        normalize(element.innerText || element.textContent).includes(candidate)
                    );
                    if (partial) {
                        partial.click();
                        return true;
                    }
                }

                return false;
            }""",
            {"candidates": normalized_candidates},
        )
        if selected:
            await asyncio.sleep(0.75)
        return bool(selected)

    async def _wait_for_message_composer(self) -> bool:
        """Wait for the usable LinkedIn message composer to appear."""
        return await self._resolve_message_compose_box() is not None

    async def _resolve_message_compose_box(self) -> Any | None:
        """Resolve the visible compose box used for writing a LinkedIn message.

        Uses the page-level default timeout (``BrowserConfig.default_timeout``)
        so the ``--timeout`` CLI flag is respected.
        """
        for selector in _MESSAGING_COMPOSE_FALLBACK_SELECTORS:
            locator = self._page.locator(selector)
            candidate_count: int | None = None
            try:
                candidate_count = await locator.count()
            except Exception:
                logger.debug(
                    "Could not count compose box candidates for selector %r",
                    selector,
                    exc_info=True,
                )

            logger.debug(
                "Message compose selector %r matched %s candidate(s)",
                selector,
                candidate_count if candidate_count is not None else "unknown",
            )

            # patchright quirk: locator.wait_for(state="visible") times out on
            # the contenteditable compose div even though count() > 0 and the
            # element is fully visible by every CSS/DOM criterion (display:block,
            # visibility:visible, opacity:1, non-zero bbox, no inert ancestor).
            # This appears to be a patchright bug with React-hydrated contenteditable
            # elements in isolated worlds. Skip the actionability wait when count()
            # already confirmed the element is present — downstream interactions
            # use page.evaluate() which bypasses the same check.
            if candidate_count and candidate_count > 0:
                return locator.last

            # Fallback: when count() raised an exception above (candidate_count
            # is None), attempt the original wait_for path.  This is unlikely to
            # succeed given the same patchright quirk, but preserves the prior
            # behaviour for non-patchright drivers where wait_for works normally.
            candidate = locator.last
            try:
                await candidate.wait_for(state="visible")
                return candidate
            except PlaywrightTimeoutError:
                continue

        return None

    async def _compose_page_matches_recipient(self, *candidates: str) -> bool:
        """Verify the compose page visibly identifies the intended recipient."""
        normalized_candidates = [value.strip() for value in candidates if value.strip()]
        if not normalized_candidates:
            return False

        matched = await self._page.evaluate(
            """({ candidates }) => {
                const normalize = value =>
                    (value || '').replace(/\\s+/g, ' ').trim().toLowerCase();
                const isVisible = element =>
                    !!(
                        element &&
                        (element.offsetWidth ||
                            element.offsetHeight ||
                            element.getClientRects().length)
                    );

                const targetValues = candidates.map(normalize).filter(Boolean);
                const root = document.querySelector('main') || document.body;
                if (!root) return false;

                const entries = Array.from(
                    root.querySelectorAll(
                        'button, [role="button"], a, span, div, li, p, h1, h2, h3'
                    )
                )
                    .filter(isVisible)
                    .map(element =>
                        [
                            normalize(element.innerText || element.textContent || ''),
                            normalize(element.getAttribute('aria-label') || ''),
                        ].filter(Boolean)
                    )
                    .flat();

                return targetValues.some(candidate =>
                    entries.some(entry => entry === candidate || entry.includes(candidate))
                );
            }""",
            {"candidates": normalized_candidates},
        )
        return bool(matched)

    async def _message_text_visible(self, message: str) -> bool:
        """Wait until the compose page visibly contains the just-sent message text.

        Uses the page-level default timeout (``BrowserConfig.default_timeout``).
        """
        try:
            await self._page.wait_for_function(
                """({ expected }) => {
                    const normalize = value =>
                        (value || '').replace(/\\s+/g, ' ').trim();
                    const bodyText = normalize(document.body?.innerText || '');
                    return bodyText.includes(normalize(expected));
                }""",
                arg={"expected": message},
            )
            return True
        except PlaywrightTimeoutError:
            return False

    async def _dismiss_message_ui(self) -> None:
        """Best-effort dismissal for the profile messaging UI."""
        if not await self._locator_is_visible(_MESSAGING_CLOSE_SELECTOR, timeout=750):
            return
        try:
            await self._click_first(_MESSAGING_CLOSE_SELECTOR, timeout=1500)
            await asyncio.sleep(0.5)
        except Exception:
            logger.debug("Could not dismiss LinkedIn messaging UI", exc_info=True)

    @staticmethod
    def _extract_thread_id(url: str) -> str | None:
        """Parse a LinkedIn thread id from a messaging thread URL."""
        match = re.search(r"/messaging/thread/([^/?#]+)/", url)
        return match.group(1) if match else None

    async def _resolve_conversation_thread_urls(self, display_name: str) -> list[str]:
        """Return all thread URLs whose participant name matches display_name.

        Uses URL-driven search (`/messaging/?searchTerm=…`) plus click-to-capture
        because LinkedIn renders the messaging sidebar with no anchor hrefs, no
        data-thread attributes, and no embedded URNs — clicking each row and
        reading the resulting SPA URL is the only available extraction path.

        Matches by case-insensitive equality on the cleaned participant name
        derived from the row's aria-label, which tolerates duplicate threads
        with the same participant. Browser locale is forced to en-US so the
        verb prefix strips reliably; in any other locale the comparison fails
        cleanly with "Could not find a conversation" rather than returning
        a wrong-thread match.
        """
        search_url = (
            f"https://www.linkedin.com/messaging/?searchTerm={quote_plus(display_name)}"
        )
        await self._navigate_to_page(search_url)
        await detect_rate_limit(self._page)
        await handle_modal_close(self._page)
        await self._wait_for_main_text(log_context="Messaging search results")

        refs = await self._extract_conversation_thread_refs(
            limit=None, context="search"
        )
        target_name = display_name.strip().lower()
        urls: list[str] = []
        for ref in refs:
            ref_text = (ref.get("text") or "").strip().lower()
            if not ref_text or ref_text != target_name:
                continue
            urls.append(f"https://www.linkedin.com{ref['url']}")
        return urls

    async def _open_conversation_by_username(
        self, linkedin_username: str, index: int = 0
    ) -> None:
        """Open the ``index``-th conversation thread for the named participant.

        ``index`` is 0-based and orders threads as the search-results sidebar
        renders them (LinkedIn surfaces newest activity first).
        """
        if index < 0:
            raise LinkedInScraperException(f"index must be non-negative (got {index}).")

        profile_url = f"https://www.linkedin.com/in/{linkedin_username}/"
        await self._navigate_to_page(profile_url)
        await detect_rate_limit(self._page)

        try:
            await self._page.wait_for_selector("main")
        except PlaywrightTimeoutError:
            logger.debug("Profile page did not load for %s", linkedin_username)

        await handle_modal_close(self._page)
        display_name = await self._read_profile_display_name()
        if not display_name:
            raise LinkedInScraperException(
                f"Could not resolve a display name for {linkedin_username}."
            )

        try:
            thread_urls = await self._resolve_conversation_thread_urls(display_name)
            if not thread_urls:
                raise LinkedInScraperException(
                    f"Could not find a conversation for {linkedin_username}."
                )
            if index >= len(thread_urls):
                raise LinkedInScraperException(
                    f"index {index} out of range: only {len(thread_urls)} "
                    f"thread(s) exist for {linkedin_username}."
                )

            await self._navigate_to_page(thread_urls[index])
        except PlaywrightTimeoutError as exc:
            raise LinkedInScraperException(
                "Messaging search results did not load in time."
            ) from exc

    async def scrape_company(
        self,
        company_name: str,
        requested: set[str],
        callbacks: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Scrape a company profile with configurable sections.

        Returns:
            {url, sections: {name: text}}
        """
        requested = requested | {"about"}
        base_url = f"https://www.linkedin.com/company/{company_name}"
        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}

        requested_ordered = [
            (name, suffix, is_overlay)
            for name, (suffix, is_overlay) in COMPANY_SECTIONS.items()
            if name in requested
        ]
        total = len(requested_ordered)

        if callbacks:
            await callbacks.on_start("company profile", base_url)

        try:
            for i, (section_name, suffix, is_overlay) in enumerate(requested_ordered):
                if i > 0:
                    await asyncio.sleep(_NAV_DELAY)

                url = base_url + suffix
                try:
                    if is_overlay:
                        extracted = await self._extract_overlay(
                            url, section_name=section_name
                        )
                    else:
                        extracted = await self.extract_page(
                            url, section_name=section_name
                        )

                    if extracted.text and extracted.text != _RATE_LIMITED_MSG:
                        sections[section_name] = extracted.text
                        if extracted.references:
                            references[section_name] = extracted.references
                    elif extracted.error:
                        section_errors[section_name] = extracted.error
                except LinkedInScraperException:
                    raise
                except Exception as e:
                    logger.warning("Error scraping section %s: %s", section_name, e)
                    section_errors[section_name] = build_issue_diagnostics(
                        e,
                        context="scrape_company",
                        target_url=url,
                        section_name=section_name,
                    )

                # "Scraped" = processed/attempted, not necessarily successful.
                # Per-section failures are captured in section_errors.
                if callbacks:
                    percent = round((i + 1) / total * 95)
                    await callbacks.on_progress(
                        f"Scraped {section_name} ({i + 1}/{total})", percent
                    )
        except LinkedInScraperException as e:
            if callbacks:
                await callbacks.on_error(e)
            raise

        result: dict[str, Any] = {
            "url": f"{base_url}/",
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors

        if callbacks:
            await callbacks.on_complete("company profile", result)

        return result

    async def get_company_employees(
        self,
        company_name: str,
        keywords: str | None = None,
    ) -> dict[str, Any]:
        """List employees at a company from the /people/ page.

        Returns:
            {url, sections: {employees: text}, references: {employees: [...]}}
        """
        url = f"https://www.linkedin.com/company/{company_name}/people/"
        if keywords:
            url += f"?keywords={quote_plus(keywords)}"
        extracted = await self.extract_page(url, section_name="employees")

        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        if extracted.text and extracted.text != _RATE_LIMITED_MSG:
            sections["employees"] = extracted.text
            if extracted.references:
                references["employees"] = extracted.references
        elif extracted.error:
            section_errors["employees"] = extracted.error

        result: dict[str, Any] = {
            "url": url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def scrape_job(self, job_id: str) -> dict[str, Any]:
        """Scrape a single job posting.

        Returns:
            {url, sections: {name: text}}
        """
        url = f"https://www.linkedin.com/jobs/view/{job_id}/"
        extracted = await self.extract_page(url, section_name="job_posting")

        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        if extracted.text and extracted.text != _RATE_LIMITED_MSG:
            sections["job_posting"] = extracted.text
            if extracted.references:
                references["job_posting"] = extracted.references
        elif extracted.error:
            section_errors["job_posting"] = extracted.error

        result: dict[str, Any] = {
            "url": url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def _extract_job_ids(self) -> list[str]:
        """Extract unique job IDs from job card links on the current page.

        Finds all `a[href*="/jobs/view/"]` links and extracts the numeric
        job ID from each href. Returns deduplicated IDs in DOM order.
        """
        return await self._page.evaluate(
            """() => {
                const links = document.querySelectorAll('a[href*="/jobs/view/"]');
                const seen = new Set();
                const ids = [];
                for (const a of links) {
                    const match = a.href.match(/\\/jobs\\/view\\/(\\d+)/);
                    if (match && !seen.has(match[1])) {
                        seen.add(match[1]);
                        ids.push(match[1]);
                    }
                }
                return ids;
            }"""
        )

    async def _extract_search_page(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Extract innerText from a job search page with soft rate-limit retry.

        Mirrors the noise-only detection and single-retry behavior of
        ``extract_page`` / ``_extract_page_once`` so that callers get a
        ``_RATE_LIMITED_MSG`` sentinel instead of silent empty results.
        """
        try:
            result = await self._extract_search_page_once(url, section_name)
            if result.text != _RATE_LIMITED_MSG:
                return result

            logger.info(
                "Retrying search page %s after %.0fs backoff",
                url,
                _RATE_LIMIT_RETRY_DELAY,
            )
            await asyncio.sleep(_RATE_LIMIT_RETRY_DELAY)
            result = await self._extract_search_page_once(url, section_name)
            if result.text == _RATE_LIMITED_MSG:
                logger.warning("Search page %s still rate-limited after retry", url)
            return result

        except LinkedInScraperException:
            raise
        except Exception as e:
            logger.warning("Failed to extract search page %s: %s", url, e)
            return ExtractedSection(
                text="",
                references=[],
                error=build_issue_diagnostics(
                    e,
                    context="extract_search_page",
                    target_url=url,
                    section_name=section_name,
                ),
            )

    async def _extract_search_page_once(
        self,
        url: str,
        section_name: str,
    ) -> ExtractedSection:
        """Single attempt to navigate, scroll sidebar, and extract innerText."""
        await self._navigate_to_page(url)
        await detect_rate_limit(self._page)

        main_found = True
        try:
            await self._page.wait_for_selector("main")
        except PlaywrightTimeoutError:
            logger.debug("No <main> element found on %s", url)
            main_found = False

        await handle_modal_close(self._page)
        if main_found:
            await scroll_job_sidebar(self._page, pause_time=0.5, max_scrolls=5)

        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]
        if raw_result["source"] == "body":
            logger.debug("No <main> at evaluation time on %s, using body fallback", url)
        elif not main_found:
            logger.debug(
                "<main> appeared after wait timeout on %s, sidebar scroll was skipped",
                url,
            )

        if not raw:
            return ExtractedSection(text="", references=[])
        truncated = _truncate_linkedin_noise(raw)
        if not truncated and raw.strip():
            logger.warning(
                "Search page %s returned only LinkedIn chrome (likely rate-limited)",
                url,
            )
            return ExtractedSection(text=_RATE_LIMITED_MSG, references=[])
        cleaned = _filter_linkedin_noise_lines(truncated)
        return ExtractedSection(
            text=cleaned,
            references=build_references(raw_result["references"], section_name),
        )

    async def _get_total_search_pages(self) -> int | None:
        """Read total page count from LinkedIn's pagination state element.

        Parses the "Page X of Y" text from ``.jobs-search-pagination__page-state``.
        Returns ``None`` when the element is absent or unparseable.

        NOTE: This is a deliberate DOM exception. The element has ``display: none``
        (screen-reader only), so the text never appears in ``innerText``. A class-based
        selector is the only reliable way to read it. Gracefully returns ``None`` if
        LinkedIn renames the class — pagination just falls back to ``max_pages``.
        """
        text = await self._page.evaluate(
            """() => {
                const el = document.querySelector(
                    '.jobs-search-pagination__page-state'
                );
                return el ? el.textContent.trim() : null;
            }"""
        )
        if not text:
            return None
        match = re.search(r"of\s+(\d+)", text)
        return int(match.group(1)) if match else None

    @staticmethod
    def _build_job_search_url(
        keywords: str,
        location: str | None = None,
        date_posted: str | None = None,
        job_type: str | None = None,
        experience_level: str | None = None,
        work_type: str | None = None,
        easy_apply: bool = False,
        sort_by: str | None = None,
    ) -> str:
        """Build a LinkedIn job search URL with optional filters.

        Human-readable names are normalized to LinkedIn URL codes.
        Comma-separated values are normalized individually.
        Unknown values pass through unchanged.
        """
        params = f"keywords={quote_plus(keywords)}"
        if location:
            params += f"&location={quote_plus(location)}"

        if date_posted:
            mapped = _DATE_POSTED_MAP.get(date_posted.strip(), date_posted)
            params += f"&f_TPR={quote_plus(mapped)}"
        if job_type:
            params += f"&f_JT={_normalize_csv(job_type, _JOB_TYPE_MAP)}"
        if experience_level:
            params += f"&f_E={_normalize_csv(experience_level, _EXPERIENCE_LEVEL_MAP)}"
        if work_type:
            params += f"&f_WT={_normalize_csv(work_type, _WORK_TYPE_MAP)}"
        if easy_apply:
            params += "&f_EA=true"
        if sort_by:
            mapped = _SORT_BY_MAP.get(sort_by.strip(), sort_by)
            params += f"&sortBy={quote_plus(mapped)}"

        return f"https://www.linkedin.com/jobs/search/?{params}"

    async def search_jobs(
        self,
        keywords: str,
        location: str | None = None,
        max_pages: int = 3,
        date_posted: str | None = None,
        job_type: str | None = None,
        experience_level: str | None = None,
        work_type: str | None = None,
        easy_apply: bool = False,
        sort_by: str | None = None,
    ) -> dict[str, Any]:
        """Search for jobs with pagination and job ID extraction.

        Scrolls the job sidebar (not the main page) and paginates through
        results. Uses LinkedIn's "Page X of Y" indicator to cap pagination,
        and stops early when a page yields no new job IDs.

        Args:
            keywords: Search keywords
            location: Optional location filter
            max_pages: Maximum pages to load (1-10, default 3)
            date_posted: Filter by date posted (past_hour, past_24_hours, past_week, past_month)
            job_type: Filter by job type (full_time, part_time, contract, temporary, volunteer, internship, other)
            experience_level: Filter by experience level (internship, entry, associate, mid_senior, director, executive)
            work_type: Filter by work type (on_site, remote, hybrid)
            easy_apply: Only show Easy Apply jobs
            sort_by: Sort results (date, relevance)

        Returns:
            {url, sections: {search_results: text}, job_ids: [str]}
        """
        base_url = self._build_job_search_url(
            keywords,
            location=location,
            date_posted=date_posted,
            job_type=job_type,
            experience_level=experience_level,
            work_type=work_type,
            easy_apply=easy_apply,
            sort_by=sort_by,
        )
        all_job_ids: list[str] = []
        seen_ids: set[str] = set()
        page_texts: list[str] = []
        page_references: list[Reference] = []
        section_errors: dict[str, dict[str, Any]] = {}
        total_pages: int | None = None
        total_pages_queried = False

        for page_num in range(max_pages):
            # Stop if we already know we've reached the last page
            if total_pages is not None and page_num >= total_pages:
                logger.debug("All %d pages fetched, stopping", total_pages)
                break

            if page_num > 0:
                await asyncio.sleep(_NAV_DELAY)

            url = (
                base_url
                if page_num == 0
                else f"{base_url}&start={page_num * _PAGE_SIZE}"
            )

            try:
                extracted = await self._extract_search_page(
                    url, section_name="search_results"
                )

                if not extracted.text or extracted.text == _RATE_LIMITED_MSG:
                    if extracted.error:
                        section_errors["search_results"] = extracted.error
                    # Navigation failed or rate-limited; skip ID extraction
                    break

                # Read total pages from pagination state (once only, best-effort)
                if not total_pages_queried:
                    total_pages_queried = True
                    try:
                        total_pages = await self._get_total_search_pages()
                    except Exception as e:
                        logger.debug("Could not read total pages: %s", e)
                    else:
                        if total_pages is not None:
                            logger.debug("LinkedIn reports %d total pages", total_pages)

                # Extract job IDs from hrefs (page is already loaded)
                page_url = urlparse(self._page.url)
                if (
                    page_url.netloc.lower() not in {"linkedin.com", "www.linkedin.com"}
                    or page_url.path.rstrip("/") != "/jobs/search"
                ):
                    logger.debug(
                        "Unexpected page URL after extraction: %s — "
                        "skipping job ID extraction",
                        self._page.url,
                    )
                    page_texts.append(extracted.text)
                    if extracted.references:
                        page_references.extend(extracted.references)
                    break
                page_ids = await self._extract_job_ids()
                reference_ids = []
                for reference in extracted.references or []:
                    if reference.get("kind") != "job":
                        continue
                    job_match = re.search(
                        r"/jobs/view/(\d+)(?:/|$)",
                        urlparse(reference["url"]).path,
                    )
                    if job_match:
                        reference_ids.append(job_match.group(1))
                page_ids = list(dict.fromkeys([*page_ids, *reference_ids]))
                new_ids = [jid for jid in page_ids if jid not in seen_ids]

                if not new_ids:
                    page_texts.append(extracted.text)
                    if extracted.references:
                        page_references.extend(extracted.references)
                    logger.debug("No new job IDs on page %d, stopping", page_num + 1)
                    break

                for jid in new_ids:
                    seen_ids.add(jid)
                    all_job_ids.append(jid)

                page_texts.append(extracted.text)
                if extracted.references:
                    page_references.extend(extracted.references)

            except LinkedInScraperException:
                raise
            except Exception as e:
                logger.warning("Error on search page %d: %s", page_num + 1, e)
                section_errors["search_results"] = build_issue_diagnostics(
                    e,
                    context="search_jobs",
                    target_url=url,
                    section_name="search_results",
                )
                break

        result: dict[str, Any] = {
            "url": base_url,
            "sections": {"search_results": "\n---\n".join(page_texts)}
            if page_texts
            else {},
            "job_ids": all_job_ids,
        }
        if page_references:
            result["references"] = {
                "search_results": dedupe_references(page_references, cap=15)
            }
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def search_people(
        self,
        keywords: str,
        location: str | None = None,
        network: list[str] | None = None,
        current_company: str | None = None,
    ) -> dict[str, Any]:
        """Search for people and extract the results page.

        Args:
            keywords: Free-text query ("software engineer", "recruiter at Google").
            location: Optional location filter ("New York", "Remote").
            network: Optional connection-degree filter. Each element is one of
                ``"F"`` (1st-degree), ``"S"`` (2nd-degree), ``"O"`` (3rd-degree
                and beyond). Example: ``["F"]`` to only return 1st-degree
                connections. Invalid tokens raise ``ValueError``.
            current_company: Optional current-employer filter. LinkedIn's
                ``currentCompany`` facet only filters on the numeric company
                URN id (e.g. ``"1115"`` for SAP); plain company names are
                accepted by the URL but ignored by LinkedIn and return the
                unfiltered result set. Look up a company's URN via
                ``get_company_profile`` -- it is exposed under
                ``references["about"]``.

        Returns:
            {url, sections: {name: text}}
        """
        if network is not None:
            invalid = [t for t in network if t not in _NETWORK_TOKENS]
            if invalid:
                raise FilterValidationError(
                    "Invalid network token(s) "
                    f"{invalid!r}; expected any of {list(_NETWORK_TOKENS)!r}"
                )

        if current_company and not re.fullmatch(r"[0-9]+", current_company):
            raise FilterValidationError(
                f"current_company must be a numeric LinkedIn company URN id "
                f"(e.g. '1115' for SAP); got {current_company!r}. Plain-text "
                f"company names are silently ignored by LinkedIn. Look up the "
                f'URN via get_company_profile -> references["about"].'
            )

        params = f"keywords={quote_plus(keywords)}"
        if location:
            params += f"&location={quote_plus(location)}"
        if network:
            params += f"&network={_encode_list_facet(network)}"
        if current_company:
            params += f"&currentCompany={_encode_list_facet([current_company])}"

        url = f"https://www.linkedin.com/search/results/people/?{params}"
        extracted = await self.extract_page(url, section_name="search_results")

        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        if extracted.text and extracted.text != _RATE_LIMITED_MSG:
            sections["search_results"] = extracted.text
            if extracted.references:
                references["search_results"] = extracted.references
        elif extracted.error:
            section_errors["search_results"] = extracted.error

        result: dict[str, Any] = {
            "url": url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def search_sales_navigator_leads(
        self,
        url: str | None = None,
        keywords: str | None = None,
        location: str | None = None,
        max_pages: int = 2,
        lead_list: bool = False,
    ) -> dict[str, Any]:
        """Read leads from the signed-in person's own Sales Navigator, read only.

        The reliable input is ``url``: a Sales Navigator people search (or a people lead list) the person built
        in the product, with its filters. ``keywords`` (and ``location``, folded into the keywords) builds a
        plain keyword search. The reader navigates, scrolls and reads rows; it never clicks connect, message,
        InMail, save or like.

        Args:
            url: ``https://www.linkedin.com/sales/search/people?...`` or ``/sales/lists/people/<id>``.
            keywords: Plain keyword search when no url is given.
            location: Added to the keywords (Sales Navigator filters locations by id).
            max_pages: Result pages to read (1-5); about 25 leads each.
            lead_list: The url must be a people lead list.

        Returns:
            ``{url, pages_read, leads: [...], warnings: [...], section_errors: {...}}``. Each lead has name,
            first_name, last_name, title, company, company_url, company_sales_url, location, profile_url,
            linkedin_url, degree, tenure_in_role, tenure_in_company and raw_lines.

        Raises:
            FilterValidationError: bad input.
            SalesNavigatorAccessError: the account cannot open Sales Navigator people search.
            AuthenticationError / RateLimitError: signed out, checkpoint, or throttled on the first page.
        """
        if not 1 <= int(max_pages) <= _sn.MAX_PAGES:
            raise FilterValidationError(
                f"max_pages must be from 1 to {_sn.MAX_PAGES}; got {max_pages!r}."
            )
        try:
            base_url, _is_list, warnings = _sn.build_search_url(
                _LINKEDIN_ORIGIN,
                url=url,
                keywords=keywords,
                location=location,
                lead_list=lead_list,
            )
        except ValueError as exc:
            raise FilterValidationError(str(exc)) from exc

        leads: list[dict[str, Any]] = []
        seen: set[str] = set()
        section_errors: dict[str, dict[str, Any]] = {}
        pages_read = 0

        for page_number in range(1, int(max_pages) + 1):
            page_url = _sn.with_page(base_url, page_number)
            if page_number > 1:
                await asyncio.sleep(random.uniform(*_SALES_NAV_PACE_SECONDS))
            try:
                await self._navigate_to_page(page_url)
                await detect_rate_limit(self._page)
                rows = await self._read_sales_navigator_page(page_number)
            except LinkedInScraperException as exc:
                if page_number == 1:
                    raise
                # Pages already read are real: keep them and say why the run stopped.
                logger.warning(
                    "Sales Navigator stopped on page %d: %s", page_number, exc
                )
                warnings.append(f"Stopped before page {page_number}: {exc}")
                section_errors["sales_navigator"] = build_issue_diagnostics(
                    exc,
                    context="search_sales_navigator",
                    target_url=page_url,
                    section_name="sales_navigator",
                )
                break
            except Exception as exc:
                logger.warning("Error on Sales Navigator page %d: %s", page_number, exc)
                section_errors["sales_navigator"] = build_issue_diagnostics(
                    exc,
                    context="search_sales_navigator",
                    target_url=page_url,
                    section_name="sales_navigator",
                )
                break

            pages_read += 1
            fresh = []
            for row in rows:
                lead = _sn.classify_row(row)
                key = lead["profile_url"] or row.get("key", "")
                if not key or key in seen or not lead["name"]:
                    continue
                seen.add(key)
                fresh.append(lead)
            leads.extend(fresh)
            if not rows:
                warnings.append(
                    f"Page {page_number} had no leads."
                    if page_number > 1
                    else "The search returned no leads."
                )
                break
            if not fresh:
                warnings.append(
                    f"Page {page_number} repeated leads already read; stopped."
                )
                break

        result: dict[str, Any] = {
            "url": base_url,
            "pages_read": pages_read,
            "leads": leads,
            "warnings": warnings,
            "section_errors": section_errors,
        }
        return result

    async def _read_sales_navigator_page(
        self, page_number: int
    ) -> list[dict[str, Any]]:
        """Wait for, scroll through and read one Sales Navigator result page (never clicks)."""
        try:
            await self._page.wait_for_selector(
                'a[href*="/sales/lead/"]', timeout=_SALES_NAV_RESULT_WAIT_MS
            )
        except PlaywrightTimeoutError:
            logger.debug(
                "No lead rows appeared on Sales Navigator page %d", page_number
            )

        state = await self._page.evaluate(_sn.STATE_JS)
        state = state if isinstance(state, dict) else {}
        path = str(state.get("path") or urlparse(self._page.url).path)
        origin_host = urlparse(_LINKEDIN_ORIGIN).netloc.lower()
        landed_host = str(state.get("host") or urlparse(self._page.url).netloc).lower()
        if landed_host.removeprefix("www.") != origin_host.removeprefix(
            "www."
        ) or not _sn.is_results_path(path):
            raise SalesNavigatorAccessError(
                "This LinkedIn account has no Sales Navigator access: LinkedIn sent the people search to "
                f"{landed_host}{path} instead of the lead search. Open the search in your browser to check "
                "the subscription, then try again."
            )
        if _sn.looks_like_upsell(state):
            raise SalesNavigatorAccessError(
                "This LinkedIn account has no Sales Navigator access: the page offers Sales Navigator "
                "instead of showing search results."
            )
        if not state.get("lead_count"):
            return []

        rows: dict[str, dict[str, Any]] = {}
        unchanged = 0
        last_count = -1
        for _step in range(_SALES_NAV_MAX_SCROLL_STEPS):
            batch = await self._page.evaluate(_sn.ROWS_JS)
            for row in batch if isinstance(batch, list) else []:
                if isinstance(row, dict) and row.get("key"):
                    rows.setdefault(str(row["key"]), row)
            step = await self._page.evaluate(
                _sn.SCROLL_JS, {"fraction": _SALES_NAV_SCROLL_FRACTION}
            )
            await asyncio.sleep(_SALES_NAV_SCROLL_PAUSE_SECONDS)
            count = len(rows)
            at_end = bool(
                isinstance(step, dict)
                and (step.get("atBottom") or not step.get("moved"))
            )
            unchanged = unchanged + 1 if (count == last_count and at_end) else 0
            last_count = count
            if unchanged >= 2:
                break
        # one last read: the final scroll step may have rendered more rows
        batch = await self._page.evaluate(_sn.ROWS_JS)
        for row in batch if isinstance(batch, list) else []:
            if isinstance(row, dict) and row.get("key"):
                rows.setdefault(str(row["key"]), row)
        return list(rows.values())

    async def search_companies(
        self,
        keywords: str,
    ) -> dict[str, Any]:
        """Search for companies and extract the results page.

        Returns:
            {url, sections: {search_results: text}}
        """
        url = f"https://www.linkedin.com/search/results/companies/?keywords={quote_plus(keywords)}"
        extracted = await self.extract_page(url, section_name="search_results")

        sections: dict[str, str] = {}
        references: dict[str, list[Reference]] = {}
        section_errors: dict[str, dict[str, Any]] = {}
        if extracted.text and extracted.text != _RATE_LIMITED_MSG:
            sections["search_results"] = extracted.text
            if extracted.references:
                references["search_results"] = extracted.references
        elif extracted.error:
            section_errors["search_results"] = extracted.error

        result: dict[str, Any] = {
            "url": url,
            "sections": sections,
        }
        if references:
            result["references"] = references
        if section_errors:
            result["section_errors"] = section_errors
        return result

    async def get_inbox(self, limit: int = 20) -> dict[str, Any]:
        """List recent conversations from the messaging inbox."""
        url = "https://www.linkedin.com/messaging/"
        await self._navigate_to_page(url)
        await detect_rate_limit(self._page)
        await self._wait_for_main_text(log_context="Messaging inbox")
        await handle_modal_close(self._page)

        scrolls = max(1, limit // 10)
        await self._scroll_main_scrollable_region(
            position="bottom", attempts=scrolls, pause_time=0.5
        )

        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]
        cleaned = strip_linkedin_noise(raw) if raw else ""
        references: list[Reference] = (
            build_references(raw_result["references"], "inbox") if cleaned else []
        )

        # LinkedIn's conversation sidebar uses JS click handlers instead of
        # <a> tags, so anchor extraction cannot capture thread IDs.  Click each
        # conversation item and read the resulting SPA URL to build references.
        conversation_refs = await self._extract_conversation_thread_refs(
            limit=limit, context="inbox"
        )
        if conversation_refs:
            references = dedupe_references(conversation_refs + references)

        return self._single_section_result(
            url,
            "inbox",
            cleaned,
            references=references,
        )

    async def _extract_conversation_thread_refs(
        self, limit: int | None, context: str
    ) -> list[Reference]:
        """Click each visible conversation item and capture the thread URL.

        Works for both the inbox sidebar and the URL-driven search-results
        sidebar (`/messaging/?searchTerm=…`), which share the same DOM shape:
        each conversation row is an ``<li>`` containing a ``<label>`` with an
        ``aria-label`` attribute carrying the participant name.

        LinkedIn renders the sidebar with no ``<a href>`` tags, no
        ``data-thread-id`` attributes, and no embedded URNs — clicking each
        row and reading the SPA URL is the only reliable extraction path.
        Pass ``limit=None`` to capture every visible row.
        """
        # The conversation list mounts after main text settles, so wait
        # explicitly for at least one label rather than relying on
        # _wait_for_main_text alone (which only checks chrome text). LinkedIn
        # routinely takes several seconds to hydrate the messaging sidebar
        # after a navigation; an empty sidebar (zero matches) returns on
        # timeout.
        #
        # Selector is structural (`main li label[aria-label]`) rather than
        # text-prefix-based (`aria-label^="Select conversation"`) so it
        # survives any LinkedIn locale — the verb in the aria-label is
        # locale-dependent, the attribute's presence inside a list-item label
        # is not.
        #
        # Wait on `state="attached"` instead of the default `visible`:
        # Ember-managed labels are reliably attached but Playwright's
        # visibility heuristic doesn't always consider them visible.
        try:
            await self._page.wait_for_selector(
                "main li label[aria-label]",
                state="attached",
                timeout=10000,
            )
        except PlaywrightTimeoutError:
            structure = await self._page.evaluate(
                """() => {
                    const main = document.querySelector('main');
                    if (!main) return {main: false};
                    return {
                        main: true,
                        list_items: main.querySelectorAll('li').length,
                        aria_labels: main.querySelectorAll('label[aria-label]').length,
                        role_buttons: main.querySelectorAll('[role="button"]').length,
                        buttons: main.querySelectorAll('button').length,
                        thread_links: main.querySelectorAll('a[href*="/messaging/thread/"]').length,
                    };
                }"""
            )
            logger.debug(
                "conversation labels did not appear within 10s (context=%s, structure=%s)",
                context,
                structure,
            )
            return []

        # The Ember click handler lives on an inner div; the <li> and <label>
        # don't trigger SPA navigation.  No role/aria attributes exist on the
        # clickable element, so class-name selectors are unavoidable here.
        # The aria-label value flows through unmodified — Python strips any
        # known locale prefix to derive a clean participant name for refs.
        conversations: list[dict[str, str]] = await self._page.evaluate(
            """async ({ limit }) => {
                const labels = Array.from(document.querySelectorAll(
                    'main li label[aria-label]'
                ));
                const cap = (limit == null)
                    ? labels.length
                    : Math.min(labels.length, limit);
                const results = [];
                for (let i = 0; i < cap; i++) {
                    const label = labels[i];
                    const ariaLabel = label.getAttribute('aria-label') || '';
                    const row = label.closest('li');
                    const directLink = row?.querySelector(
                        'a[href*="/messaging/thread/"]'
                    );
                    const directMatch = directLink?.href.match(
                        /\\/messaging\\/thread\\/([^/?#]+)/
                    );
                    if (directMatch) {
                        results.push({ ariaLabel, threadId: directMatch[1] });
                        continue;
                    }
                    const clickTargets = [
                        row?.querySelector('div[class*="listitem__link"]'),
                        row?.querySelector('button, [role="button"]'),
                        label.parentElement,
                        row,
                    ].filter((target, index, targets) =>
                        target && targets.indexOf(target) === index
                    );
                    if (!clickTargets.length) continue;
                    const before = location.href;
                    let after = before;
                    let match = null;
                    for (const clickTarget of clickTargets) {
                        clickTarget.click();
                        // The delegated click handler may live on different
                        // descendants across LinkedIn messaging layouts.
                        for (let waits = 0; waits < 12; waits++) {
                            await new Promise(r => setTimeout(r, 100));
                            after = location.href;
                            match = after.match(
                                /\\/messaging\\/thread\\/([^/?#]+)/
                            );
                            if (match) break;
                        }
                        if (match) break;
                    }
                    if (match) {
                        results.push({ ariaLabel, threadId: match[1] });
                    }
                }
                return results;
            }""",
            {"limit": limit},
        )
        refs: list[Reference] = []
        for conv in conversations:
            ref: Reference = {
                "kind": "conversation",
                "url": f"/messaging/thread/{conv['threadId']}/",
                "context": context,
            }
            name = self._strip_select_conversation_prefix(conv.get("ariaLabel", ""))
            if name:
                ref["text"] = name
            refs.append(ref)
        return refs

    # Best-effort prefix strip for the en-US "Select conversation with " verb.
    # Browser locale is forced to en-US (see BrowserManager) so this normally
    # succeeds; the regex falls through silently for any other locale, in
    # which case the full aria-label flows into the ref's text field rather
    # than a stripped name.
    _SELECT_CONVERSATION_PREFIX_RE = re.compile(
        r"^Select conversation with\s+", re.IGNORECASE
    )

    @classmethod
    def _strip_select_conversation_prefix(cls, aria_label: str) -> str:
        return cls._SELECT_CONVERSATION_PREFIX_RE.sub("", aria_label).strip()

    async def get_conversation(
        self,
        linkedin_username: str | None = None,
        thread_id: str | None = None,
        index: int = 0,
    ) -> dict[str, Any]:
        """Read a specific messaging conversation by thread ID or username.

        ``index`` (0-based) selects which thread to open when a participant has
        multiple conversation threads — e.g. an organic 1-on-1 plus a separate
        InMail. Ignored when ``thread_id`` is provided. Use
        ``search_conversations`` to enumerate thread IDs first if disambiguation
        by index is impractical.

        Side effect when looked up by username: resolution searches LinkedIn's
        messaging inbox for the participant's display name and click-visits
        every matching row to capture its thread ID (no anchor hrefs or
        thread-id attributes exist in the sidebar). Each visit selects the row
        in the LinkedIn UI and may mark it as read. Pass ``thread_id`` directly
        to skip this enumeration.
        """
        if not linkedin_username and not thread_id:
            raise LinkedInScraperException(
                "Provide at least one of linkedin_username or thread_id"
            )

        if thread_id:
            await self._navigate_to_page(
                f"https://www.linkedin.com/messaging/thread/{thread_id}/"
            )
        else:
            await self._open_conversation_by_username(
                linkedin_username or "", index=index
            )

        await detect_rate_limit(self._page)
        await self._wait_for_main_text(log_context="Conversation")
        await handle_modal_close(self._page)
        await self._scroll_main_scrollable_region(
            position="top", attempts=3, pause_time=0.5
        )

        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]
        cleaned = strip_linkedin_noise(raw) if raw else ""
        references = (
            build_references(raw_result["references"], "conversation")
            if cleaned
            else []
        )
        return self._single_section_result(
            self._page.url,
            "conversation",
            cleaned,
            references=references,
        )

    async def search_conversations(
        self, keywords: str, limit: int = 20
    ) -> dict[str, Any]:
        """Search messages by keyword.

        Uses LinkedIn's ``?searchTerm=`` URL parameter to drive the search
        rather than typing into the searchbox — the URL form is reliable
        regardless of how soon the messaging SPA mounts its searchbox role,
        and (critically) preserves the search filter across click-to-capture
        navigations so per-thread refs can be enumerated.

        ``limit`` caps how many search-result rows the click-to-capture loop
        visits. Each visit selects the row in LinkedIn's UI (and may mark it
        as read), so a low cap is preferable for noisy queries.
        """
        search_url = (
            f"https://www.linkedin.com/messaging/?searchTerm={quote_plus(keywords)}"
        )
        await self._navigate_to_page(search_url)
        await detect_rate_limit(self._page)
        await handle_modal_close(self._page)
        await self._wait_for_main_text(log_context="Messaging search")

        raw_result = await self._extract_root_content(["main"])
        raw = raw_result["text"]
        cleaned = strip_linkedin_noise(raw) if raw else ""
        references: list[Reference] = (
            build_references(raw_result["references"], "search_results")
            if cleaned
            else []
        )

        # Same click-to-capture path as get_inbox: LinkedIn's search sidebar
        # has no anchor hrefs or thread-id attributes, so the only way to
        # surface per-result thread IDs is to click each row and read the SPA
        # URL. URL-driven search keeps the filter active across clicks.
        conversation_refs = await self._extract_conversation_thread_refs(
            limit=limit, context="search_results"
        )
        if conversation_refs:
            references = dedupe_references(conversation_refs + references)

        return self._single_section_result(
            self._page.url,
            "search_results",
            cleaned,
            references=references,
        )

    async def send_message(
        self,
        linkedin_username: str,
        message: str,
        *,
        confirm_send: bool,
        profile_urn: str | None = None,
    ) -> dict[str, Any]:
        """Send a message to a LinkedIn user with explicit confirmation gating.

        Args:
            linkedin_username: LinkedIn username of the recipient.
            message: The message text to send.
            confirm_send: Must be True to actually send (False does a dry run).
            profile_urn: Optional profile URN (e.g. ACoAAB...) to construct the
                compose URL directly, bypassing the Message-button lookup.
        """
        profile_url = f"https://www.linkedin.com/in/{linkedin_username}/"
        await self._navigate_to_page(profile_url)
        await detect_rate_limit(self._page)

        try:
            await self._page.wait_for_selector("main")
        except PlaywrightTimeoutError:
            logger.debug("Profile page did not load for %s", linkedin_username)

        await handle_modal_close(self._page)
        display_name = await self._read_profile_display_name()
        if profile_urn:
            # Build the full compose URL that LinkedIn's own Message button
            # generates. The minimal ?recipient=<URN> form works for established
            # connections but shows a "Say hello" widget (no compose box) for new
            # connections. Adding profileUrn + screenContext + interop=msgOverlay
            # consistently opens the real composer regardless of connection age.
            _encoded = quote_plus(f"urn:li:fsd_profile:{profile_urn}")
            compose_url: str | None = (
                f"https://www.linkedin.com/messaging/compose/"
                f"?profileUrn={_encoded}"
                f"&recipient={profile_urn}"
                f"&screenContext=NON_SELF_PROFILE_VIEW"
                f"&interop=msgOverlay"
            )
        else:
            compose_url = await self._resolve_message_compose_href()
        if not compose_url:
            return self._message_action_result(
                profile_url,
                "message_unavailable",
                "LinkedIn did not expose a usable Message action for this profile.",
            )

        await self._navigate_to_page(compose_url)
        await detect_rate_limit(self._page)

        try:
            await self._page.wait_for_selector("main")
        except PlaywrightTimeoutError:
            logger.debug("Compose page did not fully load for %s", linkedin_username)

        await handle_modal_close(self._page)
        message_surface = await self._wait_for_message_surface()
        logger.debug(
            "Message surface for %s before hydration was %s",
            linkedin_username,
            message_surface,
        )

        recipient_selected = False
        if message_surface == "recipient_picker":
            recipient_selected = await self._select_message_recipient(
                display_name or "",
                linkedin_username,
            )
            logger.debug(
                "Recipient picker selection for %s returned %s",
                linkedin_username,
                recipient_selected,
            )
            if not recipient_selected:
                await self._dismiss_message_ui()
                return self._message_action_result(
                    self._page.url,
                    "recipient_resolution_failed",
                    "LinkedIn opened a compose page, but the visible recipient did not match the requested profile.",
                )
            message_surface = await self._wait_for_message_surface()
            logger.debug(
                "Message surface for %s after recipient selection was %s",
                linkedin_username,
                message_surface,
            )

        compose_box = await self._resolve_message_compose_box()
        if compose_box is None:
            await self._dismiss_message_ui()
            return self._message_action_result(
                self._page.url,
                "composer_unavailable",
                "LinkedIn did not expose a usable message composer.",
                recipient_selected=recipient_selected,
            )

        logger.debug(
            "Message compose box resolved for %s after hydration",
            linkedin_username,
        )

        if not await self._compose_page_matches_recipient(
            display_name or "",
            linkedin_username,
        ):
            logger.debug(
                "Recipient match still failed for %s after compose hydration",
                linkedin_username,
            )
            await self._dismiss_message_ui()
            return self._message_action_result(
                self._page.url,
                "recipient_resolution_failed",
                "LinkedIn opened a compose page, but the visible recipient did not match the requested profile.",
                recipient_selected=recipient_selected,
            )
        recipient_selected = True

        if not confirm_send:
            await self._dismiss_message_ui()
            return self._message_action_result(
                self._page.url,
                "confirmation_required",
                "Set confirm_send=true to send the message.",
                recipient_selected=recipient_selected,
            )

        # patchright quirk: compose_box.click() and press_sequentially() use
        # actionability checks internally and hit the same wait_for timeout.
        # Instead: focus via page.evaluate() (no actionability check) and type
        # via page.keyboard.type() which operates on the active element directly
        # and fires the real keydown/input/keyup events React needs to enable Send.
        #
        # DOM dependency: innerText extraction is not applicable here — we need
        # to call .focus() on the element reference, which requires querySelector.
        # Selectors use only role + contenteditable + aria-label (ARIA attributes,
        # not layout class names) so they are stable across LinkedIn UI changes.
        focused = await self._page.evaluate(
            """() => {
                const el = document.querySelector(
                    'div[role="textbox"][contenteditable="true"][aria-label*="Write a message"],'
                    + 'div[role="textbox"][contenteditable="true"]'
                );
                if (!el) return false;
                el.focus();
                return true;
            }"""
        )
        if not focused:
            await self._dismiss_message_ui()
            return self._message_action_result(
                self._page.url,
                "compose_interact_failed",
                "Could not focus compose box via JavaScript.",
                recipient_selected=recipient_selected,
            )
        await asyncio.sleep(0.1)
        await self._page.keyboard.type(message, delay=15)
        await asyncio.sleep(0.3)

        # patchright actionability also blocks send_button.click(). Use JS click
        # on any visible, enabled send button; fall back to Enter key which
        # LinkedIn's composer also accepts for submission.
        #
        # DOM dependency: we need btn.click() on the element reference — not
        # achievable via innerText or URL navigation. Selectors use only type,
        # aria-label, and data attributes (no layout class names).
        await asyncio.sleep(1.0)  # allow React to process keyboard input
        sent_via_js = await self._page.evaluate(
            """() => {
                const btn = Array.from(document.querySelectorAll(
                    'button[type="submit"], button[aria-label*="Send"], button[aria-label*="send"],'
                    + 'button[data-control-name="send"]'
                )).find(b => !b.disabled && (b.offsetWidth || b.offsetHeight || b.getClientRects().length));
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        )
        if not sent_via_js:
            await self._page.keyboard.press("Enter")

        if not await self._message_text_visible(message):
            await self._dismiss_message_ui()
            return self._message_action_result(
                self._page.url,
                "send_unavailable",
                "LinkedIn did not confirm that the message was sent.",
                recipient_selected=recipient_selected,
            )

        return self._message_action_result(
            self._page.url,
            "sent",
            "Message sent.",
            recipient_selected=recipient_selected,
            sent=True,
        )

    async def reply_to_conversation(
        self,
        thread_id: str,
        message: str,
        *,
        confirm_send: bool,
    ) -> dict[str, Any]:
        """Reply inside an existing messaging thread with explicit confirmation gating.

        Replying in-thread works for InMail and recruiter threads whose sender
        exposes no Message action on their profile (non-connections), so it is
        the reliable path for answering recruiters. Navigation is by thread URL;
        the composer and Send control are located by role, contenteditable, and
        aria-label attributes only, never by layout class names.

        Args:
            thread_id: LinkedIn messaging thread ID.
            message: Reply text to send.
            confirm_send: Must be True to actually send (False does a dry run
                that only verifies the reply box is available).
        """
        thread_id = (thread_id or "").strip()
        if not re.fullmatch(r"[A-Za-z0-9_+=-]{1,128}", thread_id):
            raise LinkedInScraperException("Invalid conversation thread ID.")
        if not (message or "").strip():
            raise LinkedInScraperException("Reply text is required.")

        thread_url = f"https://www.linkedin.com/messaging/thread/{thread_id}/"
        await self._navigate_to_page(thread_url)
        await detect_rate_limit(self._page)
        try:
            await self._page.wait_for_selector("main")
        except PlaywrightTimeoutError:
            logger.debug("Thread page did not fully load for %s", thread_id)
        await handle_modal_close(self._page)

        if "/messaging/thread/" not in (self._page.url or ""):
            return self._message_action_result(
                self._page.url,
                "thread_unavailable",
                "LinkedIn did not open this conversation thread.",
            )

        compose_box = await self._resolve_message_compose_box()
        if compose_box is None:
            return self._message_action_result(
                self._page.url,
                "composer_unavailable",
                "LinkedIn did not show a reply box for this conversation. It may be closed or need a reply from LinkedIn directly.",
            )

        if not confirm_send:
            return self._message_action_result(
                self._page.url,
                "confirmation_required",
                "Set confirm_send=true to send the reply.",
                recipient_selected=True,
            )

        # Same patchright workaround as send_message: focus through evaluate()
        # (no actionability wait) and type through the keyboard so React sees
        # real input events. The focus script reports any pre-existing draft so
        # it can be cleared first instead of being sent along with the reply.
        focus = await self._page.evaluate(
            """() => {
                const selectors = [
                    'main div[role="textbox"][contenteditable="true"]',
                    'div[role="textbox"][contenteditable="true"][aria-label*="Write a message"]',
                    'div[role="textbox"][contenteditable="true"]',
                ];
                for (const selector of selectors) {
                    const el = document.querySelector(selector);
                    if (!el) continue;
                    el.focus();
                    return { ok: true, existing: (el.innerText || '').trim().length };
                }
                return { ok: false, existing: 0 };
            }"""
        )
        if not isinstance(focus, dict) or not focus.get("ok"):
            return self._message_action_result(
                self._page.url,
                "compose_interact_failed",
                "Could not focus the reply box.",
                recipient_selected=True,
            )
        if focus.get("existing"):
            await self._page.keyboard.press("ControlOrMeta+A")
            await self._page.keyboard.press("Backspace")
            await asyncio.sleep(0.2)

        await asyncio.sleep(0.1)
        await self._page.keyboard.type(message, delay=15)
        await asyncio.sleep(1.0)  # allow React to enable the Send control

        sent_via_js = await self._page.evaluate(
            """() => {
                const scope = document.querySelector('main') || document;
                const btn = Array.from(scope.querySelectorAll(
                    'button[type="submit"], button[aria-label*="Send"], button[aria-label*="send"],'
                    + 'button[data-control-name="send"]'
                )).find(b => !b.disabled && (b.offsetWidth || b.offsetHeight || b.getClientRects().length));
                if (!btn) return false;
                btn.click();
                return true;
            }"""
        )
        if not sent_via_js:
            await self._page.keyboard.press("Enter")

        if not await self._reply_delivered(message):
            return self._message_action_result(
                self._page.url,
                "send_unavailable",
                "LinkedIn did not confirm that the reply was sent. Check the conversation before retrying.",
                recipient_selected=True,
            )

        return self._message_action_result(
            self._page.url,
            "sent",
            "Reply sent.",
            recipient_selected=True,
            sent=True,
        )

    async def _reply_delivered(self, message: str) -> bool:
        """Confirm a thread reply left the composer and appears in the thread.

        Checking only that the text is on the page would also match text still
        sitting unsent in the reply box, so the composer must be empty too.
        """
        try:
            await self._page.wait_for_function(
                """({ expected }) => {
                    const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                    const target = normalize(expected);
                    const composer = document.querySelector(
                        'main div[role="textbox"][contenteditable="true"], div[role="textbox"][contenteditable="true"]'
                    );
                    const composerText = normalize(composer ? composer.innerText : '');
                    if (composerText.includes(target)) return false;
                    const root = document.querySelector('main') || document.body;
                    return normalize(root ? root.innerText : '').includes(target);
                }""",
                arg={"expected": message[:300]},
            )
            return True
        except PlaywrightTimeoutError:
            return False

    async def easy_apply(
        self,
        job_id: str,
        *,
        answers: Mapping[str, str] | None = None,
        resume_path: str | None = None,
        submit: bool = False,
        profile: Mapping[str, str] | None = None,
        callbacks: ProgressCallback | None = None,
    ) -> dict[str, Any]:
        """Fill (and optionally submit) a LinkedIn Easy Apply application.

        Opens ``/jobs/view/{job_id}/``, finds the apply control inside the
        job header, tells Easy Apply from an external apply by what the click
        does (dialog vs. popup/navigation), then walks the ``role="dialog"``
        form step by step: reads every question, fills it from ``answers``
        (keyed by the label LinkedIn shows) and ``profile`` (canonical
        contact fields), uploads ``resume_path`` on the resume step, unchecks
        the follow-company box and advances with the footer's primary button.
        It never guesses: a required question without an answer stops the run
        with ``needs_review``; ``submit=False`` stops at the review step with
        ``filled``; ``submit=True`` presses the final button and reports
        ``submitted`` only after LinkedIn's confirmation.

        Detection is structural wherever LinkedIn allows it; the English
        strings it still depends on live in
        :data:`linkedin_mcp_server.scraping.easy_apply.EASY_APPLY_LABELS`
        with the limitation documented there.

        Returns the shape from docs/auto-apply-spec.md section 4:
        ``{status, questions, unanswered, external_url, evidence, steps, error}``.
        Raises :class:`LinkedInScraperException` for invalid input (job id,
        missing resume file) and lets authentication/rate-limit errors
        propagate so the tool layer can recover the session.
        """
        try:
            clean_job_id = _ea.validate_job_id(job_id)
            clean_resume = _ea.validate_resume_path(resume_path)
        except ValueError as exc:
            raise LinkedInScraperException(str(exc)) from exc
        run = _EasyApplyRun(
            self,
            clean_job_id,
            answers={str(k): str(v) for k, v in (answers or {}).items()},
            resume_path=clean_resume,
            submit=bool(submit),
            profile={str(k): str(v) for k, v in (profile or {}).items()},
            callbacks=callbacks,
        )
        return await run.execute()

    async def _extract_root_content(
        self,
        selectors: list[str],
    ) -> dict[str, Any]:
        """Extract innerText and raw anchor metadata from the first matching root."""
        result = await self._page.evaluate(
            """({ selectors }) => {
                const normalize = value => (value || '').replace(/\\s+/g, ' ').trim();
                const containerSelector = 'section, article, li, div';
                const headingSelector = 'h1, h2, h3';
                const directHeadingSelector = ':scope > h1, :scope > h2, :scope > h3';
                const MAX_HEADING_CONTAINERS = 300;
                const MAX_REFERENCE_ANCHORS = 500;

                const getHeadingText = element => {
                    if (!element) return '';

                    const heading =
                        element.matches && element.matches(headingSelector)
                            ? element
                            : element.querySelector
                              ? element.querySelector(directHeadingSelector)
                              : null;

                    return normalize(heading?.innerText || heading?.textContent);
                };

                const getPreviousHeading = node => {
                    let sibling = node?.previousElementSibling || null;
                    for (let index = 0; sibling && index < 3; index += 1) {
                        const heading = getHeadingText(sibling);
                        if (heading) {
                            return heading;
                        }
                        sibling = sibling.previousElementSibling;
                    }
                    return '';
                };

                const root = selectors
                    .map(selector => document.querySelector(selector))
                    .find(Boolean);
                const source = root ? 'root' : 'body';
                const container = root || document.body;
                const text = container ? (container.innerText || '').trim() : '';
                const headingMap = new WeakMap();

                const candidateContainers = [
                    container,
                    ...Array.from(container.querySelectorAll(containerSelector)).slice(
                        0,
                        MAX_HEADING_CONTAINERS,
                    ),
                ];
                candidateContainers.forEach(node => {
                    const ownHeading = getHeadingText(node);
                    const previousHeading = getPreviousHeading(node);
                    const heading = ownHeading || previousHeading;
                    if (heading) {
                        headingMap.set(node, heading);
                    }
                });

                const findHeading = element => {
                    let current = element.closest(containerSelector) || container;
                    for (let depth = 0; current && depth < 4; depth += 1) {
                        const heading = headingMap.get(current);
                        if (heading) {
                            return heading;
                        }
                        if (current === container) {
                            break;
                        }
                        current = current.parentElement?.closest(containerSelector) || null;
                    }
                    return '';
                };

                const references = Array.from(container.querySelectorAll('a[href]'))
                    .slice(0, MAX_REFERENCE_ANCHORS)
                    .map(anchor => {
                        const rawHref = (anchor.getAttribute('href') || '').trim();
                        if (!rawHref || rawHref === '#') {
                            return null;
                        }

                        const href = rawHref.startsWith('#')
                            ? rawHref
                            : (anchor.href || rawHref);

                        return {
                            href,
                            text: normalize(anchor.innerText || anchor.textContent),
                            aria_label: normalize(anchor.getAttribute('aria-label')),
                            title: normalize(anchor.getAttribute('title')),
                            heading: findHeading(anchor),
                            in_article: Boolean(anchor.closest('article')),
                            in_nav: Boolean(anchor.closest('nav')),
                            in_footer: Boolean(anchor.closest('footer')),
                        };
                    })
                    .filter(Boolean);

                return { source, text, references };
            }""",
            {"selectors": selectors},
        )
        return result


class _EasyApplyRun:
    """State for one Easy Apply attempt (see ``LinkedInExtractor.easy_apply``).

    Owns the step log, the questions seen so far and the browser-level
    helpers; the decisions about *what* to fill and how to classify what the
    page shows are delegated to the pure ``scraping.easy_apply`` module.
    """

    def __init__(
        self,
        extractor: LinkedInExtractor,
        job_id: str,
        *,
        answers: dict[str, str],
        resume_path: str | None,
        submit: bool,
        profile: dict[str, str],
        callbacks: ProgressCallback | None,
    ) -> None:
        self._extractor = extractor
        self._page = extractor._page
        self.job_id = job_id
        self.job_url = f"{_LINKEDIN_ORIGIN}/jobs/view/{job_id}/"
        self.answers = answers
        self.resume_path = resume_path
        self.submit = submit
        self.profile = profile
        self._callbacks = callbacks
        self.steps: list[str] = []
        self._questions: dict[tuple[str, str], _ea.EasyApplyQuestion] = {}
        self._clicked_primary = False
        self._last_action: _ea.StepAction | None = None

    # ------------------------------------------------------------------
    # Bookkeeping
    # ------------------------------------------------------------------

    def _step(self, message: str) -> None:
        self.steps.append(message)
        logger.debug("easy_apply[%s]: %s", self.job_id, message)

    async def _progress(self, message: str, percent: int) -> None:
        if self._callbacks is None:
            return
        try:
            await self._callbacks.on_progress(message, percent)
        except Exception:
            logger.debug("Progress callback failed", exc_info=True)

    def _merge(self, questions: list[_ea.EasyApplyQuestion]) -> None:
        for question in questions:
            self._questions[(question.label, question.kind)] = question

    @property
    def _key_args(self) -> dict[str, Any]:
        return {"keyAttr": _EASY_APPLY_KEY_ATTR}

    # ------------------------------------------------------------------
    # Entry
    # ------------------------------------------------------------------

    async def execute(self) -> dict[str, Any]:
        try:
            return await self._run()
        except LinkedInScraperException:
            raise
        except Exception as exc:
            logger.warning(
                "Easy Apply for job %s failed: %s", self.job_id, exc, exc_info=True
            )
            self._step(f"Stopped: {type(exc).__name__}: {exc}")
            return await self._finish(
                "failed",
                error=f"{type(exc).__name__}: {exc}",
                close_dialog=True,
            )

    async def _run(self) -> dict[str, Any]:
        await self._progress("Opening the job page", 5)
        await self._extractor._navigate_to_page(self.job_url)
        await detect_rate_limit(self._page)
        try:
            await self._page.wait_for_selector(
                "main", timeout=_EASY_APPLY_DIALOG_TIMEOUT_MS
            )
        except PlaywrightTimeoutError:
            logger.debug("Job page %s has no <main>", self.job_url)
        await handle_modal_close(self._page)
        self._step("Opened the job page")

        if "/jobs/view/" not in urlparse(self._page.url or "").path:
            return await self._finish(
                "failed",
                error=f"LinkedIn did not open the job page (now at {self._page.url}).",
            )

        header = await self._wait_for_header()
        if header is None:
            return await self._finish(
                "failed",
                error=(
                    "Could not find the job header (no title on the page). "
                    "The posting may be unavailable or the page did not load"
                    f"{await self._page_brief()}."
                ),
            )
        controls = list(header.get("controls") or [])
        control = _ea.choose_apply_control(controls)
        if control is None:
            if _ea.header_shows_applied(str(header.get("text", ""))):
                self._step("The job header shows the application as already sent")
                return await self._finish("already_applied")
            return await self._finish(
                "failed",
                error=(
                    "No apply control was recognised in the job header "
                    f"(controls seen: {_ea.describe_controls(controls)}). "
                    + _ea.LOCALE_LIMITATION
                ),
            )
        self._step(f"Found the apply control: {control.text or control.kind}")

        await self._progress("Opening the application", 15)
        observed = await self._click_apply_control(control)
        outcome = observed.get("outcome")
        if outcome in {"popup", "navigated"}:
            external_url = str(observed.get("url") or control.href or "") or None
            self._step(f"The apply control opened an external site: {external_url}")
            return await self._finish(
                "external", external_url=external_url, screenshot=False
            )
        if outcome != "dialog":
            if control.kind == "external_anchor" and control.href:
                self._step(f"The apply link points to an external site: {control.href}")
                return await self._finish(
                    "external", external_url=control.href, screenshot=False
                )
            return await self._finish(
                "failed",
                error=(
                    "Clicking the apply control opened neither the Easy Apply "
                    "dialog nor an external site."
                ),
            )
        self._step("The Easy Apply dialog opened")
        return await self._walk_dialog()

    # ------------------------------------------------------------------
    # Job header and apply control
    # ------------------------------------------------------------------

    async def _read_header(self) -> dict[str, Any] | None:
        try:
            header = await self._page.evaluate(_EASY_APPLY_HEADER_JS, self._key_args)
        except Exception:
            logger.debug("Job header probe failed", exc_info=True)
            return None
        return header if isinstance(header, dict) else None

    async def _wait_for_header(self) -> dict[str, Any] | None:
        """Read the job header, giving a page that is still drawing itself time to finish.

        LinkedIn renders ``<main>`` first and fills the top card in afterwards, so a single read
        straight after ``<main>`` appears can see no title or no buttons yet. Poll until the title
        and at least one control are there (or the budget runs out) and return the last reading.
        """
        loop = asyncio.get_running_loop()
        deadline = loop.time() + _EASY_APPLY_HEADER_WAIT_SECONDS
        header = await self._read_header()
        while not (header and header.get("controls")) and loop.time() < deadline:
            await asyncio.sleep(_EASY_APPLY_HEADER_POLL_SECONDS)
            header = await self._read_header() or header
        return header

    async def _page_brief(self) -> str:
        """A short description of what the browser is showing, for an error the person has to act on."""
        try:
            title = str(await self._page.title() or "").strip()
        except Exception:
            return ""
        return f" (the tab says: {title[:80]!r})" if title else ""

    async def _header_applied(self) -> bool:
        header = await self._read_header()
        return header is not None and _ea.header_shows_applied(
            str(header.get("text", ""))
        )

    async def _click_key(self, key: str) -> bool:
        """Click a keyed element: real click first, JS click as fallback."""
        selector = f'[{_EASY_APPLY_KEY_ATTR}="{key}"]'
        try:
            await self._page.locator(selector).first.click(timeout=5000)
            return True
        except Exception:
            logger.debug(
                "Locator click on %s failed; using JS click", key, exc_info=True
            )
        try:
            clicked = await self._page.evaluate(
                _EASY_APPLY_CLICK_KEY_JS, {**self._key_args, "key": key}
            )
        except Exception:
            logger.debug("JS click on %s failed", key, exc_info=True)
            return False
        return bool(clicked)

    async def _click_apply_control(self, control: _ea.ApplyControl) -> dict[str, Any]:
        """Click the apply control and report what happened.

        Outcomes: ``dialog`` (Easy Apply form opened), ``popup`` (a new tab
        opened; its URL is captured and the tab closed), ``navigated`` (the
        page left ``/jobs/view/``; the browser goes back), ``nothing``.
        """
        popups: list[Any] = []

        def on_popup(popup: Any) -> None:
            popups.append(popup)

        start_url = self._page.url or ""
        self._page.on("popup", on_popup)
        try:
            if not await self._click_key(control.key):
                return {"outcome": "nothing"}
            loop = asyncio.get_running_loop()
            deadline = loop.time() + _EASY_APPLY_OBSERVE_SECONDS
            while True:
                if popups:
                    break
                current = self._page.url or ""
                if current != start_url and "/jobs/view/" not in urlparse(current).path:
                    await self._return_to_job_page()
                    return {"outcome": "navigated", "url": current}
                if await self._dialog_state() is not None:
                    return {"outcome": "dialog"}
                if loop.time() >= deadline:
                    return {"outcome": "nothing"}
                await asyncio.sleep(0.25)
        finally:
            self._page.remove_listener("popup", on_popup)
        url = await self._settle_popup(popups[0], control.href)
        return {"outcome": "popup", "url": url}

    async def _settle_popup(self, popup: Any, fallback: str) -> str:
        try:
            await popup.wait_for_load_state(
                "domcontentloaded", timeout=_EASY_APPLY_DIALOG_TIMEOUT_MS
            )
        except Exception:
            logger.debug("Popup did not finish loading", exc_info=True)
        url = ""
        for _ in range(8):
            try:
                url = popup.url or ""
            except Exception:
                url = ""
            if url and url != "about:blank":
                break
            await asyncio.sleep(0.25)
        try:
            await popup.close()
        except Exception:
            logger.debug("Could not close popup", exc_info=True)
        return _ea.external_url_from_popup(url, fallback)

    async def _return_to_job_page(self) -> None:
        try:
            await self._page.go_back(timeout=_EASY_APPLY_DIALOG_TIMEOUT_MS)
        except Exception:
            logger.debug("Could not navigate back to the job page", exc_info=True)

    # ------------------------------------------------------------------
    # Dialog walk
    # ------------------------------------------------------------------

    async def _dialog_state(self) -> dict[str, Any] | None:
        try:
            state = await self._page.evaluate(_EASY_APPLY_DIALOG_JS, self._key_args)
        except Exception:
            logger.debug("Dialog probe failed", exc_info=True)
            return None
        return state if isinstance(state, dict) else None

    async def _wait_for_dialog_state(self, seconds: float) -> dict[str, Any] | None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + seconds
        while True:
            state = await self._dialog_state()
            if state is not None or loop.time() >= deadline:
                return state
            await asyncio.sleep(0.25)

    @staticmethod
    def _primary_of(state: dict[str, Any]) -> tuple[str, str]:
        primary = state.get("primary") or {}
        return str(primary.get("text", "")), str(primary.get("aria_label", ""))

    def _looks_submitted(self, state: dict[str, Any]) -> bool:
        if not self._clicked_primary:
            return False
        if _ea.dialog_text_confirms_sent(str(state.get("text", ""))):
            return True
        # Structural fallback, only after an unrecognised (non-English) button:
        # the form controls and the progress bar are gone.
        return (
            self._last_action == "unknown"
            and not state.get("questions")
            and int(state.get("control_count") or 0) == 0
            and not state.get("has_progress")
        )

    async def _walk_dialog(self) -> dict[str, Any]:
        previous_signature: tuple[Any, ...] | None = None
        last_primary = ""
        for step_no in range(1, _ea.MAX_STEPS + 1):
            state = await self._wait_for_dialog_state(_EASY_APPLY_ADVANCE_WAIT_SECONDS)
            if state is None:
                if self._clicked_primary and await self._header_applied():
                    self._step(
                        "The dialog closed and the job header shows the application as sent"
                    )
                    return await self._finish("submitted", close_dialog=False)
                return await self._finish(
                    "failed",
                    error="The Easy Apply dialog closed before the application was completed.",
                )
            if self._looks_submitted(state):
                return await self._finish_submitted(str(state.get("text", "")))

            questions = _ea.questions_from_dom(list(state.get("questions") or []))
            primary_text, primary_aria = self._primary_of(state)
            signature = _ea.step_signature(questions, primary_text)
            if signature == previous_signature:
                # The step did not change after the last press: either LinkedIn
                # flagged an answer inline or the form is stuck.
                flagged = [q for q in questions if q.error]
                if flagged:
                    _ea.finalize_statuses(questions, set(), {})
                    self._merge(questions)
                    self._step(
                        "LinkedIn flagged: "
                        + ", ".join(f"{q.label!r} ({q.error})" for q in flagged)
                    )
                    return await self._finish("needs_review", close_dialog=True)
                return await self._finish(
                    "failed",
                    error=f"The Easy Apply form did not advance after pressing {last_primary!r}.",
                    close_dialog=True,
                )
            previous_signature = signature

            title = str(state.get("title") or "").strip() or f"step {step_no}"
            await self._progress(f"Filling {title}", min(20 + step_no * 10, 80))
            actions, unmatched = _ea.plan_fill(
                questions, self.answers, self.profile, self.resume_path
            )
            notes = {q.label: q.message for q in questions if q.message}
            await self._apply_actions(actions, {q.key: q for q in questions}, notes)

            refreshed = await self._dialog_state()
            if refreshed is not None:
                questions = _ea.questions_from_dom(
                    list(refreshed.get("questions") or [])
                )
                primary_text, primary_aria = self._primary_of(refreshed)
                state = refreshed
            _ea.finalize_statuses(questions, unmatched, notes)
            self._merge(questions)

            unanswered = _ea.unanswered_of(questions)
            if unanswered:
                self._step(
                    "Stopped: required questions without an answer: "
                    + ", ".join(repr(q.label) for q in unanswered)
                )
                return await self._finish("needs_review", close_dialog=True)

            if not primary_text:
                return await self._finish(
                    "failed",
                    error="The Easy Apply dialog has no footer button to continue with.",
                    close_dialog=True,
                )
            action = _ea.classify_step_button(primary_text, primary_aria)
            if not self.submit and action in {"submit", "unknown"}:
                if action == "submit":
                    self._step(
                        "Stopped at the review step without submitting (submit is false)"
                    )
                else:
                    self._step(
                        f"Stopped before the unrecognised footer button {primary_text!r} "
                        f"(submit is false). {_ea.LOCALE_LIMITATION}"
                    )
                return await self._finish("filled", close_dialog=True)
            if action == "submit":
                return await self._submit_application(primary_text, state)
            if action == "unknown":
                self._step(
                    f"Footer button {primary_text!r} is not in the locale table; "
                    "pressing it because submit is true"
                )
            last_primary = primary_text
            self._last_action = action
            await self._press_primary(primary_text)

        return await self._finish(
            "failed",
            error=f"Gave up after {_ea.MAX_STEPS} dialog steps.",
            close_dialog=True,
        )

    async def _press_primary(self, text: str) -> None:
        if not await self._click_key("primary"):
            raise LinkedInScraperException(
                f"Could not press the dialog button {text!r}."
            )
        self._clicked_primary = True
        self._step(f"Pressed {text!r}")
        await asyncio.sleep(0.5)

    async def _submit_application(
        self, primary_text: str, state: dict[str, Any]
    ) -> dict[str, Any]:
        await self._progress("Submitting the application", 85)
        previous_text = str(state.get("text", ""))
        self._last_action = "submit"
        await self._press_primary(primary_text)
        value: Any = None
        try:
            handle = await self._page.wait_for_function(
                _EASY_APPLY_POST_SUBMIT_JS,
                arg={
                    "sentLabels": list(_ea.application_sent_labels()),
                    "previousText": previous_text,
                },
                timeout=_EASY_APPLY_SUBMIT_TIMEOUT_MS,
            )
            value = await handle.json_value()
        except PlaywrightTimeoutError:
            value = None
        if not isinstance(value, dict):
            return await self._finish(
                "failed",
                error=(
                    "LinkedIn did not confirm the submission within the time limit. "
                    "Check the job page before retrying."
                ),
                close_dialog=True,
            )
        if value.get("state") == "closed":
            if await self._header_applied():
                self._step(
                    "The dialog closed and the job header shows the application as sent"
                )
                return await self._finish("submitted", close_dialog=False)
            return await self._finish(
                "failed",
                error=(
                    "The dialog closed after submit but LinkedIn showed no "
                    "confirmation. Check the job page before retrying."
                ),
                close_dialog=False,
            )
        return await self._finish_submitted(str(value.get("text", "")))

    async def _finish_submitted(self, confirmation_text: str) -> dict[str, Any]:
        self._step("LinkedIn confirmed the application was sent")
        await self._progress("Capturing the confirmation", 95)
        return await self._finish(
            "submitted", confirmation_text=confirmation_text, close_dialog=True
        )

    # ------------------------------------------------------------------
    # Filling
    # ------------------------------------------------------------------

    async def _apply_actions(
        self,
        actions: list[_ea.FillAction],
        by_key: dict[str, _ea.EasyApplyQuestion],
        notes: dict[str, str],
    ) -> None:
        for action in actions:
            question = by_key.get(action.key)
            label = question.label if question else action.key
            selector = f'[{_EASY_APPLY_KEY_ATTR}="{action.key}"]'
            try:
                if action.action == "fill":
                    await self._fill_text(action.key, selector, action.value)
                    picked = None
                    if question is not None and question.combobox:
                        picked = await self._pick_combobox_option(action.value)
                    self._step(
                        f"Filled {label!r}"
                        + (f" and chose {picked!r}" if picked else "")
                    )
                elif action.action == "select":
                    await self._page.locator(selector).first.select_option(
                        index=action.index, timeout=5000
                    )
                    chosen = str(action.index)
                    if question is not None and action.index in question.option_indices:
                        chosen = question.options[
                            question.option_indices.index(action.index)
                        ]
                    self._step(f"Selected {chosen!r} for {label!r}")
                elif action.action in {"radio", "check"}:
                    ok = await self._page.evaluate(
                        _EASY_APPLY_SET_CHOICE_JS,
                        {
                            **self._key_args,
                            "key": action.key,
                            "option": action.option,
                            "checked": True
                            if action.action == "radio"
                            else action.checked,
                        },
                    )
                    if not ok:
                        raise LinkedInScraperException("choice control not found")
                    if action.action == "radio":
                        self._step(f"Chose {action.option!r} for {label!r}")
                    else:
                        verb = "Checked" if action.checked else "Unchecked"
                        target = (
                            f"{action.option!r} for {label!r}"
                            if action.option
                            else repr(label)
                        )
                        self._step(f"{verb} {target}")
                elif action.action == "file":
                    await self._page.locator(selector).first.set_input_files(
                        action.value, timeout=10000
                    )
                    name = Path(action.value).name
                    self._step(f"Uploaded {name} for {label!r}")
                    notes[label] = f"Uploaded {name}."
            except Exception as exc:
                logger.debug(
                    "Easy Apply action %s on %r failed: %s",
                    action.action,
                    label,
                    exc,
                    exc_info=True,
                )
                notes[label] = f"Could not fill this field ({type(exc).__name__})."
                self._step(f"Could not fill {label!r}")

    async def _fill_text(self, key: str, selector: str, value: str) -> None:
        try:
            await self._page.locator(selector).first.fill(value, timeout=5000)
            return
        except Exception:
            logger.debug("fill() failed for %s; setting the value via JS", key)
        ok = await self._page.evaluate(
            _EASY_APPLY_SET_VALUE_JS, {**self._key_args, "key": key, "value": value}
        )
        if not ok:
            raise LinkedInScraperException("text control not found")

    async def _pick_combobox_option(self, value: str) -> str | None:
        try:
            await self._page.wait_for_selector(
                '[role="option"]', state="visible", timeout=2000
            )
        except PlaywrightTimeoutError:
            return None
        try:
            picked = await self._page.evaluate(
                _EASY_APPLY_PICK_OPTION_JS, {"value": value}
            )
        except Exception:
            logger.debug("Typeahead option pick failed", exc_info=True)
            return None
        await asyncio.sleep(0.2)
        return picked if isinstance(picked, str) and picked else None

    # ------------------------------------------------------------------
    # Evidence and cleanup
    # ------------------------------------------------------------------

    async def _capture_evidence(
        self, confirmation_text: str, screenshot: bool
    ) -> dict[str, Any]:
        page_text = ""
        try:
            text = await self._page.evaluate(_EASY_APPLY_EVIDENCE_TEXT_JS)
            page_text = text if isinstance(text, str) else ""
        except Exception:
            logger.debug("Could not read page text for evidence", exc_info=True)
        screenshot_b64: str | None = None
        if screenshot:
            try:
                png = await self._page.screenshot(type="png")
                screenshot_b64 = base64.b64encode(png).decode("ascii")
            except Exception:
                logger.debug("Could not take a screenshot", exc_info=True)
        return {
            "page_text": page_text[:20000],
            "confirmation_text": confirmation_text,
            "screenshot_base64": screenshot_b64,
            "url": self._page.url,
        }

    async def _close_dialogs(self) -> None:
        """Close whatever dialog is open without touching form buttons.

        Prefers the dialog's own dismiss control (icon-only labelled button),
        then Escape. A follow-up prompt *without form controls* (LinkedIn's
        "Save this application?") is answered with its primary button, which
        keeps the draft; a dialog that still holds form controls is never
        advanced from here.
        """
        for _ in range(4):
            state = await self._dialog_state()
            if state is None:
                return
            dismissed = False
            try:
                dismissed = bool(
                    await self._page.evaluate(
                        _EASY_APPLY_CLICK_KEY_JS, {**self._key_args, "key": "dismiss"}
                    )
                )
            except Exception:
                logger.debug("Dismiss click failed", exc_info=True)
            if not dismissed:
                try:
                    await self._page.keyboard.press("Escape")
                except Exception:
                    logger.debug("Escape failed", exc_info=True)
            await asyncio.sleep(0.4)
            after = await self._dialog_state()
            if after is None:
                self._step("Closed the dialog")
                return
            if (
                int(after.get("control_count") or 0) == 0
                and after.get("text") != state.get("text")
                and after.get("primary")
            ):
                primary_text, _ = self._primary_of(after)
                await self._click_key("primary")
                self._step(f"Answered the follow-up prompt with {primary_text!r}")
                await asyncio.sleep(0.4)

    async def _finish(
        self,
        status: _ea.Status,
        *,
        error: str = "",
        external_url: str | None = None,
        confirmation_text: str = "",
        screenshot: bool = True,
        close_dialog: bool = False,
    ) -> dict[str, Any]:
        evidence = await self._capture_evidence(confirmation_text, screenshot)
        if close_dialog:
            await self._close_dialogs()
        return _ea.build_result(
            status,
            list(self._questions.values()),
            evidence=evidence,
            steps=self.steps,
            error=error,
            external_url=external_url,
        )
