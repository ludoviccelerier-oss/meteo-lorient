"""Réglages partagés : zone couverte, accès réseau, écriture JSON."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

# Zone : de Concarneau (ouest) à la ria d'Étel (est), Groix et la rade de Lorient.
BBOX = {"south": 47.55, "north": 47.95, "west": -4.05, "east": -3.10}

# Les 6 spots de surf les plus connus de la zone, d'ouest en est.
# Coordonnées : OpenStreetMap / Nominatim (plages, relevé du 30/09/2026).
SURF_SPOTS = [
    {"id": "kerou", "name": "Le Kérou", "town": "Le Pouldu, Clohars-Carnoët", "lat": 47.7678, "lon": -3.5625},
    {"id": "loch", "name": "Le Loc'h", "town": "Guidel-Plages", "lat": 47.7544, "lon": -3.5142},
    {"id": "fort-bloque", "name": "Fort-Bloqué", "town": "Ploemeur", "lat": 47.7350, "lon": -3.5048},
    {"id": "perello", "name": "Le Pérello", "town": "Ploemeur", "lat": 47.6994, "lon": -3.4451},
    {"id": "gavres", "name": "Grande plage de Gâvres", "town": "Gâvres · Plouhinec", "lat": 47.6762, "lon": -3.2675},
    {"id": "kerhillio", "name": "Kerhillio", "town": "Erdeven", "lat": 47.6108, "lon": -3.1675},
]

UA = "meteo-lorient/1.0 (+https://github.com/ludoviccelerier-oss/meteo-lorient)"


def in_bbox(lat, lon, margin: float = 0.0) -> bool:
    try:
        lat, lon = float(lat), float(lon)
    except (TypeError, ValueError):
        return False
    return (BBOX["south"] - margin <= lat <= BBOX["north"] + margin
            and BBOX["west"] - margin <= lon <= BBOX["east"] + margin)


def fetch(url: str, headers: dict | None = None, timeout: int = 60, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = Request(url, headers={"User-Agent": UA, **(headers or {})})
            with urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (HTTPError, URLError, TimeoutError) as exc:
            last = exc
            if isinstance(exc, HTTPError) and exc.code in (403, 404):
                break
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"{url} : {last}")


def fetch_range(url: str, start: int, end: int, timeout: int = 60) -> tuple[bytes, int | None, bool]:
    """Octets [start, end] d'un fichier : (données, taille totale, requête partielle honorée)."""
    last = None
    for attempt in range(3):
        try:
            req = Request(url, headers={"User-Agent": UA, "Range": f"bytes={start}-{end}"})
            with urlopen(req, timeout=timeout) as resp:
                data = resp.read()
                if resp.status == 206:
                    total = resp.headers.get("Content-Range", "").rsplit("/", 1)[-1]
                    return data, int(total) if total.isdigit() else None, True
                return data, len(data), False  # serveur sans Range : fichier complet
        except (HTTPError, URLError, TimeoutError) as exc:
            last = exc
            time.sleep(2 ** (attempt + 1))
    raise RuntimeError(f"{url} : {last}")


def fetch_json(url: str, **kw):
    return json.loads(fetch(url, **kw).decode("utf-8", errors="replace"))


def read_json(path: str | None):
    if not path or not os.path.exists(path):
        return None
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: str, data) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")),
                          encoding="utf-8")


def warn(msg: str) -> None:
    """Avertissement visible dans le résumé GitHub Actions."""
    print(f"::warning::{msg}", file=sys.stderr)
