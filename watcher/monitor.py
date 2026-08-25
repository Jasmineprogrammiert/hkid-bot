"""Noticing when the watcher itself is broken.

Silence normally means 'no slots'. Without this it would equally mean 'this
stopped working weeks ago', and the two are indistinguishable from a phone."""

import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta

from .alerts import notify
from .feed import COOLDOWN_MIN_S
from .state import save_state

COOLDOWN_MAX_S = 86400
# Consecutive unexplained failures before we assume it's us, not them.
FAIL_STREAK_LIMIT = 3


def ping_healthcheck(cfg, ok=True):
    """Dead-man's switch, for the failure this script cannot report itself.

    check_heartbeat() only runs if the script runs. If the workflow stops
    entirely -- disabled after 60 days idle, Actions minutes exhausted, repo
    deleted -- nothing here executes and nothing warns you. So an outside
    service watches for the pings to stop instead.

    Never allowed to break a run: a monitoring failure must not become an
    outage of the thing being monitored.
    """
    url = cfg.get("healthcheck_url", "").strip()
    if not url:
        return
    target = url.rstrip("/") + ("" if ok else "/fail")
    try:
        with urllib.request.urlopen(target, timeout=10):
            pass
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"[warn] healthcheck ping failed: {exc}", file=sys.stderr)


def check_heartbeat(cfg, state):
    """Warn if it has been too long since a successful read.

    Silence is the normal state of this tool, which means a dead watcher looks
    exactly like a quiet one. This is the only thing that tells them apart.
    Alerts once per outage; a successful read re-arms it.
    """
    hours = cfg.get("heartbeat_hours", 8)
    if not hours:
        return
    last = state.get("last_success")
    if not last:
        return                                  # nothing to compare against yet
    stale_for = datetime.now() - datetime.fromisoformat(last)
    if stale_for < timedelta(hours=hours) or state.get("heartbeat_sent"):
        return

    since = datetime.fromisoformat(last).strftime("%a %d %b, %H:%M")
    notify(
        cfg,
        "HKID watcher may be broken",
        f"No successful check since {since} "
        f"({int(stale_for.total_seconds() // 3600)}h ago).\n\n"
        "Slots could be opening without you hearing about it. "
        "Worth looking at the GitHub Actions tab.",
        priority="high",
    )
    state["heartbeat_sent"] = True
    print(f"[warn] heartbeat: no success for {stale_for}", file=sys.stderr)


def in_cooldown(state):
    """True while we have promised to leave the server alone."""
    resume = state.get("cooldown_until")
    return bool(resume and datetime.now() < datetime.fromisoformat(resume))


def start_cooldown(cfg, state, floor_s, reason):
    """Stop making requests for a while, escalating if it keeps happening."""
    wait = min(max(floor_s, state.get("cooldown_len", 0) * 2), COOLDOWN_MAX_S)
    resume_at = datetime.now() + timedelta(seconds=wait)
    state["cooldown_until"] = resume_at.isoformat(timespec="seconds")
    state["cooldown_len"] = wait
    print(f"[hold] {reason} - pausing {wait // 60} min", file=sys.stderr)
    ping_healthcheck(cfg, ok=False)


def mark_success(state):
    """A clean read clears every kind of pushback we were tracking."""
    state["cooldown_until"] = None
    state["cooldown_len"] = 0
    state["fail_streak"] = 0
    state["last_success"] = datetime.now().isoformat(timespec="seconds")
    state["heartbeat_sent"] = False


def record_failure(cfg, state, message):
    """Log a failure we can't explain, and back off once they stack up.

    We have never seen this server reject anything, so the 429/403/503 list in
    fetch() is convention, not observation. A block might instead arrive as an
    HTML queue page (note the /surgecontrolgate/ path) or as a changed schema.
    Counting consecutive failures catches those without having to name them.
    """
    streak = state.get("fail_streak", 0) + 1
    state["fail_streak"] = streak
    if streak >= FAIL_STREAK_LIMIT:
        # Only now is this worth waking someone for. A single blip is not.
        start_cooldown(cfg, state, COOLDOWN_MIN_S, f"{streak} consecutive failures")
    save_state(state, set(state["seen"]), None)
    print(f"[error] {message}", file=sys.stderr)