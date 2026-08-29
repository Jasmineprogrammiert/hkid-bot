"""What carries between runs.

Two things: which slots have already been reported, and every lastUpdateTime
the feed has served. The first prevents repeat alerts; the second backs --stats."""

import json
from datetime import datetime, timezone
from pathlib import Path

# Written at the repo root, one level above this package.
STATE = Path(__file__).resolve().parent.parent / "state.json"

# The feed sits behind a load balancer whose nodes regenerate seconds apart, so
# two stamps a few seconds apart are the same publication seen twice, not two
# refreshes. Anything closer together than this is treated as that jitter.
JITTER_S = 120
MAX_STAMPS = 200


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


def refresh_intervals(stamps):
    """Gaps in minutes between consecutive feed publications, jitter removed."""
    times = []
    for s in stamps:
        try:
            times.append(datetime.strptime(s["feed"], "%m/%d/%Y %H:%M:%S"))
        except (ValueError, KeyError):
            continue
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