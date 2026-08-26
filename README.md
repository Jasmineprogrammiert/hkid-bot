# HKID slot watcher

Checks HKID appointment availability and alerts your phone when a slot opens on or before
the date you already booked.

The data comes from the Immigration Department's public [Appointment Quota Preview][preview].
Booking is still manual — this just tells you when it's worth going to do it.

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
less likely — 216 checks a day instead of 288. Cron works in UTC, so that window is `23,0-16`.

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
  history.db    every change the feed has shown, kept for good
```

`check.py` is the entry point; the parts live in `watcher/` — `config` (settings), `feed`
(reading and filtering), `alerts` (ntfy), `state` (what carries between runs), `history`
(recording changes), `monitor` (noticing when the watcher itself is broken). Dependencies
point one way only: the first three import nothing local, `alerts` uses `feed`, `monitor`
sits on `alerts` and `state`, and `check.py` wires them together.

**Freshness.** The feed regenerates every 15 minutes — `refreshTime = 9e5` in the page's own
script, and measured at a median of 15.0 min. This checks every 5. Every run logs the feed's
own `lastUpdateTime`, so you can re-check that:

```sh
python3 check.py --stats
```

```
observations recorded : 84
distinct publications : 29
refresh interval      : median 15.0 min (min 14.9, max 16.0, n=28)
first seen            : 08/25/2026 10:02:14
latest                : 08/26/2026 09:47:31
```

Gaps under two minutes are ignored — the feed sits behind a load balancer whose nodes publish
a second or two apart, so one publication can surface twice with different timestamps.

**History.** Alerting asks only what's open now and discards the rest. `history.db` keeps
what can't be recovered later: when each cell changed. Transitions, not snapshots —
`red → green` a cancellation appearing, `green → red` someone taking it, `→ gone` a date
leaving the rolling window rather than being booked. Every office is stored, timed by the
feed's own clock. Failures warn and return zero, so collection can't break the alerting it
rides on.

## What this can't do

Two limits, both structural.

**It's day-level.** The feed gives a status per office per day, never individual times — so
it can't tell you whether an opening on your booked day beats the time you hold.

**It's 15 minutes behind.** That's Immigration's republish interval, not a setting here;
polling faster returns byte-identical data. So **a slot that opens and is taken inside one
cycle never appears at all** — the feed shows full before and full after, and no transition
is ever published. How big that blind spot is depends on how fast freed slots go, which is
what `--report` measures:

```sh
python3 check.py --report
```

It shows when openings appear, by hour and weekday, and how long each survived — including
how many lasted under 15 minutes and so were never visible here.

The authoritative view lives inside the booking system, which computes availability per
applicant — so the answer depends on who is asking, can't be shared, and can't be cached.
That path sits behind a queue gate and client attestation, and this stays out of it
deliberately: the gate exists to keep automated clients out, and tripping it risks the
appointment you already hold.

What the department publishes openly instead is a batch-computed summary, identical for every
visitor and cheap to serve, so casual reads never touch the booking database. The 15 minutes
is the price of that separation rather than an oversight.

Openings that outlast a cycle are still caught, and the report can point you at the hours
worth checking by hand — but this won't win a race against someone already inside the
booking system.

## When something breaks

Silence normally means "no slots". Without the layers below it would equally mean "this broke
three weeks ago", and from your phone the two look identical.

```
Immigration rejects us     ->  back off quietly, doubling each time
runs but fails for 8h      ->  push: "HKID watcher may be broken"
gives up after 3 failures  ->  ping /fail   ->  healthchecks.io emails you
stops running entirely     ->  no ping      ->  healthchecks.io emails you
```

Each layer covers the blind spot of the one above. The last matters most: if the workflow is
disabled or out of minutes, no code here runs, so only an outside watcher can notice.

**The 8-hour heartbeat** clears the largest legitimate gap — the quiet overnight hours plus
cron drift — while still reaching you the same day. It fires once per outage, not once per
run. "Succeeded" means usable data, not an HTTP 200: counting a changed schema as success
would keep resetting the clock, and the heartbeat could never fire.

**The dead-man's switch** is optional. Create a free check at
[healthchecks.io](https://healthchecks.io), set **Period 8 hours, Grace 1 hour**, and add its
ping URL as a `HEALTHCHECK_URL` secret. Treat it as a password — anyone holding it can fake a
heartbeat.

One failed check stays silent; only three in a row report. An alert you learn to ignore is
worse than none.

**Two ways the schedule can stop silently:**

| | What happens | Fix |
|---|---|---|
| **Actions minutes** | Private repos get 2,000/month. Because each job now polls for ~50 minutes, that allowance is gone in under two days. | Keep the repo public, or drop the loop and the cron to `*/30` |
| **Inactivity** | GitHub disables scheduled workflows after 60 days without repo activity. | Any commit resets the clock |

## Notes

**You won't get the same alert twice.** `state.json` records what's already been reported. A
slot that fills and later reopens *will* alert again — that's wanted. If a push fails,
nothing is recorded, so the next run retries instead of losing the alert.

**Load on the government server.** One request per check, ~58 KB, no browser. The endpoint
sends no `ETag` and publishes no rate limits, so rather than probe for the line the script
**never retries a rejection**: on `429`, `403` or `503` it sleeps at least an hour, honouring
`Retry-After`, doubling on repeats, capped at a day.

> Retrying through a throttle is what turns a temporary slowdown into a lasting block.