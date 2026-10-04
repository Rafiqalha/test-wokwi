const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const test = require("node:test");

function dashboard() {
  const elements = {};
  const context = { Intl, Date, Number, Math, URLSearchParams, Node: class {},
    document: { addEventListener() {}, getElementById(id) {
      return elements[id] ||= { textContent: "", style: {} };
    } } };
  vm.createContext(context);
  vm.runInContext(fs.readFileSync("dashboard/app.js", "utf8"), context);
  return { elements, run: (source) => vm.runInContext(source, context) };
}

test("missing durations are excluded and an empty set is not zero", () => {
  const { elements, run } = dashboard();
  run('renderLatency([{protokol:"HTTP",local_write_ms:null},{protokol:"HTTP"}])');
  assert.equal(elements["latency-count"].textContent, "0");
  assert.equal(elements["latency-p50"].textContent, "—");
  assert.equal(elements["http-latency"].textContent, "—");
});

test("a measured zero is retained while null and strings are excluded", () => {
  const { elements, run } = dashboard();
  run('renderLatency([{protokol:"HTTP",local_write_ms:null},{protokol:"HTTP",local_write_ms:0},{protokol:"HTTP",local_write_ms:10},{protokol:"HTTP",local_write_ms:"20"}])');
  assert.equal(elements["latency-count"].textContent, "2");
  assert.equal(elements["latency-p50"].textContent, "5.00");
  assert.equal(elements["latency-p95"].textContent, "9.50");
});

test("RTT aggregates only successful measurements and counts reported failures", () => {
  const { elements, run } = dashboard();
  run(`state.snapshot={attempt_totals:{total:4}, attempts:[
    {protokol:"MQTT",source_mode:"dht22",status:"ok",rtt_ms:10,waktu_dilaporkan:new Date().toISOString()},
    {protokol:"MQTT",source_mode:"dht22",status:"ok",rtt_ms:30,waktu_dilaporkan:new Date().toISOString()},
    {protokol:"MQTT",source_mode:"dht22",status:"timeout",rtt_ms:null,waktu_dilaporkan:new Date().toISOString()},
    {protokol:"HTTP",source_mode:"demo",status:"error",rtt_ms:null,waktu_dilaporkan:new Date().toISOString()}]}; renderRtt();`);
  assert.equal(elements["rtt-mqtt-count"].textContent, "2");
  assert.equal(elements["rtt-mqtt-p50"].textContent, "20.00");
  assert.equal(elements["rtt-mqtt-timeout"].textContent, "1");
  assert.equal(elements["rtt-http-p50"].textContent, "—");
  assert.equal(elements["rtt-http-error"].textContent, "1");
  assert.match(elements["rtt-source-note"].textContent, /tercampur/);
});

test("CSV exports the active time/protocol selection and includes provenance", () => {
  const { run } = dashboard();
  const result = run(`state.protocol="HTTP"; state.range="15";
    state.snapshot={readings:[
      {protokol:"HTTP",seq:3,waktu_diterima:new Date().toISOString()},
      {protokol:"MQTT",seq:2,waktu_diterima:new Date().toISOString()},
      {protokol:"HTTP",seq:1,waktu_diterima:new Date(Date.now()-3600000).toISOString()}]};
    let exported; downloadCsv=(rows,columns,name)=>{exported={rows,columns,name};};
    exportCsv(); JSON.stringify(exported);`);
  const data = JSON.parse(result);
  assert.deepEqual(data.rows.map((row) => row.seq), [3]);
  assert.ok(data.columns.includes("session_id"));
  assert.ok(data.columns.includes("source_mode"));
  assert.ok(data.columns.includes("rtt_ms"));
});

test("server filters include both device and session", () => {
  const { run } = dashboard();
  const query = run('state.source="dht22";state.session="esp32-01|boot-01";queryFilters()');
  const params = new URLSearchParams(query);
  assert.equal(params.get("source_mode"), "dht22");
  assert.equal(params.get("device_id"), "esp32-01");
  assert.equal(params.get("session_id"), "boot-01");
});

test("all vertical axis labels stay inside their chart", () => {
  for (const [name, count] of [["renderTrend", 10], ["renderThroughput", 5], ["renderScatter", 10]]) {
    const { run } = dashboard();
    const result = JSON.parse(run(`let positions=[];
      label=(_ctx,text,x,y,align)=>positions.push({text,y,align});
      canvas=()=>({node:{},width:600,height:300,ctx:new Proxy({}, {
        get:(_target,key)=>key==="createLinearGradient"?()=>({addColorStop(){}}):()=>{},set:()=>true})});
      ${name}([{suhu:27,kelembapan:60,protokol:"MQTT",waktu_diterima:new Date(Date.now()-2000).toISOString()},
               {suhu:28,kelembapan:61,protokol:"HTTP",waktu_diterima:new Date().toISOString()}]);
      JSON.stringify(positions.slice(0,${count}));`));
    const labels = name === "renderScatter" ? result.filter((_item, index) => index % 2 === 0) : result;
    assert.ok(labels.every((item) => item.y >= 10 && item.y <= 275), `${name}: labels fall outside chart`);
  }
});
