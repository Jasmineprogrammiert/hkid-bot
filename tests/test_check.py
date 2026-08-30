"""The run itself: what gets pushed, and what gets recorded as pushed.

The rollback is the subtle one. state.json is what stops a slot being reported
twice, so writing a key into it means 'you have been told about this'. If the
push failed, that is a lie, and the alert is lost silently -- the next run would
see the key and stay quiet."""

import pytest

import check as check_module
from watcher.feed import OPEN_FULL

REPORTED = "RHK|2026-10-01"
NEW = "RKO|2026-10-02"


@pytest.fixture
def run(monkeypatch, cfg, make_feed):
    """Drive main() with the network, the clock's side effects and disk removed."""
    def go(*, cells, seen=(), push_lands=True, argv=("check.py",)):
        saved = {}
        monkeypatch.setattr(check_module.sys, "argv", list(argv))
        monkeypatch.setattr(check_module, "load_config", lambda: cfg)
        monkeypatch.setattr(check_module, "load_state",
                            lambda: {"seen": list(seen), "stamps": [],
                                     "cooldown_until": None, "cooldown_len": 0,
                                     "fail_streak": 0, "last_success": None,
                                     "heartbeat_sent": False})
        monkeypatch.setattr(check_module, "fetch", lambda: make_feed(*cells))
        monkeypatch.setattr(check_module, "record_history", lambda feed: 0)
        monkeypatch.setattr(check_module, "notify",
                            lambda *a, **k: push_lands)
        monkeypatch.setattr(check_module, "save_state",
                            lambda state, keys, stamp: saved.update(keys=keys, stamp=stamp))
        return check_module.main(), saved
    return go


def test_a_delivered_alert_is_recorded_so_it_is_not_repeated(run):
    code, saved = run(cells=[("RHK", "10/01/2026", OPEN_FULL)])
    assert code == 0
    assert saved["keys"] == {REPORTED}


def test_a_failed_push_records_nothing_new(run):
    """The next run must retry, not conclude it already told you."""
    code, saved = run(cells=[("RKO", "10/02/2026", OPEN_FULL)], push_lands=False)
    assert code == 1
    assert saved["keys"] == set()


def test_a_failed_push_keeps_what_was_already_reported(run):
    """Rolling back must not also forget the alert that did land earlier --
    that would re-push an opening you have already been told about."""
    code, saved = run(cells=[("RHK", "10/01/2026", OPEN_FULL),
                             ("RKO", "10/02/2026", OPEN_FULL)],
                      seen=[REPORTED], push_lands=False)
    assert code == 1
    assert saved["keys"] == {REPORTED}


def test_a_slot_that_closed_is_dropped_from_state(run):
    """Only what currently qualifies is kept, so a slot that fills and later
    reopens alerts again -- which is wanted."""
    _, saved = run(cells=[("RKO", "10/02/2026", OPEN_FULL)], seen=[REPORTED])
    assert saved["keys"] == {NEW}


def test_nothing_open_pushes_nothing_and_still_succeeds(run):
    code, saved = run(cells=[("RHK", "10/20/2026", OPEN_FULL)])
    assert code == 0
    assert saved["keys"] == set()


def test_the_feed_stamp_is_recorded_for_stats(run):
    _, saved = run(cells=[("RHK", "10/01/2026", OPEN_FULL)])
    assert saved["stamp"] == "08/29/2026 12:00:00"


def test_a_stale_replica_does_not_re_alert(monkeypatch, cfg, make_feed):
    """The 30 Aug duplicate, reproduced.

    A node served publication 09:46:36 between two reads of 09:47:27. Taken as
    a fresh grid, the older one wiped what had been reported, so the newer one
    looked new again and pushed a second identical alert to the phone.
    """
    state = {"seen": [], "stamps": [], "cooldown_until": None, "cooldown_len": 0,
             "fail_streak": 0, "last_success": None, "heartbeat_sent": False}
    pushes, recorded = [], []
    feeds = iter([
        make_feed(("RHK", "10/01/2026", OPEN_FULL), stamp="08/30/2026 09:47:27"),
        make_feed(stamp="08/30/2026 09:46:36"),          # the stale replica
        make_feed(("RHK", "10/01/2026", OPEN_FULL), stamp="08/30/2026 09:47:27"),
    ])

    def remember(st, keys, stamp):
        st["seen"] = sorted(keys)
        if stamp and stamp != "?" and (not st["stamps"] or st["stamps"][-1]["feed"] != stamp):
            st["stamps"].append({"feed": stamp})

    monkeypatch.setattr(check_module.sys, "argv", ["check.py"])
    monkeypatch.setattr(check_module, "load_config", lambda: cfg)
    monkeypatch.setattr(check_module, "load_state", lambda: state)
    monkeypatch.setattr(check_module, "fetch", lambda: next(feeds))
    monkeypatch.setattr(check_module, "record_history", lambda feed: recorded.append(feed) or 0)
    monkeypatch.setattr(check_module, "notify", lambda *a, **k: pushes.append(a) or True)
    monkeypatch.setattr(check_module, "save_state", remember)

    assert check_module.main() == 0      # the opening appears
    assert check_module.main() == 0      # the replica arrives
    assert check_module.main() == 0      # the real publication returns

    assert len(pushes) == 1, "the same opening was pushed twice"
    assert state["seen"] == [REPORTED], "the replica wiped what had been reported"
    assert len(recorded) == 2, "the replica was recorded as history that never happened"