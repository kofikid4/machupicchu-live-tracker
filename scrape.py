#!/usr/bin/env python3
"""
Machu Picchu daily-pool availability logger.

Loads https://tuboleto.cultura.pe/disponibilidad/llaqta_machupicchu in a headless
browser and reads the availability JSON as the page's own requests return it. The
API signs each request with a short-lived hash, so we let the app do the signing
rather than forging it. Nothing here touches reservation or payment endpoints.

Writes:
  data/observations/YYYY-MM.csv   one row only when a number actually changes
  data/runs.csv                   one row per poll, so silence is distinguishable
                                  from breakage
  data/state.json                 last seen values, for change detection

Usage:
  python scrape.py                 single poll
  python scrape.py --debug         also dump screenshot + HTML + raw captures
  python scrape.py --loop 25 --every 5    poll every 5 min for 25 min
"""

import argparse
import csv
import json
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright

LIMA = ZoneInfo("America/Lima")
PAGE_URL = "https://tuboleto.cultura.pe/disponibilidad/llaqta_machupicchu"
API_MARKER = "comunes/disponibilidad-actual"
TICKETS_URL = "https://api-tuboleto.cultura.pe/recaudador/ticket/tickets-por-fecha/{}"

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
OBS_DIR = DATA / "observations"
RUNS_CSV = DATA / "runs.csv"
STATE_JSON = DATA / "state.json"
DEBUG_DIR = ROOT / "debug"

OBS_FIELDS = [
    "ts_utc", "ts_lima", "fecha_visita", "nid_circuito", "nid_ruta",
    "circuito", "ruta", "ncupo", "ncupo_actual", "vendidos",
]
RUN_FIELDS = [
    "ts_utc", "ts_lima", "ok", "n_dates", "n_routes", "n_changes",
    "n_captures", "sale_window", "total_disponible", "board_fecha", "inicia",
    "entregados", "picker_hidden", "duration_ms", "note",
]


# ---------------------------------------------------------------- persistence

def append_csv(path: Path, fields: list, rows: list) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    # If the schema has changed since this file was started, appending would
    # silently misalign every subsequent row. Retire the old file instead.
    if path.exists():
        with path.open(encoding="utf-8") as fh:
            header = (fh.readline() or "").strip().lstrip("\ufeff").split(",")
        if header and header != fields:
            retired = path.with_suffix(f".{int(time.time())}.csv")
            path.rename(retired)
            print(f"  schema changed, retired {path.name} -> {retired.name}")
    new = not path.exists()
    with path.open("a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        if new:
            w.writeheader()
        w.writerows(rows)


def load_state() -> dict:
    try:
        return json.loads(STATE_JSON.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(state: dict) -> None:
    STATE_JSON.parent.mkdir(parents=True, exist_ok=True)
    STATE_JSON.write_text(json.dumps(state, indent=1, sort_keys=True), encoding="utf-8")


# ------------------------------------------------------------------ capture

def capture(debug: bool = False, timeout_ms: int = 60_000) -> dict:
    """Open the page, harvest every availability response it makes."""
    captures = []          # {fecha, status, rows}
    other_calls = []       # for debug: everything else the app hit

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-dev-shm-usage"])
        ctx = browser.new_context(
            locale="es-PE",
            timezone_id="America/Lima",
            viewport={"width": 1400, "height": 1800},
        )
        page = ctx.new_page()

        def on_response(resp):
            url = resp.url
            if "api-tuboleto" in url and API_MARKER not in url:
                entry = {"url": url, "status": resp.status}
                if debug:
                    try:
                        entry["body"] = resp.json()
                    except Exception:
                        entry["body"] = None
                other_calls.append(entry)
                return
            if API_MARKER not in url:
                return
            fecha = None
            try:
                post = resp.request.post_data
                if post:
                    fecha = json.loads(post).get("fecha")
            except Exception:
                pass
            body = None
            try:
                body = resp.json()
            except Exception:
                pass
            captures.append({"fecha": fecha, "status": resp.status, "rows": body})

        page.on("response", on_response)
        page.goto(PAGE_URL, wait_until="domcontentloaded", timeout=timeout_ms)

        # The page polls on a loop, so networkidle never fires. Wait for the
        # first availability payload, then linger briefly to catch any others.
        deadline = time.time() + timeout_ms / 1000
        while time.time() < deadline and not captures:
            page.wait_for_timeout(500)
        page.wait_for_timeout(4000)

        window = read_board(page)

        if debug:
            DEBUG_DIR.mkdir(exist_ok=True)
            page.screenshot(path=str(DEBUG_DIR / "page.png"), full_page=True)
            (DEBUG_DIR / "page.html").write_text(page.content(), encoding="utf-8")
            (DEBUG_DIR / "captures.json").write_text(
                json.dumps({"availability": captures,
                            "other_api_calls": other_calls,
                            "sale_window": window}, indent=2, ensure_ascii=False),
                encoding="utf-8")

        browser.close()

    return {"captures": captures, "window": window, "other": other_calls}


def read_board(page) -> dict:
    """
    Read what the board itself is displaying.

    The date picker (input#fecha) exists but its container carries Tailwind's
    `hidden`, so the portal is locked to one visit date. If that ever changes,
    picker_hidden flips to False and the board date moves, which is how we detect
    the sale window widening to two or three days in a busy period. No
    interaction needed, we just record what is on screen.
    """
    js = """
    () => {
      const out = {};
      const vis = el => el.offsetParent !== null;
      for (const el of document.querySelectorAll('span,p,div,h1,h2,h3')) {
        if (el.children.length || !vis(el)) continue;
        const t = (el.textContent || '').trim();
        let m;
        if ((m = t.match(/^(\\d{2})\\/(\\d{2})\\/(\\d{4})$/)))
          out.board_fecha = m[3] + '-' + m[2] + '-' + m[1];
        if ((m = t.match(/Inicia:\\s*(.+)$/i)))       out.inicia = m[1].trim();
        if ((m = t.match(/Disponibles:\\s*(\\d+)/i)))  out.disponibles = +m[1];
        if ((m = t.match(/Entregados:\\s*(\\d+)/i)))   out.entregados = +m[1];
      }
      const inp = document.querySelector('input#fecha, input[type=date]');
      if (inp) {
        out.picker_hidden = inp.offsetParent === null;
        out.picker_min = inp.min || null;
        out.picker_max = inp.max || null;
      }
      return out;
    }
    """
    try:
        return page.evaluate(js) or {}
    except Exception:
        return {}


def fetch_ticket_counter(fecha: str):
    """The unsigned counter endpoint. Cheap, so we log it as a cross-check."""
    try:
        import urllib.request
        req = urllib.request.Request(
            TICKETS_URL.format(fecha),
            headers={"Referer": "https://tuboleto.cultura.pe/",
                     "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read()).get("totalticket")
    except Exception:
        return None


# --------------------------------------------------------------------- poll

def poll(debug: bool = False) -> dict:
    started = time.time()
    now = datetime.now(timezone.utc)
    ts_utc = now.isoformat(timespec="seconds")
    ts_lima = now.astimezone(LIMA).isoformat(timespec="seconds")

    ok, note = True, ""
    result = {"captures": [], "window": [], "other": []}
    try:
        result = capture(debug=debug)
    except Exception as exc:
        ok = False
        note = f"{type(exc).__name__}: {exc}"[:200]
        if debug:
            traceback.print_exc()

    board = result.get("window") or {}
    state = load_state()
    obs_rows, dates, routes, total_avail = [], set(), 0, 0

    # The page polls on a loop, so the same visit date arrives several times per
    # run. Keep the most recent payload per date, otherwise the run summary
    # double counts.
    latest = {}
    for cap in result["captures"]:
        if isinstance(cap.get("rows"), list) and cap.get("fecha"):
            latest[cap["fecha"]] = cap

    for fecha, cap in sorted(latest.items()):
        rows = cap["rows"]
        dates.add(fecha)
        for r in rows:
            if not isinstance(r, dict) or "nidRuta" not in r:
                continue
            routes += 1
            ncupo = r.get("ncupo")
            actual = r.get("ncupoActual")
            total_avail += actual or 0
            key = f"{fecha}|{r['nidRuta']}"
            sig = [ncupo, actual]
            if state.get(key) == sig:
                continue                      # unchanged, don't write a row
            state[key] = sig
            obs_rows.append({
                "ts_utc": ts_utc,
                "ts_lima": ts_lima,
                "fecha_visita": fecha,
                "nid_circuito": r.get("nidCircuito"),
                "nid_ruta": r.get("nidRuta"),
                "circuito": r.get("circuito"),
                "ruta": r.get("ruta"),
                "ncupo": ncupo,
                "ncupo_actual": actual,
                "vendidos": (ncupo - actual) if None not in (ncupo, actual) else None,
            })

    if not dates and ok:
        ok, note = False, "page loaded but no availability payload captured"

    if obs_rows:
        append_csv(OBS_DIR / f"{now.astimezone(LIMA):%Y-%m}.csv", OBS_FIELDS, obs_rows)
        save_state(state)

    append_csv(RUNS_CSV, RUN_FIELDS, [{
        "ts_utc": ts_utc,
        "ts_lima": ts_lima,
        "ok": int(ok),
        "n_dates": len(dates),
        "n_routes": routes,
        "n_changes": len(obs_rows),
        "n_captures": len(result["captures"]),
        "sale_window": ";".join(sorted(dates)),
        "total_disponible": total_avail,
        "board_fecha": board.get("board_fecha", ""),
        "inicia": board.get("inicia", ""),
        "entregados": board.get("entregados", ""),
        "picker_hidden": board.get("picker_hidden", ""),
        "duration_ms": int((time.time() - started) * 1000),
        "note": note[:200],
    }])

    print(f"[{ts_lima}] ok={ok} dates={sorted(dates)} routes={routes} "
          f"changes={len(obs_rows)} avail={total_avail} {note}")
    if debug:
        print("  board:", board)
        print("  other api calls:", [c["url"].split("/")[-2:] for c in result["other"]])
    return {"ok": ok, "changes": len(obs_rows)}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--debug", action="store_true",
                    help="dump screenshot, HTML and raw captures to debug/")
    ap.add_argument("--loop", type=int, default=0,
                    help="keep polling for this many minutes")
    ap.add_argument("--every", type=float, default=5,
                    help="minutes between polls when looping")
    args = ap.parse_args()

    if not args.loop:
        return 0 if poll(debug=args.debug)["ok"] else 1

    end = time.time() + args.loop * 60
    failures = 0
    while True:
        try:
            if not poll(debug=args.debug)["ok"]:
                failures += 1
        except Exception:
            failures += 1
            traceback.print_exc()
        if time.time() + args.every * 60 >= end:
            break
        time.sleep(args.every * 60)
    return 1 if failures and failures >= args.loop / args.every else 0


if __name__ == "__main__":
    sys.exit(main())
