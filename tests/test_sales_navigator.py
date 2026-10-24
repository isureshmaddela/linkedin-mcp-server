"""Sales Navigator lead reading: the real extractor in a real Chromium against a stand-in Sales Navigator.

The stand-in (``tests/linkedin_sales_nav_stub.py``) serves two differently structured, obfuscated-class layouts,
lazily rendered rows, ``page=`` pagination and every failure page. Nothing here talks to linkedin.com, and every
browser test ends by asserting the reader never clicked a write control or opened a lead's profile.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from linkedin_sales_nav_stub import PAGE_SIZE, SalesNavStub, make_leads
from patchright.async_api import async_playwright
from pydantic import ValidationError
from test_tools import get_tool_fn

from linkedin_mcp_server.core.exceptions import (
    AuthenticationError,
    RateLimitError,
    SalesNavigatorAccessError,
)
from linkedin_mcp_server.scraping import extractor as extractor_module
from linkedin_mcp_server.scraping import sales_navigator as sn
from linkedin_mcp_server.scraping.extractor import (
    FilterValidationError,
    LinkedInExtractor,
)

ORIGIN = "https://www.linkedin.com"


# ----------------------------------------------------------------------------- URLs (no browser)


def test_a_people_search_url_keeps_its_filters_byte_for_byte_and_loses_only_the_page():
    url = (
        f"{ORIGIN}/sales/search/people?query=(recentSearchParam:(id:5,doLogHistory:true),"
        "filters:List((type:REGION,values:List((id:103644278,text:United%20States)))),keywords:cto)&page=3&sessionId=abc"
    )
    clean, is_list = sn.validate_url(ORIGIN, url)
    assert not is_list
    assert "page=" not in clean
    assert clean.startswith(
        f"{ORIGIN}/sales/search/people?query=(recentSearchParam:(id:5,doLogHistory:true)"
    )
    assert clean.endswith("keywords:cto)&sessionId=abc")
    assert "id:103644278,text:United%20States" in clean


@pytest.mark.parametrize(
    "url",
    [
        "https://www.linkedin.com/in/someone/",
        "https://www.linkedin.com/search/results/people/?keywords=cto",
        "https://www.linkedin.com/sales/search/company?query=x",
        "https://www.linkedin.com/sales/lead/ACwAAB123,NAME_SEARCH,x",
        "https://evil.example/sales/search/people?query=x",
        "http://www.linkedin.com/sales/search/people?query=x",
        "https://www.linkedin.com.evil.example/sales/search/people?query=x",
        "javascript:alert(1)",
        "",
    ],
)
def test_anything_that_is_not_a_sales_navigator_people_search_or_list_is_refused(url):
    with pytest.raises(ValueError):
        sn.validate_url(ORIGIN, url)


def test_a_lead_list_is_accepted_and_lead_list_mode_is_strict():
    clean, is_list = sn.validate_url(
        ORIGIN, f"{ORIGIN}/sales/lists/people/6812345678?sortCriteria=CREATED_TIME"
    )
    assert is_list and clean.endswith(
        "/sales/lists/people/6812345678?sortCriteria=CREATED_TIME"
    )
    assert sn.validate_url(ORIGIN, f"{ORIGIN}/sales/lists/people/1", lead_list=True)[1]
    with pytest.raises(ValueError, match="lead list"):
        sn.validate_url(ORIGIN, f"{ORIGIN}/sales/search/people?query=x", lead_list=True)
    with pytest.raises(ValueError):
        sn.build_search_url(
            ORIGIN, url=None, keywords="cto", location=None, lead_list=True
        )


def test_linkedin_dot_com_without_www_is_accepted_for_the_real_origin_only():
    assert sn.validate_url(ORIGIN, "https://linkedin.com/sales/search/people?query=x")[
        0
    ].startswith("https://linkedin.com/sales/search/people")
    with pytest.raises(ValueError):
        sn.validate_url(
            "http://127.0.0.1:9", "https://linkedin.com/sales/search/people?query=x"
        )


def test_keywords_and_a_location_build_a_keyword_search_and_say_how():
    url, is_list, warnings = sn.build_search_url(
        ORIGIN, url=None, keywords="cloud architect", location="Austin", lead_list=False
    )
    assert (
        url
        == f"{ORIGIN}/sales/search/people?query=(keywords:cloud%20architect%20Austin)"
    )
    assert not is_list and any(
        "location was added to the keywords" in w for w in warnings
    )
    no_location = sn.build_search_url(
        ORIGIN, url=None, keywords="cto", location=None, lead_list=False
    )
    assert no_location[2] == []
    with pytest.raises(ValueError):
        sn.build_search_url(
            ORIGIN, url=None, keywords="  ", location="", lead_list=False
        )


def test_pages_are_addressed_by_the_page_parameter():
    base = f"{ORIGIN}/sales/search/people?query=(keywords:cto)"
    assert sn.with_page(base, 1) == base
    assert sn.with_page(base, 3) == f"{base}&page=3"
    assert (
        sn.with_page(f"{ORIGIN}/sales/lists/people/9", 2)
        == f"{ORIGIN}/sales/lists/people/9?page=2"
    )
    assert sn.with_page(f"{base}&page=2", 4) == f"{base}&page=4"


# ----------------------------------------------------------------------------- classification (no browser)


def row(**kw):
    base = {
        "key": "K",
        "profile_url": "https://www.linkedin.com/sales/lead/K,NAME_SEARCH,x",
        "name": "Jane Doe",
        "company": "Acme Corp",
        "company_sales_url": "https://www.linkedin.com/sales/company/1",
        "company_url": "",
        "linkedin_url": "",
        "lines": [],
    }
    base.update(kw)
    return base


def test_a_card_is_told_apart_into_title_company_location_degree_and_tenure():
    lead = sn.classify_row(
        row(
            lines=[
                "Jane Doe",
                "· 2nd",
                "VP of Engineering",
                "Acme Corp",
                "Austin, Texas, United States",
                "3 years 2 months in role",
                "5 years in company",
                "Message",
                "Save",
            ]
        )
    )
    assert (lead["name"], lead["first_name"], lead["last_name"]) == (
        "Jane Doe",
        "Jane",
        "Doe",
    )
    assert lead["title"] == "VP of Engineering" and lead["company"] == "Acme Corp"
    assert lead["location"] == "Austin, Texas, United States"
    assert lead["degree"] == "2nd"
    assert (
        lead["tenure_in_role"] == "3 years 2 months"
        and lead["tenure_in_company"] == "5 years"
    )
    assert "Message" in lead["raw_lines"]


def test_a_comma_in_a_title_is_not_mistaken_for_a_location():
    lead = sn.classify_row(
        row(
            lines=[
                "Jane Doe",
                "Director, Data Platform",
                "Greater Seattle Area",
                "1 year in role",
            ]
        )
    )
    assert lead["title"] == "Director, Data Platform"
    assert lead["location"] == "Greater Seattle Area"
    assert lead["tenure_in_role"] == "1 year"


def test_title_at_company_is_split_when_the_company_is_not_otherwise_known():
    lead = sn.classify_row(
        row(
            company="",
            lines=[
                "Jane Doe",
                "Head of Growth at Globex",
                "London, England, United Kingdom",
            ],
        )
    )
    assert lead["title"] == "Head of Growth" and lead["company"] == "Globex"
    known = sn.classify_row(
        row(
            lines=[
                "Jane Doe",
                "Head of Growth at Acme Corp",
                "Acme Corp",
                "Toronto, Ontario, Canada",
            ]
        )
    )
    assert known["title"] == "Head of Growth" and known["company"] == "Acme Corp"


def test_credentials_and_degree_on_the_name_line_do_not_leak_into_the_lead():
    lead = sn.classify_row(
        row(
            name="Jane Doe, MBA",
            lines=["Jane Doe, MBA · 3rd", "Chief Technology Officer", "Acme Corp"],
        )
    )
    assert lead["name"] == "Jane Doe" and (lead["first_name"], lead["last_name"]) == (
        "Jane",
        "Doe",
    )
    assert lead["degree"] == "3rd" and lead["title"] == "Chief Technology Officer"


def test_nothing_is_invented_when_a_card_has_only_a_name():
    lead = sn.classify_row(row(company="", lines=["Jane Doe"]))
    assert lead["title"] == "" and lead["location"] == "" and lead["company"] == ""
    assert (
        lead["degree"] == ""
        and lead["tenure_in_role"] == ""
        and lead["linkedin_url"] == ""
    )


# ----------------------------------------------------------------------------- browser fixtures


@pytest.fixture
def stub():
    server = SalesNavStub().start()
    yield server
    try:
        assert server.writes == [], f"the reader sent a write request: {server.writes}"
        assert server.lead_pages == [], "the reader opened a lead's profile page"
    finally:
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


def expected(total: int, index: int) -> dict[str, str]:
    return make_leads(total)[index]


# ----------------------------------------------------------------------------- reading both layouts


@pytest.mark.parametrize("layout", ["a", "b"])
async def test_a_page_of_leads_is_read_in_full_on_either_layout(
    extractor, stub, layout
):
    result = await extractor.search_sales_navigator_leads(
        url=stub.search_url(layout=layout, total=60), max_pages=1
    )
    leads = result["leads"]
    assert len(leads) == PAGE_SIZE, "all 25 rows, not just the 8 that render first"
    assert (
        result["pages_read"] == 1
        and result["warnings"] == []
        and result["section_errors"] == {}
    )
    first = expected(60, 0)
    lead = leads[0]
    assert lead["name"] == first["name"]
    assert lead["first_name"] == first["name"].split(" ")[0]
    assert lead["title"] == first["title"]
    assert lead["company"] == first["company"]
    assert lead["location"] == first["location"]
    assert lead["degree"] == first["degree"]
    assert lead["tenure_in_role"] == first["role"]
    assert (
        lead["profile_url"]
        == f"{stub.base_url}/sales/lead/{first['key']},NAME_SEARCH,Ab0"
    )
    assert "?" not in lead["profile_url"], "tracking parameters are removed"
    assert (
        lead["company_sales_url"]
        == f"{stub.base_url}/sales/company/{first['company_id']}"
    )
    assert lead["raw_lines"]
    if layout == "b":
        assert lead["linkedin_url"] == f"https://www.linkedin.com/in/{first['slug']}/"
    else:
        assert lead["linkedin_url"] == ""
    # every one of the 25 distinct people came back with a name and a company
    assert len({item["profile_url"] for item in leads}) == PAGE_SIZE
    assert all(item["name"] and item["company"] and item["title"] for item in leads)


async def test_rows_really_are_lazy_so_the_scroll_loop_is_what_loads_them(
    extractor, stub
):
    from linkedin_mcp_server.scraping import sales_navigator

    await extractor._navigate_to_page(stub.search_url(layout="a", total=60))
    await extractor._page.wait_for_selector('a[href*="/sales/lead/"]')
    rows = await extractor._page.evaluate(sales_navigator.ROWS_JS)
    assert 0 < len(rows) < PAGE_SIZE, f"{len(rows)} rows before any scroll"


async def test_a_page_that_draws_its_rows_late_is_waited_for(
    extractor, stub, monkeypatch
):
    monkeypatch.setattr(extractor_module, "_SALES_NAV_RESULT_WAIT_MS", 5000)
    result = await extractor.search_sales_navigator_leads(
        url=stub.search_url(mode="late", total=30), max_pages=1
    )
    assert len(result["leads"]) == PAGE_SIZE


# ----------------------------------------------------------------------------- pages, dedupe, lists


async def test_pages_are_followed_by_the_page_parameter_and_leads_are_unique(
    extractor, stub
):
    result = await extractor.search_sales_navigator_leads(
        url=stub.search_url(layout="a", total=60), max_pages=3
    )
    assert result["pages_read"] == 3
    assert len(result["leads"]) == 60
    assert len({lead["profile_url"] for lead in result["leads"]}) == 60
    hits = [h for h in stub.page_hits() if h.startswith("/sales/search/people")]
    assert any("page=2" in h for h in hits) and any("page=3" in h for h in hits)
    assert not any("page=1" in h for h in hits), "page 1 is the URL as given"
    names = [lead["name"] for lead in result["leads"]]
    assert names[PAGE_SIZE] == expected(60, PAGE_SIZE)["name"], (
        "page 2 continues where page 1 stopped"
    )


async def test_a_search_that_ends_early_stops_without_error(extractor, stub):
    result = await extractor.search_sales_navigator_leads(
        url=stub.search_url(layout="b", total=30), max_pages=5
    )
    assert len(result["leads"]) == 30
    assert result["pages_read"] == 3, "the third page was empty and ended the run"
    assert any("Page 3 had no leads" in w for w in result["warnings"])


async def test_a_page_that_repeats_the_first_adds_nothing_and_stops(extractor, stub):
    result = await extractor.search_sales_navigator_leads(
        url=stub.search_url(mode="repeat", total=60), max_pages=3
    )
    assert len(result["leads"]) == PAGE_SIZE
    assert result["pages_read"] == 2
    assert any("repeated leads already read" in w for w in result["warnings"])


async def test_a_lead_list_is_read_like_a_search(extractor, stub):
    result = await extractor.search_sales_navigator_leads(
        url=stub.list_url(total=30), max_pages=1, lead_list=True
    )
    assert len(result["leads"]) == PAGE_SIZE
    assert result["url"].startswith(f"{stub.base_url}/sales/lists/people/123456")


async def test_keywords_and_a_location_run_a_keyword_search(extractor, stub):
    result = await extractor.search_sales_navigator_leads(
        keywords="cloud architect", location="Austin", max_pages=1
    )
    assert result["url"] == (
        f"{stub.base_url}/sales/search/people?query=(keywords:cloud%20architect%20Austin)"
    )
    assert len(result["leads"]) == PAGE_SIZE
    assert any("location was added to the keywords" in w for w in result["warnings"])


async def test_zero_results_is_a_warning_not_an_error(extractor, stub):
    result = await extractor.search_sales_navigator_leads(
        url=stub.search_url(mode="empty"), max_pages=2
    )
    assert result["leads"] == [] and result["pages_read"] == 1
    assert result["warnings"] == ["The search returned no leads."]
    assert result["section_errors"] == {}


# ----------------------------------------------------------------------------- the ways it fails


async def test_an_account_without_sales_navigator_is_told_so_when_redirected(
    extractor, stub
):
    with pytest.raises(SalesNavigatorAccessError, match="no Sales Navigator access"):
        await extractor.search_sales_navigator_leads(
            url=stub.search_url(mode="redirect")
        )


async def test_an_account_without_sales_navigator_is_told_so_on_an_upsell_page(
    extractor, stub
):
    with pytest.raises(SalesNavigatorAccessError, match="no Sales Navigator access"):
        await extractor.search_sales_navigator_leads(url=stub.search_url(mode="upsell"))


async def test_a_signed_out_session_is_an_authentication_error(extractor, stub):
    with pytest.raises(AuthenticationError):
        await extractor.search_sales_navigator_leads(url=stub.search_url(mode="login"))


async def test_a_security_checkpoint_stops_the_run(extractor, stub):
    with pytest.raises((AuthenticationError, RateLimitError)):
        await extractor.search_sales_navigator_leads(
            url=stub.search_url(mode="checkpoint")
        )


async def test_a_throttled_page_is_a_rate_limit_error(extractor, stub):
    with pytest.raises(RateLimitError):
        await extractor.search_sales_navigator_leads(
            url=stub.search_url(mode="ratelimit")
        )


async def test_hitting_the_login_wall_on_a_later_page_keeps_the_leads_already_read(
    extractor, stub
):
    result = await extractor.search_sales_navigator_leads(
        url=stub.search_url(mode="fail2", total=60), max_pages=3
    )
    assert len(result["leads"]) == PAGE_SIZE and result["pages_read"] == 1
    assert any("Stopped before page 2" in w for w in result["warnings"])
    assert "sales_navigator" in result["section_errors"]


# ----------------------------------------------------------------------------- input validation


async def test_bad_input_is_refused_before_the_browser_is_used(stub, monkeypatch):
    monkeypatch.setattr(extractor_module, "_LINKEDIN_ORIGIN", ORIGIN)
    extractor = LinkedInExtractor(MagicMock())
    for kwargs in (
        {},
        {"url": "https://www.linkedin.com/in/someone/"},
        {
            "url": stub.search_url()
        },  # the stub's address is not linkedin.com unless the origin is overridden
        {"keywords": "cto", "max_pages": 0},
        {"keywords": "cto", "max_pages": 6},
        {"url": f"{ORIGIN}/sales/search/people?query=x", "lead_list": True},
    ):
        with pytest.raises(FilterValidationError):
            await extractor.search_sales_navigator_leads(**kwargs)


# ----------------------------------------------------------------------------- the MCP tool


def _tool_extractor(result=None, error=None) -> MagicMock:
    mock = MagicMock()
    mock.search_sales_navigator_leads = AsyncMock(
        return_value=result, side_effect=error
    )
    return mock


async def test_the_tool_passes_its_arguments_through_and_returns_the_leads(
    mock_context,
):
    from linkedin_mcp_server.tools.sales import register_sales_tools

    payload = {
        "url": "u",
        "pages_read": 1,
        "leads": [{"name": "Jane Doe"}],
        "warnings": [],
        "section_errors": {},
    }
    extractor = _tool_extractor(payload)
    mcp = FastMCP("test")
    register_sales_tools(mcp)
    tool_fn = await get_tool_fn(mcp, "search_sales_navigator")
    result = await tool_fn(
        mock_context,
        url="https://www.linkedin.com/sales/search/people?query=x",
        max_pages=3,
        extractor=extractor,
    )
    assert result == payload
    extractor.search_sales_navigator_leads.assert_awaited_once_with(
        url="https://www.linkedin.com/sales/search/people?query=x",
        keywords=None,
        location=None,
        max_pages=3,
    )


async def test_the_tool_is_read_only_and_has_the_documented_arguments():
    from linkedin_mcp_server.tools.sales import register_sales_tools

    mcp = FastMCP("test")
    register_sales_tools(mcp, tool_timeout=7.5)
    tool = await mcp.get_tool("search_sales_navigator")
    assert tool is not None and tool.timeout == 7.5
    assert tool.annotations is not None and tool.annotations.readOnlyHint is True
    assert set(tool.parameters["properties"]) == {
        "url",
        "keywords",
        "location",
        "max_pages",
    }
    for bad in (0, 6):
        with pytest.raises(ValidationError, match="max_pages"):
            await mcp.call_tool(
                "search_sales_navigator", {"keywords": "cto", "max_pages": bad}
            )


async def test_validation_and_access_errors_reach_the_client_with_their_message(
    mock_context,
):
    from linkedin_mcp_server.tools.sales import register_sales_tools

    mcp = FastMCP("test")
    register_sales_tools(mcp)
    tool_fn = await get_tool_fn(mcp, "search_sales_navigator")
    with pytest.raises(ToolError, match="must be a Sales Navigator"):
        await tool_fn(
            mock_context,
            url="https://example.com",
            extractor=_tool_extractor(
                error=FilterValidationError("url must be a Sales Navigator address")
            ),
        )
    with pytest.raises(ToolError, match="no Sales Navigator access"):
        await tool_fn(
            mock_context,
            keywords="cto",
            extractor=_tool_extractor(
                error=SalesNavigatorAccessError(
                    "This LinkedIn account has no Sales Navigator access."
                )
            ),
        )
    with pytest.raises(ToolError, match="Rate limit"):
        await tool_fn(
            mock_context,
            keywords="cto",
            extractor=_tool_extractor(error=RateLimitError("slow down")),
        )
