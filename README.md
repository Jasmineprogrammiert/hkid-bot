# HKID slot watcher

Checks HKID appointment availability and alerts your phone when a slot opens on or before
the date you already booked.

The data comes from the Immigration Department's public [Appointment Quota Preview][preview].
Booking is still manual — this just tells you when it's worth going to do it.

**One limit worth knowing up front:** the feed is day-level only. It can tell you a day has
opened up, but not which times are free — so it can't tell you whether that slot beats your
existing one on the same day.

[preview]: https://eservices.es2.immd.gov.hk/es/quota-enquiry-client/?l=en-US&appId=579

## Setup

**1. Push notifications.** Install [ntfy](https://ntfy.sh) (free, no account) and subscribe
to a random topic. Anyone who knows the topic can read and post to it, so make it
unguessable:

```sh
python3 -c "import secrets; print(secrets.token_hex(16))"
```

**2. Configure.** Anything identifying comes from the environment, never the repo. Put it in
a `.env` beside the script — gitignored, so it stays out of the public repo:

```ini
TARGET_DATE=2099-12-31   # the booking you already hold
TARGET_TIME=09:00        # optional, shown in the alert
NTFY_TOPIC=your-generated-topic
HEALTHCHECK_URL=         # optional, see "When something breaks"
```

Real environment variables override the file, so `TARGET_DATE=... python3 check.py` works
for a one-off.

**3. Check it works.**

```sh
python3 check.py --test
```

`--test` pushes regardless of results, so your phone should buzz. Without it, the script
only notifies when something actually opens up.

## Running it 24/7

`.github/workflows/watch.yml` runs the check on GitHub's servers, so your machine doesn't
need to be on. Add the same values as repository secrets under
Settings → Secrets and variables → Actions — `TARGET_DATE` and `NTFY_TOPIC` are required,
`TARGET_TIME` and `HEALTHCHECK_URL` optional.

**The 5-minute cadence comes from a loop inside the job, not from the cron.** GitHub
documents that scheduled runs are dropped under load; measured here, a `*/5` schedule
delivered one dispatch every 35–47 minutes. So each dispatch polls for ~50 minutes itself,
and the concurrency group queues overlapping dispatches into continuous coverage.

Keep the repo **public**; a private one can't sustain 5-minute polling
([why](#when-something-breaks)).

Nothing runs between **01:00 and 07:00 HKT**, when most people are asleep and changes are
less likely — 216 runs a day instead of 288. Cron works in UTC, so that window is `23,0-16`.

Any always-on machine works too, as a crontab entry:

```sh
*/5 7-23,0 * * * cd ~/hkid-bot && /usr/bin/python3 check.py >> watch.log 2>&1
```

## Settings

`config.json` holds preferences. The identifying keys are in there too, but stay blank —
those come from the environment.

| Key | Meaning |
|---|---|
| `offices` | Which offices to watch |
| `include_almost_full` | Also alert on *almost full*, not just *available* |
| `include_target_day` | Also alert when the booked day itself has quota |
| `heartbeat_hours` | Warn if no check has succeeded in this many hours (`0` disables) |
| `ntfy_server` | Defaults to `https://ntfy.sh` |

Office codes: `RHK` Wan Chai · `RKO` Cheung Sha Wan · `RTK` Tseung Kwan O ·
`FTO` Fo Tan · `TMO` Tuen Mun · `YLO` Yuen Long.

## How it works

```
every 5 min, 07:00-01:00 HKT
      |
      v
  fetch feed  ---- 429/403/503 ----> back off 1h+, make no requests
      |
      v
  any day open on or before your booking?
      |
      +-- no ---------------------------> stay silent
      |
      +-- yes --> already reported? --+-- yes --> stay silent
                                      |
                                      +-- no ---> ntfy --> phone
      |
      v
  state.json    reported slots + feed timestamps, read again next run
```

```
check.py           the run itself - entry point
config.json        preferences
watcher/
  config.py        settings, from .env and the environment
  feed.py          reading the quota feed, comparing it to your booking
  alerts.py        formatting an opening and pushing it to your phone
  state.py         what carries between runs
  monitor.py       noticing when the watcher itself is broken
```

Dependencies point one way only — `config`, `feed` and `state` import nothing local;
`alerts` uses `feed`; `monitor` sits on `alerts` and `state`; `check.py` wires them up.

**Freshness.** The feed regenerates about every 15 minutes; this checks every 5. You don't
have to take that on trust — every run logs the feed's own `lastUpdateTime`:

```sh
python3 check.py --stats
```

```
observations recorded : 84
distinct publications : 29
refresh interval      : median 15.0 min (min 14.9, max 16.0, n=28)
```

If that median moves, the poll interval should move with it.

Gaps under two minutes are ignored: the feed sits behind a load balancer whose nodes
publish a second or two apart, so one publication can show up twice with different
timestamps.

## When something breaks

Silence normally means "no slots". Without the layers below it would equally mean "this
broke three weeks ago" — indistinguishable from your phone.

```
Immigration rejects us     ->  back off quietly, doubling each time
runs but fails for 8h      ->  push: "HKID watcher may be broken"
gives up after 3 failures  ->  ping /fail   ->  healthchecks.io emails you
stops running entirely     ->  no ping      ->  healthchecks.io emails you
```

Each layer covers the blind spot of the one above. The last matters most: if the workflow is
disabled or out of Actions minutes, no code here runs, so nothing here could report it. Only
an outside watcher notices the silence.

**The 8-hour heartbeat** clears the largest legitimate gap — six quiet overnight hours plus
cron drift — while still reaching you the same day. It fires once per outage, not once per
run, and re-arms after the next successful check. "Succeeded" means usable data, not merely
an HTTP 200: if the feed answered but its shape had changed, counting that as success would
keep resetting the clock and the heartbeat could never fire.

**The dead-man's switch** is optional. Create a free check at
[healthchecks.io](https://healthchecks.io), set **Period 8 hours, Grace 1 hour**, and add its
ping URL as a `HEALTHCHECK_URL` secret. Treat that URL as a password — anyone holding it can
fake a heartbeat and suppress a real alert.

A single failed check stays silent on purpose; only three in a row report. One flaky network
moment shouldn't page you, and an alert you learn to ignore is worse than none.

**Two ways the schedule can stop without telling you:**

| | What happens | Fix |
|---|---|---|
| **Actions minutes** | Private repos get 2,000/month, and GitHub rounds every run up to a full minute. 5-minute polling spends the lot in about a week. | Keep the repo public, or drop the cron to `*/30` |
| **Inactivity** | GitHub disables scheduled workflows after 60 days without repo activity. | Any commit resets the clock |

## Notes

**You won't get the same alert twice.** `state.json` records what's already been reported. A
slot that fills and later reopens *will* alert again — that's wanted. If a push fails,
nothing is recorded, so the next run retries instead of losing the alert.

**Load on the government server.** Each run is one request, about 58 KB, no browser. The
endpoint sends no `ETag` or `Last-Modified`, so every check pays full price, and publishes no
rate limits to aim at. Rather than probe for where the line is, the script sends one request
per run, **never retries a rejection**, and on `429`, `403` or `503` sleeps at least an hour
— honouring `Retry-After`, doubling on repeats, capped at a day.

> Retrying through a throttle is what turns a temporary slowdown into a lasting block.