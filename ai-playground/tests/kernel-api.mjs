// Tier 2: the kernel-side Python API an LLM agent calls, driven through the real
// kernel protocol and independent of any model. Every entry point of
// contents/oold_agent.py is exercised with a positive and a negative case.
//
// Assertions are line-exact against stream output. A traceback echoes the failing
// source line, so a substring search over all output matches the print() call
// itself and passes while the cell raised.

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

const report = { url: PAGE_URL, tests: [], timings: {}, kernel: {}, cells: {} };
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
  writeFileSync(`${ARTIFACTS}/kernel-api.json`, JSON.stringify(report, null, 2));
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

// Every assertion goes through here, so no test can pass on a cell that raised:
// a traceback would otherwise leave the expected line in place from an earlier
// successful print while the rest of the cell never ran.
function cellOk(name, out) {
  const ok = out.status === "ok" && out.errors.length === 0;
  check(`${name}: cell executed without raising`, ok, ok ? `status=${out.status}` : `status=${out.status} ${out.errors.join(" ").slice(0, 600)}`);
  return ok;
}

function expectLine(name, out, line) {
  check(`${name}: "${line.length > 110 ? line.slice(0, 107) + "..." : line}"`, marker(out, line), marker(out, line) ? "" : nearest(out, line));
}

// A failing line-exact assertion is useless without the line that was there
// instead, so report the closest one by shared prefix.
function nearest(out, line) {
  const head = line.split(" ").slice(0, 2).join(" ");
  const candidates = lines(out).filter((l) => l.startsWith(head));
  return candidates.length ? `got: ${candidates.map((c) => JSON.stringify(c)).join(" | ").slice(0, 400)}` : "no line with that prefix";
}

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await context.newPage();

const pageErrors = [];
const failedRequests = [];
const badStatuses = [];

// jupyter-mcp-manager arrives as a dependency of jupyterlite-ai and polls a server
// REST route. A static site has no such route, so its 404 is expected.
const EXPECTED_404 = [/\/jupyter-mcp-manager\/servers/];

page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));
page.on("requestfailed", (r) => failedRequests.push(`${r.url()} :: ${r.failure()?.errorText}`));
page.on("response", (r) => {
  if (r.status() >= 400) badStatuses.push(`${r.status()} ${r.url()}`);
});

// ------------------------------------------------------------------ 1. kernel
section("TEST 1: kernel comes up and window.__exec drives it");
const t0 = Date.now();
const resp = await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
check("HTTP 200 for lab app", resp.status() === 200, `status=${resp.status()}`);
await page.waitForSelector(".jp-Notebook", { timeout: 180000 });

const kernelInfo = await page.evaluate(async (timeoutMs) => {
  const deadline = Date.now() + timeoutMs;
  const byDeadline = (p, what) =>
    Promise.race([
      p,
      new Promise((_, rej) => setTimeout(() => rej(new Error(`${what} did not settle`)), Math.max(0, deadline - Date.now()))),
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
  await page.screenshot({ path: `${ARTIFACTS}/kernel-api-no-kernel.png` });
  await browser.close();
  finish(1);
}

// Same driver tests/run.mjs installs. The deadline matters: a dead kernel leaves
// future.done pending forever and page.evaluate honours no timeout.
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
    const reply = await Promise.race([future.done, new Promise((r) => setTimeout(() => r(timedOut), timeoutMs ?? defaultTimeout))]);
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

// --------------------------------------------------------------- 2. fixtures
section("TEST 2: contents drive carries the module and its fixtures");
const drive = await run(
  "drive",
  `
import os
print("CWD " + os.getcwd())
for _n in (
    "oold_agent.py",
    "fixture_Measurement.schema.json",
    "fixture_Measurement.instance.json",
    "fixture_Broken.schema.json",
    "fixture_bad.instance.json",
    "fixture_measurements.csv",
    "fixture_malformed.csv",
    "fixture_measurements.xlsx",
    "fixture_workbook.xlsx",
    "fixture_merged_header.xlsx",
    "fixture_legacy.xls",
    "fixture_corrupt.xlsx",
    "fixture_protocol.docx",
    "fixture_notes.docx",
    "fixture_corrupt.docx",
    "fixture_mislabelled.docx",
    "fixture_not_a.docx",
    "fixture_overview.pptx",
    "fixture_plain.pptx",
    "fixture_corrupt.pptx",
    "fixture_mislabelled.pptx",
    "fixture_report.pdf",
    "fixture_notes.pdf",
    "fixture_corrupt.pdf",
    "fixture_mislabelled.pdf",
):
    print(("DRIVE OK " if os.path.exists(_n) else "DRIVE MISSING ") + _n)
`
);
console.log(drive.stdout);
cellOk("drive", drive);
for (const name of [
  "oold_agent.py",
  "fixture_Measurement.schema.json",
  "fixture_Measurement.instance.json",
  "fixture_Broken.schema.json",
  "fixture_bad.instance.json",
  "fixture_measurements.csv",
  "fixture_malformed.csv",
  "fixture_measurements.xlsx",
  "fixture_workbook.xlsx",
  "fixture_merged_header.xlsx",
  "fixture_legacy.xls",
  "fixture_corrupt.xlsx",
  "fixture_protocol.docx",
  "fixture_notes.docx",
  "fixture_corrupt.docx",
  "fixture_mislabelled.docx",
  "fixture_not_a.docx",
  "fixture_overview.pptx",
  "fixture_plain.pptx",
  "fixture_corrupt.pptx",
  "fixture_mislabelled.pptx",
  "fixture_report.pdf",
  "fixture_notes.pdf",
  "fixture_corrupt.pdf",
  "fixture_mislabelled.pdf",
]) {
  expectLine("drive", drive, `DRIVE OK ${name}`);
}
// The negative of the same question: a name that is not there must report missing
// rather than be silently skipped, otherwise the loop above proves nothing.
const driveNeg = await run("drive-negative", `
import os
print(("DRIVE OK " if os.path.exists("fixture_nope.csv") else "DRIVE MISSING ") + "fixture_nope.csv")
`);
cellOk("drive-negative", driveNeg);
expectLine("drive-negative", driveNeg, "DRIVE MISSING fixture_nope.csv");

// ------------------------------------------------------------------ 3. setup
section("TEST 3: oold_agent.setup() installs oold and the validation extra");
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
import oold, sys
print("SETUP python " + sys.version.split()[0])
print("SETUP path " + str(oold.__file__))
`,
  SETUP_TIMEOUT
);
console.log(setup.stdout);
if (setup.errors.length) console.log(setup.errors.join("\n").slice(0, 3000));
report.timings.setupMs = Date.now() - tSetup;
cellOk("setup", setup);
expectLine("setup", setup, "SETUP oold 2.2.0");
expectLine("setup", setup, "SETUP validation True");
expectLine("setup", setup, "SETUP pyodide_http True");
expectLine("setup", setup, "SETUP failed 0");
expectLine("setup", setup, "SETUP python 3.14.2");
expectLine("setup", setup, "SETUP path /lib/python3.14/site-packages/oold/__init__.py");
if (!marker(setup, "SETUP validation True")) {
  console.log("oold.validation never imported, the rest of the suite cannot run");
  await browser.close();
  finish(1);
}

const preamble = `
import json, re as _re
import oold_agent
SCHEMA = json.loads(open("fixture_Measurement.schema.json", encoding="utf-8").read())
INSTANCE = json.loads(open("fixture_Measurement.instance.json", encoding="utf-8").read())

def _norm(prefix, text):
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith(("PASS", "FAIL", "WARN", "Error:", "exit code")):
            print(prefix + " " + " ".join(stripped.split()))
`;

// -------------------------------------------------------------------- 4. CLI
section("TEST 4: oold_cli runs the click app in-process");
const cli = await run(
  "cli",
  `${preamble}
_norm("CLI1", oold_agent.oold_cli("validate", "fixture_Measurement.schema.json"))
_norm("CLI2", oold_agent.oold_cli("validate", "fixture_Broken.schema.json"))
_norm("CLI3", oold_agent.oold_cli("validate", "fixture_nope.json"))
`
);
console.log(cli.stdout);
if (cli.errors.length) console.log(cli.errors.join("\n").slice(0, 3000));
cellOk("cli", cli);
expectLine("cli positive", cli, "CLI1 PASS fixture_Measurement.schema.json");
expectLine("cli positive", cli, "CLI1 exit code 0");
expectLine("cli negative", cli, "CLI2 FAIL fixture_Broken.schema.json");
expectLine("cli negative", cli, "CLI2 exit code 1");
expectLine(
  "cli negative names schema.meta",
  cli,
  "CLI2 FAIL schema.meta fixture_Broken.schema.json [1.0.0]: at /x-oold-uuid: 'not-a-uuid' is not a 'uuid'"
);
expectLine(
  "cli negative names context.predicates",
  cli,
  "CLI2 FAIL OOLD-EXT-2b61 context.predicates fixture_Broken.schema.json: property 'sensor_id' expands to 'schema:identifier', which is not an absolute IRI: the prefix is probably undefined"
);
// SystemExit must not escape: a bad invocation reports click's exit code 2 as data
// and the cell still finishes clean, which cellOk above asserts.
expectLine("cli bad invocation", cli, "CLI3 Error: Invalid value for 'TARGET': Path 'fixture_nope.json' does not exist.");
expectLine("cli bad invocation", cli, "CLI3 exit code 2");

// ------------------------------------------------------------- 5. validation
section("TEST 5: validate_schema, validate_instance, validate");
const validation = await run(
  "validation",
  `${preamble}
r = oold_agent.validate_schema("fixture_Measurement.schema.json")
print("VS1 passed " + str(r.passed))
print("VS1 counts " + json.dumps(r.counts, sort_keys=True))
r = oold_agent.validate_schema("fixture_Broken.schema.json")
print("VS2 passed " + str(r.passed))
print("VS2 failures " + ",".join(sorted(c.id for c in r.failures())))
print("VS2 rules " + ",".join(sorted(c.rule or "-" for c in r.failures())))
print("VS2 faults " + str(r.counts["fault"]))

r = oold_agent.validate_instance("fixture_Measurement.instance.json", "fixture_Measurement.schema.json")
print("VI1 passed " + str(r.passed))
print("VI1 failures " + ",".join(sorted(c.id for c in r.failures())) + "|")
r = oold_agent.validate_instance("fixture_bad.instance.json", "fixture_Measurement.schema.json")
print("VI2 passed " + str(r.passed))
print("VI2 failures " + ",".join(sorted(c.id for c in r.failures())))
print("VI2 message " + r.failures()[0].message)

print("MEM1 passed " + str(oold_agent.validate_schema(SCHEMA).passed))
_broken = dict(SCHEMA)
_broken["x-oold-uuid"] = "not-a-uuid"
r = oold_agent.validate_schema(_broken)
print("MEM2 passed " + str(r.passed) + " " + ",".join(sorted(c.id for c in r.failures())))
print("MEM3 passed " + str(oold_agent.validate_instance(INSTANCE, SCHEMA).passed))
_bad = dict(INSTANCE)
_bad["value"] = "twenty-two"
r = oold_agent.validate_instance(_bad, SCHEMA)
print("MEM4 passed " + str(r.passed) + " " + ",".join(sorted(c.id for c in r.failures())))

print("V1 " + str(oold_agent.validate("fixture_Measurement.schema.json").passed))
print("V2 " + str(oold_agent.validate("fixture_bad.instance.json").passed))
print("V3 " + str(oold_agent.validate("fixture_nope.json").fatal_error))
`
);
console.log(validation.stdout);
if (validation.errors.length) console.log(validation.errors.join("\n").slice(0, 3000));
cellOk("validation", validation);
expectLine("validate_schema positive", validation, "VS1 passed True");
expectLine("validate_schema positive", validation, 'VS1 counts {"fail": 0, "fault": 0, "ok": 32, "skip": 0, "warn": 0}');
expectLine("validate_schema negative", validation, "VS2 passed False");
expectLine("validate_schema negative names its check ids", validation, "VS2 failures context.predicates,schema.meta");
expectLine("validate_schema negative cites its rule", validation, "VS2 rules -,OOLD-EXT-2b61");
expectLine("validate_schema negative is a finding, not a fault", validation, "VS2 faults 0");
expectLine("validate_instance positive", validation, "VI1 passed True");
expectLine("validate_instance positive", validation, "VI1 failures |");
expectLine("validate_instance negative", validation, "VI2 passed False");
expectLine("validate_instance negative names its check id", validation, "VI2 failures instance.schema");
expectLine("validate_instance negative names the violation", validation, "VI2 message at /value: 'twenty-two' is not of type 'number'");
expectLine("in-memory schema positive", validation, "MEM1 passed True");
expectLine("in-memory schema negative", validation, "MEM2 passed False schema.meta");
expectLine("in-memory instance positive", validation, "MEM3 passed True");
expectLine("in-memory instance negative", validation, "MEM4 passed False instance.schema");
expectLine("validate dispatches a schema", validation, "V1 True");
expectLine("validate dispatches an instance", validation, "V2 False");
expectLine("validate reports a missing file as data", validation, "V3 not found: fixture_nope.json");

// -------------------------------------------------------------------- 6. csv
section("TEST 6: csv_profile");
const csv = await run(
  "csv",
  `${preamble}
p = oold_agent.csv_profile("fixture_measurements.csv")
print("CSV1 ok " + str(p["ok"]))
print("CSV1 rows " + str(p["rows"]))
print("CSV1 columns " + ",".join(c["name"] for c in p["columns"]))
print("CSV1 types " + ",".join(c["type"] for c in p["columns"]))
print("CSV1 nulls " + ",".join(str(c["nulls"]) for c in p["columns"]))
print("CSV1 distinct " + ",".join(str(c["distinct"]) for c in p["columns"]))
print("CSV1 identifier " + str(p["identifier"]))
print("CSV1 examples unit " + ",".join(p["columns"][4]["examples"]))
print("CSV1 problems " + str(len(p["problems"])))

q = oold_agent.csv_profile("fixture_malformed.csv")
print("CSV2 ok " + str(q["ok"]))
print("CSV2 problems " + " | ".join(q["problems"]))
print("CSV2 identifier " + str(q["identifier"]))

m = oold_agent.csv_profile("fixture_nope.csv")
print("CSV3 ok " + str(m["ok"]))
print("CSV3 problems " + str(len(m["problems"])))
print("CSV3 reason " + m["problems"][0].split(":")[0])
`
);
console.log(csv.stdout);
if (csv.errors.length) console.log(csv.errors.join("\n").slice(0, 3000));
cellOk("csv", csv);
expectLine("csv_profile positive", csv, "CSV1 ok True");
expectLine("csv_profile positive", csv, "CSV1 rows 4");
expectLine("csv_profile positive", csv, "CSV1 columns taken_at,sensor_id,measurement_id,value,unit,note");
expectLine("csv_profile infers types", csv, "CSV1 types date,string,string,number,string,string");
expectLine("csv_profile counts nulls", csv, "CSV1 nulls 0,0,0,0,0,3");
expectLine("csv_profile counts distinct values", csv, "CSV1 distinct 4,2,4,3,1,1");
// taken_at is unique and leftmost; picking by position alone would get this wrong.
expectLine("csv_profile guesses the identifier", csv, "CSV1 identifier measurement_id");
expectLine("csv_profile collects examples", csv, "CSV1 examples unit degC");
expectLine("csv_profile positive has no problems", csv, "CSV1 problems 0");
expectLine("csv_profile negative", csv, "CSV2 ok False");
expectLine(
  "csv_profile negative names both problems",
  csv,
  "CSV2 problems duplicate column name(s): value | 2 row(s) do not have 4 fields: line 3, 4"
);
expectLine("csv_profile still profiles a malformed file", csv, "CSV2 identifier sensor_id");
expectLine("csv_profile missing file", csv, "CSV3 ok False");
expectLine("csv_profile missing file", csv, "CSV3 problems 1");
expectLine("csv_profile missing file does not raise", csv, "CSV3 reason could not be read");

// ------------------------------------------------------------- 7. spreadsheets
section("TEST 7: sheets and xlsx_profile");
const xlsx = await run(
  "xlsx",
  `${preamble}
x = oold_agent.xlsx_profile("fixture_measurements.xlsx")
print("XL1 ok " + str(x["ok"]))
print("XL1 sheet " + str(x["sheet"]))
print("XL1 sheets " + ",".join(x["sheets"]))
print("XL1 rows " + str(x["rows"]))
print("XL1 columns " + ",".join(c["name"] for c in x["columns"]))
print("XL1 types " + ",".join(c["type"] for c in x["columns"]))
print("XL1 nulls " + ",".join(str(c["nulls"]) for c in x["columns"]))
print("XL1 distinct " + ",".join(str(c["distinct"]) for c in x["columns"]))
print("XL1 identifier " + str(x["identifier"]))
print("XL1 examples value " + ",".join(x["columns"][3]["examples"]))
print("XL1 problems " + str(len(x["problems"])))

_csv = oold_agent.csv_profile("fixture_measurements.csv")
_keys = ("name", "type", "nulls", "distinct", "unique")
_shape = lambda p: [{k: c[k] for k in _keys} for c in p["columns"]]
print("XL1 same as csv " + str(_shape(_csv) == _shape(x))
      + " rows " + str(_csv["rows"] == x["rows"])
      + " identifier " + str(_csv["identifier"] == x["identifier"]))
print("XL1 keys " + ",".join(sorted(set(_csv) ^ set(x))))
print("XL1 examples value in csv " + ",".join(_csv["columns"][3]["examples"]))

s = oold_agent.sheets("fixture_workbook.xlsx")
print("SH1 ok " + str(s["ok"]))
print("SH1 sheets " + ",".join(s["sheets"]))
print("SH1 problems " + str(len(s["problems"])))
s2 = oold_agent.sheets("fixture_nope.xlsx")
print("SH2 ok " + str(s2["ok"]))
print("SH2 sheets " + ",".join(s2["sheets"]) + "|")
print("SH2 reason " + s2["problems"][0].split(":")[0])

w = oold_agent.xlsx_profile("fixture_workbook.xlsx")
print("XL2 ok " + str(w["ok"]))
print("XL2 sheet " + str(w["sheet"]))
print("XL2 sheets " + ",".join(w["sheets"]))
print("XL2 problems " + " | ".join(w["problems"]))
print("XL2 columns " + ",".join(c["name"] for c in w["columns"]))

named = oold_agent.xlsx_profile("fixture_workbook.xlsx", sheet="sensors")
print("XL3 ok " + str(named["ok"]))
print("XL3 sheet " + str(named["sheet"]))
print("XL3 rows " + str(named["rows"]))
print("XL3 columns " + ",".join(c["name"] for c in named["columns"]))
print("XL3 types " + ",".join(c["type"] for c in named["columns"]))
print("XL3 problems " + str(len(named["problems"])))
print("XL3 by index " + str(oold_agent.xlsx_profile("fixture_workbook.xlsx", sheet=1)["sheet"]))

blank = oold_agent.xlsx_profile("fixture_workbook.xlsx", sheet="blank")
print("XL4 ok " + str(blank["ok"]) + " rows " + str(blank["rows"]))
print("XL4 problems " + " | ".join(blank["problems"]))
unknown = oold_agent.xlsx_profile("fixture_workbook.xlsx", sheet="nope")
print("XL5 ok " + str(unknown["ok"]) + " sheet " + str(unknown["sheet"]))
print("XL5 problems " + " | ".join(unknown["problems"]))

merged = oold_agent.xlsx_profile("fixture_merged_header.xlsx")
print("XL6 ok " + str(merged["ok"]))
print("XL6 rows " + str(merged["rows"]))
print("XL6 columns " + ",".join(c["name"] for c in merged["columns"]))
print("XL6 problems " + " | ".join(merged["problems"]))

legacy = oold_agent.xlsx_profile("fixture_legacy.xls")
print("XL7 ok " + str(legacy["ok"]))
print("XL7 sheet " + str(legacy["sheet"]))
print("XL7 columns " + ",".join(c["name"] for c in legacy["columns"]))
print("XL7 types " + ",".join(c["type"] for c in legacy["columns"]))

broken = oold_agent.xlsx_profile("fixture_corrupt.xlsx")
print("XL8 returned " + type(broken).__name__)
print("XL8 ok " + str(broken["ok"]))
print("XL8 sheets " + ",".join(broken["sheets"]) + "|")
print("XL8 problems " + str(len(broken["problems"])))
print("XL8 reason " + broken["problems"][0].split(":")[0])
absent = oold_agent.xlsx_profile("fixture_nope.xlsx")
print("XL9 ok " + str(absent["ok"]))
print("XL9 reason " + absent["problems"][0].split(":")[0])
`
);
console.log(xlsx.stdout);
if (xlsx.errors.length) console.log(xlsx.errors.join("\n").slice(0, 3000));
cellOk("xlsx", xlsx);
expectLine("xlsx_profile positive", xlsx, "XL1 ok True");
expectLine("xlsx_profile names the sheet it read", xlsx, "XL1 sheet measurements");
expectLine("xlsx_profile lists the sheets", xlsx, "XL1 sheets measurements");
expectLine("xlsx_profile positive", xlsx, "XL1 rows 4");
expectLine("xlsx_profile positive", xlsx, "XL1 columns taken_at,sensor_id,measurement_id,value,unit,note");
expectLine("xlsx_profile infers types from typed cells", xlsx, "XL1 types date,string,string,number,string,string");
expectLine("xlsx_profile counts empty cells as nulls", xlsx, "XL1 nulls 0,0,0,0,0,3");
expectLine("xlsx_profile counts distinct values", xlsx, "XL1 distinct 4,2,4,3,1,1");
expectLine("xlsx_profile guesses the identifier", xlsx, "XL1 identifier measurement_id");
expectLine("xlsx_profile positive has no problems", xlsx, "XL1 problems 0");
// The two files hold the same table, so the agent reads one shape either way.
expectLine("a sheet and a CSV of the same table profile alike", xlsx, "XL1 same as csv True rows True identifier True");
expectLine("the shapes differ only in delimiter against sheet and sheets", xlsx, "XL1 keys delimiter,sheet,sheets");
// xlsx has one numeric type, a double, so 22.0 and 22 are the same cell and the
// profile shows what the sheet shows. The CSV keeps the text it was written with.
expectLine("whole numbers render as the sheet shows them", xlsx, "XL1 examples value 20.25,21.5,22");
expectLine("the CSV of the same table keeps its own text", xlsx, "XL1 examples value in csv 20.25,21.5,22.0");
expectLine("sheets positive", xlsx, "SH1 ok True");
expectLine("sheets lists every sheet in workbook order", xlsx, "SH1 sheets measurements,sensors,blank");
expectLine("sheets positive has no problems", xlsx, "SH1 problems 0");
expectLine("sheets negative", xlsx, "SH2 ok False");
expectLine("sheets negative lists nothing", xlsx, "SH2 sheets |");
expectLine("sheets negative does not raise", xlsx, "SH2 reason could not be read");
expectLine("multi-sheet workbook profiles one sheet", xlsx, "XL2 ok False");
expectLine("multi-sheet workbook profiles the first sheet", xlsx, "XL2 sheet measurements");
expectLine("multi-sheet workbook reports every sheet", xlsx, "XL2 sheets measurements,sensors,blank");
expectLine(
  "the sheet it chose is stated, not taken silently",
  xlsx,
  "XL2 problems workbook has 3 sheets (measurements, sensors, blank); profiled 'measurements'. Pass sheet= to profile another."
);
expectLine("multi-sheet workbook still profiles columns", xlsx, "XL2 columns taken_at,sensor_id,measurement_id,value,unit,note");
// Naming the sheet answers the question, so there is nothing left to report.
expectLine("a named sheet is profiled without comment", xlsx, "XL3 ok True");
expectLine("a named sheet is the one read", xlsx, "XL3 sheet sensors");
expectLine("a named sheet is profiled", xlsx, "XL3 rows 2");
expectLine("a named sheet is profiled", xlsx, "XL3 columns sensor_id,placed_at,depth_m");
expectLine("a named sheet is profiled", xlsx, "XL3 types string,string,number");
expectLine("a named sheet raises no sheet problem", xlsx, "XL3 problems 0");
expectLine("sheet also takes an index", xlsx, "XL3 by index sensors");
expectLine("an empty sheet is data, not an exception", xlsx, "XL4 ok False rows 0");
expectLine("an empty sheet says so", xlsx, "XL4 problems sheet 'blank' is empty");
expectLine("an unknown sheet name is data", xlsx, "XL5 ok False sheet None");
expectLine(
  "an unknown sheet name lists the sheets there are",
  xlsx,
  "XL5 problems no sheet named 'nope': the workbook has measurements, sensors, blank"
);
expectLine("a merged header row does not raise", xlsx, "XL6 ok False");
expectLine("a merged header row leaves the rest readable", xlsx, "XL6 rows 3");
expectLine("empty header cells are named by position", xlsx, "XL6 columns Measurements 2024,column_2,column_3");
expectLine(
  "a merged header row arrives as a problem",
  xlsx,
  "XL6 problems 2 of 3 header cells are empty, at column 2, 3: a merged or title row above the real header reads this way"
);
// xlrd in the distribution is 2.x and reads only this format, never xlsx; calamine
// reads it too, through its own CFB parser, so the brief can send the model one way.
expectLine("legacy xls opens through calamine", xlsx, "XL7 ok True");
expectLine("legacy xls opens through calamine", xlsx, "XL7 sheet legacy");
expectLine("legacy xls opens through calamine", xlsx, "XL7 columns sensor_id,placed_at,depth_m");
expectLine("legacy xls opens through calamine", xlsx, "XL7 types string,string,number");
expectLine("a corrupt workbook comes back as data", xlsx, "XL8 returned dict");
expectLine("a corrupt workbook comes back as data", xlsx, "XL8 ok False");
expectLine("a corrupt workbook lists no sheets", xlsx, "XL8 sheets |");
expectLine("a corrupt workbook reports one problem", xlsx, "XL8 problems 1");
expectLine("a corrupt workbook does not raise", xlsx, "XL8 reason could not be read");
expectLine("a missing workbook does not raise", xlsx, "XL9 ok False");
expectLine("a missing workbook does not raise", xlsx, "XL9 reason could not be read");

// ---------------------------------------------------------------- 8. documents
section("TEST 8: tables, doc_profile and doc_text");
const docs = await run(
  "docs",
  `${preamble}
t = oold_agent.tables("fixture_protocol.docx")
print("DT1 ok " + str(t["ok"]))
print("DT1 format " + str(t["format"]))
print("DT1 tables " + ",".join(e["where"] for e in t["tables"]))
print("DT1 sizes " + ",".join(str(e["rows"]) + "x" + str(e["columns"]) for e in t["tables"]))
print("DT1 problems " + str(len(t["problems"])))

d = oold_agent.doc_profile("fixture_protocol.docx")
print("DX1 returned " + type(d).__name__)
print("DX1 ok " + str(d["ok"]))
print("DX1 format " + str(d["format"]))
print("DX1 table " + str(d["table"]) + " where " + str(d["where"]))
print("DX1 rows " + str(d["rows"]))
print("DX1 columns " + ",".join(c["name"] for c in d["columns"]))
print("DX1 types " + ",".join(c["type"] for c in d["columns"]))
print("DX1 nulls " + ",".join(str(c["nulls"]) for c in d["columns"]))
print("DX1 distinct " + ",".join(str(c["distinct"]) for c in d["columns"]))
print("DX1 examples value " + ",".join(d["columns"][1]["examples"]))
print("DX1 identifier " + str(d["identifier"]))
print("DX1 problems " + " | ".join(d["problems"]))

second = oold_agent.doc_profile("fixture_protocol.docx", table=1)
print("DX2 ok " + str(second["ok"]) + " where " + str(second["where"]))
print("DX2 columns " + ",".join(c["name"] for c in second["columns"]))
print("DX2 examples term " + ",".join(second["columns"][0]["examples"]))
print("DX2 problems " + str(len(second["problems"])))
print("DX2 last " + str(oold_agent.doc_profile("fixture_protocol.docx", table=-1)["where"]))
bad = oold_agent.doc_profile("fixture_protocol.docx", table=5)
print("DX3 ok " + str(bad["ok"]) + " table " + str(bad["table"]))
print("DX3 problems " + " | ".join(bad["problems"]))
named = oold_agent.doc_profile("fixture_protocol.docx", table="table 1")
print("DX4 problems " + " | ".join(named["problems"]))

x = oold_agent.doc_text("fixture_protocol.docx")
print("TX1 ok " + str(x["ok"]))
print("TX1 format " + str(x["format"]))
print("TX1 headings " + " | ".join(x["headings"]))
print("TX1 blocks " + ",".join(b["where"] for b in x["blocks"]))
print("TX1 chars " + str(x["chars"]) + " joined " + str(x["chars"] == len(x["text"])))
print("TX1 dictionary " + str("unit is the unit of the reading" in x["text"]))
print("TX1 problems " + str(len(x["problems"])))
cut = oold_agent.doc_text("fixture_protocol.docx", max_chars=60)
print("TX2 ok " + str(cut["ok"]) + " blocks " + str(len(cut["blocks"])) + " chars " + str(cut["chars"]))
print("TX2 problems " + " | ".join(cut["problems"]))

p = oold_agent.doc_profile("fixture_overview.pptx")
print("PP1 ok " + str(p["ok"]))
print("PP1 format " + str(p["format"]))
print("PP1 where " + str(p["where"]))
print("PP1 columns " + ",".join(c["name"] for c in p["columns"]))
print("PP1 types " + ",".join(c["type"] for c in p["columns"]))
print("PP1 identifier " + str(p["identifier"]))
print("PP1 problems " + str(len(p["problems"])))
_keys = ("name", "type", "examples", "nulls", "distinct", "unique")
_shape = lambda prof: [{k: c[k] for k in _keys} for c in prof["columns"]]
print("PP1 same as docx " + str(_shape(p) == _shape(d)))
pt = oold_agent.doc_text("fixture_overview.pptx")
print("PP2 headings " + " | ".join(pt["headings"]))
print("PP2 blocks " + ",".join(b["where"] for b in pt["blocks"]))
print("PP2 slide2 " + pt["blocks"][1]["text"].replace("\\n", " / "))

r = oold_agent.doc_profile("fixture_report.pdf")
print("PF1 ok " + str(r["ok"]))
print("PF1 format " + str(r["format"]))
print("PF1 where " + str(r["where"]))
print("PF1 columns " + ",".join(c["name"] for c in r["columns"]))
print("PF1 types " + ",".join(c["type"] for c in r["columns"]))
print("PF1 identifier " + str(r["identifier"]))
print("PF1 problems " + str(len(r["problems"])))
rt = oold_agent.doc_text("fixture_report.pdf")
print("PF2 headings " + " | ".join(rt["headings"]))
print("PF2 blocks " + ",".join(b["where"] for b in rt["blocks"]))
print("PF2 method " + str("read once per day at 08:00" in rt["text"]))

for _name in ("fixture_notes.docx", "fixture_plain.pptx", "fixture_notes.pdf"):
    _t = oold_agent.tables(_name)
    _p = oold_agent.doc_profile(_name)
    _x = oold_agent.doc_text(_name)
    print("NT " + _name + " tables " + str(_t["ok"]) + " " + str(len(_t["tables"])) + " " + " | ".join(_t["problems"]))
    print("NT " + _name + " profile " + str(_p["ok"]) + " columns " + str(len(_p["columns"])))
    print("NT " + _name + " text " + str(_x["ok"]) + " blocks " + str(len(_x["blocks"])))

for _name in ("fixture_corrupt.docx", "fixture_corrupt.pptx", "fixture_corrupt.pdf"):
    _p = oold_agent.doc_profile(_name)
    _x = oold_agent.doc_text(_name)
    print("CO " + _name + " returned " + type(_p).__name__ + " ok " + str(_p["ok"]) + " format " + str(_p["format"]))
    print("CO " + _name + " reason " + _p["problems"][0].split(":")[0])
    print("CO " + _name + " text ok " + str(_x["ok"]) + " chars " + str(_x["chars"]))

for _name in ("fixture_mislabelled.docx", "fixture_mislabelled.pptx", "fixture_mislabelled.pdf"):
    _p = oold_agent.doc_profile(_name)
    print("ML " + _name + " format " + str(_p["format"]) + " ok " + str(_p["ok"]) + " columns " + str(len(_p["columns"])))
    print("ML " + _name + " problem " + _p["problems"][0])

_na = oold_agent.doc_profile("fixture_not_a.docx")
print("WR1 ok " + str(_na["ok"]) + " format " + str(_na["format"]))
print("WR1 problem " + _na["problems"][0])
_sheet = oold_agent.doc_profile("fixture_measurements.xlsx")
print("WR2 ok " + str(_sheet["ok"]) + " format " + str(_sheet["format"]))
print("WR2 problem " + _sheet["problems"][0])
_gone = oold_agent.doc_text("fixture_nope.docx")
print("WR3 ok " + str(_gone["ok"]) + " reason " + _gone["problems"][0].split(":")[0])
`
);
console.log(docs.stdout);
if (docs.errors.length) console.log(docs.errors.join("\n").slice(0, 3000));
cellOk("docs", docs);
expectLine("tables lists every table in reading order", docs, "DT1 ok True");
expectLine("tables names the format it read", docs, "DT1 format docx");
expectLine("tables names where each one sits", docs, "DT1 tables table 1,table 2");
expectLine("tables sizes each one before it is profiled", docs, "DT1 sizes 3x3,3x2");
expectLine("tables positive has no problems", docs, "DT1 problems 0");
expectLine("doc_profile returns data", docs, "DX1 returned dict");
expectLine("doc_profile names the format", docs, "DX1 format docx");
expectLine("doc_profile names the table it read", docs, "DX1 table 0 where table 1");
expectLine("doc_profile counts the body rows", docs, "DX1 rows 3");
expectLine("doc_profile names the columns", docs, "DX1 columns sensor_id,value,unit");
expectLine("doc_profile infers types from cell text", docs, "DX1 types string,number,string");
expectLine("doc_profile counts nulls", docs, "DX1 nulls 0,0,0");
expectLine("doc_profile counts distinct values", docs, "DX1 distinct 3,3,1");
expectLine("doc_profile carries example values", docs, "DX1 examples value 20.25,21.5,22");
expectLine("doc_profile guesses the identifier", docs, "DX1 identifier sensor_id");
// Several tables are answered the way several sheets are: list them all, profile the
// first, say so. ok is false because the caller still has a choice to make.
expectLine("a document with several tables profiles one", docs, "DX1 ok False");
expectLine(
  "the table it chose is stated, not taken silently",
  docs,
  "DX1 problems document has 2 tables (0 at table 1, 1 at table 2); profiled table 0. Pass table= to profile another."
);
expectLine("naming a table answers the question", docs, "DX2 ok True where table 2");
expectLine("a named table is the one profiled", docs, "DX2 columns term,meaning");
expectLine("a data dictionary profiles like any other table", docs, "DX2 examples term sensor_id,unit,value");
expectLine("a named table raises no table problem", docs, "DX2 problems 0");
expectLine("table= counts from the end as well", docs, "DX2 last table 2");
expectLine("an index past the end is data, not an exception", docs, "DX3 ok False table None");
expectLine("an index past the end says how many there are", docs, "DX3 problems no table at index 5: the document has 2");
expectLine(
  "a table name is refused, because a document has none",
  docs,
  "DX4 problems table= takes an index, not 'table 1': the document has 2 tables"
);
expectLine("doc_text positive", docs, "TX1 ok True");
expectLine("doc_text names the format", docs, "TX1 format docx");
expectLine("doc_text returns the outline", docs, "TX1 headings Measurement protocol | Method | Readings | Data dictionary");
expectLine("doc_text locates every block", docs, "TX1 blocks paragraph 1,paragraph 2,paragraph 3,paragraph 4,paragraph 5,paragraph 6");
expectLine("text is the blocks joined, and chars counts it", docs, "TX1 chars 311 joined True");
// What a column means is written next to the table, not in it, so the prose has to
// come back whole rather than as a heading list.
expectLine("doc_text returns the data dictionary prose", docs, "TX1 dictionary True");
expectLine("doc_text positive has no problems", docs, "TX1 problems 0");
expectLine("max_chars stops reading and says so", docs, "TX2 ok False blocks 2 chars 311");
expectLine(
  "max_chars reports the whole length, not the kept one",
  docs,
  "TX2 problems only 2 of 6 blocks were read: the document is 311 characters and max_chars is 60"
);
expectLine("a pptx table profiles", docs, "PP1 ok True");
expectLine("a pptx table profiles", docs, "PP1 format pptx");
expectLine("a pptx table is located by its slide", docs, "PP1 where slide 3");
expectLine("a pptx table profiles", docs, "PP1 columns sensor_id,value,unit");
expectLine("a pptx table profiles", docs, "PP1 types string,number,string");
expectLine("a pptx table profiles", docs, "PP1 identifier sensor_id");
expectLine("a pptx table positive has no problems", docs, "PP1 problems 0");
// The docx and the pptx carry the same table, so the agent reads one shape either way.
expectLine("one table profiles alike whatever carried it", docs, "PP1 same as docx True");
expectLine("pptx headings are the slide titles", docs, "PP2 headings Sensor overview | Method | Readings");
expectLine("pptx text is located by slide", docs, "PP2 blocks slide 1,slide 2,slide 3");
expectLine("a slide keeps its lines", docs, "PP2 slide2 Method / One reading per sensor per day / Values in degC");
expectLine("a pdf table profiles", docs, "PF1 ok True");
expectLine("a pdf table profiles", docs, "PF1 format pdf");
expectLine("a pdf table is located by its page", docs, "PF1 where page 2");
expectLine("a pdf table profiles", docs, "PF1 columns sensor id,value,unit");
expectLine("a pdf table profiles", docs, "PF1 types string,number,string");
expectLine("a pdf table profiles", docs, "PF1 identifier sensor id");
expectLine("a pdf table positive has no problems", docs, "PF1 problems 0");
expectLine("pdf headings come from the bookmark tree", docs, "PF2 headings Measurement protocol | Readings");
expectLine("pdf text is located by page", docs, "PF2 blocks page 1,page 2");
expectLine("pdf text carries the methods section", docs, "PF2 method True");
// A document with no table is not an error to raise, and its text still comes back.
for (const [name, blocks] of [
  ["fixture_notes.docx", 3],
  ["fixture_plain.pptx", 2],
  ["fixture_notes.pdf", 1],
]) {
  expectLine("a document with no tables says so", docs, `NT ${name} tables False 0 document has no tables; its text is in doc_text`);
  expectLine("a document with no tables profiles nothing", docs, `NT ${name} profile False columns 0`);
  expectLine("a document with no tables still has text", docs, `NT ${name} text True blocks ${blocks}`);
}
// A truncated zip has lost its central directory; MuPDF rebuilds a damaged pdf, so
// only its header line survives in the fixture. Either way the answer is data.
for (const [name, format] of [
  ["fixture_corrupt.docx", "None"],
  ["fixture_corrupt.pptx", "None"],
  ["fixture_corrupt.pdf", "pdf"],
]) {
  expectLine("a corrupt document comes back as data", docs, `CO ${name} returned dict ok False format ${format}`);
  expectLine("a corrupt document does not raise", docs, `CO ${name} reason could not be read`);
  expectLine("a corrupt document yields no text", docs, `CO ${name} text ok False chars 0`);
}
// The suffix is checked against the bytes and never trusted for them, so a renamed
// document is still extracted rather than handed to the wrong reader.
for (const [name, suffix, format] of [
  ["fixture_mislabelled.docx", "docx", "pdf"],
  ["fixture_mislabelled.pptx", "pptx", "docx"],
  ["fixture_mislabelled.pdf", "pdf", "pptx"],
]) {
  expectLine("a wrong suffix does not stop the extraction", docs, `ML ${name} format ${format} ok False columns 3`);
  expectLine("a wrong suffix is reported", docs, `ML ${name} problem suffix says ${suffix} and the bytes say ${format}; read as ${format}`);
}
expectLine("a file that is no document at all is data", docs, "WR1 ok False format None");
expectLine("a file that is no document at all says what it saw", docs, "WR1 problem not a docx, pptx or pdf: the file starts with b'take'");
expectLine("a spreadsheet is turned away, not parsed", docs, "WR2 ok False format None");
expectLine(
  "a spreadsheet is sent to the function that reads it",
  docs,
  "WR2 problem this is a spreadsheet, not a document: read it with xlsx_profile or sheets"
);
expectLine("a missing document does not raise", docs, "WR3 ok False reason could not be read");

// -------------------------------------------------------------------- 9. rdf
section("TEST 9: to_rdf, from_rdf, graph, terms");
const rdf = await run(
  "rdf",
  `${preamble}
NQ = oold_agent.to_rdf(INSTANCE, SCHEMA, format=oold_agent.NQUADS)
_lines = [ln for ln in NQ.strip().split("\\n") if ln.strip()]
print("RDF1 triples " + str(len(_lines)))
print("RDF1 subjects " + ",".join(sorted({ln.split(" ")[0] for ln in _lines})))
TTL = oold_agent.to_rdf(INSTANCE, SCHEMA)
print("RDF2 empty triples " + str(len([ln for ln in oold_agent.to_rdf(INSTANCE, [], format=oold_agent.NQUADS).strip().split("\\n") if ln.strip()])))

g = oold_agent.graph(NQ)
print("GRAPH1 nodes " + str(len(g["nodes"])))
print("GRAPH1 edges " + str(len(g["edges"])))
print("GRAPH1 node ids " + ",".join(n["id"] for n in g["nodes"]))
print("GRAPH1 edge " + g["edges"][0]["from"] + " -" + g["edges"][0]["label"] + "-> " + g["edges"][0]["to"])
g2 = oold_agent.graph(TTL, format=oold_agent.TURTLE)
print("GRAPH1 turtle nodes " + str(len(g2["nodes"])) + " edges " + str(len(g2["edges"])))
e = oold_agent.graph("")
print("GRAPH2 empty nodes " + str(len(e["nodes"])) + " edges " + str(len(e["edges"])))

t = oold_agent.terms(SCHEMA)
_cells = [c.split("<")[0].strip() for c in _re.findall(r"<code>(.*?)</code>", t, _re.S)]
print("TERMS1 cells " + "|".join(_cells))
print("TERMS2 empty " + oold_agent.terms([]))
print("TERMS2 nocontext " + oold_agent.terms({"title": "X", "type": "object"}))

back = oold_agent.from_rdf(NQ, SCHEMA, format=oold_agent.NQUADS)
print("ROUND1 " + json.dumps({k: back.get(k) for k in ("id", "sensor_id", "value", "unit")}, sort_keys=True))
empty = oold_agent.from_rdf("", SCHEMA, format=oold_agent.NQUADS)
print("ROUND2 keys " + ",".join(sorted(k for k in empty if not k.startswith("@"))) + "|")
`
);
console.log(rdf.stdout);
if (rdf.errors.length) console.log(rdf.errors.join("\n").slice(0, 3000));
cellOk("rdf", rdf);
expectLine("to_rdf positive", rdf, "RDF1 triples 4");
expectLine("to_rdf names the expected subject", rdf, "RDF1 subjects <https://example.org/measurement/m-001>");
// No schema chain means no @context, so nothing reaches RDF. The positive above
// would pass just as well against a to_rdf that ignored its arguments.
expectLine("to_rdf without a schema produces nothing", rdf, "RDF2 empty triples 0");
expectLine("graph positive", rdf, "GRAPH1 nodes 2");
expectLine("graph positive", rdf, "GRAPH1 edges 1");
expectLine(
  "graph keeps literals off the canvas",
  rdf,
  "GRAPH1 node ids http://qudt.org/schema/qudt/Quantity,https://example.org/measurement/m-001"
);
expectLine(
  "graph edge carries its predicate",
  rdf,
  "GRAPH1 edge https://example.org/measurement/m-001 -type-> http://qudt.org/schema/qudt/Quantity"
);
expectLine("graph reads turtle too", rdf, "GRAPH1 turtle nodes 2 edges 1");
expectLine("graph negative", rdf, "GRAPH2 empty nodes 0 edges 0");
expectLine(
  "terms positive",
  rdf,
  "TERMS1 cells id|@id|ex|https://example.org/measurement/|schema|http://schema.org/|qudt|http://qudt.org/schema/qudt/|sensor_id|schema:identifier|http://purl.org/dc/terms/identifier|value|qudt:numericValue|unit|qudt:unit|*"
);
expectLine("terms negative: no schema", rdf, "TERMS2 empty <p style='color:#999'>No schema to read.</p>");
expectLine(
  "terms negative: no @context",
  rdf,
  "TERMS2 nocontext <p style='color:#999'>This schema declares no <code>@context</code>.</p>"
);
expectLine("from_rdf positive", rdf, 'ROUND1 {"id": "ex:m-001", "sensor_id": "S-11", "unit": "degC", "value": 21.5}');
expectLine("from_rdf negative", rdf, "ROUND2 keys |");

// ----------------------------------------------------------- 10. diagnostics
section("DIAGNOSTICS");
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`failed requests: ${failedRequests.length}`);
failedRequests.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`HTTP >= 400 responses: ${badStatuses.length}`);
badStatuses.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
report.pageErrors = pageErrors;
report.failedRequests = failedRequests;
report.badStatuses = badStatuses;

check("no uncaught page errors over the whole run", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 5)));
const unexpected = badStatuses.filter((s) => !EXPECTED_404.some((re) => re.test(s)));
check("no unexpected HTTP error responses", unexpected.length === 0, JSON.stringify(unexpected.slice(0, 5)));

await page.screenshot({ path: `${ARTIFACTS}/kernel-api.png`, fullPage: false });
await browser.close();
finish(failures === 0 ? 0 : 1);
