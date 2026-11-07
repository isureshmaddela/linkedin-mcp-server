"""The Sales Navigator reader's output feeding Aira Leads: the contract between the two halves.

The reader (``linkedin_mcp_server``) runs in a real Chromium against the stand-in Sales Navigator; its answer is
then normalized, scored, saved and de-duplicated by Aira Leads. If either side renames a field this fails.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import AsyncIterator

import pytest
from fastmcp import FastMCP
from linkedin_sales_nav_stub import PAGE_SIZE, SalesNavStub, make_leads
from patchright.async_api import async_playwright
from test_tools import get_tool_fn

from aira.leads import scoring, service, sources, store
from aira.schema import migrate
from linkedin_mcp_server.scraping import extractor as extractor_module
from linkedin_mcp_server.scraping.extractor import LinkedInExtractor

REAL_ORIGIN = "https://www.linkedin.com"


@pytest.fixture
def stub():
    server = SalesNavStub().start()
    yield server
    assert server.writes == [] and server.lead_pages == []
    server.stop()


@pytest.fixture
async def extractor(stub, monkeypatch) -> AsyncIterator[LinkedInExtractor]:
    monkeypatch.setattr(extractor_module, "_LINKEDIN_ORIGIN", stub.base_url)
    monkeypatch.setattr(extractor_module, "_SALES_NAV_PACE_SECONDS", (0.0, 0.01))
    monkeypatch.setattr(extractor_module, "_SALES_NAV_RESULT_WAIT_MS", 1500)
    monkeypatch.setattr(extractor_module, "_SALES_NAV_SCROLL_PAUSE_SECONDS", 0.3)
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        context = await browser.new_context(viewport={"width": 1280, "height": 800})
        yield LinkedInExtractor(await context.new_page())
        await browser.close()


@pytest.fixture
def db(tmp_path):
    connection = sqlite3.connect(tmp_path / "leads.sqlite3")
    connection.row_factory = sqlite3.Row
    migrate(connection)
    yield connection
    connection.close()


def as_linkedin(result: dict, stub: SalesNavStub) -> dict:
    """The stand-in serves from loopback; real Sales Navigator addresses live on linkedin.com."""
    return json.loads(json.dumps(result).replace(stub.base_url, REAL_ORIGIN))


@pytest.mark.parametrize("layout", ["a", "b"])
async def test_what_the_reader_returns_becomes_scored_deduplicated_leads(
    extractor, stub, db, layout
):
    raw = await extractor.search_sales_navigator_leads(
        url=stub.search_url(layout=layout, total=60), max_pages=1
    )
    result = as_linkedin(raw, stub)
    found = sources.normalize_sales_navigator(result)
    assert len(found) == PAGE_SIZE
    first = make_leads(60)[0]
    assert found[0]["name"] == first["name"] and found[0]["title"] == first["title"]
    assert (
        found[0]["company"] == first["company"]
        and found[0]["location"] == first["location"]
    )
    assert found[0]["sales_nav_url"].startswith(f"{REAL_ORIGIN}/sales/lead/")
    scoring.save_icp(
        db,
        {
            "titles": [first["title"].split()[0]],
            "locations": [first["location"].split(",")[0]],
        },
    )
    list_id = store.default_list(db)["id"]
    saved = service.save_leads(db, found, list_id)
    assert (
        saved["created"] == PAGE_SIZE
        and saved["duplicates"] == 0
        and saved["failed"] == 0
    )
    stored = store.query_leads(db, source="sales_navigator", limit=100)
    assert stored["total"] == PAGE_SIZE and all(
        lead["kind"] == "person" for lead in stored["leads"]
    )
    assert all(
        lead["sales_nav_url"] and lead["score"] is not None for lead in stored["leads"]
    )
    if layout == "b":
        assert stored["leads"][0]["linkedin_url"].startswith(f"{REAL_ORIGIN}/in/")
    again = service.save_leads(db, sources.normalize_sales_navigator(result), list_id)
    assert again["created"] == 0 and again["duplicates"] == PAGE_SIZE, (
        "re-reading the same search adds nothing"
    )


async def test_the_mcp_tool_answer_has_the_shape_leads_expects(
    extractor, stub, db, monkeypatch
):
    from linkedin_mcp_server.tools.sales import register_sales_tools

    mcp = FastMCP("test")
    register_sales_tools(mcp)
    tool_fn = await get_tool_fn(mcp, "search_sales_navigator")

    class Context:
        async def report_progress(self, **_kwargs):
            return None

        async def info(self, *_args, **_kwargs):
            return None

    result = await tool_fn(
        Context(),
        url=stub.search_url(layout="a", total=30),
        max_pages=1,
        extractor=extractor,
    )
    assert {"url", "pages_read", "leads", "warnings", "section_errors"} <= set(result)
    found = sources.normalize_sales_navigator(as_linkedin(result, stub))
    assert len(found) == PAGE_SIZE and all(lead["name"] for lead in found)


async def test_a_gateway_wrapping_the_reader_drives_the_whole_search(
    extractor, stub, db
):
    """service.search -> gateway -> the reader's answer: the path the app takes, with the stand-in as LinkedIn."""
    # The test already runs inside an event loop, so the gateway replays the answer the reader produced here.
    raw = as_linkedin(
        await extractor.search_sales_navigator_leads(
            url=stub.search_url(layout="b", total=30), max_pages=1
        ),
        stub,
    )

    class Replay:
        def linkedin_state(self) -> str:
            return "connected"

        def linkedin_call(self, name, arguments):
            assert name == "search_sales_navigator"
            assert arguments["url"].startswith(f"{REAL_ORIGIN}/sales/search/people")
            return raw

        def sender_name(self) -> str:
            return ""

        def model(self, prompt):
            return None

    previous = service.gateway()
    service.set_gateway(Replay())
    try:
        out = service.search(
            db,
            None,
            {
                "source": "sales_navigator",
                "url": f"{REAL_ORIGIN}/sales/search/people?query=(keywords:cto)",
                "auto_add": True,
            },
        )
    finally:
        service.set_gateway(previous)
    assert (
        out["count"] == PAGE_SIZE
        and out["added"] == PAGE_SIZE
        and out["pages_read"] == 1
    )
    assert store.query_leads(db, source="sales_navigator")["total"] == PAGE_SIZE
