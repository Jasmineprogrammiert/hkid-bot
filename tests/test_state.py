"""What carries between runs, and the clock it is written with."""

from datetime import datetime, timedelta, timezone

from watcher.state import (JITTER_S, load_state, now, parse, refresh_intervals,
                           save_state)


def stamps(*feed_times):
    return [{"feed": t} for t in feed_times]


def test_gaps_are_reported_in_minutes():
    gaps = refresh_intervals(stamps("08/29/2026 12:00:00", "08/29/2026 12:15:00"))
    assert gaps == [15.0]


def test_jitter_is_not_a_refresh():
    """Two load-balancer nodes stamping seconds apart is one publication seen
    twice, not two refreshes -- counting it would drag the median down."""
    close = timedelta(seconds=JITTER_S - 1)
    first = datetime(2026, 8, 29, 12, 0, 0)
    assert refresh_intervals(stamps(f"{first:%m/%d/%Y %H:%M:%S}",
                                    f"{first + close:%m/%d/%Y %H:%M:%S}")) == []


def test_a_gap_on_the_jitter_boundary_counts():
    first = datetime(2026, 8, 29, 12, 0, 0)
    at_limit = first + timedelta(seconds=JITTER_S)
    assert refresh_intervals(stamps(f"{first:%m/%d/%Y %H:%M:%S}",
                                    f"{at_limit:%m/%d/%Y %H:%M:%S}")) == [2.0]


def test_unparseable_stamps_are_skipped_not_fatal():
    gaps = refresh_intervals(stamps("08/29/2026 12:00:00", "not a date",
                                    "08/29/2026 12:20:00"))
    assert gaps == [20.0]


def test_reported_slots_survive_a_round_trip():
    save_state({"seen": [], "stamps": []}, {"RHK|2026-10-01"}, "08/29/2026 12:00:00")
    assert load_state()["seen"] == ["RHK|2026-10-01"]


def test_the_same_publication_is_only_logged_once():
    """--stats counts publications, so a repeated stamp must not inflate it."""
    state = {"seen": [], "stamps": []}
    save_state(state, set(), "08/29/2026 12:00:00")
    state = load_state()
    save_state(state, set(), "08/29/2026 12:00:00")
    assert len(load_state()["stamps"]) == 1


def test_the_clock_is_utc_aware():
    """The runner is UTC and the laptop is HKT; naive stamps make the two
    state files disagree by eight hours the first time they meet."""
    assert now().tzinfo is not None
    assert now().utcoffset() == timedelta(0)


def test_stamps_written_before_the_clock_was_utc_still_parse():
    naive = "2026-08-29T12:00:00"
    assert parse(naive).tzinfo == timezone.utc
    assert (now() - parse(naive)).total_seconds() > 0