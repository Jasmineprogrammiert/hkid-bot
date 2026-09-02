"""Loading settings.

Identifying values (booking date, ntfy topic, healthcheck URL) come from the
environment or a gitignored .env, never from the repo, so this can stay public."""

import json
import os
from datetime import date
from pathlib import Path

from .feed import OFFICE_NAMES

# Data files live at the repo root, one level above this package.
ROOT = Path(__file__).resolve().parent.parent


def load_dotenv():
    """Read KEY=VALUE lines from a local .env, without overriding real env vars.

    Saves having to export things by hand for local runs. Gitignored, so the
    personal values in it never reach the public repo.
    """
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def parse_offices(raw):
    """Split an OFFICES override into codes.

    Comma-separated, because a shell variable and a GitHub secret are both
    single strings. Case and spacing are forgiven; an unknown code is not --
    validate() rejects that below.
    """
    return [code.strip().upper() for code in raw.split(",") if code.strip()]


def load_config():
    """Config file holds only non-identifying defaults; anything personal comes
    from the environment, so a public repo never carries your details."""
    load_dotenv()
    cfg = json.loads((ROOT / "config.json").read_text())
    for env_key, cfg_key in (("NTFY_TOPIC", "ntfy_topic"),
                             ("NTFY_SERVER", "ntfy_server"),
                             ("TARGET_DATE", "target_date"),
                             ("TARGET_TIME", "target_time"),
                             ("HEALTHCHECK_URL", "healthcheck_url")):
        if os.environ.get(env_key):
            cfg[cfg_key] = os.environ[env_key]

    # Which offices you would actually travel to is a personal preference, so
    # it overrides from the environment like the identifying values above.
    # config.json keeps every office, so a fresh clone watches all of them.
    if os.environ.get("OFFICES"):
        cfg["offices"] = parse_offices(os.environ["OFFICES"])

    if not cfg.get("target_date"):
        raise SystemExit(
            "No target date set. Export TARGET_DATE=YYYY-MM-DD (the appointment you\n"
            "already hold); anything earlier than it will trigger an alert."
        )
    validate(cfg)
    return cfg


# What find_openings() indexes directly, and the type each has to be.
EXPECTED = (("offices", list), ("include_almost_full", bool), ("include_target_day", bool))


def validate(cfg):
    """Fail here, naming the key, rather than four frames deeper.

    find_openings() indexes these straight off the config. A missing or
    mistyped one surfaced there as a KeyError or ValueError, which check.py
    reports as 'schema may have changed' -- blaming Immigration for a typo in
    a local file. Checking at load keeps that message honest.
    """
    for key, kind in EXPECTED:
        if key not in cfg:
            raise SystemExit(f"config.json is missing {key!r}.")
        if not isinstance(cfg[key], kind):
            raise SystemExit(f"config.json: {key!r} should be a {kind.__name__}, "
                             f"not a {type(cfg[key]).__name__}.")
    if not cfg["offices"]:
        raise SystemExit("config.json: 'offices' is empty, so nothing can ever match.")
    # A code that is not in the feed matches nothing, and silence is this
    # tool's normal state -- so a typo here would look exactly like "no slots"
    # for as long as you left it. Name it now instead.
    unknown = [o for o in cfg["offices"] if o not in OFFICE_NAMES]
    if unknown:
        raise SystemExit(
            f"unknown office code(s): {', '.join(unknown)}. "
            f"Valid codes are {', '.join(sorted(OFFICE_NAMES))}.")
    try:
        date.fromisoformat(cfg["target_date"])
    except (ValueError, TypeError):
        raise SystemExit(f"target date {cfg['target_date']!r} is not YYYY-MM-DD.") from None