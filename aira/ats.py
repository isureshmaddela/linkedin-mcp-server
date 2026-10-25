"""Applicant-tracking-system (ATS) job sources for the Career Copilot.

This module adds read-only adapters for the public, documented posting APIs of
Lever, Ashby and SmartRecruiters, a catalog describing every job source the
copilot knows about (available or planned), a factory that builds the right
adapter for a source id, careers-URL detection for company portals, a
single-posting importer, and a Greenhouse application-question reader. It
reuses the provider-neutral contracts from ``linkedin_mcp_server.job_sources``
and never imports the HTTP server.

All network access goes through one private helper, :func:`_get_json`. Every
adapter accepts an injectable ``json_getter`` with the same ``(url) -> Any``
shape so tests never touch the network.
"""

from __future__ import annotations

import json
import math
import re
import time
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.error import HTTPError
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlsplit
from urllib.request import Request, urlopen

from linkedin_mcp_server.job_sources import (
    GreenhouseJobAdapter,
    JobPosting,
    JobSearchQuery,
    JobSearchResult,
    JobSourceCapabilities,
    canonicalize_job_url,
    html_to_text,
    parse_timestamp,
    validate_http_url,
)

__all__ = [
    "AshbyJobAdapter",
    "DETAIL_FETCH_LIMIT",
    "LeverJobAdapter",
    "PortalReference",
    "SOURCE_CATALOG",
    "SmartRecruitersJobAdapter",
    "SourceDescriptor",
    "build_adapter",
    "catalog_as_dicts",
    "detect_portal",
    "fetch_greenhouse_questions",
    "find_source",
    "import_job_from_url",
]

JsonGetter = Callable[[str], Any]
Sleeper = Callable[[float], None]
Opener = Callable[..., Any]

#: SmartRecruiters detail endpoints fetched per search (descriptions for the rest stay empty).
DETAIL_FETCH_LIMIT = 25

_MAX_ATTEMPTS = 3
_RETRY_DELAYS = (0.5, 1.0)
_TITLE_LIMIT = 500
_COMPANY_LIMIT = 300
_LOCATION_LIMIT = 300
_DESCRIPTION_LIMIT = 50_000
_ID_LIMIT = 200
_ALLOWED_FILTERS = frozenset({"work_type", "job_type"})
_POSTING_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,100}$")
_GREENHOUSE_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)
_HTTP_STATUS_HINTS = {
    400: "bad request",
    401: "authentication required",
    403: "access denied",
    404: "not found",
    429: "rate limited",
}
_EMPLOYMENT_TYPES = {
    "fulltime": "full_time",
    "parttime": "part_time",
    "contract": "contract",
    "contractor": "contract",
    "internship": "internship",
    "intern": "internship",
    "temporary": "temporary",
    "temp": "temporary",
}
_WORKPLACE_TYPES = {
    "remote": "remote",
    "hybrid": "hybrid",
    "onsite": "on_site",
    "inoffice": "on_site",
}
_ATS_CAPABILITIES = JobSourceCapabilities(
    search=True,
    import_jobs=True,
    normalize=True,
    resolve_application_link=True,
    report_health=True,
)


# --------------------------------------------------------------------------- #
# HTTP transport
# --------------------------------------------------------------------------- #


def _describe_http_error(host: str, code: int) -> str:
    hint = _HTTP_STATUS_HINTS.get(code) or ("server error" if code >= 500 else "")
    suffix = f" ({hint})" if hint else ""
    return f"{host} returned HTTP {code}{suffix}."


def _get_json(
    url: str,
    timeout: float = 20,
    *,
    sleeper: Sleeper | None = None,
    opener: Opener | None = None,
) -> Any:
    """Fetch ``url`` with ``urllib`` and decode its JSON body.

    HTTP 429/5xx responses and network errors are retried with exponential
    backoff (0.5s, then 1s) for at most three attempts; other HTTP errors and
    invalid JSON fail immediately. Every failure becomes a ``RuntimeError``
    whose message names only the host, never the URL query (which may carry
    keys). ``sleeper`` and ``opener`` exist so tests can run without waiting
    or touching the network.
    """
    validate_http_url(url, "Job source URL")
    host = (urlsplit(url).hostname or "the job source").lower()
    sleep = sleeper or time.sleep
    open_url = opener or urlopen
    request = Request(
        url, headers={"Accept": "application/json", "User-Agent": "Aira/1.0"}
    )
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            with open_url(request, timeout=timeout) as response:
                body = response.read()
        except HTTPError as error:
            message = _describe_http_error(host, error.code)
            retryable = error.code == 429 or error.code >= 500
            if not retryable or attempt == _MAX_ATTEMPTS:
                raise RuntimeError(message) from error
        except OSError as error:  # URLError and socket timeouts are OSError subclasses.
            timed_out = isinstance(error, TimeoutError) or isinstance(
                getattr(error, "reason", None), TimeoutError
            )
            message = (
                f"Request to {host} timed out."
                if timed_out
                else f"Could not reach {host}."
            )
            if attempt == _MAX_ATTEMPTS:
                raise RuntimeError(message) from error
        else:
            try:
                return json.loads(body)
            except (TypeError, ValueError) as error:
                raise RuntimeError(
                    f"{host} returned a response that is not valid JSON."
                ) from error
        sleep(_RETRY_DELAYS[min(attempt, len(_RETRY_DELAYS)) - 1])
    raise RuntimeError(f"Could not reach {host}.")


# --------------------------------------------------------------------------- #
# Shared normalization helpers
# --------------------------------------------------------------------------- #


def _as_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clean_text(value: Any, limit: int) -> str:
    """Collapse whitespace in a scalar value and bound its length."""
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()[:limit]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        key = value.casefold()
        if value and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _optional_url(value: Any) -> str | None:
    """Return ``value`` when it is an absolute HTTP(S) URL, otherwise ``None``."""
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        validate_http_url(candidate, "URL")
    except ValueError:
        return None
    return candidate[:2000]


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
    elif isinstance(value, str):
        try:
            number = float(value.replace(",", "").strip())
        except ValueError:
            return None
    else:
        return None
    if not math.isfinite(number) or number < 0:
        return None
    return number


def _salary_bounds(minimum: Any, maximum: Any) -> tuple[float | None, float | None]:
    """Parse a salary range, swapping reversed bounds instead of rejecting them."""
    low, high = _number(minimum), _number(maximum)
    if low is not None and high is not None and low > high:
        low, high = high, low
    return low, high


def _currency(value: Any) -> str | None:
    code = str(value or "").strip().upper()
    return code if re.fullmatch(r"[A-Z]{3}", code) else None


def _ms_timestamp(value: Any) -> datetime | None:
    """Convert a millisecond epoch (Lever ``createdAt``) to an aware datetime."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value <= 0:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000, tz=timezone.utc)
    except (OverflowError, OSError, ValueError):
        return None


def _token_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def _employment_type(value: Any) -> str | None:
    """Map labels such as ``Full-time``/``FullTime``/``Intern`` to the shared vocabulary."""
    return _EMPLOYMENT_TYPES.get(_token_key(value))


def _workplace_type(value: Any) -> str | None:
    """Map ``remote``/``hybrid``/``onsite``/``on-site`` to the shared vocabulary."""
    return _WORKPLACE_TYPES.get(_token_key(value))


def _seniority(value: Any) -> str | None:
    label = _clean_text(value, 100).lower()
    return label or None


def _require_posting_id(value: str, provider: str) -> str:
    posting_id = str(value or "").strip()
    if not _POSTING_ID_RE.fullmatch(posting_id):
        raise ValueError(f"Enter a valid {provider} posting ID.")
    return posting_id


def _validated_filters(query: JobSearchQuery, provider: str) -> tuple[str, str]:
    """Reject unknown filter keys and normalize ``work_type``/``job_type`` values."""
    unsupported = set(query.filters) - _ALLOWED_FILTERS
    if unsupported:
        names = ", ".join(sorted(str(name) for name in unsupported))
        raise ValueError(f"Unsupported {provider} job filters: {names}.")
    raw_work_type = str(query.filters.get("work_type") or "").strip()
    raw_job_type = str(query.filters.get("job_type") or "").strip()
    work_type = (
        (_workplace_type(raw_work_type) or raw_work_type.casefold())
        if raw_work_type
        else ""
    )
    job_type = (
        (_employment_type(raw_job_type) or raw_job_type.casefold())
        if raw_job_type
        else ""
    )
    return work_type, job_type


def _matches_locally(
    query: JobSearchQuery,
    job: JobPosting,
    work_type: str,
    job_type: str,
    *,
    keyword_text: str = "",
    location_text: str = "",
    apply_keywords: bool = True,
) -> bool:
    """Apply keyword, location and filter matching on normalized fields."""
    if apply_keywords:
        haystack = " ".join((job.title, job.description, keyword_text)).casefold()
        if any(token not in haystack for token in query.keywords.casefold().split()):
            return False
    if query.location:
        location_haystack = (location_text or job.location).casefold()
        if query.location.casefold() not in location_haystack:
            return False
    if work_type and job.workplace_type != work_type:
        return False
    if job_type and job.employment_type != job_type:
        return False
    return True


def _health_report(source: str, probe: Callable[[], str]) -> dict[str, Any]:
    """Run one lightweight probe and report without ever raising."""
    checked_at = datetime.now(timezone.utc).isoformat()
    try:
        message = probe()
    except Exception as error:
        text = str(error).strip() or error.__class__.__name__
        return {
            "source": source,
            "ok": False,
            "message": text[:500],
            "checked_at": checked_at,
        }
    return {"source": source, "ok": True, "message": message, "checked_at": checked_at}


# --------------------------------------------------------------------------- #
# Lever
# --------------------------------------------------------------------------- #


class LeverJobAdapter:
    """Read-only adapter for Lever's public Postings API.

    Lever's API returns postings for a *site* (the first path segment of a
    ``jobs.lever.co`` URL) and does not include a company name, so ``company``
    is set to the site identifier. Keyword and location matching happens
    locally over the full postings list; filters ``work_type`` and ``job_type``
    are applied to the normalized fields. The description is
    ``descriptionPlain`` (or ``description`` HTML converted to text) followed by
    each ``lists`` section rendered as ``## heading`` plus text, and finally
    ``additionalPlain``/``additional`` (closing text such as EEO statements).
    """

    source_name = "lever"
    _site_re = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

    def __init__(self, site: str, json_getter: JsonGetter | None = None) -> None:
        site = site.strip()
        if not self._site_re.fullmatch(site):
            raise ValueError("Enter a valid Lever site name.")
        self.site = site
        self._json_getter: JsonGetter = json_getter or _get_json

    @property
    def capabilities(self) -> JobSourceCapabilities:
        return _ATS_CAPABILITIES

    def _postings_url(self, limit: int | None = None) -> str:
        suffix = f"&limit={limit}" if limit is not None else ""
        return (
            "https://api.lever.co/v0/postings/"
            f"{quote(self.site, safe='')}?mode=json{suffix}"
        )

    def _posting_url(self, posting_id: str) -> str:
        return (
            "https://api.lever.co/v0/postings/"
            f"{quote(self.site, safe='')}/{quote(posting_id, safe='')}?mode=json"
        )

    @staticmethod
    def _location_values(record: Mapping[str, Any]) -> list[str]:
        categories = _as_mapping(record.get("categories"))
        values = [_clean_text(categories.get("location"), _LOCATION_LIMIT)]
        values.extend(
            _clean_text(item, _LOCATION_LIMIT)
            for item in _as_list(categories.get("allLocations"))
        )
        values.append(_clean_text(record.get("country"), 100))
        return _unique(values)

    @staticmethod
    def _description(record: Mapping[str, Any]) -> str:
        plain = str(record.get("descriptionPlain") or "").strip()
        if not plain:
            plain = html_to_text(str(record.get("description") or ""))
        parts = [plain]
        for item in _as_list(record.get("lists")):
            if not isinstance(item, Mapping):
                continue
            heading = _clean_text(item.get("text"), 200)
            body = html_to_text(str(item.get("content") or ""))
            if heading or body:
                parts.append(f"## {heading}\n{body}".strip())
        additional = str(record.get("additionalPlain") or "").strip()
        if not additional:
            additional = html_to_text(str(record.get("additional") or ""))
        parts.append(additional)
        return "\n\n".join(part for part in parts if part)[:_DESCRIPTION_LIMIT]

    def normalize(self, record: Mapping[str, Any]) -> JobPosting:
        posting_id = _clean_text(record.get("id"), _ID_LIMIT)
        if not posting_id:
            raise ValueError("Lever posting has no ID.")
        hosted_url = str(record.get("hostedUrl") or "").strip() or (
            f"https://jobs.lever.co/{quote(self.site, safe='')}/{quote(posting_id, safe='')}"
        )
        validate_http_url(hosted_url, "Lever posting URL")
        categories = _as_mapping(record.get("categories"))
        locations = self._location_values(record)
        primary_location = _clean_text(categories.get("location"), _LOCATION_LIMIT)
        location = primary_location or "; ".join(locations)[:_LOCATION_LIMIT]
        salary = _as_mapping(record.get("salaryRange"))
        salary_min, salary_max = _salary_bounds(salary.get("min"), salary.get("max"))
        return JobPosting(
            source=self.source_name,
            external_id=posting_id,
            source_url=hosted_url,
            application_url=_optional_url(record.get("applyUrl")) or hosted_url,
            title=_clean_text(record.get("text"), _TITLE_LIMIT),
            company=self.site[:_COMPANY_LIMIT],
            location=location,
            workplace_type=_workplace_type(record.get("workplaceType")),
            employment_type=_employment_type(categories.get("commitment")),
            salary_min=salary_min,
            salary_max=salary_max,
            currency=_currency(salary.get("currency")),
            description=self._description(record),
            posted_at=_ms_timestamp(record.get("createdAt")),
            application_method="lever_postings_api",
        )

    def _load_postings(self, url: str) -> list[Any]:
        payload = self._json_getter(url)
        if not isinstance(payload, list):
            raise RuntimeError("Lever returned an invalid postings list.")
        return payload

    def search(self, query: JobSearchQuery) -> JobSearchResult:
        work_type, job_type = _validated_filters(query, "Lever")
        jobs: list[JobPosting] = []
        seen: set[str] = set()
        for record in self._load_postings(self._postings_url()):
            if not isinstance(record, Mapping):
                continue
            try:
                job = self.normalize(record)
            except ValueError:
                continue
            if job.external_id in seen:
                continue
            categories = _as_mapping(record.get("categories"))
            keyword_text = " ".join(
                _clean_text(categories.get(key), 200) for key in ("team", "department")
            )
            if not _matches_locally(
                query,
                job,
                work_type,
                job_type,
                keyword_text=keyword_text,
                location_text=" | ".join(self._location_values(record)),
            ):
                continue
            jobs.append(job)
            seen.add(job.external_id)
        return JobSearchResult(tuple(jobs))

    def fetch_job(self, posting_id: str) -> JobPosting:
        """Fetch one posting by its Lever ID (the UUID in the hosted URL)."""
        posting_id = _require_posting_id(posting_id, "Lever")
        payload = self._json_getter(self._posting_url(posting_id))
        if isinstance(payload, list):
            payload = next(
                (item for item in payload if isinstance(item, Mapping)), None
            )
        if not isinstance(payload, Mapping):
            raise RuntimeError("Lever returned an invalid posting response.")
        return self.normalize(payload)

    def health(self) -> dict[str, Any]:
        def probe() -> str:
            self._load_postings(self._postings_url(limit=1))
            return f"Lever postings API responded for site '{self.site}'."

        return _health_report(self.source_name, probe)


# --------------------------------------------------------------------------- #
# Ashby
# --------------------------------------------------------------------------- #


class AshbyJobAdapter:
    """Read-only adapter for Ashby's public Job Board API.

    The API returns the whole board in one response, so searching, importing a
    single job and health checks all use the same endpoint. Ashby does not
    return a company name, so ``company`` is the job board name. Unlisted jobs
    (``isListed`` false) are skipped by ``search`` but still importable through
    ``fetch_job`` because a candidate with a direct link may apply to them.
    Only the ``Salary`` compensation component is mapped to the salary fields.
    """

    source_name = "ashby"
    _name_re = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")

    def __init__(
        self, job_board_name: str, json_getter: JsonGetter | None = None
    ) -> None:
        job_board_name = job_board_name.strip()
        if not self._name_re.fullmatch(job_board_name):
            raise ValueError("Enter a valid Ashby job board name.")
        self.job_board_name = job_board_name
        self._json_getter: JsonGetter = json_getter or _get_json

    @property
    def capabilities(self) -> JobSourceCapabilities:
        return _ATS_CAPABILITIES

    def _board_url(self) -> str:
        return (
            "https://api.ashbyhq.com/posting-api/job-board/"
            f"{quote(self.job_board_name, safe='')}?includeCompensation=true"
        )

    @staticmethod
    def _postal_address(value: Any) -> str:
        postal = _as_mapping(_as_mapping(value).get("postalAddress"))
        parts = [
            _clean_text(postal.get(key), 100)
            for key in ("addressLocality", "addressRegion", "addressCountry")
        ]
        return ", ".join(part for part in parts if part)

    @classmethod
    def _location_values(cls, record: Mapping[str, Any]) -> list[str]:
        values = [_clean_text(record.get("location"), _LOCATION_LIMIT)]
        for item in _as_list(record.get("secondaryLocations")):
            if isinstance(item, Mapping):
                values.append(_clean_text(item.get("location"), _LOCATION_LIMIT))
                values.append(cls._postal_address(item.get("address")))
        values.append(cls._postal_address(record.get("address")))
        return _unique(values)

    @staticmethod
    def _salary(
        record: Mapping[str, Any],
    ) -> tuple[float | None, float | None, str | None]:
        compensation = _as_mapping(record.get("compensation"))
        for component in _as_list(compensation.get("summaryComponents")):
            if not isinstance(component, Mapping):
                continue
            if _token_key(component.get("compensationType")) != "salary":
                continue
            low, high = _salary_bounds(
                component.get("minValue"), component.get("maxValue")
            )
            return low, high, _currency(component.get("currencyCode"))
        return None, None, None

    def normalize(self, record: Mapping[str, Any]) -> JobPosting:
        job_id = _clean_text(record.get("id"), _ID_LIMIT)
        if not job_id:
            raise ValueError("Ashby job has no ID.")
        job_url = str(record.get("jobUrl") or "").strip() or (
            "https://jobs.ashbyhq.com/"
            f"{quote(self.job_board_name, safe='')}/{quote(job_id, safe='')}"
        )
        validate_http_url(job_url, "Ashby posting URL")
        description = str(record.get("descriptionPlain") or "").strip()
        if not description:
            description = html_to_text(str(record.get("descriptionHtml") or ""))
        location = (
            _clean_text(record.get("location"), _LOCATION_LIMIT)
            or (self._postal_address(record.get("address"))[:_LOCATION_LIMIT])
        )
        salary_min, salary_max, currency = self._salary(record)
        return JobPosting(
            source=self.source_name,
            external_id=job_id,
            source_url=job_url,
            application_url=_optional_url(record.get("applyUrl")) or job_url,
            title=_clean_text(record.get("title"), _TITLE_LIMIT),
            company=self.job_board_name[:_COMPANY_LIMIT],
            location=location,
            workplace_type="remote" if record.get("isRemote") is True else None,
            employment_type=_employment_type(record.get("employmentType")),
            salary_min=salary_min,
            salary_max=salary_max,
            currency=currency,
            description=description[:_DESCRIPTION_LIMIT],
            posted_at=parse_timestamp(record.get("publishedAt")),
            application_method="ashby_job_board",
        )

    def _load_jobs(self) -> list[Any]:
        payload = self._json_getter(self._board_url())
        if not isinstance(payload, Mapping):
            raise RuntimeError("Ashby returned an invalid job board response.")
        jobs = payload.get("jobs")
        if not isinstance(jobs, list):
            raise RuntimeError("Ashby returned an invalid jobs list.")
        return jobs

    def search(self, query: JobSearchQuery) -> JobSearchResult:
        work_type, job_type = _validated_filters(query, "Ashby")
        jobs: list[JobPosting] = []
        seen: set[str] = set()
        for record in self._load_jobs():
            if not isinstance(record, Mapping) or record.get("isListed") is False:
                continue
            try:
                job = self.normalize(record)
            except ValueError:
                continue
            if job.external_id in seen:
                continue
            keyword_text = " ".join(
                _clean_text(record.get(key), 200) for key in ("department", "team")
            )
            if not _matches_locally(
                query,
                job,
                work_type,
                job_type,
                keyword_text=keyword_text,
                location_text=" | ".join(self._location_values(record)),
            ):
                continue
            jobs.append(job)
            seen.add(job.external_id)
        return JobSearchResult(tuple(jobs))

    def fetch_job(self, job_id: str) -> JobPosting:
        """Load the board and return the job with ``job_id`` (listed or not)."""
        job_id = _require_posting_id(job_id, "Ashby")
        for record in self._load_jobs():
            if (
                isinstance(record, Mapping)
                and _clean_text(record.get("id"), _ID_LIMIT) == job_id
            ):
                return self.normalize(record)
        raise RuntimeError(
            f"Posting {job_id} is not on the Ashby job board '{self.job_board_name}'; "
            "it may have closed."
        )

    def health(self) -> dict[str, Any]:
        def probe() -> str:
            listed = sum(
                1
                for record in self._load_jobs()
                if isinstance(record, Mapping) and record.get("isListed") is not False
            )
            return (
                f"Ashby job board '{self.job_board_name}' responded with "
                f"{listed} listed job(s)."
            )

        return _health_report(self.source_name, probe)


# --------------------------------------------------------------------------- #
# SmartRecruiters
# --------------------------------------------------------------------------- #


class SmartRecruitersJobAdapter:
    """Read-only adapter for the SmartRecruiters public Posting API.

    Keyword matching is delegated to the API's ``q`` parameter because the
    list endpoint carries no description text; location and the
    ``work_type``/``job_type`` filters are applied locally. Pages of 100
    postings are read through ``offset`` for up to ``query.max_pages`` pages,
    then the detail endpoint (which adds the job ad sections and the apply
    link) is fetched sequentially for at most ``DETAIL_FETCH_LIMIT`` postings.
    Detail fetching stops after ``DETAIL_FAILURE_LIMIT`` failures; postings
    without details keep an empty description and are reported in
    ``section_errors["details"]``. The list ``ref`` field is the JSON API link,
    so the application link uses ``applyUrl``/``postingUrl`` from the detail
    response and falls back to the public posting page.
    """

    source_name = "smartrecruiters"
    PAGE_SIZE = 100
    DETAIL_FETCH_LIMIT = DETAIL_FETCH_LIMIT
    DETAIL_FAILURE_LIMIT = 3
    _identifier_re = re.compile(r"^[A-Za-z0-9_-]{1,100}$")

    def __init__(
        self, company_identifier: str, json_getter: JsonGetter | None = None
    ) -> None:
        company_identifier = company_identifier.strip()
        if not self._identifier_re.fullmatch(company_identifier):
            raise ValueError("Enter a valid SmartRecruiters company identifier.")
        self.company_identifier = company_identifier
        self._json_getter: JsonGetter = json_getter or _get_json

    @property
    def capabilities(self) -> JobSourceCapabilities:
        return _ATS_CAPABILITIES

    def _base_url(self) -> str:
        return (
            "https://api.smartrecruiters.com/v1/companies/"
            f"{quote(self.company_identifier, safe='')}/postings"
        )

    def _postings_url(self, keywords: str, offset: int, limit: int) -> str:
        parameters: dict[str, str | int] = {}
        if keywords:
            parameters["q"] = keywords
        parameters["limit"] = limit
        parameters["offset"] = offset
        return f"{self._base_url()}?{urlencode(parameters)}"

    def _posting_url(self, posting_id: str) -> str:
        return f"{self._base_url()}/{quote(posting_id, safe='')}"

    def _public_url(self, posting_id: str) -> str:
        return (
            "https://jobs.smartrecruiters.com/"
            f"{quote(self.company_identifier, safe='')}/{quote(posting_id, safe='')}"
        )

    @staticmethod
    def _location_text(record: Mapping[str, Any]) -> str:
        location = _as_mapping(record.get("location"))
        full = _clean_text(location.get("fullLocation"), _LOCATION_LIMIT)
        if full:
            return full
        country = _clean_text(location.get("country"), 100)
        if len(country) == 2:
            country = country.upper()
        parts = [
            _clean_text(location.get("city"), 100),
            _clean_text(location.get("region"), 100),
            country,
        ]
        return ", ".join(part for part in parts if part)[:_LOCATION_LIMIT]

    @staticmethod
    def _description(record: Mapping[str, Any]) -> str:
        sections = _as_mapping(_as_mapping(record.get("jobAd")).get("sections"))
        parts: list[str] = []
        for key in (
            "companyDescription",
            "jobDescription",
            "qualifications",
            "additionalInformation",
        ):
            section = _as_mapping(sections.get(key))
            heading = _clean_text(section.get("title"), 200)
            body = html_to_text(str(section.get("text") or ""))
            if body:
                parts.append(f"## {heading}\n{body}" if heading else body)
        return "\n\n".join(parts)[:_DESCRIPTION_LIMIT]

    def normalize(self, record: Mapping[str, Any]) -> JobPosting:
        posting_id = _clean_text(record.get("id"), _ID_LIMIT)
        if not posting_id:
            raise ValueError("SmartRecruiters posting has no ID.")
        company = _as_mapping(record.get("company"))
        location = _as_mapping(record.get("location"))
        source_url = self._public_url(posting_id)
        application_url = (
            _optional_url(record.get("applyUrl"))
            or _optional_url(record.get("postingUrl"))
            or source_url
        )
        return JobPosting(
            source=self.source_name,
            external_id=posting_id,
            source_url=source_url,
            application_url=application_url,
            title=_clean_text(record.get("name"), _TITLE_LIMIT),
            company=_clean_text(company.get("name"), _COMPANY_LIMIT)
            or self.company_identifier[:_COMPANY_LIMIT],
            location=self._location_text(record),
            workplace_type="remote" if location.get("remote") is True else None,
            employment_type=_employment_type(
                _as_mapping(record.get("typeOfEmployment")).get("label")
            ),
            description=self._description(record),
            seniority=_seniority(
                _as_mapping(record.get("experienceLevel")).get("label")
            ),
            posted_at=parse_timestamp(record.get("releasedDate")),
            application_method="smartrecruiters_posting_api",
        )

    def _load_page(self, url: str) -> Mapping[str, Any]:
        payload = self._json_getter(url)
        if not isinstance(payload, Mapping):
            raise RuntimeError("SmartRecruiters returned an invalid postings response.")
        if not isinstance(payload.get("content"), list):
            raise RuntimeError("SmartRecruiters returned an invalid postings list.")
        return payload

    def search(self, query: JobSearchQuery) -> JobSearchResult:
        work_type, job_type = _validated_filters(query, "SmartRecruiters")
        selected: list[tuple[Mapping[str, Any], JobPosting]] = []
        seen: set[str] = set()
        offset = 0
        for _page in range(query.max_pages):
            payload = self._load_page(
                self._postings_url(query.keywords, offset, self.PAGE_SIZE)
            )
            content = _as_list(payload.get("content"))
            for record in content:
                if not isinstance(record, Mapping):
                    continue
                try:
                    job = self.normalize(record)
                except ValueError:
                    continue
                if job.external_id in seen:
                    continue
                seen.add(job.external_id)
                if _matches_locally(
                    query, job, work_type, job_type, apply_keywords=False
                ):
                    selected.append((record, job))
            offset += len(content)
            total_found = payload.get("totalFound")
            if len(content) < self.PAGE_SIZE:
                break
            if (
                isinstance(total_found, int)
                and not isinstance(total_found, bool)
                and offset >= total_found
            ):
                break

        jobs: list[JobPosting] = []
        failures: list[dict[str, str]] = []
        fetched = 0
        for index, (record, job) in enumerate(selected):
            if (
                index < self.DETAIL_FETCH_LIMIT
                and len(failures) < self.DETAIL_FAILURE_LIMIT
            ):
                try:
                    detail = self._json_getter(self._posting_url(job.external_id))
                    if not isinstance(detail, Mapping):
                        raise RuntimeError(
                            "SmartRecruiters returned an invalid posting detail."
                        )
                    job = self.normalize({**record, **detail})
                    fetched += 1
                except (RuntimeError, ValueError) as error:
                    failures.append(
                        {"id": job.external_id, "message": str(error)[:500]}
                    )
            jobs.append(job)
        section_errors: dict[str, Any] = {}
        if failures or fetched < len(jobs):
            section_errors["details"] = {
                "error_type": "partial",
                "fetched": fetched,
                "skipped": len(jobs) - fetched,
                "failures": failures,
            }
        return JobSearchResult(tuple(jobs), section_errors)

    def fetch_job(self, posting_id: str) -> JobPosting:
        """Fetch one posting (with job ad sections) by its SmartRecruiters ID."""
        posting_id = _require_posting_id(posting_id, "SmartRecruiters")
        payload = self._json_getter(self._posting_url(posting_id))
        if not isinstance(payload, Mapping):
            raise RuntimeError("SmartRecruiters returned an invalid posting response.")
        return self.normalize(payload)

    def health(self) -> dict[str, Any]:
        def probe() -> str:
            payload = self._load_page(self._postings_url("", 0, 1))
            total = payload.get("totalFound")
            count = (
                total if isinstance(total, int) and not isinstance(total, bool) else "?"
            )
            return (
                f"SmartRecruiters company '{self.company_identifier}' responded with "
                f"{count} public posting(s)."
            )

        return _health_report(self.source_name, probe)


AtsAdapter = LeverJobAdapter | AshbyJobAdapter | SmartRecruitersJobAdapter


# --------------------------------------------------------------------------- #
# Source catalog
# --------------------------------------------------------------------------- #


_GROUPS = frozenset({"linkedin", "google", "aira", "ats", "company_site"})
_STATUSES = frozenset({"available", "planned"})


@dataclass(frozen=True, slots=True)
class SourceDescriptor:
    """Describes one job source the copilot knows about."""

    source: str
    label: str
    group: str
    status: str
    access_method: str
    identifier_label: str
    identifier_help: str
    capabilities: JobSourceCapabilities
    reason: str = ""

    def __post_init__(self) -> None:
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", self.source):
            raise ValueError("Source ids are lowercase snake_case.")
        if self.group not in _GROUPS:
            raise ValueError(f"Unknown source group '{self.group}'.")
        if self.status not in _STATUSES:
            raise ValueError(f"Unknown source status '{self.status}'.")
        if self.status == "planned" and not self.reason.strip():
            raise ValueError("Planned sources must say why they are not available.")
        if self.status == "available" and self.reason.strip():
            raise ValueError("Available sources do not carry a reason.")


def _planned(
    source: str,
    label: str,
    reason: str,
    *,
    group: str = "ats",
    access_method: str = "",
) -> SourceDescriptor:
    return SourceDescriptor(
        source=source,
        label=label,
        group=group,
        status="planned",
        access_method=access_method or "Not integrated",
        identifier_label="",
        identifier_help="",
        capabilities=JobSourceCapabilities(),
        reason=reason,
    )


_NO_PUBLIC_API = "No official public candidate-facing posting API"

SOURCE_CATALOG: tuple[SourceDescriptor, ...] = (
    SourceDescriptor(
        source="linkedin",
        label="LinkedIn",
        group="linkedin",
        status="available",
        access_method="Existing LinkedIn MCP browser session",
        identifier_label="",
        identifier_help="",
        capabilities=JobSourceCapabilities(search=True, normalize=True),
    ),
    SourceDescriptor(
        source="google_jobs",
        label="Google Jobs",
        group="google",
        status="available",
        access_method="SerpApi google_jobs engine (GET, needs a SerpApi key from API or SERPAPI_API_KEY)",
        identifier_label="",
        identifier_help="No identifier is needed; connect a SerpApi key in API > Account or set SERPAPI_API_KEY.",
        capabilities=JobSourceCapabilities(
            search=True, import_jobs=True, normalize=True, resolve_application_link=True
        ),
    ),
    SourceDescriptor(
        source="aira_jobs",
        label="Aira Jobs",
        group="aira",
        status="available",
        access_method="Aira's own job search over free public job-board APIs (Himalayas, The Muse, Remotive, Jobicy, Arbeitnow); no key, no credits",
        identifier_label="",
        identifier_help="No identifier or key is needed.",
        capabilities=JobSourceCapabilities(
            search=True, import_jobs=True, normalize=True
        ),
    ),
    SourceDescriptor(
        source="greenhouse",
        label="Greenhouse",
        group="ats",
        status="available",
        access_method="Official public Job Board API (GET)",
        identifier_label="Board token",
        identifier_help=(
            "The board token is the first path segment of a careers URL such as "
            "https://boards.greenhouse.io/<token>/jobs/123."
        ),
        capabilities=JobSourceCapabilities(
            search=True, import_jobs=True, normalize=True, resolve_application_link=True
        ),
    ),
    SourceDescriptor(
        source="lever",
        label="Lever",
        group="ats",
        status="available",
        access_method="Official public Postings API (GET)",
        identifier_label="Site name",
        identifier_help=(
            "The site name is the first path segment of a careers URL such as "
            "https://jobs.lever.co/<site>/<posting-id>."
        ),
        capabilities=_ATS_CAPABILITIES,
    ),
    SourceDescriptor(
        source="ashby",
        label="Ashby",
        group="ats",
        status="available",
        access_method="Official public Job Board API (GET)",
        identifier_label="Job board name",
        identifier_help=(
            "The job board name is the first path segment of a careers URL such as "
            "https://jobs.ashbyhq.com/<name>/<job-id>."
        ),
        capabilities=_ATS_CAPABILITIES,
    ),
    SourceDescriptor(
        source="smartrecruiters",
        label="SmartRecruiters",
        group="ats",
        status="available",
        access_method="Official public Posting API (GET)",
        identifier_label="Company identifier",
        identifier_help=(
            "The company identifier is the first path segment of a careers URL such as "
            "https://jobs.smartrecruiters.com/<Company>/<posting-id>-title."
        ),
        capabilities=_ATS_CAPABILITIES,
    ),
    _planned(
        "workday",
        "Workday",
        "No official public candidate-facing posting API; the career-site JSON "
        "endpoint is unofficial",
        access_method="Unofficial career-site JSON endpoint (not used)",
    ),
    _planned(
        "icims",
        "iCIMS",
        f"{_NO_PUBLIC_API}; iCIMS career portals are tenant-specific and need "
        "per-portal scraping",
    ),
    _planned(
        "taleo",
        "Oracle Taleo",
        f"{_NO_PUBLIC_API}; Taleo career sections are rendered by tenant-specific "
        "portals",
    ),
    _planned(
        "oracle_careers",
        "Oracle Recruiting Cloud",
        f"{_NO_PUBLIC_API}; Oracle Recruiting Cloud career sites rely on an "
        "unofficial REST endpoint",
    ),
    _planned(
        "sap_successfactors",
        "SAP SuccessFactors",
        f"{_NO_PUBLIC_API}; SuccessFactors Career Site Builder feeds are "
        "tenant-configured",
    ),
    _planned(
        "bamboohr",
        "BambooHR",
        f"{_NO_PUBLIC_API}; BambooHR careers pages expose only an unofficial "
        "embedded JSON feed",
    ),
    _planned(
        "ukg",
        "UKG Pro Recruiting",
        f"{_NO_PUBLIC_API}; UKG career portals are tenant-specific",
    ),
    _planned(
        "company_site",
        "Company career site",
        "Direct career sites need a per-site adapter or sitemap/JSON-LD feed; "
        "not yet implemented",
        group="company_site",
        access_method="Per-site adapter or sitemap/JSON-LD feed (not yet implemented)",
    ),
)


def find_source(source: str) -> SourceDescriptor | None:
    """Return the catalog entry for a source id (case-insensitive), if any."""
    key = source.strip().lower()
    return next((item for item in SOURCE_CATALOG if item.source == key), None)


def catalog_as_dicts() -> list[dict[str, Any]]:
    """Return the catalog as JSON-serializable dictionaries."""
    return [asdict(descriptor) for descriptor in SOURCE_CATALOG]


# --------------------------------------------------------------------------- #
# Adapter factory
# --------------------------------------------------------------------------- #


def _build_ats_adapter(
    source: str, identifier: str, json_getter: JsonGetter | None
) -> AtsAdapter:
    if source == "lever":
        return LeverJobAdapter(identifier, json_getter)
    if source == "ashby":
        return AshbyJobAdapter(identifier, json_getter)
    if source == "smartrecruiters":
        return SmartRecruitersJobAdapter(identifier, json_getter)
    raise ValueError(f"Source {source} has no ATS adapter.")


def build_adapter(
    source: str, identifier: str = "", *, json_getter: JsonGetter | None = None
) -> GreenhouseJobAdapter | AtsAdapter:
    """Build the adapter for an available ATS source.

    ``identifier`` is the board token / site name / job board name / company
    identifier from the catalog. Planned sources raise ``ValueError`` with
    ``"Source <label> is not available yet."``; sources configured elsewhere
    (LinkedIn, Google Jobs) raise ``ValueError`` as well.
    """
    key = source.strip().lower()
    descriptor = find_source(key)
    if descriptor is None:
        raise ValueError(f"Unknown job source '{key}'.")
    if descriptor.status != "available":
        raise ValueError(f"Source {descriptor.label} is not available yet.")
    if key == "greenhouse":
        return GreenhouseJobAdapter(identifier, json_getter)
    if key in {"lever", "ashby", "smartrecruiters"}:
        return _build_ats_adapter(key, identifier, json_getter)
    raise ValueError(
        f"Source {descriptor.label} is configured elsewhere; build_adapter supports "
        "greenhouse, lever, ashby and smartrecruiters."
    )


# --------------------------------------------------------------------------- #
# Company portal URL detection
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PortalReference:
    """A recognised ATS portal: source id, board identifier and optional posting id."""

    source: str
    identifier: str
    external_id: str | None = None


_GREENHOUSE_HOSTS = frozenset(
    {
        "boards.greenhouse.io",
        "job-boards.greenhouse.io",
        "boards.eu.greenhouse.io",
        "job-boards.eu.greenhouse.io",
    }
)
_LEVER_HOSTS = frozenset({"jobs.lever.co", "jobs.eu.lever.co"})
_ASHBY_HOSTS = frozenset({"jobs.ashbyhq.com"})
_SMARTRECRUITERS_HOSTS = frozenset(
    {"jobs.smartrecruiters.com", "careers.smartrecruiters.com"}
)
_WORKDAY_HOST_RE = re.compile(r"^([a-z0-9-]{1,100})\.wd\d{1,3}\.myworkdayjobs\.com$")
_SMARTRECRUITERS_POSTING_RE = re.compile(r"^(\d{1,30})(?:-.*)?$")


def _greenhouse_reference(segments: list[str], query: str) -> PortalReference | None:
    if (
        len(segments) == 2
        and segments[0] == "embed"
        and segments[1] in {"job_app", "job_board"}
    ):
        parameters = dict(parse_qsl(query))
        token = parameters.get("for", "").strip()
        job_id = parameters.get("token", "").strip()
        if not _GREENHOUSE_TOKEN_RE.fullmatch(token):
            return None
        return PortalReference(
            "greenhouse", token, job_id if job_id.isdigit() else None
        )
    if not segments or not _GREENHOUSE_TOKEN_RE.fullmatch(segments[0]):
        return None
    if len(segments) == 1:
        return PortalReference("greenhouse", segments[0], None)
    if len(segments) >= 3 and segments[1] == "jobs" and segments[2].isdigit():
        return PortalReference("greenhouse", segments[0], segments[2])
    return None


def _uuid_portal_reference(
    source: str, identifier_re: re.Pattern[str], segments: list[str]
) -> PortalReference | None:
    if not segments or not identifier_re.fullmatch(segments[0]):
        return None
    if len(segments) == 1:
        return PortalReference(source, segments[0], None)
    if _UUID_RE.fullmatch(segments[1]):
        return PortalReference(source, segments[0], segments[1].lower())
    return None


def _smartrecruiters_reference(segments: list[str]) -> PortalReference | None:
    if not segments or not SmartRecruitersJobAdapter._identifier_re.fullmatch(
        segments[0]
    ):
        return None
    if len(segments) == 1:
        return PortalReference("smartrecruiters", segments[0], None)
    match = _SMARTRECRUITERS_POSTING_RE.fullmatch(segments[1])
    if match:
        return PortalReference("smartrecruiters", segments[0], match.group(1))
    return None


def detect_portal(url: str) -> PortalReference | None:
    """Recognise Greenhouse, Lever, Ashby, SmartRecruiters and Workday careers URLs.

    Returns a :class:`PortalReference` whose ``external_id`` is set only for a
    single-posting URL, ``None`` for any other URL, and raises ``ValueError``
    for a URL that is not absolute http(s).
    """
    canonical = canonicalize_job_url(url)
    parsed = urlsplit(canonical)
    host = (parsed.hostname or "").lower()
    segments = [unquote(segment) for segment in parsed.path.split("/") if segment]
    if host in _GREENHOUSE_HOSTS:
        return _greenhouse_reference(segments, parsed.query)
    if host in _LEVER_HOSTS:
        return _uuid_portal_reference("lever", LeverJobAdapter._site_re, segments)
    if host in _ASHBY_HOSTS:
        return _uuid_portal_reference("ashby", AshbyJobAdapter._name_re, segments)
    if host in _SMARTRECRUITERS_HOSTS:
        return _smartrecruiters_reference(segments)
    workday = _WORKDAY_HOST_RE.fullmatch(host)
    if workday:
        return PortalReference("workday", workday.group(1), None)
    return None


# --------------------------------------------------------------------------- #
# Single posting import and Greenhouse questions
# --------------------------------------------------------------------------- #


def _get_greenhouse_job(
    board_token: str, job_id: str, json_getter: JsonGetter | None
) -> Mapping[str, Any]:
    token = board_token.strip()
    if not _GREENHOUSE_TOKEN_RE.fullmatch(token):
        raise ValueError("Enter a valid Greenhouse board token.")
    job_id = str(job_id).strip()
    if not job_id.isdigit():
        raise ValueError("Enter a numeric Greenhouse job ID.")
    url = (
        "https://boards-api.greenhouse.io/v1/boards/"
        f"{quote(token, safe='')}/jobs/{job_id}?questions=true"
    )
    payload = (json_getter or _get_json)(url)
    if not isinstance(payload, Mapping):
        raise RuntimeError("Greenhouse returned an invalid job response.")
    return payload


def import_job_from_url(
    url: str, *, json_getter: JsonGetter | None = None
) -> JobPosting:
    """Import one posting from a Greenhouse, Lever, Ashby or SmartRecruiters job URL."""
    reference = detect_portal(url)
    if reference is None:
        raise ValueError(
            "This link is not a supported job posting URL. Paste a Greenhouse, Lever, "
            "Ashby or SmartRecruiters job page link."
        )
    descriptor = find_source(reference.source)
    if descriptor is None or descriptor.status != "available":
        label = descriptor.label if descriptor else reference.source
        raise ValueError(f"Source {label} is not available yet.")
    if not reference.external_id:
        raise ValueError(
            f"This link points to the whole {descriptor.label} job board "
            f"'{reference.identifier}', not a single posting. Open one job and paste "
            "its link."
        )
    if reference.source == "greenhouse":
        adapter = GreenhouseJobAdapter(reference.identifier, json_getter)
        return adapter.normalize(
            _get_greenhouse_job(
                reference.identifier, reference.external_id, json_getter
            )
        )
    ats_adapter = _build_ats_adapter(
        reference.source, reference.identifier, json_getter
    )
    return ats_adapter.fetch_job(reference.external_id)


_GREENHOUSE_FIELD_TYPES = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_single_select": "select",
    "multi_value_multi_select": "multi_select",
}


def _normalize_greenhouse_question(raw: Any) -> dict[str, Any] | None:
    if not isinstance(raw, Mapping):
        return None
    label = _clean_text(raw.get("label"), 500)
    first_field = next(
        (item for item in _as_list(raw.get("fields")) if isinstance(item, Mapping)),
        None,
    )
    field_name = _clean_text(first_field.get("name"), 200) if first_field else ""
    if not label and not field_name:
        return None
    raw_type = _clean_text(first_field.get("type"), 100).lower() if first_field else ""
    question_type = _GREENHOUSE_FIELD_TYPES.get(raw_type, "unknown")
    options: list[str] = []
    for value in _as_list(first_field.get("values")) if first_field else []:
        if not isinstance(value, Mapping):
            continue
        option = _clean_text(value.get("label"), 300) or _clean_text(
            value.get("value"), 300
        )
        if option:
            options.append(option)
    if question_type == "select" and {option.casefold() for option in options} == {
        "yes",
        "no",
    }:
        question_type = "boolean"
    return {
        "label": label or field_name,
        "required": bool(raw.get("required")),
        "type": question_type,
        "options": options,
        "field_name": field_name,
    }


def fetch_greenhouse_questions(
    board_token: str, job_id: str, *, json_getter: JsonGetter | None = None
) -> list[dict[str, Any]]:
    """Return the application questions of a Greenhouse job in a normalized shape.

    Each question is ``{"label", "required", "type", "options", "field_name"}``
    where ``type`` is one of ``text``, ``textarea``, ``select``,
    ``multi_select``, ``file``, ``boolean`` (a single-select whose only options
    are Yes/No) or ``unknown``. Malformed questions are skipped, never raised.
    """
    payload = _get_greenhouse_job(board_token, job_id, json_getter)
    questions: list[dict[str, Any]] = []
    for raw in _as_list(payload.get("questions")):
        try:
            normalized = _normalize_greenhouse_question(raw)
        except Exception:
            normalized = None
        if normalized is not None:
            questions.append(normalized)
    return questions
