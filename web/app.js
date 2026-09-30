/* Météo Lorient — vent réel (windmorbihan), prévision AROME, vagues MFWAM. */
"use strict";

const MS_TO_KT = 1.94384;
const LIVE_REFRESH_MS = 2 * 60 * 1000;
const COMPASS = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
  "S", "SSO", "SO", "OSO", "O", "ONO", "NO", "NNO"];

// Échelles de couleur : [valeur, couleur]
const WIND_STOPS = [[0, "#a6d8f0"], [6, "#7fd1a3"], [10, "#c8e65a"], [14, "#fee34a"],
  [18, "#fdae61"], [22, "#f46d43"], [28, "#d73027"], [35, "#9e0142"]];
const WAVE_STOPS = [[0, "#d6f3ff"], [0.5, "#9adcf6"], [1, "#4fb8f0"], [1.5, "#2f82d8"],
  [2, "#4b5bd0"], [3, "#8a44c8"], [4, "#c7338a"], [6, "#7a0030"]];

const state = { live: null, fc: null, index: 0, playing: null, layers: {} };

// --- utilitaires ------------------------------------------------------------

const $ = (id) => document.getElementById(id);
const compass = (d) => (d == null ? "—" : COMPASS[Math.round(d / 22.5) % 16]);
const fmt = (v, n = 0) => (v == null || Number.isNaN(v) ? "—" : v.toFixed(n));

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

async function getJSON(url) {
  const r = await fetch(`${url}?t=${Math.floor(Date.now() / 60000)}`);
  if (!r.ok) throw new Error(`${url} : ${r.status}`);
  return r.json();
}

function ago(epochSec) {
  const m = Math.round((Date.now() / 1000 - epochSec) / 60);
  if (m < 1) return "à l'instant";
  if (m < 60) return `il y a ${m} min`;
  const h = Math.floor(m / 60);
  return `il y a ${h} h ${String(m % 60).padStart(2, "0")}`;
}

const dayFmt = new Intl.DateTimeFormat("fr-FR", { weekday: "short", day: "numeric", timeZone: "Europe/Paris" });
const hourFmt = new Intl.DateTimeFormat("fr-FR", { hour: "2-digit", minute: "2-digit", timeZone: "Europe/Paris" });

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

function arrowSvg(deg, size, color, stroke = "#0b2239") {
  // deg : direction d'où vient le vent / la houle ; la flèche pointe là où il va.
  return `<svg width="${size}" height="${size}" viewBox="-10 -10 20 20" aria-hidden="true">
    <g transform="rotate(${(deg + 180) % 360})">
      <path d="M0,-8 L5,4 L0,1.5 L-5,4 Z" fill="${color}" stroke="${stroke}" stroke-width="1.2" stroke-linejoin="round"/>
    </g></svg>`;
}

// --- carte ------------------------------------------------------------------

// Zone : de Concarneau à Étel, Groix et la rade de Lorient
const ZONE = L.latLngBounds([47.58, -3.98], [47.92, -3.16]);
const map = L.map("map", { zoomControl: true, attributionControl: true, minZoom: 9 }).fitBounds(ZONE);

L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
  maxZoom: 18,
  attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
}).addTo(map);

L.tileLayer("https://tiles.openseamap.org/seamark/{z}/{x}/{y}.png", {
  maxZoom: 18, opacity: 0.9,
  attribution: '<a href="https://www.openseamap.org">OpenSeaMap</a>',
}).addTo(map);

map.attributionControl.addAttribution(
  'Vent réel <a href="https://www.windmorbihan.com">windmorbihan</a> · Prévisions © <a href="https://meteofrance.com">Météo-France</a> (AROME, MFWAM), <a href="https://www.etalab.gouv.fr/licence-ouverte-open-licence/">Licence Ouverte</a>'
);

function rasterOverlay(grid, values, stops, opacity) {
  const canvas = document.createElement("canvas");
  canvas.width = grid.nx;
  canvas.height = grid.ny;
  const ctx = canvas.getContext("2d");
  const img = ctx.createImageData(grid.nx, grid.ny);
  for (let p = 0; p < values.length; p++) {
    const c = colorAt(stops, values[p]);
    if (!c) continue;
    img.data.set([c[0], c[1], c[2], 255], p * 4);
  }
  ctx.putImageData(img, 0, 0);
  return L.imageOverlay(canvas.toDataURL(), gridBounds(grid), { opacity, interactive: false, className: "raster" });
}

// --- vent réel --------------------------------------------------------------

const liveLayer = L.layerGroup();

function sparkline(history) {
  const pts = history.filter((h) => h[0] >= Date.now() / 1000 - 6 * 3600);
  if (pts.length < 2) return "";
  const w = 220, h = 56, t0 = pts[0][0], t1 = pts[pts.length - 1][0];
  const vmax = Math.max(10, ...pts.map((p) => p[2] ?? p[1] ?? 0));
  const x = (t) => ((t - t0) / Math.max(1, t1 - t0)) * w;
  const y = (v) => h - 4 - ((v ?? 0) / vmax) * (h - 8);
  const line = (k) => pts.filter((p) => p[k] != null).map((p) => `${x(p[0]).toFixed(1)},${y(p[k]).toFixed(1)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" role="img" aria-label="Vent sur 6 heures">
    <polyline points="${line(2)}" fill="none" stroke="#f46d43" stroke-width="1.2" stroke-dasharray="3 2"/>
    <polyline points="${line(1)}" fill="none" stroke="#2f82d8" stroke-width="2"/>
    <text x="2" y="10" font-size="10" fill="#5b6b7b">${Math.round(vmax)} kt</text>
    <text x="${w - 2}" y="${h - 2}" font-size="10" fill="#5b6b7b" text-anchor="end">6 h</text>
  </svg><div class="muted">bleu : vent moyen · orange : rafales</div>`;
}

function renderLive() {
  liveLayer.clearLayers();
  if (!state.live) return;
  for (const s of state.live.sensors) {
    const last = s.last;
    const stale = !last || Date.now() / 1000 - last.t > 30 * 60;
    const kt = last?.avg;
    const color = cssColor(WIND_STOPS, kt);
    const html = `<div class="pin${stale ? " stale" : ""}" style="border:2px solid ${color}">
      ${last?.dir != null ? arrowSvg(last.dir, 22, color) : ""}<span>${fmt(kt)}${last?.gust != null ? `<small>/${fmt(last.gust)}</small>` : ""}</span></div>`;
    const marker = L.marker([s.lat, s.lon], {
      icon: L.divIcon({ className: "live-icon", html, iconSize: [0, 0] }),
      keyboard: true, title: s.name,
    });
    marker.bindPopup(() => `<h3>${s.name}</h3>
      ${last ? `<table>
        <tr><td>Vent moyen</td><td><b>${fmt(last.avg)} kt</b></td></tr>
        <tr><td>Rafales</td><td><b>${fmt(last.gust)} kt</b></td></tr>
        <tr><td>Direction</td><td>${compass(last.dir)} (${fmt(last.dir)}°)</td></tr>
        ${last.temp != null ? `<tr><td>Température</td><td>${fmt(last.temp, 1)} °C</td></tr>` : ""}
      </table><div class="muted">Relevé ${ago(last.t)}</div>${sparkline(s.history || [])}`
      : '<div class="muted">Pas de relevé récent</div>'}`, { maxWidth: 260 });
    marker.on("click", (e) => L.DomEvent.stopPropagation(e));
    liveLayer.addLayer(marker);
  }
}

async function loadLive() {
  try {
    state.live = await getJSON("data/live.json");
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
  if (!a?.times?.length) return;
  const k = state.index;

  windRaster.addLayer(rasterOverlay(a.grid, windSpeedsKt(a, k), WIND_STOPS, 0.45));
  const data = velocityData(a, k);
  if (!velocity) {
    velocity = L.velocityLayer({
      displayValues: false, data, maxVelocity: 18, velocityScale: 0.006,
      particleMultiplier: 1 / 150, lineWidth: 1.6, frameRate: 20,
      colorScale: ["rgba(255,255,255,0.9)"],
    });
    if ($("l-wind").checked) velocity.addTo(map);
  } else {
    velocity.setData(data);
  }

  const w = state.fc.waves, wi = waveIndexFor(a.times[k]);
  if (wi >= 0) {
    waveLayer.addLayer(rasterOverlay(w.grid, w.hs[wi], WAVE_STOPS, 0.65));
    const g = w.grid, dirs = w.dir?.[wi];
    if (dirs) {
      for (let j = 0; j < g.ny; j += 2) {
        for (let i = 0; i < g.nx; i += 2) {
          const d = dirs[j * g.nx + i];
          if (d == null) continue;
          waveLayer.addLayer(L.marker([g.la1 - j * g.dy, g.lo1 + i * g.dx], {
            icon: L.divIcon({ className: "wave-arrow", html: arrowSvg(d, 16, "#fff", "#0b2239"), iconSize: [16, 16], iconAnchor: [8, 8] }),
            interactive: false, keyboard: false,
          }));
        }
      }
    }
  }
  renderTime();
}

function renderTime() {
  const a = state.fc?.arome;
  if (!a?.times?.length) { $("when-label").textContent = "—"; return; }
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
  try {
    state.fc = await getJSON("data/forecast.json");
  } catch (e) {
    console.warn(e);
    state.fc = null;
  }
  const n = state.fc?.arome?.times?.length || 0;
  $("slider").max = Math.max(0, n - 1);
  $("slider").disabled = n === 0;
  setIndex(nearestNowIndex());
  renderStatus();
}

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

function legendScale(stops, unit) {
  return `<div class="scale">${stops.map(([v, c]) => `<span style="background:${c}">${v}</span>`).join("")}</div>
    <div class="title">${unit}</div>`;
}

function renderLegend() {
  const parts = [];
  if ($("l-live").checked || $("l-wind").checked) parts.push(`<div class="title">Vent (nœuds)</div>${legendScale(WIND_STOPS, "")}`);
  if ($("l-waves").checked) parts.push(`<div class="title">Hauteur des vagues (m)</div>${legendScale(WAVE_STOPS, "")}`);
  $("legend").innerHTML = parts.join("");
  $("legend").hidden = parts.length === 0;
}

function renderStatus() {
  const bits = [];
  if (state.live) bits.push(`Vent réel ${ago(state.live.updated)}${state.live.stale ? " (source indisponible)" : ""}`);
  const run = (m) => m?.run ? `${m.model.split(" ")[0]} ${hourFmt.format(new Date(m.run))}` : null;
  const runs = [run(state.fc?.arome), run(state.fc?.waves)].filter(Boolean);
  if (runs.length) bits.push(`Run ${runs.join(" · ")}`);
  if (!state.fc?.arome) bits.push("Prévisions indisponibles");
  $("status").textContent = bits.join(" — ");
}

function toggle(id, layer, onMap) {
  const apply = () => {
    if ($(id).checked) onMap ? onMap(true) : layer.addTo(map);
    else onMap ? onMap(false) : map.removeLayer(layer);
    renderLegend();
  };
  $(id).addEventListener("change", apply);
  apply();
}

toggle("l-live", liveLayer);
toggle("l-wind", null, (on) => {
  if (on) { windRaster.addTo(map); if (velocity) velocity.addTo(map); }
  else { map.removeLayer(windRaster); if (velocity) map.removeLayer(velocity); }
});
toggle("l-waves", waveLayer);

$("slider").addEventListener("input", (e) => setIndex(Number(e.target.value)));
$("now").addEventListener("click", () => setIndex(nearestNowIndex()));
$("play").addEventListener("click", () => {
  if (state.playing) {
    clearInterval(state.playing);
    state.playing = null;
    $("play").textContent = "▶";
    return;
  }
  $("play").textContent = "❚❚";
  state.playing = setInterval(() => {
    const n = Number($("slider").max) + 1;
    setIndex((state.index + 1) % Math.max(1, n));
  }, 900);
});

loadLive();
loadForecast();
setInterval(loadLive, LIVE_REFRESH_MS);
setInterval(renderStatus, 60 * 1000);
