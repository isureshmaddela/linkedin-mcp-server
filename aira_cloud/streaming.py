"""The sign-in broker: streams a candidate's private browser to them so they can sign in to LinkedIn themselves.

A candidate's LinkedIn browser profile lives on one worker host. When they connect LinkedIn from a hosted
organization, Aira issues a one-time link (``AIRA_CLOUD_REMOTE_LOGIN_URL``, ``https://broker.example/s/{token}``).
This service answers that link:

* ``GET /s/<token>`` serves a viewer page (a canvas, a hidden text input for the on-screen keyboard). It does
  not use up the token; a link preview or a prefetch does no harm.
* ``/ws/<token>`` is a WebSocket. The viewer says how big its screen is, the broker validates the token with the
  web tier (signed with the broker secret, once), starts Chromium on the candidate's profile directory on
  this host, and streams the page as JPEG frames from a CDP screencast. Mouse, wheel, key and text events come
  back and are replayed with ``Input.dispatch*``.

The viewer only ever receives pixels and short status messages: the broker never sends it a cookie, a storage
entry or a page's text. It cannot be pointed anywhere: navigations are limited to LinkedIn's sign-in hosts plus a
configured allowlist, other windows and downloads are refused. The session ends when the session cookie appears
(the profile is then exported exactly as the local ``--login`` flow does), when the viewer leaves or cancels, at
the time limit, when the link is revoked (a newer one was issued, the candidate left, the organization was
suspended), or when the broker shuts down. Starts and ends are written to the organization's audit log.

What the broker can see: keystrokes pass through this process on their way to the browser. It does not log or
keep them, but it is part of the trust boundary the operator runs; say so to customers.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from aira_cloud.config import CloudConfig

logger = logging.getLogger("aira_cloud.broker")

TOKEN = re.compile(r"[A-Za-z0-9_-]{16,100}")
MIN_WIDTH, MAX_WIDTH = 320, 1600
MIN_HEIGHT, MAX_HEIGHT = 360, 1400
MAX_TEXT = 2000
MAX_EVENTS_PER_SECOND = 400

# Windows virtual-key codes for the keys the viewer forwards as key events (everything else arrives as text).
VIRTUAL_KEYS = {
    "Backspace": 8,
    "Tab": 9,
    "Enter": 13,
    "Escape": 27,
    "PageUp": 33,
    "PageDown": 34,
    "End": 35,
    "Home": 36,
    "ArrowLeft": 37,
    "ArrowUp": 38,
    "ArrowRight": 39,
    "ArrowDown": 40,
    "Delete": 46,
}
MOUSE_BUTTONS = {"left": "left", "middle": "middle", "right": "right"}


class BrokerError(Exception):
    """A refusal the viewer is told about (code and message are safe to show)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


# ---------------------------------------------------------------------------- the web tier's side


class AppClient:
    """Signed calls to the web tier (the worker agents' scheme, with its own secret and purpose label)."""

    def __init__(
        self, base_url: str, secret: str, clock: Callable[[], float] = time.time
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.secret = secret
        self.clock = clock

    def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        from aira_cloud.agent import sign

        body = json.dumps(payload).encode()
        stamp = int(self.clock())
        nonce = secrets.token_hex(12)
        request = Request(
            self.base_url + path,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "X-Aira-Agent-Timestamp": str(stamp),
                "X-Aira-Agent-Nonce": nonce,
                "X-Aira-Agent-Signature": sign(
                    self.secret, stamp, nonce, body, purpose="broker"
                ),
            },
        )
        try:
            with urlopen(request, timeout=15) as response:  # noqa: S310 - the configured web tier
                return json.loads(response.read() or b"{}")
        except HTTPError as error:
            try:
                detail = json.loads(error.read() or b"{}")
            except ValueError:
                detail = {}
            raise BrokerError(
                {404: "invalid_link", 410: "link_used", 401: "misconfigured"}.get(
                    error.code, "unavailable"
                ),
                {
                    404: "This sign-in link is not valid.",
                    410: str(
                        detail.get("error")
                        or "This sign-in link was already used or has expired."
                    ),
                    401: "The sign-in service is not set up correctly. Tell your administrator.",
                }.get(error.code, "The sign-in service is unavailable. Try again."),
            ) from None
        except (URLError, OSError, ValueError):
            raise BrokerError(
                "unavailable", "The sign-in service is unavailable. Try again."
            ) from None

    def validate(self, token: str) -> dict[str, Any]:
        return self._post("/api/internal/remote-login/validate", {"token": token})

    def event(self, token: str, event: str, outcome: str = "") -> dict[str, Any]:
        return self._post(
            "/api/internal/remote-login/event",
            {"token": token, "event": event, "outcome": outcome},
        )


# ---------------------------------------------------------------------------- navigation policy


def host_allowed(url: str, allowed: tuple[str, ...]) -> bool:
    """Only http(s) to an allowed host (or a subdomain of one); ``about:blank`` is the empty start page."""
    if url == "about:blank":
        return True
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        return False
    host = (parts.hostname or "").lower().rstrip(".")
    return bool(host) and any(
        host == entry or host.endswith("." + entry) for entry in allowed
    )


def navigation_hosts(config: CloudConfig) -> tuple[str, ...]:
    """LinkedIn's own sign-in hosts, the start URL's host, and whatever the operator allowed."""
    start = (urlsplit(config.broker_start_url).hostname or "").lower()
    base = {"linkedin.com", start, *config.broker_allowed_hosts}
    return tuple(sorted(h for h in base if h))


# ---------------------------------------------------------------------------- the viewer page

VIEWER_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="referrer" content="no-referrer">
<meta name="robots" content="noindex">
<title>Sign in to LinkedIn</title>
<style nonce="__NONCE__">
:root { color-scheme: light dark; --bg:#f6f7f9; --fg:#14181f; --muted:#5b6472; --bar:#ffffff; --line:#d9dde3; --accent:#0a66c2; --danger:#b3261e; }
@media (prefers-color-scheme: dark) { :root { --bg:#12151a; --fg:#eef1f5; --muted:#a3acb9; --bar:#1b2027; --line:#2c333d; --accent:#6ab0ff; --danger:#ff8a80; } }
* { box-sizing: border-box; }
html, body { height: 100%; margin: 0; }
body { background: var(--bg); color: var(--fg); font: 15px/1.4 system-ui, -apple-system, "Segoe UI", sans-serif; display: flex; flex-direction: column; }
header { background: var(--bar); border-bottom: 1px solid var(--line); padding: 10px max(16px, env(safe-area-inset-left)); display: flex; gap: 12px; align-items: center; flex-wrap: wrap; }
header h1 { font-size: 16px; margin: 0; flex: 1 1 auto; }
#state { color: var(--muted); font-size: 14px; flex: 1 1 100%; order: 3; min-height: 1.4em; }
#state.error { color: var(--danger); }
button { font: inherit; padding: 8px 14px; border-radius: 8px; border: 1px solid var(--line); background: transparent; color: var(--fg); cursor: pointer; min-height: 40px; }
button:focus-visible, canvas:focus-visible { outline: 2px solid var(--accent); outline-offset: 2px; }
main { flex: 1 1 auto; min-height: 0; display: flex; justify-content: center; align-items: flex-start; padding: 0; }
#stage { position: relative; width: 100%; height: 100%; display: flex; justify-content: center; align-items: flex-start; }
canvas { display: block; max-width: 100%; max-height: 100%; background: #fff; touch-action: none; cursor: default; }
#kb { position: fixed; left: -1000px; top: 0; width: 1px; height: 1px; opacity: 0; }
#done { display: none; margin: 40px 16px; max-width: 520px; text-align: center; }
#done.show { display: block; }
.note { color: var(--muted); font-size: 13px; padding: 6px 16px; }
</style>
</head>
<body>
<header>
  <h1>Sign in to LinkedIn</h1>
  <button id="cancel" type="button">Cancel</button>
  <div id="state" role="status" aria-live="polite">Connecting&hellip;</div>
</header>
<main id="main">
  <div id="stage"><canvas id="screen" tabindex="0" aria-label="Private browser window" data-frames="0"></canvas></div>
  <div id="done" role="status"></div>
</main>
<p class="note" id="note">This is a private browser that Monosage runs for you. Type your LinkedIn details here yourself; Monosage does not store or log what you type, and keeps only the signed-in session.</p>
<input id="kb" type="text" autocomplete="off" autocapitalize="off" autocorrect="off" spellcheck="false" inputmode="text" aria-hidden="true" tabindex="-1">
<script nonce="__NONCE__">
(() => {
  "use strict";
  const token = location.pathname.split("/").pop();
  const canvas = document.getElementById("screen");
  const ctx = canvas.getContext("2d");
  const stateEl = document.getElementById("state");
  const kb = document.getElementById("kb");
  const doneEl = document.getElementById("done");
  let ws = null, frames = 0, ready = false, ended = false, touchScroll = null;

  const setState = (text, error) => { stateEl.textContent = text; stateEl.className = error ? "error" : ""; };
  const send = (obj) => { if (ws && ws.readyState === 1) ws.send(JSON.stringify(obj)); };
  const size = () => {
    const bar = document.querySelector("header").offsetHeight + document.getElementById("note").offsetHeight;
    return { w: Math.max(320, Math.floor(window.innerWidth)), h: Math.max(360, Math.floor(window.innerHeight - bar - 4)) };
  };
  const finish = (message, error) => {
    ended = true; ready = false;
    document.getElementById("stage").style.display = "none";
    doneEl.textContent = message; doneEl.className = "show";
    setState(error ? "Not connected" : "Done", error);
    document.getElementById("cancel").textContent = "Close";
  };

  const connect = () => {
    const scheme = location.protocol === "https:" ? "wss" : "ws";
    ws = new WebSocket(scheme + "://" + location.host + "/ws/" + encodeURIComponent(token));
    ws.binaryType = "arraybuffer";
    ws.onopen = () => { const s = size(); send({ t: "hello", w: s.w, h: s.h }); setState("Starting a private browser\\u2026"); };
    ws.onmessage = async (event) => {
      if (typeof event.data === "string") return status(JSON.parse(event.data));
      try {
        const bitmap = await createImageBitmap(new Blob([event.data], { type: "image/jpeg" }));
        if (canvas.width !== bitmap.width || canvas.height !== bitmap.height) { canvas.width = bitmap.width; canvas.height = bitmap.height; }
        ctx.drawImage(bitmap, 0, 0); bitmap.close();
        frames += 1; canvas.dataset.frames = String(frames);
        if (!ready) { ready = true; setState("Sign in below. This window closes by itself when you are signed in."); }
      } catch (_) { /* a damaged frame is skipped */ }
    };
    ws.onclose = () => { if (!ended) finish("The connection closed before sign-in finished. Start again from Monosage.", true); };
  };

  const status = (m) => {
    if (m.type === "error") finish(m.message || "Sign-in could not start.", true);
    else if (m.type === "ended") finish(m.message || "Finished.", m.outcome !== "signed_in");
    else if (m.type === "blocked") setState("That address is not allowed in this window.", true);
    else if (m.type === "focus") { if (m.editable && touch) kb.focus({ preventScroll: true }); else if (!m.editable && touch) kb.blur(); }
    else if (m.type === "notice") setState(m.message);
  };

  const point = (e) => {
    const r = canvas.getBoundingClientRect();
    return { x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)), y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)) };
  };
  let touch = false;
  canvas.addEventListener("pointerdown", (e) => {
    touch = e.pointerType === "touch"; canvas.setPointerCapture(e.pointerId); const p = point(e);
    if (touch) { touchScroll = { x: e.clientX, y: e.clientY, sx: e.clientX, sy: e.clientY, moved: false, p }; return; }
    send({ t: "mouse", k: "down", x: p.x, y: p.y, b: e.button === 2 ? "right" : "left" }); if (!touch) kb.focus({ preventScroll: true });
  });
  canvas.addEventListener("pointermove", (e) => {
    if (touch) {
      if (!touchScroll) return;
      const dx = e.clientX - touchScroll.x, dy = e.clientY - touchScroll.y;
      if (!touchScroll.moved && Math.hypot(e.clientX - touchScroll.sx, e.clientY - touchScroll.sy) < 8) return;
      touchScroll.moved = true; touchScroll.x = e.clientX; touchScroll.y = e.clientY;
      const r = canvas.getBoundingClientRect(); const p = point(e);
      send({ t: "wheel", x: p.x, y: p.y, dx: -dx * (canvas.width / r.width), dy: -dy * (canvas.height / r.height) });
      return;
    }
    const p = point(e); send({ t: "mouse", k: "move", x: p.x, y: p.y, b: e.buttons ? "left" : "none" });
  });
  canvas.addEventListener("pointerup", (e) => {
    const p = point(e);
    if (touch) {
      const tap = touchScroll && !touchScroll.moved; const start = touchScroll && touchScroll.p; touchScroll = null;
      if (tap && start) { send({ t: "mouse", k: "click", x: start.x, y: start.y, b: "left" }); }
      return;
    }
    send({ t: "mouse", k: "up", x: p.x, y: p.y, b: e.button === 2 ? "right" : "left" });
  });
  canvas.addEventListener("contextmenu", (e) => e.preventDefault());
  // taps must not pull focus off the hidden input (that would hide a phone's keyboard between two fields)
  canvas.addEventListener("mousedown", (e) => e.preventDefault());
  canvas.addEventListener("wheel", (e) => {
    e.preventDefault(); const p = point(e); const r = canvas.getBoundingClientRect();
    send({ t: "wheel", x: p.x, y: p.y, dx: e.deltaX * (canvas.width / r.width), dy: e.deltaY * (canvas.height / r.height) });
  }, { passive: false });
  canvas.addEventListener("focus", () => { if (!touch) kb.focus({ preventScroll: true }); });

  const SPECIAL = new Set(["Backspace", "Tab", "Enter", "Escape", "PageUp", "PageDown", "End", "Home", "ArrowLeft", "ArrowUp", "ArrowRight", "ArrowDown", "Delete"]);
  const mods = (e) => (e.altKey ? 1 : 0) | (e.ctrlKey ? 2 : 0) | (e.metaKey ? 4 : 0) | (e.shiftKey ? 8 : 0);
  kb.addEventListener("keydown", (e) => {
    if (!SPECIAL.has(e.key)) return;
    e.preventDefault();
    send({ t: "key", key: e.key, mods: mods(e) });
  });
  kb.addEventListener("beforeinput", (e) => {
    if (e.inputType === "deleteContentBackward") { e.preventDefault(); send({ t: "key", key: "Backspace", mods: 0 }); }
    else if (e.inputType === "insertLineBreak" || e.inputType === "insertParagraph") { e.preventDefault(); send({ t: "key", key: "Enter", mods: 0 }); }
  });
  kb.addEventListener("input", (e) => {
    if (e.data && !e.isComposing) send({ t: "text", s: e.data });
    else if (e.inputType === "insertFromPaste" && kb.value) send({ t: "text", s: kb.value });
    if (!e.isComposing) kb.value = "";
  });
  kb.addEventListener("compositionend", (e) => { if (e.data) send({ t: "text", s: e.data }); kb.value = ""; });
  kb.addEventListener("paste", (e) => {
    const text = (e.clipboardData || window.clipboardData).getData("text"); e.preventDefault();
    if (text) send({ t: "text", s: text });
  });

  document.getElementById("cancel").addEventListener("click", () => {
    if (ended) { window.close(); return; }
    send({ t: "cancel" }); finish("Sign-in cancelled. You can start again from Monosage.", true);
  });
  let resizeTimer = 0;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(() => { const s = size(); send({ t: "resize", w: s.w, h: s.h }); }, 250); });
  window.addEventListener("pagehide", () => { if (ws && !ended) ws.close(); });
  connect();
})();
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------- one sign-in session


@dataclass(eq=False)
class Session:
    broker: Broker
    ws: Any
    token: str
    width: int
    height: int
    org_id: str = ""
    candidate_id: str = ""
    profile_dir: Path | None = None
    outcome: str = ""
    stop_reason: str = ""
    blocked: int = 0
    started: bool = False
    _events: int = 0
    _window: float = 0.0
    _manager: Any = None
    _cdp: Any = None
    _page: Any = None
    _stop: asyncio.Event = field(default_factory=asyncio.Event)

    # -- lifecycle ----------------------------------------------------------------------

    def request_stop(self, reason: str) -> None:
        if not self.stop_reason:
            self.stop_reason = reason
        self._stop.set()

    async def run(self) -> None:
        broker = self.broker
        try:
            info = await asyncio.to_thread(broker.app.validate, self.token)
            self.org_id, self.candidate_id = info["org_id"], info["candidate_id"]
            self.profile_dir = Path(info["profile_dir"])
            await broker.claim(self)
            await asyncio.to_thread(broker.app.event, self.token, "started")
            self.started = True
            await self._launch()
            await self._serve()
        except BrokerError as error:
            self.outcome = self.outcome or "refused"
            await self._tell(
                {"type": "error", "code": error.code, "message": error.message}
            )
        except Exception:  # noqa: BLE001 - the viewer gets a plain message; the log has the detail
            logger.exception("sign-in session failed")
            self.outcome = "error"
            await self._tell(
                {
                    "type": "error",
                    "code": "failed",
                    "message": "The private browser could not be started. Try again from Monosage.",
                }
            )
        finally:
            await self._close()

    async def _launch(self) -> None:
        from linkedin_mcp_server.core import BrowserManager

        assert self.profile_dir is not None
        options: dict[str, Any] = {"accept_downloads": False}
        # the same browser identity the local sign-in and the worker use (their environment variables)
        user_agent = os.environ.get("USER_AGENT") or None
        if os.environ.get("CHROME_PATH"):
            options["executable_path"] = os.environ["CHROME_PATH"]
        self._manager = BrowserManager(
            user_data_dir=self.profile_dir,
            headless=True,
            viewport={"width": self.width, "height": self.height},
            user_agent=user_agent,
            **options,
        )
        await self._manager.start()
        context = self._manager.context
        page = self._manager.page
        self._page = page
        context.on(
            "page", lambda popup: asyncio.ensure_future(self._popup(popup, page))
        )
        page.on("dialog", lambda dialog: asyncio.ensure_future(self._dismiss(dialog)))
        page.on(
            "framenavigated",
            lambda frame: asyncio.ensure_future(self._after_navigation(frame)),
        )
        self._cdp = await context.new_cdp_session(page)
        self._cdp.on("Fetch.requestPaused", self._on_request)
        self._cdp.on("Page.screencastFrame", self._on_frame)
        await self._cdp.send(
            "Fetch.enable",
            {"patterns": [{"resourceType": "Document", "requestStage": "Request"}]},
        )
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 70,
                "maxWidth": self.width,
                "maxHeight": self.height,
                "everyNthFrame": 1,
            },
        )
        await page.goto(self.broker.config.broker_start_url, wait_until="commit")

    async def _serve(self) -> None:
        reader = asyncio.ensure_future(self._read_loop())
        monitor = asyncio.ensure_future(self._monitor_loop())
        try:
            await asyncio.wait({reader, monitor}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for task in (reader, monitor):
                task.cancel()
            for task in (reader, monitor):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        self.outcome = self.outcome or self.stop_reason or "abandoned"

    async def _close(self) -> None:
        broker = self.broker
        if self._cdp is not None:
            with contextlib.suppress(Exception):
                await self._cdp.send("Page.stopScreencast")
        if self._manager is not None:
            with contextlib.suppress(Exception):
                await self._manager.close()
        outcome = self.outcome or self.stop_reason or "error"
        if self.started:
            with contextlib.suppress(BrokerError):
                await asyncio.to_thread(
                    broker.app.event, self.token, "finished", outcome
                )
        message = {
            "signed_in": "You are signed in to LinkedIn. You can close this window.",
            "cancelled": "Sign-in cancelled. You can start again from Monosage.",
            "timeout": "This sign-in window timed out. Start again from Monosage.",
            "revoked": "This sign-in link was replaced or is no longer valid. Start again from Monosage.",
            "replaced": "A newer sign-in window was opened for this account.",
            "shutdown": "The sign-in service is restarting. Start again from Monosage.",
        }.get(outcome)
        if message and self.outcome != "refused":
            await self._tell({"type": "ended", "outcome": outcome, "message": message})
        with contextlib.suppress(Exception):
            await self.ws.close()
        broker.release(self)

    # -- the viewer's events -------------------------------------------------------------

    async def _read_loop(self) -> None:
        try:
            async for message in self.ws:
                if isinstance(message, str):
                    await self._input(message)
        except Exception:  # noqa: BLE001 - a closed or broken socket is the viewer leaving
            pass
        self.request_stop("abandoned")

    def _allow_event(self) -> bool:
        now = time.monotonic()
        if now - self._window >= 1:
            self._window, self._events = now, 0
        self._events += 1
        return self._events <= MAX_EVENTS_PER_SECOND

    async def _input(self, raw: str) -> None:
        if not self._allow_event():
            return
        try:
            event = json.loads(raw)
        except ValueError:
            return
        if not isinstance(event, dict) or self._cdp is None:
            return
        kind = event.get("t")
        try:
            if kind == "mouse":
                await self._mouse(event)
            elif kind == "wheel":
                await self._wheel(event)
            elif kind == "key":
                await self._key(event)
            elif kind == "text":
                text = str(event.get("s", ""))[:MAX_TEXT]
                if text:
                    await self._cdp.send("Input.insertText", {"text": text})
            elif kind == "resize":
                await self._resize(event)
            elif kind == "cancel":
                self.request_stop("cancelled")
        except BrokerError:
            raise
        except Exception:  # noqa: BLE001 - one bad event must not end the session
            logger.debug("input event failed", exc_info=True)

    def _xy(self, event: dict[str, Any]) -> tuple[float, float]:
        def unit(value: Any) -> float:
            try:
                return min(1.0, max(0.0, float(value)))
            except (TypeError, ValueError):
                return 0.0

        return unit(event.get("x")) * self.width, unit(event.get("y")) * self.height

    async def _mouse(self, event: dict[str, Any]) -> None:
        assert self._cdp is not None
        x, y = self._xy(event)
        button = MOUSE_BUTTONS.get(str(event.get("b", "left")), "none")
        action = str(event.get("k", ""))
        send = self._cdp.send
        if action == "move":
            await send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mouseMoved",
                    "x": x,
                    "y": y,
                    "button": "none",
                    "buttons": 1 if button == "left" else 0,
                },
            )
        elif action in {"down", "up"}:
            await send(
                "Input.dispatchMouseEvent",
                {
                    "type": "mousePressed" if action == "down" else "mouseReleased",
                    "x": x,
                    "y": y,
                    "button": button if button != "none" else "left",
                    "buttons": 1 if action == "down" else 0,
                    "clickCount": 1,
                },
            )
            if action == "up":
                await self._settle()
                asyncio.ensure_future(self._report_focus())
        elif action == "click":
            await send(
                "Input.dispatchMouseEvent",
                {"type": "mouseMoved", "x": x, "y": y, "button": "none"},
            )
            for phase in ("mousePressed", "mouseReleased"):
                await send(
                    "Input.dispatchMouseEvent",
                    {
                        "type": phase,
                        "x": x,
                        "y": y,
                        "button": "left",
                        "buttons": 1 if phase == "mousePressed" else 0,
                        "clickCount": 1,
                    },
                )
            await self._settle()
            asyncio.ensure_future(self._report_focus())

    async def _wheel(self, event: dict[str, Any]) -> None:
        assert self._cdp is not None
        x, y = self._xy(event)

        def delta(value: Any) -> float:
            try:
                return max(-3000.0, min(3000.0, float(value)))
            except (TypeError, ValueError):
                return 0.0

        await self._cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseWheel",
                "x": x,
                "y": y,
                "deltaX": delta(event.get("dx")),
                "deltaY": delta(event.get("dy")),
            },
        )

    async def _key(self, event: dict[str, Any]) -> None:
        assert self._cdp is not None
        key = str(event.get("key", ""))
        code = VIRTUAL_KEYS.get(key)
        if code is None:
            return  # printable characters arrive as text
        try:
            modifiers = int(event.get("mods", 0)) & 15
        except (TypeError, ValueError):
            modifiers = 0
        base = {
            "key": key,
            "code": key,
            "windowsVirtualKeyCode": code,
            "nativeVirtualKeyCode": code,
            "modifiers": modifiers,
        }
        down = (
            {**base, "type": "keyDown", "text": "\r"}
            if key == "Enter"
            else {**base, "type": "rawKeyDown"}
        )
        await self._cdp.send("Input.dispatchKeyEvent", down)
        await self._cdp.send("Input.dispatchKeyEvent", {**base, "type": "keyUp"})

    async def _resize(self, event: dict[str, Any]) -> None:
        width, height = clamp_size(event.get("w"), event.get("h"))
        if (width, height) == (self.width, self.height) or self._page is None:
            return
        self.width, self.height = width, height
        await self._page.set_viewport_size({"width": width, "height": height})
        assert self._cdp is not None
        await self._cdp.send("Page.stopScreencast")
        await self._cdp.send(
            "Page.startScreencast",
            {
                "format": "jpeg",
                "quality": 70,
                "maxWidth": width,
                "maxHeight": height,
                "everyNthFrame": 1,
            },
        )

    async def _settle(self) -> None:
        """Wait until the page has handled the click (and moved focus) before the next typed character arrives."""
        with contextlib.suppress(Exception):
            await self._page.evaluate("0")

    async def _report_focus(self) -> None:
        """Tell the viewer whether a text field has focus, so a phone shows its keyboard only then."""
        await asyncio.sleep(0.12)
        try:
            editable = await self._page.evaluate(
                "(() => { const a = document.activeElement; if (!a) return false;"
                " return a.isContentEditable || (a.tagName === 'TEXTAREA') ||"
                " (a.tagName === 'INPUT' && !['button','submit','checkbox','radio','reset','image','file'].includes((a.type || 'text').toLowerCase())); })()"
            )
        except Exception:  # noqa: BLE001 - mid-navigation
            return
        await self._tell({"type": "focus", "editable": bool(editable)})

    # -- the browser's events ------------------------------------------------------------

    async def _on_frame(self, params: dict[str, Any]) -> None:
        try:
            await self.ws.send(base64.b64decode(params["data"]))
        except Exception:  # noqa: BLE001 - the viewer is gone; the read loop ends the session
            return
        with contextlib.suppress(Exception):
            assert self._cdp is not None
            await self._cdp.send(
                "Page.screencastFrameAck", {"sessionId": params["sessionId"]}
            )

    async def _on_request(self, params: dict[str, Any]) -> None:
        assert self._cdp is not None
        url = str(params.get("request", {}).get("url", ""))
        try:
            if host_allowed(url, self.broker.hosts):
                await self._cdp.send(
                    "Fetch.continueRequest", {"requestId": params["requestId"]}
                )
            else:
                self.blocked += 1
                logger.info("blocked navigation to %s", urlsplit(url).hostname or "?")
                # 204 No Content: the browser stays on the page it is showing (an error page would replace it)
                await self._cdp.send(
                    "Fetch.fulfillRequest",
                    {
                        "requestId": params["requestId"],
                        "responseCode": 204,
                        "responseHeaders": [],
                    },
                )
                await self._tell(
                    {"type": "blocked", "host": urlsplit(url).hostname or ""}
                )
        except Exception:  # noqa: BLE001 - the page closed under us
            logger.debug("request decision failed", exc_info=True)

    async def _after_navigation(self, frame: Any) -> None:
        """Belt and braces for what the network layer cannot see (other schemes, history navigations)."""
        try:
            if frame != self._page.main_frame:
                return
            url = frame.url
            if not host_allowed(url, self.broker.hosts):
                self.blocked += 1
                await self._tell(
                    {"type": "blocked", "host": urlsplit(url).hostname or ""}
                )
                await self._page.goto(
                    self.broker.config.broker_start_url, wait_until="commit"
                )
        except Exception:  # noqa: BLE001
            logger.debug("navigation guard failed", exc_info=True)

    async def _popup(self, popup: Any, opener: Any) -> None:
        """New windows are not shown: a link meant for one loads in the main window when it is allowed."""
        try:
            await popup.wait_for_load_state("commit", timeout=3000)
        except Exception:  # noqa: BLE001
            pass
        url = popup.url
        with contextlib.suppress(Exception):
            await popup.close()
        if url and host_allowed(url, self.broker.hosts) and url != "about:blank":
            with contextlib.suppress(Exception):
                await opener.goto(url, wait_until="commit")
        else:
            self.blocked += 1
            await self._tell({"type": "blocked", "host": urlsplit(url).hostname or ""})

    @staticmethod
    async def _dismiss(dialog: Any) -> None:
        with contextlib.suppress(Exception):
            await dialog.dismiss()

    # -- ending conditions ---------------------------------------------------------------

    async def _monitor_loop(self) -> None:
        broker = self.broker
        loop = asyncio.get_running_loop()
        deadline = loop.time() + broker.config.broker_session_seconds
        next_heartbeat = loop.time() + broker.heartbeat_interval
        while not self._stop.is_set():
            await asyncio.sleep(broker.poll_interval)
            if self._stop.is_set():
                return
            if loop.time() >= deadline:
                self.request_stop("timeout")
                return
            if await self._signed_in():
                await asyncio.sleep(
                    broker.finalize_delay
                )  # let the browser write the cookie to disk
                await self._export_profile()
                self.outcome = "signed_in"
                self.request_stop("signed_in")
                return
            if loop.time() >= next_heartbeat:
                next_heartbeat = loop.time() + broker.heartbeat_interval
                try:
                    answer = await asyncio.to_thread(
                        broker.app.event, self.token, "heartbeat"
                    )
                except BrokerError:
                    continue  # a web-tier hiccup does not end a sign-in
                if not answer.get("active"):
                    self.request_stop("revoked")
                    return

    async def _signed_in(self) -> bool:
        config = self.broker.config
        domain = config.broker_signed_in_domain.lower().lstrip(".")
        try:
            cookies = await self._manager.context.cookies()
        except Exception:  # noqa: BLE001
            return False
        for cookie in cookies:
            host = str(cookie.get("domain", "")).lower().lstrip(".")
            if (
                cookie.get("name") == config.broker_signed_in_cookie
                and cookie.get("value")
                and (host == domain or host.endswith("." + domain))
            ):
                return True
        return False

    async def _export_profile(self) -> None:
        """Leave the profile the way the local ``--login`` flow does, so the worker sees a ready session."""
        from linkedin_mcp_server.common_utils import secure_mkdir, secure_write_text
        from linkedin_mcp_server.session_state import (
            portable_cookie_path,
            write_source_state,
        )

        assert self.profile_dir is not None
        domain = self.broker.config.broker_signed_in_domain.lower().lstrip(".")
        cookies = [
            self._manager._normalize_cookie_domain(c)
            for c in await self._manager.context.cookies()
            if domain in str(c.get("domain", "")).lower()
        ]
        path = portable_cookie_path(self.profile_dir)
        secure_mkdir(path.parent)
        secure_write_text(path, json.dumps(cookies, indent=2), mode=0o600)
        write_source_state(self.profile_dir)

    async def _tell(self, message: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            await self.ws.send(json.dumps(message))


def clamp_size(width: Any, height: Any) -> tuple[int, int]:
    def number(value: Any, low: int, high: int, default: int) -> int:
        try:
            return max(low, min(high, int(float(value))))
        except (TypeError, ValueError):
            return default

    return number(width, MIN_WIDTH, MAX_WIDTH, 1000), number(
        height, MIN_HEIGHT, MAX_HEIGHT, 700
    )


# ---------------------------------------------------------------------------- the service


class Broker:
    """Accepts viewers, enforces the caps, and runs one :class:`Session` per sign-in."""

    def __init__(
        self,
        config: CloudConfig,
        *,
        secret: str | None = None,
        app_url: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.hosts = navigation_hosts(config)
        secret = secret if secret is not None else config.broker_secret
        if not secret:
            raise ValueError(
                "AIRA_CLOUD_BROKER_SECRET must be set: the broker signs its calls to the web tier with it."
            )
        self.app = AppClient(
            app_url or config.broker_app_url or config.base_url, secret, clock
        )
        self.poll_interval = 1.0
        self.heartbeat_interval = 5.0
        self.finalize_delay = 1.5
        self.sessions: set[Session] = set()
        self._by_candidate: dict[tuple[str, str], Session] = {}
        self._server: Any = None
        self._thread: threading.Thread | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self.port = 0

    # -- caps ------------------------------------------------------------------------------

    async def claim(self, session: Session) -> None:
        """One session per candidate: a newer sign-in replaces the older one."""
        key = (session.org_id, session.candidate_id)
        older = self._by_candidate.get(key)
        if older is not None and older is not session:
            older.request_stop("replaced")
            for _ in range(100):
                if self._by_candidate.get(key) is not older:
                    break
                await asyncio.sleep(0.1)
        self._by_candidate[key] = session

    def release(self, session: Session) -> None:
        self.sessions.discard(session)
        key = (session.org_id, session.candidate_id)
        if self._by_candidate.get(key) is session:
            del self._by_candidate[key]

    # -- HTTP and WebSocket -----------------------------------------------------------------

    def _process_request(self, connection: Any, request: Any) -> Any:
        from websockets.datastructures import Headers
        from websockets.http11 import Response

        def reply(
            status: HTTPStatus,
            body: bytes,
            content_type: str,
            extra: dict[str, str] | None = None,
        ) -> Response:
            headers = Headers(
                {
                    "Content-Type": content_type,
                    "Content-Length": str(len(body)),
                    "Cache-Control": "no-store",
                    "Referrer-Policy": "no-referrer",
                    "X-Content-Type-Options": "nosniff",
                    "X-Frame-Options": "DENY",
                    **(extra or {}),
                }
            )
            return Response(status.value, status.phrase, headers, body)

        path = urlsplit(request.path).path
        upgrade = request.headers.get("Upgrade", "").lower() == "websocket"
        if path.startswith("/ws/"):
            if not upgrade:
                return reply(
                    HTTPStatus.UPGRADE_REQUIRED,
                    b"WebSocket only",
                    "text/plain; charset=utf-8",
                )
            origin = request.headers.get("Origin")
            host = (request.headers.get("Host") or "").lower()
            if origin and urlsplit(origin).netloc.lower() != host:
                return reply(
                    HTTPStatus.FORBIDDEN, b"Forbidden", "text/plain; charset=utf-8"
                )
            if not TOKEN.fullmatch(path[4:]):
                return reply(
                    HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8"
                )
            return None
        if path == "/healthz":
            return reply(HTTPStatus.OK, b"ok", "text/plain; charset=utf-8")
        if path.startswith("/s/") and TOKEN.fullmatch(path[3:]):
            nonce = secrets.token_urlsafe(16)
            host = request.headers.get("Host", "")
            policy = (
                f"default-src 'none'; script-src 'nonce-{nonce}'; style-src 'nonce-{nonce}'; "
                f"connect-src ws://{host} wss://{host}; img-src data: blob:; frame-ancestors 'none'; "
                "base-uri 'none'; form-action 'none'"
            )
            return reply(
                HTTPStatus.OK,
                VIEWER_HTML.replace("__NONCE__", nonce).encode(),
                "text/html; charset=utf-8",
                {"Content-Security-Policy": policy},
            )
        return reply(HTTPStatus.NOT_FOUND, b"Not found", "text/plain; charset=utf-8")

    async def _handler(self, ws: Any) -> None:
        token = urlsplit(ws.request.path).path[4:]
        try:
            hello = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
        except Exception:  # noqa: BLE001
            return
        if not isinstance(hello, dict) or hello.get("t") != "hello":
            return
        if len(self.sessions) >= self.config.broker_max_sessions:
            # refused before the link is used, so the person can try again in a minute
            await ws.send(
                json.dumps(
                    {
                        "type": "error",
                        "code": "busy",
                        "message": "All private browsers are in use right now. Try again in a minute.",
                    }
                )
            )
            return
        width, height = clamp_size(hello.get("w"), hello.get("h"))
        session = Session(self, ws, token, width, height)
        self.sessions.add(session)
        await session.run()

    async def serve(self, host: str | None = None, port: int | None = None) -> None:
        from websockets.asyncio.server import serve

        self._server = await serve(
            self._handler,
            host or self.config.broker_host,
            self.config.broker_port if port is None else port,
            process_request=self._process_request,
            max_size=65536,
            compression=None,
            ping_interval=20,
            ping_timeout=20,
        )
        self.port = self._server.sockets[0].getsockname()[1]
        logger.info(
            "Sign-in broker listening on %s:%d",
            host or self.config.broker_host,
            self.port,
        )

    async def shutdown(self) -> None:
        for session in list(self.sessions):
            session.request_stop("shutdown")
        for _ in range(100):
            if not self.sessions:
                break
            await asyncio.sleep(0.1)
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()

    # -- running it from synchronous code (tests, embedding) ---------------------------------

    def start_in_thread(self, host: str = "127.0.0.1", port: int = 0) -> int:
        ready = threading.Event()
        failure: list[BaseException] = []

        def run() -> None:
            loop = asyncio.new_event_loop()
            self._loop = loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(self.serve(host, port))
            except BaseException as error:  # noqa: BLE001
                failure.append(error)
                ready.set()
                return
            ready.set()
            loop.run_forever()
            loop.close()

        self._thread = threading.Thread(target=run, name="broker", daemon=True)
        self._thread.start()
        ready.wait(30)
        if failure:
            raise failure[0]
        return self.port

    def stop_thread(self) -> None:
        loop = self._loop
        if loop is None:
            return
        asyncio.run_coroutine_threadsafe(self.shutdown(), loop).result(60)
        loop.call_soon_threadsafe(loop.stop)
        if self._thread:
            self._thread.join(10)
        self._loop = None


def run_forever(config: CloudConfig) -> None:
    """The ``python -m aira_cloud broker`` entry point."""
    broker = Broker(config)

    async def main() -> None:
        await broker.serve()
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        import signal

        for name in ("SIGINT", "SIGTERM"):
            with contextlib.suppress(NotImplementedError, ValueError, AttributeError):
                loop.add_signal_handler(getattr(signal, name), stop.set)
        try:
            await stop.wait()
        except asyncio.CancelledError:
            pass
        await broker.shutdown()

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass


__all__ = [
    "AppClient",
    "Broker",
    "BrokerError",
    "Session",
    "VIEWER_HTML",
    "clamp_size",
    "host_allowed",
    "navigation_hosts",
    "run_forever",
]
