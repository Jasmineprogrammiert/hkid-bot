"""Turning openings into a phone notification.

The push is retried, because one dropped notification is one missed slot."""

import sys
import time
import urllib.error
import urllib.request

from .feed import OFFICE_NAMES, OPEN_FULL, REFERER


def describe(hits):
    lines = []
    for h in hits:
        status = "available" if h["status"] == OPEN_FULL else "almost full"
        office = OFFICE_NAMES.get(h["office"], h["office"])
        line = f"{h['date']:%a %d %b}  ·  {office}  ·  {status}"
        if h["beats"] == "same-day":
            line += "  (time unknown)"
        lines.append(line)
    return "\n".join(lines)


def headline(hits):
    # Kept ASCII on purpose: this becomes an HTTP header, which is latin-1 only.
    first = f"{hits[0]['date']:%a %d %b}"
    if len(hits) == 1:
        return f"HKID slot open - {first}"
    return f"HKID - {len(hits)} slots from {first}"


def notify(cfg, title, body, priority="high", attempts=3):
    """Push to ntfy. Retries, because one dropped push is one missed slot."""
    topic = cfg.get("ntfy_topic", "").strip()
    if not topic:
        print("[warn] no ntfy_topic configured - printing only", file=sys.stderr)
        return False
    url = f"{cfg['ntfy_server'].rstrip('/')}/{topic}"
    # HTTP headers are latin-1; a stray dash or CJK char here would raise mid-alert.
    safe_title = title.encode("latin-1", "replace").decode("latin-1")

    for attempt in range(1, attempts + 1):
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={
                "Title": safe_title,
                "Priority": priority,
                "Click": REFERER,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                if resp.status < 300:
                    return True
                print(f"[warn] ntfy returned {resp.status} (try {attempt}/{attempts})",
                      file=sys.stderr)
        except (urllib.error.URLError, TimeoutError) as exc:
            print(f"[warn] ntfy push failed: {exc} (try {attempt}/{attempts})",
                  file=sys.stderr)
        if attempt < attempts:
            time.sleep(2 ** attempt)

    print("[error] ntfy push failed after all retries", file=sys.stderr)
    return False