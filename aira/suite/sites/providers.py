"""The models Sites can build with, and one streaming call per kind of API.

* ``builtin``: Monosage's offline designer (:mod:`aira.suite.sites.designer`): no model, always available.
* ``ollama`` / ``lmstudio``: models on this computer (Ollama's ``/api/chat``, LM Studio's OpenAI-compatible server).
* ``claude`` (Anthropic Messages API), ``gemini`` (Google's Generative Language API), ``openai``, ``openrouter`` and
  ``custom`` (any OpenAI-compatible server: vLLM, Groq, Together, DeepSeek, a company gateway, ...).
* ``platform`` (hosted build only): the organization's model the platform provides, through the suite gateway.

Every call streams: :func:`stream` hands each piece of text to ``on_text`` as it arrives and checks ``should_stop``
between pieces, so the page can show the code being written and a person can stop a run. Keys are secrets kept in
the account's encrypted store (``sites_key_<provider>``); they never appear in a response, a log line or an error.
In the hosted build a custom address must be a public ``https`` address, and the local providers are not offered.
"""

from __future__ import annotations

import ipaddress
import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote, urlsplit

from aira.suite import kit

KEY_PREFIX = "sites_key_"
CONNECT_TIMEOUT = 30.0
READ_TIMEOUT = 300.0  # a local model may take minutes to load before the first token
MAX_OUTPUT_CHARS = 2_500_000
MAX_TOKENS = 32000  # Anthropic needs a ceiling; long sites stream well under it
ALLOW_LOCAL_ENV = "AIRA_SITES_ALLOW_LOCAL_URLS"  # tests: let the hosted build call a stand-in on 127.0.0.1


class ModelError(RuntimeError):
    """A model call failed in a way a person can act on (the server answers 502 with this sentence)."""


class Stopped(Exception):
    """The person stopped the run."""


@dataclass(frozen=True)
class Provider:
    id: str
    label: str
    kind: str  # builtin | ollama | openai | anthropic | gemini | platform
    base_url: str = ""
    default_model: str = ""
    needs_key: bool = False
    key_optional: bool = False
    local: bool = False  # runs on this computer (not offered in the hosted build)
    models: tuple[str, ...] = ()
    help: str = ""
    key_url: str = ""
    hosted_only: bool = False
    extra_headers: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "kind": self.kind,
            "base_url": self.base_url,
            "default_model": self.default_model,
            "needs_key": self.needs_key,
            "key_optional": self.key_optional,
            "local": self.local,
            "models": list(self.models),
            "help": self.help,
            "key_url": self.key_url,
        }


PROVIDERS: dict[str, Provider] = {
    p.id: p
    for p in (
        Provider(
            "builtin",
            "Monosage designer",
            "builtin",
            default_model="designer-1",
            models=("designer-1",),
            help="Works offline with no model: builds a polished site from your words and applies simple changes "
            "(colors, dark mode, sections, the name). Connect a model for anything you can describe.",
        ),
        Provider(
            "ollama",
            "Ollama (on this computer)",
            "ollama",
            base_url=os.getenv("OLLAMA_URL", "http://127.0.0.1:11434"),
            default_model="qwen2.5-coder:7b",
            local=True,
            models=(
                "qwen2.5-coder:7b",
                "qwen2.5-coder:14b",
                "llama3.1:8b",
                "deepseek-coder-v2",
                "gemma3:12b",
            ),
            help="Free and private: the model runs on your computer. Install Ollama, then run "
            "'ollama pull qwen2.5-coder:7b'.",
            key_url="https://ollama.com/download",
        ),
        Provider(
            "lmstudio",
            "LM Studio (on this computer)",
            "openai",
            base_url="http://127.0.0.1:1234/v1",
            local=True,
            key_optional=True,
            help="Start LM Studio's local server (Developer > Start server) and load a coding model.",
            key_url="https://lmstudio.ai",
        ),
        Provider(
            "claude",
            "Claude (Anthropic)",
            "anthropic",
            base_url="https://api.anthropic.com",
            default_model="claude-sonnet-5-5",
            needs_key=True,
            models=(
                "claude-sonnet-5-5",
                "claude-opus-5-5",
                "claude-haiku-5-5",
                "claude-fable-5-1",
            ),
            help="Excellent at design and code. Uses your Anthropic API key (console.anthropic.com > API keys).",
            key_url="https://console.anthropic.com/settings/keys",
        ),
        Provider(
            "gemini",
            "Gemini (Google)",
            "gemini",
            base_url="https://generativelanguage.googleapis.com/v1beta",
            default_model="gemini-2.5-flash",
            needs_key=True,
            models=("gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite"),
            help="Fast and capable, with a free tier. Create a key at aistudio.google.com > Get API key.",
            key_url="https://aistudio.google.com/app/apikey",
        ),
        Provider(
            "openai",
            "OpenAI",
            "openai",
            base_url="https://api.openai.com/v1",
            default_model="gpt-5",
            needs_key=True,
            models=("gpt-5", "gpt-5-mini", "gpt-4.1", "gpt-4o"),
            help="Uses your OpenAI API key (platform.openai.com > API keys).",
            key_url="https://platform.openai.com/api-keys",
        ),
        Provider(
            "openrouter",
            "OpenRouter (hundreds of models)",
            "openai",
            base_url="https://openrouter.ai/api/v1",
            default_model="openrouter/auto",
            needs_key=True,
            models=(
                "openrouter/auto",
                "anthropic/claude-sonnet-4.5",
                "google/gemini-2.5-pro",
                "deepseek/deepseek-chat",
            ),
            help="One key for models from many companies, including free ones (openrouter.ai > Keys).",
            key_url="https://openrouter.ai/keys",
            extra_headers=(("X-Title", "Monosage Sites"),),
        ),
        Provider(
            "custom",
            "Any OpenAI-compatible server",
            "openai",
            key_optional=True,
            help="vLLM, Groq, Together, DeepSeek, Mistral, Azure OpenAI or your own gateway: enter its address "
            "(ending in /v1), a model name and a key if it needs one.",
        ),
        Provider(
            "platform",
            "Your organization's model",
            "platform",
            default_model="platform",
            hosted_only=True,
            help="The model your organization's plan includes. Nothing to set up.",
        ),
    )
}

TEMPLATE_ONLY = "builtin"


def hosted() -> bool:
    from aira.hosting import HOOKS

    return HOOKS.model_json is not None


def offered() -> list[Provider]:
    """The providers this build offers, in the order the page lists them."""
    is_hosted = hosted()
    return [
        p
        for p in PROVIDERS.values()
        if (not p.hosted_only or is_hosted) and not (p.local and is_hosted)
    ]


def get(provider_id: str) -> Provider:
    provider = PROVIDERS.get(str(provider_id or "").strip().lower())
    if provider is None or provider not in offered():
        raise ValueError(
            "Pick a model provider: " + ", ".join(p.label for p in offered()) + "."
        )
    return provider


@dataclass(frozen=True)
class Config:
    provider: Provider
    base_url: str
    model: str
    key: str | None = field(default=None, repr=False)

    @property
    def ready(self) -> bool:
        return not self.provider.needs_key or bool(self.key)


def check_base_url(provider: Provider, value: Any) -> str:
    """An address the provider may be called at: http(s) here; hosted, a public https address only."""
    text = str(value or "").strip().rstrip("/")
    if not text:
        return ""
    parts = urlsplit(text)
    if (
        parts.scheme not in ("http", "https")
        or not parts.hostname
        or parts.username
        or parts.password
    ):
        raise ValueError(
            "The address starts with http:// or https://, like http://127.0.0.1:11434."
        )
    if len(text) > 300:
        raise ValueError("That address is too long.")
    if hosted() and text != provider.base_url.rstrip("/"):
        if parts.scheme != "https" and os.getenv(ALLOW_LOCAL_ENV) != "1":
            raise ValueError(
                "Online, a model server's address must start with https://."
            )
    return text


def _check_public_host(url: str) -> None:
    """Hosted: refuse an address that resolves to a private, loopback or link-local machine (no SSRF)."""
    if not hosted() or os.getenv(ALLOW_LOCAL_ENV) == "1":
        return
    host = urlsplit(url).hostname or ""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ModelError(f"Could not find {host}. Check the address.") from None
    for info in infos:
        if not ipaddress.ip_address(info[4][0]).is_global:
            raise ModelError(
                f"{host} is not a public address, so it cannot be used online."
            )


def load_config(
    connection: Any, secret_store: Any, provider_id: str, *, model: str = ""
) -> Config:
    """The provider with its saved address, model and key for this account."""
    from aira.airaapi import store as api_store

    provider = get(provider_id)
    row = (
        kit.fetch_one(
            connection, "SELECT * FROM sb_providers WHERE id = ?", (provider.id,)
        )
        or {}
    )
    key = None
    if provider.needs_key or provider.key_optional:
        api_store.ensure_schema(connection)
        try:
            key = secret_store.get(connection, KEY_PREFIX + provider.id)
            if not key and provider.id == "claude":
                from aira import model_provider

                key = secret_store.get(
                    connection, model_provider.KEY_SECRET
                )  # the key saved in Settings
        except Exception:  # noqa: BLE001 - an unreadable key reads as no key; the page asks for one again
            key = None
    base = str(row.get("base_url") or "") or provider.base_url
    chosen = str(model or row.get("model") or "").strip()
    if not chosen and provider.kind == "ollama":
        chosen = installed_ollama_model(base.rstrip("/"))
    chosen = chosen or provider.default_model
    return Config(provider, base.rstrip("/"), chosen[:200], key or None)


def installed_ollama_model(base_url: str) -> str:
    """The best model this computer's Ollama has (a coding model first), or "" when Ollama is not running."""
    try:
        with urllib.request.urlopen(f"{base_url}/api/tags", timeout=2) as response:  # noqa: S310 - local address
            names = [
                str(m.get("name"))
                for m in json.loads(response.read() or b"{}").get("models") or []
                if m.get("name")
            ]
    except (OSError, ValueError):
        return ""
    names.sort(
        key=lambda name: (
            not any(word in name for word in ("coder", "code", "qwen")),
            name,
        )
    )
    return names[0] if names else ""


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def _friendly(status: int, body: bytes, config: Config) -> ModelError:
    label = config.provider.label
    detail = ""
    try:
        data = json.loads(body.decode("utf-8", "replace") or "{}")
        error = data.get("error") if isinstance(data, dict) else None
        if isinstance(error, dict):
            detail = str(error.get("message") or "")
        elif isinstance(error, str):
            detail = error
        elif isinstance(data, list) and data and isinstance(data[0], dict):
            detail = str((data[0].get("error") or {}).get("message") or "")
    except (ValueError, AttributeError):
        detail = body.decode("utf-8", "replace")[:200]
    if config.key:
        detail = detail.replace(config.key, "****")
    detail = detail.strip()[:300]
    if status in (401, 403):
        message = (
            f"{label} refused the key. Check it in Sites > Models and save it again."
        )
    elif status == 404:
        message = f"{label} has no model called {config.model or 'that'}. Pick another model in Sites > Models."
    elif status == 429:
        message = f"{label} is busy or the account is out of credit or quota. Wait a minute or check the account."
    elif status >= 500:
        message = (
            f"{label} had a problem on its side ({status}). Try again in a moment."
        )
    else:
        message = f"{label} could not take this request ({status})."
    return ModelError(
        f"{message} {detail}".strip()
        if detail and status not in (401, 403)
        else message
    )


def _open(
    url: str,
    config: Config,
    body: dict[str, Any] | None,
    headers: dict[str, str],
    *,
    timeout: float,
):
    _check_public_host(url)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(
        url, data=data, method="POST" if data is not None else "GET"
    )
    request.add_header("Content-Type", "application/json")
    request.add_header("User-Agent", "Monosage-Sites/1.0")
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310 - the address is checked above
    except urllib.error.HTTPError as error:
        raise _friendly(error.code, error.read() or b"", config) from None
    except (urllib.error.URLError, OSError) as error:
        reason = getattr(error, "reason", error)
        if config.provider.local:
            raise ModelError(
                f"Could not reach {config.provider.label} at {config.base_url}. Is it running? ({reason})"
            ) from None
        raise ModelError(
            f"Could not reach {config.provider.label}: {reason}."
        ) from None


def _lines(response: Any) -> Iterator[str]:
    with response:
        while True:
            try:
                raw = response.readline()
            except (TimeoutError, OSError) as error:
                raise ModelError(
                    f"The model stopped answering ({error}). Try again."
                ) from None
            if not raw:
                return
            yield raw.decode("utf-8", "replace").rstrip("\r\n")


def _sse(response: Any) -> Iterator[dict[str, Any]]:
    """Server-sent events: each ``data:`` payload as JSON (``[DONE]`` ends the stream)."""
    for line in _lines(response):
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if payload == "[DONE]":
            return
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if isinstance(event, dict):
            yield event


def _auth(config: Config) -> dict[str, str]:
    headers = dict(config.provider.extra_headers)
    kind = config.provider.kind
    if not config.key:
        return headers
    if kind == "anthropic":
        headers["x-api-key"] = config.key
    elif kind == "gemini":
        headers["x-goog-api-key"] = config.key
    else:
        headers["Authorization"] = f"Bearer {config.key}"
    return headers


# ---------------------------------------------------------------------------
# streaming one answer
# ---------------------------------------------------------------------------

TextSink = Callable[[str], None]
StopCheck = Callable[[], bool]


def stream(
    config: Config,
    system: str,
    prompt: str,
    on_text: TextSink,
    should_stop: StopCheck = lambda: False,
    *,
    max_tokens: int = MAX_TOKENS,
) -> str:
    """Ask the model; every piece of its answer goes to ``on_text``; returns the whole answer."""
    if config.provider.needs_key and not config.key:
        raise ModelError(
            f"Add your {config.provider.label} key in Sites > Models first."
        )
    if config.provider.kind not in ("builtin", "platform") and not config.base_url:
        raise ModelError(
            f"Enter the address of {config.provider.label} in Sites > Models."
        )
    if config.provider.kind not in ("builtin", "platform") and not config.model:
        raise ModelError(
            f"Choose a model for {config.provider.label} in Sites > Models."
        )
    parts: list[str] = []
    total = 0

    def emit(piece: str) -> None:
        nonlocal total
        if should_stop():
            raise Stopped
        if not piece:
            return
        parts.append(piece)
        total += len(piece)
        if total > MAX_OUTPUT_CHARS:
            raise ModelError(
                "The model wrote far more than a website needs. Try a shorter request."
            )
        on_text(piece)

    kind = config.provider.kind
    if kind == "ollama":
        _ollama(config, system, prompt, emit)
    elif kind == "openai":
        _openai(config, system, prompt, emit)
    elif kind == "anthropic":
        _anthropic(config, system, prompt, emit, max_tokens)
    elif kind == "gemini":
        _gemini(config, system, prompt, emit)
    elif kind == "platform":
        _platform(system, prompt, emit)
    else:
        raise ModelError(
            "The offline designer does not take free text; it builds from your words directly."
        )
    if should_stop():
        raise Stopped
    return "".join(parts)


def _ollama(config: Config, system: str, prompt: str, emit: TextSink) -> None:
    body: dict[str, Any] = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
        "think": False,
        "options": {"num_ctx": 16384, "temperature": 0.4},
    }
    try:
        response = _open(
            f"{config.base_url}/api/chat",
            config,
            body,
            _auth(config),
            timeout=READ_TIMEOUT,
        )
    except ModelError as error:
        if "think" not in str(error).lower():
            raise
        body.pop("think")  # an older Ollama, or a model without a thinking switch
        response = _open(
            f"{config.base_url}/api/chat",
            config,
            body,
            _auth(config),
            timeout=READ_TIMEOUT,
        )
    for line in _lines(response):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if event.get("error"):
            raise ModelError(f"Ollama: {event['error']}")
        emit(str((event.get("message") or {}).get("content") or ""))
        if event.get("done"):
            return


def _openai(config: Config, system: str, prompt: str, emit: TextSink) -> None:
    body = {
        "model": config.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": True,
    }
    response = _open(
        f"{config.base_url}/chat/completions",
        config,
        body,
        _auth(config),
        timeout=READ_TIMEOUT,
    )
    for event in _sse(response):
        if event.get("error"):
            error = event["error"]
            raise ModelError(
                f"{config.provider.label}: {error.get('message') if isinstance(error, dict) else error}"
            )
        for choice in event.get("choices") or []:
            delta = choice.get("delta") or {}
            emit(str(delta.get("content") or ""))


def _anthropic(
    config: Config, system: str, prompt: str, emit: TextSink, max_tokens: int
) -> None:
    body = {
        "model": config.model,
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": prompt}],
        "stream": True,
    }
    headers = {**_auth(config), "anthropic-version": "2023-06-01"}
    response = _open(
        f"{config.base_url}/v1/messages", config, body, headers, timeout=READ_TIMEOUT
    )
    for event in _sse(response):
        kind = event.get("type")
        if kind == "error":
            raise ModelError(
                f"Claude: {(event.get('error') or {}).get('message') or 'the request failed'}."
            )
        if kind == "content_block_delta":
            delta = event.get("delta") or {}
            if delta.get("type") == "text_delta":
                emit(str(delta.get("text") or ""))
        elif kind == "message_delta":
            if (event.get("delta") or {}).get("stop_reason") == "refusal":
                raise ModelError(
                    "Claude declined this request. Change the wording and try again."
                )


def _gemini(config: Config, system: str, prompt: str, emit: TextSink) -> None:
    body = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.4},
    }
    model = config.model.removeprefix("models/")
    url = f"{config.base_url}/models/{quote(model, safe='-._')}:streamGenerateContent?alt=sse"
    response = _open(url, config, body, _auth(config), timeout=READ_TIMEOUT)
    for event in _sse(response):
        if event.get("error"):
            raise ModelError(
                f"Gemini: {(event.get('error') or {}).get('message') or 'the request failed'}."
            )
        for candidate in event.get("candidates") or []:
            for part in (candidate.get("content") or {}).get("parts") or []:
                if not part.get("thought"):
                    emit(str(part.get("text") or ""))
            if candidate.get("finishReason") in (
                "SAFETY",
                "PROHIBITED_CONTENT",
                "BLOCKLIST",
            ):
                raise ModelError(
                    "Gemini stopped for safety reasons. Change the wording and try again."
                )


PLATFORM_FORMAT = (
    "\n\nReply with ONE JSON object and nothing else: "
    '{"plan": "...", "files": [{"path": "index.html", "content": "..."}], "deletes": [], "summary": "..."}'
)


def _platform(system: str, prompt: str, emit: TextSink) -> None:
    """The hosted organization's model answers JSON in one piece; it is replayed as the tagged format."""
    from aira import suite

    reply = suite.gateway().model(system + "\n\n" + prompt + PLATFORM_FORMAT)
    data = kit.loads(reply, None) if reply else None
    if not isinstance(data, dict) or not isinstance(data.get("files"), list):
        raise ModelError(
            "Your organization's model did not return a website. Try again or pick another model."
        )
    pieces = [f"<plan>{data.get('plan') or ''}</plan>\n"]
    for item in data["files"]:
        if isinstance(item, dict) and item.get("path"):
            pieces.append(
                f'<file path="{item["path"]}">\n{item.get("content") or ""}\n</file>\n'
            )
    for path in data.get("deletes") or []:
        pieces.append(f'<delete path="{path}"/>\n')
    pieces.append(f"<summary>{data.get('summary') or ''}</summary>")
    text = "".join(pieces)
    for start in range(0, len(text), 400):
        emit(text[start : start + 400])


# ---------------------------------------------------------------------------
# model lists and a connection check
# ---------------------------------------------------------------------------


def list_models(config: Config) -> list[str]:
    """The models the provider says this key can use (falls back to the suggestions)."""
    kind = config.provider.kind
    if kind in ("builtin", "platform"):
        return list(config.provider.models)
    if config.provider.needs_key and not config.key:
        raise ModelError(f"Add your {config.provider.label} key first.")
    headers = _auth(config)
    if kind == "ollama":
        response = _open(
            f"{config.base_url}/api/tags",
            config,
            None,
            headers,
            timeout=CONNECT_TIMEOUT,
        )
        with response:
            data = json.loads(response.read() or b"{}")
        return sorted(
            str(m.get("name")) for m in data.get("models") or [] if m.get("name")
        )
    if kind == "anthropic":
        headers["anthropic-version"] = "2023-06-01"
        url = f"{config.base_url}/v1/models?limit=100"
    elif kind == "gemini":
        url = f"{config.base_url}/models?pageSize=200"
    else:
        url = f"{config.base_url}/models"
    response = _open(url, config, None, headers, timeout=CONNECT_TIMEOUT)
    with response:
        data = json.loads(response.read() or b"{}")
    if kind == "gemini":
        names = [
            str(m.get("name", "")).removeprefix("models/")
            for m in data.get("models") or []
            if "generateContent" in (m.get("supportedGenerationMethods") or [])
        ]
    else:
        names = [str(m.get("id")) for m in data.get("data") or [] if m.get("id")]
    return sorted(set(names))


def check(config: Config) -> dict[str, Any]:
    """One tiny request, so a wrong key or address shows before a build needs it."""
    if config.provider.kind == "builtin":
        return {"ok": True, "reply": "ready", "model": config.model}
    pieces: list[str] = []
    stream(
        config,
        "You are a connection check.",
        "Reply with the single word OK.",
        pieces.append,
        max_tokens=16,
    )
    reply = "".join(pieces).strip()
    if not reply:
        raise ModelError(
            f"{config.provider.label} answered with nothing. Pick another model."
        )
    return {"ok": True, "reply": reply[:60], "model": config.model}
