"""Life stages in a real browser against the local server.

Your own life: the birth date places you on the road of life; a checklist item is ticked, set aside, or turned into a
goal; an interest brings ideas; wishes are written. Someone you look after: a baby is added, a vaccine with its next
dose shows as coming up, a milestone is recorded, a plan is asked for. The overview shows your stage. A phone in dark
mode has no sideways scroll. It fails on any script error. Screenshots go to ``STAGES_SHOTS`` (default: none).
"""

# ruff: noqa: F811
from __future__ import annotations

import os
import re
from datetime import date, timedelta
from pathlib import Path

import pytest

patchright = pytest.importorskip("patchright.sync_api")

from aira import suite  # noqa: E402
from aira.suite import kit  # noqa: E402
from test_careers import env, stub  # noqa: E402, F401
from test_leads_ui import browser, go, signed_in_page, until  # noqa: E402, F401
from test_suite_care_ui import dismiss_reminders  # noqa: E402

SHOTS = Path(os.environ["STAGES_SHOTS"]) if os.environ.get("STAGES_SHOTS") else None


class _NoModel:
    def model(self, prompt):
        return None


@pytest.fixture(autouse=True)
def _no_model():
    previous = suite.gateway()
    suite.set_gateway(_NoModel())
    yield
    suite.set_gateway(previous)


def shot(page, name: str) -> None:
    if SHOTS is None:
        return
    SHOTS.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=True)


def open_stages(page, env, query: str = "") -> None:
    go(page, env, f"journey?tab=stages{query}")
    page.wait_for_selector("#main .jr-stage-hero")
    page.wait_for_timeout(300)
    dismiss_reminders(page)


def hero_title(page) -> str:
    return page.locator("#main .jr-stage-title").inner_text()


def test_your_own_life_from_the_birth_date_to_your_wishes(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    open_stages(page, env)
    assert page.locator("#main h1").inner_text() == "Life stages"
    # No birth date yet: a guess from the profile, and a prompt for it.
    page.wait_for_selector("#main .jr-birth-prompt")
    born = date.today().replace(year=date.today().year - 34).isoformat()
    page.fill("#main .jr-birth-prompt input[type=date]", born)
    page.click("#main .jr-birth-prompt button:has-text('Save')")
    page.wait_for_selector("#main .jr-birth-prompt", state="detached")
    assert hero_title(page) == "Building years"
    assert "34 years old" in page.locator("#main .jr-stage-hero").inner_text()
    stops = page.locator("#main .jr-road-stop")
    assert stops.count() == 11  # baby to elder: your own road starts at birth
    assert (
        page.locator("#main .jr-road-stop.current .jr-road-title").inner_text()
        == "Building years"
    )
    assert (
        page.locator("#main .jr-road-stop.current").inner_text().count("You are here")
        == 1
    )
    shot(page, "01-own-stage")

    # Tick an item, set one aside, and turn one into a goal (it shows the goal it became).
    move = page.locator("#main .jr-item[data-jr-open='item:building.move']")
    move.locator("button.life-check").click()
    page.wait_for_selector("#main .jr-item.done[data-jr-open='item:building.move']")
    home = page.locator("#main .jr-item[data-jr-open='item:building.home']")
    home.locator("button:has-text('Not for me')").click()
    page.wait_for_selector(
        "#main .jr-item.skipped[data-jr-open='item:building.home'] button:has-text('Put it back')"
    )
    page.locator(
        "#main .jr-item[data-jr-open='item:building.career'] button:has-text('Make it a goal')"
    ).click()
    page.wait_for_selector(".modal h2:has-text('New goal')")
    assert page.input_value(".modal input[name=title]") == "Grow in your career"
    assert page.input_value(".modal select[name=category]") == "career"
    page.click(".modal button:has-text('Add goal')")
    page.wait_for_selector(
        "#main .jr-item[data-jr-open='item:building.career'] a:has-text('Goal: Grow in your career')"
    )
    assert "1 of" in page.locator("#main .jr-stage-progress").first.inner_text()

    # Earlier stages: still-open items come along, and a past stage shows its milestones first.
    assert page.locator("#main .jr-carried .jr-item").count() >= 2
    page.locator("#main .jr-road-stop", has_text="School years").click()
    page.wait_for_url(re.compile(r"stage=child"))
    page.wait_for_selector("#main h2:has-text('Looking back: School years')")
    page.locator("#main button.jr-milestone", has_text="Learned to ride a bike").click()
    page.wait_for_selector(".modal input[name=title]")
    page.fill(".modal input[name=day]", f"{int(born[:4]) + 7}-06-01")
    page.click(".modal button:has-text('Save the moment')")
    page.wait_for_selector(
        "#main .jr-milestone.done:has-text('Learned to ride a bike')"
    )
    shot(page, "02-looking-back")

    # Interests bring ideas for this age, with a way to earn through Monosage's apps.
    page.locator("#main .jr-road-stop.current").click()
    page.wait_for_selector("#main h2:has-text('What matters now')")
    page.locator("#main .jr-interest", has_text="Technology and coding").click()
    page.wait_for_selector("#main .jr-idea:has-text('Earn with your tech skills')")
    assert (
        page.locator("#main .jr-idea a:has-text('Open Sites')").get_attribute("href")
        == "#/sites"
    )
    page.locator("#main .jr-idea", has_text="Teach others").locator(
        "button:has-text('Done')"
    ).click()
    page.wait_for_selector("#main .jr-idea.done:has-text('Teach others')")

    # A plan for the stage (no writing model here: the checklist's own).
    page.click("#main .jr-stage-hero button:has-text('Ask for a plan')")
    page.wait_for_selector(".modal .jr-plan-step")
    assert page.locator(".modal .jr-plan-step").count() >= 3
    page.click(".modal button:has-text('Close')")
    page.wait_for_selector(".modal-backdrop", state="detached")

    # Wishes and legacy.
    page.click("#main button:has-text('Write your wishes')")
    page.wait_for_selector(".modal h2:has-text('Your wishes')")
    page.fill(".modal textarea[name=health_proxy]", "My sister Lata, 98765 43210")
    page.select_option(".modal select[name=organ_donation]", "yes")
    page.click(".modal button:has-text('Save my wishes')")
    page.wait_for_selector("#main .card:has-text('1 of 7 written')")
    shot(page, "03-ideas-and-wishes")

    # The overview shows the stage of life.
    go(page, env, "journey")
    page.wait_for_selector("#main .jr-stage-mini")
    assert "Building years" in page.locator("#main .jr-stage-mini").inner_text()
    assert not problems, problems
    context.close()


def test_someone_you_look_after_from_a_baby_onwards(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(browser, env, width=1440, height=900)
    open_stages(page, env)
    page.click("#main .jr-people button:has-text('Someone you look after')")
    page.wait_for_selector(".modal h2:has-text('Someone you look after')")
    page.fill(".modal input[name=name]", "Mira")
    page.fill(
        ".modal input[name=birth_date]",
        (date.today() - timedelta(days=150)).isoformat(),
    )
    page.fill(".modal input[name=birth_place]", "Hyderabad")
    page.click(".modal button:has-text('Add')")
    page.wait_for_url(re.compile(r"life=[0-9a-f]{32}"))
    page.wait_for_selector("#main .jr-stage-title:has-text('Baby')")
    assert "Mira · My child" in page.locator("#main .jr-stage-hero").inner_text()
    assert "Mira" in page.locator("#main .jr-person.on").inner_text()
    # Babies are too young for interest ideas: the page says what helps instead.
    assert "play, talk, songs and stories" in page.locator("#main").inner_text()

    # A vaccine with its next dose: the book shows it coming up.
    page.locator(
        "#main .jr-item[data-jr-open='item:baby.vaccines'] button:has-text('Add a vaccine')"
    ).click()
    page.wait_for_selector(".modal h2:has-text('Add a vaccine')")
    page.fill(".modal input[name=title]", "Rotavirus")
    page.fill(".modal input[name=detail]", "Second dose")
    page.fill(
        ".modal input[name=next_due]", (date.today() + timedelta(days=12)).isoformat()
    )
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector("#main .jr-due:has-text('Rotavirus · Second dose')")
    # A measurement and a milestone.
    page.locator("#main .jr-book-head button:has-text('Add a measurement')").click()
    page.wait_for_selector(".modal input[name=height_cm]")
    page.fill(".modal input[name=height_cm]", "62.5")
    page.fill(".modal input[name=weight_kg]", "6.4")
    page.click(".modal button:has-text('Save')")
    page.wait_for_selector("#main .jr-growth td:has-text('62.5 cm')")
    page.locator("#main button.jr-milestone", has_text="First smile").click()
    page.fill(
        ".modal input[name=day]", (date.today() - timedelta(days=100)).isoformat()
    )
    page.click(".modal button:has-text('Save the moment')")
    page.wait_for_selector("#main .jr-milestone.done:has-text('First smile')")
    # Make a money goal from the future fund: the goal form starts as a money goal.
    page.locator(
        "#main .jr-item[data-jr-open='item:baby.future_fund'] button:has-text('Make it a goal')"
    ).click()
    page.wait_for_selector(".modal h2:has-text('New goal')")
    assert (
        page.input_value(".modal input[name=title]")
        == "Mira: Start saving for their future"
    )
    assert page.input_value(".modal select[name=measure]") == "money"
    page.keyboard.press("Escape")
    page.wait_for_selector(".modal-backdrop", state="detached")
    shot(page, "04-baby")

    # Back to your own life.
    page.locator("#main .jr-person", has_text="(you)").click()
    page.wait_for_url(re.compile(r"tab=stages(?!.*life=)"))
    page.wait_for_selector("#main .jr-stage-hero")
    assert page.locator("#main .jr-person").count() == 2
    assert not problems, problems
    context.close()


def test_a_phone_in_dark_mode_has_no_sideways_scroll_on_life_stages(browser, env):
    kit.forget_schema()
    context, page, problems = signed_in_page(
        browser, env, width=390, height=844, dark=True
    )
    open_stages(page, env)
    page.fill("#main .jr-birth-prompt input[type=date]", "1966-03-03")
    page.click("#main .jr-birth-prompt button:has-text('Save')")
    page.wait_for_selector("#main .jr-stage-title:has-text('Later working years')")
    for stage in ("", "&stage=baby", "&stage=elder"):
        open_stages(page, env, stage)
        until(
            page,
            lambda: page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth"
            ),
            what="no sideways scroll",
        )
        # The road scrolls inside itself, not the page.
        assert page.evaluate(
            "() => { const r = document.querySelector('.jr-road'); return r.scrollWidth > r.clientWidth; }"
        )
        shot(page, f"05-phone{stage.replace('&stage=', '-') or '-now'}")
    assert not problems, problems
    context.close()
