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
from collections import Counter
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

OPEN = ("quota-g", "quota-y")
FULL_ = "quota-r"


def _events(conn):
    """Transitions worth reporting, with the feed's own time parsed."""
    rows = conn.execute(
        "SELECT office_id, slot_date, from_status, to_status, feed_stamp FROM transition"
        " WHERE from_status IS NOT NULL ORDER BY id")
    out = []
    for office, day, was, now_, stamp in rows:
        try:
            at = datetime.strptime(stamp, "%m/%d/%Y %H:%M:%S")
        except ValueError:
            continue
        if was == FULL_ and now_ in OPEN:
            out.append((at, office, day, "opened"))
        elif was in OPEN and now_ == FULL_:
            out.append((at, office, day, "taken"))
    return out


def _survivals(events):
    """Minutes between a slot opening and the same slot being taken."""
    opened, spans = {}, []
    for at, office, day, kind in events:
        key = (office, day)
        if kind == "opened":
            opened[key] = at
        elif kind == "taken" and key in opened:
            spans.append((at - opened.pop(key)).total_seconds() / 60)
    return spans


def report():
    """When openings appear, and how long they last. Returns False if no data."""
    if not DB.exists():
        print("no history yet - the database is written on the first run")
        return False
    conn = connect()
    try:
        events = _events(conn)
        total = conn.execute("SELECT count(*) FROM transition").fetchone()[0]
    finally:
        conn.close()

    opens = [e for e in events if e[3] == "opened"]
    print(f"transitions recorded : {total}")
    print(f"openings seen        : {len(opens)}")
    if not opens:
        print("\nNothing has opened yet. Come back once the feed has shown a red -> green.")
        return False

    print(f"first               : {opens[0][0]:%a %d %b %H:%M}")
    print(f"latest              : {opens[-1][0]:%a %d %b %H:%M}")

    print("\nopenings by hour (feed's own clock)")
    by_hour = Counter(e[0].hour for e in opens)
    peak = max(by_hour.values())
    for h in range(24):
        n = by_hour.get(h, 0)
        if n or 7 <= h <= 23:
            print(f"  {h:02d}:00  {'#' * int(12 * n / peak) if n else ''}{'' if n else '.'} {n or ''}")

    print("\nopenings by weekday")
    names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    by_day = Counter(e[0].weekday() for e in opens)
    peak = max(by_day.values())
    for d in range(7):
        n = by_day.get(d, 0)
        print(f"  {names[d]}  {'#' * int(12 * n / peak) if n else '.'} {n or ''}")

    spans = _survivals(events)
    print("\nhow long an opening lasted before it was taken")
    if not spans:
        print("  no opening has been taken yet - none have closed since appearing")
    else:
        spans.sort()
        mid = spans[len(spans) // 2]
        print(f"  median {mid:.0f} min   (min {min(spans):.0f}, max {max(spans):.0f}, n={len(spans)})")
        print(f"  under 15 min: {sum(1 for s in spans if s < 15)} of {len(spans)}"
              "  <- these are the ones a 15-minute feed can never show in time")
    return True