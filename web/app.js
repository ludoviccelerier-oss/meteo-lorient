/* Météo Lorient — vent réel (windmorbihan), prévision AROME, vagues MFWAM. */
"use strict";

const MS_TO_KT = 1.94384;
const LIVE_REFRESH_MS = 60 * 1000;
// Les données sont publiées sur GitHub Pages (vent réel toutes les 5 min, prévisions toutes
// les heures), la page peut être servie ailleurs (Netlify) : elle va alors les y chercher.
const DATA_BASE = location.hostname.endsWith("github.io") || location.hostname === "localhost"
  ? "data/"
  : "https://ludoviccelerier-oss.github.io/meteo-lorient/data/";
// Clé de cache alignée sur la cadence de publication : le navigateur et le CDN réutilisent
// le même fichier tant qu'aucune nouvelle version ne peut exister.
const BUCKET = { live: 5 * 60 * 1000, forecast: 15 * 60 * 1000 };
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];

// Échelles de couleur : [valeur, couleur]
const WIND_STOPS = [[0, "#a6d8f0"], [6, "#7fd1a3"], [10, "#c8e65a"], [14, "#fee34a"],
  [18, "#fdae61"], [22, "#f46d43"], [28, "#d73027"], [35, "#9e0142"]];
const WAVE_STOPS = [[0, "#d6f3ff"], [0.5, "#9adcf6"], [1, "#4fb8f0"], [1.5, "#2f82d8"],
  [2, "#4b5bd0"], [3, "#8a44c8"], [4, "#c7338a"], [6, "#7a0030"]];

// Commune affichée sous le nom de chaque balise (nom windmorbihan → lieu)
const TOWNS = {
  "Beg Meil": "Fouesnant", "Pointe de Trévignon": "Trégunc", "Drenec": "Archipel des Glénan",
  "Feu de Kerroch": "Ploemeur · entrée de la rade", "Groix Sémaphore": "Île de Groix",
  "Semaphore d'Etel": "Barre d'Étel", "Isthme": "Presqu'île de Quiberon",
};

// Noms affichés (windmorbihan écrit parfois sans accents)
const NAMES = { "Semaphore d'Etel": "Sémaphore d'Étel", "Groix Sémaphore": "Sémaphore de Groix", "Drenec": "Le Drenec" };
const displayName = (s) => NAMES[s.name] || s.name;

const state = { live: null, fc: null, index: 0, playing: null, markers: {} };

// --- utilitaires ------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const compass = (d) => (d == null ? "—" : COMPASS[Math.round(d / 22.5) % 16]);
const fmt = (v, n = 0) => (v == null || Number.isNaN(v) ? "—" : v.toFixed(n));
const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const on = (id) => $(id).getAttribute("aria-pressed") === "true";

function hexToRgb(h) {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function colorAt(stops, v) {
  if (v == null || Number.isNaN(v)) return null;
  if (v <= stops[0][0]) return hexToRgb(stops[0][1]);
  for (let i = 1; i < stops.length; i++) {
    if (v <= stops[i][0]) {
      const [v0, c0] = stops[i - 1], [v1, c1] = stops[i];
      const t = (v - v0) / (v1 - v0), a = hexToRgb(c0), b = hexToRgb(c1);
      return a.map((x, k) => Math.round(x + (b[k] - x) * t));
    }
  }
  return hexToRgb(stops[stops.length - 1][1]);
}

const cssColor = (stops, v) => {
  const c = colorAt(stops, v);
  return c ? `rgb(${c.join(",")})` : "#ccc";
};

async function getJSON(name, bucket) {
  const r = await fetch(`${DATA_BASE}${name}?v=${Math.floor(Date.now() / bucket)}`);
  if (!r.ok) throw new Error(`${name} : ${r.status}`);
  return r.json();
}

function ago(epochSec) {
  const m = Math.round((Date.now() / 1000 - epochSec) / 60);
  if (m < 1) return "à l'instant";
  if (m < 60) return `il y a ${m} min`;
  const h = Math.floor(m / 60);
  return `il y a ${h} h ${String(m % 60).padStart(2, "0")}`;
}

const TZ = "Europe/Paris";
const dayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", timeZone: TZ });
const hourFmt = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: TZ });
const dateFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "long", day: "numeric", month: "long", timeZone: TZ });
const parseTime = (iso) => new Date(iso);

// Grille : valeur la plus proche d'un point (lat, lon)
function sample(grid, values, lat, lon) {
  if (!grid || !values) return null;
  const j = Math.round((grid.la1 - lat) / grid.dy);
  const i = Math.round((lon - grid.lo1) / grid.dx);
  if (i < 0 || j < 0 || i >= grid.nx || j >= grid.ny) return null;
  return values[j * grid.nx + i];
}

function gridBounds(g) {
  const south = g.la1 - g.dy * (g.ny - 1), east = g.lo1 + g.dx * (g.nx - 1);
  return L.latLngBounds([south - g.dy / 2, g.lo1 - g.dx / 2], [g.la1 + g.dy / 2, east + g.dx / 2]);
}

function arrowSvg(deg, size, color, stroke = "#333") {
  // deg : direction d'où vient le vent / la houle ; la flèche pointe là où il va.
  return `<svg width="${size}" height="${size}" viewBox="-10 -10 20 20" aria-hidden="true">
    <g transform="rotate(${(deg + 180) % 360})">
      <path d="M0,-8 L5,4 L0,1.5 L-5,4 Z" fill="${color}" stroke="${stroke}" stroke-width="1.2" stroke-linejoin="round"/>
    </g></svg>`;
}

function sparkline(history, w = 220, h = 34) {
  const pts = (history || []).filter((p) => p[0] >= Date.now() / 1000 - 6 * 3600);
  if (pts.length < 2) return "";
  const t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const vmax = Math.max(10, ...pts.map((p) => p[2] ?? p[1] ?? 0));
  const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * w;
  const y = (v) => h - 3 - ((v ?? 0) / vmax) * (h - 6);
  const line = (k) => pts.filter((p) => p[k] != null).map((p) => `${x(p[0]).toFixed(1)},${y(p[k]).toFixed(1)}`).join(" ");
  return `<svg class="spark" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none" role="img" aria-label="Vent sur les 6 dernières heures">
    <polyline points="${line(2)}" fill="none" stroke="#f46d43" stroke-width="1.2" stroke-dasharray="3 2" vector-effect="non-scaling-stroke"/>
    <polyline points="${line(1)}" fill="none" stroke="#DA4445" stroke-width="2" vector-effect="non-scaling-stroke"/>
  </svg>`;
}

// --- carte ------------------------------------------------------------------

// Zone : de Concarneau à Étel, Groix et la rade de Lorient
const ZONE = L.latLngBounds([47.58, -3.98], [47.92, -3.16]);
const map = L.map("map", { zoomControl: true, minZoom: 9 }).fitBounds(ZONE);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);
L.tileLayer("https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png", {
  maxZoom: 18, opacity: 0.9,
  attribution: '<a href="https://www.openseamap.org">OpenSeaMap</a>',
}).addTo(map);
map.attributionControl.addAttribution(
  'Vent réel <a href="https://www.windmorbihan.com">windmorbihan</a> · Prévisions © <a href="https://meteofrance.com">Météo-France</a>'
);

function rasterOverlay(grid, values, stops, opacity) {
  const canvas = document.createElement("canvas");
  canvas.width = grid.nx;
  canvas.height = grid.ny;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(grid.nx, grid.ny);
  for (let p = 0; p < values.length; p++) {
    const c = colorAt(stops, values[p]);
    if (c) img.data.set([c[0], c[1], c[2], 255], p * 4);
  }
  ctx.putImageData(img, 0, 0);
  return L.imageOverlay(canvas.toDataURL(), gridBounds(grid), { opacity, interactive: false });
}

// --- vent réel --------------------------------------------------------------

const liveLayer = L.layerGroup();
const isStale = (last) => !last || Date.now() / 1000 - last.t > 30 * 60;

function popupHtml(s) {
  const last = s.last;
  if (!last) return `<h3>${esc(displayName(s))}</h3><div class="muted">Pas de relevé récent</div>`;
  return `<h3>${esc(displayName(s))}</h3><table>
      <tr><td>Vent moyen</td><td><b>${fmt(last.avg)} kt</b></td></tr>
      <tr><td>Rafales</td><td><b>${fmt(last.gust)} kt</b></td></tr>
      <tr><td>Direction</td><td>${compass(last.dir)} (${fmt(last.dir)}°)</td></tr>
      ${last.temp != null ? `<tr><td>Température</td><td>${fmt(last.temp, 1)} °C</td></tr>` : ""}
    </table><div class="muted">Relevé ${ago(last.t)}</div>`;
}

const MAX_AGE_S = 2 * 3600;
function visibleSensors() {
  // Balises sans relevé depuis 2 h (ex. Pen Men, Lann Bihoué) : masquées. D'ouest en est.
  const now = Date.now() / 1000;
  return (state.live?.sensors || []).filter((s) => s.last && now - s.last.t < MAX_AGE_S)
    .sort((a, b) => a.lon - b.lon);
}

function renderLive() {
  liveLayer.clearLayers();
  state.markers = {};
  const sensors = visibleSensors();
  for (const s of sensors) {
    const last = s.last, color = cssColor(WIND_STOPS, last?.avg);
    const html = `<div class="pin${isStale(last) ? " stale" : ""}" style="border:2px solid ${color}">
      ${last?.dir != null ? arrowSvg(last.dir, 22, color) : ""}<span>${fmt(last?.avg)}${last?.gust != null ? `<small>/${fmt(last.gust)}</small>` : ""}</span></div>`;
    const marker = L.marker([s.lat, s.lon], {
      icon: L.divIcon({ className: "live-icon", html, iconSize: [0, 0] }), title: displayName(s),
    }).bindPopup(() => popupHtml(s), { maxWidth: 260 });
    marker.on("click", (e) => L.DomEvent.stopPropagation(e));
    liveLayer.addLayer(marker);
    state.markers[s.id] = marker;
  }

  const list = $("board-list");
  if (!sensors.length) {
    list.innerHTML = '<p class="empty">Aucune balise disponible pour le moment.</p>';
    return;
  }
  list.innerHTML = sensors.map((s) => {
    const last = s.last;
    return `<button type="button" class="station-card${isStale(last) ? " stale" : ""}" data-id="${esc(s.id)}">
      <span><span class="station-name"><span class="dot" style="background:${cssColor(WIND_STOPS, last?.avg)}"></span>${esc(displayName(s))}</span><br>
      <span class="station-town">${esc(TOWNS[s.name] || "")}</span></span>
      <span class="station-wind">${last?.dir != null ? arrowSvg(last.dir, 26, cssColor(WIND_STOPS, last.avg)) : ""}
        <span class="kt">${fmt(last?.avg)}<small> / ${fmt(last?.gust)} kt</small></span></span>
      ${sparkline(s.history)}
      <span class="station-meta"><span>${last ? `${compass(last.dir)} · ${fmt(last.dir)}°` : "—"}</span><span>${last ? ago(last.t) : "pas de relevé"}</span></span>
    </button>`;
  }).join("");
  const updated = state.live.updated;
  $("board-sub").textContent = `Balises windmorbihan, en nœuds (moyen / rafales) · mis à jour ${ago(updated)}`
    + (state.live.stale ? " · source momentanément indisponible" : "");
}

$("board-list").addEventListener("click", (e) => {
  const card = e.target.closest(".station-card");
  const marker = card && state.markers[card.dataset.id];
  if (!marker) return;
  if (!on("l-live")) setLayer("l-live", true);
  map.flyTo(marker.getLatLng(), Math.max(map.getZoom(), 11), { duration: 0.6 });
  marker.openPopup();
  if (window.matchMedia("(max-width: 900px)").matches) $("map").scrollIntoView({ behavior: "smooth", block: "center" });
});

async function loadLive() {
  try {
    state.live = await getJSON("live.json", BUCKET.live);
    renderLive();
  } catch (e) {
    console.warn(e);
  }
  renderStatus();
}

// --- prévisions -------------------------------------------------------------

const windRaster = L.layerGroup();
const waveLayer = L.layerGroup();
let velocity = null;

function velocityData(a, k) {
  const g = a.grid;
  const header = (n) => ({
    parameterCategory: 2, parameterNumber: n, lo1: g.lo1, la1: g.la1,
    lo2: g.lo1 + g.dx * (g.nx - 1), la2: g.la1 - g.dy * (g.ny - 1),
    dx: g.dx, dy: g.dy, nx: g.nx, ny: g.ny, refTime: a.times[k], forecastTime: 0,
  });
  return [{ header: header(2), data: a.u[k].map((x) => x ?? 0) },
    { header: header(3), data: a.v[k].map((x) => x ?? 0) }];
}

function waveIndexFor(time) {
  const w = state.fc?.waves;
  if (!w?.times?.length) return -1;
  const t = parseTime(time).getTime();
  let best = -1, bestDiff = Infinity;
  w.times.forEach((x, i) => {
    const d = Math.abs(parseTime(x).getTime() - t);
    if (d < bestDiff) { best = i; bestDiff = d; }
  });
  return bestDiff <= 3 * 3600 * 1000 ? best : -1;
}

function windSpeedsKt(a, k) {
  return a.u[k].map((u, p) => (u == null || a.v[k][p] == null ? null : Math.hypot(u, a.v[k][p]) * MS_TO_KT));
}

function renderForecast() {
  const a = state.fc?.arome;
  windRaster.clearLayers();
  waveLayer.clearLayers();
  if (!a?.times?.length) { renderTime(); renderSpots(); return; }
  const k = state.index;

  windRaster.addLayer(rasterOverlay(a.grid, windSpeedsKt(a, k), WIND_STOPS, 0.5));
  const data = velocityData(a, k);
  if (!velocity) {
    velocity = L.velocityLayer({
      displayValues: false, data, maxVelocity: 18, velocityScale: 0.006,
      particleMultiplier: 1 / 150, lineWidth: 1.5, frameRate: 20,
      colorScale: ["rgba(30,40,55,0.75)"],
    });
    if (on("l-wind")) velocity.addTo(map);
  } else {
    velocity.setData(data);
  }

  const w = state.fc.waves, wi = waveIndexFor(a.times[k]);
  if (wi >= 0) {
    waveLayer.addLayer(rasterOverlay(w.grid, w.hs[wi], WAVE_STOPS, 0.65));
    const g = w.grid, dirs = w.dir?.[wi];
    for (let j = 0; dirs && j < g.ny; j += 2) {
      for (let i = 0; i < g.nx; i += 2) {
        const d = dirs[j * g.nx + i];
        if (d == null) continue;
        waveLayer.addLayer(L.marker([g.la1 - j * g.dy, g.lo1 + i * g.dx], {
          icon: L.divIcon({ className: "wave-arrow", html: arrowSvg(d, 16, "#fff"), iconSize: [16, 16], iconAnchor: [8, 8] }),
          interactive: false, keyboard: false,
        }));
      }
    }
  }
  renderTime();
  renderSpots();
}

function renderTime() {
  const a = state.fc?.arome;
  if (!a?.times?.length) { $("when-label").textContent = "Prévisions indisponibles"; return; }
  const t = parseTime(a.times[state.index]);
  const diffH = Math.round((t - Date.now()) / 3600000);
  const rel = diffH === 0 ? "maintenant" : diffH > 0 ? `+${diffH} h` : `${diffH} h`;
  $("when-label").textContent = `${dayFmt.format(t)} ${hourFmt.format(t)} · ${rel}`;
}

function nearestNowIndex() {
  const times = state.fc?.arome?.times || [];
  let best = 0, bestDiff = Infinity;
  times.forEach((x, i) => {
    const d = Math.abs(parseTime(x) - Date.now());
    if (d < bestDiff) { best = i; bestDiff = d; }
  });
  return best;
}

function setIndex(i) {
  state.index = i;
  $("slider").value = i;
  renderForecast();
}

async function loadForecast() {
  const previous = state.fc?.generated;
  try {
    state.fc = await getJSON("forecast.json", BUCKET.forecast);
  } catch (e) {
    console.warn(e);
  }
  if (state.fc?.generated === previous && previous) return; // rien de nouveau
  const n = state.fc?.arome?.times?.length || 0;
  $("slider").max = Math.max(0, n - 1);
  $("slider").disabled = n === 0;
  setIndex(nearestNowIndex());
  renderStatus();
}

// --- spots de surf ------------------------------------------------------------

const spotLayer = L.layerGroup();
const dirFrom = (u, v) => (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360;

function spotAt(spot, time) {
  // Valeurs du spot à l'heure choisie (échéance la plus proche, à 3 h près)
  const out = {};
  const t = parseTime(time).getTime();
  const pick = (times) => {
    let best = -1, diff = Infinity;
    times.forEach((x, i) => { const d = Math.abs(parseTime(x) - t); if (d < diff) { diff = d; best = i; } });
    return diff <= 3 * 3600 * 1000 ? best : -1;
  };
  const w = spot.waves;
  if (w) {
    const i = pick(w.times);
    if (i >= 0) Object.assign(out, { hs: w.hs[i], tp: w.tp[i], wdir: w.dir[i] });
  }
  const v = spot.wind;
  if (v) {
    const i = pick(v.times);
    if (i >= 0 && v.u[i] != null && v.v[i] != null) {
      out.wind = Math.hypot(v.u[i], v.v[i]) * MS_TO_KT;
      out.windDir = dirFrom(v.u[i], v.v[i]);
      out.gust = v.gust?.[i] != null ? v.gust[i] * MS_TO_KT : null;
    }
  }
  return out;
}

function hsBars(spot) {
  // Hauteur des vagues sur les 48 h à venir, une barre par échéance
  const w = spot.waves;
  if (!w) return "";
  const now = Date.now() - 3600 * 1000;
  const pts = w.times.map((t, i) => [parseTime(t).getTime(), w.hs[i]]).filter(([t, v]) => t >= now && v != null);
  if (pts.length < 2) return "";
  const max = Math.max(1.5, ...pts.map((p) => p[1])), W = 220, H = 30, bw = W / pts.length;
  const sel = state.fc?.arome?.times?.[state.index] ? parseTime(state.fc.arome.times[state.index]).getTime() : 0;
  return `<svg class="spark" viewBox="0 0 ${W} ${H}" preserveAspectRatio="none" role="img" aria-label="Hauteur des vagues sur 48 heures">
    ${pts.map(([t, v], k) => {
      const h = Math.max(1, (v / max) * (H - 2));
      const on = Math.abs(t - sel) < 1.6 * 3600 * 1000;
      return `<rect x="${(k * bw + 0.5).toFixed(1)}" y="${(H - h).toFixed(1)}" width="${Math.max(1, bw - 1).toFixed(1)}" height="${h.toFixed(1)}" fill="${on ? "#DA4445" : cssColor(WAVE_STOPS, v)}"/>`;
    }).join("")}
  </svg>`;
}

function spotPopup(spot, x) {
  return `<h3>${esc(spot.name)}</h3><div class="muted">${esc(spot.town)} · ${$("when-label").textContent}</div>
    <table>
      <tr><td>Vagues</td><td><b>${fmt(x.hs, 1)} m</b></td></tr>
      <tr><td>Période</td><td><b>${fmt(x.tp)} s</b></td></tr>
      <tr><td>Houle de</td><td>${compass(x.wdir)}${x.wdir != null ? ` (${fmt(x.wdir)}°)` : ""}</td></tr>
      <tr><td>Vent</td><td>${fmt(x.wind)} kt ${compass(x.windDir)}${x.gust != null ? ` · raf. ${fmt(x.gust)}` : ""}</td></tr>
    </table>
    ${spot.waves ? `<div class="muted">Modèle MFWAM : point de mer à ${String(spot.waves.km).replace(".", ",")} km du spot</div>` : ""}`;
}

function renderSpots() {
  spotLayer.clearLayers();
  const spots = state.fc?.spots || [];
  const time = state.fc?.arome?.times?.[state.index] || state.fc?.waves?.times?.[0];
  const list = $("spot-list");
  if (!spots.length || !time) {
    list.innerHTML = '<p class="empty">Prévisions de vagues indisponibles pour le moment.</p>';
    return;
  }
  $("spot-sub").textContent = `Hauteur, période et direction de la houle · ${$("when-label").textContent}`;
  list.innerHTML = spots.map((spot) => {
    const x = spotAt(spot, time);
    const color = cssColor(WAVE_STOPS, x.hs);
    spotLayer.addLayer(L.marker([spot.lat, spot.lon], {
      icon: L.divIcon({ className: "live-icon", iconSize: [0, 0],
        html: `<div class="pin spot-pin" style="border:2px solid ${color}">${x.wdir != null ? arrowSvg(x.wdir, 20, color) : ""}<span>${fmt(x.hs, 1)}<small> m · ${fmt(x.tp)} s</small></span></div>` }),
      title: spot.name,
    }).bindPopup(spotPopup(spot, x), { maxWidth: 260 }).on("click", (e) => L.DomEvent.stopPropagation(e)));
    return `<button type="button" class="station-card spot-card" data-spot="${esc(spot.id)}">
      <span><span class="station-name"><span class="dot" style="background:${color}"></span>${esc(spot.name)}</span><br>
      <span class="station-town">${esc(spot.town)}</span></span>
      <span class="station-wind">${x.wdir != null ? arrowSvg(x.wdir, 24, color) : ""}
        <span class="kt">${fmt(x.hs, 1)}<small> m · ${fmt(x.tp)} s</small></span></span>
      ${hsBars(spot)}
      <span class="station-meta"><span>Houle de ${compass(x.wdir)}</span><span>Vent ${fmt(x.wind)} kt ${compass(x.windDir)}</span></span>
    </button>`;
  }).join("");
}

$("spot-list").addEventListener("click", (e) => {
  const card = e.target.closest(".spot-card");
  const spot = card && (state.fc?.spots || []).find((s) => s.id === card.dataset.spot);
  if (!spot) return;
  if (!on("l-spots")) setLayer("l-spots", true);
  map.flyTo([spot.lat, spot.lon], Math.max(map.getZoom(), 12), { duration: 0.6 });
  const time = state.fc.arome?.times?.[state.index];
  L.popup({ maxWidth: 260 }).setLatLng([spot.lat, spot.lon]).setContent(spotPopup(spot, spotAt(spot, time))).openOn(map);
  if (window.matchMedia("(max-width: 900px)").matches) $("map").scrollIntoView({ behavior: "smooth", block: "center" });
});

// --- clic sur la carte : prévision au point ---------------------------------

map.on("click", (e) => {
  const a = state.fc?.arome;
  if (!a?.times?.length) return;
  const { lat, lng } = e.latlng, k = state.index;
  const u = sample(a.grid, a.u[k], lat, lng), v = sample(a.grid, a.v[k], lat, lng);
  const g = sample(a.grid, a.gust?.[k], lat, lng);
  const wind = u != null && v != null ? Math.hypot(u, v) * MS_TO_KT : null;
  const dir = u != null && v != null ? (Math.atan2(-u, -v) * 180 / Math.PI + 360) % 360 : null;

  const w = state.fc.waves, wi = waveIndexFor(a.times[k]);
  const hs = wi >= 0 ? sample(w.grid, w.hs[wi], lat, lng) : null;
  const tp = wi >= 0 ? sample(w.grid, w.tp?.[wi], lat, lng) : null;
  const wd = wi >= 0 ? sample(w.grid, w.dir?.[wi], lat, lng) : null;

  L.popup({ maxWidth: 240 }).setLatLng(e.latlng).setContent(`<h3>Prévision · ${$("when-label").textContent}</h3>
    <table>
      <tr><td>Vent</td><td><b>${fmt(wind)} kt</b> ${compass(dir)}</td></tr>
      <tr><td>Rafales</td><td><b>${fmt(g != null ? g * MS_TO_KT : null)} kt</b></td></tr>
      <tr><td>Vagues</td><td><b>${fmt(hs, 1)} m</b>${tp != null ? ` · ${fmt(tp)} s` : ""}${wd != null ? ` · de ${compass(wd)}` : ""}</td></tr>
    </table><div class="muted">${lat.toFixed(3)}, ${lng.toFixed(3)} · AROME / MFWAM</div>`).openOn(map);
});

// --- interface ----------------------------------------------------------------

function legendScale(title, stops) {
  return `<div><div>${title}</div><div class="scale">${stops.map(([v, c]) => `<span style="background:${c}">${v}</span>`).join("")}</div></div>`;
}

function renderLegend() {
  const parts = [];
  if (on("l-live") || on("l-wind")) parts.push(legendScale("Vent (nœuds)", WIND_STOPS));
  if (on("l-waves") || on("l-spots")) parts.push(legendScale("Hauteur des vagues (m)", WAVE_STOPS));
  $("legend").innerHTML = parts.join("");
}

function renderStatus() {
  const bits = [];
  if (state.live) bits.push(`Vent réel ${ago(state.live.updated)}`);
  if (state.fc?.generated) bits.push(`Prévisions ${ago(state.fc.generated)}`);
  $("map-status").textContent = bits.join(" · ");
}

function renderClock() {
  const now = new Date();
  $("clock-time").textContent = hourFmt.format(now);
  $("clock-date").textContent = dateFmt.format(now);
}

const LAYERS = {
  "l-live": (show) => (show ? liveLayer.addTo(map) : map.removeLayer(liveLayer)),
  "l-wind": (show) => {
    if (show) { windRaster.addTo(map); velocity?.addTo(map); }
    else { map.removeLayer(windRaster); if (velocity) map.removeLayer(velocity); }
  },
  "l-waves": (show) => (show ? waveLayer.addTo(map) : map.removeLayer(waveLayer)),
  "l-spots": (show) => (show ? spotLayer.addTo(map) : map.removeLayer(spotLayer)),
};

function setLayer(id, show) {
  $(id).setAttribute("aria-pressed", String(show));
  LAYERS[id](show);
  renderLegend();
}

for (const id of Object.keys(LAYERS)) {
  $(id).addEventListener("click", () => setLayer(id, !on(id)));
  setLayer(id, on(id));
}

$("slider").addEventListener("input", (e) => setIndex(Number(e.target.value)));
$("now").addEventListener("click", () => setIndex(nearestNowIndex()));
$("play").addEventListener("click", () => {
  if (state.playing) {
    clearInterval(state.playing);
    state.playing = null;
    $("play").textContent = "▶";
    $("play").setAttribute("aria-label", "Lecture de l'animation");
    return;
  }
  $("play").textContent = "❚❚";
  $("play").setAttribute("aria-label", "Pause");
  state.playing = setInterval(() => {
    const n = Number($("slider").max) + 1;
    setIndex((state.index + 1) % Math.max(1, n));
  }, 900);
});

renderClock();
loadLive();
loadForecast();
setInterval(renderClock, 15 * 1000);
setInterval(loadLive, LIVE_REFRESH_MS);
setInterval(loadForecast, 10 * 60 * 1000);
setInterval(renderStatus, 60 * 1000);
