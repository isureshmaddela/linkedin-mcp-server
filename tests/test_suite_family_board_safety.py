"""The Family board's public page: children and teens by their initials only, and a link that expires and can be
replaced."""

from __future__ import annotations

from datetime import timedelta

import pytest

import test_suite_family
from aira.suite import kit, life, public
from aira.suite.family import family_routes, service  # noqa: F401 - registers the board page

db = test_suite_family.db  # shared fixtures
home = test_suite_family.home
_no_model = test_suite_family._no_model


def test_the_board_names_children_and_teens_by_initials(db, home):
    pat, ana, mia = home
    teo = life.add(db, pat, {"name": "Teo Rao", "age_group": "teen"})
    service.create_post(db, mia, {"text": "I lost a tooth!"})
    service.create_post(db, teo, {"text": "Match day"})
    service.create_post(db, ana, {"text": "Family news"})
    page = service.board_page(db)
    authors = {p["text"]: p["author"]["name"] for p in page["posts"]}
    assert authors == {
        "I lost a tooth!": "M.",
        "Match day": "T. R.",
        "Family news": "Ana",
    }
    assert sorted(p["name"] for p in page["people"]) == sorted(
        ["Pat", "Ana", "M.", "T. R."]
    )
    assert service.initials("mia rao") == "M. R." and service.initials("") == "?"


def test_the_board_link_expires_and_can_be_rotated(db, home):
    link = public.create_link(
        db, "family", "board", service.BOARD_REF, title="Our family"
    )
    service.start_board_link(db)
    assert public.view(db, link["token"])["page"]["title"]
    state = service.board_link_state(db)
    assert state["days"] == 30
    assert kit.parse_time(state["expires_at"]) > kit.utcnow() + timedelta(days=29)
    # its time is up: the page and its actions answer like a revoked link
    kit.set_setting(
        db,
        "family",
        "board_link",
        {"expires_at": kit.iso(kit.utcnow() - timedelta(minutes=1))},
    )
    db.commit()
    with pytest.raises(LookupError):
        public.view(db, link["token"])
    with pytest.raises(LookupError):
        public.act(
            db, link["token"], "post", {"name": "May", "text": "Hi"}, {"client": "x"}
        )
    # a new link works again for 30 days; the old token stays dead
    public.revoke(db, "family", "board", service.BOARD_REF)
    fresh = public.create_link(
        db, "family", "board", service.BOARD_REF, title="Our family"
    )
    service.start_board_link(db)
    assert fresh["token"] != link["token"]
    assert public.view(db, fresh["token"])["page"]["people"]
    with pytest.raises(LookupError):
        public.view(db, link["token"])
