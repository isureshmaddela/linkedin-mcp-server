"""Billing: pricing, quotes, checkout, plan changes, the provider webhook and the stand-in hosted checkout."""

from __future__ import annotations

import html
from typing import Any
from urllib.parse import quote

from aira_cloud.db import one
from aira_cloud.errors import Forbidden, NotFound, ValidationFailed
from aira_cloud.webio import Reply, Req, cloud_route


@cloud_route("GET", r"/api/billing/plans", auth="public")
def public_plans(req: Req):  # noqa: ANN201
    return 200, {
        "plans": req.app.billing.public_plans(),
        "trial_days": req.app.config.trial_days,
    }


@cloud_route("GET", r"/api/org/billing")
def billing_get(req: Req):  # noqa: ANN201
    app, principal = req.app, req.principal
    summary = app.billing.summary(principal.org_id)
    payload: dict[str, Any] = {"billing": summary, "plans": app.billing.public_plans()}
    if app.authz.can(principal, "billing.manage"):
        payload["invoices"] = app.billing.invoices(principal)
        payload["can_manage"] = True
    else:
        app.authz.require(principal, "usage.read")
        payload["can_manage"] = False
    return 200, payload


def _plan_and_seats(req: Req) -> tuple[str, int]:
    body = req.body()
    try:
        seats = int(body.get("seats", 0))
    except (TypeError, ValueError):
        raise ValidationFailed("Enter the number of candidate seats.") from None
    return str(body.get("plan", "")), seats


@cloud_route("POST", r"/api/org/billing/quote")
def billing_quote(req: Req):  # noqa: ANN201
    req.app.authz.require(req.principal, "billing.manage")
    plan, seats = _plan_and_seats(req)
    return 200, req.app.billing.quote(
        req.principal.org_id, plan, seats, enforce_usage=False
    )


@cloud_route("POST", r"/api/org/billing/checkout")
def billing_checkout(req: Req):  # noqa: ANN201
    plan, seats = _plan_and_seats(req)
    return 200, req.app.billing.start_checkout(req.principal, plan, seats)


@cloud_route("POST", r"/api/org/billing/change-plan")
def billing_change(req: Req):  # noqa: ANN201
    plan, seats = _plan_and_seats(req)
    return 200, req.app.billing.change_plan(req.principal, plan, seats or None)


@cloud_route("POST", r"/api/org/billing/cancel-change")
def billing_cancel_change(req: Req):  # noqa: ANN201
    req.app.billing.cancel_pending_change(req.principal)
    return 200, {"ok": True}


@cloud_route("POST", r"/api/org/billing/overage")
def billing_overage(req: Req):  # noqa: ANN201
    """Turn metered overage on or off (Team and Enterprise)."""
    return 200, req.app.billing.set_overage(
        req.principal, bool(req.body().get("enabled"))
    )


@cloud_route("POST", r"/api/org/billing/cancel")
def billing_cancel(req: Req):  # noqa: ANN201
    return 200, req.app.billing.cancel(req.principal)


@cloud_route("POST", r"/api/org/billing/reactivate")
def billing_reactivate(req: Req):  # noqa: ANN201
    return 200, req.app.billing.reactivate(req.principal)


@cloud_route("POST", r"/api/org/billing/portal")
def billing_portal(req: Req):  # noqa: ANN201
    return 200, req.app.billing.portal(req.principal)


@cloud_route("POST", r"/api/billing/webhook/([a-z]{3,20})", auth="public", csrf=False)
def billing_webhook(req: Req):  # noqa: ANN201
    provider = req.group()
    headers = req.request.headers
    signature = headers.get("stripe-signature") or headers.get("x-aira-signature") or ""
    return 200, req.app.billing.handle_webhook(provider, req.request.body, signature)


# ----------------------------------------------------------------------------- stand-in hosted checkout

PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Hosted checkout (stand-in)</title><link rel="stylesheet" href="/static/app.css"></head><body class="auth-screen"><main class="auth-panel">
<div class="brand"><span class="brand-mark">A</span><span>AIRA<small>Checkout</small></span></div>{body}</main></body></html>"""


def _page(body: str, status: int = 200) -> Reply:
    return Reply(
        status,
        PAGE.format(body=body).encode("utf-8"),
        content_type="text/html; charset=utf-8",
    )


def _standin(req: Req) -> Any:
    if req.app.config.billing_provider != "standin":
        raise NotFound("Not found.")
    return req.app.billing.standin


@cloud_route(
    "GET", r"/billing/standin/checkout/(cs_standin_[a-z0-9]{20})", auth="public"
)
def standin_checkout_page(req: Req):  # noqa: ANN201
    provider = _standin(req)
    session_id = req.group()
    session = provider.checkout_session(session_id)
    if not session:
        raise NotFound("This checkout does not exist.")
    notice = ""
    if session["status"] == "paid":
        notice = "<p class='muted'>This checkout was already paid.</p>"
    elif req.q("declined"):
        notice = "<p role='alert' style='color:var(--danger,#b3261e)'>The card was declined. Try again.</p>"
    amount = f"{session['amount_cents'] / 100:,.2f} {html.escape(session['currency']).upper()}"
    body = (
        f"<h1 style='margin-top:18px'>Pay for {html.escape(session['plan_name'])}</h1>"
        f"<p class='muted'>{int(session['seats'])} candidate seats &middot; {amount} per month</p>"
        "<p class='muted'>This is the stand-in payment page used when no payment provider is configured. No money moves.</p>"
        f"{notice}"
        f"<form method='post' action='/billing/standin/checkout/{session_id}/pay'><button class='btn' type='submit' style='width:100%;margin-top:8px'>Pay {amount}</button></form>"
        f"<form method='post' action='/billing/standin/checkout/{session_id}/decline'><button class='btn ghost' type='submit' style='width:100%;margin-top:8px'>Simulate a declined card</button></form>"
        f"<form method='post' action='/billing/standin/checkout/{session_id}/cancel'><button class='btn ghost' type='submit' style='width:100%;margin-top:8px'>Cancel</button></form>"
    )
    return _page(body)


@cloud_route(
    "POST", r"/billing/standin/checkout/(cs_standin_[a-z0-9]{20})/pay", auth="public"
)
def standin_checkout_pay(req: Req):  # noqa: ANN201
    provider = _standin(req)
    session_id = req.group()
    session = provider.checkout_session(session_id)
    if not session:
        raise NotFound("This checkout does not exist.")
    if session["status"] != "paid":
        subscription_id = f"sub_standin_{session['org_id'][:12]}"
        for event_type, data in (
            (
                "checkout.completed",
                {
                    "org_id": session["org_id"],
                    "plan_id": session["plan_id"],
                    "seats": session["seats"],
                    "customer_id": session["customer_id"],
                    "subscription_id": subscription_id,
                },
            ),
            (
                "invoice.paid",
                {
                    "org_id": session["org_id"],
                    "customer_id": session["customer_id"],
                    "subscription_id": subscription_id,
                    "invoice_id": f"in_{session_id[-12:]}",
                    "amount_cents": session["amount_cents"],
                    "currency": session["currency"],
                    "period_start": req.app.clock.now(),
                    "period_end": req.app.clock.now() + 30 * 86400,
                },
            ),
        ):
            payload, signature = provider.make_event(event_type, data)
            req.app.billing.handle_webhook("standin", payload, signature)
        provider.mark_checkout(session_id, "paid")
    return Reply(
        303,
        b"",
        headers=[("Location", session["success_url"])],
        content_type="text/plain",
    )


@cloud_route(
    "POST",
    r"/billing/standin/checkout/(cs_standin_[a-z0-9]{20})/decline",
    auth="public",
)
def standin_checkout_decline(req: Req):  # noqa: ANN201
    if not _standin(req).checkout_session(req.group()):
        raise NotFound("This checkout does not exist.")
    return Reply(
        303,
        b"",
        headers=[("Location", f"/billing/standin/checkout/{req.group()}?declined=1")],
        content_type="text/plain",
    )


@cloud_route(
    "POST", r"/billing/standin/checkout/(cs_standin_[a-z0-9]{20})/cancel", auth="public"
)
def standin_checkout_cancel(req: Req):  # noqa: ANN201
    provider = _standin(req)
    session = provider.checkout_session(req.group())
    if not session:
        raise NotFound("This checkout does not exist.")
    return Reply(
        303,
        b"",
        headers=[("Location", session["cancel_url"])],
        content_type="text/plain",
    )


@cloud_route(
    "GET", r"/billing/standin/portal/(cus_standin_[a-z0-9]{12})", auth="public"
)
def standin_portal_page(req: Req):  # noqa: ANN201
    _standin(req)
    customer = req.group()
    with req.app.db.read() as connection:
        if (
            one(
                connection,
                "SELECT 1 AS x FROM subscriptions WHERE billing_customer_id = ?",
                (customer,),
            )
            is None
        ):
            raise NotFound("No organization uses this billing account.")
    body = (
        "<h1 style='margin-top:18px'>Billing portal (stand-in)</h1><p class='muted'>Use these to see how Aira reacts to payment events.</p>"
        f"<form method='post' action='/billing/standin/portal/{customer}/fail'><button class='btn ghost' type='submit' style='width:100%;margin-top:8px'>Simulate a failed payment</button></form>"
        f"<form method='post' action='/billing/standin/portal/{customer}/recover'><button class='btn' type='submit' style='width:100%;margin-top:8px'>Simulate a recovered payment</button></form>"
        f"<p class='muted'><a href='{quote(req.app.config.base_url)}/#/org/billing'>Back to Aira</a></p>"
    )
    return _page(body)


def _portal_event(req: Req, event_type: str) -> Reply:
    provider = _standin(req)
    customer = req.group()
    with req.app.db.read() as connection:
        sub = one(
            connection,
            "SELECT org_id FROM subscriptions WHERE billing_customer_id = ?",
            (customer,),
        )
    if sub is None:
        raise NotFound("No organization uses this billing account.")
    now = req.app.clock.now()
    data = {
        "org_id": sub["org_id"],
        "customer_id": customer,
        "invoice_id": f"in_portal_{now}",
        "amount_cents": 0,
    }
    if event_type == "invoice.paid":
        data.update({"period_start": now, "period_end": now + 30 * 86400})
    payload, signature = provider.make_event(event_type, data)
    req.app.billing.handle_webhook("standin", payload, signature)
    return Reply(
        303,
        b"",
        headers=[("Location", f"{req.app.config.base_url}/#/org/billing")],
        content_type="text/plain",
    )


@cloud_route(
    "POST", r"/billing/standin/portal/(cus_standin_[a-z0-9]{12})/fail", auth="public"
)
def standin_portal_fail(req: Req):  # noqa: ANN201
    return _portal_event(req, "invoice.payment_failed")


@cloud_route(
    "POST", r"/billing/standin/portal/(cus_standin_[a-z0-9]{12})/recover", auth="public"
)
def standin_portal_recover(req: Req):  # noqa: ANN201
    return _portal_event(req, "invoice.paid")


__all__ = ["Forbidden"]
