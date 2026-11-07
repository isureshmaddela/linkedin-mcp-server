"""One folder per app: every file a page asks for as /static/<name> is found, wherever it lives."""

from __future__ import annotations

import re
from pathlib import Path

from aira import webfiles

ROOT = Path(__file__).resolve().parents[2]
PAGES = [
    webfiles.SHELL / "index.html",
    webfiles.SHELL / "public.html",
    webfiles.SHELL / "copyright.html",
    ROOT / "monosage_cloud" / "routes" / "public.py",
]


def test_every_static_file_a_page_names_is_served():
    for page in PAGES:
        for name in re.findall(
            r"/static/([A-Za-z0-9_.-]+\.(?:js|css))", page.read_text(encoding="utf-8")
        ):
            found = webfiles.find(name)
            assert found is not None and found.is_file(), (page.name, name)


def test_each_app_keeps_its_screens_in_its_own_folder():
    index = webfiles.index(refresh=True)
    assert (
        index["views-shop.js"]
        == ROOT / "aira" / "suite" / "shop" / "web" / "views-shop.js"
    )
    assert index["views-leads.js"].parent == ROOT / "aira" / "leads" / "web"
    assert index["app.js"].parent == webfiles.SHELL  # the shell stays where it was
    # an app's screens are not also left behind in the shell (one copy of each file)
    for folder in webfiles.app_dirs():
        for path in folder.iterdir():
            assert not (webfiles.SHELL / path.name).exists(), path.name


def test_only_indexed_files_can_be_served():
    assert webfiles.find("../pyproject.toml") is None
    assert webfiles.find("auto_apply_assistant.py") is None
    assert webfiles.find("does-not-exist.js") is None


def test_the_translation_catalog_reads_every_apps_screens():
    scripts = {path.name for path in webfiles.scripts()}
    assert {"views-shop.js", "views-social-dating.js", "app.js"} <= scripts
