import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage();
page.on("pageerror", (e) => console.log("[pageerror]", String(e).slice(0, 600)));
page.on("console", (m) => { if (m.type()==="error") console.log("[console:error]", m.text().slice(0,300)); });
await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
await page.waitForFunction(() => document.querySelector('[data-testid="status"]')?.textContent === "ready", null, { timeout: 240000 });
console.log("UI injection text:", await page.getByTestId("injection").textContent());

const r = await page.evaluate(async () => {
  const pg = window.__playground;
  const out = { statusFn: pg.status(), injectionFn: pg.injection() };

  const trials = ["nested/mod.py", "a/b/c/deep.py", "top_level.py"];
  out.openFileTrials = {};
  for (const p of trials) {
    try { out.openFileTrials[p] = "ok -> " + pg.workspace.openFile(p, "VALUE = 1\n").path(); }
    catch (e) { out.openFileTrials[p] = "THREW: " + String(e).slice(0, 200); }
  }

  const h2 = pg.workspace.openFile("probe_nested.py", "from nested.mod import VALUE\nx = VALUE\n");
  out.nestedImportDiagnostics = pg.workspace.checkFile(h2).map(d => `${d.id()} ${d.message()}`);

  const h3 = pg.workspace.openFile("probe_oold.py", [
    "from oold.model._notation import OoldModel, OoldField, Link",
    "from pydantic import Field",
    "",
    "class Thing(OoldModel):",
    "    id: str",
    "    label: str | None = None",
    "",
    "t = Thing(id='x')",
    "t.",
  ].join("\n"));
  out.oooldImportDiagnostics = pg.workspace.checkFile(h3).map(d => `${d.id()} ${d.message()}`);
  const P = pg.TyPosition;
  const c3 = pg.workspace.completions(h3, new P(9, 3));
  out.instanceCompletions = { count: c3.length, names: c3.map(c => c.name).filter(n => !n.startsWith("__")) };

  const h = pg.handles["notation_example.py"];
  const text = pg.workspace.sourceText(h);
  pg.workspace.updateFile(h, text + "\nPerson.\n");
  const idx = (text + "\nPerson.\n").split("\n").findIndex(l => l.trim() === "Person.");
  const t0 = performance.now();
  const cp = pg.workspace.completions(h, new P(idx + 1, 8));
  out.personCompletions = { ms: Math.round(performance.now()-t0), count: cp.length, names: cp.map(c=>c.name).filter(n=>!n.startsWith("__")) };
  pg.workspace.updateFile(h, text);

  out.hover = pg.hoverAt("notation_example.py", (() => {
    const lines = text.split("\n");
    return lines.findIndex(l => l.trim() === "class Person(OoldModel):") + 1;
  })(), text.split("\n").find(l=>l.trim()==="class Person(OoldModel):").indexOf("OoldModel") + 2);

  return out;
});
console.log(JSON.stringify(r, null, 2));

console.log("\n=== DOM selectors ===");
for (const sel of [".monaco-editor", "textarea.inputarea", ".monaco-editor .view-lines", ".native-edit-context"]) {
  console.log(sel, "=>", await page.locator(sel).count());
}
await browser.close();
