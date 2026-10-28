"""SerpApi engine catalog: loading, validation and request parameter checking.

The catalog JSON files in ``catalog/`` were compiled from public SerpApi
documentation. They are data, not code, so this loader is deliberately
tolerant of the quirks of documentation-derived data while refusing anything
that would make a request unsafe or ambiguous:

* ``platform.json`` has no ``engines`` list and is exposed separately.
* Any ``*.json`` file with an ``engines`` list is merged, including optional
  ``extra_*.json`` files added later.
* ``enum`` parameters may carry ``options_truncated`` (the documentation listed
  only some values) or, rarely, no options at all. Both are treated as free
  text so a valid value that the docs omit is never rejected.
* ``csv`` and ``boolean`` parameters may also list ``options``.
* Engines may be flagged ``unlisted`` (sub-endpoints reached through another
  engine's results) or ``deprecated``; both stay callable.

Structural problems (duplicate ids, unknown parameter types, requirement groups
naming parameters that do not exist) raise :class:`CatalogError` at load time.
"""

from __future__ import annotations

import difflib
import json
import math
import re
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

CATALOG_DIR = Path(__file__).with_name("catalog")
PLATFORM_FILE = "platform.json"
PARAM_TYPES = frozenset(
    {"string", "enum", "integer", "number", "boolean", "csv", "location", "date"}
)
# Names that belong to the request envelope, not to an engine. They are applied
# from run options (or by Aira itself), so accepting them as engine parameters
# would let a stored search carry a credential or silently change behaviour.
RESERVED_PARAMS = {
    "api_key": "Monosage adds the SerpApi key itself; never put it in parameters.",
    "engine": "The engine is chosen separately from its parameters.",
    "output": "Set the output format in the run options.",
    "async": "Set async in the run options.",
    "no_cache": "Set no_cache in the run options.",
    "zero_trace": "Set zero_trace in the run options.",
    "json_restrictor": "Set json_restrictor in the run options.",
}
MAX_TEXT_LENGTH = 20_000
MAX_CSV_ITEMS = 500
_ENGINE_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_PARAM_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_.]{0,63}$")
_INT_RE = re.compile(r"^[+-]?\d{1,18}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_COMPACT_DATE_RE = re.compile(r"^\d{8}$")
CATEGORY_LABELS = {
    "web": "Web search",
    "news": "News",
    "images": "Images",
    "video": "Video",
    "ai": "AI answers",
    "maps_local": "Maps and local",
    "travel": "Travel",
    "shopping": "Shopping",
    "marketplace": "Marketplaces",
    "scholar_patents": "Scholar and patents",
    "app_store": "App stores",
    "reviews": "Reviews",
    "trends": "Trends",
    "finance": "Finance",
    "jobs": "Jobs",
    "social": "Social",
    "utility": "Utilities",
}


class CatalogError(ValueError):
    """The catalog files are structurally invalid."""


@dataclass(frozen=True, slots=True)
class Option:
    value: str
    label: str


@dataclass(frozen=True, slots=True)
class Param:
    name: str
    label: str
    type: str
    required: bool = False
    group: str = ""
    help: str = ""
    default: Any = None
    requires_one_of: str | None = None
    options: tuple[Option, ...] = ()
    options_truncated: bool = False
    suggestions: tuple[Option, ...] = ()

    @property
    def closed_options(self) -> bool:
        """True when the documented options are the complete set of valid values."""
        return bool(self.options) and not self.options_truncated

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "name": self.name,
            "label": self.label,
            "type": self.type,
            "required": self.required,
            "group": self.group,
            "help": self.help,
            "default": self.default,
            "requires_one_of": self.requires_one_of,
        }
        if self.options:
            data["options"] = [
                {"value": item.value, "label": item.label} for item in self.options
            ]
        if self.options_truncated:
            data["options_truncated"] = True
        if self.suggestions:
            data["suggestions"] = [
                {"value": item.value, "label": item.label} for item in self.suggestions
            ]
        return data


@dataclass(frozen=True, slots=True)
class Engine:
    id: str
    name: str
    category: str
    family: str
    description: str
    docs_url: str
    endpoint: str
    params: tuple[Param, ...]
    requires_any_of: tuple[tuple[str, ...], ...]
    pagination: Mapping[str, Any]
    result_keys: tuple[str, ...]
    result_item_fields: Mapping[str, tuple[str, ...]]
    aira_use: str
    verified: str
    unlisted: bool = False
    deprecated: bool = False
    deprecated_reason: str = ""
    # "serpapi" engines are answered by SerpApi with the user's key; "aira" engines
    # are answered by Aira itself (no key, no credits).
    provider: str = "serpapi"
    _by_name: Mapping[str, Param] = field(default_factory=dict, repr=False)

    def param(self, name: str) -> Param | None:
        return self._by_name.get(name)

    @property
    def required(self) -> list[str]:
        return [item.name for item in self.params if item.required]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "family": self.family,
            "category": self.category,
            "description": self.description,
            "docs_url": self.docs_url,
            "param_count": len(self.params),
            "required": self.required,
            "requires_any_of": [list(group) for group in self.requires_any_of],
            "pagination": dict(self.pagination),
            "aira_use": self.aira_use,
            "unlisted": self.unlisted,
            "deprecated": self.deprecated,
            "provider": self.provider,
        }

    def to_dict(self) -> dict[str, Any]:
        data = self.summary()
        data.update(
            {
                "endpoint": self.endpoint,
                "params": [item.to_dict() for item in self.params],
                "result_keys": list(self.result_keys),
                "result_item_fields": {
                    key: list(value) for key, value in self.result_item_fields.items()
                },
                "verified": self.verified,
                "deprecated_reason": self.deprecated_reason,
            }
        )
        return data


@dataclass(frozen=True, slots=True)
class _Catalog:
    engines: Mapping[str, Engine]
    platform: Mapping[str, Any]


_lock = threading.Lock()
_loaded: _Catalog | None = None


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _options(raw: Any, where: str) -> tuple[Option, ...]:
    if raw in (None, ""):
        return ()
    if not isinstance(raw, list):
        raise CatalogError(f"{where}: options must be a list.")
    seen: set[str] = set()
    result: list[Option] = []
    for item in raw:
        if isinstance(item, Mapping):
            if "value" not in item:
                raise CatalogError(f"{where}: an option has no value.")
            value = _option_value(item["value"])
            label = _text(item.get("label")) or value
        elif isinstance(item, (str, int, float, bool)):
            value = _option_value(item)
            label = value
        else:
            raise CatalogError(f"{where}: unsupported option {item!r}.")
        if value not in seen:
            seen.add(value)
            result.append(Option(value, label))
    return tuple(result)


def _option_value(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value).strip()


def _parse_param(raw: Any, engine_id: str) -> Param:
    if not isinstance(raw, Mapping):
        raise CatalogError(f"{engine_id}: a parameter is not an object.")
    name = _text(raw.get("name"))
    where = f"{engine_id}.{name or '?'}"
    if not _PARAM_NAME_RE.match(name):
        raise CatalogError(f"{where}: invalid parameter name.")
    kind = _text(raw.get("type"))
    if kind not in PARAM_TYPES:
        raise CatalogError(f"{where}: unknown parameter type {kind!r}.")
    group = raw.get("requires_one_of")
    return Param(
        name=name,
        label=_text(raw.get("label")) or name,
        type=kind,
        required=bool(raw.get("required")),
        group=_text(raw.get("group")),
        help=_text(raw.get("help")),
        default=raw.get("default"),
        requires_one_of=group if isinstance(group, str) and group else None,
        options=_options(raw.get("options"), where),
        options_truncated=bool(raw.get("options_truncated")),
        suggestions=_options(raw.get("suggestions"), where),
    )


def _merge_alternative_groups(
    groups: list[tuple[str, ...]], by_name: Mapping[str, Param]
) -> list[tuple[str, ...]]:
    """Fold singleton groups whose parameters share a ``requires_one_of`` label.

    The documentation extract sometimes lists alternatives such as ``text`` or
    ``url`` as two one-name groups ``[["text"], ["url"]]``, which would demand
    both. The parameters themselves say they are alternatives by sharing a
    ``requires_one_of`` label (``query_or_image``), so those groups become one.
    Singleton groups on parameters that are individually ``required`` stay
    separate: they genuinely are all required.
    """
    merged: list[tuple[str, ...]] = []
    by_label: dict[str, int] = {}
    for group in groups:
        spec = by_name[group[0]]
        label = spec.requires_one_of
        if len(group) == 1 and label and not spec.required:
            if label in by_label:
                index = by_label[label]
                merged[index] = (*merged[index], group[0])
            else:
                by_label[label] = len(merged)
                merged.append(group)
        else:
            merged.append(group)
    return merged


def _parse_engine(raw: Any, source: str) -> Engine:
    if not isinstance(raw, Mapping):
        raise CatalogError(f"{source}: an engine entry is not an object.")
    engine_id = _text(raw.get("id"))
    if not _ENGINE_ID_RE.match(engine_id):
        raise CatalogError(f"{source}: invalid engine id {engine_id!r}.")
    for required in ("name", "category", "family", "description", "docs_url"):
        if not _text(raw.get(required)):
            raise CatalogError(f"{engine_id}: missing {required}.")
    params_raw = raw.get("params")
    if not isinstance(params_raw, list):
        raise CatalogError(f"{engine_id}: params must be a list.")
    params = tuple(_parse_param(item, engine_id) for item in params_raw)
    by_name: dict[str, Param] = {}
    for item in params:
        if item.name in by_name:
            raise CatalogError(f"{engine_id}: duplicate parameter {item.name!r}.")
        by_name[item.name] = item
    groups: list[tuple[str, ...]] = []
    for group in raw.get("requires_any_of") or []:
        if not isinstance(group, list) or not group:
            raise CatalogError(f"{engine_id}: requires_any_of holds an empty group.")
        names = tuple(str(name) for name in group)
        missing = [name for name in names if name not in by_name]
        if missing:
            raise CatalogError(
                f"{engine_id}: requires_any_of names unknown parameters {missing}."
            )
        groups.append(names)
    groups = _merge_alternative_groups(groups, by_name)
    pagination = raw.get("pagination")
    item_fields = raw.get("result_item_fields") or {}
    if not isinstance(item_fields, Mapping):
        raise CatalogError(f"{engine_id}: result_item_fields must be an object.")
    return Engine(
        id=engine_id,
        name=_text(raw.get("name")),
        category=_text(raw.get("category")),
        family=_text(raw.get("family")),
        description=_text(raw.get("description")),
        docs_url=_text(raw.get("docs_url")),
        endpoint=_text(raw.get("endpoint")) or "https://serpapi.com/search.json",
        params=params,
        requires_any_of=tuple(groups),
        pagination=dict(pagination) if isinstance(pagination, Mapping) else {},
        result_keys=tuple(str(key) for key in raw.get("result_keys") or ()),
        result_item_fields={
            str(key): tuple(str(item) for item in value or ())
            for key, value in item_fields.items()
        },
        aira_use=_text(raw.get("aira_use")),
        verified=_text(raw.get("verified")) or "docs",
        unlisted=bool(raw.get("unlisted")),
        deprecated=bool(raw.get("deprecated")),
        deprecated_reason=_text(raw.get("deprecated_reason")),
        provider=_provider(raw.get("provider"), engine_id),
        _by_name=by_name,
    )


PROVIDERS = ("serpapi", "aira")


def _provider(raw: Any, engine_id: str) -> str:
    value = _text(raw) or "serpapi"
    if value not in PROVIDERS:
        raise CatalogError(
            f"{engine_id}: provider must be one of {', '.join(PROVIDERS)}."
        )
    return value


def is_local(engine_id: str) -> bool:
    """Whether Aira answers this engine itself (no SerpApi key, no credits)."""
    try:
        return engine(engine_id).provider == "aira"
    except LookupError:
        return False


def local_engine_ids() -> tuple[str, ...]:
    return tuple(
        item.id for item in _catalog().engines.values() if item.provider == "aira"
    )


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise CatalogError(
            f"{path.name}: cannot read catalog file ({error})."
        ) from error


def load_catalog(directory: Path | None = None) -> _Catalog:
    """Read and validate every catalog file in ``directory`` (uncached)."""
    root = directory or CATALOG_DIR
    platform: Mapping[str, Any] = {}
    engines: dict[str, Engine] = {}
    for path in sorted(root.glob("*.json")):
        data = _read_json(path)
        if not isinstance(data, Mapping):
            raise CatalogError(f"{path.name}: expected a JSON object.")
        if path.name == PLATFORM_FILE:
            platform = data
            continue
        entries = data.get("engines")
        if not isinstance(entries, list):
            raise CatalogError(f"{path.name}: missing an engines list.")
        for entry in entries:
            engine = _parse_engine(entry, path.name)
            if engine.id in engines:
                raise CatalogError(f"Duplicate engine id {engine.id!r} in {path.name}.")
            engines[engine.id] = engine
    if not engines:
        raise CatalogError("The engine catalog is empty.")
    return _Catalog(engines=engines, platform=platform)


def _catalog() -> _Catalog:
    global _loaded
    if _loaded is None:
        with _lock:
            if _loaded is None:
                _loaded = load_catalog()
    return _loaded


def engines() -> dict[str, Engine]:
    """Every engine keyed by id, in catalog order."""
    return dict(_catalog().engines)


def engine(engine_id: str) -> Engine:
    """Return one engine or raise ``LookupError`` with a close-match hint."""
    found = _catalog().engines.get(str(engine_id))
    if found is not None:
        return found
    close = difflib.get_close_matches(str(engine_id), _catalog().engines, n=3)
    hint = f" Did you mean {', '.join(close)}?" if close else ""
    raise LookupError(f"Unknown engine {str(engine_id)[:60]!r}.{hint}")


def platform() -> Mapping[str, Any]:
    """The platform.json document (dashboard pages, errors, plans, account API)."""
    return _catalog().platform


def common_parameters() -> list[dict[str, Any]]:
    value = platform().get("common_parameters")
    return [dict(item) for item in value] if isinstance(value, list) else []


def categories() -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in _catalog().engines.values():
        counts[item.category] = counts.get(item.category, 0) + 1
    return [
        {
            "id": key,
            "label": CATEGORY_LABELS.get(key, key.replace("_", " ").title()),
            "count": count,
        }
        for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def families() -> list[dict[str, Any]]:
    counts: dict[str, int] = {}
    for item in _catalog().engines.values():
        counts[item.family] = counts.get(item.family, 0) + 1
    return [
        {"id": key, "count": count}
        for key, count in sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
    ]


def search(*, category: str = "", family: str = "", text: str = "") -> list[Engine]:
    """Filter engines by category, family and a free-text match on id, name and notes."""
    needle = text.strip().lower()
    result = []
    for item in _catalog().engines.values():
        if category and item.category != category:
            continue
        if family and item.family.lower() != family.lower():
            continue
        if (
            needle
            and needle
            not in " ".join(
                (item.id, item.name, item.description, item.family, item.aira_use)
            ).lower()
        ):
            continue
        result.append(item)
    return result


def credit_cost(
    engine_id: str,
    options: Mapping[str, Any] | None = None,
    params: Mapping[str, Any] | None = None,
) -> int:
    """Searches a live (uncached, successful) call consumes.

    SerpApi bills one search per successful call regardless of result size
    (platform.json billing_rules). The one documented exception is the
    ``extract`` engine in ``crawl`` mode, which bills one credit per page and
    so costs ``max_pages`` (default 10, maximum 100). Cache hits cost nothing;
    the service applies that, not this function. ``options`` is accepted for
    forward compatibility: no run option changes the price.
    """
    found = engine(engine_id)
    if found.provider == "aira":
        return 0
    values = params or {}
    if found.id == "extract" and str(values.get("mode", "")) == "crawl":
        try:
            pages = int(values.get("max_pages", 10))
        except (TypeError, ValueError):
            pages = 10
        return max(1, min(pages, 100))
    return 1


# ---------------------------------------------------------------------------
# Parameter validation
# ---------------------------------------------------------------------------


def _coerce_text(value: Any, label: str) -> str:
    if isinstance(value, bool):
        text = "true" if value else "false"
    elif isinstance(value, (str, int, float)):
        text = str(value).strip()
    else:
        raise ValueError(f"{label} must be text.")
    if len(text) > MAX_TEXT_LENGTH:
        raise ValueError(f"{label} is longer than {MAX_TEXT_LENGTH:,} characters.")
    return text


def _coerce_int(value: Any, label: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a whole number.")
    if isinstance(value, int):
        return value
    if isinstance(value, float) and math.isfinite(value) and value.is_integer():
        return int(value)
    if isinstance(value, str) and _INT_RE.match(value.strip()):
        return int(value.strip())
    raise ValueError(f"{label} must be a whole number.")


def _coerce_number(value: Any, label: str) -> int | float:
    if isinstance(value, bool):
        raise ValueError(f"{label} must be a number.")
    try:
        number = float(value.strip()) if isinstance(value, str) else float(value)
    except (TypeError, ValueError, AttributeError):
        raise ValueError(f"{label} must be a number.") from None
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number.")
    return int(number) if number.is_integer() and abs(number) < 1e15 else number


def _coerce_bool(value: Any, label: str) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "1", "yes", "on"}:
        return True
    if text in {"false", "0", "no", "off"}:
        return False
    raise ValueError(f"{label} must be true or false.")


def _coerce_date(value: Any, label: str, compact: bool = False) -> str:
    """A real date, sent in the form the engine documents.

    Most engines take ISO dates; a few (Google Images, Forums, the Ads
    Transparency Center) document ``YYYYMMDD``. Those accept either spelling
    (the form's date picker emits ISO) and the value is sent in the engine's
    own form; ISO engines accept only ISO, so an ambiguous digit run is refused.
    """
    if isinstance(value, datetime):
        parsed: date | None = value.date()
    elif isinstance(value, date):
        parsed = value
    else:
        text = str(value).strip()
        parsed = None
        try:
            if _DATE_RE.match(text):
                parsed = date.fromisoformat(text)
            elif compact and _COMPACT_DATE_RE.match(text):
                parsed = datetime.strptime(text, "%Y%m%d").date()
        except ValueError:
            parsed = None
    if parsed is None:
        wanted = "YYYYMMDD or YYYY-MM-DD" if compact else "YYYY-MM-DD"
        raise ValueError(f"{label} must be a real date in {wanted} form.")
    return parsed.strftime("%Y%m%d") if compact else parsed.isoformat()


def _option_hint(options: tuple[Option, ...]) -> str:
    values = [item.value for item in options]
    shown = ", ".join(values[:12])
    return f"{shown}, ..." if len(values) > 12 else shown


def _coerce_enum(value: Any, spec: Param, label: str) -> str:
    text = _coerce_text(value, label)
    if not text:
        return text
    if spec.closed_options and text not in {item.value for item in spec.options}:
        raise ValueError(f"{label} must be one of: {_option_hint(spec.options)}.")
    return text


def _coerce_csv(value: Any, spec: Param, label: str) -> str:
    if isinstance(value, (list, tuple, set, frozenset)):
        raw_items = list(value)
    elif isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        raw_items = [value]
    else:
        raise ValueError(f"{label} must be a comma-separated list.")
    items = [_coerce_text(item, label) for item in raw_items]
    items = [item for item in items if item]
    if len(items) > MAX_CSV_ITEMS:
        raise ValueError(f"{label} has more than {MAX_CSV_ITEMS} entries.")
    if spec.closed_options:
        allowed = {item.value for item in spec.options}
        bad = [item for item in items if item not in allowed]
        if bad:
            raise ValueError(
                f"{label} has unsupported value(s) {', '.join(bad[:4])}; "
                f"use any of: {_option_hint(spec.options)}."
            )
    return ",".join(items)


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, tuple, set, frozenset)):
        return not any(not _is_blank(item) for item in value)
    return False


def _coerce(value: Any, spec: Param) -> Any:
    label = f"'{spec.name}'"
    kind = spec.type
    if kind == "integer":
        return _coerce_int(value, label)
    if kind == "number":
        return _coerce_number(value, label)
    if kind == "boolean":
        return _coerce_bool(value, label)
    if kind == "date":
        return _coerce_date(value, label, compact="YYYYMMDD" in (spec.help or ""))
    if kind == "csv":
        return _coerce_csv(value, spec, label)
    if kind == "enum":
        return _coerce_enum(value, spec, label)
    return _coerce_text(value, label)


def validate_params(
    engine_id: str,
    params: Mapping[str, Any] | None,
    *,
    allow_unknown: bool = False,
) -> tuple[dict[str, Any], list[str]]:
    """Check request parameters against the engine definition.

    Returns ``(clean, errors)``. ``clean`` holds only values that were present
    and valid, coerced to canonical Python types (integers, floats, booleans,
    ISO dates, comma-joined csv text); blank values count as absent so a form
    can submit every field. Defaults are never injected: SerpApi applies its
    own, and storing only what the caller chose keeps cache keys stable.
    ``errors`` is empty when the call may proceed. Unknown parameters are errors
    unless ``allow_unknown`` is true, in which case scalar values pass through
    unchanged.
    """
    found = engine(engine_id)
    if params is None:
        params = {}
    if not isinstance(params, Mapping):
        return {}, ["params must be an object of parameter names to values."]
    clean: dict[str, Any] = {}
    errors: list[str] = []
    invalid: set[str] = set()
    for raw_name, value in params.items():
        name = str(raw_name)
        spec = found.param(name)
        if spec is None:
            if name in RESERVED_PARAMS:
                errors.append(
                    f"'{name}' is not an engine parameter. {RESERVED_PARAMS[name]}"
                )
            elif not allow_unknown:
                close = difflib.get_close_matches(
                    name, [item.name for item in found.params], n=1
                )
                hint = f" Did you mean '{close[0]}'?" if close else ""
                errors.append(f"Unknown parameter '{name[:60]}' for {found.id}.{hint}")
            elif not _PARAM_NAME_RE.match(name):
                errors.append(f"Invalid parameter name '{name[:60]}'.")
            elif not _is_blank(value):
                try:
                    clean[name] = _coerce_text(value, f"'{name}'")
                except ValueError as error:
                    errors.append(str(error))
            continue
        if _is_blank(value):
            continue
        try:
            coerced = _coerce(value, spec)
        except ValueError as error:
            errors.append(str(error))
            invalid.add(name)
            continue
        if coerced != "":
            clean[name] = coerced
    grouped = {name for group in found.requires_any_of for name in group}
    for spec in found.params:
        if (
            spec.required
            and spec.name not in clean
            and spec.name not in invalid
            and spec.name not in grouped
        ):
            errors.append(f"'{spec.name}' is required ({spec.label}).")
    for group in found.requires_any_of:
        if any(name in clean or name in invalid for name in group):
            continue
        errors.append(
            f"'{group[0]}' is required."
            if len(group) == 1
            else f"Provide at least one of: {', '.join(group)}."
        )
    if "location" in clean and "uule" in clean:
        errors.append("'location' cannot be combined with 'uule'; use one of them.")
    return clean, errors
