"""Engine catalog: loading, validation and parameter checking for every engine."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from aira.airaapi import catalog, compat
from aira.airaapi.catalog import CatalogError, PARAM_TYPES


def _write(directory: Path, name: str, payload) -> None:
    (directory / name).write_text(json.dumps(payload), encoding="utf-8")


def _engine(engine_id="demo", **overrides):
    entry = {
        "id": engine_id,
        "name": "Demo",
        "category": "web",
        "family": "Demo",
        "description": "A demo engine.",
        "docs_url": "https://example.com/docs",
        "params": [
            {"name": "q", "label": "Query", "type": "string", "required": True},
        ],
        "requires_any_of": [],
        "pagination": {"kind": "none"},
        "result_keys": ["organic_results"],
        "result_item_fields": {},
        "aira_use": "",
        "verified": "docs",
    }
    entry.update(overrides)
    return entry


# -- the shipped catalog --------------------------------------------------------


def test_shipped_catalog_counts_and_flags():
    engines = catalog.engines()
    # 136 SerpApi engines (126 official plus 10 sub-endpoints) and Aira's own aira_jobs
    assert len(engines) == 137
    assert len(set(engines)) == 137
    assert {key for key, value in engines.items() if value.provider == "aira"} == {
        "aira_jobs"
    }
    unlisted = {key for key, value in engines.items() if value.unlisted}
    assert len(unlisted) == 10 and "google_jobs_listing" in unlisted
    assert engines["google_scholar_profiles"].deprecated
    assert engines["google_scholar_profiles"].deprecated_reason
    assert sum(item["count"] for item in catalog.categories()) == 137
    assert {item["id"] for item in catalog.categories()} >= {"jobs", "travel", "web"}
    assert sum(item["count"] for item in catalog.families()) == 137


def test_every_engine_validates_structurally():
    for found in catalog.engines().values():
        assert found.name and found.docs_url.startswith("https://")
        assert found.endpoint.endswith("/search.json") or found.provider == "aira"
        names = [spec.name for spec in found.params]
        assert len(names) == len(set(names)), found.id
        for spec in found.params:
            assert spec.type in PARAM_TYPES, (found.id, spec.name)
            assert spec.label
        for group in found.requires_any_of:
            assert group and all(name in names for name in group), found.id
        # One engine (google_sports) has no documented result keys; that is data, not an error.
        assert found.result_keys or found.id == "google_sports", found.id
        summary = found.summary()
        assert summary["param_count"] == len(found.params)
        detail = found.to_dict()
        assert detail["params"][0]["name"] == found.params[0].name
        json.dumps(detail)


def test_every_param_type_is_covered_and_handled():
    seen = {spec.type for found in catalog.engines().values() for spec in found.params}
    assert seen == set(PARAM_TYPES)


def _sample(spec: catalog.Param):
    if spec.type == "enum":
        return spec.options[0].value if spec.options else "free"
    if spec.type == "integer":
        return 3
    if spec.type == "number":
        return 2.5
    if spec.type == "boolean":
        return True
    if spec.type == "csv":
        return spec.options[0].value if spec.closed_options else "a,b"
    if spec.type == "date":
        return "2026-12-01"
    if spec.type == "location":
        return "Austin, Texas, United States"
    return "value"


def test_every_parameter_of_every_engine_accepts_a_valid_value():
    for found in catalog.engines().values():
        params = {
            spec.name: _sample(spec) for spec in found.params if spec.name != "uule"
        }
        clean, errors = catalog.validate_params(found.id, params)
        assert errors == [], (found.id, errors)
        assert set(clean) == set(params), found.id


def test_generated_sample_params_are_valid_for_every_engine():
    for found in catalog.engines().values():
        sample = compat.sample_params(found.id, date(2026, 10, 22))
        clean, errors = catalog.validate_params(found.id, sample)
        assert errors == [], (found.id, sample, errors)
        assert clean, found.id


def test_catalog_lint_parameters_are_consistent():
    """Structural lint over every shipped engine (types, enums, groups, pagination)."""
    for found in catalog.engines().values():
        names = {spec.name for spec in found.params}
        alternatives = {
            name for group in found.requires_any_of if len(group) > 1 for name in group
        }
        for spec in found.params:
            where = (found.id, spec.name)
            if spec.type == "enum":
                assert spec.options, where
            if (
                spec.type == "enum"
                and spec.closed_options
                and spec.default not in (None, "")
            ):
                assert str(spec.default) in {item.value for item in spec.options}, where
            # A parameter that is one of several alternatives is not also "required":
            # the form would show both a required star and a "one of" badge.
            assert not (spec.required and spec.name in alternatives), where
        pagination = found.pagination
        for key in ("param", "prev_param"):
            if pagination.get(key):
                assert pagination[key] in names, (found.id, key, pagination[key])
        # search_metadata and search_parameters are the envelope of every body, not result data.
        assert not {"search_metadata", "search_parameters"} & set(found.result_keys), (
            found.id
        )
        if pagination.get("kind", "none") != "none":
            assert pagination.get("param"), found.id


def _documented_date(spec: catalog.Param) -> str:
    compact = "YYYYMMDD" in spec.help
    return "20261205" if compact else "2026-12-05"


def test_date_parameters_accept_the_format_their_docs_state():
    for found in catalog.engines().values():
        for spec in found.params:
            if spec.type != "date":
                continue
            clean, errors = catalog.validate_params(
                found.id, {spec.name: _documented_date(spec)}, allow_unknown=False
            )
            assert not [e for e in errors if spec.name in e and "real date" in e], (
                found.id,
                spec.name,
                errors,
            )


def test_unknown_engine_suggests_close_matches():
    with pytest.raises(LookupError, match="google_jobs"):
        catalog.engine("google_job")
    with pytest.raises(LookupError):
        catalog.engine("")


def test_search_filters_by_category_family_and_text():
    assert {item.id for item in catalog.search(category="jobs")} - {"aira_jobs"} == {
        "google_jobs",
        "google_jobs_listing",
    }
    assert all(item.family == "Bing" for item in catalog.search(family="bing"))
    assert [item.id for item in catalog.search(text="flights")]
    assert catalog.search(text="zzzz-no-such-engine") == []


def test_common_parameters_and_platform_documents_are_exposed():
    names = {item["name"] for item in catalog.common_parameters()}
    assert {"api_key", "engine", "output", "no_cache", "async"} <= names
    assert catalog.platform()["errors"]


# -- validate_params ----------------------------------------------------------


def test_validate_requires_q_and_drops_blank_values():
    clean, errors = catalog.validate_params("google_jobs", {"q": "  ", "gl": ""})
    assert clean == {}
    assert errors == ["'q' is required."]
    clean, errors = catalog.validate_params(
        "google_jobs", {"q": " barista ", "hl": None, "gl": "  "}
    )
    assert clean == {"q": "barista"} and errors == []


def test_validate_rejects_unknown_parameters_with_a_hint():
    clean, errors = catalog.validate_params("google_jobs", {"q": "x", "locaton": "y"})
    assert clean == {"q": "x"}
    assert errors == [
        "Unknown parameter 'locaton' for google_jobs. Did you mean 'location'?"
    ]


def test_validate_allow_unknown_passes_scalars_through():
    clean, errors = catalog.validate_params(
        "google_jobs", {"q": "x", "brand_new": 5, "also new": "z"}, allow_unknown=True
    )
    assert clean["brand_new"] == "5"
    assert any("Invalid parameter name" in error for error in errors)
    clean, errors = catalog.validate_params(
        "google_jobs", {"q": "x", "brand_new": {"nested": 1}}, allow_unknown=True
    )
    assert any("must be text" in error for error in errors)


@pytest.mark.parametrize("name", sorted(catalog.RESERVED_PARAMS))
def test_reserved_envelope_names_are_never_parameters(name):
    clean, errors = catalog.validate_params("google", {"q": "x", name: "secret-value"})
    assert name not in clean
    assert any(f"'{name}' is not an engine parameter" in error for error in errors)
    assert not any("secret-value" in error for error in errors)
    _, errors = catalog.validate_params(
        "google", {"q": "x", name: "secret-value"}, allow_unknown=True
    )
    assert any(f"'{name}' is not an engine parameter" in error for error in errors)


def test_integer_coercion():
    ok, errors = catalog.validate_params("google", {"q": "x", "start": "20"})
    assert ok["start"] == 20 and errors == []
    ok, _ = catalog.validate_params("google", {"q": "x", "start": 20.0})
    assert ok["start"] == 20
    for bad in ("2.5", True, "ten", 2.5, [1], float("nan")):
        _, errors = catalog.validate_params("google", {"q": "x", "start": bad})
        assert errors == ["'start' must be a whole number."], bad


def test_number_coercion_and_canonical_form():
    clean, errors = catalog.validate_params(
        "google", {"q": "x", "lat": "40.5", "lon": 3.0}
    )
    assert clean["lat"] == 40.5 and clean["lon"] == 3 and errors == []
    for bad in ("north", True, float("inf"), "nan"):
        _, errors = catalog.validate_params("google", {"q": "x", "lat": bad})
        assert errors, bad


def test_boolean_coercion():
    for raw, expected in (("true", True), ("0", False), ("Yes", True), (False, False)):
        clean, errors = catalog.validate_params(
            "google_flights",
            {"departure_id": "AUS", "arrival_id": "JFK", "show_hidden": raw},
        )
        assert clean["show_hidden"] is expected and errors == []
    _, errors = catalog.validate_params(
        "google_flights",
        {"departure_id": "AUS", "arrival_id": "JFK", "show_hidden": "maybe"},
    )
    assert errors == ["'show_hidden' must be true or false."]


def test_date_validation():
    base = {"q": "paris", "check_out_date": "2026-12-05"}
    clean, errors = catalog.validate_params(
        "google_hotels", {**base, "check_in_date": date(2026, 12, 1)}
    )
    assert clean["check_in_date"] == "2026-12-01" and errors == []
    for bad in ("2026-13-01", "12/01/2026", "2026-W49-1", "20261201", "2026-02-30"):
        _, errors = catalog.validate_params(
            "google_hotels", {**base, "check_in_date": bad}
        )
        assert any("YYYY-MM-DD" in error for error in errors), bad


def test_enum_validation_lists_choices():
    clean, errors = catalog.validate_params("google", {"q": "x", "device": "tablet"})
    assert clean["device"] == "tablet" and errors == []
    _, errors = catalog.validate_params("google", {"q": "x", "device": "watch"})
    assert errors == ["'device' must be one of: desktop, tablet, mobile."]


def test_truncated_enum_accepts_free_text():
    spec = catalog.engine("google").param("tbm")
    assert spec is not None and spec.options_truncated
    clean, errors = catalog.validate_params("google", {"q": "x", "tbm": "lcl"})
    assert clean["tbm"] == "lcl" and errors == []


def test_csv_accepts_lists_and_strings_and_checks_closed_options():
    clean, _ = catalog.validate_params(
        "google_flights",
        {"departure_id": ["AUS", " JFK ", ""], "arrival_id": "LHR, CDG"},
    )
    assert clean["departure_id"] == "AUS,JFK" and clean["arrival_id"] == "LHR,CDG"
    spec = catalog.engine("google_maps_directions").param("avoid")
    assert spec is not None and spec.closed_options
    first = spec.options[0].value
    clean, errors = catalog.validate_params(
        "google_maps_directions",
        {"start_addr": "A", "end_addr": "B", "avoid": [first]},
    )
    assert clean["avoid"] == first and errors == []
    _, errors = catalog.validate_params(
        "google_maps_directions",
        {"start_addr": "A", "end_addr": "B", "avoid": "teleporters"},
    )
    assert any("unsupported value" in error for error in errors)
    _, errors = catalog.validate_params("google_flights", {"departure_id": {"a": 1}})
    assert any("comma-separated" in error for error in errors)


def test_requires_any_of_groups():
    _, errors = catalog.validate_params("google_flights", {})
    assert errors == [
        "Provide at least one of: departure_id, multi_city_json, selected_flights_json.",
        "Provide at least one of: arrival_id, multi_city_json, selected_flights_json.",
    ]
    _, errors = catalog.validate_params("google_flights", {"multi_city_json": "[]"})
    assert errors == []
    # Alternatives that share a requires_one_of label are one group, not two.
    assert catalog.engine("yandex_images").requires_any_of == (("text", "url"),)
    for params in ({"text": "cat"}, {"url": "https://example.com/a.png"}):
        assert catalog.validate_params("yandex_images", params)[1] == []
    _, errors = catalog.validate_params("yandex_images", {})
    assert errors == ["Provide at least one of: text, url."]
    _, errors = catalog.validate_params("naver_ai_overview", {"page_token": "t"})
    assert errors == []
    # Individually required parameters stay required together.
    _, errors = catalog.validate_params(
        "google_maps_autocomplete", {"ll": "@40,-70,14z"}
    )
    assert errors == ["'q' is required."]
    _, errors = catalog.validate_params("google_maps_autocomplete", {})
    assert len(errors) == 2


def test_required_parameters_are_enforced():
    _, errors = catalog.validate_params("google_hotels", {"q": "paris"})
    assert errors == [
        "'check_in_date' is required (Check-in date).",
        "'check_out_date' is required (Check-out date).",
    ]


def test_invalid_value_is_reported_once_not_also_as_missing():
    _, errors = catalog.validate_params("google_jobs", {"q": {"x": 1}})
    assert errors == ["'q' must be text."]


def test_location_and_uule_conflict():
    _, errors = catalog.validate_params(
        "google", {"q": "x", "location": "Austin", "uule": "w+CAIQ"}
    )
    assert "'location' cannot be combined with 'uule'; use one of them." in errors


def test_text_length_cap_and_non_mapping_params():
    _, errors = catalog.validate_params("google", {"q": "x" * 30_000})
    assert any("longer than" in error for error in errors)
    clean, errors = catalog.validate_params("google", ["q"])  # type: ignore[arg-type]
    assert clean == {} and errors
    clean, errors = catalog.validate_params("google_jobs", None)
    assert clean == {} and errors == ["'q' is required."]


def test_validation_never_injects_defaults():
    clean, _ = catalog.validate_params("google", {"q": "x"})
    assert clean == {"q": "x"}


# -- credit cost ----------------------------------------------------------------


def test_credit_cost_is_one_search_except_crawl():
    assert catalog.credit_cost("google", {}) == 1
    assert catalog.credit_cost("google_maps_reviews", None) == 1
    assert catalog.credit_cost("extract", {}, {"mode": "scrape"}) == 1
    assert catalog.credit_cost("extract", {}, {"mode": "crawl"}) == 10
    assert catalog.credit_cost("extract", {}, {"mode": "crawl", "max_pages": 25}) == 25
    assert (
        catalog.credit_cost("extract", {}, {"mode": "crawl", "max_pages": 900}) == 100
    )
    assert catalog.credit_cost("extract", {}, {"mode": "crawl", "max_pages": "x"}) == 10
    with pytest.raises(LookupError):
        catalog.credit_cost("nope", {})


# -- loader tolerance and strictness ----------------------------------------------


def test_loader_tolerates_documentation_quirks(tmp_path):
    _write(tmp_path, "platform.json", {"common_parameters": [{"name": "api_key"}]})
    entry = _engine(
        params=[
            {
                "name": "q",
                "label": "Query",
                "type": "string",
                "required": True,
                "extra": 1,
            },
            {"name": "mode", "type": "enum"},
            {
                "name": "region",
                "type": "enum",
                "options": [{"value": 1, "label": "One"}, {"value": "1"}, "two", True],
                "options_truncated": True,
            },
            {"name": "flags", "type": "csv", "options": ["a", "b"]},
            {"name": "on", "type": "boolean", "options": [{"value": "true"}]},
        ],
        unlisted=True,
        deprecated=True,
        endpoint="",
    )
    _write(tmp_path, "extra_one.json", {"engines": [entry], "ignored": True})
    loaded = catalog.load_catalog(tmp_path)
    found = loaded.engines["demo"]
    assert found.unlisted and found.deprecated
    assert found.endpoint == "https://serpapi.com/search.json"
    mode = found.param("mode")
    assert mode is not None and not mode.options and not mode.closed_options
    region = found.param("region")
    assert region is not None
    assert [item.value for item in region.options] == ["1", "two", "true"]
    assert not region.closed_options
    assert loaded.platform["common_parameters"] == [{"name": "api_key"}]


@pytest.mark.parametrize(
    "mutate,message",
    [
        (lambda e: e.update(id="Bad Id"), "invalid engine id"),
        (lambda e: e.update(name=""), "missing name"),
        (lambda e: e.update(params="nope"), "params must be a list"),
        (
            lambda e: e["params"].append({"name": "q", "type": "string"}),
            "duplicate parameter",
        ),
        (
            lambda e: e["params"].append({"name": "z", "type": "wizard"}),
            "unknown parameter type",
        ),
        (
            lambda e: e["params"].append({"name": "9bad", "type": "string"}),
            "invalid parameter name",
        ),
        (lambda e: e.update(requires_any_of=[["ghost"]]), "unknown parameters"),
        (lambda e: e.update(requires_any_of=[[]]), "empty group"),
        (lambda e: e.update(result_item_fields=["x"]), "result_item_fields"),
        (
            lambda e: e["params"].append({"name": "o", "type": "enum", "options": "x"}),
            "options must be a list",
        ),
    ],
)
def test_loader_rejects_structural_errors(tmp_path, mutate, message):
    entry = _engine()
    mutate(entry)
    _write(tmp_path, "one.json", {"engines": [entry]})
    with pytest.raises(CatalogError, match=message):
        catalog.load_catalog(tmp_path)


def test_loader_rejects_duplicates_empty_and_broken_files(tmp_path):
    _write(tmp_path, "a.json", {"engines": [_engine("dup")]})
    _write(tmp_path, "b.json", {"engines": [_engine("dup")]})
    with pytest.raises(CatalogError, match="Duplicate engine id"):
        catalog.load_catalog(tmp_path)
    empty = tmp_path / "empty"
    empty.mkdir()
    with pytest.raises(CatalogError, match="empty"):
        catalog.load_catalog(empty)
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "x.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CatalogError, match="cannot read"):
        catalog.load_catalog(broken)
    wrong = tmp_path / "wrong"
    wrong.mkdir()
    _write(wrong, "x.json", [1, 2])
    with pytest.raises(CatalogError, match="expected a JSON object"):
        catalog.load_catalog(wrong)
    noengines = tmp_path / "noengines"
    noengines.mkdir()
    _write(noengines, "x.json", {"things": []})
    with pytest.raises(CatalogError, match="engines list"):
        catalog.load_catalog(noengines)
