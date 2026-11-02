"use strict";

const $ = (id) => document.getElementById(id);
const SVG_NS = "http://www.w3.org/2000/svg";
const fmtInt = new Intl.NumberFormat("id-ID");
const fmtTime = new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const fmtDate = new Intl.DateTimeFormat("id-ID", { timeZone: "Asia/Jakarta", day: "2-digit", month: "short", year: "numeric", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
const SQL_KEYWORDS = new Set(("select from where and or not order by desc asc limit group as join left inner on with over partition " +
  "is null like in between distinct case when then else end union all having").split(" "));
const SQL_FUNCTIONS = new Set("count sum min max avg round strftime substr coalesce lag lead length julianday datetime".split(" "));
// Fixed diagram positions for the project tables; relations are drawn only when present.
const ERD = { width: 290, header: 36, row: 21, viewWidth: 1150,
  pos: { devices: [40, 20], alerts: [40, 268], readings: [440, 20], delivery_attempts: [840, 20] } };
const state = { loading: false, last: 0, pending: null };

function el(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}
function svg(tag, attrs = {}, text) {
  const node = document.createElementNS(SVG_NS, tag);
  for (const [name, value] of Object.entries(attrs)) node.setAttribute(name, value);
  if (text !== undefined) node.textContent = text;
  return node;
}
const set = (id, value) => { $(id).textContent = value; };
const dateTime = (value) => Number.isFinite(Date.parse(value)) ? `${fmtDate.format(new Date(value))} WIB` : "—";
const bytes = (value) => value >= 1048576 ? `${(value / 1048576).toLocaleString("id-ID", { maximumFractionDigits: 1 })} MB` : `${fmtInt.format(Math.ceil(value / 1024))} KB`;
const cellText = (value) => value === null || value === undefined ? "NULL" : String(value);

function highlightSql(sql) {
  const fragment = document.createDocumentFragment();
  const pattern = /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)|([\s\S])/g;
  let match;
  while ((match = pattern.exec(sql))) {
    const [token, comment, text, numeral, word] = match;
    let className = comment ? "com" : text ? "str" : numeral ? "num" : "";
    if (word) {
      const lower = word.toLowerCase();
      if (SQL_KEYWORDS.has(lower)) className = "kw";
      else if (SQL_FUNCTIONS.has(lower) && sql[match.index + word.length] === "(") className = "fn";
    }
    fragment.append(className ? el("span", className, token) : document.createTextNode(token));
  }
  return fragment;
}

function renderInfo(data) {
  const info = data.database;
  set("db-engine", info.engine);
  set("db-file", `${info.file.replaceAll("\\", "/")} · ${bytes(info.bytes + info.wal_bytes)}`);
  set("db-journal", `${info.journal_mode.toUpperCase()} · foreign key aktif`);
  set("db-generated", dateTime(data.generated_at));
}

function renderCounts(tables) {
  const cards = tables.map((table) => {
    const card = el("article", "metric");
    const value = el("div", "metric-value"); value.append(el("span", "", fmtInt.format(table.rows)));
    const icon = svg("svg", { class: "metric-icon" }); icon.append(svg("use", { href: "#i-stack" }));
    card.append(value, el("strong", "mono", table.name), el("span", "metric-foot", table.note || `${table.columns.length} kolom`), icon);
    return card;
  });
  $("table-counts").replaceChildren(...cards);
}

const boxHeight = (table) => ERD.header + table.columns.length * ERD.row;

function erdBox(table, x, y) {
  const group = svg("g");
  const height = boxHeight(table);
  group.append(svg("rect", { class: "erd-box", x, y, width: ERD.width, height, rx: 12 }));
  group.append(svg("rect", { class: "erd-head", x: x + 1, y: y + 1, width: ERD.width - 2, height: ERD.header - 1, rx: 11 }));
  group.append(svg("rect", { class: "erd-head", x: x + 1, y: y + 18, width: ERD.width - 2, height: ERD.header - 18 }));
  group.append(svg("text", { class: "erd-title", x: x + 14, y: y + 23 }, table.name));
  group.append(svg("text", { class: "erd-count", x: x + ERD.width - 14, y: y + 23, "text-anchor": "end" }, `${fmtInt.format(table.rows)} baris`));
  table.columns.forEach((column, index) => {
    const top = y + ERD.header + index * ERD.row;
    if (index) group.append(svg("line", { x1: x + 1, x2: x + ERD.width - 1, y1: top, y2: top, stroke: "#262626" }));
    group.append(svg("text", { class: "erd-col", x: x + 14, y: top + 14.5 }, column.name));
    group.append(svg("text", { class: "erd-type", x: x + ERD.width - 50, y: top + 14.5, "text-anchor": "end" }, column.type));
    const key = [column.pk && "PK", column.fk && "FK"].filter(Boolean).join(" ");
    if (key) group.append(svg("text", { class: "erd-key", x: x + ERD.width - 14, y: top + 14.5, "text-anchor": "end" }, key));
  });
  return group;
}

function erdLine(root, points, labels, logical = false) {
  root.append(svg("polyline", { class: `erd-line${logical ? " logical" : ""}`, points: points.map((point) => point.join(",")).join(" ") }));
  for (const [text, x, y, className = "erd-label"] of labels) root.append(svg("text", { class: className, x, y, "text-anchor": "middle" }, text));
}

function renderErd(tables) {
  const byName = Object.fromEntries(tables.map((table) => [table.name, table]));
  const placed = Object.entries(ERD.pos).filter(([name]) => byName[name]);
  const height = Math.max(...placed.map(([name, [, y]]) => y + boxHeight(byName[name]))) + 24;
  const root = svg("svg", { viewBox: `0 0 ${ERD.viewWidth} ${height}`, role: "img", "aria-label": "Diagram relasi tabel basis data" });
  const has = (table, column) => byName[table]?.columns.some((item) => item.name === column && item.fk);
  const [dx, dy] = ERD.pos.devices, [ax, ay] = ERD.pos.alerts, [rx] = ERD.pos.readings, [qx] = ERD.pos.delivery_attempts;
  const right = (x) => x + ERD.width;
  let foreignKeys = 0;
  if (has("readings", "device_id")) {
    foreignKeys++;
    erdLine(root, [[right(dx), 100], [rx, 100]], [["device_id", (right(dx) + rx) / 2, 92], ["1", right(dx) + 10, 116, "erd-card"], ["N", rx - 10, 116, "erd-card"]]);
  }
  if (has("alerts", "device_id")) {
    foreignKeys++;
    const x = dx + ERD.width / 2, top = dy + boxHeight(byName.devices);
    erdLine(root, [[x, top], [x, ay]], [["device_id", x + 40, (top + ay) / 2 + 4], ["1", x - 12, top + 16, "erd-card"], ["N", x - 12, ay - 6, "erd-card"]]);
  }
  if (has("alerts", "event_id")) {
    foreignKeys++;
    erdLine(root, [[rx, 300], [right(ax), 300]], [["event_id", (right(ax) + rx) / 2, 292], ["1", rx - 10, 316, "erd-card"], ["N", right(ax) + 10, 316, "erd-card"]]);
  }
  if (byName.readings && byName.delivery_attempts) {
    erdLine(root, [[right(rx), 150], [qx, 150]], [["identitas", (right(rx) + qx) / 2, 134], ["pengiriman", (right(rx) + qx) / 2, 146],
      ["1", right(rx) + 10, 166, "erd-card"], ["0..1", qx - 16, 166, "erd-card"]], true);
  }
  for (const [name, [x, y]] of placed) root.append(erdBox(byName[name], x, y));
  $("erd").replaceChildren(root);
  set("relation-badge", `${foreignKeys} foreign key`);
}

function headerRow(names) {
  const row = el("tr");
  for (const name of names) row.append(el("th", "", name));
  const head = el("thead"); head.append(row);
  return head;
}

function structurePanel(table) {
  const panel = el("article", "panel structure-panel"); panel.id = `table-${table.name}`;
  const heading = el("div", "panel-heading"), copy = el("div");
  copy.append(el("h2", "mono", table.name), el("p", "", `${table.note ? `${table.note} · ` : ""}${fmtInt.format(table.rows)} baris`));
  heading.append(copy, el("span", "panel-badge", `${table.columns.length} kolom`));
  const grid = el("table", "structure-table");
  const body = el("tbody");
  table.columns.forEach((column, index) => {
    const row = el("tr");
    const keys = el("td");
    if (column.pk) keys.append(el("span", "key-tag pk", "PK"));
    if (column.fk) { keys.append(el("span", "key-tag", "FK")); keys.append(document.createTextNode(`→ ${column.fk}`)); }
    if (!column.pk && !column.fk) keys.textContent = "—";
    const nullable = !(column.notnull || column.pk);
    row.append(el("td", "", String(index + 1)), el("td", "mono", column.name), el("td", "mono", column.type),
      el("td", "", nullable ? "boleh" : "NOT NULL"), el("td", "mono", column.default ?? "—"), keys,
      el("td", "mono", column.checks.length ? column.checks.join("; ") : "—"), el("td", "", column.comment || "—"));
    body.append(row);
  });
  grid.append(headerRow(["#", "KOLOM", "TIPE", "NULL", "DEFAULT", "KUNCI", "ATURAN CHECK", "KETERANGAN"]), body);
  const wrap = el("div", "table-wrap"); wrap.append(grid);
  const meta = el("div", "structure-meta");
  const indexes = el("div"), constraints = el("div");
  const indexList = el("ul"), constraintList = el("ul");
  for (const index of table.indexes) {
    const auto = index.name.startsWith("sqlite_autoindex");
    const label = auto ? `otomatis (${index.origin})` : index.name;
    indexList.append(el("li", "", `${label}: (${index.columns.join(", ")})${index.unique ? " UNIQUE" : ""}${index.where ? ` WHERE ${index.where}` : ""}`));
  }
  for (const item of table.constraints.length ? table.constraints : ["—"]) constraintList.append(el("li", "", item));
  indexes.append(el("span", "section-label", "INDEKS"), indexList);
  constraints.append(el("span", "section-label", "CONSTRAINT TINGKAT TABEL"), constraintList);
  meta.append(indexes, constraints);
  panel.append(heading, wrap, meta);
  return panel;
}

function queryPanel(query, index) {
  const panel = el("article", "panel query-panel"); panel.id = `query-${index + 1}`;
  const heading = el("div", "panel-heading"), copy = el("div");
  copy.append(el("h2", "", query.judul), el("p", "", query.tujuan));
  const rows = query.rows || [];
  heading.append(copy, el("span", "panel-badge", query.error ? "gagal" : `${rows.length} baris`));
  const code = el("pre", "code-block"); code.append(highlightSql(query.sql));
  panel.append(heading, code);
  if (query.error) { panel.append(el("p", "query-error", `Query gagal: ${query.error}`)); return panel; }
  const grid = el("table"), body = el("tbody");
  if (!rows.length) {
    const row = el("tr"), cell = el("td", "empty-row", "Tidak ada baris yang memenuhi kondisi query");
    cell.colSpan = query.columns.length; row.append(cell); body.append(row);
  }
  for (const values of rows) {
    const row = el("tr");
    for (const value of values) row.append(el("td", "", cellText(value)));
    body.append(row);
  }
  grid.append(headerRow(query.columns), body);
  const wrap = el("div", "table-wrap"); wrap.append(grid);
  const meta = el("div", "query-meta");
  const count = el("span"); count.append(el("strong", "", `${rows.length} baris`), document.createTextNode(query.truncated ? " (ditampilkan sebagian)" : " hasil"));
  const time = el("span"); time.append(document.createTextNode("Waktu eksekusi "), el("strong", "", `${query.elapsed_ms.toFixed(2)} ms`));
  const plan = el("div"); plan.append(document.createTextNode("Rencana eksekusi (EXPLAIN QUERY PLAN):"));
  const steps = el("ul", "plan-list"); for (const step of query.plan) steps.append(el("li", "", step));
  plan.append(steps);
  meta.append(count, time, plan);
  panel.append(wrap, meta);
  return panel;
}

function render(data) {
  renderInfo(data);
  renderCounts(data.tables);
  renderErd(data.tables);
  $("structures").replaceChildren(...data.tables.map(structurePanel));
  $("queries").replaceChildren(...data.queries.map(queryPanel));
}

async function load() {
  if (state.loading) return;
  state.loading = true;
  try {
    const response = await fetch("/api/database", { cache: "no-store" });
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    render(await response.json());
    $("side-dot").className = "pulse-dot live"; set("side-status", "Basis data terbaca");
  } catch (error) {
    $("side-dot").className = "pulse-dot offline"; set("side-status", `Gagal membaca: ${error.message}`);
  } finally {
    state.loading = false; state.last = Date.now();
  }
}

// New readings refresh the page at most every 10 seconds.
function scheduleLoad() {
  if (state.pending) return;
  state.pending = setTimeout(() => { state.pending = null; load(); }, Math.max(0, 10000 - (Date.now() - state.last)));
}

function init() {
  const tick = () => set("clock", `${fmtTime.format(new Date())} WIB`);
  tick(); setInterval(tick, 1000);
  $("reload-button").addEventListener("click", load);
  load();
  if ("EventSource" in window) new EventSource("/api/stream").addEventListener("update", scheduleLoad);
}
document.addEventListener("DOMContentLoaded", init);
