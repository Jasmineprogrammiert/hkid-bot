"""What carries between runs, and the clock it is written with."""

from datetime import datetime, timedelta, timezone

from watcher.state import (JITTER_S, REPLAY_WINDOW_S, is_replay, load_state, now,
                           parse, refresh_intervals, save_state)


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


# --- the feed going backwards -------------------------------------------------

def test_a_publication_older_than_one_already_processed_is_a_replay():
    """A load-balancer node serving yesterday's grid is not news."""
    assert is_replay("08/30/2026 09:46:36", stamps("08/30/2026 09:47:27"))


def test_the_newest_publication_is_not_a_replay():
    assert not is_replay("08/30/2026 09:47:27", stamps("08/30/2026 09:46:36"))


def test_the_same_publication_again_is_not_a_replay():
    """Jitter is handled elsewhere; re-reading the current grid is harmless."""
    assert not is_replay("08/30/2026 09:47:27", stamps("08/30/2026 09:47:27"))


def test_a_stamp_far_behind_is_treated_as_a_genuine_reset():
    """Refusing those forever would turn one bad reading into a silent watcher."""
    old = datetime(2026, 8, 30, 9, 47, 27) - timedelta(seconds=REPLAY_WINDOW_S + 60)
    assert not is_replay(f"{old:%m/%d/%Y %H:%M:%S}", stamps("08/30/2026 09:47:27"))


def test_nothing_processed_yet_cannot_be_a_replay():
    assert not is_replay("08/30/2026 09:47:27", [])


def test_an_unreadable_stamp_is_not_treated_as_a_replay():
    assert not is_replay("not a date", stamps("08/30/2026 09:47:27"))