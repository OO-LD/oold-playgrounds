import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8902";
const NB = process.env.NB ?? "start.ipynb";
const PAGE_URL = `${BASE}/lab/index.html?path=${NB}`;
const HEADLESS = process.env.HEADED !== "1";
const KERNEL_TIMEOUT = Number(process.env.KERNEL_TIMEOUT ?? 180000);
const EXEC_TIMEOUT = Number(process.env.EXEC_TIMEOUT ?? 300000);
const SUITE_TIMEOUT = Number(process.env.SUITE_TIMEOUT ?? 1800000);
const ARTIFACTS = new globalThis.URL("../artifacts/", import.meta.url).pathname.replace(/^\//, "");

mkdirSync(ARTIFACTS, { recursive: true });

const report = { url: PAGE_URL, tests: [], timings: {}, ai: {}, kernel: {} };
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

function finish(code) {
  writeFileSync(`${ARTIFACTS}/report.json`, JSON.stringify(report, null, 2));
  section("SUMMARY");
  for (const t of report.tests) console.log(`${t.ok ? "PASS" : "FAIL"}  ${t.name}`);
  console.log(`\n${report.tests.length - failures}/${report.tests.length} passed, ${failures} failed`);
  process.exit(code);
}

// A kernel that never boots used to park the run forever, because
// page.evaluate honours no timeout and sessionContext.ready never settles.
const watchdog = setTimeout(() => {
  check("suite completed within SUITE_TIMEOUT", false, `${SUITE_TIMEOUT} ms elapsed`);
  finish(1);
}, SUITE_TIMEOUT);
watchdog.unref();

// A traceback echoes the failing source line, so a substring search over all
// output matches the print() call itself and passes while the cell raised.
// Markers are matched line-exact against stream output only.
const marker = (out, line) => out.stdout.split("\n").some((l) => l.trim() === line);

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await context.newPage();

const consoleErrors = [];
const pageErrors = [];
const failedRequests = [];
const badStatuses = [];

// jupyter-mcp-manager arrives as a dependency of jupyterlite-ai and polls a
// server REST route. A static site has no such route, so its 404 is expected.
const EXPECTED_404 = [/\/jupyter-mcp-manager\/servers/];

page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));
page.on("requestfailed", (r) => failedRequests.push(`${r.url()} :: ${r.failure()?.errorText}`));
page.on("response", (r) => {
  if (r.status() >= 400) badStatuses.push(`${r.status()} ${r.url()}`);
});

// ---------------------------------------------------------------- 1. load
section("TEST 1: lab app loads over plain HTTP");
const t0 = Date.now();
const resp = await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
check("HTTP 200 for lab app", resp.status() === 200, `status=${resp.status()}`);

await page.waitForSelector("#main", { timeout: 180000 });
const tShell = Date.now() - t0;
report.timings.labShellMs = tShell;
console.log(`lab shell rendered after ${tShell} ms`);
check("lab shell rendered", true, `#main after ${tShell} ms`);

await page.waitForSelector(".jp-Notebook", { timeout: 180000 });
const tNotebook = Date.now() - t0;
report.timings.notebookMs = tNotebook;
console.log(`notebook widget rendered after ${tNotebook} ms`);
check("notebook widget rendered", true, `.jp-Notebook after ${tNotebook} ms`);

// A truncated build still renders an empty .jp-Notebook, so the selector alone
// is not evidence that the requested file was opened.
const openedPath = await page.evaluate(() => window.jupyterapp?.shell?.currentWidget?.context?.path ?? null);
report.openedPath = openedPath;
check(`?path=${NB} opened the requested notebook`, openedPath === NB, `currentWidget.context.path=${openedPath}`);

check("no uncaught page errors during load", pageErrors.length === 0, JSON.stringify(pageErrors));
check("no failed requests during load", failedRequests.length === 0, JSON.stringify(failedRequests.slice(0, 5)));

// ------------------------------------------------------- 2. kernel starts
section("TEST 2: pyodide kernel starts and reaches idle");
const kernelInfo = await page.evaluate(async (timeoutMs) => {
  const deadline = Date.now() + timeoutMs;
  const byDeadline = (p, what) =>
    Promise.race([
      p,
      new Promise((_, rej) =>
        setTimeout(() => rej(new Error(`${what} did not settle`)), Math.max(0, deadline - Date.now()))
      ),
    ]);
  const panel = window.jupyterapp.shell.currentWidget;
  try {
    await byDeadline(panel.sessionContext.ready, "sessionContext.ready");
  } catch (e) {
    return { error: e.message };
  }
  const k = panel.sessionContext.session?.kernel;
  if (!k) return { error: "session has no kernel" };
  let info;
  try {
    info = await byDeadline(k.info, "kernel.info");
  } catch (e) {
    return { error: e.message, name: k.name, status: k.status };
  }
  // Reaching idle is the assertion. Startup can still be busy right after
  // kernel_info_reply, so poll for the transition instead of sampling once.
  while (k.status !== "idle" && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 250));
  }
  return {
    name: k.name,
    status: k.status,
    implementation: info.implementation,
    language: info.language_info?.name,
    pyVersion: info.language_info?.version,
  };
}, KERNEL_TIMEOUT);
const tKernel = Date.now() - t0;
report.timings.kernelReadyMs = tKernel;
report.kernel = kernelInfo;
console.log(JSON.stringify(kernelInfo, null, 2));
console.log(`kernel ready after ${tKernel} ms (cold, empty browser cache)`);
check("kernel became ready", !kernelInfo.error, kernelInfo.error ?? `${tKernel} ms`);
check("kernel is the pyodide kernel", kernelInfo.name === "python", `name=${kernelInfo.name}`);
check("kernel status idle", kernelInfo.status === "idle", `status=${kernelInfo.status}`);
if (kernelInfo.error) {
  console.log("kernel never came up, the rest of the suite cannot run");
  await page.screenshot({ path: `${ARTIFACTS}/lab-no-kernel.png` });
  await browser.close();
  finish(1);
}

// Kernel driver shared by every suite in this playground. window.__exec(code)
// runs code through the real kernel protocol and splits IOPub into stdout,
// results and errors, so assertions can target stream output alone. The
// deadline matters: a dead kernel leaves future.done pending forever.
await page.evaluate((defaultTimeout) => {
  window.__kernel = () => window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  window.__exec = async (code, timeoutMs) => {
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
    const timedOut = Symbol("timeout");
    const reply = await Promise.race([
      future.done,
      new Promise((r) => setTimeout(() => r(timedOut), timeoutMs ?? defaultTimeout)),
    ]);
    out.status = reply === timedOut ? "timeout" : reply.content.status;
    return out;
  };
}, EXEC_TIMEOUT);

const sanity = await page.evaluate(() => window.__exec("print('KERNEL EXEC OK')"));
check("window.__exec drives the kernel", marker(sanity, "KERNEL EXEC OK"), `status=${sanity.status} ${sanity.errors.join(" ")}`);

// ------------------------------------------------------------- 3. oold
section("TEST 3: oold wheel installs and imports in the kernel");
const tOold = Date.now();
const oold = await page.evaluate(() =>
  window.__exec(`
import os, sys, importlib.metadata

# The binding is selected at import time, so it has to be set before anything
# imports oold.model.
os.environ.setdefault("OOLD_DESCRIPTOR_BINDING", "1")

import piplite

# Pyodide ships typing-extensions 4.11.0 and micropip refuses to resolve a
# >=4.14.0 requirement against an already-installed older version, so it goes
# first and the stale module object is dropped.
await piplite.install("typing-extensions>=4.14.0")
sys.modules.pop("typing_extensions", None)
import typing_extensions

await piplite.install("oold", keep_going=True)
import oold

print("typing-extensions", importlib.metadata.version("typing_extensions"))
print("OOLD VERSION " + oold.__version__)
print("OOLD FILE " + str(oold.__file__))
print("OOLD IMPORT OK")
`)
);
console.log(oold.stdout);
if (oold.errors.length) console.log(oold.errors.join("\n").slice(0, 2000));
report.oold = { stdout: oold.stdout, errors: oold.errors, status: oold.status, ms: Date.now() - tOold };

const oooldVersion = (oold.stdout.split("\n").find((l) => l.trim().startsWith("OOLD VERSION ")) ?? "")
  .trim()
  .replace("OOLD VERSION ", "");
check("import oold succeeds in the kernel", marker(oold, "OOLD IMPORT OK"), `status=${oold.status}`);
check(
  "oold reports a version",
  /^\d+\.\d+\.\d+/.test(oooldVersion),
  `version=${oooldVersion || "<none>"}`
);

// ------------------------------------------- 4. jupyterlite-ai is present
section("TEST 4: jupyterlite-ai extension is in the built site");
const ai = await page.evaluate(() => {
  const app = window.jupyterapp;
  const ids = app.listPlugins ? app.listPlugins() : [];
  const aiPlugins = ids.filter((id) => id.startsWith("@jupyterlite/ai:"));
  const activated = aiPlugins.filter((id) => {
    try {
      return app.isPluginActivated(id);
    } catch {
      return false;
    }
  });
  return { total: ids.length, aiPlugins, activated };
});
report.ai = ai;
console.log(`plugins registered: ${ai.total}`);
console.log(`@jupyterlite/ai plugins: ${JSON.stringify(ai.aiPlugins, null, 2)}`);
console.log(`activated: ${JSON.stringify(ai.activated)}`);
check(
  "jupyterlite-ai plugins registered in window.jupyterapp",
  ai.aiPlugins.length > 0,
  `${ai.aiPlugins.length} plugins, e.g. ${ai.aiPlugins.slice(0, 3).join(", ")}`
);
check(
  "jupyterlite-ai chat plugin registered",
  ai.aiPlugins.includes("@jupyterlite/ai:chat"),
  ai.aiPlugins.includes("@jupyterlite/ai:chat") ? "" : `have: ${ai.aiPlugins.join(", ")}`
);
check(
  "at least one jupyterlite-ai plugin activated",
  ai.activated.length > 0,
  `activated=${JSON.stringify(ai.activated)}`
);

// Relative URL on purpose: the site has to work from a subpath, which is what
// GitHub Pages serves.
const aiSettings = await page.evaluate(async () => {
  const resp = await fetch("../build/schemas/all_federated.json", { headers: { Accept: "application/json" } });
  if (!resp.ok) return { ok: false, status: resp.status };
  const data = await resp.json();
  const entry = data.find((s) => s.id === "@jupyterlite/ai:chat");
  return {
    ok: true,
    ids: data.map((s) => s.id),
    found: !!entry,
    title: entry?.schema?.title,
    properties: entry ? Object.keys(entry.schema?.properties ?? {}) : [],
  };
});
report.ai.settings = aiSettings;
console.log(`federated settings schemas: ${JSON.stringify(aiSettings.ids)}`);
console.log(`@jupyterlite/ai:chat title=${aiSettings.title} properties=${JSON.stringify(aiSettings.properties)}`);
check(
  "jupyterlite-ai settings schema served by the site",
  aiSettings.ok === true && aiSettings.found === true && aiSettings.title === "AI Chat",
  `ok=${aiSettings.ok} found=${aiSettings.found} title=${aiSettings.title}`
);

// ------------------------------------------------- 5. notebook executes
section("TEST 5: placeholder notebook runs");
await page.evaluate(() => window.jupyterapp.commands.execute("notebook:run-all-cells"));
const RUN_TIMEOUT = 5 * 60 * 1000;
const pollStart = Date.now();
let runState;
while (true) {
  runState = await page.evaluate(() => {
    const cells = window.jupyterapp.shell.currentWidget.content.model.cells;
    let total = 0;
    let done = 0;
    let errored = 0;
    for (let i = 0; i < cells.length; i++) {
      const c = cells.get(i);
      if (c.type !== "code") continue;
      total += 1;
      if (c.executionCount !== null && c.executionCount !== undefined) done += 1;
      const outs = c.outputs ? c.outputs.toJSON() : [];
      if (outs.some((o) => o.output_type === "error")) errored += 1;
    }
    const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
    return { total, done, errored, kernelStatus: k.status };
  });
  if (runState.done >= runState.total && runState.kernelStatus === "idle") break;
  if (Date.now() - pollStart > RUN_TIMEOUT) break;
  await page.waitForTimeout(1000);
}
console.log(`run-all: ${JSON.stringify(runState)}`);

// Stream output only, line-exact. See the marker helper above.
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
console.log("--- notebook stream output ---");
console.log(streamText);
report.notebookStream = streamText;
check(
  "notebook reached its marker",
  streamText.split("\n").some((l) => l.trim() === "AI PLAYGROUND READY"),
  `cells ${runState.done}/${runState.total}, errored=${runState.errored}`
);
check("no notebook cell raised", runState.errored === 0, `errored=${runState.errored}`);

// --------------------------------------------- 6. contents drive payload
section("TEST 6: contents drive carries the placeholder files");
const drive = await page.evaluate(() =>
  window.__exec(`
import os
print("CWD " + os.getcwd())
for _n in ("start.ipynb", "sample.csv"):
    print(("DRIVE OK " if os.path.exists(_n) else "DRIVE MISSING ") + _n)
`)
);
console.log(drive.stdout);
check(
  "sample.csv is on the contents drive",
  marker(drive, "DRIVE OK sample.csv"),
  drive.stdout.trim().split("\n").join(" | ")
);

// ------------------------------------------------------------ diagnostics
section("CONSOLE DIAGNOSTICS");
console.log(`console.error count: ${consoleErrors.length}`);
consoleErrors.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`failed requests: ${failedRequests.length}`);
failedRequests.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`HTTP >= 400 responses: ${badStatuses.length}`);
badStatuses.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
report.consoleErrors = consoleErrors;
report.pageErrors = pageErrors;
report.failedRequests = failedRequests;
report.badStatuses = badStatuses;

check("no uncaught page errors over the whole run", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 5)));
check("no failed requests over the whole run", failedRequests.length === 0, JSON.stringify(failedRequests.slice(0, 5)));

// A wrong relative URL shows up here as a 404 on a static asset, which is the
// failure mode when the site is served from a subpath.
const unexpected = badStatuses.filter((s) => !EXPECTED_404.some((re) => re.test(s)));
check("no unexpected HTTP error responses", unexpected.length === 0, JSON.stringify(unexpected.slice(0, 5)));

await page.screenshot({ path: `${ARTIFACTS}/lab-smoke.png`, fullPage: false });
await browser.close();
finish(failures === 0 ? 0 : 1);
