"""Live updates for a member's open pages in the hosted build (Server-Sent Events).

The local server streams ``GET /api/notify/stream`` itself (``auto_apply_assistant.py``). Through the gateway the
same route answers here: the gateway sends the headers, then the stream writes hints as they happen
(``HttpResponse.stream``), until it times out, the page goes away or the server drains. A hint names a topic and
ids, never content: the page fetches the data through the usual routes, so every permission check still applies.

The channel is the member's organization, the same ``home`` Social, Dating and Play publish to. Streams are capped
per channel and in total (:mod:`aira.notify.realtime`); past the cap the page gets 429 and keeps polling.
"""

from __future__ import annotations

from typing import Any

from aira import notify as aira_notify
from aira.notify import realtime
from monosage_cloud.webio import HttpResponse, Req, cloud_route


@cloud_route("GET", r"/api/notify/stream")
def notify_stream(req: Req) -> Any:
    if not aira_notify.live():
        return 404, {"error": "Live updates are off here.", "code": "live_off"}
    channel = str(getattr(req.principal, "org_id", "") or "")
    if not channel:
        return 403, {"error": "Live updates need a member of an organization.", "code": "forbidden"}
    last = str(req.request.headers.get("last-event-id") or req.q("last") or "")[:64]
    try:
        after, ticket = realtime.HUB.open_stream(channel)
    except realtime.Busy as error:
        return 429, {"error": str(error), "code": "live_busy"}

    def stream(write: Any, stop: Any) -> None:
        try:
            realtime.serve(channel, last, after, write, stop=stop, ticket=ticket)
        finally:
            realtime.HUB.close(channel, ticket)

    return HttpResponse(
        200,
        [
            ("Content-Type", "text/event-stream; charset=utf-8"),
            ("Cache-Control", "no-store"),
            ("X-Accel-Buffering", "no"),
        ],
        b"",
        stream=stream,
    )
