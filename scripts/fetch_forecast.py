#!/usr/bin/env python3
"""
Prévisions : vent AROME 0,01° (≈ 1,3 km) et vagues MFWAM 0,025°, Météo-France open data.

Les fichiers GRIB2 sont repérés via l'API data.gouv.fr (liste des ressources de chaque
jeu de données), téléchargés, découpés sur la zone (common.BBOX) et réduits en un seul
JSON léger pour la carte. Un modèle dont le dernier run est déjà publié (--previous)
n'est pas retéléchargé.

  python3 scripts/fetch_forecast.py --previous prev/forecast.json --out public/data/forecast.json
  python3 scripts/fetch_forecast.py --discover      # inventaire des fichiers, sans téléchargement
"""

from __future__ import annotations

import argparse
import math
import os
import re
import tempfile
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import eccodes
import numpy as np

from common import BBOX, fetch, fetch_json, read_json, warn, write_json

DATASETS = {
    "arome": "paquets-arome-resolution-0-01deg",
    "waves": "paquets-de-modele-de-vagues-mfwam-resolution-0-025deg",
}
AROME_PACKAGE = os.environ.get("AROME_PACKAGE", "SP1")
MAX_LEAD_H = {"arome": 48, "waves": 72}

RUN_RE = re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}[:%3A]*\d{2}[:%3A]*\d{2}Z)", re.I)
PKG_RE = re.compile(r"__([A-Z]{2}\d)__")
LEAD_RE = re.compile(r"__(\d{2,3})H(?:(\d{2,3})H)?__")


# --- inventaire data.gouv ----------------------------------------------------

def list_resources(slug: str) -> list[dict]:
    """Toutes les ressources d'un jeu de données (API v2 paginée, repli sur la v1)."""
    out, url = [], f"https://www.data.gouv.fr/api/2/datasets/{slug}/resources/?page_size=200"
    try:
        while url:
            page = fetch_json(url)
            out += page.get("data", [])
            url = page.get("next_page")
    except Exception as exc:
        warn(f"API v2 data.gouv ({exc}), repli sur la v1")
        out = fetch_json(f"https://www.data.gouv.fr/api/1/datasets/{slug}/").get("resources", [])
    return out


def parse(resource: dict) -> dict | None:
    url = resource.get("url") or ""
    if ".grib2" not in url.lower():
        return None
    run, pkg, lead = RUN_RE.search(url), PKG_RE.search(url), LEAD_RE.search(url)
    if not (run and pkg and lead):
        return None
    run_iso = re.sub(r"%3A", ":", run.group(1), flags=re.I)
    if ":" not in run_iso:  # 2026-09-30T060000Z
        run_iso = f"{run_iso[:13]}:{run_iso[13:15]}:{run_iso[15:17]}Z"
    first = int(lead.group(1))
    last = int(lead.group(2)) if lead.group(2) else first
    return {"url": url, "run": run_iso, "pkg": pkg.group(1), "first": first, "last": last}


def inventory(model: str) -> dict:
    files = [p for p in (parse(r) for r in list_resources(DATASETS[model])) if p]
    by_run = defaultdict(list)
    for f in files:
        by_run[f["run"]].append(f)
    print(f"[{model}] {len(files)} fichiers GRIB2, runs : {sorted(by_run)[-4:]}")
    for run in sorted(by_run)[-1:]:
        pk = defaultdict(list)
        for f in by_run[run]:
            pk[f["pkg"]].append(f"{f['first']}-{f['last']}")
        for p, leads in sorted(pk.items()):
            print(f"   {run} {p}: {len(leads)} fichiers ({leads[0]} … {leads[-1]})")
    return by_run


def pick_run(by_run: dict, pkg: str | None = None) -> str | None:
    """Run le plus récent dont le paquet voulu est au complet jusqu'à l'échéance utile."""
    for run in sorted(by_run, reverse=True):
        files = [f for f in by_run[run] if pkg is None or f["pkg"] == pkg]
        if files and max(f["last"] for f in files) >= 24:
            return run
    return None


# --- lecture GRIB ------------------------------------------------------------

def crop_messages(path: str, wanted: set[str] | None = None) -> list[dict]:
    """Messages GRIB découpés sur BBOX : shortName, échéance, grille, valeurs (N→S, O→E)."""
    out = []
    with open(path, "rb") as fh:
        while True:
            gid = eccodes.codes_grib_new_from_file(fh)
            if gid is None:
                break
            try:
                short = eccodes.codes_get(gid, "shortName")
                if wanted is not None and short not in wanted:
                    continue
                if eccodes.codes_get(gid, "gridType") != "regular_ll":
                    continue
                ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
                la1 = eccodes.codes_get(gid, "latitudeOfFirstGridPointInDegrees")
                lo1 = eccodes.codes_get(gid, "longitudeOfFirstGridPointInDegrees")
                dx = eccodes.codes_get(gid, "iDirectionIncrementInDegrees")
                dy = eccodes.codes_get(gid, "jDirectionIncrementInDegrees")
                j_pos = eccodes.codes_get(gid, "jScansPositively")
                i_neg = eccodes.codes_get(gid, "iScansNegatively")
                eccodes.codes_set(gid, "missingValue", 1e20)
                vals = eccodes.codes_get_values(gid).reshape(nj, ni)
                vals = np.where(vals >= 1e19, np.nan, vals)

                lats = la1 + (dy if j_pos else -dy) * np.arange(nj)
                lons = lo1 + (-dx if i_neg else dx) * np.arange(ni)
                lons = np.where(lons > 180, lons - 360, lons)
                eps = 1e-6
                rows = np.where((lats >= BBOX["south"] - eps) & (lats <= BBOX["north"] + eps))[0]
                cols = np.where((lons >= BBOX["west"] - eps) & (lons <= BBOX["east"] + eps))[0]
                if not len(rows) or not len(cols):
                    continue
                sub = vals[np.ix_(rows, cols)]
                sub_lats, sub_lons = lats[rows], lons[cols]
                if sub_lats[0] < sub_lats[-1]:
                    sub, sub_lats = sub[::-1], sub_lats[::-1]
                if sub_lons[0] > sub_lons[-1]:
                    sub, sub_lons = sub[:, ::-1], sub_lons[::-1]
                out.append({
                    "short": short,
                    "name": eccodes.codes_get(gid, "name"),
                    "step": int(eccodes.codes_get(gid, "endStep")),
                    "level": eccodes.codes_get(gid, "level"),
                    "date": eccodes.codes_get(gid, "dataDate"),
                    "time": eccodes.codes_get(gid, "dataTime"),
                    "grid": {"la1": round(float(sub_lats[0]), 4), "lo1": round(float(sub_lons[0]), 4),
                             "dx": round(dx, 5), "dy": round(dy, 5),
                             "nx": int(sub.shape[1]), "ny": int(sub.shape[0])},
                    "values": sub,
                })
            finally:
                eccodes.codes_release(gid)
    return out


def download(url: str, tmp: str) -> str:
    path = os.path.join(tmp, url.rsplit("/", 1)[-1].split("?")[0])
    t0 = time.time()
    data = fetch(url, timeout=300)
    with open(path, "wb") as fh:
        fh.write(data)
    print(f"   {len(data) / 1e6:6.1f} Mo en {time.time() - t0:4.1f} s  {os.path.basename(path)}")
    return path


def flat(a: np.ndarray, nd: int = 1) -> list:
    """Tableau → liste JSON (NaN → null), arrondi pour alléger."""
    return [None if math.isnan(x) else round(float(x), nd) for x in a.ravel()]


def iso(run: str, step: int) -> str:
    t = datetime.fromisoformat(run.replace("Z", "+00:00")).timestamp() + step * 3600
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


def wanted_leads(files: list[dict], max_h: int) -> list[dict]:
    """Horaire jusqu'à +24 h, puis toutes les 3 h : autant de lisibilité pour 3× moins de Mo."""
    keep = [f for f in files if f["first"] <= max_h
            and (f["last"] <= 24 or f["last"] % 3 == 0 or f["first"] != f["last"])]
    return sorted(keep, key=lambda f: f["first"])


# --- AROME -------------------------------------------------------------------

U_NAMES, V_NAMES = {"10u", "u10"}, {"10v", "v10"}
GUST_NAMES = {"10fg", "fg10", "i10fg", "gust", "max_10fg"}
# AROME SP1 publie la rafale en composantes : max_10efg (est) / max_10nfg (nord)
UGUST_NAMES = {"max_10efg", "10efg", "10ugust", "ugust"}
VGUST_NAMES = {"max_10nfg", "10nfg", "10vgust", "vgust"}


def build_arome(by_run: dict, tmp: str) -> dict | None:
    run = pick_run(by_run, AROME_PACKAGE)
    if not run:
        warn(f"AROME : aucun run complet du paquet {AROME_PACKAGE}")
        return None
    files = wanted_leads([f for f in by_run[run] if f["pkg"] == AROME_PACKAGE], MAX_LEAD_H["arome"])
    print(f"[arome] run {run} : {len(files)} fichiers {AROME_PACKAGE}")
    with ThreadPoolExecutor(4) as pool:
        paths = list(pool.map(lambda f: download(f["url"], tmp), files))

    steps: dict[int, dict] = defaultdict(dict)
    grid, seen = None, set()
    for path in paths:
        for m in crop_messages(path):
            seen.add(f"{m['short']} ({m['name']}, niv {m['level']})")
            if m["short"] in U_NAMES and m["level"] == 10:
                steps[m["step"]]["u"] = m["values"]
            elif m["short"] in V_NAMES and m["level"] == 10:
                steps[m["step"]]["v"] = m["values"]
            elif m["short"] in GUST_NAMES:
                steps[m["step"]]["gust"] = m["values"]
            elif m["short"] in UGUST_NAMES:
                steps[m["step"]]["ug"] = m["values"]
            elif m["short"] in VGUST_NAMES:
                steps[m["step"]]["vg"] = m["values"]
            else:
                continue
            grid = grid or m["grid"]
        os.remove(path)
    print("[arome] paramètres lus :", "; ".join(sorted(seen)))

    times, u, v, gust = [], [], [], []
    for step in sorted(steps):
        s = steps[step]
        if "u" not in s or "v" not in s:
            continue
        g = s.get("gust")
        if g is None and "ug" in s and "vg" in s:
            g = np.hypot(s["ug"], s["vg"])
        times.append(iso(run, step))
        u.append(flat(s["u"]))
        v.append(flat(s["v"]))
        gust.append(flat(g) if g is not None else None)
    if not times:
        warn("AROME : vent à 10 m introuvable dans les fichiers (voir paramètres lus)")
        return None
    print(f"[arome] {len(times)} échéances, grille {grid['nx']}×{grid['ny']}")
    return {"model": f"AROME 0,01° ({AROME_PACKAGE})", "run": run, "unit": "m/s",
            "grid": grid, "times": times, "u": u, "v": v, "gust": gust}


# --- MFWAM -------------------------------------------------------------------

HS_NAMES = {"swh", "VHM0", "hs"}
DIR_NAMES = {"mwd", "VMDR", "dirpw"}
TP_NAMES = {"pp1d", "mwp", "perpw", "mp2", "VTPK", "VTM02"}


def build_waves(by_run: dict, tmp: str) -> dict | None:
    run = pick_run(by_run)
    if not run:
        warn("MFWAM : aucun run disponible")
        return None
    pkgs = sorted({f["pkg"] for f in by_run[run]})
    print(f"[waves] run {run}, paquets {pkgs}")

    # Le contenu exact des paquets n'est pas figé dans la doc : on ouvre le premier
    # fichier de chacun et on garde celui qui porte la hauteur significative.
    chosen, seen = None, {}
    for pkg in pkgs:
        first = min((f for f in by_run[run] if f["pkg"] == pkg), key=lambda f: f["first"])
        msgs = crop_messages(download(first["url"], tmp))
        seen[pkg] = sorted({f"{m['short']} ({m['name']})" for m in msgs})
        if any(m["short"] in HS_NAMES for m in msgs):
            chosen = pkg
            break
    for pkg, names in seen.items():
        print(f"[waves] {pkg} : {'; '.join(names)}")
    if not chosen:
        warn("MFWAM : hauteur significative introuvable")
        return None

    files = wanted_leads([f for f in by_run[run] if f["pkg"] == chosen], MAX_LEAD_H["waves"])
    with ThreadPoolExecutor(4) as pool:
        paths = list(pool.map(lambda f: download(f["url"], tmp), files))

    steps: dict[int, dict] = defaultdict(dict)
    grid = None
    for path in paths:
        for m in crop_messages(path, HS_NAMES | DIR_NAMES | TP_NAMES):
            key = ("hs" if m["short"] in HS_NAMES else "dir" if m["short"] in DIR_NAMES else "tp")
            if key == "tp" and "tp" in steps[m["step"]] and m["short"] != "pp1d":
                continue  # la période pic (pp1d) prime sur les périodes moyennes
            steps[m["step"]][key] = m["values"]
            grid = grid or m["grid"]
        os.remove(path)

    times, hs, dr, tp = [], [], [], []
    for step in sorted(steps):
        s = steps[step]
        if step > MAX_LEAD_H["waves"] or "hs" not in s:
            continue
        times.append(iso(run, step))
        hs.append(flat(s["hs"]))
        dr.append(flat(s["dir"], 0) if "dir" in s else None)
        tp.append(flat(s["tp"]) if "tp" in s else None)
    print(f"[waves] {len(times)} échéances, grille {grid['nx']}×{grid['ny']}")
    return {"model": f"MFWAM 0,025° ({chosen})", "run": run, "grid": grid,
            "times": times, "hs": hs, "dir": dr, "tp": tp}


# --- main --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--previous")
    ap.add_argument("--out")
    ap.add_argument("--discover", action="store_true")
    ap.add_argument("--force", action="store_true", help="ignorer le cache --previous")
    a = ap.parse_args()

    previous = read_json(a.previous) or {}
    result = {"generated": previous.get("generated"), "bbox": BBOX,
              "arome": previous.get("arome"), "waves": previous.get("waves")}
    changed = False
    with tempfile.TemporaryDirectory() as tmp:
        for model, builder in (("arome", build_arome), ("waves", build_waves)):
            try:
                by_run = inventory(model)
                if a.discover:
                    continue
                latest = pick_run(by_run, AROME_PACKAGE if model == "arome" else None)
                if not a.force and latest and (previous.get(model) or {}).get("run") == latest:
                    print(f"[{model}] run {latest} déjà publié, rien à faire")
                    continue
                built = builder(by_run, tmp)
                if built:
                    result[model], changed = built, True
            except Exception as exc:
                warn(f"{model} : {exc}")
    if a.discover:
        return 0
    if changed:
        result["generated"] = int(time.time())
    if not a.out:
        return 0
    if not (result["arome"] or result["waves"]):
        warn("aucune prévision disponible")
    write_json(a.out, result)
    print(f"écrit {a.out} ({os.path.getsize(a.out) / 1e6:.2f} Mo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
