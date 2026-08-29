import { chromium } from "playwright";
const browser = await chromium.launch();
const page = await browser.newPage();
page.on("pageerror", (e) => console.log("[pageerror]", String(e).slice(0, 500)));
await page.goto("http://localhost:5173/", { waitUntil: "domcontentloaded" });
await page.getByTestId("status").filter({ hasText: "ready" }).waitFor({ timeout: 180000 });
console.log("status ready");

console.log("monaco-editor count:", await page.locator(".monaco-editor").count());
console.log("textarea.inputarea count:", await page.locator("textarea.inputarea").count());
console.log("native-edit-context count:", await page.locator(".native-edit-context").count());
console.log("all textareas:", await page.locator("textarea").count());
console.log("monaco classes:", await page.locator(".monaco-editor").first().getAttribute("class"));

// Directly test injection resolvability + completions without keyboard
const probe = await page.evaluate(async () => {
  const pg = window.__playground;
  const ws = pg.workspace;
  const inj = pg.injection();

  // A fresh buffer that imports from an injected module
  const probeHandle = ws.openFile("probe_import.py", [
    "from oold.model._notation import OoldModel, OoldField, Link",
    "from pydantic import Field",
    "",
    "class Thing(OoldModel):",
    "    id: str",
    "    label: str | None = None",
    "",
    "t = Thing(id='x')",
    "t.",
    "",
  ].join("\n"));

  const diags = ws.checkFile(probeHandle).map((d) => `${d.id()} ${d.message()}`);

  const P = pg.TyPosition;
  const compl = ws.completions(probeHandle, new P(9, 3)).map((c) => c.name);

  return { injection: inj, probeDiagnostics: diags, probeCompletions: compl.slice(0, 40), probeCount: compl.length };
});
console.log(JSON.stringify(probe, null, 2));

// Now Person. in the real file, by editing the workspace directly
const person = await page.evaluate(() => {
  const pg = window.__playground;
  const ws = pg.workspace;
  const h = pg.handles["notation_example.py"];
  const text = ws.sourceText(h);
  const updated = text + "\nPerson.\n";
  ws.updateFile(h, updated);
  const lines = updated.split("\n");
  const idx = lines.findIndex((l) => l.trim() === "Person.");
  const P = pg.TyPosition;
  const t0 = performance.now();
  const compl = ws.completions(h, new P(idx + 1, 8));
  const ms = Math.round(performance.now() - t0);
  return { line: idx + 1, ms, count: compl.length, names: compl.map((c) => c.name) };
});
console.log("Person. completions:", JSON.stringify({line: person.line, ms: person.ms, count: person.count}, null, 2));
console.log("names:", person.names.join(", "));
await browser.close();
