"""What carries between runs.

Two things: which slots have already been reported, and every lastUpdateTime
the feed has served. The first prevents repeat alerts; the second backs --stats."""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Written at the repo root, one level above this package.
STATE = Path(__file__).resolve().parent.parent / "state.json"

# The feed sits behind a load balancer whose nodes regenerate seconds apart, so
# two stamps a few seconds apart are the same publication seen twice, not two
# refreshes. Anything closer together than this is treated as that jitter.
JITTER_S = 120
MAX_STAMPS = 200

# The same load balancer that stamps a publication twice can also serve one
# OLDER than a publication already processed. Read as a fresh grid, that older
# one looks like every open slot closing at once: it wipes what has been
# reported, so the next real publication re-alerts on slots already pushed, and
# it writes a pair of phantom transitions into history that never happened.
#
# Bounded on purpose. A stamp far behind the newest is likelier a genuine reset
# at their end than a stale replica, and refusing those forever would turn one
# bad reading into a permanently silent watcher.
REPLAY_WINDOW_S = 3600


def now():
    """The clock, in UTC, everywhere.

    The runner is UTC and the laptop is HKT. A naive local clock writes stamps
    whose meaning depends on which machine wrote them, so the first time the two
    state files meet, every interval is eight hours out.
    """
    return datetime.now(timezone.utc)


def parse(ts):
    """Read a stored stamp back. Values written before this was UTC are naive;
    treat them as UTC rather than crashing on a naive/aware subtraction."""
    at = datetime.fromisoformat(ts)
    return at if at.tzinfo else at.replace(tzinfo=timezone.utc)


def load_state():
    if not STATE.exists():
        return {"seen": [], "stamps": []}
    try:
        state = json.loads(STATE.read_text())
    except (json.JSONDecodeError, OSError):
        return {"seen": [], "stamps": []}
    state.setdefault("seen", [])
    state.setdefault("stamps", [])
    state.setdefault("cooldown_until", None)
    state.setdefault("cooldown_len", 0)
    state.setdefault("fail_streak", 0)
    state.setdefault("last_success", None)
    state.setdefault("heartbeat_sent", False)
    return state


def save_state(state, keys, stamp):
    """Persist reported slots, and log this observation of the feed's own stamp."""
    stamps = state.get("stamps", [])
    if stamp and stamp != "?" and (not stamps or stamps[-1]["feed"] != stamp):
        stamps.append({"feed": stamp,
                       "seen_at": now().isoformat(timespec="seconds")})
    STATE.write_text(json.dumps({
        "seen": sorted(keys),
        "stamps": stamps[-MAX_STAMPS:],
        "cooldown_until": state.get("cooldown_until"),
        "cooldown_len": state.get("cooldown_len", 0),
        "fail_streak": state.get("fail_streak", 0),
        "last_success": state.get("last_success"),
        "heartbeat_sent": state.get("heartbeat_sent", False),
        "updated": now().isoformat(timespec="seconds"),
    }))


def feed_time(stamp):
    """The feed's own clock, or None if it is not a stamp we recognise."""
    try:
        return datetime.strptime(stamp, "%m/%d/%Y %H:%M:%S")
    except (ValueError, TypeError):
        return None


def is_replay(stamp, stamps):
    """True if this publication is older than one already processed.

    Not the same as the jitter case above: that is one publication surfacing
    twice a second or two apart, which is harmless. This is the feed going
    backwards, which is not.
    """
    current = feed_time(stamp)
    seen = [t for t in (feed_time(s.get("feed")) for s in stamps) if t]
    if not current or not seen:
        return False
    behind = max(seen) - current
    return timedelta(0) < behind <= timedelta(seconds=REPLAY_WINDOW_S)


def refresh_intervals(stamps):
    """Gaps in minutes between consecutive feed publications, jitter removed."""
    times = []
    for s in stamps:
        parsed = feed_time(s.get("feed"))
        if parsed:
            times.append(parsed)
    gaps = []
    for earlier, later in zip(times, times[1:]):
        delta = (later - earlier).total_seconds()
        if delta >= JITTER_S:
            gaps.append(delta / 60)
    return gaps


def print_stats(state):
    stamps = state.get("stamps", [])
    gaps = refresh_intervals(stamps)
    print(f"observations recorded : {len(stamps)}")
    if not gaps:
        print("not enough data yet - needs a few runs spanning two publications")
        return
    gaps.sort()
    mid = gaps[len(gaps) // 2]
    print(f"distinct publications : {len(gaps) + 1}")
    print(f"refresh interval      : median {mid:.1f} min "
          f"(min {min(gaps):.1f}, max {max(gaps):.1f}, n={len(gaps)})")
    print(f"first seen            : {stamps[0]['feed']}")
    print(f"latest                : {stamps[-1]['feed']}")