"""Backing off, and telling the difference between paused and broken."""

from datetime import timedelta

import pytest

from watcher import monitor
from watcher.feed import COOLDOWN_MIN_S
from watcher.monitor import (COOLDOWN_MAX_S, check_heartbeat, in_cooldown,
                             mark_success, record_failure, start_cooldown)
from watcher.state import now


@pytest.fixture
def pushed(monkeypatch):
    """Capture notifications instead of sending them."""
    sent = []
    monkeypatch.setattr(monitor, "notify",
                        lambda cfg, title, body, priority="high": sent.append((title, body)))
    return sent


def fresh_state(**over):
    state = {"seen": [], "stamps": [], "cooldown_until": None, "cooldown_len": 0,
             "fail_streak": 0, "last_success": None, "heartbeat_sent": False}
    state.update(over)
    return state


# --- backing off -----------------------------------------------------------

def test_the_first_back_off_uses_the_floor(cfg):
    state = fresh_state()
    start_cooldown(cfg, state, COOLDOWN_MIN_S, "HTTP 429")
    assert state["cooldown_len"] == COOLDOWN_MIN_S


def test_repeats_double_rather_than_nibbling_at_the_limit(cfg):
    state = fresh_state(cooldown_len=COOLDOWN_MIN_S)
    start_cooldown(cfg, state, COOLDOWN_MIN_S, "HTTP 429")
    assert state["cooldown_len"] == COOLDOWN_MIN_S * 2


def test_escalation_stops_at_a_day(cfg):
    state = fresh_state(cooldown_len=COOLDOWN_MAX_S)
    start_cooldown(cfg, state, COOLDOWN_MIN_S, "HTTP 429")
    assert state["cooldown_len"] == COOLDOWN_MAX_S


def test_a_longer_retry_after_wins_over_the_floor(cfg):
    state = fresh_state()
    start_cooldown(cfg, state, COOLDOWN_MIN_S * 5, "Retry-After")
    assert state["cooldown_len"] == COOLDOWN_MIN_S * 5


def test_in_cooldown_tracks_the_resume_time():
    ahead = (now() + timedelta(minutes=5)).isoformat(timespec="seconds")
    behind = (now() - timedelta(minutes=5)).isoformat(timespec="seconds")
    assert in_cooldown({"cooldown_until": ahead})
    assert not in_cooldown({"cooldown_until": behind})
    assert not in_cooldown({"cooldown_until": None})


def test_a_clean_read_clears_every_kind_of_pushback():
    state = fresh_state(cooldown_until="2026-08-29T12:00:00+00:00", cooldown_len=7200,
                        fail_streak=2, heartbeat_sent=True)
    mark_success(state)
    assert state["cooldown_until"] is None
    assert (state["cooldown_len"], state["fail_streak"]) == (0, 0)
    assert state["heartbeat_sent"] is False   # re-armed for the next outage


# --- unexplained failures --------------------------------------------------

def test_one_blip_is_not_worth_backing_off_for(cfg):
    state = fresh_state()
    record_failure(cfg, state, "boom")
    record_failure(cfg, state, "boom")
    assert state["fail_streak"] == 2
    assert state["cooldown_until"] is None


def test_three_in_a_row_means_it_is_probably_us(cfg):
    state = fresh_state(fail_streak=2)
    record_failure(cfg, state, "boom")
    assert state["cooldown_until"] is not None


# --- heartbeat -------------------------------------------------------------

def stale(hours):
    return (now() - timedelta(hours=hours)).isoformat(timespec="seconds")


def test_a_recent_success_says_nothing(cfg, pushed):
    check_heartbeat(cfg, fresh_state(last_success=stale(1)))
    assert pushed == []


def test_a_long_silence_reports_once_per_outage(cfg, pushed):
    state = fresh_state(last_success=stale(9))
    check_heartbeat(cfg, state)
    check_heartbeat(cfg, state)
    assert len(pushed) == 1
    assert pushed[0][0] == "HKID watcher may be broken"


def test_nothing_to_compare_against_stays_quiet(cfg, pushed):
    check_heartbeat(cfg, fresh_state(last_success=None))
    assert pushed == []


def test_zero_hours_disables_it(cfg, pushed):
    cfg["heartbeat_hours"] = 0
    check_heartbeat(cfg, fresh_state(last_success=stale(99)))
    assert pushed == []


def test_a_deliberate_pause_is_not_reported_as_a_breakage(cfg, pushed):
    """A back-off is deliberate, so it gets its own words -- but it is still
    reported, because nothing is checked for as long as it lasts."""
    until = (now() + timedelta(hours=2)).isoformat(timespec="seconds")
    check_heartbeat(cfg, fresh_state(last_success=stale(9)), paused_until=until)
    assert len(pushed) == 1
    title, body = pushed[0]
    assert title == "HKID watcher paused"
    assert "may be broken" not in title + body
    assert "Backing off until" in body


def test_a_stamp_from_before_the_clock_was_utc_does_not_crash(cfg, pushed):
    """Naive values are treated as UTC rather than raising on the subtraction."""
    check_heartbeat(cfg, fresh_state(last_success="2020-01-01T00:00:00"))
    assert len(pushed) == 1