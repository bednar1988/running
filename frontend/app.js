const $ = (sel) => document.querySelector(sel);

Chart.defaults.font.family = "ui-monospace, 'Cascadia Code', 'SF Mono', Consolas, monospace";

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function apiPost(path, body) {
  const res = await fetch(path, {
    method: "POST",
    headers: body ? { "Content-Type": "application/json" } : undefined,
    body: body ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function apiPatch(path, body) {
  const res = await fetch(path, {
    method: "PATCH",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

async function apiDelete(path) {
  const res = await fetch(path, { method: "DELETE" });
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function paceToSeconds(paceStr) {
  if (!paceStr) return null;
  const [m, s] = paceStr.split(":").map(Number);
  return m * 60 + s;
}

function secondsToPace(sec) {
  if (sec === null || sec === undefined || Number.isNaN(sec)) return "—";
  const m = Math.floor(sec / 60);
  const s = Math.round(sec % 60);
  return `${m}:${s.toString().padStart(2, "0")}`;
}

// Local-date (not UTC) YYYY-MM-DD — toISOString() shifts to UTC and can land on the wrong
// day/month near local midnight, which broke the week/month/year boundary queries below.
function isoLocal(d) {
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function escapeHtml(s) {
  const div = document.createElement("div");
  div.textContent = s ?? "";
  return div.innerHTML;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric" });
}

// --- Tabs -------------------------------------------------------------

const loadedTabs = new Set(["overview"]);

function switchTab(name) {
  document.querySelectorAll(".tab-btn").forEach((b) => b.classList.toggle("active", b.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach((p) => p.classList.toggle("hidden", p.dataset.tab !== name));
  if (!loadedTabs.has(name)) {
    loadedTabs.add(name);
    if (name === "activities") loadActivities();
    if (name === "records") loadRecords();
    if (name === "plan") loadPlanTab();
  }
}

document.querySelectorAll(".tab-btn").forEach((b) => b.addEventListener("click", () => switchTab(b.dataset.tab)));

// --- Overview tab -------------------------------------------------------

let weeklyVolumeChart, pacehrChart, efChart, zonesChart, wellnessChart, aggChart;
let overviewWeeks = 12;

const ZONE_COLORS = ["#3f8f96", "#5fa050", "#d1a52e", "#d1701f", "#9c2f6b"];
// Fixed pace/HR colors everywhere the two are plotted together — blue vs red reads at a glance,
// unlike two similarly-muted warm tones from the rest of the palette.
const PACE_COLOR = "#4a7ba8";
const HR_COLOR = "#a83c3c";

async function loadSummaryCards() {
  const today = new Date();
  const startOfWeek = new Date(today);
  startOfWeek.setDate(today.getDate() - today.getDay() + 1);
  const startOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  const startOfYear = new Date(today.getFullYear(), 0, 1);

  const [week, month, year, weeklyVolume] = await Promise.all([
    api(`/api/aggregate?period=week&start=${isoLocal(startOfWeek)}`),
    api(`/api/aggregate?period=month&start=${isoLocal(startOfMonth)}`),
    api(`/api/aggregate?period=year&start=${isoLocal(startOfYear)}`),
    api(`/api/weekly-volume?weeks=12`),
  ]);

  const lastWeek = weeklyVolume.at(-1);
  const pctChange = lastWeek?.pct_change_vs_prior_week;
  const warn = pctChange !== null && pctChange !== undefined && pctChange > 10;

  // .at(-1): the most recent (current) bucket — buckets are sorted ascending by period key.
  const cards = [
    { label: "Ten tydzień", value: `${week.at(-1)?.distance_km ?? 0} km` },
    { label: "Ten miesiąc", value: `${month.at(-1)?.distance_km ?? 0} km` },
    { label: "Ten rok", value: `${year.at(-1)?.distance_km ?? 0} km` },
    {
      label: "Ten tydzień vs poprzedni",
      value: pctChange !== null && pctChange !== undefined ? `${pctChange > 0 ? "+" : ""}${pctChange}%` : "—",
      warn,
    },
  ];

  $("#summary-cards").innerHTML = cards
    .map(
      (c) => `<div class="card"><div class="label">${c.label}</div><div class="value ${c.warn ? "warn" : ""}">${c.value}</div></div>`
    )
    .join("");
}

async function loadWeeklyVolumeChart() {
  const data = await api(`/api/weekly-volume?weeks=${overviewWeeks}`);
  const ctx = $("#weekly-volume-chart");
  if (weeklyVolumeChart) weeklyVolumeChart.destroy();
  weeklyVolumeChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: data.map((d) => d.week),
      datasets: [
        {
          label: "Dystans (km)",
          data: data.map((d) => d.distance_km),
          backgroundColor: "#a8461c",
        },
      ],
    },
    options: {
      scales: {
        y: { beginAtZero: true, title: { display: true, text: "km" } },
      },
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: (ctx) => {
              const pct = data[ctx.dataIndex]?.pct_change_vs_prior_week;
              return pct !== null && pct !== undefined ? `${pct > 0 ? "+" : ""}${pct}% vs poprz. tydzień` : "";
            },
          },
        },
      },
    },
  });
}

async function loadPaceHrChart() {
  const data = await api(`/api/progression/pace-hr?weeks=${overviewWeeks}`);
  const ctx = $("#pacehr-chart");
  if (pacehrChart) pacehrChart.destroy();
  pacehrChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map((d) => d.week),
      datasets: [
        {
          label: "Śr. tempo",
          data: data.map((d) => paceToSeconds(d.avg_pace_per_km)),
          borderColor: PACE_COLOR,
          yAxisID: "pace",
          tension: 0.25,
        },
        {
          label: "Śr. tętno",
          data: data.map((d) => d.avg_hr),
          borderColor: HR_COLOR,
          yAxisID: "hr",
          tension: 0.25,
        },
      ],
    },
    options: {
      scales: {
        // reverse: true so fewer seconds/km (faster pace) plots higher — a rising line means
        // running faster, matching how the HR axis reads (rising = higher, more effort).
        pace: {
          type: "linear",
          position: "left",
          reverse: true,
          title: { display: true, text: "Tempo (mm:ss/km, wyżej = szybciej)" },
          ticks: { callback: (value) => secondsToPace(value) },
        },
        hr: {
          type: "linear",
          position: "right",
          grid: { drawOnChartArea: false },
          title: { display: true, text: "Tętno (bpm)" },
        },
      },
      plugins: {
        tooltip: {
          callbacks: {
            label: (ctx) =>
              ctx.dataset.yAxisID === "pace"
                ? `${ctx.dataset.label}: ${secondsToPace(ctx.parsed.y)}/km`
                : `${ctx.dataset.label}: ${ctx.parsed.y}`,
          },
        },
      },
    },
  });
}

async function loadEfficiencyChart() {
  const data = await api(`/api/progression/efficiency-factor?weeks=${overviewWeeks}`);
  const ctx = $("#ef-chart");
  if (efChart) efChart.destroy();
  efChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map((d) => d.week),
      datasets: [
        {
          label: "Efficiency Factor",
          data: data.map((d) => d.ef),
          borderColor: "#b8903f",
          backgroundColor: "rgba(184,144,63,0.15)",
          fill: true,
          tension: 0.25,
        },
      ],
    },
    options: {
      plugins: { legend: { display: false } },
      scales: { y: { title: { display: true, text: "m/min na uderzenie serca (wyżej = lepiej)" } } },
    },
  });
}

async function loadZonesChart() {
  const data = await api(`/api/hr-zones/weekly?weeks=${overviewWeeks}`);
  const ctx = $("#zones-chart");
  if (zonesChart) zonesChart.destroy();
  zonesChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: data.map((d) => d.week),
      datasets: [1, 2, 3, 4, 5].map((z, i) => ({
        label: `Strefa ${z}`,
        data: data.map((d) => d[`zone_${z}_min`]),
        backgroundColor: ZONE_COLORS[i],
      })),
    },
    options: {
      scales: {
        x: { stacked: true },
        y: { stacked: true, title: { display: true, text: "minuty" } },
      },
    },
  });
}

async function loadWellnessChart() {
  let url = "/api/wellness";
  if (overviewWeeks < 520) {
    const start = new Date();
    start.setDate(start.getDate() - overviewWeeks * 7);
    url += `?start=${isoLocal(start)}`;
  }
  const data = await api(url);
  const ctx = $("#wellness-chart");
  if (wellnessChart) wellnessChart.destroy();
  wellnessChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map((d) => d.date),
      datasets: [
        {
          label: "Tętno spoczynkowe",
          data: data.map((d) => d.resting_hr),
          borderColor: "#a8461c",
          yAxisID: "rhr",
          tension: 0.25,
          pointRadius: 0,
        },
        {
          label: "HRV (ms)",
          data: data.map((d) => d.hrv_avg_ms),
          borderColor: "#b8903f",
          yAxisID: "hrv",
          tension: 0.25,
          pointRadius: 0,
        },
      ],
    },
    options: {
      scales: {
        x: { ticks: { maxTicksLimit: 10 } },
        rhr: { type: "linear", position: "left", title: { display: true, text: "RHR (bpm)" } },
        hrv: { type: "linear", position: "right", grid: { drawOnChartArea: false }, title: { display: true, text: "HRV (ms)" } },
      },
    },
  });
}

async function loadAggregate() {
  const period = $("#agg-period").value;
  const data = await api(`/api/aggregate?period=${period}`);

  const ctx = $("#agg-chart");
  if (aggChart) aggChart.destroy();
  aggChart = new Chart(ctx, {
    type: "bar",
    data: {
      labels: data.map((d) => d.period),
      datasets: [{ label: "Dystans (km)", data: data.map((d) => d.distance_km), backgroundColor: "#b8903f" }],
    },
    options: { plugins: { legend: { display: false } } },
  });

  $("#agg-table tbody").innerHTML = data
    .slice()
    .reverse()
    .map(
      (d) =>
        `<tr><td>${d.period}</td><td>${d.distance_km}</td><td>${d.duration_h}</td><td>${d.avg_pace_per_km ?? "—"}</td><td>${d.activity_count}</td><td>${d.calories}</td></tr>`
    )
    .join("");
}

// --- Activities tab -----------------------------------------------------

let activityDetailCache = {};

const DECOUPLING_TOOLTIP =
  "Aerobic decoupling: o ile spadła efektywność (tempo/tętno) między 1. a 2. połową biegu. " +
  "<5% = dobra wytrzymałość aerobowa, 5–10% = w porządku, >10% = zbyt duże zmęczenie / tempo za szybkie jak na aktualną bazę.";

function decouplingBadge(pct) {
  if (pct === null || pct === undefined)
    return `<span class="decoupling-badge unknown" title="${DECOUPLING_TOOLTIP} (za mało okrążeń z tętnem, żeby policzyć)">brak danych</span>`;
  const cls = pct < 5 ? "good" : pct <= 10 ? "ok" : "high";
  return `<span class="decoupling-badge ${cls}" title="${DECOUPLING_TOOLTIP}">${pct > 0 ? "+" : ""}${pct}%</span>`;
}

function zoneMiniBar(zones) {
  const total = zones.reduce((sum, z) => sum + (z.time_in_zone_min || 0), 0);
  if (!total) return `<span class="hint">Brak danych o strefach tętna dla tego treningu.</span>`;
  const active = zones.filter((z) => z.time_in_zone_min);
  const spans = active
    .map((z) => {
      const range = z.zone_low_bpm && z.zone_high_bpm ? ` (${z.zone_low_bpm}–${z.zone_high_bpm} bpm)` : "";
      return `<span class="zone-${z.zone_number}" style="width:${(z.time_in_zone_min / total) * 100}%" title="Strefa ${z.zone_number}${range}: ${z.time_in_zone_min} min"></span>`;
    })
    .join("");
  const legend = active
    .map(
      (z) =>
        `<span class="zone-legend-item"><span class="zone-${z.zone_number} zone-swatch"></span>Strefa ${z.zone_number}: ${z.time_in_zone_min} min (${Math.round((z.time_in_zone_min / total) * 100)}%)</span>`
    )
    .join("");
  return `<div class="zone-mini-bar">${spans}</div><div class="zone-legend">${legend}</div>`;
}

// Garmin's aerobic_te_label/anaerobic_te_label are raw internal message keys (e.g.
// "IMPACTING_TEMPO_22"), not human text — describe the 0-5 scale ourselves instead.
function teDescription(value) {
  if (value == null) return "—";
  if (value < 1) return "brak korzyści";
  if (value < 2) return "niewielka korzyść";
  if (value < 3) return "utrzymanie formy";
  if (value < 4) return "poprawa";
  if (value < 5) return "znacząca poprawa";
  return "przetrenowanie";
}

function elevationSummary(detail) {
  if (detail.elevation_gain_m == null && detail.elevation_loss_m == null) return "";
  return `<div class="hint">Suma: <strong>${Math.round(detail.elevation_gain_m ?? 0)} m</strong> podjazdu, <strong>${Math.round(detail.elevation_loss_m ?? 0)} m</strong> zjazdu</div>`;
}

function trainingEffectSummary(detail) {
  if (detail.aerobic_te == null && detail.anaerobic_te == null && detail.calories == null) return "";
  return `
    <div class="te-summary">
      <span>Aerobowy <strong>${detail.aerobic_te?.toFixed(1) ?? "—"}</strong> <span class="hint">${teDescription(detail.aerobic_te)}</span></span>
      <span>Beztlenowy <strong>${detail.anaerobic_te?.toFixed(1) ?? "—"}</strong> <span class="hint">${teDescription(detail.anaerobic_te)}</span></span>
      <span>Kalorie <strong>${detail.calories ?? "—"}</strong> <span class="hint">kcal</span></span>
    </div>
  `;
}

function weatherIcon(condition) {
  if (!condition) return "";
  const c = condition.toLowerCase();
  if (/thunder|storm/.test(c)) return "⛈";
  if (/snow|sleet/.test(c)) return "❄";
  if (/rain|shower|drizzle/.test(c)) return "🌧";
  if (/fog|mist|haze/.test(c)) return "🌫";
  if (/wind/.test(c)) return "💨";
  if (/partly|mostly sunny/.test(c)) return "⛅";
  if (/cloud|overcast/.test(c)) return "☁";
  if (/clear|sun/.test(c)) return "☀";
  return "";
}

function weatherSummary(detail) {
  if (detail.weather_temp_c == null && !detail.weather_condition) return "";
  const parts = [];
  if (detail.weather_temp_c != null) parts.push(`${Math.round(detail.weather_temp_c)}°C`);
  if (detail.weather_humidity_pct != null) parts.push(`${detail.weather_humidity_pct}% wilgotności`);
  const icon = weatherIcon(detail.weather_condition);
  return `<div class="hint weather-line">${parts.join(" &middot; ")}${icon ? ` <span class="weather-icon">${icon}</span>` : ""}</div>`;
}

let lapCharts = {};

function renderLapCharts(id, laps) {
  if (lapCharts[id]) {
    lapCharts[id].pace?.destroy();
    lapCharts[id].elevation?.destroy();
  }
  lapCharts[id] = {};

  const paceLaps = laps.filter((l) => l.avg_pace_per_km && l.avg_hr);
  const paceCtx = $(`#lap-pacehr-${id}`);
  if (paceCtx && paceLaps.length >= 2) {
    lapCharts[id].pace = new Chart(paceCtx, {
      type: "line",
      data: {
        labels: paceLaps.map((l) => l.lap_index),
        datasets: [
          {
            label: "Tempo",
            data: paceLaps.map((l) => paceToSeconds(l.avg_pace_per_km)),
            borderColor: PACE_COLOR,
            yAxisID: "pace",
            tension: 0.2,
          },
          {
            label: "Tętno",
            data: paceLaps.map((l) => l.avg_hr),
            borderColor: HR_COLOR,
            yAxisID: "hr",
            tension: 0.2,
          },
        ],
      },
      options: {
        maintainAspectRatio: false,
        scales: {
          pace: { type: "linear", position: "left", reverse: true, ticks: { callback: (v) => secondsToPace(v) } },
          hr: { type: "linear", position: "right", grid: { drawOnChartArea: false } },
        },
        plugins: {
          tooltip: {
            callbacks: {
              label: (ctx) =>
                ctx.dataset.yAxisID === "pace"
                  ? `${ctx.dataset.label}: ${secondsToPace(ctx.parsed.y)}/km`
                  : `${ctx.dataset.label}: ${ctx.parsed.y}`,
            },
          },
        },
      },
    });
  }

  const elevLaps = laps.filter((l) => l.elevation_gain_m != null || l.elevation_loss_m != null);
  const elevCtx = $(`#lap-elevation-${id}`);
  if (elevCtx && elevLaps.length >= 2) {
    lapCharts[id].elevation = new Chart(elevCtx, {
      type: "bar",
      data: {
        labels: elevLaps.map((l) => l.lap_index),
        datasets: [
          { label: "Podjazd", data: elevLaps.map((l) => l.elevation_gain_m ?? 0), backgroundColor: "#d1701f" },
          { label: "Zjazd", data: elevLaps.map((l) => -(l.elevation_loss_m ?? 0)), backgroundColor: "#5fa050" },
        ],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${Math.abs(ctx.parsed.y)} m` } },
        },
      },
    });
  }
}

function lapsTable(laps) {
  if (!laps.length) return `<p class="hint">Brak danych o okrążeniach.</p>`;
  const rows = laps
    .map(
      (l) =>
        `<tr><td>${l.lap_index}</td><td>${l.distance_km ?? "—"} km</td><td>${l.duration_min ?? "—"} min</td><td>${l.avg_pace_per_km ?? "—"}</td><td>${l.avg_hr ?? "—"}</td><td>${l.avg_cadence_spm ?? "—"}</td></tr>`
    )
    .join("");
  return `<table class="laps-table"><thead><tr><th>#</th><th>Dystans</th><th>Czas</th><th>Tempo</th><th>Tętno</th><th>Kadencja</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function loadTrackMap(id) {
  const mapEl = $(`#map-${id}`);
  if (!mapEl) return;
  try {
    const data = await api(`/api/activities/${id}/track`);
    const map = L.map(mapEl, { attributionControl: false, zoomControl: false });
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 19 }).addTo(map);
    const line = L.polyline(data.points, { color: "#a8461c", weight: 3 }).addTo(map);
    map.fitBounds(line.getBounds(), { padding: [10, 10] });
    setTimeout(() => map.invalidateSize(), 50);
  } catch (e) {
    mapEl.classList.add("empty");
    mapEl.textContent = "Brak danych GPS dla tego treningu";
  }
}

function markRowIgnored(id, isIgnored) {
  const row = document.querySelector(`tr.activity-row[data-id="${id}"]`);
  if (!row) return;
  row.classList.toggle("ignored-row", isIgnored);
  const label = row.querySelector(".ignored-label");
  if (isIgnored && !label) {
    row.children[1].insertAdjacentHTML("beforeend", ` <span class="ignored-label">ignorowany</span>`);
  } else if (!isIgnored && label) {
    label.remove();
  }
}

function markRowHasNote(id, hasNote) {
  const row = document.querySelector(`tr.activity-row[data-id="${id}"]`);
  if (!row) return;
  const cell = row.querySelector(".note-indicator");
  if (!cell) return;
  cell.textContent = hasNote ? "📝" : "";
  cell.title = hasNote ? "Ma notatkę" : "";
}

function section(title, innerHtml) {
  if (!innerHtml) return "";
  return `<div class="detail-section"><div class="detail-section-title">${title}</div>${innerHtml}</div>`;
}

let noteEditing = {};

function noteSectionInner(note, editing) {
  if (editing) {
    return `
      <textarea class="note-textarea" placeholder="Notatka do tego treningu...">${escapeHtml(note)}</textarea>
      <div class="note-actions">
        <button class="note-save-btn">Zapisz</button>
        <button class="note-cancel-btn btn-subtle">Anuluj</button>
      </div>
    `;
  }
  if (note) {
    return `
      <p class="note-text">${escapeHtml(note)}</p>
      <div class="note-actions">
        <button class="note-edit-btn btn-subtle">Edytuj</button>
        <button class="note-delete-btn btn-subtle">Usuń</button>
      </div>
    `;
  }
  return `<div class="note-actions"><button class="note-add-btn">Dodaj notatkę</button></div>`;
}

function renderNoteSection(id, container) {
  const body = container.querySelector(`#note-body-${id}`);
  if (!body) return;
  const detail = activityDetailCache[id];
  body.innerHTML = noteSectionInner(detail.note, !!noteEditing[id]);

  const addBtn = body.querySelector(".note-add-btn");
  const editBtn = body.querySelector(".note-edit-btn");
  const cancelBtn = body.querySelector(".note-cancel-btn");
  const deleteBtn = body.querySelector(".note-delete-btn");
  const saveBtn = body.querySelector(".note-save-btn");

  const startEditing = () => {
    noteEditing[id] = true;
    renderNoteSection(id, container);
  };
  if (addBtn) addBtn.addEventListener("click", startEditing);
  if (editBtn) editBtn.addEventListener("click", startEditing);
  if (cancelBtn) cancelBtn.addEventListener("click", () => {
    noteEditing[id] = false;
    renderNoteSection(id, container);
  });
  if (deleteBtn) deleteBtn.addEventListener("click", async () => {
    const result = await apiPost(`/api/activities/${id}/note`, { note: "" });
    activityDetailCache[id].note = result.note;
    noteEditing[id] = false;
    renderNoteSection(id, container);
    markRowHasNote(id, !!result.note);
  });
  if (saveBtn) saveBtn.addEventListener("click", async () => {
    const textarea = body.querySelector(".note-textarea");
    const result = await apiPost(`/api/activities/${id}/note`, { note: textarea.value });
    activityDetailCache[id].note = result.note;
    noteEditing[id] = false;
    renderNoteSection(id, container);
    markRowHasNote(id, !!result.note);
  });
}

async function loadActivityDetail(id, container) {
  if (!activityDetailCache[id]) {
    activityDetailCache[id] = await api(`/api/activities/${id}`);
  }
  const detail = activityDetailCache[id];

  container.innerHTML = `
    <div class="detail-content">
      ${section("Efekt treningowy", trainingEffectSummary(detail))}
      ${section("Warunki", weatherSummary(detail))}
      ${section("Tętno", `<div>Aerobic decoupling: ${decouplingBadge(detail.decoupling_pct)}</div>${zoneMiniBar(detail.hr_zones)}`)}
      ${section("Tempo i tętno per okrążenie", `<div class="lap-chart-box"><canvas id="lap-pacehr-${id}"></canvas></div>`)}
      ${section("Przewyższenie per okrążenie", `<div class="lap-chart-box small"><canvas id="lap-elevation-${id}"></canvas></div>${elevationSummary(detail)}`)}
      ${section("Okrążenia", lapsTable(detail.laps))}
      <div class="detail-section"><div class="detail-section-title">Notatka</div><div id="note-body-${id}"></div></div>
      ${section("Trasa", `<div class="track-map" id="map-${id}"></div>`)}
      <div class="detail-actions">
        <button class="ignore-toggle-btn" title="Wyklucza trening z metryk liczonych na tętnie (tempo↔tętno, EF, strefy, eksport) — dystans i czas nadal się liczą do agregatów.">${detail.is_ignored ? "Przywróć" : "Ignoruj"}</button>
      </div>
    </div>
  `;

  noteEditing[id] = false;
  renderNoteSection(id, container);

  container.querySelector(".ignore-toggle-btn").addEventListener("click", async () => {
    const result = await apiPost(`/api/activities/${id}/toggle-ignore`);
    activityDetailCache[id].is_ignored = result.is_ignored;
    markRowIgnored(id, result.is_ignored);
    await loadActivityDetail(id, container);
  });

  renderLapCharts(id, detail.laps);

  loadTrackMap(id);
}

async function loadActivities() {
  const limitValue = $("#activities-limit").value;
  const limit = limitValue === "all" ? 0 : limitValue;
  const data = await api(`/api/activities?limit=${limit}`);

  const tbody = $("#activities-table tbody");
  tbody.innerHTML = "";

  data.activities.forEach((a) => {
    const row = document.createElement("tr");
    row.className = "activity-row" + (a.is_ignored ? " ignored-row" : "");
    row.dataset.id = a.id;
    row.innerHTML = `
      <td class="expand-toggle">▶</td>
      <td>${fmtDate(a.date)}${a.is_ignored ? ' <span class="ignored-label">ignorowany</span>' : ""}</td>
      <td>${a.distance_km} km</td>
      <td>${a.duration_min} min</td>
      <td>${a.avg_pace_per_km ?? "—"}</td>
      <td>${a.avg_hr ?? "—"}</td>
      <td>${a.cadence_spm ?? "—"}</td>
      <td>${a.aerobic_te != null ? a.aerobic_te.toFixed(1) : "—"}</td>
      <td>${a.anaerobic_te != null ? a.anaerobic_te.toFixed(1) : "—"}</td>
      <td>${a.calories ?? "—"}</td>
      <td class="note-indicator" title="${a.has_note ? "Ma notatkę" : ""}">${a.has_note ? "📝" : ""}</td>
    `;

    const detailRow = document.createElement("tr");
    detailRow.className = "detail-row hidden";
    const detailCell = document.createElement("td");
    detailCell.colSpan = 11;
    detailCell.innerHTML = `<div class="detail-loading">Ładowanie…</div>`;
    detailRow.appendChild(detailCell);

    row.addEventListener("click", async () => {
      const opening = detailRow.classList.contains("hidden");
      row.classList.toggle("expanded", opening);
      detailRow.classList.toggle("hidden", !opening);
      if (opening) await loadActivityDetail(a.id, detailCell);
    });

    tbody.appendChild(row);
    tbody.appendChild(detailRow);
  });
}

$("#activities-limit").addEventListener("change", loadActivities);

// --- Records tab -----------------------------------------------------------

function recordProgressionTable(progression) {
  if (!progression || progression.length === 0) {
    return `<p class="hint">Brak treningu wystarczająco długiego, żeby policzyć wynik na tym dystansie.</p>`;
  }
  if (progression.length === 1) {
    return `<p class="hint">Tylko jeden zanotowany wynik — brak wcześniejszej historii do pokazania.</p>`;
  }
  const rows = progression
    .slice()
    .reverse()
    .map((e) => `<tr><td>${fmtDate(e.date)}</td><td>${e.time}</td><td>${e.pace_per_km}/km</td></tr>`)
    .join("");
  return `<table class="progression-table"><thead><tr><th>Data</th><th>Czas</th><th>Tempo</th></tr></thead><tbody>${rows}</tbody></table>`;
}

async function loadRecords() {
  const tbody = $("#records-table tbody");
  try {
    const records = await api("/api/records");
    tbody.innerHTML = "";

    records.forEach((r) => {
      const row = document.createElement("tr");
      row.className = "activity-row";
      row.innerHTML = `
        <td class="expand-toggle">▶</td>
        <td>${r.label}</td>
        <td>${r.time ?? "—"}</td>
        <td>${r.pace_per_km ? r.pace_per_km + "/km" : "—"}</td>
        <td>${r.date ? fmtDate(r.date) : "—"}</td>
      `;

      const detailRow = document.createElement("tr");
      detailRow.className = "detail-row hidden";
      const detailCell = document.createElement("td");
      detailCell.colSpan = 5;
      detailCell.innerHTML = recordProgressionTable(r.progression);
      detailRow.appendChild(detailCell);

      row.addEventListener("click", () => {
        const opening = detailRow.classList.contains("hidden");
        row.classList.toggle("expanded", opening);
        detailRow.classList.toggle("hidden", !opening);
      });

      tbody.appendChild(row);
      tbody.appendChild(detailRow);
    });
  } catch (e) {
    tbody.innerHTML = `<tr><td colspan="5" class="hint">Błąd liczenia rekordów — sprawdź logi kontenera</td></tr>`;
    console.error(e);
  }
}

// --- Plan tab ----------------------------------------------------------

const BLOCK_TYPE_LABELS = {
  interval: "Interwały",
  tempo: "Tempo",
  low: "Spokojny",
  medium: "Średni",
  long: "Długi",
  rest: "Odpoczynek",
  race: "Zawody",
};

let planTemplates = [];
let editingTemplateId = null;
let planWeeksShown = 16;
const PLAN_WEEKS_BACK = 2;
const PLAN_WEEKS_INCREMENT = 8;

function planStartMonday() {
  const today = new Date();
  const monday = new Date(today);
  monday.setDate(today.getDate() - ((today.getDay() + 6) % 7) - PLAN_WEEKS_BACK * 7);
  monday.setHours(0, 0, 0, 0);
  return monday;
}

async function loadPlanTab() {
  await loadPlanTemplates();
  await renderPlanGrid();
}

async function loadPlanTemplates() {
  planTemplates = await api("/api/plan/templates");
  renderTemplatesTable();
}

function renderTemplatesTable() {
  const tbody = $("#plan-templates-table tbody");
  if (!planTemplates.length) {
    tbody.innerHTML = `<tr><td colspan="6" class="hint">Brak zdefiniowanych bloków — dodaj pierwszy poniżej.</td></tr>`;
    return;
  }
  tbody.innerHTML = planTemplates
    .map(
      (t) => `
      <tr>
        <td>${escapeHtml(t.name)}</td>
        <td>${BLOCK_TYPE_LABELS[t.block_type] ?? t.block_type}</td>
        <td>${t.zone ?? "—"}</td>
        <td>${t.volume_text ? escapeHtml(t.volume_text) : "—"}</td>
        <td>${t.note ? escapeHtml(t.note) : "—"}</td>
        <td>
          <button type="button" class="pt-edit-btn" data-id="${t.id}">Edytuj</button>
          <button type="button" class="pt-delete-btn" data-id="${t.id}">Usuń</button>
        </td>
      </tr>
    `
    )
    .join("");

  tbody.querySelectorAll(".pt-edit-btn").forEach((btn) => {
    btn.addEventListener("click", () => startEditingTemplate(Number(btn.dataset.id)));
  });
  tbody.querySelectorAll(".pt-delete-btn").forEach((btn) => {
    btn.addEventListener("click", async () => {
      try {
        await apiDelete(`/api/plan/templates/${btn.dataset.id}`);
        await loadPlanTemplates();
        await renderPlanGrid();
      } catch (e) {
        alert("Nie można usunąć — ten blok jest gdzieś przypisany w planie. Usuń najpierw te przypisania.");
      }
    });
  });
}

function startEditingTemplate(id) {
  const t = planTemplates.find((x) => x.id === id);
  if (!t) return;
  editingTemplateId = id;
  $("#pt-name").value = t.name;
  $("#pt-type").value = t.block_type;
  $("#pt-zone").value = t.zone ?? "";
  $("#pt-volume").value = t.volume_text ?? "";
  $("#pt-note").value = t.note ?? "";
  $("#pt-submit-btn").textContent = "Zapisz zmiany";
}

function resetTemplateForm() {
  editingTemplateId = null;
  $("#plan-template-form").reset();
  $("#pt-submit-btn").textContent = "Dodaj blok";
}

$("#plan-template-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const name = $("#pt-name").value.trim();
  if (!name) return;
  const zoneValue = $("#pt-zone").value;
  const payload = {
    name,
    block_type: $("#pt-type").value,
    zone: zoneValue ? Number(zoneValue) : null,
    volume_text: $("#pt-volume").value.trim() || null,
    note: $("#pt-note").value.trim() || null,
  };
  if (editingTemplateId) {
    await apiPatch(`/api/plan/templates/${editingTemplateId}`, payload);
  } else {
    await apiPost("/api/plan/templates", payload);
  }
  resetTemplateForm();
  await loadPlanTemplates();
  await renderPlanGrid();
});

$("#plan-more-btn").addEventListener("click", async () => {
  planWeeksShown += PLAN_WEEKS_INCREMENT;
  await renderPlanGrid();
});

async function renderPlanGrid() {
  const start = planStartMonday();
  const end = new Date(start);
  end.setDate(start.getDate() + planWeeksShown * 7 - 1);

  const blocks = await api(`/api/plan?start=${isoLocal(start)}&end=${isoLocal(end)}`);
  const blocksByDay = {};
  blocks.forEach((b) => {
    (blocksByDay[b.day] ??= []).push(b);
  });

  const dayLabels = ["Pon", "Wt", "Śr", "Czw", "Pt", "Sob", "Nd"];
  const todayIso = isoLocal(new Date());

  let html = `<table class="plan-table"><thead><tr><th></th>${dayLabels.map((d) => `<th>${d}</th>`).join("")}</tr></thead><tbody>`;

  for (let w = 0; w < planWeeksShown; w++) {
    const weekStart = new Date(start);
    weekStart.setDate(start.getDate() + w * 7);
    const weekEnd = new Date(weekStart);
    weekEnd.setDate(weekStart.getDate() + 6);
    const fmt = (d) => `${d.getDate()}.${String(d.getMonth() + 1).padStart(2, "0")}`;

    html += `<tr><td class="plan-week-label">${fmt(weekStart)}–${fmt(weekEnd)}</td>`;
    for (let d = 0; d < 7; d++) {
      const day = new Date(weekStart);
      day.setDate(weekStart.getDate() + d);
      const dayIso = isoLocal(day);
      const dayBlocks = blocksByDay[dayIso] || [];
      html += `<td class="plan-day-cell${dayIso === todayIso ? " plan-today" : ""}" data-day="${dayIso}">`;
      html += dayBlocks
        .map(
          (b) =>
            `<div class="plan-chip${b.zone ? ` zone-${b.zone}` : ""}" data-block-id="${b.id}" title="${escapeHtml(b.note ?? "")}">${escapeHtml(b.name)}${b.volume_text ? ` · ${escapeHtml(b.volume_text)}` : ""}</div>`
        )
        .join("");
      html += `<button type="button" class="plan-add-btn" data-day="${dayIso}">+</button></td>`;
    }
    html += "</tr>";
  }
  html += "</tbody></table>";

  $("#plan-grid").innerHTML = html;

  $("#plan-grid").querySelectorAll(".plan-add-btn").forEach((btn) => {
    btn.addEventListener("click", () => openPlanPicker(btn.dataset.day, btn));
  });
  $("#plan-grid").querySelectorAll(".plan-chip").forEach((chip) => {
    chip.addEventListener("click", async () => {
      if (confirm("Usunąć ten blok z tego dnia?")) {
        await apiDelete(`/api/plan/${chip.dataset.blockId}`);
        await renderPlanGrid();
      }
    });
  });
}

function openPlanPicker(day, anchorBtn) {
  document.querySelectorAll(".plan-picker").forEach((p) => p.remove());
  if (!planTemplates.length) {
    alert("Najpierw dodaj przynajmniej jeden blok w bibliotece powyżej.");
    return;
  }

  const picker = document.createElement("div");
  picker.className = "plan-picker";
  picker.innerHTML = planTemplates
    .map((t) => `<button type="button" data-template-id="${t.id}">${escapeHtml(t.name)}</button>`)
    .join("");
  anchorBtn.insertAdjacentElement("afterend", picker);

  picker.querySelectorAll("button").forEach((btn) => {
    btn.addEventListener("click", async (ev) => {
      ev.stopPropagation();
      await apiPost("/api/plan", { day, template_id: Number(btn.dataset.templateId) });
      picker.remove();
      await renderPlanGrid();
    });
  });

  setTimeout(() => {
    document.addEventListener("click", function closePicker(ev) {
      if (!picker.contains(ev.target) && ev.target !== anchorBtn) {
        picker.remove();
        document.removeEventListener("click", closePicker);
      }
    });
  }, 0);
}

// --- Global (sync, export) -----------------------------------------------

async function loadRangedCharts() {
  await Promise.all([loadWeeklyVolumeChart(), loadPaceHrChart(), loadEfficiencyChart(), loadZonesChart(), loadWellnessChart()]);
}

async function refreshAll() {
  await Promise.all([loadSummaryCards(), loadRangedCharts(), loadAggregate()]);
  if (loadedTabs.has("activities")) {
    activityDetailCache = {};
    await loadActivities();
  }
}

$("#sync-btn").addEventListener("click", async () => {
  const btn = $("#sync-btn");
  const status = $("#sync-status");
  btn.disabled = true;
  status.textContent = "Synchronizacja z Garmin Connect...";
  try {
    const res = await fetch("/api/sync", { method: "POST" });
    if (!res.ok) throw new Error(await res.text());
    const result = await res.json();
    status.textContent = `Gotowe: +${result.new_activities} treningów, ${result.wellness_days_synced} dni wellness, ${result.weather_backfilled} pogoda`;
    await refreshAll();
  } catch (e) {
    status.textContent = "Błąd synchronizacji — sprawdź logi kontenera";
    console.error(e);
  } finally {
    btn.disabled = false;
  }
});

$("#agg-period").addEventListener("change", loadAggregate);

$("#overview-range").addEventListener("change", (e) => {
  overviewWeeks = Number(e.target.value);
  loadRangedCharts();
});

$("#export-btn").addEventListener("click", async () => {
  const start = $("#export-start").value;
  const end = $("#export-end").value;
  const params = new URLSearchParams();
  if (start) params.set("start", start);
  if (end) params.set("end", end);
  const data = await api(`/api/export?${params}`);
  $("#export-output").value = JSON.stringify(data, null, 2);
  $("#copy-btn").disabled = false;
});

$("#copy-btn").addEventListener("click", async () => {
  await navigator.clipboard.writeText($("#export-output").value);
  const btn = $("#copy-btn");
  const original = btn.textContent;
  btn.textContent = "Skopiowano!";
  setTimeout(() => (btn.textContent = original), 1500);
});

refreshAll();
