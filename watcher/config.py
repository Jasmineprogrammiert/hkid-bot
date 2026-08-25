"""Loading settings.

Identifying values (booking date, ntfy topic, healthcheck URL) come from the
environment or a gitignored .env, never from the repo, so this can stay public."""

import json
import os
from pathlib import Path

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

    if not cfg.get("target_date"):
        raise SystemExit(
            "No target date set. Export TARGET_DATE=YYYY-MM-DD (the appointment you\n"
            "already hold); anything earlier than it will trigger an alert."
        )
    return cfg