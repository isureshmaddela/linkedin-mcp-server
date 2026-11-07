"""Setting up two-step verification: the setup key stays the same for five minutes.

The sign-in page asks for a key whenever it draws the setup screen; a reload or a second tab within five minutes gets
the key already typed into the authenticator app, so the code it shows still works. After that a new key is made.
"""

from __future__ import annotations

from cloud_support import Browser, pay, signup

from monosage_cloud import crypto
from monosage_cloud.identity import MFA_PENDING_SECONDS


def test_the_setup_key_is_kept_for_five_minutes(cloud):
    result = signup(cloud)
    pay(cloud, result["org"]["org_id"], "team", 25)  # owners on Team must use MFA
    owner = Browser(cloud)
    owner.login("owner@acme.test")
    assert owner.call("GET", "/api/org")[1]["code"] == "mfa_enrollment_required"

    first = owner.call("POST", "/api/auth/mfa/begin", {})[1]
    cloud.clock.advance(seconds=MFA_PENDING_SECONDS - 60)
    again = owner.call("POST", "/api/auth/mfa/begin", {})[1]
    assert again["secret"] == first["secret"]  # the page asked again: same key

    cloud.clock.advance(seconds=120)  # more than five minutes after it was made
    fresh = owner.call("POST", "/api/auth/mfa/begin", {})[1]
    assert fresh["secret"] != first["secret"]

    status, enabled = owner.call(
        "POST",
        "/api/auth/mfa/enable",
        {"code": crypto.totp_code(fresh["secret"], cloud.clock.now())},
    )
    assert status == 200 and len(enabled["recovery_codes"]) == 10
    assert owner.call("GET", "/api/org")[0] == 200
