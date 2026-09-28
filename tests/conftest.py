from __future__ import annotations

from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO


@pytest.fixture(scope="session")
def settings():
    from osworld_science.config import Settings
    return Settings.load(REPO)


@pytest.fixture(scope="session")
def taskset(settings):
    from osworld_science.tasks.registry import TaskSet
    ts = TaskSet(settings.data_dir)
    if len(ts) == 0:
        pytest.skip("no task definitions under data/<domain>/tasks (scripts/data_prep/hf_download.py)")
    return ts


@pytest.fixture(scope="session")
def layout(settings):
    from osworld_science.data.layout import DataLayout
    return DataLayout(settings.data_dir)


def pytest_collection_modifyitems(config, items):
    """`vm` tests never run by default; `data` tests are skipped when data/ is absent."""
    from osworld_science.config import Settings
    s = Settings.load(REPO)
    from osworld_science.data.layout import DataLayout
    layout = DataLayout(s.data_dir)
    has_public = has_private = any(layout.has_data(d) for d in layout.available_domains())
    for item in items:
        if "vm" in item.keywords and not config.getoption("-m", default=""):
            item.add_marker(pytest.mark.skip(reason="needs a running guest (select with -m vm)"))
        if "data" in item.keywords and not (has_public and has_private):
            item.add_marker(pytest.mark.skip(reason="needs data/<domain>/{public,private} (hf_download.py)"))
