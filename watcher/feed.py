"""Reading the Immigration Department's public quota feed.

One request per run, no retry on rejection: retrying through a throttle is what
turns a temporary slowdown into a lasting block."""

import json
import urllib.error
import urllib.request
from datetime import datetime, date

FEED = "https://eservices.es2.immd.gov.hk/surgecontrolgate/ticket/getSituation?svcId=579"
REFERER = "https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=en-US&appId=579"

# Sends a standard browser User-Agent rather than identifying as a script.
# The politeness that actually matters is above: one request, no retries.
USER_AGENT = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
              "AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/140.0.0.0 Safari/537.36")

# Every office the feed carries, and what to call it in a notification.
OFFICE_NAMES = {
    "RHK": "Wan Chai",
    "RKO": "Cheung Sha Wan",
    "RTK": "Tseung Kwan O",
    "FTO": "Fo Tan",
    "TMO": "Tuen Mun",
    "YLO": "Yuen Long",
}

# quotaR values in the feed, best to worst.
OPEN_FULL = "quota-g"      # green  - quota available
OPEN_ALMOST = "quota-y"    # amber  - almost full
FULL = "quota-r"           # red    - full

COOLDOWN_MIN_S = 3600


class BackOff(Exception):
    """The server asked us to stop. Carries how many seconds to stay away."""

    def __init__(self, seconds, reason):
        super().__init__(reason)
        self.seconds = seconds
        self.reason = reason


def fetch():
    req = urllib.request.Request(
        FEED,
        headers={
            "Accept": "application/json",
            "Referer": REFERER,
            "User-Agent": USER_AGENT,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        # 429/403/503 are the server telling us to back off. Honour it rather
        # than retrying -- retrying through a rate limit is what turns a
        # temporary throttle into a lasting block.
        if exc.code in (429, 403, 503):
            retry_after = exc.headers.get("Retry-After") if exc.headers else None
            wait = COOLDOWN_MIN_S
            if retry_after and retry_after.isdigit():
                wait = max(wait, int(retry_after))
            raise BackOff(wait, f"HTTP {exc.code}") from exc
        raise


def find_openings(feed, cfg):
    """Return openings strictly better than -- or on -- the booked date."""
    target = date.fromisoformat(cfg["target_date"])
    wanted = set(cfg["offices"])
    accept = {OPEN_FULL} | ({OPEN_ALMOST} if cfg["include_almost_full"] else set())

    hits = []
    for row in feed["data"]:
        if row["officeId"] not in wanted or row["quotaR"] not in accept:
            continue
        when = datetime.strptime(row["date"], "%m/%d/%Y").date()
        if when < target:
            beats = "earlier"
        elif when == target and cfg["include_target_day"]:
            beats = "same-day"
        else:
            continue
        hits.append({
            "key": f"{row['officeId']}|{when.isoformat()}",
            "date": when,
            "office": row["officeId"],
            "status": row["quotaR"],
            "beats": beats,
        })
    hits.sort(key=lambda h: (h["date"], h["office"]))
    return hits