"""Worker agents and remote sign-in: LinkedIn-bound requests run on the host that holds the candidate's session."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request

import pytest
from cloud_support import (
    Browser,
    build_org,
    local_module,
    sign_in,
)  # noqa: F401

from aira_cloud.agent import AgentServer, agent_secret, sign
from aira_cloud.gateway import Gateway
from aira_cloud.worker import Worker

BROKER_SECRET = "broker-secret-" + "x" * 30


@pytest.fixture
def team(cloud):
    org = build_org(cloud, plan="team")
    cloud.tenancy.update_policy(
        org.owner,
        {"linkedin_allowed": True, "allowed_sources": ["aira_jobs", "linkedin"]},
    )
    # the candidate has accepted the LinkedIn notice (what connecting records)
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE workspaces SET linkedin_opt_in_at = ? WHERE candidate_id = ?",
            (cloud.clock.now(), org.candidate.member_id),
        )
    return org


def start_agent(cloud, worker_id: str, *, active: int = 0):
    agent = AgentServer(cloud, Gateway(cloud), port=0).start()
    cloud.queue.register_worker(worker_id, "apply", "host", 4, active, agent.url)
    return agent


@pytest.fixture
def spy(monkeypatch):
    """Record which thread ran each LinkedIn function: the test's own thread is the web tier, an agent's is not."""
    local = local_module()
    calls: list[dict] = []
    main = threading.current_thread()

    def status(user):
        calls.append(
            {
                "fn": "status",
                "where": "web" if threading.current_thread() is main else "agent",
                "user": user["user_id"],
            }
        )
        return {"state": "connected", "message": "spy"}

    def connect(user, *, fresh_login=False):
        calls.append(
            {
                "fn": "connect",
                "where": "web" if threading.current_thread() is main else "agent",
                "user": user["user_id"],
            }
        )
        return {
            "state": "login",
            "message": "Complete sign-in in the LinkedIn browser window.",
        }

    monkeypatch.setattr(local, "_linkedin_status", status)
    monkeypatch.setattr(local, "_start_linkedin_login", connect)
    return calls


def test_without_any_agent_the_web_process_runs_it(team, cloud, spy):
    candidate = sign_in(cloud, team.candidate)
    status, body = candidate.call("GET", "/api/linkedin/status")
    assert status == 200 and body["message"] == "spy" and spy[0]["where"] == "web"


def test_connect_picks_a_worker_and_later_requests_follow_the_session(team, cloud, spy):
    busy = start_agent(cloud, "w-busy", active=3)
    idle = start_agent(cloud, "w-idle", active=0)
    candidate = sign_in(cloud, team.candidate)
    status, body = candidate.call(
        "POST", "/api/linkedin/connect", {"acknowledge": True}
    )
    assert status == 200 and body["state"] == "login"
    assert spy[0] == {
        "fn": "connect",
        "where": "agent",
        "user": team.candidate.member_id,
    }
    assert (
        cloud.workspaces.row(team.org_id, team.candidate.member_id)["linkedin_host"]
        == idle.url
    ), "the least busy worker holds the session"
    for _ in range(3):
        assert candidate.call("GET", "/api/linkedin/status")[1]["message"] == "spy"
    assert [c["where"] for c in spy] == ["agent"] * 4
    busy.stop()
    idle.stop()


def test_a_worker_that_stops_answering_loses_the_session_and_another_takes_over(
    team, cloud, spy
):
    first = start_agent(cloud, "w-1")
    candidate = sign_in(cloud, team.candidate)
    candidate.call("POST", "/api/linkedin/connect", {"acknowledge": True})
    assert (
        cloud.workspaces.row(team.org_id, team.candidate.member_id)["linkedin_host"]
        == first.url
    )
    first.stop()  # the worker dies
    status, body = candidate.call("GET", "/api/linkedin/status")
    assert status == 503 and "could not be reached" in body["error"]
    assert (
        cloud.workspaces.row(team.org_id, team.candidate.member_id)["linkedin_host"]
        == ""
    ), "the dead worker no longer holds the session"
    second = start_agent(cloud, "w-2")
    status, body = candidate.call(
        "POST", "/api/linkedin/connect", {"acknowledge": True}
    )
    assert (
        status == 200
        and cloud.workspaces.row(team.org_id, team.candidate.member_id)["linkedin_host"]
        == second.url
    )
    second.stop()


def test_the_agent_executes_only_what_the_web_tier_signed(team, cloud, spy):
    agent = start_agent(cloud, "w-1")
    secret = agent_secret(cloud)

    def post(body: bytes, *, signature=None, timestamp=None, nonce="0" * 24):
        stamp = cloud.clock.now() if timestamp is None else timestamp
        headers = {
            "Content-Type": "application/json",
            "X-Aira-Agent-Timestamp": str(stamp),
            "X-Aira-Agent-Nonce": nonce,
            "X-Aira-Agent-Signature": signature
            if signature is not None
            else sign(secret, stamp, nonce, body),
        }
        request = urllib.request.Request(
            f"{agent.url}/agent/v1/invoke", data=body, method="POST", headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())

    spec = {
        "org_id": team.org_id,
        "candidate_id": team.candidate.member_id,
        "principal": {
            "member_id": team.candidate.member_id,
            "org_id": team.org_id,
            "role": "candidate",
            "email": team.candidate.email,
            "kind": "member",
        },
        "route": "api_linkedin_status",
        "method": "GET",
        "path": "/api/linkedin/status",
        "payload": {},
    }
    body = json.dumps(spec).encode()
    assert post(body, signature="0" * 64)[0] == 401, "a wrong signature is refused"
    assert post(body, timestamp=cloud.clock.now() - 3600)[0] == 401, (
        "an old request is refused"
    )
    assert post(body, nonce="not-hex")[0] == 401
    status, reply = post(body, nonce="a" * 24)
    assert status == 200 and reply["status"] == 200 and spy[-1]["where"] == "agent"
    assert post(body, nonce="a" * 24)[0] == 401, "a request cannot be replayed"
    tampered = json.dumps({**spec, "candidate_id": team.other.member_id}).encode()
    assert (
        post(
            tampered,
            signature=sign(secret, cloud.clock.now(), "b" * 24, body),
            nonce="b" * 24,
        )[0]
        == 401
    ), "the body is part of the signature"
    # even a correctly signed request for a route that does not exist, or a candidate outside the organization, does nothing
    assert (
        post(json.dumps({**spec, "route": "api_nonexistent"}).encode(), nonce="c" * 24)[
            0
        ]
        == 500
    )
    assert (
        post(json.dumps({**spec, "candidate_id": "f" * 32}).encode(), nonce="d" * 24)[0]
        == 500
    )
    request = urllib.request.Request(
        f"{agent.url}/agent/v1/invoke", data=b"{}", method="POST"
    )
    with pytest.raises(urllib.error.HTTPError) as raised:
        urllib.request.urlopen(request, timeout=10)
    assert raised.value.code == 401, "an unsigned request is refused"
    agent.stop()


def test_a_coach_is_refused_in_the_web_tier_before_anything_is_forwarded(
    team, cloud, spy
):
    agent = start_agent(cloud, "w-1")
    cloud.tenancy.assign_coach(
        team.owner, team.coach.member_id, team.candidate.member_id, "approve"
    )
    coach = sign_in(cloud, team.coach)
    assert (
        coach.call(
            "POST",
            "/api/linkedin/connect",
            {"acknowledge": True},
            as_candidate=team.candidate.member_id,
        )[0]
        == 403
    )
    assert (
        coach.call("GET", "/api/inbox", as_candidate=team.candidate.member_id)[0] == 403
    )
    assert spy == [], "the agent never saw a request the web tier refused"
    agent.stop()


def test_redaction_still_applies_to_responses_that_came_back_from_an_agent(
    team, cloud, spy, monkeypatch
):
    agent = start_agent(cloud, "w-1")
    local = local_module()
    # make a profile read go through the agent, to prove the web tier redacts what an agent returns
    from aira_cloud import policy

    monkeypatch.setitem(policy.REMOTE_ROUTES, "api_profile", False)
    candidate = sign_in(cloud, team.candidate)
    candidate.call(
        "POST",
        "/api/profile",
        {
            "resume": "Cloud architect. " * 10,
            "application_profile": {
                "full_name": "Cam",
                "email": "cand@acme.test",
                "phone": "+1 555 0100",
                "location": "Seattle",
                "visa_status": "SECRET-VISA-VALUE",
            },
        },
    )
    cloud.tenancy.assign_coach(
        team.owner, team.coach.member_id, team.candidate.member_id, "approve"
    )
    with cloud.db.write() as connection:
        connection.execute(
            "UPDATE workspaces SET linkedin_host = ? WHERE candidate_id = ?",
            (agent.url, team.candidate.member_id),
        )
    coach = sign_in(cloud, team.coach)
    status, data = coach.call(
        "GET", "/api/profile", as_candidate=team.candidate.member_id
    )
    assert (
        status == 200
        and "SECRET-VISA-VALUE" not in json.dumps(data)
        and data["sensitive_set"]["visa_status"] is True
    )
    assert (
        candidate.call("GET", "/api/profile")[1]["candidate_profile"]["visa_status"]
        == "SECRET-VISA-VALUE"
    )
    agent.stop()
    assert local is not None


# ----------------------------------------------------------------------------- queue affinity


def test_jobs_pinned_to_a_session_host_wait_for_that_host_unless_it_is_gone(cloud):
    one = start_agent(cloud, "w-1")
    two = start_agent(cloud, "w-2")
    job = cloud.queue.enqueue("org-a", "autopilot.due", candidate_id="c1", host=one.url)
    assert cloud.queue.lease("w-2", agent_url=two.url) is None, (
        "another host may not take a job pinned elsewhere"
    )
    assert cloud.queue.lease("w-1", agent_url=one.url)["job_id"] == job
    cloud.queue.complete(job)
    pinned = cloud.queue.enqueue(
        "org-a", "autopilot.due", candidate_id="c2", host=one.url
    )
    one.stop()
    cloud.clock.advance(seconds=61)  # w-1 stopped reporting
    cloud.queue.register_worker("w-2", "apply", "host", 4, 0, two.url)
    assert cloud.queue.lease("w-2", agent_url=two.url)["job_id"] == pinned, (
        "a dead host's work is picked up by another"
    )
    two.stop()


def test_the_worker_registers_its_agent_address(cloud):
    agent = AgentServer(cloud, Gateway(cloud), port=0).start()
    worker = Worker(
        cloud, threads=1, handlers={}, run_tick=False, worker_id="w-reg", agent=agent
    )
    cloud.queue.register_worker("w-reg", "apply", "host", 1, 0, worker.agent.url)
    assert [a["agent_url"] for a in cloud.agents.agents()] == [agent.url]
    agent.stop()


# ----------------------------------------------------------------------------- remote sign-in


def test_remote_sign_in_link_is_short_lived_single_use_and_the_candidates_own(
    tmp_path, spy
):
    from aira.hosting import HOOKS
    from cloud_support import make_app

    HOOKS.reset()
    app = make_app(
        tmp_path,
        remote_login_url="https://login.example.test/s/{token}",
        broker_secret=BROKER_SECRET,
    )
    org = build_org(app, plan="team")
    app.tenancy.update_policy(
        org.owner,
        {"linkedin_allowed": True, "allowed_sources": ["aira_jobs", "linkedin"]},
    )
    candidate = sign_in(app, org.candidate)
    status, body = candidate.call(
        "POST", "/api/linkedin/connect", {"acknowledge": True}
    )
    assert status == 200 and body["state"] == "login"
    link = body["remote_login"]["url"]
    assert (
        link.startswith("https://login.example.test/s/")
        and body["remote_login"]["expires_in"] == 600
    )
    token = link.rsplit("/", 1)[1]
    assert app.workspaces.row(org.org_id, org.candidate.member_id)[
        "linkedin_opt_in_at"
    ], "connecting recorded the User Agreement notice"
    # the gateway validates the token once, with a signed call
    import time as _time

    def validate(token_value, *, nonce, stamp=None, bad=False):
        raw = json.dumps({"token": token_value}).encode()
        stamp = app.clock.now() if stamp is None else stamp
        browser = Browser(app)
        headers = {
            "X-Aira-Agent-Timestamp": str(stamp),
            "X-Aira-Agent-Nonce": nonce,
            "X-Aira-Agent-Signature": "0" * 64
            if bad
            else sign(BROKER_SECRET, stamp, nonce, raw, "broker"),
        }
        return browser.call(
            "POST",
            "/api/internal/remote-login/validate",
            {"token": token_value},
            headers=headers,
        )

    assert validate(token, nonce="1" * 24, bad=True)[0] == 401
    # the agent secret (even over the same body) is not the broker's credential, and signatures do not cross purposes
    raw = json.dumps({"token": token}).encode()
    stamp = app.clock.now()
    for secret, purpose in (
        (agent_secret(app), "broker"),
        (agent_secret(app), "agent"),
        (BROKER_SECRET, "agent"),
    ):
        status, _ = Browser(app).call(
            "POST",
            "/api/internal/remote-login/validate",
            {"token": token},
            headers={
                "X-Aira-Agent-Timestamp": str(stamp),
                "X-Aira-Agent-Nonce": "9" * 24,
                "X-Aira-Agent-Signature": sign(secret, stamp, "9" * 24, raw, purpose),
            },
        )
        assert status == 401, (purpose, secret == BROKER_SECRET)
    status, info = validate(token, nonce="2" * 24)
    assert (
        status == 200
        and info["candidate_id"] == org.candidate.member_id
        and info["org_id"] == org.org_id
    )
    assert info["profile_dir"].endswith("/linkedin/profile") or info[
        "profile_dir"
    ].endswith("\\linkedin\\profile")
    assert validate(token, nonce="3" * 24)[0] == 410, "single use"
    # a new connect replaces the earlier link; an expired link is refused
    status, again = candidate.call(
        "POST", "/api/linkedin/connect", {"acknowledge": True}
    )
    fresh = again["remote_login"]["url"].rsplit("/", 1)[1]
    app.clock.advance(seconds=601)
    assert validate(fresh, nonce="4" * 24)[0] == 410
    assert validate("nonsense-token-value", nonce="5" * 24)[0] == 404
    assert "linkedin.remote_login_issued" in [
        r["action"]
        for r in app.audit.list(org.org_id, candidate_id=org.candidate.member_id)
    ]
    app.close()
    assert _time is not None


def test_a_signed_request_cannot_be_replayed_inside_the_window_by_another_verifier(
    team, cloud
):
    from aira_cloud.agent import Verifier

    secret = agent_secret(cloud)
    body = b'{"x": 1}'
    stamp = cloud.clock.now()
    headers = {
        "X-Aira-Agent-Timestamp": str(stamp),
        "X-Aira-Agent-Nonce": "d" * 24,
        "X-Aira-Agent-Signature": sign(secret, stamp, "d" * 24, body),
    }
    Verifier(cloud).verify(headers, body)
    with pytest.raises(PermissionError):  # a second process shares the control plane
        Verifier(cloud).verify(headers, body)
    # still refused at the last second the timestamp is acceptable (the nonce is kept that long)
    cloud.clock.advance(seconds=59)
    with pytest.raises(PermissionError):
        Verifier(cloud).verify(headers, body)
    # an unconfigured broker secret switches the broker routes off
    cloud.config.broker_secret = ""
    with pytest.raises(PermissionError):
        Verifier(cloud).verify(headers, body, purpose="broker")
