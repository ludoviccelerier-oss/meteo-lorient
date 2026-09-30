#!/usr/bin/env python3
"""Coordonnées des spots de surf via Nominatim (OpenStreetMap). Outil ponctuel."""
import json, time, urllib.parse, urllib.request

QUERIES = [
    "Fort-Bloqué, Ploemeur",
    "Plage du Fort-Bloqué",
    "Fort Bloqué beach",
    "Plage de Gâvres",
    "Grande Plage Gâvres beach",
]
for q in QUERIES:
    url = "https://nominatim.openstreetmap.org/search?" + urllib.parse.urlencode(
        {"q": q, "format": "jsonv2", "limit": 3, "countrycodes": "fr"})
    req = urllib.request.Request(url, headers={"User-Agent": "meteo-lorient/1.0 (github.com/ludoviccelerier-oss/meteo-lorient)"})
    rows = json.load(urllib.request.urlopen(req, timeout=30))
    print(f"== {q}")
    for r in rows:
        print(f"   {float(r['lat']):.5f}, {float(r['lon']):.5f}  [{r.get('category')}/{r.get('type')}] {r['display_name'][:110]}")
    time.sleep(1.2)  # politique d'usage Nominatim : 1 requête / seconde
