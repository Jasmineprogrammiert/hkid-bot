"""Fixtures shared by the suite, and the reason it can find `watcher` at all:
pytest puts the directory holding the root conftest on sys.path.

Nothing here touches the network or the real state file."""

import pytest

from watcher import state as state_module


@pytest.fixture(autouse=True)
def isolated_state(tmp_path, monkeypatch):
    """Point every state write at a throwaway file.

    record_failure() and save_state() write to disk as a side effect. Without
    this, a test run would overwrite the state.json of a live watcher.
    """
    path = tmp_path / "state.json"
    monkeypatch.setattr(state_module, "STATE", path)
    return path


@pytest.fixture
def make_feed():
    """Build a feed payload from (office, date, quota) triples."""
    def build(*cells, stamp="08/29/2026 12:00:00"):
        return {"lastUpdateTime": stamp,
                "data": [{"officeId": o, "date": d, "quotaR": q} for o, d, q in cells]}
    return build


@pytest.fixture
def cfg():
    """The strictest settings: watched offices only, green only, before the date."""
    return {"target_date": "2026-10-09", "target_time": "09:00",
            "offices": ["RHK", "RKO"], "include_almost_full": False,
            "include_target_day": False, "heartbeat_hours": 8,
            "ntfy_topic": "t", "ntfy_server": "https://ntfy.example",
            "healthcheck_url": ""}