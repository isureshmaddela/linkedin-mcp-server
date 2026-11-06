"""Routes behind Careers > Profile & resume > AI instructions (also Settings > AI instructions).

The person's own instructions and skills live with the account's other settings (``app_ai_profile``); in the hosted
build that is the candidate's own workspace. The organization's instructions and skills come from the hosted build
(``HOOKS.org_ai_profile``) and are read-only here: owners and admins edit them through ``/api/org/ai-profile``.
"""

from __future__ import annotations

from typing import Any

from aira import ai_instructions, model_provider
from aira.airaapi.http import Request, aira_route

SETTING = "ai_profile"


def personal_profile(setting: Any) -> dict[str, Any]:
    """The person's own profile from a settings reader (``request.setting`` or the app's ``_setting``)."""
    try:
        return ai_instructions.load(setting(SETTING))
    except Exception:  # noqa: BLE001 - an unreadable setting never stops a model call
        return ai_instructions.empty_profile()


def organization_profile() -> dict[str, Any] | None:
    """The organization's profile in the hosted build (None in the local build or when it cannot be read)."""
    from aira.hosting import HOOKS

    if HOOKS.org_ai_profile is None:
        return None
    try:
        return HOOKS.org_ai_profile()
    except Exception:  # noqa: BLE001 - no tenant bound, a key problem: the call goes on without it
        return None


def standing_text(setting: Any, purpose: str, *, note: str = "") -> str:
    """The text one Careers call adds to its system prompt."""
    return ai_instructions.compose(
        purpose, organization_profile(), personal_profile(setting), note=note
    )


def _status(request: Request) -> dict[str, Any]:
    organization = organization_profile()
    return {
        "personal": ai_instructions.public(personal_profile(request.setting)),
        "organization": ai_instructions.public(organization, can_edit=False)
        if organization is not None
        else None,
        "hosted": organization is not None,
        **ai_instructions.catalog(),
    }


@aira_route("GET", r"/api/settings/ai-profile")
def ai_profile_status(request: Request) -> tuple[int, dict[str, Any]]:
    return 200, _status(request)


@aira_route("PUT", r"/api/settings/ai-profile")
def ai_profile_save(request: Request) -> tuple[int, dict[str, Any]]:
    """Save the person's own instructions and skills (the whole profile; the page sends what it shows)."""
    profile = ai_instructions.validate_profile(request.payload)
    with request.connect() as connection:
        model_provider.save_settings(
            connection, {SETTING: ai_instructions.dump(profile)}
        )
        connection.commit()
    request.audit(
        "settings.ai_profile",
        "settings",
        SETTING,
        {
            "instructions_chars": len(profile["instructions"]),
            "skills": len(profile["skills"]),
            "enabled": sum(1 for skill in profile["skills"] if skill["enabled"]),
        },
    )
    return 200, _status(request)


@aira_route("POST", r"/api/settings/ai-profile/preview")
def ai_profile_preview(request: Request) -> tuple[int, dict[str, Any]]:
    """Exactly what one task's model calls receive after the fixed rule (nothing is sent to a model)."""
    task = str(request.payload.get("task") or "").strip()
    if task not in ai_instructions.TASKS:
        raise ValueError(
            "Choose a task to preview: " + ", ".join(ai_instructions.TASKS) + "."
        )
    text = standing_text(request.setting, task)
    return 200, {
        "task": task,
        "label": ai_instructions.TASKS[task],
        "text": text,
        "chars": len(text),
        "rule": model_provider.SYSTEM_RULE,
    }
