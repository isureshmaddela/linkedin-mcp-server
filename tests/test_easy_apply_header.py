"""The Easy Apply job-header probe, run in a real Chromium against LinkedIn-shaped job pages.

A job page that shows its title as a plain element instead of an ``<h1>``, or that draws its top card a
moment after ``<main>`` appears, used to end every Easy Apply run with "Could not find the job header" even
though the page was fine and the Easy Apply button was on screen.
"""

from __future__ import annotations

import pytest
from patchright.async_api import async_playwright

from linkedin_mcp_server.scraping import easy_apply as ea
from linkedin_mcp_server.scraping import extractor as extractor_module
from linkedin_mcp_server.scraping.extractor import _EasyApplyRun

NAV = """<nav><a href="/feed/">Home</a><a href="/jobs/">Jobs</a><button>Me</button></nav>"""

CLASSIC = f"""<html><head><title>(3) Salesforce Developer | Bishop Gold Group | LinkedIn</title></head><body>{NAV}
<main><section><div><a href="/company/bishop">Bishop Gold Group</a>
<h1>Salesforce Developer</h1><p>Los Angeles, CA</p>
<button aria-label="Easy Apply to Salesforce Developer at Bishop Gold Group"><span>Easy Apply</span></button>
<button aria-pressed="false">Save</button></div></section></main></body></html>"""

NO_H1 = f"""<html><head><title>Salesforce Developer | Bishop Gold Group | LinkedIn</title></head><body>{NAV}
<main><div><div><a href="/company/bishop"><span>Bishop Gold Group</span></a></div>
<div><p class="title">Salesforce Developer <svg width="12" height="12"></svg></p>
<p>Los Angeles, CA · 3 weeks ago · Over 100 applicants</p></div>
<div><a role="button" href="/jobs/view/1/apply/" aria-label="Easy Apply to this job">Easy Apply</a>
<button aria-pressed="false">Save</button></div></div>
<section><h2>People also viewed</h2><p>Salesforce Developer</p></section></main></body></html>"""

LATE = f"""<html><head><title>Salesforce Developer | Bishop Gold Group | LinkedIn</title></head><body>{NAV}
<main id="m"></main>
<script>
setTimeout(() => {{
  document.getElementById('m').innerHTML = '<div><p>Salesforce Developer</p><p>Remote</p></div>';
}}, 700);
setTimeout(() => {{
  document.querySelector('#m div').insertAdjacentHTML('afterend',
    '<div><button aria-label="Easy Apply to Salesforce Developer"><span>Easy Apply</span></button></div>');
}}, 1600);
</script></body></html>"""

UNAVAILABLE = f"""<html><head><title>LinkedIn</title></head><body>{NAV}
<main><p>This job is no longer available.</p></main></body></html>"""


class Runner:
    """Just enough of _EasyApplyRun for the header methods: a page and the key attribute."""

    def __init__(self, page) -> None:
        self._page = page

    _key_args = _EasyApplyRun._key_args
    _read_header = _EasyApplyRun._read_header
    _wait_for_header = _EasyApplyRun._wait_for_header
    _page_brief = _EasyApplyRun._page_brief


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={"width": 1280, "height": 900})
        yield await context.new_page()
        await browser.close()


async def header_of(page, html: str) -> dict | None:
    await page.set_content(html)
    return await Runner(page)._wait_for_header()


def apply_control(header: dict):
    return ea.choose_apply_control(list(header["controls"]))


async def test_a_classic_h1_job_page_still_works(page):
    header = await header_of(page, CLASSIC)
    assert header is not None and header["title"] == "Salesforce Developer"
    control = apply_control(header)
    assert control is not None and control.kind == "easy_apply"


async def test_a_job_title_that_is_not_an_h1_is_found_from_the_tab_title(page):
    header = await header_of(page, NO_H1)
    assert header is not None, "the title shown in a plain element must be recognised"
    assert header["title"] == "Salesforce Developer"
    control = apply_control(header)
    assert control is not None and control.kind == "easy_apply"
    # the nav bar and the 'People also viewed' repeat are not mistaken for the top card
    texts = [c["text"] for c in header["controls"]]
    assert "Jobs" not in texts and "Me" not in texts


async def test_a_page_that_draws_its_top_card_late_is_waited_for(page):
    header = await header_of(page, LATE)
    assert header is not None and header["controls"], (
        "the title and the button appear after main does"
    )
    assert apply_control(header).kind == "easy_apply"


async def test_a_page_with_no_job_on_it_gives_up_with_what_the_tab_says(
    page, monkeypatch
):
    monkeypatch.setattr(extractor_module, "_EASY_APPLY_HEADER_WAIT_SECONDS", 0.6)
    await page.set_content(UNAVAILABLE)
    runner = Runner(page)
    assert await runner._wait_for_header() is None
    assert "LinkedIn" in await runner._page_brief()
