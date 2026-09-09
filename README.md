# Machu Picchu daily pool tracker

Logs the 1,000 same-cycle entry tickets for the Llaqta de Machupicchu, so we can
answer one question properly: **for a given visit date, what time did each route
sell out, and how many days ahead was it?**

Source: the public availability board at
`tuboleto.cultura.pe/disponibilidad/llaqta_machupicchu`.

## The six routes

| nidRuta | Circuit | Route | Allocation |
|---|---|---|---|
| 7 | 1, Panorámico | 1-A: Montaña Machupicchu | 50 |
| 8 | 1, Panorámico | 1-B: Terraza superior | 100 |
| 11 | 2, Clásico | 2-A: Clásico Diseñada | 600 |
| 12 | 2, Clásico | 2-B: Terraza Inferior | 100 |
| 13 | 3, Realeza | 3-A: Montaña Waynapicchu | 50 |
| 14 | 3, Realeza | 3-B: Realeza diseñada | 100 |

Total 1,000. Allocations are logged every poll rather than assumed, because the
Ministry does adjust them, and a step change in `ncupo` is itself a finding.
Join on `nid_ruta`, not on the Spanish name, since the names get retitled.

## How it collects

The API (`api-tuboleto.cultura.pe/comunes/disponibilidad-actual`) requires a
signed request: a `code` hash plus a `timestamp` with a TTL under about eight
minutes. Confirmed server side, with three distinct rejections for a missing
field, a stale timestamp, and a bad hash.

So we don't sign anything. `scrape.py` opens the real page in headless Chromium
and reads the availability JSON as the page's own requests return it. The app
signs its own traffic exactly as intended. This is mechanically the same as
leaving the tab open and writing the numbers down, it survives a redeploy or a
rotated salt, and it never touches a reservation or payment endpoint.

## Data

**`data/observations/YYYY-MM.csv`** appends a row only when `ncupo` or
`ncupoActual` actually changes for a (visit date, route) pair. Overnight, when
nothing moves, it writes nothing.

| column | meaning |
|---|---|
| `ts_utc`, `ts_lima` | real fetch time. Use `ts_lima`, the sale runs on Peru time (UTC-5, no DST) |
| `fecha_visita` | the visit date these tickets admit you on |
| `nid_ruta`, `nid_circuito` | stable route and circuit ids |
| `ncupo` | allocation for that route |
| `ncupo_actual` | still available |
| `vendidos` | `ncupo - ncupo_actual` |

**`data/runs.csv`** gets one row per poll regardless. This is what separates
"nothing sold" from "the scraper broke", and `sale_window` records which visit
dates the portal was offering, which widens to two or three days when demand is
high.

**`data/state.json`** is last-seen values for change detection. Delete it to
force a full re-log.

## Running it

```bash
pip install -r requirements.txt
playwright install chromium

python scrape.py                      # one poll
python scrape.py --debug              # plus screenshot, HTML and raw captures
python scrape.py --loop 25 --every 5  # poll every 5 min for 25 min
```

## Scheduling

`.github/workflows/scrape.yml` polls every 10 minutes. **The repo needs to be
public**, otherwise this eats roughly 5,000 Actions minutes a month against a
2,000 minute free allowance. Public repos get standard runners free.

Scheduled workflows on GitHub are best effort and routinely run late under load,
sometimes by more than 15 minutes, and occasionally get skipped. Effective
resolution is therefore nearer 10 to 20 minutes than a clean 10. Every row
carries its real fetch time, so the jitter is measurable rather than assumed. If
it turns out to be bad, switch the job to `--loop 25 --every 5`, which polls
inside a single run and sidesteps the scheduler entirely, at the cost of far more
runner minutes.

## Not yet built

The dashboard. There is nothing to plot until a few days of curves exist, so the
sequencing is: start logging now, build charts once the data justifies them.
