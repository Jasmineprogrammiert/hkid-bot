"""Every change the feed has ever shown, kept as transitions.

The alert path only asks whether a slot is open right now and discards the rest.
This keeps the part that cannot be recovered afterwards: when a cell changed.

Transitions rather than snapshots. The feed republishes the same grid every
~15 min, so storing each fetch would be almost entirely duplicate rows. A row
here means something actually happened:

    red -> green    a cancellation appeared
    green -> red    someone took it
    the gap between how long it stayed free

Event time is the feed's own lastUpdateTime, never our clock. We poll every
5 min against a feed that regenerates every ~15, so the moment we notice a
change is up to a refresh later than the change itself.

Never allowed to break a run: collection rides along with the alerting, and
must not be able to take it down."""

import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# Written at the repo root, one level above this package.
DB = Path(__file__).resolve().parent.parent / "history.db"

# A date leaving the feed's rolling window is not someone taking the slot, so
# it gets its own terminal status rather than looking like a booking.
EXPIRED = "gone"

SCHEMA = """
CREATE TABLE IF NOT EXISTS cell (
    office_id  TEXT NOT NULL,
    slot_date  TEXT NOT NULL,
    status     TEXT NOT NULL,
    PRIMARY KEY (office_id, slot_date)
);

CREATE TABLE IF NOT EXISTS transition (
    id          INTEGER PRIMARY KEY,
    office_id   TEXT NOT NULL,
    slot_date   TEXT NOT NULL,
    from_status TEXT,
    to_status   TEXT NOT NULL,
    feed_stamp  TEXT NOT NULL,
    recorded_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS transition_cell ON transition (office_id, slot_date);
CREATE INDEX IF NOT EXISTS transition_stamp ON transition (feed_stamp);
"""

INSERT_TRANSITION = """
INSERT INTO transition
    (office_id, slot_date, from_status, to_status, feed_stamp, recorded_at)
VALUES (?, ?, ?, ?, ?, ?)
"""

UPSERT_CELL = """
INSERT INTO cell (office_id, slot_date, status) VALUES (?, ?, ?)
ON CONFLICT (office_id, slot_date) DO UPDATE SET status = excluded.status
"""


def connect():
    conn = sqlite3.connect(DB, timeout=10)
    conn.executescript(SCHEMA)
    return conn


def grid(feed):
    """{(office, ISO date): status} for every cell the feed carries.

    Every office, not just the configured ones -- the office filter is an
    alerting preference, and narrowing what gets stored would throw away data
    that cannot be collected again later.
    """
    cells = {}
    for row in feed["data"]:
        when = datetime.strptime(row["date"], "%m/%d/%Y").date()
        cells[(row["officeId"], when.isoformat())] = row["quotaR"]
    return cells


def diff(before, after):
    """Cells whose status changed, as (office, date, from, to) rows."""
    changes = [(office, day, before.get((office, day)), status)
               for (office, day), status in after.items()
               if before.get((office, day)) != status]
    changes += [(office, day, before[(office, day)], EXPIRED)
                for (office, day) in before.keys() - after.keys()]
    return changes


def record(feed):
    """Store what changed since the last publication. Returns rows written.

    The same publication seen twice writes nothing: the load balancer's nodes
    stamp a second or two apart, but the grid they serve is identical, so the
    diff is empty. The jitter needs no special case here.
    """
    stamp = feed.get("lastUpdateTime")
    if not stamp:
        return 0
    try:
        after = grid(feed)
        now = datetime.now().isoformat(timespec="seconds")
        conn = connect()
        try:
            with conn:
                before = {(office, day): status for office, day, status
                          in conn.execute("SELECT office_id, slot_date, status FROM cell")}
                changes = diff(before, after)
                conn.executemany(INSERT_TRANSITION,
                                 [(o, d, was, now_, stamp, now)
                                  for o, d, was, now_ in changes])
                conn.executemany(UPSERT_CELL,
                                 [(o, d, s) for (o, d), s in after.items()])
                conn.executemany("DELETE FROM cell WHERE office_id = ? AND slot_date = ?",
                                 sorted(before.keys() - after.keys()))
            return len(changes)
        finally:
            conn.close()
    except (sqlite3.Error, KeyError, ValueError, TypeError, OSError) as exc:
        print(f"[warn] history not recorded: {exc}", file=sys.stderr)
        return 0