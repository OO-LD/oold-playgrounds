import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8899";
const ARTIFACTS = new globalThis.URL("../artifacts/", import.meta.url).pathname.replace(/^\//, "");
mkdirSync(ARTIFACTS, { recursive: true });

const mb = (n) => (n / 1024 / 1024).toFixed(2) + " MB";
const stats = async (reset = false) =>
  (await fetch(`${BASE}/__stats${reset ? "?reset" : ""}`)).json();

const out = {};
const results = [];
let failures = 0;
function check(name, ok, detail) {
  results.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` :: ${detail}` : ""}`);
}
function section(t) {
  console.log(`\n${"=".repeat(72)}\n${t}\n${"=".repeat(72)}`);
}

// ------------------------------------------------- cold shell + kernel boot
section("Cold load: shell, kernel, total bytes off the wire");
await stats(true);
const browser = await chromium.launch();
const ctx = await browser.newContext();
const external = { bytes: 0, requests: 0, hosts: {} };
ctx.on("response", async (r) => {
  const u = r.url();
  if (u.startsWith(BASE) || u.startsWith("data:") || u.startsWith("blob:")) return;
  external.requests += 1;
  const host = new globalThis.URL(u).host;
  const len = parseInt(r.headers()["content-length"] ?? "0", 10) || 0;
  external.bytes += len;
  external.hosts[host] = (external.hosts[host] ?? 0) + len;
});
const page = await ctx.newPage();

const t0 = Date.now();
await page.goto(`${BASE}/notebooks/index.html?path=oold_demo.ipynb`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".jp-Notebook", { timeout: 180000 });
out.shellMs = Date.now() - t0;
const afterShell = await stats();
out.shellBytes = afterShell.bytes;

await page.evaluate(async () => {
  const panel = window.jupyterapp.shell.currentWidget;
  await panel.sessionContext.ready;
  await panel.sessionContext.session.kernel.info;
});
// First real execution forces the interpreter to be fully live.
await page.evaluate(async () => {
  const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  await k.requestExecute({ code: "1+1" }).done;
});
out.kernelMs = Date.now() - t0;
const afterKernel = await stats();
out.kernelBytes = afterKernel.bytes;
out.kernelRequests = afterKernel.requests;

console.log(`shell rendered      : ${out.shellMs} ms, ${mb(out.shellBytes)} served`);
console.log(`kernel ready + 1+1  : ${out.kernelMs} ms, ${mb(out.kernelBytes)} served (${out.kernelRequests} requests)`);
check("cold shell under 15 s", out.shellMs < 15000, `${out.shellMs} ms`);
check("cold kernel under 60 s", out.kernelMs < 60000, `${out.kernelMs} ms`);

// ------------------------------------------ full notebook: bytes and timing
section("Full notebook run: incremental bytes for the pip installs");
const tRun = Date.now();
await page.evaluate(() => window.jupyterapp.commands.execute("notebook:run-all-cells"));
let state;
let lastDone = -1;
let stalls = 0;
while (true) {
  state = await page.evaluate(() => {
    const cells = window.jupyterapp.shell.currentWidget.content.model.cells;
    let total = 0, done = 0, errored = 0;
    for (let i = 0; i < cells.length; i++) {
      const c = cells.get(i);
      if (c.type !== "code") continue;
      total++;
      if (c.executionCount != null) done++;
      if ((c.outputs?.toJSON() ?? []).some((o) => o.output_type === "error")) errored++;
    }
    const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
    return { total, done, errored, kernelStatus: k.status };
  });
  if (state.done >= state.total && state.kernelStatus === "idle") break;
  if (state.kernelStatus === "idle" && state.done === lastDone) stalls++;
  else stalls = 0;
  lastDone = state.done;
  if (stalls >= 5 || Date.now() - tRun > 900000) break;
  await page.waitForTimeout(3000);
}
out.runMs = Date.now() - tRun;
const afterRun = await stats();
out.totalBytes = afterRun.bytes;
out.totalRequests = afterRun.requests;
console.log(`run-all              : ${Math.round(out.runMs / 1000)} s, cells ${state.done}/${state.total}, errors ${state.errored}`);
out.externalBytes = external.bytes;
out.externalHosts = external.hosts;
console.log(`bytes from this host : ${mb(out.totalBytes)} over ${out.totalRequests} requests`);
console.log(`bytes from elsewhere : ${mb(external.bytes)} over ${external.requests} requests`);
for (const [h, b] of Object.entries(external.hosts).sort((a, b) => b[1] - a[1])) {
  console.log(`   ${h.padEnd(28)} ${mb(b)}`);
}
console.log(`grand total          : ${mb(out.totalBytes + external.bytes)}`);
check("notebook ran to completion", state.done === state.total && state.errored === 0, JSON.stringify(state));

// --------------------------------------------------------------- anywidget
section("anywidget rendering under the pyodide kernel");
const aw = await page.evaluate(async () => {
  const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  const future = k.requestExecute({ code: "probe" });
  const mimes = [];
  future.onIOPub = (msg) => {
    if (msg.header.msg_type === "execute_result" || msg.header.msg_type === "display_data") {
      mimes.push(Object.keys(msg.content.data));
    }
  };
  await future.done;
  return mimes;
});
console.log(`mime bundles offered for the widget: ${JSON.stringify(aw)}`);
const hasWidgetMime = aw.flat().some((m) => m.includes("widget"));
check(
  "kernel emits an ipywidgets mime bundle for an anywidget instance",
  hasWidgetMime,
  hasWidgetMime ? "" : `only ${JSON.stringify(aw.flat())} -- no widget manager in this build`
);
// Scope to output areas. The cell source contains the same string inside the
// _esm literal, so a whole-page search would match the editor and pass anyway.
const widgetDom = await page.evaluate(() => {
  const outs = [...document.querySelectorAll(".jp-OutputArea")];
  const texts = outs.map((o) => o.innerText).filter((t) => t.includes("anywidget alive"));
  return {
    outputAreas: outs.length,
    hit: texts.length > 0,
    sample: texts[0]?.slice(0, 120) ?? null,
    widgetNodes: document.querySelectorAll(".jp-OutputArea .lm-Widget.jupyter-widgets, .jp-OutputArea .widget-subarea").length,
  };
});
console.log(`output areas=${widgetDom.outputAreas} widgetNodes=${widgetDom.widgetNodes} rendered=${widgetDom.hit}`);
console.log(`rendered text: ${JSON.stringify(widgetDom.sample)}`);
check(
  "anywidget renders its ESM view into a notebook output area",
  widgetDom.hit,
  widgetDom.hit ? widgetDom.sample : "no output area contains the ESM-rendered text; only the plain repr"
);

writeFileSync(`${ARTIFACTS}/coldload-report.json`, JSON.stringify({ out, results }, null, 2));
section("SUMMARY");
for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}`);
console.log(`\n${results.length - failures}/${results.length} passed, ${failures} failed`);
await browser.close();
