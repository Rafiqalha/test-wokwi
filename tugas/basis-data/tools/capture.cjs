// Screenshot kode, dashboard, dan halaman database, lalu cetak laporan ke PDF.
// Pemakaian:
//   node capture.cjs <chrome.exe> code <repo> <folder-gambar>
//   node capture.cjs <chrome.exe> app <url-dasar> <folder-gambar> <dashboard|database>
//   node capture.cjs <chrome.exe> pdf <laporan.html> <keluaran.pdf>
//   node capture.cjs <chrome.exe> compress <folder-gambar> <folder-pdf> <lebar-maks> <kualitas-jpeg>
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const { spawn } = require("node:child_process");
const { pathToFileURL } = require("node:url");

const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, timeout = 20000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await check(); if (value) return value; await delay(150); }
  throw new Error("Batas waktu menunggu halaman habis");
}

class CDP {
  constructor(socket) {
    this.socket = socket; this.nextId = 0; this.pending = new Map();
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      const pending = message.id && this.pending.get(message.id);
      if (!pending) return;
      this.pending.delete(message.id);
      if (message.error) pending.reject(new Error(message.error.message)); else pending.resolve(message.result);
    });
  }
  request(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.nextId; this.pending.set(id, { resolve, reject });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const response = await this.request("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  }
}

async function openBrowser(browserPath) {
  const profile = fs.mkdtempSync(path.join(os.tmpdir(), "laporan-cdp-"));
  const child = spawn(browserPath, ["--headless=new", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
    "--hide-scrollbars", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"], { windowsHide: true, stdio: "ignore" });
  const active = path.join(profile, "DevToolsActivePort");
  const port = await until(() => fs.existsSync(active) && fs.readFileSync(active, "utf8").split("\n")[0]);
  const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  const socket = new WebSocket(targets.find((target) => target.type === "page").webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
  const cdp = new CDP(socket);
  await cdp.request("Page.enable"); await cdp.request("Runtime.enable");
  cdp.close = async () => { await cdp.request("Browser.close").catch(() => {}); await delay(400); child.kill(); };
  return cdp;
}

async function go(cdp, url, ready) {
  await cdp.request("Page.navigate", { url });
  await until(() => cdp.evaluate(`document.readyState === "complete" && document.fonts.status === "loaded" && (${ready})`));
  await delay(900);
}

// Union rectangle of one or more elements, in document coordinates.
async function rect(cdp, selectors, pad) {
  return cdp.evaluate(`(() => {
    const boxes = ${JSON.stringify(selectors)}.map((s) => document.querySelector(s).getBoundingClientRect());
    const left = Math.min(...boxes.map((b) => b.left)), top = Math.min(...boxes.map((b) => b.top));
    const right = Math.max(...boxes.map((b) => b.right)), bottom = Math.max(...boxes.map((b) => b.bottom));
    return { x: Math.max(0, left + scrollX - ${pad}), y: Math.max(0, top + scrollY - ${pad}),
             width: right - left + ${2 * pad}, height: bottom - top + ${2 * pad} };
  })()`);
}

async function shoot(cdp, file, clip, format = "png") {
  const result = await cdp.request("Page.captureScreenshot", { format, quality: format === "jpeg" ? 90 : undefined,
    clip: { ...clip, scale: 1 }, captureBeyondViewport: true });
  fs.writeFileSync(file, Buffer.from(result.data, "base64"));
  console.log(`${path.basename(file)} ${Math.round(clip.width)}x${Math.round(clip.height)}`);
}

// ---------- Tampilan kode dengan pewarnaan sintaks sederhana ----------
const LANG = {
  sql: { pattern: /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|()([A-Za-z_][A-Za-z0-9_]*)|([\s\S])/g,
    keywords: new Set(("create table if not exists primary key references on delete cascade check in between and or not null default " +
      "unique index where insert into values ignore integer text real autoincrement like desc length").split(" ")) },
  py: { pattern: /(#[^\n]*)|((?:[rRbBfF]{1,2})?(?:"""[\s\S]*?"""|'''[\s\S]*?'''|"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'))|(\b\d+(?:\.\d+)?\b)|(@[A-Za-z_][\w.]*)|([A-Za-z_][A-Za-z0-9_]*)|([\s\S])/g,
    keywords: new Set(("def return if else elif for in not and or with as try except raise import from class None True False is " +
      "lambda while continue break pass").split(" ")) },
};
const escapeHtml = (text) => text.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]);

function highlight(text, lang) {
  const { pattern, keywords } = LANG[lang];
  const tokens = []; let match; pattern.lastIndex = 0;
  while ((match = pattern.exec(text))) {
    const [token, comment, string, numeral, decorator, word] = match;
    let cls = comment ? "com" : string ? "str" : numeral ? "num" : decorator ? "dec" : "";
    if (word) cls = keywords.has(lang === "sql" ? word.toLowerCase() : word) ? "kw" : text[match.index + word.length] === "(" ? "fn" : "";
    tokens.push([token, cls]);
  }
  const lines = [""];
  for (const [token, cls] of tokens) {
    token.split("\n").forEach((piece, index) => {
      if (index) lines.push("");
      if (piece) lines[lines.length - 1] += cls ? `<span class="${cls}">${escapeHtml(piece)}</span>` : escapeHtml(piece);
    });
  }
  return lines;
}

function codePage(file, label, start, end) {
  const text = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n");
  const lang = file.endsWith(".py") ? "py" : "sql";
  const lines = highlight(text, lang).slice(start - 1, end);
  const rows = lines.map((line, index) => `<div class="ln">${start + index}</div><div class="src">${line || " "}</div>`).join("");
  return `<!doctype html><meta charset="utf-8"><style>
    body { margin: 0; background: #ffffff; font-family: Consolas, 'Cascadia Mono', monospace; }
    .frame { display: inline-block; min-width: 900px; background: #1e1e1e; border-radius: 10px; overflow: hidden; border: 1px solid #2b2b2b; }
    .bar { display: flex; align-items: center; gap: 14px; height: 38px; padding: 0 16px; background: #252526; border-bottom: 1px solid #333; font: 13px 'Segoe UI', Arial, sans-serif; color: #cccccc; }
    .dots span { display: inline-block; width: 11px; height: 11px; margin-right: 6px; border-radius: 50%; background: #5a5a5a; }
    .tab { padding: 6px 12px; background: #1e1e1e; border-radius: 6px 6px 0 0; color: #ffffff; }
    .path { margin-left: auto; color: #9d9d9d; font-size: 12px; }
    .code { display: grid; grid-template-columns: auto 1fr; padding: 12px 22px 16px 0; font-size: 14.5px; line-height: 21px; color: #d4d4d4; }
    .ln { padding: 0 18px 0 16px; text-align: right; color: #6e7681; user-select: none; }
    .src { white-space: pre; padding-right: 24px; }
    .kw { color: #569cd6; } .str { color: #ce9178; } .num { color: #b5cea8; } .com { color: #6a9955; font-style: italic; }
    .fn { color: #dcdcaa; } .dec { color: #c586c0; }
  </style><div class="frame"><div class="bar"><span class="dots"><span></span><span></span><span></span></span>
  <span class="tab">${escapeHtml(path.basename(file))}</span><span class="path">${escapeHtml(label)} &middot; baris ${start}&ndash;${end}</span></div>
  <div class="code">${rows}</div></div>`;
}

// Cari rentang baris dari penanda awal sampai sebelum penanda akhir.
function lineRange(file, startMarker, endMarker) {
  const lines = fs.readFileSync(file, "utf8").replace(/\r\n/g, "\n").split("\n");
  const start = lines.findIndex((line) => line.startsWith(startMarker)) + 1;
  let end = endMarker ? lines.findIndex((line, index) => index >= start && line.startsWith(endMarker)) : lines.length;
  if (start < 1 || end < 0) throw new Error(`Penanda tidak ditemukan: ${startMarker}`);
  while (end > start && !lines[end - 1].trim()) end--;
  return [start, end];
}

async function captureCode(cdp, repo, out) {
  const schema = path.join(repo, "database", "schema.sql"), server = path.join(repo, "server.py");
  const pages = [
    ["kode-schema-1.png", schema, "database/schema.sql", lineRange(schema, "-- ====", "-- 3) Laporan")],
    ["kode-schema-2.png", schema, "database/schema.sql", lineRange(schema, "-- 3) Laporan", null)],
    ["kode-init-db.png", server, "server.py", lineRange(server, "def register_devices", "def save_reading")],
    ["kode-save-reading.png", server, "server.py", lineRange(server, "def save_reading", "def save_metric")],
  ];
  const temp = fs.mkdtempSync(path.join(os.tmpdir(), "laporan-kode-"));
  await cdp.request("Emulation.setDeviceMetricsOverride", { width: 1200, height: 900, deviceScaleFactor: 2, mobile: false });
  for (const [name, file, label, [start, end]] of pages) {
    const html = path.join(temp, name.replace(".png", ".html"));
    fs.writeFileSync(html, codePage(file, label, start, end));
    await go(cdp, pathToFileURL(html).href, "true");
    await shoot(cdp, path.join(out, name), await rect(cdp, [".frame"], 0));
  }
}

async function captureApp(cdp, base, out, group) {
  await cdp.request("Emulation.setDeviceMetricsOverride", { width: 1440, height: 900, deviceScaleFactor: 1.5, mobile: false });
  if (group === "dashboard") {
    await go(cdp, `${base}/`, `document.getElementById("metric-total").textContent !== "--" && document.querySelectorAll("#events-body tr").length > 1`);
    await cdp.evaluate(`document.querySelector('[data-range="15"]').click(); true`);  // Hanya sesi yang sedang berjalan.
    await delay(1500);
    const top = await rect(cdp, [".topbar", ".chart-grid"], 0);
    await shoot(cdp, path.join(out, "dash-atas.jpg"), { x: 0, y: 0, width: 1440, height: top.y + top.height + 24 }, "jpeg");
    await shoot(cdp, path.join(out, "dash-status-rtt.jpg"), await rect(cdp, [".status-strip", ".analysis-grid", ".rtt-panel"], 12), "jpeg");
    await shoot(cdp, path.join(out, "dash-database.jpg"), await rect(cdp, ["#database-summary"], 12), "jpeg");
    await shoot(cdp, path.join(out, "dash-event-log.jpg"), await rect(cdp, ["#pipeline", "#events"], 12), "jpeg");
  } else {
    await go(cdp, `${base}/database`, `!!document.getElementById("query-5")`);
    const top = await rect(cdp, [".topbar", "#table-counts"], 0);
    await shoot(cdp, path.join(out, "db-atas.jpg"), { x: 0, y: 0, width: 1440, height: top.y + top.height + 24 }, "jpeg");
    await shoot(cdp, path.join(out, "db-relasi.jpg"), await rect(cdp, ["#relations"], 12), "jpeg");
    for (const name of ["devices", "readings", "alerts", "delivery_attempts"]) {
      await shoot(cdp, path.join(out, `db-tabel-${name}.jpg`), await rect(cdp, [`#table-${name}`], 12), "jpeg");
    }
    for (let index = 1; index <= 5; index++) {
      await shoot(cdp, path.join(out, `db-query-${index}.jpg`), await rect(cdp, [`#query-${index}`], 12), "jpeg");
    }
    fs.writeFileSync(path.join(out, "..", "bukti", "database.json"),
      JSON.stringify(await cdp.evaluate(`fetch("/api/database").then((r) => r.json())`), null, 2));
  }
}

// Perkecil gambar untuk PDF (batas unggah LMS 2 MB): lebar maksimum dan JPEG berkualitas tertentu.
async function compressImages(cdp, src, dst, maxWidth, quality) {
  fs.mkdirSync(dst, { recursive: true });
  const page = path.join(fs.mkdtempSync(path.join(os.tmpdir(), "laporan-kompres-")), "gambar.html");
  await cdp.request("Emulation.setDeviceMetricsOverride", { width: 2400, height: 1200, deviceScaleFactor: 1, mobile: false });
  for (const name of fs.readdirSync(src).filter((file) => /\.(png|jpe?g)$/i.test(file))) {
    fs.writeFileSync(page, `<!doctype html><style>html,body{margin:0;background:#fff}img{display:block}</style><img id="i" src="${pathToFileURL(path.resolve(src, name)).href}">`);
    await go(cdp, pathToFileURL(page).href, `document.getElementById("i").complete`);
    const [naturalWidth, naturalHeight] = await cdp.evaluate(`[document.getElementById("i").naturalWidth, document.getElementById("i").naturalHeight]`);
    const width = Math.min(maxWidth, naturalWidth), height = Math.round(naturalHeight * width / naturalWidth);
    await cdp.evaluate(`Object.assign(document.getElementById("i").style, { width: "${width}px", height: "${height}px" }) && true`);
    const out = path.join(dst, name.replace(/\.(png|jpe?g)$/i, ".jpg"));
    const result = await cdp.request("Page.captureScreenshot", { format: "jpeg", quality, clip: { x: 0, y: 0, width, height, scale: 1 }, captureBeyondViewport: true });
    fs.writeFileSync(out, Buffer.from(result.data, "base64"));
    console.log(`${path.basename(out)} ${width}x${height} ${(fs.statSync(out).size / 1024).toFixed(0)} KB`);
  }
}

async function printPdf(cdp, html, pdf) {
  await go(cdp, pathToFileURL(path.resolve(html)).href, "Array.from(document.images).every((img) => img.complete)");
  // Nomor halaman diatur oleh @page di laporan.html (tanpa nomor pada sampul).
  const result = await cdp.request("Page.printToPDF", { printBackground: true, preferCSSPageSize: true });
  fs.writeFileSync(pdf, Buffer.from(result.data, "base64"));
  console.log(`${pdf} ${(fs.statSync(pdf).size / 1048576).toFixed(2)} MB`);
}

async function main() {
  const [browserPath, mode, a, b, c] = process.argv.slice(2);
  const cdp = await openBrowser(browserPath);
  try {
    if (mode === "code") { fs.mkdirSync(b, { recursive: true }); await captureCode(cdp, a, b); }
    else if (mode === "app") { fs.mkdirSync(b, { recursive: true }); fs.mkdirSync(path.join(b, "..", "bukti"), { recursive: true }); await captureApp(cdp, a, b, c); }
    else if (mode === "pdf") await printPdf(cdp, a, b);
    else if (mode === "compress") await compressImages(cdp, a, b, Number(c), Number(process.argv[7]));
    else throw new Error(`Mode tidak dikenal: ${mode}`);
  } finally { await cdp.close(); }
}
main().catch((error) => { console.error(error.stack); process.exitCode = 1; });
