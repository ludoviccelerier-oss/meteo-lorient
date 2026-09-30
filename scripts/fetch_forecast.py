#!/usr/bin/env python3
"""
Prévisions : vent AROME 0,01° (≈ 1,3 km) et vagues MFWAM 0,025°, Météo-France open data.

Les fichiers GRIB2 sont repérés via l'API data.gouv.fr (liste des ressources de chaque
jeu de données), téléchargés, découpés sur la zone (common.BBOX) et réduits en un seul
JSON léger pour la carte. Chaque heure prend le run le plus récent qui la couvre ;
un fichier déjà découpé n'est jamais retéléchargé
(dossier --state, conservé entre passages par GitHub Actions). Les prévisions ne sont
recalculées qu'une fois par heure (--min-interval) ; entre-temps la dernière sortie est
republiée telle quelle.

  python3 scripts/fetch_forecast.py --state state --out public/data/forecast.json
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

from common import BBOX, SURF_SPOTS, fetch, fetch_json, fetch_range, read_json, warn, write_json

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


# --- téléchargement ciblé ----------------------------------------------------------
#
# Un fichier AROME SP1 (≈ 23 Mo) porte 6 paramètres, dont 2 inutiles ici (température,
# humidité) ; un fichier MFWAM (≈ 5 Mo) en porte 13, dont 3 utiles. Chaque message GRIB2
# commence par un en-tête qui donne sa longueur et son paramètre : on lit ces en-têtes
# par requêtes partielles (Range) et on ne télécharge que les messages utiles.

HEAD_BYTES = 2048
STATS = defaultdict(lambda: {"fetched": 0, "full": 0})


def signature_from_head(head: bytes) -> tuple | None:
    """(discipline, modèle de produit, catégorie, numéro) lus dans les sections 0 et 4."""
    pos = 16
    while pos + 11 <= len(head):
        length = int.from_bytes(head[pos:pos + 4], "big")
        if head[pos + 4] == 4:
            return (head[6], int.from_bytes(head[pos + 7:pos + 9], "big"), head[pos + 9], head[pos + 10])
        if length <= 0:
            return None
        pos += length
    return None


def signature_of(gid) -> tuple:
    return tuple(eccodes.codes_get(gid, k) for k in
                 ("discipline", "productDefinitionTemplateNumber", "parameterCategory", "parameterNumber"))


def calibrate(path: str, names: set[str]) -> list[list]:
    """Signatures des paramètres utiles, apprises sur un fichier complet."""
    found = {}
    with open(path, "rb") as fh:
        while (gid := eccodes.codes_grib_new_from_file(fh)) is not None:
            try:
                short = eccodes.codes_get(gid, "shortName")
                if short in names:
                    found[short] = list(signature_of(gid))
            finally:
                eccodes.codes_release(gid)
    if "pp1d" in found:  # la période pic suffit : inutile de télécharger les périodes moyennes
        found = {k: v for k, v in found.items() if k == "pp1d" or k not in TP_NAMES}
    return list(found.values())


def ranged_download(url: str, sigs: list[list], tmp: str, model: str) -> str | None:
    """Ne télécharge que les messages dont la signature est attendue ; None si impossible."""
    wanted = {tuple(x) for x in sigs}
    offset, total, parts, fetched = 0, None, [], 0
    while total is None or offset < total:
        head, size, partial = fetch_range(url, offset, offset + HEAD_BYTES - 1)
        if not partial:
            return None
        total = total or size
        fetched += len(head)
        if head[:4] != b"GRIB":
            return None
        length = int.from_bytes(head[8:16], "big")
        if signature_from_head(head) in wanted:
            if length <= len(head):
                parts.append(head[:length])
            else:
                body, _, _ = fetch_range(url, offset, offset + length - 1)
                parts.append(body)
                fetched += len(body)
        offset += length
    STATS[model]["fetched"] += fetched
    STATS[model]["full"] += total or 0
    path = os.path.join(tmp, url.rsplit("/", 1)[-1].split("?")[0])
    with open(path, "wb") as fh:
        fh.write(b"".join(parts))
    return path


def select_files(files: list[dict], max_h: int, now: float) -> list[dict]:
    """Pour chaque heure à venir, le fichier du run le plus récent qui la couvre.

    data.gouv retire au fil de l'eau les premières échéances de chaque run, pendant que
    le run suivant se publie : un seul run laisserait un trou sur les heures en cours.
    Horaire jusqu'à +24 h, puis toutes les 3 h (3× moins de Mo, lisibilité identique).
    """
    start = (int(now) // 3600 - 1) * 3600
    best: dict[int, dict] = {}
    for f in files:
        if f["first"] != f["last"]:
            continue  # paquets par tranche d'échéances : non utilisés par ces jeux de données
        valid = int(datetime.fromisoformat(f["run"].replace("Z", "+00:00")).timestamp()) + f["last"] * 3600
        ahead = (valid - start) // 3600
        if ahead < 0 or ahead > max_h or (ahead > 25 and (valid // 3600) % 3):
            continue
        if valid not in best or f["run"] > best[valid]["run"]:
            best[valid] = {**f, "valid": valid}
    return [best[v] for v in sorted(best)]


def iso_of(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%MZ")


# --- extraction par fichier ------------------------------------------------------

U_NAMES, V_NAMES = {"10u", "u10"}, {"10v", "v10"}
GUST_NAMES = {"10fg", "fg10", "i10fg", "gust", "max_10fg"}
# AROME SP1 publie la rafale en composantes : max_10efg (est) / max_10nfg (nord)
UGUST_NAMES = {"max_10efg", "10efg", "10ugust", "ugust"}
VGUST_NAMES = {"max_10nfg", "10nfg", "10vgust", "vgust"}

HS_NAMES = {"swh", "VHM0", "hs"}
DIR_NAMES = {"mwd", "VMDR", "dirpw"}
TP_NAMES = {"pp1d", "mwp", "perpw", "mp2", "VTPK", "VTM02"}


def extract_arome(path: str) -> dict | None:
    got, grid, seen = {}, None, set()
    for m in crop_messages(path):
        seen.add(m["short"])
        s = m["short"]
        key = ("u" if s in U_NAMES and m["level"] == 10 else "v" if s in V_NAMES and m["level"] == 10
               else "gust" if s in GUST_NAMES else "ug" if s in UGUST_NAMES
               else "vg" if s in VGUST_NAMES else None)
        if key:
            got[key], grid = m["values"], grid or m["grid"]
    if "u" not in got or "v" not in got:
        warn(f"AROME : vent à 10 m absent de {os.path.basename(path)} (lus : {sorted(seen)})")
        return None
    g = got.get("gust")
    if g is None and "ug" in got and "vg" in got:
        g = np.hypot(got["ug"], got["vg"])
    return {"grid": grid, "u": flat(got["u"]), "v": flat(got["v"]),
            "gust": flat(g) if g is not None else None}


def extract_waves(path: str) -> dict | None:
    got, grid = {}, None
    for m in crop_messages(path, HS_NAMES | DIR_NAMES | TP_NAMES):
        key = "hs" if m["short"] in HS_NAMES else "dir" if m["short"] in DIR_NAMES else "tp"
        if key == "tp" and "tp" in got and m["short"] != "pp1d":
            continue  # la période pic (pp1d) prime sur les périodes moyennes
        got[key], grid = m["values"], grid or m["grid"]
    if "hs" not in got:
        warn(f"MFWAM : hauteur significative absente de {os.path.basename(path)}")
        return None
    return {"grid": grid, "hs": flat(got["hs"]),
            "dir": flat(got["dir"], 0) if "dir" in got else None,
            "tp": flat(got["tp"]) if "tp" in got else None}


MODELS = {
    "arome": {"package": AROME_PACKAGE, "fields": ("u", "v", "gust"), "extract": extract_arome,
              "names": U_NAMES | V_NAMES | GUST_NAMES | UGUST_NAMES | VGUST_NAMES,
              "label": f"AROME 0,01° ({AROME_PACKAGE})", "extra": {"unit": "m/s"}},
    "waves": {"package": os.environ.get("WAVES_PACKAGE", "SP1"), "fields": ("hs", "dir", "tp"),
              "extract": extract_waves, "label": "MFWAM 0,025°", "extra": {},
              "names": HS_NAMES | DIR_NAMES | TP_NAMES},
}


def build(model: str, by_run: dict, cache: dict, tmp: str) -> dict | None:
    spec = MODELS[model]
    files = [f for run in by_run.values() for f in run if f["pkg"] == spec["package"]]
    chosen = select_files(files, MAX_LEAD_H[model], time.time())
    if not chosen:
        warn(f"{model} : aucun fichier {spec['package']} dans la fenêtre de prévision")
        return None
    todo = [f for f in chosen if f["url"] not in cache]
    sigs = cache.get("_sigs")
    runs = sorted({f["run"] for f in chosen})
    print(f"[{model}] {len(chosen)} échéances (runs {', '.join(r[5:16] for r in runs)}), "
          f"{len(todo)} fichier(s) à télécharger")

    def full(f):
        path = download(f["url"], tmp)
        size = os.path.getsize(path)
        STATS[model]["fetched"] += size
        STATS[model]["full"] += size
        return path

    def work(f, path=None):
        try:
            if path is None and sigs:
                path = ranged_download(f["url"], sigs, tmp, model)
            ranged = path is not None and sigs is not None
            path = path or full(f)
            try:
                data = spec["extract"](path)
            finally:
                os.remove(path)
            if data is None and ranged:  # signatures périmées : on retente en entier
                path = full(f)
                try:
                    data = spec["extract"](path)
                finally:
                    os.remove(path)
            return f["url"], data
        except Exception as exc:  # un fichier défectueux ne doit pas priver la carte des autres
            warn(f"{model} : {os.path.basename(f['url'])} ignoré ({exc})")
            return f["url"], None

    # Premier fichier téléchargé en entier pour apprendre où sont les paramètres utiles.
    if todo and not sigs:
        first = todo.pop(0)
        path = full(first)
        sigs = calibrate(path, spec["names"])
        cache["_sigs"] = sigs
        print(f"[{model}] signatures utiles : {sigs}")
        url, data = work(first, path)
        if data:
            cache[url] = data

    with ThreadPoolExecutor(4) as pool:
        for url, data in pool.map(work, todo):
            if data:
                cache[url] = data
    st = STATS[model]
    if st["full"]:
        print(f"[{model}] téléchargé {st['fetched'] / 1e6:.1f} Mo sur {st['full'] / 1e6:.1f} Mo "
              f"({100 * st['fetched'] / st['full']:.0f} %)")

    ok = [f for f in chosen if cache.get(f["url"])]
    if not ok:
        return None
    grid = cache[ok[-1]["url"]]["grid"]
    ok = [f for f in ok if cache[f["url"]]["grid"] == grid]  # grille homogène
    out = {"model": spec["label"], "run": ok[-1]["run"] if ok else None,
           "runs": sorted({f["run"] for f in ok}), "grid": grid,
           "times": [iso_of(f["valid"]) for f in ok], **spec["extra"]}
    for field in spec["fields"]:
        out[field] = [cache[f["url"]][field] for f in ok]
    out["run"] = max(out["runs"])
    return out


# --- spots de surf ------------------------------------------------------------

def nearest_cell(grid: dict, values: list, lat: float, lon: float, max_ring: int = 3):
    """Indice de la maille valide la plus proche (les mailles côtières MFWAM sont souvent
    marquées terre : on cherche la mer dans un rayon de max_ring mailles)."""
    j0 = round((grid["la1"] - lat) / grid["dy"])
    i0 = round((lon - grid["lo1"]) / grid["dx"])
    best = None
    for j in range(j0 - max_ring, j0 + max_ring + 1):
        for i in range(i0 - max_ring, i0 + max_ring + 1):
            if not (0 <= i < grid["nx"] and 0 <= j < grid["ny"]):
                continue
            if values[j * grid["nx"] + i] is None:
                continue
            clat, clon = grid["la1"] - j * grid["dy"], grid["lo1"] + i * grid["dx"]
            d = math.hypot((clat - lat) * 111.2, (clon - lon) * 111.2 * math.cos(math.radians(lat)))
            if best is None or d < best[0]:
                best = (d, j * grid["nx"] + i, clat, clon)
    return best


def build_spots(result: dict) -> list[dict]:
    """Séries temporelles par spot : vagues (Hs, période pic, direction) et vent AROME."""
    w, a = result.get("waves"), result.get("arome")
    out = []
    for spot in SURF_SPOTS:
        item = {**spot}
        if w and w["times"]:
            cell = nearest_cell(w["grid"], w["hs"][0], spot["lat"], spot["lon"])
            if cell:
                d, p, clat, clon = cell
                item["waves"] = {"cell": [round(clat, 3), round(clon, 3)], "km": round(d, 1),
                                 "times": w["times"],
                                 "hs": [row[p] if row else None for row in w["hs"]],
                                 "tp": [row[p] if row else None for row in w["tp"]],
                                 "dir": [row[p] if row else None for row in w["dir"]]}
            else:
                warn(f"spot {spot['name']} : aucune maille de mer MFWAM à moins de 3 mailles")
        if a and a["times"]:
            cell = nearest_cell(a["grid"], a["u"][0], spot["lat"], spot["lon"], max_ring=0)
            if cell:
                p = cell[1]
                item["wind"] = {"times": a["times"],
                                "u": [row[p] for row in a["u"]], "v": [row[p] for row in a["v"]],
                                "gust": [row[p] if row else None for row in a["gust"]]}
        out.append(item)
    return out


# --- main --------------------------------------------------------------------

def summary(result: dict) -> str:
    """Contrôle de vraisemblance, affiché dans le résumé de chaque passage GitHub Actions."""
    lines = []
    a, w = result.get("arome"), result.get("waves")
    if a:
        speeds = [math.hypot(u, v) * 1.94384 for k in range(len(a["times"]))
                  for u, v in zip(a["u"][k], a["v"][k]) if u is not None and v is not None]
        gusts = [g * 1.94384 for row in a["gust"] if row for g in row if g is not None]
        lines.append(f"AROME runs {', '.join(a['runs'])} : {len(a['times'])} échéances {a['times'][0]} → {a['times'][-1]}, "
                     f"vent max {max(speeds):.0f} kt, rafale max {max(gusts):.0f} kt" if gusts else
                     f"AROME : {len(a['times'])} échéances, SANS rafales")
    if w:
        hs = [x for row in w["hs"] for x in row if x is not None]
        lines.append(f"MFWAM runs {', '.join(w['runs'])} : {len(w['times'])} échéances {w['times'][0]} → {w['times'][-1]}, "
                     f"Hs {min(hs):.1f}–{max(hs):.1f} m")
    for s in result.get("spots") or []:
        wv = s.get("waves")
        if wv:
            hs = [x for x in wv["hs"] if x is not None]
            tp = [x for x in wv["tp"] if x is not None]
            lines.append(f"Spot {s['name']} : maille à {wv['km']} km, Hs {min(hs):.1f}–{max(hs):.1f} m, "
                         f"période {min(tp):.0f}–{max(tp):.0f} s" if hs and tp else f"Spot {s['name']} : valeurs vides")
        else:
            lines.append(f"Spot {s['name']} : SANS vagues")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--state", default="state",
                    help="dossier conservé entre passages : cache des fichiers découpés, dernière sortie")
    ap.add_argument("--out")
    ap.add_argument("--min-interval", type=int, default=55 * 60,
                    help="délai minimal entre deux rafraîchissements des prévisions (s)")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--discover", action="store_true")
    a = ap.parse_args()

    cache_path = os.path.join(a.state, "forecast_cache.json")
    last_path = os.path.join(a.state, "forecast.json")
    last = read_json(last_path)
    if (a.out and last and not a.force and not a.discover
            and time.time() - (last.get("generated") or 0) < a.min_interval):
        write_json(a.out, last)
        age = (time.time() - last["generated"]) / 60
        print(f"prévisions de il y a {age:.0f} min réutilisées (rafraîchissement toutes les "
              f"{a.min_interval // 60 + 5} min environ)")
        return 0

    cache_all = read_json(cache_path) or {}
    result = {"generated": int(time.time()), "bbox": BBOX, "arome": None, "waves": None}
    with tempfile.TemporaryDirectory() as tmp:
        for model in MODELS:
            try:
                by_run = inventory(model)
                if a.discover:
                    continue
                cache = cache_all.get(model) or {}
                result[model] = build(model, by_run, cache, tmp)
                # on ne garde en cache que les fichiers encore listés par data.gouv
                used = {x["url"] for f in by_run.values() for x in f} | {"_sigs"}
                cache_all[model] = {u: d for u, d in cache.items() if u in used}
            except Exception as exc:
                warn(f"{model} : {exc}")
            if not result[model] and last and last.get(model):
                warn(f"{model} : on republie les prévisions précédentes")
                result[model] = last[model]
    if a.discover or not a.out:
        return 0
    write_json(cache_path, cache_all)
    if not (result["arome"] or result["waves"]):
        warn("aucune prévision disponible")
    result["spots"] = build_spots(result)
    write_json(a.out, result)
    write_json(last_path, result)
    report = summary(result)
    print(report)
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("### Prévisions\n\n" + report.replace("\n", "  \n") + "\n")
    print(f"écrit {a.out} ({os.path.getsize(a.out) / 1e6:.2f} Mo)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
