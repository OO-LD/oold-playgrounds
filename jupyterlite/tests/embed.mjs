import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8899";
const ARTIFACTS = new globalThis.URL("../artifacts/", import.meta.url).pathname.replace(/^\//, "");
mkdirSync(ARTIFACTS, { recursive: true });

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

const browser = await chromium.launch();
const context = await browser.newContext({ viewport: { width: 1280, height: 800 } });

async function probe(label, url, opts = {}) {
  const page = await context.newPage();
  const errs = [];
  page.on("pageerror", (e) => errs.push(`${e.name}: ${e.message}`));
  const t0 = Date.now();
  let status = null;
  try {
    const r = await page.goto(url, { waitUntil: "domcontentloaded", timeout: 120000 });
    status = r?.status();
  } catch (e) {
    return { label, url, error: e.message.split("\n")[0], page };
  }
  const out = { label, url, status, errs, page, ms: Date.now() - t0 };
  if (opts.waitFor) {
    try {
      await page.waitForSelector(opts.waitFor, { timeout: opts.timeout ?? 120000 });
      out.rendered = true;
    } catch (e) {
      out.rendered = false;
      out.error = e.message.split("\n")[0];
    }
  }
  return out;
}

// ------------------------------------------------------------- repl params
section("REPL app URL parameters");

const replCode = encodeURIComponent('print("hello from a prefilled repl")');
const replUrl = `${BASE}/repl/index.html?kernel=python&code=${replCode}&toolbar=1`;
const repl = await probe("repl prefilled", replUrl, { waitFor: ".jp-CodeConsole" });
console.log(`GET ${replUrl}\n  status=${repl.status} rendered=${repl.rendered} ${repl.error ?? ""}`);
check("repl app served at /repl/index.html", repl.status === 200, `status=${repl.status}`);
check("repl console renders", repl.rendered === true, repl.error ?? "");

if (repl.rendered) {
  const p = repl.page;
  await p.waitForTimeout(4000);
  const consoleText = await p.evaluate(() => document.body.innerText);
  const promptText = await p.evaluate(
    () => document.querySelector(".jp-CodeConsole-promptCell .cm-content")?.textContent ?? "<no prompt cell>"
  );
  console.log(`  prompt cell node : ${JSON.stringify(promptText)}`);
  console.log(`  console contains the prefilled code: ${consoleText.includes("hello from a prefilled repl")}`);
  check(
    "?code= places the code in the repl console",
    consoleText.includes("hello from a prefilled repl"),
    `prompt=${JSON.stringify(promptText)} bodyLen=${consoleText.length}`
  );
  const toolbar = await p.locator(".jp-Toolbar, .jp-NotebookPanel-toolbar").count();
  console.log(`  toolbar elements found: ${toolbar}`);
  check("?toolbar=1 renders a toolbar", toolbar > 0, `count=${toolbar}`);
  await p.screenshot({ path: `${ARTIFACTS}/embed-repl.png` });

  // ?execute=1 should run the prefilled code without user interaction
  const execUrl = `${BASE}/repl/index.html?kernel=python&toolbar=1&execute=1&code=${encodeURIComponent('print(6*7)')}`;
  const ex = await probe("repl execute", execUrl, { waitFor: ".jp-CodeConsole" });
  let executed = false;
  if (ex.rendered) {
    try {
      await ex.page.waitForFunction(() => document.body.innerText.includes("42"), { timeout: 180000 });
      executed = true;
    } catch {}
    await ex.page.screenshot({ path: `${ARTIFACTS}/embed-repl-execute.png` });
  }
  console.log(`GET ${execUrl}\n  auto-executed=${executed}`);
  check("?execute=1 auto-runs prefilled code", executed, executed ? "" : "output 42 never appeared");
  await ex.page?.close();
}
await repl.page?.close();

// ------------------------------------------------------- notebooks ?path=
section("Notebooks app ?path= parameter");
const nbUrl = `${BASE}/notebooks/index.html?path=oold_demo.ipynb`;
const nb = await probe("notebooks path", nbUrl, { waitFor: ".jp-Notebook" });
console.log(`GET ${nbUrl}\n  status=${nb.status} rendered=${nb.rendered}`);
check("?path= opens the named notebook", nb.rendered === true, nb.error ?? "");
if (nb.rendered) {
  const title = await nb.page.evaluate(() => window.jupyterapp?.shell?.currentWidget?.title?.label ?? null);
  console.log(`  opened document: ${title}`);
  check("opened document is oold_demo.ipynb", title === "oold_demo.ipynb", `title=${title}`);
}
await nb.page?.close();

const badUrl = `${BASE}/notebooks/index.html?path=does-not-exist.ipynb`;
const bad = await probe("notebooks bad path", badUrl, { waitFor: ".jp-Notebook", timeout: 60000 });
let badTitle = null;
if (bad.rendered) {
  badTitle = await bad.page.evaluate(() => window.jupyterapp?.shell?.currentWidget?.title?.label ?? null);
}
console.log(`GET ${badUrl}\n  rendered=${bad.rendered} title=${badTitle}`);
check(
  "unknown ?path= yields an empty notebook of that name rather than an error",
  bad.rendered === true && badTitle === "does-not-exist.ipynb",
  `rendered=${bad.rendered} title=${badTitle}`
);
await bad.page?.close();

// ------------------------------------------------------------- iframe test
section("Cross-origin iframe embedding");
const HOST = process.env.HOST ?? "http://127.0.0.1:8900";
const framePage = await context.newPage();
const frameErrs = [];
framePage.on("pageerror", (e) => frameErrs.push(`${e.name}: ${e.message}`));
let frameOk = false;
let frameConsoleText = "";
const t0Frame = Date.now();
try {
  await framePage.goto(`${HOST}/index.html`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await framePage.frameLocator("#lite-repl").locator(".jp-CodeConsole").waitFor({ timeout: 180000 });
  frameOk = true;
  await framePage.waitForTimeout(6000);
  const f = framePage.frames().find((fr) => fr.url().includes("/repl/"));
  frameConsoleText = f ? await f.evaluate(() => document.body.innerText) : "";
} catch (e) {
  console.log("  repl iframe error:", e.message.split("\n")[0]);
}
console.log(`  repl iframe rendered = ${frameOk} after ${Date.now() - t0Frame} ms`);
console.log(`  repl iframe contains prefilled code = ${frameConsoleText.includes("inside an iframe")}`);
check("repl renders inside a cross-origin iframe", frameOk, frameErrs.join(" | "));
check("?code= prefill survives iframe embedding", frameConsoleText.includes("inside an iframe"), `console text len=${frameConsoleText.length}`);
await framePage.screenshot({ path: `${ARTIFACTS}/embed-iframe-repl.png`, fullPage: false });
await framePage.close();

// A second JupyterLite instance in a separate host page. Two of them on one
// page contend for the same service worker registration.
const nbFramePage = await context.newPage();
const nbFrameErrs = [];
nbFramePage.on("pageerror", (e) => nbFrameErrs.push(`${e.name}: ${e.message}`));
let nbFrameOk = false;
let nbKernelOk = false;
const t1Frame = Date.now();
try {
  await nbFramePage.goto(`${HOST}/notebook.html`, { waitUntil: "domcontentloaded", timeout: 60000 });
  await nbFramePage.frameLocator("#lite-notebook").locator(".jp-Notebook").waitFor({ timeout: 180000 });
  nbFrameOk = true;
  const f = nbFramePage.frames().find((fr) => fr.url().includes("/notebooks/"));
  nbKernelOk = await f.evaluate(async () => {
    const panel = window.jupyterapp.shell.currentWidget;
    await panel.sessionContext.ready;
    const reply = await panel.sessionContext.session.kernel.requestExecute({ code: "print(6*7)" }).done;
    return reply.content.status === "ok";
  });
} catch (e) {
  console.log("  notebook iframe error:", e.message.split("\n")[0]);
}
console.log(`  notebook iframe rendered = ${nbFrameOk} after ${Date.now() - t1Frame} ms, kernel executes = ${nbKernelOk}`);
check("notebook renders inside a cross-origin iframe", nbFrameOk, nbFrameErrs.join(" | "));
check("kernel executes inside a cross-origin iframe", nbKernelOk, nbFrameErrs.join(" | "));
check("no uncaught errors in either host page", frameErrs.length === 0 && nbFrameErrs.length === 0, [...frameErrs, ...nbFrameErrs].join(" | "));
await nbFramePage.screenshot({ path: `${ARTIFACTS}/embed-iframe-notebook.png`, fullPage: false });
await nbFramePage.close();

writeFileSync(`${ARTIFACTS}/embed-report.json`, JSON.stringify({ results }, null, 2));
section("SUMMARY");
for (const r of results) console.log(`${r.ok ? "PASS" : "FAIL"}  ${r.name}`);
console.log(`\n${results.length - failures}/${results.length} passed, ${failures} failed`);
await browser.close();
