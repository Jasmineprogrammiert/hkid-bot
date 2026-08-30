"""Watch the HK Immigration public appointment-quota feed and push a phone alert
when a Registration of Persons slot opens on or before the date already booked.

Reads only the public quota-preview JSON; it never books anything.

The feed is day-level only, so it can prove a *date* beats the held booking but
never a *time* within it."""

import json
import sys
import urllib.error
from datetime import date

from watcher.alerts import describe, headline, notify
from watcher.config import load_config
from watcher.feed import BackOff, fetch, find_openings
from watcher.history import prune as history_prune
from watcher.history import record as record_history
from watcher.history import report as history_report
from watcher.monitor import (
    check_heartbeat,
    in_cooldown,
    mark_success,
    ping_healthcheck,
    record_failure,
    start_cooldown,
)
from watcher.state import is_replay, load_state, print_stats, save_state


def read_feed(cfg, state):
    """Fetch and parse the feed. Returns (feed, exit_code); one is always None."""
    try:
        feed = fetch()
    except BackOff as exc:
        # Escalate the pause on repeats, so a persistent block isn't nibbled at.
        start_cooldown(cfg, state, exc.seconds, exc.reason)
        save_state(state, set(state["seen"]), None)
        return None, 3
    except (urllib.error.URLError, json.JSONDecodeError, TimeoutError) as exc:
        record_failure(cfg, state, f"could not read quota feed: {exc}")
        check_heartbeat(cfg, state)
        save_state(state, set(state["seen"]), None)
        return None, 2
    return feed, None


def send_alert(cfg, fresh, force, booked):
    """Push a real opening, or a labelled delivery test. True if it landed."""
    if fresh:
        # The only notification that ever fires on its own: a real, new opening.
        body = describe(fresh) + f"\n\nYour booking: {booked}, {cfg['target_time']}"
        if notify(cfg, headline(fresh), body):
            print(f"[ok] pushed {len(fresh)} opening(s)")
            return True
        return False

    if force:
        # Deliberate delivery check only. Labelled so it can never be mistaken
        # for a real alert, and never sent by the scheduled run.
        scope = (f"on or before {booked}" if cfg.get("include_target_day")
                 else f"earlier than {booked}")
        if notify(cfg, "HKID watcher - test",
                  f"Delivery is working. You'll be alerted when a slot opens {scope}.",
                  priority="default"):
            print("[ok] delivery test sent")
            return True
        return False

    return True


def main():
    # Reporting reads the collected history and makes no network request.
    if "--report" in sys.argv:
        return 0 if history_report() else 1

    # Housekeeping on the collected history; also no network request.
    if "--prune" in sys.argv:
        history_prune()
        return 0

    force = "--test" in sys.argv
    cfg = load_config()
    state = load_state()

    if in_cooldown(state):
        print(f"[hold] backing off until {state['cooldown_until']} "
              "- server asked us to slow down")
        # Deliberate, so it gets its own words -- but still reported, because
        # nothing is being checked for as long as it lasts.
        check_heartbeat(cfg, state, paused_until=state["cooldown_until"])
        save_state(state, set(state["seen"]), None)
        return 0

    feed, failed = read_feed(cfg, state)
    if failed is not None:
        return failed

    try:
        hits = find_openings(feed, cfg)
    except (KeyError, ValueError, TypeError) as exc:
        # Parsed as JSON but isn't the shape we expect -- most likely IMMD
        # changed the schema. Treat it as a failure so a broken watcher backs
        # off and shows up in the logs, rather than crashing every 5 minutes.
        # Config is validated at load, so a local typo can no longer arrive
        # here wearing Immigration's clothes.
        record_failure(cfg, state,
                       f"unexpected feed format ({exc}) - schema may have changed")
        check_heartbeat(cfg, state)
        save_state(state, set(state["seen"]), None)
        return 2

    # Success means usable data, not merely a 200 -- otherwise a changed schema
    # would keep resetting the clock and the heartbeat could never fire.
    mark_success(state)

    seen = set(state["seen"])
    stamp = feed.get("lastUpdateTime", "?")

    if is_replay(stamp, state["stamps"]):
        # A load-balancer node served a publication older than one already
        # processed. Taken at face value it reads as every open slot closing at
        # once, which re-alerts on the next real publication and records a pair
        # of transitions that never happened. It is a valid read, so it still
        # counts as success -- it just teaches us nothing.
        print(f"[skip] feed served {stamp}, older than one already processed "
              "- stale replica, leaving state and history alone")
        save_state(state, seen, None)
        ping_healthcheck(cfg)
        return 0

    fresh = [h for h in hits if h["key"] not in seen]
    # Store the whole grid, not just what qualifies: the alert path discards
    # everything else, and a change not recorded cannot be recovered later.
    changed = record_history(feed)
    print(f"feed updated {stamp} | {len(hits)} qualifying, "
          f"{len(fresh)} new, {changed} recorded")
    if hits:
        print(describe(hits))

    booked = date.fromisoformat(cfg["target_date"]).strftime("%a %d %b")
    delivered = send_alert(cfg, fresh, force, booked)

    # Track only what currently qualifies, so a slot that closes and reopens
    # alerts again. If the push never landed, don't record the new ones --
    # otherwise a failed notification would silently mark the slot handled.
    current = {h["key"] for h in hits}
    save_state(state, current if delivered else (seen & current), stamp)
    ping_healthcheck(cfg, ok=delivered)

    if "--stats" in sys.argv:
        print()
        print_stats(load_state())
    return 0 if delivered else 1


if __name__ == "__main__":
    sys.exit(main())