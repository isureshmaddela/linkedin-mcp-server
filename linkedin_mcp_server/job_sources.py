"""Provider-neutral job records and source adapter contracts."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html import unescape
from html.parser import HTMLParser
import base64
import hashlib
import json
import math
import re
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit
from urllib.request import Request, urlopen


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _validate_http_url(value: str, field_name: str) -> None:
    parsed = urlsplit(value)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ValueError(f"{field_name} must be an absolute HTTP(S) URL.")


def canonicalize_job_url(value: str) -> str:
    """Remove host aliases, tracking queries, and fragments from a posting URL."""
    _validate_http_url(value, "Job URL")
    parsed = urlsplit(value.strip())
    host = (parsed.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    port = parsed.port
    if port and not (
        (parsed.scheme.lower() == "http" and port == 80)
        or (parsed.scheme.lower() == "https" and port == 443)
    ):
        host = f"{host}:{port}"
    path = re.sub(r"/{2,}", "/", parsed.path).rstrip("/") or "/"
    tracking_keys = {
        "gh_src",
        "ref",
        "referrer",
        "trk",
        "trackingid",
        "tracking_id",
        "utm_campaign",
        "utm_content",
        "utm_medium",
        "utm_source",
        "utm_term",
    }
    query = sorted(
        (key, item)
        for key, item in parse_qsl(parsed.query, keep_blank_values=True)
        if key.casefold() not in tracking_keys and not key.casefold().startswith("utm_")
    )
    suffix = f"?{urlencode(query)}" if query else ""
    return f"{parsed.scheme.lower()}://{host}{path}{suffix}"


@dataclass(frozen=True, slots=True)
class JobPosting:
    """Normalized job data, with unknown provider fields left empty."""

    source: str
    external_id: str
    source_url: str
    title: str = ""
    company: str = ""
    location: str = ""
    application_url: str | None = None
    workplace_type: str | None = None
    employment_type: str | None = None
    salary_min: float | None = None
    salary_max: float | None = None
    currency: str | None = None
    description: str = ""
    required_skills: tuple[str, ...] = ()
    preferred_skills: tuple[str, ...] = ()
    seniority: str | None = None
    posted_at: datetime | None = None
    expires_at: datetime | None = None
    application_method: str | None = None
    imported_at: datetime = field(default_factory=_now_utc)
    last_seen_at: datetime = field(default_factory=_now_utc)
    content_fingerprint: str = field(init=False)

    def __post_init__(self) -> None:
        source = self.source.strip().lower()
        external_id = self.external_id.strip()
        if not source:
            raise ValueError("Job source is required.")
        if not external_id:
            raise ValueError("External job ID is required.")
        object.__setattr__(self, "source", source)
        object.__setattr__(self, "external_id", external_id)
        _validate_http_url(self.source_url, "Source URL")
        if self.application_url:
            _validate_http_url(self.application_url, "Application URL")

        for amount in (self.salary_min, self.salary_max):
            if amount is not None and (not math.isfinite(amount) or amount < 0):
                raise ValueError("Salary values must be finite and non-negative.")
        if (
            self.salary_min is not None
            and self.salary_max is not None
            and self.salary_min > self.salary_max
        ):
            raise ValueError("Minimum salary cannot exceed maximum salary.")

        for value in (
            self.imported_at,
            self.last_seen_at,
            self.posted_at,
            self.expires_at,
        ):
            if value is not None and value.utcoffset() is None:
                raise ValueError("Job timestamps must include a timezone.")

        fingerprint_fields = (
            self.title,
            self.company,
            self.location,
            self.description,
        )
        populated = [
            re.sub(r"\s+", " ", value).strip().casefold()
            for value in fingerprint_fields
            if value.strip()
        ]
        has_identity_context = (
            bool(self.description.strip())
            or sum(
                bool(value.strip())
                for value in (self.title, self.company, self.location)
            )
            >= 2
        )
        fingerprint = (
            hashlib.sha256("\n".join(populated).encode("utf-8")).hexdigest()
            if populated and has_identity_context
            else ""
        )
        object.__setattr__(self, "content_fingerprint", fingerprint)

    def to_dict(self) -> dict[str, Any]:
        """Return JSON-friendly values while retaining the normalized field names."""
        result = asdict(self)
        for key in ("imported_at", "last_seen_at", "posted_at", "expires_at"):
            value = result[key]
            result[key] = value.isoformat() if value is not None else None
        result["required_skills"] = list(self.required_skills)
        result["preferred_skills"] = list(self.preferred_skills)
        return result


@dataclass(frozen=True, slots=True)
class JobSourceCapabilities:
    search: bool = False
    import_jobs: bool = False
    normalize: bool = False
    resolve_application_link: bool = False
    read_application_status: bool = False
    prepare_application: bool = False
    submit_application: bool = False
    capture_confirmation: bool = False
    report_health: bool = False


@dataclass(frozen=True, slots=True)
class JobSearchQuery:
    keywords: str
    location: str | None = None
    max_pages: int = 3
    filters: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        keywords = self.keywords.strip()
        if not keywords or len(keywords) > 200:
            raise ValueError("Search keywords must contain 1 to 200 characters.")
        if not 1 <= self.max_pages <= 10:
            raise ValueError("Maximum pages must be from 1 to 10.")
        if self.location is not None and len(self.location.strip()) > 200:
            raise ValueError("Search location must be 200 characters or fewer.")
        object.__setattr__(self, "keywords", keywords)
        if self.location is not None:
            object.__setattr__(self, "location", self.location.strip() or None)


@dataclass(frozen=True, slots=True)
class JobSearchResult:
    jobs: tuple[JobPosting, ...]
    section_errors: Mapping[str, Any] = field(default_factory=dict)


class JobSourceAdapter(Protocol):
    source_name: str

    @property
    def capabilities(self) -> JobSourceCapabilities: ...

    def normalize(self, record: Mapping[str, Any]) -> JobPosting: ...

    def search(self, query: JobSearchQuery) -> JobSearchResult: ...


SearchTool = Callable[[str, dict[str, Any]], Mapping[str, Any]]


class LinkedInJobAdapter:
    """Normalize the typed LinkedIn job references exposed by the MCP tool."""

    source_name = "linkedin"
    _allowed_filters = {
        "date_posted",
        "job_type",
        "experience_level",
        "work_type",
        "easy_apply",
        "sort_by",
    }
    _job_path = re.compile(r"^/jobs/view/(\d+)(?:/|$)")

    def __init__(self, search_tool: SearchTool | None = None) -> None:
        self._search_tool = search_tool

    @property
    def capabilities(self) -> JobSourceCapabilities:
        return JobSourceCapabilities(
            search=self._search_tool is not None,
            normalize=True,
        )

    def normalize(self, record: Mapping[str, Any]) -> JobPosting:
        if record.get("kind") != "job":
            raise ValueError("LinkedIn record is not a job reference.")
        raw_url = str(record.get("url", "")).strip()
        absolute_url = urljoin("https://www.linkedin.com", raw_url)
        parsed = urlsplit(absolute_url)
        if parsed.hostname not in {"linkedin.com", "www.linkedin.com"}:
            raise ValueError("Job reference is not a LinkedIn URL.")
        match = self._job_path.match(parsed.path)
        if not match:
            raise ValueError("LinkedIn job reference has no numeric job ID.")
        job_id = match.group(1)
        title = re.sub(r"\s+", " ", str(record.get("text", ""))).strip()
        return JobPosting(
            source=self.source_name,
            external_id=job_id,
            source_url=f"https://www.linkedin.com/jobs/view/{job_id}/",
            title=title[:500],
        )

    @classmethod
    def normalize_search_result(cls, result: Mapping[str, Any]) -> JobSearchResult:
        adapter = cls()
        references = result.get("references", {})
        raw_references = (
            references.get("search_results", [])
            if isinstance(references, Mapping)
            else []
        )
        jobs: list[JobPosting] = []
        seen_ids: set[str] = set()
        if isinstance(raw_references, list):
            for reference in raw_references:
                if not isinstance(reference, Mapping):
                    continue
                try:
                    job = adapter.normalize(reference)
                except ValueError:
                    continue
                if job.external_id not in seen_ids:
                    jobs.append(job)
                    seen_ids.add(job.external_id)

        raw_ids = result.get("job_ids", [])
        if isinstance(raw_ids, (list, tuple)):
            for raw_id in raw_ids:
                job_id = str(raw_id).strip()
                if not job_id.isdigit() or job_id in seen_ids:
                    continue
                jobs.append(
                    JobPosting(
                        source=adapter.source_name,
                        external_id=job_id,
                        source_url=f"https://www.linkedin.com/jobs/view/{job_id}/",
                    )
                )
                seen_ids.add(job_id)

        section_errors = result.get("section_errors", {})
        if not isinstance(section_errors, Mapping):
            section_errors = {}
        return JobSearchResult(tuple(jobs), dict(section_errors))

    def search(self, query: JobSearchQuery) -> JobSearchResult:
        if self._search_tool is None:
            raise RuntimeError("LinkedIn search tool is not configured.")
        unsupported = set(query.filters) - self._allowed_filters
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"Unsupported LinkedIn job filters: {names}.")
        arguments: dict[str, Any] = {
            "keywords": query.keywords,
            "location": query.location,
            "max_pages": query.max_pages,
            **query.filters,
        }
        result = self._search_tool("search_jobs", arguments)
        return self.normalize_search_result(result)


class _DescriptionTextParser(HTMLParser):
    _BLOCK_TAGS = {"br", "dd", "div", "h1", "h2", "h3", "li", "p", "section", "tr"}

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"script", "style"}:
            self._ignored_depth += 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in {"script", "style"} and self._ignored_depth:
            self._ignored_depth -= 1
        elif not self._ignored_depth and tag in self._BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _html_to_text(value: str) -> str:
    current = value
    for _ in range(2):
        decoded = unescape(current)
        if decoded == current:
            break
        current = decoded
    parser = _DescriptionTextParser()
    parser.feed(current)
    parsed = "".join(parser.parts)
    return re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", parsed)).strip()


def html_to_text(value: str) -> str:
    """Public alias used by ATS adapters outside this module."""
    return _html_to_text(value)


def parse_timestamp(value: Any) -> datetime | None:
    """Public alias for the lenient ISO-8601 timestamp parser."""
    return _parse_timestamp(value)


def validate_http_url(value: str, field_name: str) -> None:
    """Public alias for absolute HTTP(S) URL validation."""
    _validate_http_url(value, field_name)


# Matches the external-ID rule used by Aira's job store.
_SAFE_EXTERNAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,179}$")

GreenhouseJsonGetter = Callable[[str], Mapping[str, Any]]
GoogleJobsJsonGetter = Callable[[Mapping[str, str]], Mapping[str, Any]]


class GreenhouseJobAdapter:
    """Read-only search adapter for Greenhouse's public Job Board API."""

    source_name = "greenhouse"
    _board_token = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
    _allowed_filters = {"department", "office"}

    def __init__(
        self,
        board_token: str,
        json_getter: GreenhouseJsonGetter | None = None,
    ) -> None:
        board_token = board_token.strip()
        if not self._board_token.fullmatch(board_token):
            raise ValueError("Enter a valid Greenhouse board token.")
        self.board_token = board_token
        self._json_getter = json_getter or self._get_json

    @property
    def capabilities(self) -> JobSourceCapabilities:
        return JobSourceCapabilities(
            search=True,
            import_jobs=True,
            normalize=True,
            resolve_application_link=True,
        )

    @staticmethod
    def _get_json(url: str) -> Mapping[str, Any]:
        request = Request(
            url, headers={"Accept": "application/json", "User-Agent": "Aira/1.0"}
        )
        try:
            with urlopen(request, timeout=20) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise RuntimeError(
                f"Greenhouse job board returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Could not read the Greenhouse job board response."
            ) from error
        if not isinstance(payload, Mapping):
            raise RuntimeError("Greenhouse returned an invalid job board response.")
        return payload

    @staticmethod
    def _names(records: Any) -> str:
        if not isinstance(records, list):
            return ""
        return " ".join(
            str(item.get("name", "")) for item in records if isinstance(item, Mapping)
        )

    def normalize(self, record: Mapping[str, Any]) -> JobPosting:
        raw_id = str(record.get("id", "")).strip()
        if not raw_id.isdigit():
            raise ValueError("Greenhouse job record has no numeric posting ID.")
        raw_url = str(record.get("absolute_url", "")).strip()
        _validate_http_url(raw_url, "Greenhouse posting URL")
        location = record.get("location", {})
        location_name = (
            str(location.get("name", ""))
            if isinstance(location, Mapping)
            else str(location or "")
        )
        published = record.get("first_published")
        expires = record.get("application_deadline")
        return JobPosting(
            source=self.source_name,
            external_id=raw_id,
            source_url=raw_url,
            application_url=raw_url,
            title=str(record.get("title", "")).strip()[:500],
            company=str(record.get("company_name", "")).strip()[:300],
            location=location_name.strip()[:300],
            description=_html_to_text(str(record.get("content", "")))[:50000],
            posted_at=_parse_timestamp(published),
            expires_at=_parse_timestamp(expires),
            application_method="greenhouse_job_board",
        )

    def search(self, query: JobSearchQuery) -> JobSearchResult:
        unsupported = set(query.filters) - self._allowed_filters
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"Unsupported Greenhouse job filters: {names}.")
        url = (
            "https://boards-api.greenhouse.io/v1/boards/"
            f"{quote(self.board_token, safe='')}/jobs?content=true"
        )
        response = self._json_getter(url)
        records = response.get("jobs", [])
        if not isinstance(records, list):
            raise RuntimeError("Greenhouse returned an invalid jobs list.")
        keywords = query.keywords.casefold().split()
        location_filter = (query.location or "").casefold()
        department_filter = str(query.filters.get("department", "")).casefold()
        office_filter = str(query.filters.get("office", "")).casefold()
        jobs: list[JobPosting] = []
        for record in records:
            if not isinstance(record, Mapping):
                continue
            try:
                job = self.normalize(record)
            except ValueError:
                continue
            searchable = " ".join(
                (
                    job.title,
                    job.company,
                    job.description,
                    self._names(record.get("departments", [])),
                    self._names(record.get("offices", [])),
                )
            ).casefold()
            if any(keyword not in searchable for keyword in keywords):
                continue
            if location_filter and location_filter not in job.location.casefold():
                continue
            if (
                department_filter
                and department_filter
                not in self._names(record.get("departments", [])).casefold()
            ):
                continue
            if (
                office_filter
                and office_filter
                not in self._names(record.get("offices", [])).casefold()
            ):
                continue
            jobs.append(job)
        return JobSearchResult(tuple(jobs))


def _parse_timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.utcoffset() is None else parsed


class GoogleJobsAdapter:
    """Google Jobs search through SerpApi's documented structured endpoint."""

    source_name = "google_jobs"
    _w2_re = re.compile(r"\bw[\s-]?2\b", re.IGNORECASE)
    _employment_aliases = {
        "full time": "full_time",
        "full-time": "full_time",
        "part time": "part_time",
        "part-time": "part_time",
        "contract": "contract",
        "contractor": "contract",
        "temporary": "temporary",
        "internship": "internship",
    }

    def __init__(
        self,
        api_key: str,
        json_getter: GoogleJobsJsonGetter | None = None,
    ) -> None:
        self.api_key = api_key.strip()
        if not self.api_key:
            raise ValueError(
                "Google Jobs search is not configured. Set SERPAPI_API_KEY."
            )
        self._json_getter = json_getter or self._get_json

    @property
    def capabilities(self) -> JobSourceCapabilities:
        return JobSourceCapabilities(
            search=True,
            import_jobs=True,
            normalize=True,
            resolve_application_link=True,
        )

    def use_json_getter(self, json_getter: GoogleJobsJsonGetter) -> None:
        """Send this adapter's requests through ``json_getter`` instead of SerpApi directly.

        Aira uses this to route searches through its Aira API history and cache.
        The getter receives the request parameters (including ``api_key``) and
        returns the decoded response body.
        """
        self._json_getter = json_getter

    @staticmethod
    def _get_json(parameters: Mapping[str, str]) -> Mapping[str, Any]:
        url = "https://serpapi.com/search.json?" + urlencode(parameters)
        request = Request(
            url, headers={"Accept": "application/json", "User-Agent": "Aira/1.0"}
        )
        try:
            with urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except HTTPError as error:
            raise RuntimeError(
                f"Google Jobs provider returned HTTP {error.code}."
            ) from error
        except (URLError, TimeoutError, json.JSONDecodeError) as error:
            raise RuntimeError(
                "Could not read the Google Jobs provider response."
            ) from error
        if not isinstance(payload, Mapping):
            raise RuntimeError("Google Jobs provider returned an invalid response.")
        return payload

    @classmethod
    def _employment_type(cls, value: Any) -> str | None:
        normalized = re.sub(r"\s+", " ", str(value or "")).strip().casefold()
        if normalized in {
            "full_time",
            "part_time",
            "contract",
            "temporary",
            "internship",
        }:
            return normalized
        return cls._employment_aliases.get(normalized)

    @staticmethod
    def stable_job_id(raw_job_id: str) -> str:
        """Return a short, storage-safe ID for a SerpApi ``job_id``.

        SerpApi job IDs are base64-encoded JSON (with ``=`` padding and often
        longer than 180 characters). They embed Google's ``htidocid`` plus
        query-dependent fields such as ``uule``. Hashing the ``htidocid`` keeps
        the same posting on the same ID across different searches; when it
        cannot be decoded, the whole raw ID is hashed instead. IDs that are
        already short and safe are returned unchanged.
        """
        raw_job_id = raw_job_id.strip()
        if not raw_job_id:
            return ""
        if _SAFE_EXTERNAL_ID.fullmatch(raw_job_id):
            return raw_job_id
        identity = raw_job_id
        try:
            padded = raw_job_id + "=" * (-len(raw_job_id) % 4)
            decoded = json.loads(
                base64.urlsafe_b64decode(padded.replace("+", "-").replace("/", "_"))
            )
            if (
                isinstance(decoded, Mapping)
                and str(decoded.get("htidocid", "")).strip()
            ):
                identity = "htidocid:" + str(decoded["htidocid"]).strip()
        except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
            pass
        return "g-" + hashlib.sha256(identity.encode("utf-8")).hexdigest()[:40]

    def normalize(self, record: Mapping[str, Any]) -> JobPosting:
        apply_options = record.get("apply_options", [])
        apply_link = ""
        if isinstance(apply_options, list):
            apply_link = next(
                (
                    str(option.get("link", "")).strip()
                    for option in apply_options
                    if isinstance(option, Mapping) and option.get("link")
                ),
                "",
            )
        raw_source_url = str(record.get("share_link") or apply_link).strip()
        _validate_http_url(raw_source_url, "Google Jobs posting URL")
        job_id = self.stable_job_id(str(record.get("job_id", "")))
        if not job_id:
            job_id = hashlib.sha256(
                canonicalize_job_url(raw_source_url).encode("utf-8")
            ).hexdigest()[:32]

        detected = record.get("detected_extensions", {})
        if not isinstance(detected, Mapping):
            detected = {}
        work_from_home = detected.get("work_from_home")
        workplace_type = "remote" if work_from_home is True else None
        return JobPosting(
            source=self.source_name,
            external_id=job_id,
            source_url=raw_source_url,
            application_url=apply_link or raw_source_url,
            title=str(record.get("title", "")).strip()[:500],
            company=str(record.get("company_name", "")).strip()[:300],
            location=str(record.get("location", "")).strip()[:300],
            workplace_type=workplace_type,
            employment_type=self._employment_type(detected.get("schedule_type")),
            description=str(record.get("description", "")).strip()[:50000],
            application_method="google_jobs_result",
        )

    def search(self, query: JobSearchQuery) -> JobSearchResult:
        unsupported = set(query.filters) - {"work_type", "job_type", "w2_search"}
        if unsupported:
            names = ", ".join(sorted(unsupported))
            raise ValueError(f"Unsupported Google Jobs filters: {names}.")
        work_type = str(query.filters.get("work_type", ""))
        job_type = str(query.filters.get("job_type", ""))
        w2_search = bool(query.filters.get("w2_search", False))
        keywords = query.keywords
        if w2_search:
            keywords = f'{keywords} ("W-2" OR W2)'
        parameters = {
            "engine": "google_jobs",
            "q": keywords,
            "api_key": self.api_key,
            "hl": "en",
        }
        if query.location:
            parameters["location"] = query.location

        jobs: list[JobPosting] = []
        seen_ids: set[str] = set()
        next_page_token = ""
        section_errors: dict[str, Any] = {}
        for page in range(query.max_pages):
            page_parameters = dict(parameters)
            if next_page_token:
                page_parameters["next_page_token"] = next_page_token
            response = self._json_getter(page_parameters)
            if response.get("error"):
                raise RuntimeError(
                    "Google Jobs provider rejected the search request or API key."
                )
            records = response.get("jobs_results", [])
            if not isinstance(records, list):
                raise RuntimeError(
                    "Google Jobs provider returned an invalid jobs list."
                )
            for record in records:
                if not isinstance(record, Mapping):
                    continue
                try:
                    job = self.normalize(record)
                except ValueError:
                    continue
                if job.external_id in seen_ids:
                    continue
                if work_type and job.workplace_type != work_type:
                    continue
                if job_type and self._employment_type(job.employment_type) != job_type:
                    continue
                if w2_search and not self._w2_re.search(
                    " ".join((job.title, job.company, job.description))
                ):
                    continue
                jobs.append(job)
                seen_ids.add(job.external_id)
            pagination = response.get("serpapi_pagination", {})
            next_page_token = (
                str(pagination.get("next_page_token", ""))
                if isinstance(pagination, Mapping)
                else ""
            )
            if not next_page_token:
                break
        return JobSearchResult(tuple(jobs), section_errors)
