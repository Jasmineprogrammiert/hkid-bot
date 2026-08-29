"""find_openings: which cells count as an opening worth waking someone for.

The filter is the whole product. Everything else -- alerting, state, history --
only matters if this decides correctly, so the boundaries get their own tests:
the day before the booking, the day of it, and the day after."""

from watcher.feed import FULL, OPEN_ALMOST, OPEN_FULL, find_openings

BEFORE, TARGET, AFTER = "10/01/2026", "10/09/2026", "10/20/2026"


def test_earlier_day_at_a_watched_office_qualifies(cfg, make_feed):
    hits = find_openings(make_feed(("RHK", BEFORE, OPEN_FULL)), cfg)
    assert [h["key"] for h in hits] == ["RHK|2026-10-01"]
    assert hits[0]["beats"] == "earlier"


def test_later_than_the_booking_is_not_an_opening(cfg, make_feed):
    # The point of the tool is beating the date already held; a later slot is
    # worse than doing nothing.
    assert find_openings(make_feed(("RHK", AFTER, OPEN_FULL)), cfg) == []


def test_the_booked_day_itself_is_excluded_by_default(cfg, make_feed):
    assert find_openings(make_feed(("RHK", TARGET, OPEN_FULL)), cfg) == []


def test_the_booked_day_counts_when_asked_for(cfg, make_feed):
    # Day-level data can't prove the time beats the one held, so it is labelled
    # differently rather than treated as a straight win.
    cfg["include_target_day"] = True
    hits = find_openings(make_feed(("RHK", TARGET, OPEN_FULL)), cfg)
    assert [h["beats"] for h in hits] == ["same-day"]


def test_unwatched_offices_are_ignored(cfg, make_feed):
    assert find_openings(make_feed(("TMO", BEFORE, OPEN_FULL)), cfg) == []


def test_amber_is_excluded_by_default(cfg, make_feed):
    assert find_openings(make_feed(("RHK", BEFORE, OPEN_ALMOST)), cfg) == []


def test_amber_counts_when_asked_for(cfg, make_feed):
    cfg["include_almost_full"] = True
    hits = find_openings(make_feed(("RHK", BEFORE, OPEN_ALMOST)), cfg)
    assert [h["status"] for h in hits] == [OPEN_ALMOST]


def test_full_never_qualifies_even_with_amber_allowed(cfg, make_feed):
    cfg["include_almost_full"] = True
    assert find_openings(make_feed(("RHK", BEFORE, FULL)), cfg) == []


def test_hits_are_sorted_by_date_then_office(cfg, make_feed):
    feed = make_feed(("RKO", "10/02/2026", OPEN_FULL),
                     ("RHK", "10/02/2026", OPEN_FULL),
                     ("RHK", "10/01/2026", OPEN_FULL))
    keys = [h["key"] for h in find_openings(feed, cfg)]
    assert keys == ["RHK|2026-10-01", "RHK|2026-10-02", "RKO|2026-10-02"]


def test_key_is_stable_across_runs(cfg, make_feed):
    """state.json dedupes on this string, so its shape is load-bearing."""
    feed = make_feed(("RHK", BEFORE, OPEN_FULL))
    assert find_openings(feed, cfg)[0]["key"] == find_openings(feed, cfg)[0]["key"]
    assert find_openings(feed, cfg)[0]["key"] == "RHK|2026-10-01"