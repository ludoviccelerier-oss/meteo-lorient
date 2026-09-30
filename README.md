# Météo Lorient

Carte gratuite du **vent réel**, des **prévisions de vent AROME** et des **vagues**
de Concarneau à Étel : Groix, rade de Lorient, Gâvres. Pour les marins, surfeurs
et kitesurfeurs du pays de Lorient.

## Ce que montre la carte

| Couche | Source | Fraîcheur |
|---|---|---|
| Vent réel (balises) | [windmorbihan.com](https://www.windmorbihan.com) — Beg Meil, Trévignon, Drenec, Kerroch, Groix, Étel… | toutes les 10 min |
| Prévision de vent | Météo-France **AROME 0,01°** (≈ 1,3 km), paquet SP1 | à chaque run, jusqu'à +48 h |
| Vagues | Météo-France **MFWAM 0,025°** (≈ 2,5 km) | à chaque run, jusqu'à +72 h |

- Curseur de temps et lecture animée ; bouton *Maintenant*.
- Clic sur la carte : vent, rafales, hauteur / période / direction des vagues au point.
- Clic sur une balise : vent moyen, rafales, direction, courbe des 6 dernières heures.
- Vitesses en nœuds. Fond OpenStreetMap + balisage OpenSeaMap.

## Fonctionnement

Aucun serveur. Une tâche GitHub Actions (`.github/workflows/update.yml`) tourne
toutes les 10 minutes :

1. `scripts/fetch_live.py` lit l'API JSON de windmorbihan et ajoute le relevé à
   l'historique 24 h (`data/live.json`).
2. `scripts/fetch_forecast.py` inventorie les fichiers AROME et MFWAM via l'API
   data.gouv.fr et prend, pour chaque heure, **le run le plus récent qui la couvre**
   (data.gouv retire les premières échéances d'un run pendant que le suivant se
   publie). Les GRIB2 sont découpés sur la zone ; un fichier déjà traité est gardé
   en cache et jamais retéléchargé. Sortie : `data/forecast.json`.
3. Page et données sont publiées sur GitHub Pages
   (<https://ludoviccelerier-oss.github.io/meteo-lorient/>).

**Netlify** sert la même page (`netlify.toml`, dossier `web/`) mais pas les données :
la page les lit sur GitHub Pages. Raison : sur l'offre gratuite Netlify, chaque
déploiement coûte 15 crédits sur 300 par mois (≈ 20 déploiements) ; publier toutes
les 10 minutes suspendrait le site en deux jours. Netlify ne redéploie donc qu'à
chaque modification du code.

Coût : 0 € (dépôt public : minutes Actions, cache et Pages gratuits).

## Mise en route (une seule fois)

1. GitHub : *Settings → Pages → Build and deployment → Source : **GitHub Actions***.
2. Netlify : *Add new site → Import from Git →* `meteo-lorient` (réglages lus dans
   `netlify.toml`, rien à saisir).

## En local

```bash
pip install -r scripts/requirements.txt
python scripts/fetch_live.py --out web/data/live.json
python scripts/fetch_forecast.py --cache state/forecast_cache.json --out web/data/forecast.json
python scripts/fetch_forecast.py --discover   # inventaire des fichiers Météo-France
python -m http.server -d web 8000
```

La zone couverte se règle dans `scripts/common.py` (`BBOX`).

## Limites connues

- L'API windmorbihan n'est pas documentée : elle peut changer sans préavis.
- GitHub ne garantit pas la cadence des tâches planifiées (retards de quelques
  minutes possibles) et les suspend après 60 jours sans activité sur le dépôt.
- Prévisions indicatives : ne remplacent pas le bulletin météo marine officiel.

## Licences

Code : MIT. Données Météo-France : [Licence Ouverte Etalab 2.0](https://www.etalab.gouv.fr/licence-ouverte-open-licence/).
Données de vent réel : © windmorbihan.com et ses partenaires (Compagnie des Ports
du Morbihan, ENVSN, Centrale Nantes / SEM-REV).
