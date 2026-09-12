import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8899";
const NB = process.env.NB ?? "oold_demo.ipynb";
const PAGE_URL = `${BASE}/notebooks/index.html?path=${NB}`;
const HEADLESS = process.env.HEADED !== "1";
const ARTIFACTS = new globalThis.URL("../artifacts/", import.meta.url).pathname.replace(/^\//, "");

mkdirSync(ARTIFACTS, { recursive: true });

const report = { url: PAGE_URL, tests: [], timings: {}, payload: {}, completion: {}, inspect: {} };
let failures = 0;

function check(name, ok, detail) {
  report.tests.push({ name, ok, detail });
  if (!ok) failures += 1;
  const tag = ok ? "PASS" : "FAIL";
  console.log(`[${tag}] ${name}${detail ? ` :: ${detail}` : ""}`);
}

function section(title) {
  console.log(`\n${"=".repeat(72)}\n${title}\n${"=".repeat(72)}`);
}

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await context.newPage();

const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];
let bytes = 0;
let requestCount = 0;

page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));
page.on("requestfailed", (r) => failedRequests.push(`${r.url()} :: ${r.failure()?.errorText}`));
page.on("response", async (r) => {
  requestCount += 1;
  try {
    const len = r.headers()["content-length"];
    if (len) bytes += parseInt(len, 10);
  } catch {}
});

// ---------------------------------------------------------------- 1. load
section("TEST 1: static site loads over plain HTTP");
const t0 = Date.now();
const resp = await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
check("HTTP 200 for notebooks app", resp.status() === 200, `status=${resp.status()}`);
await page.waitForSelector(".jp-Notebook", { timeout: 180000 });
const tNotebook = Date.now() - t0;
report.timings.notebookShellMs = tNotebook;
console.log(`notebook shell rendered after ${tNotebook} ms`);
check("no uncaught page errors during load", pageErrors.length === 0, JSON.stringify(pageErrors));
check("no failed requests during load", failedRequests.length === 0, JSON.stringify(failedRequests.slice(0, 5)));

// ------------------------------------------------------- 2. kernel starts
section("TEST 2: pyodide kernel starts and reaches idle");
await page.evaluate(async () => {
  const app = window.jupyterapp;
  const panel = app.shell.currentWidget;
  await panel.sessionContext.ready;
  await panel.sessionContext.session.kernel.info;
});
const kernelInfo = await page.evaluate(async () => {
  const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  const info = await k.info;
  // Reaching idle is the assertion. Startup can still be busy right after
  // kernel_info_reply, so wait for the transition instead of sampling it.
  const deadline = Date.now() + 120000;
  while (k.status !== "idle" && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 250));
  }
  return {
    name: k.name,
    status: k.status,
    implementation: info.implementation,
    language: info.language_info?.name,
    pyVersion: info.language_info?.version,
    banner: (info.banner ?? "").slice(0, 200),
  };
});
const tKernel = Date.now() - t0;
report.timings.kernelReadyMs = tKernel;
report.kernelInfo = kernelInfo;
console.log(JSON.stringify(kernelInfo, null, 2));
console.log(`kernel ready after ${tKernel} ms (cold, empty browser cache)`);
check("kernel is the pyodide kernel", kernelInfo.name === "python", `name=${kernelInfo.name}`);
check("kernel status idle", kernelInfo.status === "idle", `status=${kernelInfo.status}`);

// Helper for executing arbitrary code through the real kernel protocol.
await page.evaluate(() => {
  window.__kernel = () => window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  window.__exec = async (code) => {
    const future = window.__kernel().requestExecute({ code, stop_on_error: false });
    const out = { stdout: "", results: [], errors: [] };
    future.onIOPub = (msg) => {
      const t = msg.header.msg_type;
      if (t === "stream") out.stdout += msg.content.text;
      else if (t === "execute_result" || t === "display_data")
        out.results.push(msg.content.data["text/plain"] ?? "");
      else if (t === "error")
        out.errors.push(`${msg.content.ename}: ${msg.content.evalue}\n${(msg.content.traceback || []).join("\n")}`);
    };
    const reply = await future.done;
    out.status = reply.content.status;
    return out;
  };
});

// ------------------------------------------------------ 3. run the notebook
section("TEST 3: notebook executes end to end");
const tRunStart = Date.now();
await page.evaluate(() => window.jupyterapp.commands.execute("notebook:run-all-cells"));

const RUN_TIMEOUT = 20 * 60 * 1000;
let runState;
const pollStart = Date.now();
let lastDone = -1;
let idleStalls = 0;
while (true) {
  runState = await page.evaluate(() => {
    const cells = window.jupyterapp.shell.currentWidget.content.model.cells;
    let total = 0;
    let done = 0;
    let errored = 0;
    let lastRunning = -1;
    for (let i = 0; i < cells.length; i++) {
      const c = cells.get(i);
      if (c.type !== "code") continue;
      total += 1;
      if (c.executionCount !== null && c.executionCount !== undefined) {
        done += 1;
        lastRunning = i;
      }
      const outs = c.outputs ? c.outputs.toJSON() : [];
      if (outs.some((o) => o.output_type === "error")) errored += 1;
    }
    const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
    return { total, done, errored, lastRunning, kernelStatus: k.status };
  });
  if (runState.done >= runState.total && runState.kernelStatus === "idle") break;
  // run-all aborts at the first error, leaving later cells unexecuted forever.
  if (runState.kernelStatus === "idle" && runState.done === lastDone) idleStalls += 1;
  else idleStalls = 0;
  lastDone = runState.done;
  if (idleStalls >= 4) {
    console.log(`\n  run-all stalled at ${runState.done}/${runState.total} cells (kernel idle, ${runState.errored} errored)`);
    break;
  }
  if (Date.now() - pollStart > RUN_TIMEOUT) break;
  console.log(
    `  cells ${runState.done}/${runState.total}  errors=${runState.errored}  kernel=${runState.kernelStatus}  ${Math.round((Date.now() - tRunStart) / 1000)}s`
  );
  await page.waitForTimeout(3000);
}
console.log("");
const tRun = Date.now() - tRunStart;
report.timings.runAllMs = tRun;
console.log(`run-all finished in ${Math.round(tRun / 1000)} s :: ${JSON.stringify(runState)}`);

const cellOutputs = await page.evaluate(() => {
  const cells = window.jupyterapp.shell.currentWidget.content.model.cells;
  const res = [];
  for (let i = 0; i < cells.length; i++) {
    const c = cells.get(i);
    if (c.type !== "code") continue;
    const outs = c.outputs ? c.outputs.toJSON() : [];
    const text = outs
      .map((o) => {
        if (o.output_type === "stream") return o.text;
        if (o.output_type === "error") return `!! ${o.ename}: ${o.evalue}\n${(o.traceback || []).join("\n")}`;
        if (o.data) return o.data["text/plain"] ?? JSON.stringify(Object.keys(o.data));
        return "";
      })
      .join("");
    res.push({ index: i, executionCount: c.executionCount, source: c.sharedModel.getSource(), text });
  }
  return res;
});
report.cells = cellOutputs;

for (const c of cellOutputs) {
  const head = c.source.split("\n").find((l) => l.trim() && !l.trim().startsWith("#")) ?? "";
  console.log(`\n--- cell ${c.index} [${c.executionCount}] ${head.slice(0, 60)} ---`);
  console.log(c.text.length > 2600 ? c.text.slice(0, 2600) + "\n  ...[truncated]" : c.text);
}

// Markers must come from stream output. A traceback echoes the failing source
// line, so a substring search over everything would match the print() call
// itself and pass while the cell actually raised.
const streamText = await page.evaluate(() => {
  const cells = window.jupyterapp.shell.currentWidget.content.model.cells;
  let s = "";
  for (let i = 0; i < cells.length; i++) {
    const c = cells.get(i);
    if (c.type !== "code" || !c.outputs) continue;
    for (const o of c.outputs.toJSON()) if (o.output_type === "stream") s += o.text;
  }
  return s;
});
const marker = (m) => streamText.split("\n").some((l) => l.trim() === m);
const allText = cellOutputs.map((c) => c.text).join("\n");
check("notation_example self-checks passed", marker("ALL CHECKS PASSED"), "");
check("in-kernel completion selftest passed", marker("COMPLETION SELFTEST PASSED"), "");
check("notebook reached final cell", marker("NOTEBOOK COMPLETE"), "");
const errCells = cellOutputs.filter((c) => c.text.includes("!! "));
check(
  "no cell raised an uncaught exception",
  errCells.length === 0,
  errCells.map((c) => `cell ${c.index}: ${c.text.slice(0, 300)}`).join(" | ")
);

// --------------------------------------------- 4. per-package import result
section("TEST 4: per-package Pyodide compatibility");
const pkgProbe = await page.evaluate(async () => {
  return await window.__exec(`
import json, importlib, traceback
_names = {"oold": "oold", "rdflib": "rdflib", "pyld": "pyld", "sparqlwrapper": "SPARQLWrapper", "jsondiff": "jsondiff", "anywidget": "anywidget", "datamodel_code_generator": "datamodel_code_generator"}
_out = {}
for _p, _m in _names.items():
    try:
        _mod = importlib.import_module(_m)
        _out[_p] = {"import": "OK", "version": str(getattr(_mod, "__version__", "n/a")), "file": str(getattr(_mod, "__file__", "n/a"))}
    except Exception as _e:
        _out[_p] = {"import": "FAIL", "error": f"{type(_e).__name__}: {_e}"}
print(json.dumps(_out, indent=2))
`);
});
console.log(pkgProbe.stdout || JSON.stringify(pkgProbe.errors));
let pkgResults = {};
try {
  pkgResults = JSON.parse(pkgProbe.stdout);
} catch {}
report.packages = pkgResults;
for (const p of ["oold", "rdflib", "pyld", "sparqlwrapper", "jsondiff"]) {
  check(`import ${p}`, pkgResults[p]?.import === "OK", pkgResults[p]?.error ?? pkgResults[p]?.version ?? "no data");
}

// ------------------------------------- 5/6. completion via kernel protocol
section("TEST 5+6: complete_request over the kernel protocol");
const LINK = ["knows", "employer", "friends", "location"];
const GEN = ["serial_number", "manufacturer", "power_rating_watts", "calibration_tags"];
const SENSOR = ["sensor_uri", "reading_unit", "sample_rate_hz", "calibrated_by"];
const PLAIN = ["alpha", "beta", "gamma"];
// expect:true  -> the names must be offered
// expect:false -> they must not be, for the recorded reason
const completionProbes = [
  { label: "Person.", code: "Person.", want: LINK, expect: true, why: "oold installs link descriptors on the class" },
  { label: "alice.", code: "alice.", want: LINK, expect: true, why: "instance of an oold model" },
  { label: "PlainRuntime.", code: "PlainRuntime.", want: PLAIN, expect: true, why: "control: exec-created non-pydantic class" },
  { label: "plain.", code: "plain.", want: PLAIN, expect: true, why: "control: instance of exec-created class" },
  { label: "Instrument.", code: "Instrument.", want: GEN, expect: false, why: "pydantic v2 keeps fields in model_fields, not on the class" },
  { label: "instrument.", code: "instrument.", want: GEN, expect: true, why: "instance of the exec-generated model" },
  { label: "Sensor.", code: "Sensor.", want: SENSOR, expect: false, why: "pydantic v2 keeps fields in model_fields, not on the class" },
  { label: "sensor.", code: "sensor.", want: SENSOR, expect: true, why: "instance of the create_model class" },
  { label: "alice.emp", code: "alice.emp", want: ["employer"], expect: true, why: "prefix filtering" },
  { label: "instrument.ser", code: "instrument.ser", want: ["serial_number"], expect: true, why: "prefix on a runtime-generated field" },
  { label: "alice.employer.", code: "alice.employer.", want: ["name", "id", "type"], expect: true, why: "chained through a resolved oold link" },
];

// dir() vs completion: isolates pydantic field storage from IPython policy.
const dirDump = await page.evaluate(async () => {
  return await window.__exec(`
_L = ["knows", "employer", "friends", "location"]
_G = ["serial_number", "manufacturer", "power_rating_watts", "calibration_tags"]
_S = ["sensor_uri", "reading_unit", "sample_rate_hz", "calibrated_by"]
print("IPCompleter.use_jedi   :", get_ipython().Completer.use_jedi)
print("IPCompleter.evaluation :", get_ipython().Completer.evaluation)
print("Person.model_fields    :", sorted(Person.model_fields))
print("Instrument.model_fields:", sorted(Instrument.model_fields))
print("Sensor.model_fields    :", sorted(Sensor.model_fields))
print("dir(Person)     n Link :", [f for f in _L if f in dir(Person)])
print("dir(alice)      n Link :", [f for f in _L if f in dir(alice)])
print("dir(Instrument) n Gen  :", [f for f in _G if f in dir(Instrument)])
print("dir(instrument) n Gen  :", [f for f in _G if f in dir(instrument)])
print("dir(Sensor)     n Sen  :", [f for f in _S if f in dir(Sensor)])
print("dir(sensor)     n Sen  :", [f for f in _S if f in dir(sensor)])
print("Instrument.__module__  :", Instrument.__module__)
print("Instrument in user_ns  :", "Instrument" in get_ipython().user_ns)
print("dir(PlainRuntime)      :", [n for n in dir(PlainRuntime) if not n.startswith("_")])
print("pydantic BaseModel has __getattr__:", "__getattr__" in vars(__import__("pydantic").BaseModel))
`);
});
console.log("--- dir() / policy diagnostics ---");
console.log(dirDump.stdout || JSON.stringify(dirDump.errors));
report.dirDump = dirDump.stdout;

for (const probe of completionProbes) {
  const res = await page.evaluate(async ({ code }) => {
    const reply = await window.__kernel().requestComplete({ code, cursor_pos: code.length });
    return reply.content;
  }, probe);
  const matches = res.matches ?? [];
  const publicMatches = matches.filter((m) => !m.split(".").pop().startsWith("_"));
  report.completion[probe.label] = { status: res.status, count: matches.length, matches, publicMatches };
  console.log(`\n${probe.label}  (${matches.length} matches, status=${res.status})`);
  console.log(`  public: ${JSON.stringify(publicMatches)}`);
  const found = probe.want.filter((w) => matches.some((m) => m === w || m.endsWith("." + w)));
  if (probe.expect) {
    check(
      `complete_request "${probe.label}" offers ${probe.want.join(", ")}`,
      found.length === probe.want.length,
      found.length === probe.want.length ? probe.why : `missing: ${probe.want.filter((w) => !found.includes(w)).join(", ")}`
    );
  } else {
    check(
      `complete_request "${probe.label}" offers the pydantic API but not ${probe.want[0]}, ... (${probe.why})`,
      found.length === 0 && matches.some((m) => m.endsWith("model_validate")),
      `unexpectedly offered: ${found.join(", ")} | n=${matches.length}`
    );
  }
}

// ------------------------- 5c. evaluation policy and jedi matrix
section("TEST 5c: evaluation policy (limited vs unsafe) and jedi on/off");
const matrix = {};
for (const policy of ["limited", "unsafe"]) {
  for (const jedi of [true, false]) {
    const key = `${policy}/jedi=${jedi}`;
    await page.evaluate(
      async ({ policy, jedi }) =>
        window.__exec(
          `get_ipython().Completer.evaluation = ${JSON.stringify(policy)}\nget_ipython().Completer.use_jedi = ${jedi ? "True" : "False"}`
        ),
      { policy, jedi }
    );
    matrix[key] = {};
    for (const probe of completionProbes) {
      const res = await page.evaluate(async ({ code }) => {
        const reply = await window.__kernel().requestComplete({ code, cursor_pos: code.length });
        return reply.content.matches ?? [];
      }, probe);
      const hit = probe.want.filter((w) => res.some((m) => m === w || m.endsWith("." + w)));
      matrix[key][probe.label] = { total: res.length, hit, sample: res.filter((m) => !m.split(".").pop().startsWith("_")).slice(0, 8) };
    }
  }
}
// Restore what the kernel ships with.
await page.evaluate(async () =>
  window.__exec("get_ipython().Completer.evaluation = 'limited'\nget_ipython().Completer.use_jedi = True")
);
report.matrix = matrix;
for (const [key, probes] of Object.entries(matrix)) {
  console.log(`\n[${key}]`);
  for (const [label, r] of Object.entries(probes)) {
    console.log(`  ${label.padEnd(18)} n=${String(r.total).padStart(3)}  hit=${JSON.stringify(r.hit)}`);
  }
}
const chainedLimited = matrix["limited/jedi=true"]["alice.employer."];
const chainedUnsafe = matrix["unsafe/jedi=true"]["alice.employer."];
console.log(
  `\nchained "alice.employer." limited=${JSON.stringify(chainedLimited.hit)} unsafe=${JSON.stringify(chainedUnsafe.hit)}`
);

// -------------------------------- 5b. completion through the notebook UI
section("TEST 5b: completion through the real notebook UI (Tab key)");
async function uiComplete(text) {
  await page.evaluate(async () => {
    const app = window.jupyterapp;
    const panel = app.shell.currentWidget;
    const nb = panel.content;
    nb.activeCellIndex = nb.model.cells.length - 1;
    await app.commands.execute("notebook:insert-cell-below");
    nb.activeCellIndex = nb.model.cells.length - 1;
    nb.activate();
  });
  await page.waitForTimeout(500);
  const editor = page.locator(".jp-Notebook .jp-Cell").last().locator(".cm-content");
  await editor.click();
  await page.keyboard.type(text, { delay: 40 });
  await page.waitForTimeout(400);
  await page.keyboard.press("Tab");
  let items = [];
  try {
    await page.waitForSelector(".jp-Completer .jp-Completer-item", { timeout: 20000 });
    items = await page.locator(".jp-Completer .jp-Completer-item").allInnerTexts();
  } catch (e) {
    items = [`<no completer popup: ${e.message.split("\n")[0]}>`];
  }
  return items;
}

// Completer items render as "<kind>\n<name>\n<kind label>", so match per line.
const names = (items) => items.flatMap((i) => i.split("\n").map((s) => s.trim()));

const uiCases = [
  { label: "Person.", want: LINK, shot: "completer-oold-class.png", desc: "oold model class" },
  { label: "instrument.", want: GEN, shot: "completer-runtime-exec-instance.png", desc: "exec-generated model instance" },
  { label: "sensor.", want: SENSOR, shot: "completer-create-model-instance.png", desc: "create_model instance" },
  { label: "PlainRuntime.", want: PLAIN, shot: "completer-plain-runtime-class.png", desc: "exec-created plain class" },
];
for (const c of uiCases) {
  const items = await uiComplete(c.label);
  const flat = names(items);
  console.log(`UI "${c.label}" -> ${items.length} items :: hit ${JSON.stringify(flat.filter((n) => c.want.includes(n)))}`);
  report.completion[`UI:${c.label}`] = items;
  check(
    `UI Tab completion on ${c.desc} shows ${c.want.slice(0, 2).join(", ")}, ...`,
    c.want.every((w) => flat.includes(w)),
    `missing=${JSON.stringify(c.want.filter((w) => !flat.includes(w)))} items=${JSON.stringify(items.slice(0, 12))}`
  );
  await page.screenshot({ path: `${ARTIFACTS}/${c.shot}` });
  await page.keyboard.press("Escape");
}

// -------------------------------------------------------- 7. inspect_request
section("TEST 7: inspect_request");
const inspectProbes = [
  { label: "OoldField", code: "OoldField", cursor: 4 },
  { label: "Person", code: "Person", cursor: 3 },
  { label: "Instrument", code: "Instrument", cursor: 4 },
  { label: "alice.model_dump", code: "alice.model_dump", cursor: 12 },
];
for (const p of inspectProbes) {
  const res = await page.evaluate(async ({ code, cursor }) => {
    const reply = await window.__kernel().requestInspect({ code, cursor_pos: cursor, detail_level: 0 });
    const c = reply.content;
    return { found: c.found, status: c.status, text: c.data?.["text/plain"] ?? "" };
  }, p);
  report.inspect[p.label] = res;
  const clean = res.text.replace(/\[[0-9;]*m/g, "");
  console.log(`\n--- inspect "${p.label}" found=${res.found} len=${clean.length} ---`);
  console.log(clean.slice(0, 900));
  check(`inspect_request "${p.label}" returns content`, res.found === true && clean.length > 0, `found=${res.found} len=${clean.length}`);
}

// --------------------------------------------------------------- payload
section("PAYLOAD AND TIMING");
const perf = await page.evaluate(() => {
  const entries = performance.getEntriesByType("resource");
  let transfer = 0;
  let decoded = 0;
  const byType = {};
  for (const e of entries) {
    transfer += e.transferSize || 0;
    decoded += e.decodedBodySize || 0;
    const ext = (e.name.split("?")[0].split(".").pop() || "?").toLowerCase();
    byType[ext] = (byType[ext] || 0) + (e.transferSize || 0);
  }
  const nav = performance.getEntriesByType("navigation")[0];
  return {
    resourceCount: entries.length,
    transferBytes: transfer,
    decodedBytes: decoded,
    byType,
    domContentLoadedMs: nav ? Math.round(nav.domContentLoadedEventEnd) : null,
  };
});
report.payload = perf;
const mb = (n) => (n / 1024 / 1024).toFixed(2) + " MB";
console.log(`resources: ${perf.resourceCount}`);
console.log(`transferred: ${mb(perf.transferBytes)} (decoded ${mb(perf.decodedBytes)})`);
console.log(
  "by extension: " +
    Object.entries(perf.byType)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 8)
      .map(([k, v]) => `${k}=${mb(v)}`)
      .join(" ")
);
console.log(`notebook shell: ${report.timings.notebookShellMs} ms`);
console.log(`kernel ready:   ${report.timings.kernelReadyMs} ms`);
console.log(`run all cells:  ${report.timings.runAllMs} ms`);

// -------------------------------------------------------------- 8. shot
section("TEST 8: screenshot");
await page.evaluate(() => {
  const nb = window.jupyterapp.shell.currentWidget.content;
  nb.activeCellIndex = 0;
  nb.node.querySelector(".jp-WindowedPanel-outer").scrollTop = 0;
});
await page.waitForTimeout(1200);
await page.screenshot({ path: `${ARTIFACTS}/notebook-top.png`, fullPage: false });
await page.evaluate(() => {
  const cells = window.jupyterapp.shell.currentWidget.content;
  cells.activeCellIndex = 12;
});
await page.waitForTimeout(1200);
await page.screenshot({ path: `${ARTIFACTS}/notebook-codegen.png`, fullPage: false });
check("screenshots written", true, `${ARTIFACTS}`);

section("CONSOLE DIAGNOSTICS");
console.log(`console.error count: ${consoleErrors.length}`);
consoleErrors.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`failed requests: ${failedRequests.length}`);
failedRequests.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
report.consoleErrors = consoleErrors;
report.pageErrors = pageErrors;
report.failedRequests = failedRequests;

writeFileSync(`${ARTIFACTS}/report.json`, JSON.stringify(report, null, 2));

section("SUMMARY");
for (const t of report.tests) console.log(`${t.ok ? "PASS" : "FAIL"}  ${t.name}`);
console.log(`\n${report.tests.length - failures}/${report.tests.length} passed, ${failures} failed`);

await browser.close();
process.exit(failures === 0 ? 0 : 1);
