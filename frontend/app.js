const $ = (sel) => document.querySelector(sel);

async function api(path) {
  const res = await fetch(path);
  if (!res.ok) throw new Error(`${path} -> ${res.status}`);
  return res.json();
}

function paceToSeconds(paceStr) {
  if (!paceStr) return null;
  const [m, s] = paceStr.split(":").map(Number);
  return m * 60 + s;
}

function fmtDate(iso) {
  return new Date(iso).toLocaleDateString("pl-PL", { day: "2-digit", month: "2-digit", year: "numeric" });
}

let rollingChart, pacehrChart, aggChart;

async function loadSummaryCards() {
  const today = new Date();
  const startOfWeek = new Date(today);
  startOfWeek.setDate(today.getDate() - today.getDay() + 1);
  const startOfMonth = new Date(today.getFullYear(), today.getMonth(), 1);
  const startOfYear = new Date(today.getFullYear(), 0, 1);
  const iso = (d) => d.toISOString().slice(0, 10);

  const [week, month, year, rolling] = await Promise.all([
    api(`/api/aggregate?period=week&start=${iso(startOfWeek)}`),
    api(`/api/aggregate?period=month&start=${iso(startOfMonth)}`),
    api(`/api/aggregate?period=year&start=${iso(startOfYear)}`),
    api(`/api/rolling-volume?window_days=7`),
  ]);

  const lastRolling = rolling.at(-1);
  const pctChange = lastRolling?.pct_change_vs_prior_window;
  const warn = pctChange !== null && pctChange !== undefined && pctChange > 10;

  const cards = [
    { label: "Ten tydzień", value: `${week[0]?.distance_km ?? 0} km` },
    { label: "Ten miesiąc", value: `${month[0]?.distance_km ?? 0} km` },
    { label: "Ten rok", value: `${year[0]?.distance_km ?? 0} km` },
    {
      label: "7d rolling vs poprz. 7d",
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

async function loadRollingChart() {
  const data = await api("/api/rolling-volume?window_days=7");
  const ctx = $("#rolling-chart");
  if (rollingChart) rollingChart.destroy();
  rollingChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map((d) => d.date),
      datasets: [
        {
          label: "Krocząca suma 7d (km)",
          data: data.map((d) => d.rolling_distance_km),
          borderColor: "#ff6b35",
          backgroundColor: "rgba(255,107,53,0.15)",
          fill: true,
          tension: 0.25,
          pointRadius: 0,
        },
      ],
    },
    options: {
      scales: {
        x: { ticks: { maxTicksLimit: 10 } },
        y: { beginAtZero: true, title: { display: true, text: "km" } },
      },
      plugins: { legend: { display: false } },
    },
  });
}

async function loadPaceHrChart() {
  const data = await api("/api/progression/pace-hr?weeks=26");
  const ctx = $("#pacehr-chart");
  if (pacehrChart) pacehrChart.destroy();
  pacehrChart = new Chart(ctx, {
    type: "line",
    data: {
      labels: data.map((d) => d.week),
      datasets: [
        {
          label: "Śr. tempo (s/km)",
          data: data.map((d) => paceToSeconds(d.avg_pace_per_km)),
          borderColor: "#2ec4b6",
          yAxisID: "pace",
          tension: 0.25,
        },
        {
          label: "Śr. tętno",
          data: data.map((d) => d.avg_hr),
          borderColor: "#ff6b35",
          yAxisID: "hr",
          tension: 0.25,
        },
      ],
    },
    options: {
      scales: {
        pace: {
          type: "linear",
          position: "left",
          reverse: true,
          title: { display: true, text: "s/km (niżej = szybciej)" },
        },
        hr: {
          type: "linear",
          position: "right",
          grid: { drawOnChartArea: false },
          title: { display: true, text: "bpm" },
        },
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
      datasets: [{ label: "Dystans (km)", data: data.map((d) => d.distance_km), backgroundColor: "#2ec4b6" }],
    },
    options: { plugins: { legend: { display: false } } },
  });

  $("#agg-table tbody").innerHTML = data
    .slice()
    .reverse()
    .map(
      (d) =>
        `<tr><td>${d.period}</td><td>${d.distance_km}</td><td>${d.duration_h}</td><td>${d.avg_pace_per_km ?? "—"}</td><td>${d.activity_count}</td></tr>`
    )
    .join("");
}

async function loadActivities() {
  const data = await api("/api/activities?limit=20");
  $("#activities-table tbody").innerHTML = data.activities
    .map(
      (a) =>
        `<tr><td>${fmtDate(a.date)}</td><td>${a.distance_km} km</td><td>${a.duration_min} min</td><td>${a.avg_pace_per_km ?? "—"}</td><td>${a.avg_hr ?? "—"}</td><td>${a.cadence_spm ?? "—"}</td><td>${a.aerobic_te ?? "—"}</td></tr>`
    )
    .join("");
}

async function refreshAll() {
  await Promise.all([loadSummaryCards(), loadRollingChart(), loadPaceHrChart(), loadAggregate(), loadActivities()]);
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
    status.textContent = `Gotowe: +${result.new_activities} treningów, ${result.wellness_days_synced} dni wellness`;
    await refreshAll();
  } catch (e) {
    status.textContent = "Błąd synchronizacji — sprawdź logi kontenera";
    console.error(e);
  } finally {
    btn.disabled = false;
  }
});

$("#agg-period").addEventListener("change", loadAggregate);

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
