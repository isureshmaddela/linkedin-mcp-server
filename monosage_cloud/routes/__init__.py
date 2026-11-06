"""The control-plane API: sign-in, organization, billing, platform console, SSO and the web UI shell."""

from monosage_cloud.routes import auth, billing, org, platform, public  # noqa: F401
from monosage_cloud.routes import extra_ui_auth, extra_ui_billing, legal  # noqa: F401
from monosage_cloud.routes import apps  # noqa: F401
from monosage_cloud.routes import developers  # noqa: F401  (OAuth 2.0, tokens, webhooks)
from monosage_cloud.routes import creators  # noqa: F401  (Videos moderation, Creators & ads, ad top-ups)
from monosage_cloud.routes import live  # noqa: F401  (live updates for open pages, Server-Sent Events)
from monosage_cloud import sso_routes  # noqa: F401
