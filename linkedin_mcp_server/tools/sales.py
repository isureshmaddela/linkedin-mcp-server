"""
LinkedIn Sales Navigator tools: read leads from the person's own Sales Navigator.

Read only. The tool navigates, scrolls and reads a Sales Navigator people search (or lead list) in the
signed-in browser session; it never connects, messages, saves or likes (see scraping/sales_navigator.py).
"""

import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

from linkedin_mcp_server.config.schema import DEFAULT_TOOL_TIMEOUT_SECONDS
from linkedin_mcp_server.core.exceptions import AuthenticationError
from linkedin_mcp_server.dependencies import get_ready_extractor, handle_auth_error
from linkedin_mcp_server.error_handler import raise_tool_error
from linkedin_mcp_server.scraping.extractor import FilterValidationError

logger = logging.getLogger(__name__)


def register_sales_tools(
    mcp: FastMCP, *, tool_timeout: float = DEFAULT_TOOL_TIMEOUT_SECONDS
) -> None:
    """Register the Sales Navigator tools with the MCP server."""

    @mcp.tool(
        timeout=tool_timeout,
        title="Search Sales Navigator Leads",
        annotations={"readOnlyHint": True, "openWorldHint": True},
        tags={"sales", "search"},
        exclude_args=["extractor"],
    )
    async def search_sales_navigator(
        ctx: Context,
        url: str | None = None,
        keywords: str | None = None,
        location: str | None = None,
        max_pages: Annotated[int, Field(ge=1, le=5)] = 2,
        extractor: Any | None = None,
    ) -> dict[str, Any]:
        """
        Read leads from the signed-in account's own LinkedIn Sales Navigator (read only).

        Needs a LinkedIn account with a Sales Navigator subscription. The reliable input is url: a Sales
        Navigator people search (https://www.linkedin.com/sales/search/people?...) or a people lead list
        (https://www.linkedin.com/sales/lists/people/<id>) built in the product with its filters. keywords
        (with an optional location, added to the keywords) runs a plain keyword search instead. Nothing is
        ever clicked: no connect, message, InMail, save or like.

        Args:
            ctx: FastMCP context for progress reporting
            url: Sales Navigator people search or people lead list address
            keywords: Plain keyword search when no url is given
            location: Added to the keywords (Sales Navigator filters locations by id)
            max_pages: Result pages to read (1-5, default 2); about 25 leads per page

        Returns:
            Dict with url, pages_read, leads, warnings and section_errors. Each lead has name, first_name,
            last_name, title, company, company_url, company_sales_url, location, profile_url (the
            /sales/lead/ address), linkedin_url (public profile when shown), degree, tenure_in_role,
            tenure_in_company and raw_lines.
        """
        try:
            extractor = extractor or await get_ready_extractor(
                ctx, tool_name="search_sales_navigator"
            )
            logger.info(
                "Searching Sales Navigator: url=%s keywords='%s', location='%s', max_pages=%d",
                "yes" if url else "no",
                keywords,
                location,
                max_pages,
            )

            await ctx.report_progress(
                progress=0, total=100, message="Starting Sales Navigator search"
            )

            try:
                result = await extractor.search_sales_navigator_leads(
                    url=url,
                    keywords=keywords,
                    location=location,
                    max_pages=max_pages,
                )
            except FilterValidationError as e:
                # The message says what to fix; do not let mask_error_details reduce it.
                raise ToolError(str(e)) from e

            await ctx.report_progress(progress=100, total=100, message="Complete")

            return result

        except ToolError:
            raise
        except AuthenticationError as e:
            try:
                await handle_auth_error(e, ctx)
            except Exception as relogin_exc:
                raise_tool_error(relogin_exc, "search_sales_navigator")
        except Exception as e:
            raise_tool_error(e, "search_sales_navigator")  # NoReturn
