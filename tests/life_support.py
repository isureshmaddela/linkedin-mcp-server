"""Helpers for life app tests on the single-user server: call it as one person of the household.

household = Household(env, api)              # the signed-in account; its first person is the owner
mia = household.add("Mia", age_group="child", pin="2468")
status, data = mia.call("GET", "/api/learn/decks")
mia.ok("POST", "/api/learn/decks", {...}, expect=201)
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any


class Person:
    def __init__(
        self, env: Any, api: Any, profile: dict[str, Any], header: str
    ) -> None:
        self.env = env
        self.api = api
        self.profile = profile
        self.header = header
        self.id = profile["id"]
        self.name = profile["name"]

    def call(
        self, method: str, path: str, body: Any = None, *, raw: bool = False
    ) -> tuple[int, Any]:
        data = json.dumps(body).encode() if body is not None else None
        request = urllib.request.Request(self.env.base + path, data=data, method=method)
        for name, value in {
            "Content-Type": "application/json",
            "X-Requested-With": "aira",
            "Origin": self.env.base,
            "X-Aira-Profile": self.header,
        }.items():
            request.add_header(name, value)
        try:
            with self.api.opener.open(request, timeout=60) as response:
                payload = response.read()
                return response.status, (
                    payload if raw else json.loads(payload or b"{}")
                )
        except urllib.error.HTTPError as error:
            payload = error.read()
            try:
                return error.code, json.loads(payload or b"{}")
            except ValueError:
                return error.code, {"error": payload.decode("utf-8", "replace")}

    def ok(self, method: str, path: str, body: Any = None, *, expect: int = 200) -> Any:
        status, data = self.call(method, path, body)
        assert status == expect, (self.name, method, path, status, data)
        return data

    def get(self, path: str) -> Any:
        return self.ok("GET", path)

    def post(self, path: str, body: Any = None, *, expect: int = 200) -> Any:
        return self.ok("POST", path, {} if body is None else body, expect=expect)


class Household:
    """The account's people. The first call makes the owner from the sign-in."""

    def __init__(self, env: Any, api: Any) -> None:
        self.env = env
        self.api = api
        owner = api.get("/api/life/me")["profile"]
        self.owner = Person(env, api, owner, owner["id"])

    def add(
        self, name: str, *, age_group: str = "adult", pin: str = "", **extra: Any
    ) -> Person:
        body = {"name": name, "age_group": age_group, **extra}
        if pin:
            body["pin"] = pin
        profile = self.owner.post("/api/life/profiles", body, expect=201)["profile"]
        unlocked = self.owner.post(
            "/api/life/unlock", {"profile_id": profile["id"], "pin": pin}
        )
        return Person(self.env, self.api, profile, unlocked["header"])
