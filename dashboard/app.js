"use strict";

const $ = (id) => document.getElementById(id);
const colors = { grid: "#303030", text: "#888888", coral: "#f5f5f5", aqua: "#777777", purple: "#f5f5f5", blue: "#626262" };
const state = { snapshot: null, paused: false, range: "all", protocol: "ALL", source: "ALL", session: "ALL", stream: null, lastFetch: 0, fetching: false, hover: -1, selectedId: null };
const fmtInt = new Intl.NumberFormat("id-ID");
const fmtTime = new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const fmtShort = new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", hour12: false });
const fmtDate = new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const toMs = (value) => value ? Date.parse(value) : NaN;
const shortTime = (value) => Number.isFinite(toMs(value)) ? fmtShort.format(new Date(value)) : "—";
const dateTime = (value) => Number.isFinite(toMs(value)) ? fmtDate.format(new Date(value)) : "—";
const measured = (value) => typeof value === "number" && Number.isFinite(value) && value >= 0;
const number = (value, digits = 1) => typeof value === "number" && Number.isFinite(value) ? value.toFixed(digits) : "—";
const sourceLabel = (value) => ({ dht22: "DHT22", random: "ESP32 acak", demo: "Demo lokal", unknown: "Tidak diketahui" })[value] || "Tidak diketahui";
const set = (id, value) => { $(id).textContent = value; };
const quantile = (values, fraction) => { if (!values.length) return null; const sorted = [...values].sort((a, b) => a - b); const at = (sorted.length - 1) * fraction; const lo = Math.floor(at); return sorted[lo] + (sorted[Math.ceil(at)] - sorted[lo]) * (at - lo); };

function updateClock() { $("clock").textContent = `${fmtTime.format(new Date())} WIB`; }
function setLight(id, mode) { $(id).className = `status-light ${mode}`; }
function updateConnection(mode) {
  const connected = mode === "live" || mode === "polling";
  const receiving = !!state.snapshot?.status?.sensor_recent;
  const indicator = connected && receiving ? "live" : mode === "offline" ? "offline" : "";
  $("stream-pill").querySelector(".pulse-dot").className = `pulse-dot ${indicator}`;
  $("side-dot").className = `pulse-dot ${indicator}`;
  set("stream-label", !connected ? mode === "offline" ? "OFFLINE" : "CONNECTING" : receiving ? "LIVE SENSOR" : "WAITING SENSOR");
  set("side-status", !connected ? mode === "offline" ? "Koneksi terputus" : "Menghubungkan..." : receiving ? "Data sensor masuk" : "Menunggu node sensor");
  if (mode === "offline") { set("local-status", "Tidak terhubung"); setLight("local-light", "bad"); }
}
function rangeRows() {
  const rows = state.snapshot?.readings || [];
  const cut = state.range === "all" ? -Infinity : Date.now() - Number(state.range) * 60000;
  return rows.filter((row) => toMs(row.waktu_diterima) >= cut && (state.protocol === "ALL" || row.protokol === state.protocol)).reverse();
}
function serviceLabel(value) { return ({ online: "Terhubung", offline: "Terputus", connecting: "Menghubungkan", waiting: "Menunggu data", unknown: "Memeriksa", unconfigured: "Belum dikonfigurasi", error: "Bermasalah" })[value] || value || "Tidak diketahui"; }
function queryFilters() {
  const params = new URLSearchParams();
  if (state.source !== "ALL") params.set("source_mode", state.source);
  if (state.session !== "ALL") {
    const [device, session] = state.session.split("|");
    params.set("device_id", device); params.set("session_id", session);
  }
  return params.toString();
}
function renderSessions(data) {
  const select = $("session-filter"), options = [new Option("Semua sesi", "ALL")];
  const seen = new Set();
  for (const item of data.sessions || []) {
    if (!item.session_id) continue;
    const value = `${item.device_id}|${item.session_id}`;
    if (seen.has(value)) continue;
    seen.add(value);
    options.push(new Option(`${item.device_id} / ${item.session_id}`, value));
  }
  select.replaceChildren(...options); select.value = state.session;
}
function renderSummary(data) {
  const latest = data.readings[0]; const totals = data.totals; const status = data.status;
  set("metric-temp", latest ? number(latest.suhu) : "—"); set("metric-humidity", latest ? number(latest.kelembapan) : "—");
  set("metric-total", fmtInt.format(totals.total)); set("metric-pending", fmtInt.format(totals.pending));
  set("temp-foot", latest ? `${latest.device_id} · ${dateTime(latest.waktu_diterima)}` : "Belum ada pembacaan");
  set("humidity-foot", latest ? `${sourceLabel(latest.source_mode)} · ${latest.protokol} · seq ${latest.seq}` : "Belum ada pembacaan");
  set("total-foot", `MQTT ${fmtInt.format(totals.mqtt)} · HTTP ${fmtInt.format(totals.http)}`);
  set("pending-foot", `${fmtInt.format(totals.synced)} tersinkron ke Supabase`);
  set("sensor-status", status.sensor_recent ? "Data baru masuk" : latest ? "Tidak ada data baru" : "Menunggu data");
  setLight("sensor-light", status.sensor_recent ? "good" : latest ? "bad" : "");
  set("mqtt-status", serviceLabel(status.mqtt)); setLight("mqtt-light", status.mqtt === "online" ? "good" : status.mqtt === "offline" ? "bad" : "");
  set("local-status", "Siap · SQLite"); setLight("local-light", "good");
  set("supabase-status", serviceLabel(status.supabase)); setLight("supabase-light", status.supabase === "online" ? "good" : status.supabase === "error" ? "bad" : "");
  set("pipe-device", latest ? latest.device_id : "Menunggu perangkat");
  set("pipe-receiver", `${fmtInt.format(totals.total)} diterima`); set("pipe-local", `${fmtInt.format(totals.total)} tercatat`); set("pipe-cloud", `${fmtInt.format(totals.synced)} tersinkron`);
  set("last-event", latest ? `Terakhir ${dateTime(latest.waktu_diterima)}` : "Belum ada aktivitas");
  set("last-sync", status.last_sync_at ? dateTime(status.last_sync_at) : "Belum ada");
  const lastRequest = data.readings.find((row) => row.sync_request_ms != null);
  set("sync-request", lastRequest ? `Request Supabase terakhir: ${number(lastRequest.sync_request_ms, 2)} ms / batch` : "Request Supabase terakhir: belum ada data");
  set("backlog", fmtInt.format(totals.pending));
  set("sync-detail", `${status.sync_error ? `${serviceLabel(status.supabase)} · ${status.sync_error}` : serviceLabel(status.supabase)} · ${data.attempt_totals?.pending || 0} metrik tertunda`);
  $("supabase-status").title = status.last_check_at ? `Diperiksa ${dateTime(status.last_check_at)} WIB` : "Belum diperiksa";
  const uptime = status.uptime_seconds || 0; set("uptime", `${Math.floor(uptime / 3600)}j ${Math.floor(uptime % 3600 / 60)}m ${uptime % 60}d`);
  set("updated-at", dateTime(data.server_time));
  const stale = !status.sensor_recent;
  $("source-notice").hidden = !stale;
  set("source-last", latest ? `Data terakhir: ${dateTime(latest.waktu_diterima)} WIB.` : "Belum ada pembacaan.");
  const command = $("source-command").textContent;
  set("source-command", command.replace(/ --protocol (both|http)$/, status.mqtt === "online" ? " --protocol both" : " --protocol http"));
  set("trend-title", stale && latest ? "Tren sensor (riwayat)" : "Tren sensor langsung");
  set("throughput-title", stale && latest ? "Aliran data (riwayat)" : "Aliran data MQTT vs. HTTP");
  updateConnection(state.stream?.readyState === 1 ? "live" : "polling");
}

function canvas(id) {
  const node = $(id), rect = node.getBoundingClientRect(), dpr = Math.min(window.devicePixelRatio || 1, 2);
  const width = Math.max(1, rect.width), height = Math.max(1, rect.height);
  node.width = Math.round(width * dpr); node.height = Math.round(height * dpr);
  const ctx = node.getContext("2d"); ctx.scale(dpr, dpr); ctx.clearRect(0, 0, width, height); ctx.font = "10px Montserrat, sans-serif"; ctx.fillStyle = colors.text;
  return { node, ctx, width, height };
}
function axis(ctx, box, ticks = 4) {
  ctx.lineWidth = 1; ctx.strokeStyle = colors.grid;
  for (let i = 0; i <= ticks; i++) { const y = box.top + box.height * i / ticks; ctx.beginPath(); ctx.moveTo(box.left, y + .5); ctx.lineTo(box.left + box.width, y + .5); ctx.stroke(); }
}
function domain(values, padding = .12) { const min = Math.min(...values), max = Math.max(...values), span = max - min || Math.max(Math.abs(max) * .1, 1); return [min - span * padding, max + span * padding]; }
function label(ctx, text, x, y, align = "left") { ctx.textAlign = align; ctx.fillStyle = colors.text; ctx.fillText(text, x, y); }
function renderTrend(rows) {
  const { node, ctx, width, height } = canvas("trend-chart"); $("trend-empty").hidden = !!rows.length;
  if (!rows.length) { node.onmousemove = null; return; }
  const box = { left: 37, top: 10, width: width - 78, height: height - 39 }; axis(ctx, box);
  const temps = rows.map((r) => Number(r.suhu)), hums = rows.map((r) => Number(r.kelembapan));
  const td = domain(temps), hd = domain(hums); const times = rows.map((r) => toMs(r.waktu_diterima));
  const minT = Math.min(...times), maxT = Math.max(...times), span = maxT - minT || 1;
  const x = (i) => box.left + (times[i] - minT) / span * box.width;
  const y = (value, [min, max]) => box.top + box.height - (value - min) / (max - min) * box.height;
  for (let i = 0; i <= 4; i++) { const frac = 1 - i / 4; label(ctx, number(td[0] + (td[1] - td[0]) * frac, 0) + "°", box.left - 7, box.top + box.height * i / 4 + 3, "right"); label(ctx, number(hd[0] + (hd[1] - hd[0]) * frac, 0) + "%", box.left + box.width + 7, box.top + box.height * i / 4 + 3); }
  for (let i = 0; i <= 4; i++) { const at = minT + (maxT - minT) * i / 4; label(ctx, fmtShort.format(new Date(at)), box.left + box.width * i / 4, height - 8, i === 0 ? "left" : i === 4 ? "right" : "center"); }
  function line(values, scale, color, fillArea) {
    const drawPath = () => { ctx.beginPath(); values.forEach((value, i) => { const px = x(i), py = y(value, scale); if (!i) ctx.moveTo(px, py); else ctx.lineTo(px, py); }); };
    if (fillArea) {
      drawPath(); ctx.lineTo(x(values.length - 1), box.top + box.height); ctx.lineTo(x(0), box.top + box.height); ctx.closePath();
      const gradient = ctx.createLinearGradient(0, box.top, 0, box.top + box.height);
      gradient.addColorStop(0, "#ffffff28"); gradient.addColorStop(1, "#ffffff00"); ctx.fillStyle = gradient; ctx.fill();
    }
    drawPath(); ctx.strokeStyle = color; ctx.lineWidth = 2.2; ctx.lineJoin = "round"; ctx.stroke();
    const every = Math.max(1, Math.ceil(values.length / 48)); values.forEach((value, i) => { if (i % every && i !== values.length - 1) return; ctx.beginPath(); ctx.arc(x(i), y(value, scale), 2.3, 0, Math.PI * 2); ctx.fillStyle = color; ctx.fill(); });
  }
  line(temps, td, colors.coral, true); line(hums, hd, colors.aqua, false);
  const tip = $("trend-tooltip");
  node.onmousemove = (event) => {
    const rect = node.getBoundingClientRect(), mx = event.clientX - rect.left;
    let near = 0, distance = Infinity; for (let i = 0; i < rows.length; i++) { const next = Math.abs(x(i) - mx); if (next < distance) { distance = next; near = i; } }
    const row = rows[near]; tip.textContent = `${dateTime(row.waktu_diterima)}\n${row.protokol} · ${row.device_id} · seq ${row.seq}\nSuhu ${number(row.suhu)} °C   Kelembapan ${number(row.kelembapan)} %RH`;
    tip.style.whiteSpace = "pre-line"; tip.hidden = false; tip.style.left = `${Math.min(Math.max(0, x(near) + 10), width - 185)}px`; tip.style.top = `${Math.max(2, y(row.suhu, td) - 60)}px`;
  };
  node.onmouseleave = () => { tip.hidden = true; };
}
function renderThroughput(rows) {
  const { ctx, width, height } = canvas("throughput-chart"); $("throughput-empty").hidden = !!rows.length;
  const box = { left: 27, top: 13, width: width - 40, height: height - 42 }; axis(ctx, box);
  if (!rows.length) { set("rate-badge", "0 / menit"); return; }
  const minTime = toMs(rows[0].waktu_diterima), maxTime = toMs(rows[rows.length - 1].waktu_diterima);
  const buckets = Math.min(16, Math.max(5, Math.floor(box.width / 32))); const span = Math.max(maxTime - minTime, 60000);
  const start = maxTime - span; const data = Array.from({ length: buckets }, () => ({ MQTT: 0, HTTP: 0 }));
  for (const row of rows) { const index = Math.min(buckets - 1, Math.max(0, Math.floor((toMs(row.waktu_diterima) - start) / span * buckets))); data[index][row.protokol] = (data[index][row.protokol] || 0) + 1; }
  const max = Math.max(1, ...data.flatMap((d) => [d.MQTT, d.HTTP]));
  for (let i = 0; i <= 4; i++) label(ctx, String(Math.round(max * (1 - i / 4))), box.left - 7, box.top + box.height * i / 4 + 3, "right");
  const cell = box.width / buckets, barWidth = Math.max(3, cell * .26);
  data.forEach((bucket, i) => {
    const center = box.left + (i + .5) * cell;
    for (const [key, color, offset] of [["MQTT", colors.purple, -barWidth - 2], ["HTTP", colors.blue, 2]]) {
      const barHeight = bucket[key] / max * box.height;
      ctx.fillStyle = color; ctx.fillRect(center + offset, box.top + box.height - barHeight, barWidth, barHeight);
    }
  });
  label(ctx, fmtShort.format(new Date(start)), box.left, height - 8); label(ctx, fmtShort.format(new Date(maxTime)), box.left + box.width, height - 8, "right");
  const recent = rows.filter((r) => toMs(r.waktu_diterima) >= Date.now() - 60000).length; set("rate-badge", `${recent} / menit`);
}
function renderScatter(rows) {
  const { ctx, width, height } = canvas("scatter-chart"); $("scatter-empty").hidden = !!rows.length; set("scatter-count", `${rows.length} titik`);
  if (!rows.length) return;
  const box = { left: 43, top: 10, width: width - 56, height: height - 40 }; axis(ctx, box);
  const td = domain(rows.map((r) => Number(r.suhu)), .08), hd = domain(rows.map((r) => Number(r.kelembapan)), .08);
  for (let i = 0; i <= 4; i++) { label(ctx, number(hd[1] - (hd[1] - hd[0]) * i / 4, 0) + "%", box.left - 9, box.top + box.height * i / 4 + 3, "right"); label(ctx, number(td[0] + (td[1] - td[0]) * i / 4, 0) + "°", box.left + box.width * i / 4, height - 8, i === 0 ? "left" : i === 4 ? "right" : "center"); }
  for (const row of rows) { const x = box.left + (row.suhu - td[0]) / (td[1] - td[0]) * box.width, y = box.top + box.height - (row.kelembapan - hd[0]) / (hd[1] - hd[0]) * box.height; ctx.beginPath(); ctx.arc(x, y, 3.1, 0, Math.PI * 2); ctx.fillStyle = row.protokol === "MQTT" ? "#f5f5f5a8" : "#777777a8"; ctx.fill(); }
}
function renderLatency(rows) {
  const values = rows.map((r) => r.local_write_ms).filter(measured);
  set("latency-p50", values.length ? number(quantile(values, .5), 2) : "—"); set("latency-p95", values.length ? number(quantile(values, .95), 2) : "—"); set("latency-count", String(values.length));
  const proto = ["MQTT", "HTTP"].map((name) => ({ name, value: quantile(rows.filter((r) => r.protokol === name).map((r) => r.local_write_ms).filter(measured), .95) }));
  const max = Math.max(1, ...proto.map((p) => p.value || 0));
  for (const item of proto) { const id = item.name.toLowerCase(); set(`${id}-latency`, item.value === null ? "—" : `${number(item.value, 2)} ms`); $(`${id}-latency-bar`).style.width = `${item.value === null ? 0 : item.value / max * 100}%`; }
}
function rangeAttempts() {
  const cut = state.range === "all" ? -Infinity : Date.now() - Number(state.range) * 60000;
  return (state.snapshot?.attempts || []).filter((item) => toMs(item.waktu_dilaporkan) >= cut && (state.protocol === "ALL" || item.protokol === state.protocol));
}
function renderRtt() {
  const attempts = rangeAttempts();
  for (const protocol of ["MQTT", "HTTP"]) {
    const rows = attempts.filter((item) => item.protokol === protocol), prefix = `rtt-${protocol.toLowerCase()}`;
    const values = rows.filter((item) => item.status === "ok" && measured(item.rtt_ms)).map((item) => item.rtt_ms);
    set(`${prefix}-count`, String(values.length));
    set(`${prefix}-p50`, number(quantile(values, .5), 2)); set(`${prefix}-p95`, number(quantile(values, .95), 2));
    set(`${prefix}-timeout`, String(rows.filter((item) => item.status === "timeout").length));
    set(`${prefix}-error`, String(rows.filter((item) => item.status === "error").length));
  }
  const sources = new Set(attempts.map((item) => item.source_mode));
  set("rtt-source-note", sources.size > 1 ? "Beberapa sumber tercampur. Pilih satu sumber sebelum membandingkan RTT." : attempts.length ? `Sumber: ${sourceLabel(attempts[0].source_mode)}. RTT dilaporkan oleh pengirim.` : "Belum ada laporan RTT dari pengirim.");
  set("rtt-total", `${attempts.length} laporan dimuat / ${state.snapshot?.attempt_totals?.total || 0} total`);
}
function makeCell(row, value, className) { const cell = document.createElement("td"); if (className) cell.className = className; if (value instanceof Node) cell.append(value); else cell.textContent = value; row.append(cell); }
function tag(text, className) { const span = document.createElement("span"); span.className = className; span.textContent = text; return span; }
function renderTrace(data) {
  const item = rangeRows().find((row) => row.event_id === state.selectedId);
  $("event-trace").hidden = !item;
  if (!item) return;
  set("trace-title", `${item.device_id} / seq ${item.seq} / ${sourceLabel(item.source_mode)}`);
  set("trace-id", `${item.event_id} / sesi ${item.session_id || "tidak tersedia"}`);
  set("trace-ingress", item.delivery_status === "ok" && measured(item.rtt_ms) ? `${item.protokol} / RTT ${number(item.rtt_ms, 2)} ms (pulang-pergi)` : `${item.protokol} / RTT ${item.delivery_status || "belum dilaporkan"}`);
  set("trace-received", `${dateTime(item.waktu_diterima)} WIB`);
  set("trace-local", item.local_write_ms == null ? "Tersimpan / durasi tidak tersedia" : `Tersimpan / ${number(item.local_write_ms, 2)} ms`);
  set("trace-cloud", item.synced_at ? `${dateTime(item.synced_at)} WIB / ${item.sync_request_ms == null ? "durasi tidak tersedia" : `${number(item.sync_request_ms, 2)} ms/batch`}` : "Menunggu sinkronisasi");
}
function renderTable(data) {
  const rows = rangeRows().reverse().slice(0, 12);
  const body = $("events-body"); body.replaceChildren();
  if (!rows.length) { const tr = document.createElement("tr"); makeCell(tr, "Belum ada pembacaan untuk filter ini", "empty-row"); tr.firstChild.colSpan = 10; body.append(tr); }
  for (const item of rows) {
    const tr = document.createElement("tr");
    tr.classList.toggle("selected", item.event_id === state.selectedId);
    tr.tabIndex = 0; tr.setAttribute("role", "button"); tr.setAttribute("aria-label", `Lihat jejak ${item.device_id} seq ${item.seq}`);
    tr.addEventListener("click", () => { state.selectedId = item.event_id; renderTable(data); });
    tr.addEventListener("keydown", (event) => { if (event.key === "Enter" || event.key === " ") { event.preventDefault(); state.selectedId = item.event_id; renderTable(data); } });
    makeCell(tr, dateTime(item.waktu_diterima)); makeCell(tr, item.device_id);
    makeCell(tr, tag(item.protokol, `protocol-tag ${item.protokol.toLowerCase()}`)); makeCell(tr, String(item.seq));
    makeCell(tr, sourceLabel(item.source_mode));
    makeCell(tr, `${number(item.suhu)} °C`); makeCell(tr, `${number(item.kelembapan)} %RH`);
    makeCell(tr, item.local_write_ms == null ? "—" : `${number(item.local_write_ms, 2)} ms`);
    makeCell(tr, item.delivery_status === "ok" && measured(item.rtt_ms) ? `${number(item.rtt_ms, 2)} ms` : item.delivery_status || "—");
    makeCell(tr, tag(item.synced_at ? "Tersinkron" : "Tertunda", `sync-tag ${item.synced_at ? "done" : "pending"}`)); body.append(tr);
  }
  renderTrace(data);
  set("event-count", `${rows.length} event`);
  set("table-summary", `Menampilkan ${rows.length} dari ${data.readings.length} pembacaan terbaru${data.totals.total > data.readings.length ? ` (maks. ${data.window_limit} dimuat)` : ""}`);
}
function render() { if (!state.snapshot) return; const rows = rangeRows(); renderSummary(state.snapshot); renderTrend(rows); renderThroughput(rows); renderScatter(rows); renderLatency(rows); renderRtt(); renderTable(state.snapshot); }

async function refresh() {
  if (state.fetching || state.paused) return;
  state.fetching = true;
  const filters = queryFilters();
  try {
    const response = await fetch(`/api/dashboard?${filters}`, { cache: "no-store" }); if (!response.ok) throw new Error(`HTTP ${response.status}`);
    const data = await response.json();
    if (filters !== queryFilters()) return;
    state.snapshot = data; state.lastFetch = Date.now(); renderSessions(data); render();
    updateConnection(state.stream?.readyState === 1 ? "live" : "polling");
  } catch (error) { updateConnection("offline"); set("updated-at", `Gagal mengambil data: ${error.message}`); }
  finally { state.fetching = false; if (filters !== queryFilters()) refresh(); }
}
function connect() {
  if (!("EventSource" in window)) return;
  const stream = new EventSource("/api/stream"); state.stream = stream;
  stream.onopen = () => updateConnection("live");
  stream.addEventListener("update", () => refresh());
  stream.onerror = () => { if (stream.readyState !== 1) updateConnection(state.lastFetch ? "polling" : "offline"); };
}
function downloadCsv(readings, columns, name) {
  if (!readings.length) return;
  const cell = (value) => `"${String(value ?? "").replaceAll('"', '""')}"`;
  const csv = [columns.join(","), ...readings.map((row) => columns.map((col) => cell(row[col])).join(","))].join("\r\n");
  const url = URL.createObjectURL(new Blob(["\ufeff", csv], { type: "text/csv;charset=utf-8" }));
  const link = document.createElement("a"); link.href = url; link.download = `${name}-${new Date().toISOString().slice(0, 10)}.csv`; document.body.append(link); link.click(); link.remove(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}
function exportCsv() {
  downloadCsv(rangeRows().reverse(), ["waktu_diterima", "device_id", "session_id", "source_mode", "protokol", "seq", "suhu", "kelembapan", "local_write_ms", "rtt_ms", "delivery_status", "sync_request_ms", "synced_at", "event_id"], "sensor-pembacaan");
}
function exportRttCsv() {
  downloadCsv(rangeAttempts(), ["waktu_dilaporkan", "device_id", "session_id", "source_mode", "protokol", "seq", "status", "rtt_ms", "synced_at", "event_id"], "sensor-rtt");
}
function init() {
  updateClock(); setInterval(updateClock, 1000);
  document.querySelectorAll(".range-button").forEach((button) => button.addEventListener("click", () => { state.range = button.dataset.range; document.querySelectorAll(".range-button").forEach((b) => b.classList.toggle("active", b === button)); render(); }));
  function chooseProtocol(protocol) {
    state.protocol = protocol; $("protocol-filter").value = protocol;
    document.querySelectorAll(".protocol-button").forEach((button) => button.classList.toggle("active", button.dataset.protocol === protocol));
    render();
  }
  document.querySelectorAll(".protocol-button").forEach((button) => button.addEventListener("click", () => chooseProtocol(button.dataset.protocol)));
  $("protocol-filter").addEventListener("change", (event) => chooseProtocol(event.target.value));
  for (const [id, field] of [["source-filter", "source"], ["session-filter", "session"]]) {
    $(id).addEventListener("change", (event) => {
      state[field] = event.target.value; state.selectedId = null;
      state.snapshot = null; state.lastFetch = 0; refresh();
    });
  }
  document.querySelectorAll(".nav-item").forEach((link) => link.addEventListener("click", () => {
    document.querySelectorAll(".nav-item").forEach((item) => item.classList.toggle("active", item === link));
  }));
  $("pause-button").addEventListener("click", () => { state.paused = !state.paused; $("pause-button").lastElementChild.textContent = state.paused ? "Lanjutkan" : "Jeda tampilan"; if (!state.paused) refresh(); });
  $("copy-sensor-command").addEventListener("click", async () => {
    const command = $("source-command").textContent;
    try {
      await navigator.clipboard.writeText(command);
      const button = $("copy-sensor-command"); button.textContent = "Tersalin";
      setTimeout(() => { button.textContent = "Salin perintah"; }, 2000);
    } catch { window.prompt("Salin perintah node demo:", command); }
  });
  $("export-button").addEventListener("click", exportCsv); $("export-button").title = "Ekspor pembacaan terbaru yang dimuat (maksimal 900)";
  $("export-rtt-button").addEventListener("click", exportRttCsv);
  let resizeTimer; window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(render, 100); });
  refresh(); connect(); setInterval(() => { if (Date.now() - state.lastFetch >= 5000) refresh(); else if (state.snapshot && !state.paused) render(); }, 5000);
}
document.addEventListener("DOMContentLoaded", init);
