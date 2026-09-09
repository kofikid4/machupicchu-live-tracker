#!/usr/bin/env python3
"""Regenerate the summary section of README.md from data/*.csv.

Run after every poll (the workflow does this before committing) so the
README always shows the latest snapshot and a rolling 7-day sellout
average per route. Everything it reads is append-only CSV, so this is
pure derivation, nothing here is a source of truth.
"""
import csv
import glob
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
README = ROOT / "README.md"
START_MARK = "<!-- summary:start -->"
END_MARK = "<!-- summary:end -->"

# nidRuta never changes; the display name does, so we take whatever name
# was most recently observed rather than hardcoding one.
ROUTE_ORDER = ["7", "8", "11", "12", "13", "14"]


def load_observations():
    rows = []
    for path in sorted(glob.glob(str(ROOT / "data/observations/*.csv"))):
        with open(path, newline="", encoding="utf-8") as f:
            rows.extend(csv.DictReader(f))
    rows.sort(key=lambda r: r["ts_utc"])
    return rows


def load_runs():
    path = ROOT / "data/runs.csv"
    if not path.exists():
        return []
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def route_labels(rows):
    labels = {}
    for r in rows:
        labels[r["nid_ruta"]] = (r["circuito"], r["ruta"])
    return labels


def latest_snapshot(rows):
    """Most recent ncupo/ncupo_actual per (fecha_visita, nid_ruta). Rows are
    time-sorted on the way in, so the last write for a key wins."""
    latest = {}
    for r in rows:
        latest[(r["fecha_visita"], r["nid_ruta"])] = r
    return latest


def current_board_fecha(runs):
    for r in reversed(runs):
        if r.get("board_fecha"):
            return r["board_fecha"]
    return None


def sellout_events(rows):
    """First row where ncupo_actual hits 0, per (fecha_visita, nid_ruta).
    Returns nid_ruta -> list of (fecha_visita, ts_lima), oldest first."""
    seen = set()
    events = defaultdict(list)
    for r in rows:
        key = (r["fecha_visita"], r["nid_ruta"])
        if key in seen:
            continue
        if r["ncupo_actual"] and int(r["ncupo_actual"]) == 0:
            seen.add(key)
            events[r["nid_ruta"]].append((r["fecha_visita"], r["ts_lima"]))
    return events


def avg_clock_time(ts_list):
    minutes = []
    for ts in ts_list:
        dt = datetime.fromisoformat(ts)
        minutes.append(dt.hour * 60 + dt.minute + dt.second / 60)
    avg = sum(minutes) / len(minutes)
    h, m = divmod(int(round(avg)) % (24 * 60), 60)
    return f"{h:02d}:{m:02d}"


def build_summary():
    obs = load_observations()
    runs = load_runs()

    if not runs:
        return "No polls logged yet."

    last_run = runs[-1]
    labels = route_labels(obs)
    snapshot = latest_snapshot(obs)
    board_fecha = current_board_fecha(runs) or last_run.get("board_fecha")

    lines = []
    lines.append(f"**Last poll:** {last_run['ts_lima']} Lima "
                  f"({'ok' if last_run['ok'] == '1' else 'FAILED, see runs.csv'})")
    if board_fecha:
        entregados = last_run.get("entregados") or "?"
        lines.append(f"**On sale now:** visit date {board_fecha}, "
                      f"{entregados}/1000 delivered, "
                      f"opens {last_run.get('inicia', '?')} Lima")
    lines.append("")
    lines.append("| Route | Circuit | Allocation | Remaining | Sold | % sold |")
    lines.append("|---|---|---|---|---|---|")
    for nid in ROUTE_ORDER:
        row = snapshot.get((board_fecha, nid))
        circuito, ruta = labels.get(nid, ("?", f"nidRuta {nid}"))
        if row is None:
            lines.append(f"| {ruta} | {circuito} | ? | ? | ? | ? |")
            continue
        ncupo = int(row["ncupo"])
        remaining = int(row["ncupo_actual"])
        sold = ncupo - remaining
        pct = f"{100 * sold / ncupo:.0f}%" if ncupo else "?"
        lines.append(f"| {ruta} | {circuito} | {ncupo} | {remaining} | {sold} | {pct} |")

    lines.append("")
    lines.append("## Average sellout time by route (last 7 days)")
    lines.append("")
    lines.append("Clock time a route hit 0 remaining, averaged across visit dates in "
                  "the trailing 7 days. Widens toward the true moment only as fast as "
                  "polls land, so treat this as an upper bound until the schedule-drop "
                  "issue is fully resolved.")
    lines.append("")
    lines.append("| Route | Avg sellout (Lima) | Sample |")
    lines.append("|---|---|---|")

    events = sellout_events(obs)
    latest_ts = max((r["ts_lima"] for r in obs), default=None)
    if latest_ts:
        cutoff = datetime.fromisoformat(latest_ts) - timedelta(days=7)
    else:
        cutoff = None

    for nid in ROUTE_ORDER:
        circuito, ruta = labels.get(nid, ("?", f"nidRuta {nid}"))
        recent = [
            (fecha, ts) for fecha, ts in events.get(nid, [])
            if cutoff is None or datetime.fromisoformat(ts) >= cutoff
        ]
        if not recent:
            lines.append(f"| {ruta} | not sold out in observed data | 0 days |")
            continue
        avg = avg_clock_time([ts for _, ts in recent])
        n = len(recent)
        lines.append(f"| {ruta} | {avg} | {n} day{'s' if n != 1 else ''} |")

    lines.append("")
    lines.append("Graphs: not yet built, see \"Not yet built\" below.")
    return "\n".join(lines)


def update_readme():
    summary = build_summary()
    text = README.read_text(encoding="utf-8")
    if START_MARK not in text or END_MARK not in text:
        raise SystemExit(f"README.md is missing {START_MARK} / {END_MARK} markers")
    before, rest = text.split(START_MARK, 1)
    _, after = rest.split(END_MARK, 1)
    new_text = f"{before}{START_MARK}\n{summary}\n{END_MARK}{after}"
    README.write_text(new_text, encoding="utf-8")


if __name__ == "__main__":
    update_readme()
