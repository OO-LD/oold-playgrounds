import { test, expect, type Page, type ConsoleMessage } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";

const ARTIFACTS = "test-results/artifacts";

type Failure = { kind: string; detail: string };

let page: Page;
const consoleErrors: string[] = [];
const pageErrors: string[] = [];
const networkFailures: Failure[] = [];
const notes: string[] = [];

function record(label: string, value: unknown) {
  const rendered =
    typeof value === "string" ? value : JSON.stringify(value, null, 2);
  notes.push(`--- ${label} ---\n${rendered}`);
  console.log(`\n--- ${label} ---\n${rendered}`);
}

test.describe.configure({ mode: "serial" });

test.beforeAll(async ({ browser }) => {
  mkdirSync(ARTIFACTS, { recursive: true });
  page = await browser.newPage();

  page.on("console", (message: ConsoleMessage) => {
    if (message.type() === "error") {
      consoleErrors.push(message.text());
    }
  });
  page.on("pageerror", (error) => pageErrors.push(String(error)));
  page.on("requestfailed", (request) =>
    networkFailures.push({
      kind: "requestfailed",
      detail: `${request.url()} :: ${request.failure()?.errorText ?? "unknown"}`,
    }),
  );
  page.on("response", (response) => {
    if (response.status() >= 400) {
      networkFailures.push({
        kind: `http-${response.status()}`,
        detail: response.url(),
      });
    }
  });

  await page.goto("/", { waitUntil: "domcontentloaded" });
});

test.afterAll(async () => {
  writeFileSync(`${ARTIFACTS}/notes.txt`, notes.join("\n\n"));
  await page?.close();
});

test("1. page loads and ty initialises without uncaught errors", async () => {
  await expect(page.getByRole("heading", { name: "oold ty playground" })).toBeVisible();
  await expect(page.getByTestId("status")).not.toHaveText("booting", {
    timeout: 60_000,
  });

  const tyVersion = await page.getByTestId("ty-version").textContent();
  record("ty version reported by wasm", tyVersion ?? "(none)");

  expect(pageErrors, `uncaught page errors: ${pageErrors.join(" | ")}`).toEqual([]);
});

test("2. pyodide boots and the playground reaches ready", async () => {
  await expect(page.getByTestId("status")).toHaveText("ready", {
    timeout: 360_000,
  });

  const timings = await page.evaluate(() => window.__playground!.timings());
  const cold = {
    tyReadyMs: Math.round(timings.tyReady - timings.start),
    pyodideReadyMs: Math.round(timings.pyodideReady - timings.start),
    importsProbedMs: Math.round(timings.importsProbed - timings.start),
    injectedMs: Math.round(timings.injected - timings.start),
  };
  record("cold load timings from navigation start (ms)", cold);

  expect(cold.pyodideReadyMs).toBeGreaterThan(0);
  expect(cold.injectedMs).toBeGreaterThan(cold.pyodideReadyMs);
});

test("3. oold and each runtime dependency import inside pyodide", async () => {
  const results = await page.evaluate(async () => {
    const modules = [
      "oold",
      "oold.model._notation",
      "oold.backend.document_store",
      "rdflib",
      "pyld",
      "SPARQLWrapper",
      "jsondiff",
    ];
    const out: Record<string, { ok: boolean; detail: string }> = {};
    for (const name of modules) {
      const code = [
        "import json, importlib",
        "try:",
        `    mod = importlib.import_module("${name}")`,
        '    _r = {"ok": True, "detail": str(getattr(mod, "__version__", "n/a"))}',
        "except Exception as exc:",
        '    _r = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}',
        "json.dumps(_r)",
      ].join("\n");
      out[name] = JSON.parse(
        (await window.__playground!.runPython(code)) as string,
      );
    }
    return out;
  });

  record("per-package import results in pyodide", results);

  for (const [name, result] of Object.entries(results)) {
    expect(result.ok, `${name} failed to import: ${result.detail}`).toBe(true);
  }
});

test("4. notation_example.py runs and its self-checks pass", async () => {
  await page.getByTestId("tab-notation_example.py").click();
  await page.getByTestId("run").click();

  const output = page.getByTestId("output");
  await expect(output).toContainText("ALL CHECKS PASSED", { timeout: 120_000 });

  const stdout = (await output.textContent()) ?? "";
  record("notation_example.py stdout", stdout.trim());
  writeFileSync(`${ARTIFACTS}/notation_example_stdout.txt`, stdout);

  expect(stdout).toContain("knows[0].name          = Bob");
  expect(stdout).toContain("employer.name          = ACME");
  expect(stdout).toContain("friends[0].name        = Bob");
  expect(stdout).toContain("lists and to-one links round trip too");
  expect(stdout).not.toContain("Traceback");
});

test("5. monaco is mounted and ty reports diagnostics for the broken snippet", async () => {
  await page.getByTestId("tab-broken.py").click();
  await expect(page.locator(".monaco-editor").first()).toBeVisible();

  await expect
    .poll(async () => page.getByTestId("diagnostic").count(), {
      timeout: 60_000,
    })
    .toBeGreaterThan(0);

  const diagnostics = await page.getByTestId("diagnostic").allInnerTexts();
  record("ty diagnostics for broken.py", diagnostics);

  const joined = diagnostics.join("\n");
  expect(joined).toContain("invalid-assignment");
  expect(joined).toMatch(/definitely not an int|`str`/);

  const markers = await page.evaluate(() => {
    const w = window as unknown as {
      monaco?: { editor: { getModelMarkers(f: object): unknown[] } };
    };
    return w.monaco ? w.monaco.editor.getModelMarkers({}).length : -1;
  });
  record("monaco model markers count (-1 means monaco global not exposed)", markers);

  const squiggles = await page.locator(".squiggly-error").count();
  record("rendered squiggly-error decorations", squiggles);
  expect(squiggles).toBeGreaterThan(0);
});

test("6. injected site-packages are import-resolvable from a separate buffer", async () => {
  const result = await page.evaluate(() => {
    const playground = window.__playground!;
    const workspace = playground.workspace;

    const nested = workspace.openFile("probe_nested/mod.py", "VALUE = 1\n");
    const consumer = workspace.openFile(
      "probe_consumer.py",
      "from probe_nested.mod import VALUE\nx: int = VALUE\n",
    );

    const oold = workspace.openFile(
      "probe_oold.py",
      [
        "from oold.model._notation import Link, OoldField, OoldModel",
        "from pydantic import Field",
        "",
        "",
        "class Thing(OoldModel):",
        "    id: str",
        "    label: str | None = None",
        "",
        "",
        "thing = Thing(id='x')",
        "reveal = thing.label",
        "",
      ].join("\n"),
    );

    const describe = (handle: unknown) =>
      workspace
        .checkFile(handle as never)
        .map((diagnostic) => `${diagnostic.id()} ${diagnostic.message()}`);

    return {
      nestedPath: nested.path(),
      consumerDiagnostics: describe(consumer),
      oooldDiagnostics: describe(oold),
    };
  });

  record("import resolution across separate editor buffers", result);

  expect(result.nestedPath).toBe("/probe_nested/mod.py");
  expect(result.consumerDiagnostics).toEqual([]);
  expect(
    result.oooldDiagnostics.filter((line) => line.startsWith("unresolved-import")),
    `injected oold/pydantic did not resolve: ${result.oooldDiagnostics.join(" | ")}`,
  ).toEqual([]);
});

test("7. completion after Person. lists the declared oold model fields", async () => {
  await page.getByTestId("tab-notation_example.py").click();
  await expect(page.locator(".monaco-editor").first()).toBeVisible();

  await page.locator(".monaco-editor .view-lines").first().click();
  await page.keyboard.press("Control+End");
  await page.keyboard.press("Enter");
  await page.keyboard.type("Person", { delay: 120 });
  await page.waitForTimeout(500);
  await page.keyboard.type(".", { delay: 120 });
  await page.waitForTimeout(2500);

  const position = await page.evaluate(() => {
    const playground = window.__playground!;
    const handle = playground.handles["notation_example.py"];
    const text = playground.workspace.sourceText(handle);
    const lines = text.split("\n");
    for (let i = lines.length - 1; i >= 0; i -= 1) {
      if (lines[i].trim() === "Person.") {
        return { line: i + 1, column: lines[i].length + 1, snippet: lines[i] };
      }
    }
    return { line: -1, column: -1, snippet: lines.slice(-3).join("\\n") };
  });
  record("resolved Person. position in the ty workspace", position);
  expect(position.line, "Person. was not written into the ty workspace").toBeGreaterThan(0);

  const completions = (await page.evaluate(
    ([line, column]) =>
      window.__playground!.completionsAt("notation_example.py", line, column),
    [position.line, position.column],
  )) as { name: string; kind: number | undefined; detail: string | null }[];

  const names = completions.map((completion) => completion.name);
  record(`ty completions for Person. (${names.length} items)`, names);
  writeFileSync(
    `${ARTIFACTS}/completions_person.json`,
    JSON.stringify(completions, null, 2),
  );

  const suggestVisible = await page
    .locator(".suggest-widget.visible")
    .isVisible()
    .catch(() => false);
  const suggestItems = suggestVisible
    ? await page.locator(".suggest-widget.visible .monaco-list-row").allInnerTexts()
    : [];
  record("monaco suggest widget rows", { suggestVisible, suggestItems });
  writeFileSync(
    `${ARTIFACTS}/suggest_widget.json`,
    JSON.stringify({ suggestVisible, suggestItems }, null, 2),
  );

  await page.screenshot({
    path: `${ARTIFACTS}/completions.png`,
    fullPage: false,
  });

  for (const field of ["knows", "employer", "friends", "location"]) {
    expect(
      names,
      `field "${field}" missing from ty completions; got: ${names.join(", ")}`,
    ).toContain(field);
  }
});

test("8. hover on an oold symbol returns non-empty markdown", async () => {
  const hovers = await page.evaluate(() => {
    const playground = window.__playground!;
    const handle = playground.handles["notation_example.py"];
    const text = playground.workspace.sourceText(handle);
    const lines = text.split("\n");

    const targets = [
      { symbol: "OoldModel", match: "class Person(OoldModel):" },
      { symbol: "OoldField", match: "knows: list[\"Person\"] | None = OoldField()" },
      { symbol: "Link", match: "employer: Link[Organization] | None = Field(default=None)" },
    ];

    const out: Record<string, { found: boolean; markdown: string | null }> = {};
    for (const target of targets) {
      const index = lines.findIndex((line) => line.trim() === target.match);
      if (index === -1) {
        out[target.symbol] = { found: false, markdown: null };
        continue;
      }
      const column = lines[index].indexOf(target.symbol) + 2;
      out[target.symbol] = {
        found: true,
        markdown: playground.hoverAt("notation_example.py", index + 1, column),
      };
    }
    return out;
  });

  record("hover markdown for oold symbols", hovers);
  writeFileSync(`${ARTIFACTS}/hovers.json`, JSON.stringify(hovers, null, 2));

  expect(hovers.OoldModel.found).toBe(true);
  expect(hovers.OoldModel.markdown ?? "").not.toBe("");
  expect(hovers.OoldField.markdown ?? "").not.toBe("");
});

test("9. workspace injection stats and final screenshot", async () => {
  const injection = await page.evaluate(() => window.__playground!.injection());
  record("site-packages injected into the ty workspace", injection);

  expect(injection!.files).toBeGreaterThan(50);

  await page.getByTestId("tab-notation_example.py").click();
  await page.waitForTimeout(500);
  await page.screenshot({ path: `${ARTIFACTS}/playground.png`, fullPage: false });

  record("console errors", consoleErrors.length === 0 ? "(none)" : consoleErrors);
  record("page errors", pageErrors.length === 0 ? "(none)" : pageErrors);
  record(
    "network failures",
    networkFailures.length === 0 ? "(none)" : networkFailures,
  );

  expect(pageErrors, `uncaught page errors: ${pageErrors.join(" | ")}`).toEqual([]);
  expect(
    networkFailures,
    `failed network requests: ${networkFailures.map((f) => `${f.kind} ${f.detail}`).join(" | ")}`,
  ).toEqual([]);
  expect(
    consoleErrors,
    `console errors: ${consoleErrors.join(" | ")}`,
  ).toEqual([]);
});
