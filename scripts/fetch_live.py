#!/usr/bin/env python3
"""
Vent réel : capteurs windmorbihan.com situés dans la zone.

API JSON publique mais non documentée (repérée par github.com/XavierKain/livexwind) :
  backend.windmorbihan.com/capteurs/list.json          liste des capteurs
  private2.windmorbihan.com/mesures/getlastalljson.json dernier relevé de tous

Chaque passage ajoute le relevé courant à l'historique publié précédemment
(--previous), conservé sur HISTORY_HOURS heures.

  python3 scripts/fetch_live.py --previous state/live.json --out public/data/live.json
"""

from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone

from common import fetch_json, in_bbox, read_json, warn, write_json

SENSORS_URL = "https://backend.windmorbihan.com/capteurs/list.json"
LATEST_URL = "https://private2.windmorbihan.com/mesures/getlastalljson.json"
HEADERS = {"Referer": "https://www.windmorbihan.com/", "Accept": "application/json"}
HISTORY_HOURS = 24


def num(v):
    try:
        return round(float(v), 1)
    except (TypeError, ValueError):
        return None


def epoch_of(row: dict) -> int | None:
    # `created` est un dict {epoch: "libellé lisible"}.
    created = row.get("created")
    if isinstance(created, dict) and created:
        try:
            return int(next(iter(created)))
        except (TypeError, ValueError):
            return None
    value = num(created)
    return int(value) if value else None


def load_sensors() -> list[dict]:
    payload = fetch_json(SENSORS_URL, headers=HEADERS)
    wind = (payload.get("sensors") or {}).get("WindSensor") or {}
    print(f"windmorbihan : {len(wind)} capteurs de vent au total")
    out = []
    for nid, s in wind.items():
        lat, lon = s.get("lat"), s.get("lng")
        inside = in_bbox(lat, lon, margin=0.02)
        print(f"  {'✔' if inside else ' '} {nid:>10}  {s.get('label')}  ({lat}, {lon})")
        if inside:
            out.append({"id": str(nid), "name": s.get("label") or f"Capteur {nid}",
                        "lat": float(lat), "lon": float(lon)})
    return out


def build(previous: dict | None) -> dict:
    sensors = load_sensors()
    rows = {str(r.get("nid")): r for r in fetch_json(LATEST_URL, headers=HEADERS)
            if isinstance(r, dict) and r.get("nid") is not None}

    prev_hist = {s["id"]: s.get("history", []) for s in (previous or {}).get("sensors", [])}
    cutoff = time.time() - HISTORY_HOURS * 3600
    out = []
    for s in sensors:
        row = rows.get(s["id"]) or {}
        t = epoch_of(row)
        last = None
        if t and (row.get("wind_pow_knot") is not None or row.get("wind_pow_knot_max") is not None):
            last = {"t": t, "avg": num(row.get("wind_pow_knot")),
                    "gust": num(row.get("wind_pow_knot_max")),
                    "dir": num(row.get("wind_dir_true")), "temp": num(row.get("t"))}
        hist = [h for h in prev_hist.get(s["id"], []) if h[0] >= cutoff]
        if last and (not hist or hist[-1][0] < last["t"]):
            hist.append([last["t"], last["avg"], last["gust"], last["dir"]])
        out.append({**s, "last": last, "history": hist})
        print(f"  {s['name']}: {last}")

    missing = [s["name"] for s in out if not s["last"]]
    if missing:
        warn(f"sans relevé : {', '.join(missing)}")
    return {"updated": int(time.time()), "unit": "kt", "source": "windmorbihan.com",
            "sensors": out}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--previous")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    previous = read_json(a.previous)
    try:
        data = build(previous)
    except Exception as exc:  # on republie l'état précédent plutôt qu'une carte vide
        warn(f"windmorbihan indisponible ({exc})")
        if not previous:
            raise
        data = {**previous, "stale": True}
    write_json(a.out, data)
    print(f"écrit {a.out} à {datetime.now(timezone.utc):%H:%M}Z")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
