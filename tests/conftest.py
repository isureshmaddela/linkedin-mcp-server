import inspect
import os
import re
import sys
from functools import cache

import pytest

os.environ.setdefault(
    "AIRA_SUITE_STRICT", "1"
)  # a broken industry app fails the tests, never hides
os.environ.setdefault(
    "AIRA_MARKET_PROVIDER", "stub"
)  # Stocks: seeded offline prices; no test reaches Yahoo Finance

from cloud_support import cloud  # noqa: F401  (the hosted-shell fixture, available to every test module)

# --- markers -------------------------------------------------------------------------------------------------------
# `browser` is applied automatically to every test that drives a real Chromium, so `pytest -m "not browser"` is the
# quick run that needs no browser install, and CI runs the two halves as separate jobs. A test drives Chromium when it,
# one of its fixtures, or a plain helper function in its module calls the patchright launcher
# (`sync_playwright()`, `async_playwright()`, `.chromium.launch...`) from a module that imports the real launcher.
# Upstream tests that only mock patchright never import the launcher, so they stay in the quick run.

_LAUNCHER_IMPORT = re.compile(
    r"importorskip\(\s*[\"'](?:patchright|playwright)\.(?:sync|async)_api"
    r"|from (?:patchright|playwright)\.(?:sync|async)_api import [^\n#]*\b(?:sync|async)_playwright\b"
)
_LAUNCH_CALL = re.compile(r"\b(?:sync|async)_playwright\(\)|\.chromium\.launch")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "browser: drives a real Chromium through patchright (applied automatically; deselect with -m 'not browser')",
    )
    config.addinivalue_line(
        "markers",
        "slow: takes long enough to leave out of a quick run (deselect with -m 'not slow')",
    )


@cache
def _module_imports_launcher(module_name: str) -> bool:
    module = sys.modules.get(module_name)
    if module is None:
        return False
    try:
        source = inspect.getsource(module)
    except (OSError, TypeError):
        return False
    return bool(_LAUNCHER_IMPORT.search(source))


@cache
def _code_launches(code, module_name: str) -> bool:
    if not _module_imports_launcher(module_name):
        return False
    try:
        source = inspect.getsource(code)
    except (OSError, TypeError):
        return False
    return bool(_LAUNCH_CALL.search(source))


def _launches(function) -> bool:
    function = inspect.unwrap(function)
    code = getattr(function, "__code__", None)
    module_name = getattr(function, "__module__", None)
    return bool(code and module_name) and _code_launches(code, module_name)


@cache
def _module_has_launch_helper(module_name: str) -> bool:
    """A plain (non-test, non-fixture) function in the module's namespace launches Chromium, so any test there may."""
    module = sys.modules.get(module_name)
    if module is None:
        return False
    return any(
        inspect.isfunction(value) and not name.startswith("test") and _launches(value)
        for name, value in vars(module).items()
    )


def _drives_browser(item) -> bool:
    function = getattr(item, "function", None)
    if function is None:
        return False
    if _launches(function):
        return True
    module = getattr(item, "module", None)
    if module is not None and _module_has_launch_helper(module.__name__):
        return True
    fixture_info = getattr(item, "_fixtureinfo", None)
    if fixture_info is None:
        return False
    return any(
        _launches(fixturedef.func)
        for name in item.fixturenames
        for fixturedef in fixture_info.name2fixturedefs.get(name, ())
    )


def pytest_collection_modifyitems(config, items):  # noqa: ARG001
    for item in items:
        if item.get_closest_marker("browser") is None and _drives_browser(item):
            item.add_marker(pytest.mark.browser)


@pytest.fixture(autouse=True)
def reset_singletons():
    """Reset global state for test isolation."""
    from linkedin_mcp_server.bootstrap import reset_bootstrap_for_testing
    from linkedin_mcp_server.config import reset_config
    from linkedin_mcp_server.drivers.browser import reset_browser_for_testing

    reset_bootstrap_for_testing()
    reset_browser_for_testing()
    reset_config()
    yield
    reset_bootstrap_for_testing()
    reset_browser_for_testing()
    reset_config()


@pytest.fixture(autouse=True)
def isolate_account_link(monkeypatch):
    """The local .env may link this checkout to an account; tests opt in to that themselves.

    Empty rather than removed: the app calls load_dotenv on import, which never overrides a variable that is set.
    """
    for name in ("AIRA_ACCOUNT_URL", "AIRA_ACCOUNT_SECRET", "AIRA_ACCOUNT_ALLOW_LOCAL"):
        monkeypatch.setenv(name, "")


@pytest.fixture(autouse=True)
def isolate_profile_dir(tmp_path, monkeypatch):
    """Redirect profile directory to tmp_path via config and DEFAULT_PROFILE_DIR."""
    fake_profile = tmp_path / "profile"
    monkeypatch.setenv("USER_DATA_DIR", str(fake_profile))

    # Patch DEFAULT_PROFILE_DIR for any code still referencing the constant
    for module in [
        "linkedin_mcp_server.drivers.browser",
        "linkedin_mcp_server.authentication",
        "linkedin_mcp_server.cli_main",
        "linkedin_mcp_server.setup",
        "linkedin_mcp_server.session_state",
    ]:
        try:
            monkeypatch.setattr(f"{module}.DEFAULT_PROFILE_DIR", fake_profile)
        except AttributeError:
            pass  # Module may not be imported yet

    # Patch get_profile_dir() in all modules that import it
    for gp_module in [
        "linkedin_mcp_server.drivers.browser",
        "linkedin_mcp_server.authentication",
        "linkedin_mcp_server.cli_main",
        "linkedin_mcp_server.setup",
    ]:
        try:
            monkeypatch.setattr(f"{gp_module}.get_profile_dir", lambda: fake_profile)
        except AttributeError:
            pass

    try:
        monkeypatch.setattr(
            "linkedin_mcp_server.session_state.get_source_profile_dir",
            lambda: fake_profile,
        )
    except AttributeError:
        pass

    for source_module in [
        "linkedin_mcp_server.authentication",
        "linkedin_mcp_server.drivers.browser",
        "linkedin_mcp_server.debug_trace",
        "linkedin_mcp_server.error_diagnostics",
    ]:
        try:
            monkeypatch.setattr(
                f"{source_module}.get_source_profile_dir",
                lambda: fake_profile,
            )
        except AttributeError:
            pass

    return fake_profile


@pytest.fixture
def profile_dir(isolate_profile_dir):
    """Create a non-empty profile directory."""
    isolate_profile_dir.mkdir(parents=True, exist_ok=True)
    # Create a marker file so profile_exists() returns True
    (isolate_profile_dir / "Default" / "Cookies").parent.mkdir(
        parents=True, exist_ok=True
    )
    (isolate_profile_dir / "Default" / "Cookies").write_text("placeholder")
    return isolate_profile_dir


@pytest.fixture
def mock_context():
    """Mock FastMCP Context."""
    from unittest.mock import AsyncMock, MagicMock

    ctx = MagicMock()
    ctx.report_progress = AsyncMock()
    return ctx


@pytest.fixture(autouse=True)
def _drop_postgres_databases():
    """With AIRA_TEST_DATABASE_URL set, every hosted-shell test gets its own PostgreSQL database; drop it afterwards."""
    import pg_support

    earlier = frozenset(
        pg_support._created
    )  # made by a module or session fixture: not this test's to drop
    yield
    if pg_support.enabled():
        pg_support.drop_created(keep=earlier)


def pytest_sessionfinish(session, exitstatus):  # noqa: ARG001
    import pg_support

    if pg_support.enabled():
        pg_support.drop_created()
        pg_support.drop_template()


@pytest.fixture(autouse=True)
def _reset_public_link_index():
    """A hosted test installs its organization index and public address for public links; the next test starts
    from the single-user build's defaults (in production the two builds are separate processes)."""
    yield
    from aira import suite
    from aira.suite import public

    public.reset_index()
    suite.set_public_origin(None)
    suite.set_actor_resolver(None)
    suite.set_app_filter(None)
    from aira.suite import kit

    kit.set_event_listener(None)


@pytest.fixture(autouse=True)
def _isolate_social_database(tmp_path):
    """Social has one database per server, outside every account: a test never reaches the real one."""
    from aira.suite.social import db as social_db

    social_db.set_path(lambda: tmp_path / "social.sqlite3")
    yield
    social_db.reset_path()


@pytest.fixture(autouse=True)
def _isolate_videos(tmp_path, monkeypatch):
    """Videos (and Ads) have one database and one media folder per server: a test never reaches the real ones."""
    from aira.suite.videos import db as videos_db
    from aira.suite.videos import media as videos_media

    monkeypatch.setenv("AIRA_MEDIA_DIR", "")
    videos_db.set_path(lambda: tmp_path / "videos.sqlite3")
    videos_media.set_root(lambda: tmp_path / "media")
    yield
    videos_db.reset_path()
    videos_media.reset_root()
    videos_media.set_quota(None)
    videos_media.show_paths = True
