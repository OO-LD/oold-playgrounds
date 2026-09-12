import { chromium } from "playwright";

const BASE = process.env.BASE ?? "http://127.0.0.1:8899";
const results = [];
let failures = 0;
const check = (n, ok, d) => {
  results.push({ n, ok, d });
  if (!ok) failures++;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${n}${d ? ` :: ${d}` : ""}`);
};

const browser = await chromium.launch();
const page = await (await browser.newContext()).newPage();
await page.goto(`${BASE}/notebooks/index.html?path=oold_demo.ipynb`, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".jp-Notebook", { timeout: 180000 });
await page.evaluate(async () => {
  const p = window.jupyterapp.shell.currentWidget;
  await p.sessionContext.ready;
  await p.sessionContext.session.kernel.info;
});

const exec = async (code) =>
  page.evaluate(async (code) => {
    const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
    const f = k.requestExecute({ code, stop_on_error: false });
    let out = "";
    let err = "";
    f.onIOPub = (m) => {
      if (m.header.msg_type === "stream") out += m.content.text;
      if (m.header.msg_type === "error") err += `${m.content.ename}: ${m.content.evalue}`;
    };
    const r = await f.done;
    return { out, err, status: r.content.status };
  }, code);

console.log("--- what does the kernel see on its filesystem? ---");
const ls = await exec(`
import os, sys
print("cwd:", os.getcwd())
print("listdir:", sorted(os.listdir(".")))
print("sys.path[0:4]:", sys.path[:4])
`);
console.log(ls.out || ls.err);

console.log("--- import a sibling module shipped in contents/ ---");
const imp = await exec(`
try:
    import oold_helpers
    print("imported:", oold_helpers.__file__)
    print("qualify('Person') ->", oold_helpers.qualify("Person"))
    print("HelperMarker.origin ->", oold_helpers.HelperMarker.origin)
    OK = True
except Exception as e:
    print("FAILED:", type(e).__name__, e)
    OK = False
`);
console.log(imp.out || imp.err);
check("sibling .py in contents/ is importable by the kernel", imp.out.includes("imported:"), imp.out.trim().split("\n").pop());

if (imp.out.includes("imported:")) {
  const comp = await page.evaluate(async () => {
    const k = window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
    const r = await k.requestComplete({ code: "oold_helpers.", cursor_pos: 13 });
    return r.content.matches ?? [];
  });
  console.log(`completion on the imported module: ${JSON.stringify(comp.filter((m) => !m.startsWith("_")))}`);
  check(
    "completion works across the file boundary",
    ["qualify", "HelperMarker", "SCHEMA_BASE"].every((w) => comp.includes(w)),
    JSON.stringify(comp)
  );
}

console.log("\n--- does the site ship the file at all? ---");
const r = await fetch(`${BASE}/files/oold_helpers.py`);
console.log(`GET /files/oold_helpers.py -> ${r.status}`);
check("contents file is served as a static asset", r.status === 200, `status=${r.status}`);

console.log(`\n${results.length - failures}/${results.length} passed, ${failures} failed`);
await browser.close();
