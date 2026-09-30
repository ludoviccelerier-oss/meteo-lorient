# Météo Lorient

Carte gratuite du **vent réel**, des **prévisions de vent AROME** et des **vagues**
de Concarneau à Étel : Groix, rade de Lorient, Gâvres. Pour les marins, surfeurs
et kitesurfeurs du pays de Lorient.

## Ce que montre la carte

| Couche | Source | Fraîcheur |
|---|---|---|
| Vent réel (balises) | [windmorbihan.com](https://www.windmorbihan.com) — Kerroch, Groix… | toutes les 10 min |
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
2. `scripts/fetch_forecast.py` repère le dernier run AROME et MFWAM via l'API
   data.gouv.fr, télécharge les GRIB2, les découpe sur la zone et produit
   `data/forecast.json`. Un run déjà publié n'est pas retéléchargé.
3. La page statique (`web/`) et les données sont publiées sur GitHub Pages.

Coût : 0 € (dépôt public : minutes Actions et Pages gratuites).

## Mise en route (une seule fois)

*Settings → Pages → Build and deployment → Source : **GitHub Actions***, puis
*Actions → Mise à jour de la carte → Run workflow*.

## En local

```bash
pip install -r scripts/requirements.txt
python scripts/fetch_live.py --out web/data/live.json
python scripts/fetch_forecast.py --out web/data/forecast.json
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
