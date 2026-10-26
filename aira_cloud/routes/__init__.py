"""The control-plane API: sign-in, organization, billing, platform console, SSO and the web UI shell."""

from aira_cloud.routes import auth, billing, org, platform, public  # noqa: F401
from aira_cloud.routes import extra_ui_auth, extra_ui_billing, extra_ui_org, legal  # noqa: F401
from aira_cloud.routes import apps  # noqa: F401
from aira_cloud.routes import developers  # noqa: F401  (OAuth 2.0, tokens, webhooks)
from aira_cloud.routes import creators  # noqa: F401  (Videos moderation, Creators & ads, ad top-ups)
from aira_cloud import sso_routes  # noqa: F401
