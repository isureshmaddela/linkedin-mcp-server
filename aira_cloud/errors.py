"""Errors the hosted shell raises; the gateway maps each to one HTTP status and error code."""

from __future__ import annotations

from typing import Any


class CloudError(Exception):
    status = 400
    code = "bad_request"

    def __init__(self, message: str = "", **extra: Any) -> None:
        super().__init__(message or self.__class__.__name__)
        self.message = message or "The request could not be completed."
        if "code" in extra:
            self.code = str(
                extra.pop("code")
            )  # a specific code for this instance, e.g. org_required
        self.extra = extra

    def payload(self) -> dict[str, Any]:
        return {"error": self.message, "code": self.code, **self.extra}


class ValidationFailed(CloudError):
    status = 400
    code = "invalid"


class Unauthorized(CloudError):
    status = 401
    code = "unauthorized"


class MfaRequired(Unauthorized):
    code = "mfa_required"


class Forbidden(CloudError):
    status = 403
    code = "forbidden"


class NotFound(CloudError):
    status = 404
    code = "not_found"


class Conflict(CloudError):
    status = 409
    code = "conflict"


class Gone(CloudError):
    status = 410
    code = "gone"


class Locked(CloudError):
    status = 423
    code = "locked"


class TooManyRequests(CloudError):
    status = 429
    code = "rate_limited"


class PlanRequired(CloudError):
    """A feature or limit the organization's plan does not allow (HTTP 402)."""

    status = 402
    code = "plan_required"


class SubscriptionInactive(CloudError):
    """The subscription state blocks this action (past due, read-only, cancelled)."""

    status = 402
    code = "subscription_inactive"


class AppRequired(CloudError):
    """The organization has not subscribed to the app that owns this route (HTTP 402)."""

    status = 402
    code = "app_required"


class Unavailable(CloudError):
    status = 503
    code = "unavailable"
