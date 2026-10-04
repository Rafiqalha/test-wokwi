// Verify the actual dashboard in a temporary headless Chromium profile via CDP.
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const [browserPath, url, profile, output] = process.argv.slice(2);
const delay = (ms) => new Promise((resolve) => setTimeout(resolve, ms));
async function until(check, timeout = 15000) {
  const end = Date.now() + timeout;
  while (Date.now() < end) { const value = await check(); if (value) return value; await delay(100); }
  throw new Error("Browser check timed out");
}

class CDP {
  constructor(socket) {
    this.socket = socket; this.nextId = 0; this.pending = new Map(); this.errors = [];
    socket.addEventListener("message", ({ data }) => {
      const message = JSON.parse(data);
      if (message.method === "Runtime.exceptionThrown") this.errors.push(message.params.exceptionDetails);
      if (!message.id) return;
      const pending = this.pending.get(message.id); if (!pending) return;
      this.pending.delete(message.id); clearTimeout(pending.timer);
      if (message.error) pending.reject(new Error(message.error.message)); else pending.resolve(message.result);
    });
    socket.addEventListener("close", () => {
      for (const item of this.pending.values()) { clearTimeout(item.timer); item.reject(new Error("Browser closed")); }
      this.pending.clear();
    });
  }
  request(method, params = {}) {
    return new Promise((resolve, reject) => {
      const id = ++this.nextId;
      const timer = setTimeout(() => { this.pending.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 10000);
      this.pending.set(id, { resolve, reject, timer });
      this.socket.send(JSON.stringify({ id, method, params }));
    });
  }
  async evaluate(expression) {
    const response = await this.request("Runtime.evaluate", { expression, returnByValue: true, awaitPromise: true });
    if (response.exceptionDetails) throw new Error(JSON.stringify(response.exceptionDetails));
    return response.result.value;
  }
}

async function main() {
  fs.mkdirSync(profile, { recursive: true });
  const child = spawn(browserPath, ["--headless=new", "--disable-gpu", "--no-sandbox",
    "--no-first-run", "--no-default-browser-check", "--disable-background-networking",
    "--disable-extensions", "--remote-debugging-port=0", `--user-data-dir=${profile}`, "about:blank"],
    { windowsHide: true, stdio: "ignore" });
  let cdp;
  try {
    const active = path.join(profile, "DevToolsActivePort");
    const port = await until(() => fs.existsSync(active) && fs.readFileSync(active, "utf8").split("\n")[0]);
    const targets = await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
    const page = targets.find((target) => target.type === "page"); assert.ok(page);
    const socket = new WebSocket(page.webSocketDebuggerUrl);
    await new Promise((resolve, reject) => { socket.addEventListener("open", resolve, { once: true }); socket.addEventListener("error", reject, { once: true }); });
    cdp = new CDP(socket);
    await cdp.request("Page.enable"); await cdp.request("Runtime.enable"); await cdp.request("Network.enable");
    await cdp.request("Network.setBlockedURLs", { urls: ["*fonts.googleapis.com*", "*fonts.gstatic.com*"] });
    await cdp.request("Page.navigate", { url });
    await until(() => cdp.evaluate('document.getElementById("metric-total")?.textContent === "12"'));
    const select = async (id, value) => cdp.evaluate(`document.getElementById(${JSON.stringify(id)}).value=${JSON.stringify(value)};document.getElementById(${JSON.stringify(id)}).dispatchEvent(new Event("change",{bubbles:true}));true`);
    await select("source-filter", "unknown");
    await until(() => cdp.evaluate('document.getElementById("metric-total").textContent === "0"'));
    await select("source-filter", "demo");
    await until(() => cdp.evaluate('document.getElementById("metric-total").textContent === "12"'));
    const session = await cdp.evaluate('document.getElementById("session-filter").options[1].value');
    await select("session-filter", session);
    await until(() => cdp.evaluate('document.getElementById("metric-total").textContent === "12" && state.snapshot !== null'));
    await cdp.evaluate('document.querySelector("[data-protocol=MQTT]").click();true');
    assert.equal(await cdp.evaluate('document.getElementById("rtt-http-count").textContent'), "0");
    assert.equal(await cdp.evaluate('document.querySelectorAll("#events-body tr").length'), 6);
    await cdp.evaluate('HTMLAnchorElement.prototype.click=function(){};const create=URL.createObjectURL;URL.createObjectURL=(blob)=>{window.csvText=blob.text();return create(blob);};document.getElementById("export-rtt-button").click();true');
    const csv = await cdp.evaluate("window.csvText");
    assert.equal(csv.split("\r\n").length, 7);
    assert.match(csv.split("\r\n")[0], /source_mode/);
    await cdp.evaluate('document.querySelector("[data-protocol=ALL]").click();true');
    const report = {};
    for (const [label, width, height, mobile] of [["desktop", 1440, 1200, false], ["mobile", 390, 844, true]]) {
      await cdp.request("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile });
      await delay(300);
      assert.equal(await cdp.evaluate('document.getElementById("rtt-mqtt-count").textContent'), "6");
      assert.equal(await cdp.evaluate('document.getElementById("rtt-http-count").textContent'), "6");
      const overflow = await cdp.evaluate("document.documentElement.scrollWidth > window.innerWidth");
      assert.equal(overflow, false, `${label} page overflows horizontally`);
      const result = await cdp.request("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      const name = `dashboard-${label}.png`; fs.writeFileSync(path.join(output, name), Buffer.from(result.data, "base64"));
      await cdp.evaluate('document.querySelector(".rtt-panel").scrollIntoView({block:"center",behavior:"instant"});true');
      await delay(100);
      const rttImage = await cdp.request("Page.captureScreenshot", { format: "png", captureBeyondViewport: false });
      const rttName = `dashboard-rtt-${label}.png`; fs.writeFileSync(path.join(output, rttName), Buffer.from(rttImage.data, "base64"));
      report[label] = { rtt_rendered: true, filters_checked: true, csv_checked: true, horizontal_overflow: false, screenshot: name, rtt_screenshot: rttName };
      await cdp.evaluate('window.scrollTo({top:0,behavior:"instant"});true');
    }
    assert.deepEqual(cdp.errors, []);
    process.stdout.write(JSON.stringify(report));
  } finally {
    if (cdp) await cdp.request("Browser.close").catch(() => {});
    else child.kill();
    await delay(500);
  }
}
main().catch((error) => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
