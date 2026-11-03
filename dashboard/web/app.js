/* Detector Monitor dashboard - plain JS, no build step.
 * Data comes from Supabase (see dashboard/supabase/migrations).
 * All data-derived text is inserted with textContent (never innerHTML). */
(() => {
  "use strict";
  const cfg = window.DASHBOARD_CONFIG || {};
  const $ = (id) => document.getElementById(id);
  if (!cfg.supabaseUrl || cfg.supabaseUrl.includes("YOUR-PROJECT")) {
    document.querySelector("main").textContent =
      "Dashboard not configured: copy config.example.js to config.js and fill in the Supabase URL and anon key.";
    return;
  }
  const sb = window.supabase.createClient(cfg.supabaseUrl, cfg.supabaseAnonKey);

  const STATUS = {
    online:          { label: "Online",                 icon: "✓", color: "--good" },
    detecting:       { label: "COMPOUND DETECTED",      icon: "⚠", color: "--critical" },
    low_battery:     { label: "Low battery",            icon: "▼", color: "--warning" },
    offline:         { label: "Offline",                icon: "✕", color: "--serious" },
    offline_battery: { label: "Offline – needs charge", icon: "⚡", color: "--critical" },
  };
  const EVENT_NAMES = {
    detection_start: "Detection started", detection_end: "Detection ended",
    low_battery: "Low battery", battery_shutdown: "Shut down (battery empty)",
    boot: "Device started", shutdown: "Service stopped",
  };

  let devices = [];
  let prevStatus = {};
  let current = null;       // device id in detail view
  let rangeS = 86400;
  const charts = {};

  // ------------------------------------------------------------ helpers
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined && text !== null) e.textContent = text;
    return e;
  };
  const ago = (iso) => {
    if (!iso) return "never";
    const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return `${Math.round(s)} s ago`;
    if (s < 5400) return `${Math.round(s / 60)} min ago`;
    if (s < 172800) return `${Math.round(s / 3600)} h ago`;
    return `${Math.round(s / 86400)} days ago`;
  };
  const fmtTime = (iso) => new Date(iso).toLocaleString([], { dateStyle: "short", timeStyle: "medium" });
  const fmtNum = (v, d = 0) => (v === null || v === undefined ? "–" : Number(v).toFixed(d));
  const battText = (d) => d.battery_pct === null || d.battery_pct === undefined
    ? (d.battery_source === "none" ? "no monitor" : "–")
    : `${Math.round(d.battery_pct)}%${d.charging ? " (charging)" : ""}`;

  function chip(status) {
    const s = STATUS[status] || STATUS.offline;
    const c = el("span", "chip");
    const dot = el("span", "dot");
    dot.style.background = css(s.color);
    c.append(dot, el("span", "icon", s.icon), el("span", null, s.label));
    return c;
  }

  function show(view) {
    for (const v of ["login-view", "fleet-view", "device-view"]) $(v).hidden = v !== view;
  }

  // ------------------------------------------------------------ auth
  async function init() {
    const { data } = await sb.auth.getSession();
    sb.auth.onAuthStateChange((_e, session) => { if (!session) location.reload(); });
    if (!data.session) { show("login-view"); return; }
    $("user-email").textContent = data.session.user.email;
    $("signout-btn").hidden = false;
    if ("Notification" in window && Notification.permission === "default") $("notify-btn").hidden = false;
    await refreshFleet();
    routeFromHash();
    setInterval(refreshFleet, 30000);
    setInterval(() => { if (current) loadDevice(current, false); }, 60000);
    subscribeEvents();
  }

  $("login-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    $("login-error").hidden = true;
    const { error } = await sb.auth.signInWithPassword({
      email: $("login-email").value, password: $("login-password").value });
    if (error) { $("login-error").textContent = error.message; $("login-error").hidden = false; }
    else location.reload();
  });
  $("signout-btn").addEventListener("click", () => sb.auth.signOut());
  $("notify-btn").addEventListener("click", async () => {
    await Notification.requestPermission();
    $("notify-btn").hidden = true;
  });

  function notify(title, body) {
    if ("Notification" in window && Notification.permission === "granted") {
      try { new Notification(title, { body }); } catch (_) { /* some mobile browsers */ }
    }
  }

  // ------------------------------------------------------------ fleet
  async function refreshFleet() {
    const { data, error } = await sb.from("device_status").select("*").order("name");
    if (error) { $("fleet-updated").textContent = `Error: ${error.message}`; return; }
    devices = data;
    for (const d of devices) {
      const before = prevStatus[d.id];
      if (before && before !== d.status && ["offline", "offline_battery"].includes(d.status)) {
        notify(`${d.name} is ${STATUS[d.status].label.toLowerCase()}`, `Last seen ${ago(d.last_seen)}`);
      }
      prevStatus[d.id] = d.status;
    }
    renderFleet();
    renderBanner();
    $("fleet-updated").textContent = `Updated ${new Date().toLocaleTimeString()}`;
    if (current) renderTiles(devices.find((x) => x.id === current));
  }

  function renderFleet() {
    const root = $("fleet");
    root.replaceChildren();
    $("fleet-empty").hidden = devices.length > 0;
    for (const d of devices) {
      const card = el("button", "device-card");
      card.type = "button";
      card.addEventListener("click", () => { location.hash = `#/device/${encodeURIComponent(d.id)}`; });
      card.append(el("div", "name", d.name), el("div", "loc", [d.location, d.id].filter(Boolean).join(" · ")));
      card.append(chip(d.status));
      const dl = el("dl");
      const r = d.last_reading || {};
      const rows = [
        ["Last seen", ago(d.last_seen)],
        ["Battery", battText(d)],
        ["Score", r.score === null || r.score === undefined ? "–" : Number(r.score).toFixed(2)],
        ["Unacknowledged", `${d.unacked_detections} detection${d.unacked_detections == 1 ? "" : "s"}`],
      ];
      for (const [k, v] of rows) dl.append(el("dt", null, k), el("dd", null, v));
      card.append(dl);
      root.append(card);
    }
  }

  function renderBanner() {
    const bad = devices.filter((d) => ["detecting", "offline_battery", "offline", "low_battery"].includes(d.status));
    const b = $("alert-banner");
    if (!bad.length) { b.hidden = true; return; }
    b.hidden = false;
    b.textContent = bad.map((d) => `${STATUS[d.status].icon} ${d.name}: ${STATUS[d.status].label}`).join("   ·   ");
  }

  // ------------------------------------------------------------ realtime
  function subscribeEvents() {
    sb.channel("events-feed")
      .on("postgres_changes", { event: "INSERT", schema: "public", table: "events" }, (msg) => {
        const ev = msg.new;
        const dev = devices.find((d) => d.id === ev.device_id);
        const name = dev ? dev.name : ev.device_id;
        if (["detection_start", "low_battery", "battery_shutdown"].includes(ev.kind)) {
          notify(`${name}: ${EVENT_NAMES[ev.kind]}`, fmtTime(ev.ts));
        }
        refreshFleet();
        if (current === ev.device_id) loadEvents(current);
      })
      .subscribe();
  }

  // ------------------------------------------------------------ device detail
  window.addEventListener("hashchange", routeFromHash);
  $("back-btn").addEventListener("click", () => { location.hash = ""; });
  function routeFromHash() {
    const m = location.hash.match(/^#\/device\/(.+)$/);
    if (m) { current = decodeURIComponent(m[1]); show("device-view"); loadDevice(current, true); }
    else { current = null; show("fleet-view"); }
  }

  $("range-picker").addEventListener("click", (e) => {
    const b = e.target.closest("button[data-range]");
    if (!b) return;
    rangeS = Number(b.dataset.range);
    for (const x of $("range-picker").querySelectorAll("button")) x.classList.toggle("on", x === b);
    if (current) loadDevice(current, false);
  });

  function renderTiles(d) {
    const root = $("dev-tiles");
    root.replaceChildren();
    if (!d) return;
    $("dev-title").textContent = d.name;
    const r = d.last_reading || {};
    const tiles = [
      ["Status", null, `Last seen ${ago(d.last_seen)}`, chip(d.status)],
      ["Battery", battText(d), d.battery_source ? `source: ${d.battery_source}` : ""],
      ["Detection score", r.score === null || r.score === undefined ? "–" : Number(r.score).toFixed(2),
        d.heartbeat && d.heartbeat.alarm_threshold ? `alarm at ${d.heartbeat.alarm_threshold}` : ""],
      ["TVOC", `${fmtNum(r.ens_tvoc)} ppb`, `VOC index ${fmtNum(r.sgp_voc_index)}`],
      ["Temp / humidity", `${fmtNum(r.temp_c, 1)}°C`, `${fmtNum(r.rh)}% RH`],
      ["Wi-Fi signal", d.wifi_rssi === null ? "–" : `${d.wifi_rssi} dBm`, `CPU ${fmtNum(d.cpu_temp_c, 0)}°C`],
      ["Model", d.model_version || "none", d.upload_error ? `upload error: ${d.upload_error}` : "", null, "small"],
    ];
    for (const [label, value, sub, node, size] of tiles) {
      const t = el("div", node ? "tile wide" : "tile");
      t.append(el("div", "label", label));
      if (node) t.append(node); else t.append(el("div", size ? `value ${size}` : "value", value));
      if (sub) t.append(el("div", "sub", sub));
      root.append(t);
    }
  }

  function bucketFor(range) {
    return range <= 3600 ? 10 : range <= 86400 ? 120 : range <= 604800 ? 900 : 3600;
  }

  async function loadDevice(id, firstLoad) {
    const d = devices.find((x) => x.id === id);
    renderTiles(d);
    const to = new Date(Date.now() + 60000), from = new Date(Date.now() - rangeS * 1000);
    const bucket = bucketFor(rangeS);
    const args = { p_device_id: id, p_from: from.toISOString(), p_to: to.toISOString(), p_bucket_s: bucket };
    const [rd, bt] = await Promise.all([sb.rpc("readings_bucketed", args), sb.rpc("battery_bucketed", args)]);
    if (rd.error) console.error(rd.error);
    const rows = rd.data || [], brows = bt.data || [];
    const xmin = from.getTime(), xmax = Date.now();
    const thr = d && d.heartbeat ? d.heartbeat.alarm_threshold : null;
    drawLine("c-score", series(rows, "max_score", bucket), xmin, xmax, { min: 0, max: 1, threshold: thr, digits: 2 });
    drawLine("c-tvoc", series(rows, "tvoc", bucket), xmin, xmax, { min: 0, digits: 0, unit: " ppb" });
    drawLine("c-voc", series(rows, "voc_index", bucket), xmin, xmax, { min: 0, digits: 0 });
    drawLine("c-eco2", series(rows, "eco2", bucket), xmin, xmax, { digits: 0, unit: " ppm" });
    drawLine("c-temp", series(rows, "temp", bucket), xmin, xmax, { digits: 1, unit: "°C" });
    drawLine("c-rh", series(rows, "humidity", bucket), xmin, xmax, { min: 0, max: 100, digits: 0, unit: "%" });
    drawLine("c-batt", series(brows, "battery", Math.max(bucket, 60)), xmin, xmax, { min: 0, max: 100, digits: 0, unit: "%" });
    loadEvents(id);
  }

  // Break the line where data is missing (device offline) instead of bridging the gap.
  function series(rows, key, bucket) {
    const out = [];
    let last = null;
    for (const r of rows) {
      const x = new Date(r.bucket).getTime();
      if (last !== null && x - last > bucket * 3000) out.push({ x: last + bucket * 1000, y: null });
      out.push({ x, y: r[key] });
      last = x;
    }
    return out;
  }

  const crosshair = {
    id: "crosshair",
    afterDraw(chart) {
      const a = chart.tooltip && chart.tooltip.getActiveElements();
      if (!a || !a.length) return;
      const x = a[0].element.x, { top, bottom } = chart.chartArea, ctx = chart.ctx;
      ctx.save(); ctx.strokeStyle = css("--axis"); ctx.lineWidth = 1;
      ctx.beginPath(); ctx.moveTo(x, top); ctx.lineTo(x, bottom); ctx.stroke(); ctx.restore();
    },
  };

  // Ticks on round clock times (every 10 min / 4 h / day / 5 days).
  function timeTicks(xmin, xmax) {
    const span = xmax - xmin, H = 3600e3;
    const step = span <= H ? H / 6 : span <= 24 * H ? 4 * H : span <= 7 * 24 * H ? 24 * H : 5 * 24 * H;
    const tzOff = new Date().getTimezoneOffset() * 60e3;  // align to local midnight/hours
    const ticks = [];
    for (let v = Math.ceil((xmin - tzOff) / step) * step + tzOff; v <= xmax; v += step) ticks.push({ value: v });
    return ticks;
  }

  function timeTick(v) {
    const d = new Date(v);
    return rangeS <= 86400 ? d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })
                           : d.toLocaleDateString([], { month: "short", day: "numeric" });
  }

  function drawLine(canvasId, points, xmin, xmax, o) {
    const ink = css("--muted"), grid = css("--grid"), color = css("--series-1");
    const datasets = [{ data: points, borderColor: color, backgroundColor: color, borderWidth: 2,
      pointRadius: 0, pointHoverRadius: 4, tension: 0.2, spanGaps: false, label: "value" }];
    if (o.threshold !== null && o.threshold !== undefined) {
      datasets.push({ data: [{ x: xmin, y: o.threshold }, { x: xmax, y: o.threshold }], borderColor: ink,
        borderDash: [4, 4], borderWidth: 1, pointRadius: 0, label: "alarm threshold" });
    }
    const unit = o.unit || "";
    const config = {
      type: "line",
      data: { datasets },
      options: {
        animation: false, maintainAspectRatio: false, parsing: false, normalized: true,
        interaction: { mode: "nearest", axis: "x", intersect: false },
        plugins: {
          legend: { display: false },
          tooltip: {
            filter: (item) => item.datasetIndex === 0,
            callbacks: {
              title: (items) => items.length ? fmtTime(items[0].parsed.x) : "",
              label: (item) => ` ${fmtNum(item.parsed.y, o.digits)}${unit}`,
            },
          },
        },
        scales: {
          x: { type: "linear", min: xmin, max: xmax, grid: { color: grid },
               afterBuildTicks: (axis) => { axis.ticks = timeTicks(xmin, xmax); },
               ticks: { color: ink, autoSkip: true, maxRotation: 0, callback: timeTick } },
          y: { min: o.min, max: o.max, grid: { color: grid }, ticks: { color: ink, maxTicksLimit: 5 } },
        },
      },
      plugins: [crosshair],
    };
    if (charts[canvasId]) charts[canvasId].destroy();
    charts[canvasId] = new Chart($(canvasId), config);
    const box = $(canvasId).parentElement;
    box.querySelector(".nodata")?.remove();
    if (!points.some((p) => p.y !== null && p.y !== undefined)) {
      const n = el("div", "muted nodata", "No data in this range");
      n.style.cssText = "position:absolute;inset:0;display:grid;place-items:center";
      box.append(n);
    }
  }

  async function loadEvents(id) {
    const { data, error } = await sb.from("events").select("*").eq("device_id", id)
      .order("ts", { ascending: false }).limit(100);
    const tb = $("events-table").querySelector("tbody");
    tb.replaceChildren();
    if (error) { tb.append(rowOf([`Error: ${error.message}`])); return; }
    if (!data.length) { tb.append(rowOf(["No events yet"])); return; }
    for (const ev of data) {
      const tr = el("tr", ev.kind === "detection_start" && !ev.acknowledged ? "unacked" : "");
      tr.append(el("td", "num", fmtTime(ev.ts)), el("td", null, EVENT_NAMES[ev.kind] || ev.kind),
                el("td", null, describe(ev)));
      const td = el("td");
      if (ev.kind === "detection_start" && !ev.acknowledged) {
        const b = el("button", "ghost", "Acknowledge");
        b.addEventListener("click", async () => {
          const { data: u } = await sb.auth.getUser();
          await sb.from("events").update({ acknowledged: true, acknowledged_by: u.user.id,
            acknowledged_at: new Date().toISOString() }).eq("id", ev.id);
          loadEvents(id); refreshFleet();
        });
        td.append(b);
      } else if (ev.acknowledged) td.append(el("span", "muted", "acknowledged"));
      tr.append(td);
      tb.append(tr);
    }
  }

  function describe(ev) {
    const d = ev.details || {};
    if (d.score !== undefined) return `score ${Number(d.score).toFixed(2)}`;
    if (d.battery_pct !== undefined) return `battery ${Math.round(d.battery_pct)}%`;
    if (d.model !== undefined) return `model ${d.model || "none"}`;
    return "";
  }
  function rowOf(cells) {
    const tr = el("tr");
    for (const c of cells) { const td = el("td", "muted", c); td.colSpan = 4; tr.append(td); }
    return tr;
  }

  // ------------------------------------------------------------ CSV export (raw rows)
  $("csv-btn").addEventListener("click", async () => {
    if (!current) return;
    const btn = $("csv-btn"); btn.disabled = true; btn.textContent = "Preparing…";
    const from = new Date(Date.now() - rangeS * 1000).toISOString();
    const cols = ["ts", "ens_tvoc", "ens_eco2", "ens_aqi", "sgp_raw", "sgp_voc_index", "temp_c", "rh", "score", "prob", "detected"];
    const lines = [cols.join(",")];
    for (let off = 0; off < 300000; off += 1000) {
      const { data, error } = await sb.from("readings").select(cols.join(",")).eq("device_id", current)
        .gte("ts", from).order("ts").range(off, off + 999);
      if (error || !data.length) break;
      for (const r of data) lines.push(cols.map((c) => r[c] ?? "").join(","));
      if (data.length < 1000) break;
    }
    const blob = new Blob([lines.join("\n")], { type: "text/csv" });
    const a = el("a"); a.href = URL.createObjectURL(blob);
    a.download = `${current}_${new Date().toISOString().slice(0, 10)}.csv`; a.click();
    btn.disabled = false; btn.textContent = "Download CSV";
  });

  init();
})();
