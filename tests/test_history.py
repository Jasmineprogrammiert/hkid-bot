"""Collection, and undoing the damage a stale feed replica does to it.

The dataset is the part of this project that cannot be rebuilt later, so a
wrong row in it is worse than a missed alert: the alert comes round again, the
history does not."""

import pytest

from watcher import history
from watcher.feed import FULL, OPEN_FULL
from watcher.history import _condemned, prune, record

CELL = ("RHK", "10/01/2026")


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(history, "DB", tmp_path / "history.db")
    return history.DB


def rows(*specs):
    """(id, office, date, from, to, stamp) tuples, as stored."""
    return [(i, "RHK", "2026-10-01", was, became, stamp)
            for i, (was, became, stamp) in enumerate(specs, start=1)]


def test_a_publication_older_than_one_already_stored_is_condemned():
    """30 Aug: 09:46:36 arrived between two reads of 09:47:27."""
    condemned = _condemned(rows(
        (FULL, OPEN_FULL, "08/30/2026 09:47:27"),      # the opening, real
        (OPEN_FULL, FULL, "08/30/2026 09:46:36"),      # the replica closing it
        (FULL, OPEN_FULL, "08/30/2026 09:47:27"),      # the same change again
        (OPEN_FULL, FULL, "08/30/2026 10:02:11"),      # a real later close
    ))
    assert condemned == [2, 3]


def test_an_ordinary_sequence_is_left_alone():
    assert _condemned(rows(
        (FULL, OPEN_FULL, "08/30/2026 09:47:27"),
        (OPEN_FULL, FULL, "08/30/2026 10:02:11"),
        (FULL, OPEN_FULL, "08/30/2026 10:17:04"),
    )) == []


def test_the_year_boundary_is_not_mistaken_for_a_regression():
    """MM/DD/YYYY sorts correctly inside a year and wrongly across one, so the
    stamps are parsed rather than compared as text."""
    assert _condemned(rows(
        (FULL, OPEN_FULL, "12/31/2026 23:50:00"),
        (OPEN_FULL, FULL, "01/01/2027 00:05:00"),
    )) == []


def test_an_unreadable_stamp_is_never_deleted():
    """Deleting on a parse failure would lose real data to a formatting change."""
    assert _condemned(rows(
        (FULL, OPEN_FULL, "08/30/2026 09:47:27"),
        (OPEN_FULL, FULL, "whenever"),
    )) == []


def test_the_same_change_at_the_same_publication_cannot_happen_twice():
    """A cell changes at most once per publication, so the second is a replay."""
    assert _condemned(rows(
        (FULL, OPEN_FULL, "08/30/2026 09:47:27"),
        (FULL, OPEN_FULL, "08/30/2026 09:47:27"),
    )) == [2]


def test_prune_removes_the_phantom_pair_end_to_end(db, make_feed):
    """The morning of 30 Aug, replayed through the real recorder."""
    record(make_feed((*CELL, FULL), stamp="08/30/2026 09:30:00"))
    record(make_feed((*CELL, OPEN_FULL), stamp="08/30/2026 09:47:27"))
    record(make_feed((*CELL, FULL), stamp="08/30/2026 09:46:36"))       # replica
    record(make_feed((*CELL, OPEN_FULL), stamp="08/30/2026 09:47:27"))  # re-applied

    conn = history.connect()
    before = conn.execute("SELECT count(*) FROM transition").fetchone()[0]
    conn.close()
    assert before == 4

    assert prune() == 2

    conn = history.connect()
    left = conn.execute("SELECT from_status, to_status, feed_stamp FROM transition"
                        " ORDER BY id").fetchall()
    conn.close()
    assert left == [(None, FULL, "08/30/2026 09:30:00"),
                    (FULL, OPEN_FULL, "08/30/2026 09:47:27")]


def test_pruning_nothing_is_not_an_error(db):
    assert prune() == 0