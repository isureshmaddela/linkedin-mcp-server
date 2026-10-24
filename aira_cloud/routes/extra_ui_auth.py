"""Routes the sign-in, onboarding and coach screens need beyond the core API."""

from __future__ import annotations

from aira_cloud.policy import LINKEDIN_NOTICE
from aira_cloud.webio import Req, cloud_route


@cloud_route("GET", r"/api/me/linkedin-notice", verified=False)
def linkedin_notice(req: Req):  # noqa: ANN201
    """The warning shown before LinkedIn is connected or Easy Apply is turned on (the same text the API enforces)."""
    text = LINKEDIN_NOTICE.split(" Send acknowledge:")[0]
    return 200, {"notice": text}
