# CLAUDE.md

Context for anyone (human or agent) picking this up. Written 2026-09-09.

## What this is

Logs the 1,000 daily entry tickets for the Llaqta de Machupicchu from the public
board at `tuboleto.cultura.pe/disponibilidad/llaqta_machupicchu`, to answer: for a
given visit date, what time did each route sell out, and how far ahead?

Not the advance/pre-booking channel. Different data source, deliberately out of scope.

## Hard-won findings, do not re-derive these

**The API is signed and the signature is enforced.**
`POST https://api-tuboleto.cultura.pe/comunes/disponibilidad-actual`
Body: `{"lugar","fecha","punto":5,"code","timestamp"}`. `code` is a base64 SHA-256
(32 bytes) with a secret salt from the Angular bundle. Verified server side with
three distinct rejections:
- missing either field: `Valores requeridos`
- stale timestamp: `Código expirado` (TTL under ~8 min, measured at 463 s)
- fresh timestamp, wrong hash: `Código inválido`

Expiry is checked *before* the hash. 546 candidate constructions (permutations of
lugar/fecha/punto/timestamp, separators, JSON forms, HMAC with each field as key,
UTF-8 and UTF-16) produced no match. Don't bother retrying that.

**Therefore: drive the real page, never forge a request.** Playwright loads the
page, the app signs its own traffic, we read the JSON off the wire via a response
listener. Survives salt rotation and redeploys. Never touch reservation or
payment endpoints.

**The date picker is switched off.** `input#fecha` exists with
`min` set to tomorrow, but its container carries Tailwind `hidden`, so
`page.fill()` times out and Angular never sees synthetic events. Don't try to
drive it. Instead we passively log `picker_hidden`; if it ever flips to False the
portal has opened a wider window, which is the busy-season signal we want.

**The sale opens at 06:00 Lima** (`Inicia: 06:00 AM` on the board). Nothing moves
before it. Lima is UTC-5 year round, no DST. All analysis in Lima time.

**Six routes, 1,000 total.** Key on `nidRuta` (7, 8, 11, 12, 13, 14), never the
Spanish names, which get retitled. `ncupo` is allocation, `ncupoActual` is
remaining. Log `ncupo` every poll: the Ministry adjusts it and a step change is a
finding.

**Other endpoints the page hits:** `tickets-por-fecha/{date}` (unsigned, returns
`totalticket`), `tiempo-servidor` (server clock, probably feeds the signature
timestamp), `getDatosParametro` (config, uninspected, may hold the sale window
length), `avisos-public` (notices), plus a CAPTCHA widget.

## Open problems

1. **Fails during Peru business hours.** 08:58 Lima on a GitHub runner: page
   loaded, no availability payload in 60 s. Worked fine at 04:05. Unknown whether
   this is the CAPTCHA challenging datacenter IPs, the site being slow under
   load, or a 403. The current code now reports per-endpoint HTTP statuses on
   failure, which should settle it. **The decisive test is running locally and on
   a runner at the same moment**, since the only difference is residential versus
   datacenter IP.

2. **Scheduled workflows drop most ticks.** Measured 2026-09-09: workflow state
   `active`, permissions fine, no billing block, but of 14 due ticks in the old
   twice-an-hour rush cron only 3 runs actually landed (~21%), and none of the
   missing ones show up as queued or skipped, meaning GitHub's scheduler never
   created them. `workflow_dispatch` fired all 3 times it was tried. This is
   documented best-effort delivery on GitHub's side, not a fixable config bug.
   Mitigation applied: cron tightened to every 5 minutes (GitHub's minimum
   granularity) across both selling-hours blocks, on the theory that more
   independent ticks partially offsets a fixed drop rate, plus a short internal
   retry loop in the rush window. This does not guarantee 5-minute resolution
   at a ~75-80% drop rate; the user declined the `launchd`-on-a-Mac fallback
   for now (would sidestep GitHub's scheduler entirely and likely also solves
   problem 1, since it's residential IP instead of datacenter) in favor of
   watching whether the tightened cron is enough. Revisit if the next sellout
   window still shows multi-hour gaps.

## Data

- `data/observations/YYYY-MM.csv`: one row only when `ncupo`/`ncupoActual`
  changes for a (visit date, route) pair.
- `data/runs.csv`: one row per poll regardless. Distinguishes "nothing sold" from
  "scraper broke". Carries `board_fecha`, `inicia`, `entregados`, `picker_hidden`.
- `data/state.json`: last-seen values for change detection. Delete to force a
  full re-log.
- `append_csv` retires a file and starts fresh if the header no longer matches,
  so adding columns can't silently misalign rows.

## Not built yet

The dashboard. Deliberately deferred until real depletion curves exist. Chart
ideas discussed: sellout lead time per visit date; burn-down curves aligned by
countdown to the visit; calendar heatmap by route; purchase velocity by hour of
day; restock/rollover tracker; a live "book now or wait" panel; capacity change
log.

## Conventions

No em dashes in anything written for this project.
