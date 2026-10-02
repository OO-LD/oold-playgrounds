// Proves the vendored piplite wheels are what a cold start uses, by taking PyPI away.
//
// A green kernel-api run is no evidence on its own: with the network up, micropip
// resolves the same names from files.pythonhosted.org and nothing looks different.
// Here every request to pypi.org and files.pythonhosted.org is aborted in the browser,
// so anything not carried by the site or by the Pyodide distribution cannot install.
//
// The control at the end installs a package that is in neither, and must fail. Without
// it a block that never reached micropip would look like a pass.

import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8902";
const NB = process.env.NB ?? "start.ipynb";
const PAGE_URL = `${BASE}/lab/index.html?path=${NB}`;
const HEADLESS = process.env.HEADED !== "1";
const KERNEL_TIMEOUT = Number(process.env.KERNEL_TIMEOUT ?? 180000);
const EXEC_TIMEOUT = Number(process.env.EXEC_TIMEOUT ?? 300000);
const SETUP_TIMEOUT = Number(process.env.SETUP_TIMEOUT ?? 600000);
const SUITE_TIMEOUT = Number(process.env.SUITE_TIMEOUT ?? 1800000);
const ARTIFACTS = new globalThis.URL("../artifacts/", import.meta.url).pathname.replace(/^\//, "");

mkdirSync(ARTIFACTS, { recursive: true });

const PYPI_HOSTS = /^https?:\/\/([a-z0-9-]+\.)*(pypi\.org|pythonhosted\.org)\//i;

const report = { url: PAGE_URL, tests: [], timings: {}, kernel: {}, cells: {}, blocked: [] };
let failures = 0;

function check(name, ok, detail) {
  report.tests.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` :: ${detail}` : ""}`);
}

function section(title) {
  console.log(`\n${"=".repeat(72)}\n${title}\n${"=".repeat(72)}`);
}

function finish(code) {
  writeFileSync(`${ARTIFACTS}/offline.json`, JSON.stringify(report, null, 2));
  section("SUMMARY");
  for (const t of report.tests) console.log(`${t.ok ? "PASS" : "FAIL"}  ${t.name}`);
  console.log(`\n${report.tests.length - failures}/${report.tests.length} passed, ${failures} failed`);
  process.exit(code);
}

const watchdog = setTimeout(() => {
  check("suite completed within SUITE_TIMEOUT", false, `${SUITE_TIMEOUT} ms elapsed`);
  finish(1);
}, SUITE_TIMEOUT);
watchdog.unref();

const lines = (out) => out.stdout.split("\n").map((l) => l.trimEnd());
const marker = (out, line) => lines(out).some((l) => l === line);

function cellOk(name, out) {
  const ok = out.status === "ok" && out.errors.length === 0;
  check(
    `${name}: cell executed without raising`,
    ok,
    ok ? `status=${out.status}` : `status=${out.status} ${out.errors.join(" ").slice(0, 600)}`
  );
  return ok;
}

function expectLine(name, out, line) {
  const ok = marker(out, line);
  check(`${name}: "${line}"`, ok, ok ? "" : `lines: ${JSON.stringify(lines(out).slice(0, 12))}`);
}

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });

// Routed on the context, not the page: the pyodide kernel and micropip run in a web
// worker, and a page-scoped route does not see what the worker asks for.
const blocked = [];
await context.route(PYPI_HOSTS, async (route) => {
  blocked.push(route.request().url());
  await route.abort("blockedbyclient");
});

const page = await context.newPage();

const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));

// ------------------------------------------------------------------ 1. kernel
section("TEST 1: kernel comes up with pypi.org and files.pythonhosted.org blocked");
const t0 = Date.now();
const resp = await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
check("HTTP 200 for lab app", resp.status() === 200, `status=${resp.status()}`);
await page.waitForSelector(".jp-Notebook", { timeout: 180000 });

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
  try {
    await byDeadline(k.info, "kernel.info");
  } catch (e) {
    return { error: e.message };
  }
  while (k.status !== "idle" && Date.now() < deadline) await new Promise((r) => setTimeout(r, 250));
  return { name: k.name, status: k.status };
}, KERNEL_TIMEOUT);
report.kernel = kernelInfo;
report.timings.kernelReadyMs = Date.now() - t0;
check("kernel became ready", !kernelInfo.error, kernelInfo.error ?? `${report.timings.kernelReadyMs} ms`);
if (kernelInfo.error) {
  await browser.close();
  finish(1);
}

// Same driver tests/run.mjs installs.
await page.evaluate((defaultTimeout) => {
  window.__kernel = () => window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  window.__exec = async (code, timeoutMs) => {
    const future = window.__kernel().requestExecute({ code, stop_on_error: false });
    const out = { stdout: "", results: [], errors: [] };
    future.onIOPub = (msg) => {
      const t = msg.header.msg_type;
      if (t === "stream") out.stdout += msg.content.text;
      else if (t === "execute_result" || t === "display_data") out.results.push(msg.content.data["text/plain"] ?? "");
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

const run = async (name, code, timeoutMs) => {
  const out = await page.evaluate(([c, t]) => window.__exec(c, t), [code, timeoutMs ?? EXEC_TIMEOUT]);
  report.cells[name] = { status: out.status, stdout: out.stdout, errors: out.errors };
  return out;
};

const sanity = await run("sanity", "print('KERNEL EXEC OK')");
check("window.__exec drives the kernel", marker(sanity, "KERNEL EXEC OK"), `status=${sanity.status}`);

// ------------------------------------------------------------------- 2. setup
section("TEST 2: oold_agent.setup() completes without PyPI");
const tSetup = Date.now();
const setup = await run(
  "setup",
  `
import oold_agent
_info = await oold_agent.setup(verbose=False)
print("SETUP oold " + str(_info["oold"]))
print("SETUP validation " + str(_info["validation"]))
print("SETUP pyodide_http " + str(_info["pyodide_http"]))
print("SETUP failed " + str(len(_info["failed"])))
for _line in _info["failed"]:
    print("SETUP FAILURE " + _line)
`,
  SETUP_TIMEOUT
);
console.log(setup.stdout);
if (setup.errors.length) console.log(setup.errors.join("\n").slice(0, 3000));
report.timings.setupMs = Date.now() - tSetup;
console.log(`setup took ${report.timings.setupMs} ms`);
cellOk("setup", setup);
expectLine("setup", setup, "SETUP oold 2.2.0");
expectLine("setup", setup, "SETUP validation True");
expectLine("setup", setup, "SETUP pyodide_http True");
expectLine("setup", setup, "SETUP failed 0");

// --------------------------------------------------------------- 3. formats
section("TEST 3: oold.validation.formats imports with its format checkers");
// The import is the assertion: formats copies iri, uri, hostname and the rest out of
// Draft202012Validator.FORMAT_CHECKER at import time and raises KeyError on any that
// the backing package never registered.
const COPIED = [
  "date",
  "duration",
  "hostname",
  "idn-hostname",
  "ipv4",
  "ipv6",
  "iri",
  "iri-reference",
  "json-pointer",
  "regex",
  "relative-json-pointer",
  "uri",
  "uri-reference",
];
// Each probe is backed by a different optional package, so a conformance answer is
// evidence the vendored wheel works rather than merely unzips.
const CONFORMS = [
  ["hostname", "example.com", "true", "fqdn"],
  ["hostname", "-nope-.com", "false", "fqdn"],
  ["duration", "P1DT2H", "true", "isoduration"],
  ["duration", "P", "false", "isoduration"],
  ["iri", "https://example.org/café", "true", "rfc3987-syntax"],
  ["uri", "https://example.org/a", "true", "rfc3986-validator"],
  ["json-pointer", "/a/b", "true", "jsonpointer"],
  ["json-pointer", "a/b", "false", "jsonpointer"],
];
const formats = await run(
  "formats",
  `
import oold.validation.formats as _f
print("FORMATS module " + str(_f.__file__))
import importlib.metadata as _md
print("FORMATS jsonschema " + _md.version("jsonschema"))
for _name in ${JSON.stringify(COPIED)}:
    print("FORMATS checker " + _name + " " + str(_name in _f.OOLD_FORMAT_CHECKER.checkers))
for _name, _value, _ in ${JSON.stringify(CONFORMS.map((c) => [c[0], c[1], c[2]]))}:
    print("FORMATS conforms " + _name + " " + repr(_value) + " " + str(_f.OOLD_FORMAT_CHECKER.conforms(_value, _name)).lower())
print("FORMATS IMPORT OK")
`
);
console.log(formats.stdout);
if (formats.errors.length) console.log(formats.errors.join("\n").slice(0, 3000));
cellOk("formats", formats);
expectLine("formats", formats, "FORMATS IMPORT OK");
for (const name of COPIED) {
  expectLine("formats", formats, `FORMATS checker ${name} True`);
}
for (const [name, value, expected, backing] of CONFORMS) {
  const line = `FORMATS conforms ${name} '${value}' ${expected}`;
  const ok = marker(formats, line);
  check(`format ${name} on ${JSON.stringify(value)} is ${expected}, backed by ${backing}`, ok, ok ? "" : `lines: ${JSON.stringify(lines(formats).filter((l) => l.startsWith(`FORMATS conforms ${name} `)))}`);
}

// ------------------------------------------- 4. where the packages came from
section("TEST 4: nothing was fetched from PyPI");
const origins = await run(
  "origins",
  `
import importlib.metadata as _md
for _name in ("oold", "rdflib", "pyld", "SPARQLWrapper", "jsondiff", "datamodel-code-generator",
              "fqdn", "isoduration", "rfc3339-validator", "rfc3986-validator", "rfc3987-syntax",
              "uri-template", "webcolors", "jsonschema", "referencing", "click", "typing-extensions"):
    try:
        print("DIST " + _name + " " + _md.version(_name))
    except Exception as _e:
        print("DIST " + _name + " MISSING " + type(_e).__name__)
`
);
console.log(origins.stdout);
report.blocked = [...blocked];
console.log(`requests to pypi.org or pythonhosted.org during load and setup: ${blocked.length}`);
blocked.slice(0, 20).forEach((u) => console.log("  " + u));
check(
  "no request reached for pypi.org or files.pythonhosted.org",
  blocked.length === 0,
  blocked.length ? JSON.stringify(blocked.slice(0, 5)) : ""
);
for (const name of ["oold", "rdflib", "pyld", "SPARQLWrapper", "jsondiff", "fqdn", "isoduration", "rfc3987-syntax", "webcolors"]) {
  const line = lines(origins).find((l) => l.startsWith(`DIST ${name} `));
  check(`${name} is installed in the kernel`, !!line && !line.includes("MISSING"), line ?? "no DIST line");
}

// ------------------------------------------------------------- 5. the control
section("TEST 5: control, a package that is vendored nowhere must fail to install");
// If this installs, the block never reached micropip and tests 2 to 4 prove nothing.
const control = await run(
  "control",
  `
import piplite
try:
    await piplite.install("cowsay")
    print("CONTROL INSTALLED")
except Exception as _e:
    print("CONTROL BLOCKED " + type(_e).__name__)
`,
  120000
);
console.log(control.stdout);
const attemptedAfterControl = blocked.length;
console.log(`requests to pypi.org or pythonhosted.org after the control: ${attemptedAfterControl}`);
report.blockedAfterControl = [...blocked];
check(
  "control package could not be installed while PyPI is blocked",
  marker(control, "CONTROL BLOCKED ValueError") || lines(control).some((l) => l.startsWith("CONTROL BLOCKED ")),
  `stdout=${JSON.stringify(lines(control))}`
);
check(
  "the block is what stopped it, a request was attempted and aborted",
  attemptedAfterControl > 0,
  `${attemptedAfterControl} attempts recorded`
);

check("no uncaught page errors", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 5)));

await page.screenshot({ path: `${ARTIFACTS}/offline.png`, fullPage: false });
await browser.close();
finish(failures === 0 ? 0 : 1);
