// Tier 2, opt-in: the same loop as tests/agent-loop.mjs but with a real model.
// It costs tokens, it is not deterministic and it is never required for a merge.
// Without a key in the environment it skips loudly and exits 0.
//
// What it is for: provider differences in tool-call shape. A transcript that a
// stub replays perfectly can still fall apart against a provider that nests its
// arguments differently, which is why every configured provider is run.
//
//   OPENAI_API_KEY               [OPENAI_MODEL]
//   ANTHROPIC_API_KEY            [ANTHROPIC_MODEL]
//   GENERIC_API_KEY              GENERIC_BASE_URL GENERIC_MODEL
//   AZURE_OPENAI_API_KEY         AZURE_OPENAI_BASE_URL AZURE_OPENAI_DEPLOYMENT
//                                [AZURE_OPENAI_API_VERSION]
//   AZURE_FOUNDRY_API_KEY        AZURE_FOUNDRY_BASE_URL AZURE_FOUNDRY_MODEL
//                                [AZURE_FOUNDRY_API_VERSION]
//
// The Azure entries also honour AZURE_OPENAI_SETTINGS and AZURE_FOUNDRY_SETTINGS,
// a JSON object merged over the plugin settings. A live call that answers 401 or
// 404 is a configuration question, not a TypeScript one: turn apiKeyHeader,
// dropDefaultAuthHeaders, apiVersion, pathPattern, pathReplacement, headers and
// query until the request matches, and report the values that worked.

import { chromium } from "playwright";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8902";
const NB = process.env.NB ?? "start.ipynb";
const PAGE_URL = `${BASE}/lab/index.html?path=${NB}`;
const HEADLESS = process.env.HEADED !== "1";
const KERNEL_TIMEOUT = Number(process.env.KERNEL_TIMEOUT ?? 180000);
const EXEC_TIMEOUT = Number(process.env.EXEC_TIMEOUT ?? 300000);
const SETUP_TIMEOUT = Number(process.env.SETUP_TIMEOUT ?? 600000);
const ANSWER_TIMEOUT = Number(process.env.ANSWER_TIMEOUT ?? 900000);
const SUITE_TIMEOUT = Number(process.env.SUITE_TIMEOUT ?? 3600000);

const ROOT = new globalThis.URL("../", import.meta.url).pathname.replace(/^\//, "");
const ARTIFACTS = `${ROOT}artifacts`;
const BRIEF = readFileSync(`${ROOT}contents/OOLD-AGENT.md`, "utf-8");

const CHAT_PANEL = "@jupyterlite/ai:chat-panel";
const COMPOSER = ".jp-chat-sidepanel-widget textarea:visible";
const SETTINGS_TOKEN = "@jupyternaut/agent:IAISettingsModel";
const SETTING_REGISTRY_TOKEN = "@jupyterlab/coreutils:ISettingRegistry";
const AZURE_PLUGIN_ID = "oold-azure-providers:plugin";
const DESCRIBE_COMMAND = "oold-azure-providers:describe";

const LIVE_REPO = "SINTEF/physmet-data-documentation-templates";
const CSV_DIR = "gh/shared";
const CSV_FILE = `${CSV_DIR}/people.csv`;

mkdirSync(ARTIFACTS, { recursive: true });

function parseJsonEnv(name) {
  const raw = process.env[name];
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch (e) {
    console.log(`${name} is not valid JSON, ignored: ${e}`);
    return {};
  }
}

function configured() {
  const out = [];
  if (process.env.OPENAI_API_KEY) {
    out.push({
      id: "openai",
      provider: "openai",
      model: process.env.OPENAI_MODEL ?? "gpt-4o",
      apiKey: process.env.OPENAI_API_KEY,
    });
  }
  if (process.env.ANTHROPIC_API_KEY) {
    out.push({
      id: "anthropic",
      provider: "anthropic",
      model: process.env.ANTHROPIC_MODEL ?? "claude-sonnet-4-5",
      apiKey: process.env.ANTHROPIC_API_KEY,
    });
  }
  if (process.env.GENERIC_API_KEY && process.env.GENERIC_BASE_URL) {
    out.push({
      id: "generic",
      provider: "generic",
      model: process.env.GENERIC_MODEL ?? "gpt-4o",
      apiKey: process.env.GENERIC_API_KEY,
      baseURL: process.env.GENERIC_BASE_URL,
    });
  }
  if (process.env.AZURE_OPENAI_API_KEY && process.env.AZURE_OPENAI_BASE_URL) {
    const deployment = process.env.AZURE_OPENAI_DEPLOYMENT ?? "gpt-4o";
    out.push({
      id: "azure-openai",
      provider: "azure-openai",
      model: deployment,
      apiKey: process.env.AZURE_OPENAI_API_KEY,
      baseURL: process.env.AZURE_OPENAI_BASE_URL,
      pluginKey: "azureOpenAI",
      pluginSettings: {
        baseUrl: process.env.AZURE_OPENAI_BASE_URL,
        deployment,
        apiVersion: process.env.AZURE_OPENAI_API_VERSION ?? "2025-04-01-preview",
        apiVersionParam: "api-version",
        apiStyle: "chat",
        useDeploymentBasedUrls: true,
        apiKeyHeader: "api-key",
        dropDefaultAuthHeaders: false,
        models: [],
        headers: {},
        query: {},
        pathPattern: "",
        pathReplacement: "",
        ...parseJsonEnv("AZURE_OPENAI_SETTINGS"),
      },
    });
  }
  if (process.env.AZURE_FOUNDRY_API_KEY && process.env.AZURE_FOUNDRY_BASE_URL) {
    const model = process.env.AZURE_FOUNDRY_MODEL ?? "claude-sonnet-4-5";
    out.push({
      id: "azure-foundry-anthropic",
      provider: "azure-foundry-anthropic",
      model,
      apiKey: process.env.AZURE_FOUNDRY_API_KEY,
      baseURL: process.env.AZURE_FOUNDRY_BASE_URL,
      pluginKey: "azureFoundryAnthropic",
      pluginSettings: {
        baseUrl: process.env.AZURE_FOUNDRY_BASE_URL,
        model,
        apiVersion: process.env.AZURE_FOUNDRY_API_VERSION ?? "2024-12-01-preview",
        apiVersionParam: "api-version",
        apiKeyHeader: "api-key",
        dropDefaultAuthHeaders: true,
        anthropicVersion: "2023-06-01",
        anthropicBeta: [],
        allowDirectBrowserAccess: true,
        models: [],
        headers: {},
        query: {},
        pathPattern: "",
        pathReplacement: "",
        ...parseJsonEnv("AZURE_FOUNDRY_SETTINGS"),
      },
    });
  }
  return out;
}

const PROVIDERS = configured();

const report = { url: PAGE_URL, skipped: false, providers: PROVIDERS.map((p) => p.id), tests: [], runs: {} };
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
  writeFileSync(`${ARTIFACTS}/live-agent.json`, JSON.stringify(report, null, 2));
  section("SUMMARY");
  for (const t of report.tests) console.log(`${t.ok ? "PASS" : "FAIL"}  ${t.name}`);
  console.log(`\n${report.tests.length - failures}/${report.tests.length} passed, ${failures} failed`);
  process.exit(code);
}

if (PROVIDERS.length === 0) {
  const bar = "#".repeat(72);
  console.log(bar);
  console.log("# live-agent.mjs SKIPPED: no provider key in the environment");
  console.log("#");
  console.log("# This suite calls a real model and costs tokens. It is opt-in and never");
  console.log("# required for a merge. Nothing was verified by this run.");
  console.log("#");
  console.log("# Set one of:");
  console.log("#   OPENAI_API_KEY [OPENAI_MODEL]");
  console.log("#   ANTHROPIC_API_KEY [ANTHROPIC_MODEL]");
  console.log("#   GENERIC_API_KEY GENERIC_BASE_URL [GENERIC_MODEL]");
  console.log("#   AZURE_OPENAI_API_KEY AZURE_OPENAI_BASE_URL AZURE_OPENAI_DEPLOYMENT");
  console.log("#   AZURE_FOUNDRY_API_KEY AZURE_FOUNDRY_BASE_URL AZURE_FOUNDRY_MODEL");
  console.log(bar);
  report.skipped = true;
  report.reason = "no provider key in the environment";
  writeFileSync(`${ARTIFACTS}/live-agent.json`, JSON.stringify(report, null, 2));
  process.exit(0);
}

const watchdog = setTimeout(() => {
  check("suite completed within SUITE_TIMEOUT", false, `${SUITE_TIMEOUT} ms elapsed`);
  finish(1);
}, SUITE_TIMEOUT);
watchdog.unref();

section(`LIVE: ${PROVIDERS.length} provider(s) configured: ${PROVIDERS.map((p) => p.id).join(", ")}`);

const marker = (out, line) => out.stdout.split("\n").some((l) => l.trimEnd() === line);
const field = (out, key) => {
  const hit = out.stdout.split("\n").map((l) => l.trimEnd()).find((l) => l === key || l.startsWith(`${key} `));
  return hit === undefined ? null : hit.slice(key.length).trim();
};

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await context.newPage();

const pageErrors = [];
const providerResponses = [];
page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));
page.on("response", (r) => {
  const url = r.url();
  if (/azure\.com|openai\.com|anthropic\.com|microsoft\.com|models\.ai\.azure\.com/.test(url)) {
    // The URL and the status, never a header: an artifact file is one more place
    // a key could end up.
    providerResponses.push({ status: r.status(), url: url.split("?")[0], query: new URL(url).search });
  }
});

// ------------------------------------------------------------- kernel setup
section("KERNEL: boot, install oold, pull the SINTEF CSVs");
const resp = await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
check("HTTP 200 for lab app", resp.status() === 200, `status=${resp.status()}`);
await page.waitForSelector(".jp-Notebook", { timeout: 180000 });

const kernelInfo = await page.evaluate(async (timeoutMs) => {
  const deadline = Date.now() + timeoutMs;
  const byDeadline = (p, what) =>
    Promise.race([p, new Promise((_, rej) => setTimeout(() => rej(new Error(`${what} did not settle`)), Math.max(0, deadline - Date.now())))]);
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
  return { id: k.id, status: k.status };
}, KERNEL_TIMEOUT);
check("kernel reached idle", kernelInfo.status === "idle", kernelInfo.error ?? kernelInfo.id);
if (kernelInfo.error) {
  await browser.close();
  finish(1);
}

await page.evaluate((defaultTimeout) => {
  window.__kernel = () => window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  window.__exec = async (code, timeoutMs) => {
    const future = window.__kernel().requestExecute({ code, stop_on_error: false });
    const out = { stdout: "", results: [], errors: [] };
    future.onIOPub = (msg) => {
      const t = msg.header.msg_type;
      if (t === "stream") out.stdout += msg.content.text;
      else if (t === "execute_result" || t === "display_data") out.results.push(msg.content.data["text/plain"] ?? "");
      else if (t === "error") out.errors.push(`${msg.content.ename}: ${msg.content.evalue}`);
    };
    const timedOut = Symbol("timeout");
    const reply = await Promise.race([future.done, new Promise((r) => setTimeout(() => r(timedOut), timeoutMs ?? defaultTimeout))]);
    out.status = reply === timedOut ? "timeout" : reply.content.status;
    return out;
  };
  window.__token = (name) => {
    const app = window.jupyterapp;
    const registry = app.pluginRegistry ?? app._pluginRegistry;
    const found = [...registry._services.keys()].find((t) => t?.name === name);
    if (!found) throw new Error(`no token named ${name}`);
    return found;
  };
  window.__service = (name) => window.jupyterapp.resolveRequiredService(window.__token(name));
}, EXEC_TIMEOUT);

const run = async (label, code, timeoutMs) => {
  const out = await page.evaluate(([c, t]) => window.__exec(c, t), [code, timeoutMs ?? EXEC_TIMEOUT]);
  console.log(`--- ${label} ---`);
  console.log(out.stdout.trimEnd());
  if (out.errors.length) console.log(out.errors.join("\n").slice(0, 1500));
  return out;
};

const boot = await run(
  "boot",
  `
import json, os
import piplite
await piplite.install(["requests", "pyodide-http"], keep_going=True)
import oold_agent, oold_git
_info = await oold_agent.setup(verbose=False)
print("BOOT oold " + str(_info["oold"]))
print("BOOT validation " + str(_info["validation"]))
_pull = oold_git.pull("${LIVE_REPO}", into="gh", paths=["shared/*.csv", "context/context.json"])
print("BOOT pull " + str(_pull["ok"]) + " " + str(_pull.get("reason")))
print("BOOT csv " + str(os.path.exists("${CSV_FILE}")))
print("BOOT rate " + str(_pull.get("rate", {}).get("remaining")))
`,
  SETUP_TIMEOUT
);
check("boot cell raised nothing", boot.errors.length === 0, boot.errors.join(" ").slice(0, 300));
check("oold is installed in the kernel", marker(boot, "BOOT validation True"), field(boot, "BOOT oold") ?? "");
check(`${CSV_FILE} was pulled from ${LIVE_REPO}`, marker(boot, "BOOT csv True"), `pull=${field(boot, "BOOT pull")} rate=${field(boot, "BOOT rate")}`);
if (!marker(boot, "BOOT csv True")) {
  await browser.close();
  finish(1);
}

// The key is written into the settings file rather than into the secrets manager.
// The secrets manager needs the token of the plugin that owns the namespace,
// which a test driving the app from outside does not have.
await page.evaluate(
  async ({ token, systemPrompt }) => {
    const settings = await window.__service(token);
    await settings.updateConfig({ useSecretsManager: false, systemPrompt, toolsEnabled: true });
  },
  { token: SETTINGS_TOKEN, systemPrompt: BRIEF }
);

await page.evaluate(async (id) => {
  await window.jupyterapp.commands.execute("@jupyterlite/ai:open-chat");
  window.jupyterapp.shell.activateById(id);
}, CHAT_PANEL);
await page.waitForSelector(".jp-chat-add jp-button", { timeout: 60000 });

const PROMPT = [
  `Read ${CSV_FILE} with oold_agent.csv_profile.`,
  "Write an OO-LD schema for it to live_Person.schema.json and one instance built from the",
  "first data row to live_Person.instance.json.",
  "Validate both with oold_agent.validate_schema and oold_agent.validate_instance and fix",
  "anything that fails, until both reports pass.",
  "Run everything in the kernel with jupyterlab-ai-commands:execute-in-kernel. There is no shell.",
].join(" ");

for (const provider of PROVIDERS) {
  section(`PROVIDER ${provider.id}`);
  const run_report = { provider: provider.id, model: provider.model };
  report.runs[provider.id] = run_report;

  if (provider.pluginSettings) {
    const applied = await page.evaluate(
      async ({ token, pluginId, key, value }) => {
        try {
          const registry = await window.__service(token);
          const loaded = await registry.load(pluginId);
          await loaded.set(key, value);
          await new Promise((r) => setTimeout(r, 300));
          return { ok: true };
        } catch (e) {
          return { ok: false, error: String(e) };
        }
      },
      { token: SETTING_REGISTRY_TOKEN, pluginId: AZURE_PLUGIN_ID, key: provider.pluginKey, value: provider.pluginSettings }
    );
    check(`${provider.id}: plugin settings applied`, applied.ok === true, applied.error ?? provider.pluginKey);
    const described = await page.evaluate(async (cmd) => {
      try {
        return await window.jupyterapp.commands.execute(cmd);
      } catch (e) {
        return { error: String(e) };
      }
    }, DESCRIBE_COMMAND);
    run_report.describe = described?.config?.[provider.pluginKey] ?? described;
    console.log(`resolved config: ${JSON.stringify(run_report.describe)}`);
  }

  const before = await run(`${provider.id}: files before`, `
import glob
print("FILES " + ",".join(sorted(glob.glob("*.schema.json") + glob.glob("*.instance.json"))))
`);
  const filesBefore = (field(before, "FILES") ?? "").split(",").filter(Boolean);

  const chosen = await page.evaluate(
    async ({ token, entry }) => {
      try {
        const settings = await window.__service(token);
        const id = await settings.addProvider({
          name: `live-${entry.provider}`,
          provider: entry.provider,
          model: entry.model,
          apiKey: entry.apiKey,
          ...(entry.baseURL ? { baseURL: entry.baseURL } : {}),
        });
        await settings.setActiveProvider(id);
        await new Promise((r) => setTimeout(r, 400));
        return { ok: true, id, keyPresent: settings.getApiKey(id).length > 0 };
      } catch (e) {
        return { ok: false, error: String(e) };
      }
    },
    { token: SETTINGS_TOKEN, entry: provider }
  );
  check(`${provider.id}: provider configuration added`, chosen.ok === true, chosen.ok ? chosen.id : chosen.error);
  check(`${provider.id}: the api key is readable by the agent`, chosen.keyPresent === true, "settings.getApiKey");
  if (!chosen.ok) continue;

  providerResponses.length = 0;
  await page.click(".jp-chat-add jp-button");
  await page.waitForSelector(COMPOSER, { timeout: 60000 });
  await page.fill(COMPOSER, PROMPT);
  await page.keyboard.press("Enter");

  const answered = await page.evaluate(
    async ({ panelId, timeoutMs }) => {
      const find = () => {
        for (const area of ["main", "left", "right"]) {
          for (const w of window.jupyterapp.shell.widgets(area)) if (w.id === panelId) return w;
        }
        return null;
      };
      const model = find()?.current?.model;
      if (!model) return { error: "no chat model" };
      const deadline = Date.now() + timeoutMs;
      // Give the first request a moment to make the chat busy before waiting for
      // it to stop being busy, otherwise the poll ends immediately.
      await new Promise((r) => setTimeout(r, 3000));
      while (model.isBusy && Date.now() < deadline) await new Promise((r) => setTimeout(r, 1000));
      return { busy: model.isBusy, timedOut: Date.now() >= deadline };
    },
    { panelId: CHAT_PANEL, timeoutMs: ANSWER_TIMEOUT }
  );
  run_report.answered = answered;
  run_report.providerResponses = [...providerResponses];
  console.log(`chat finished: ${JSON.stringify(answered)}`);
  const bad = providerResponses.filter((r) => r.status >= 400);
  check(`${provider.id}: the chat finished its turn`, answered.timedOut !== true && !answered.error, JSON.stringify(answered));
  check(
    `${provider.id}: no 4xx or 5xx from the provider endpoint`,
    bad.length === 0,
    // A 401 or a 404 here is a settings question. Turn apiKeyHeader,
    // dropDefaultAuthHeaders, apiVersion, pathPattern, pathReplacement, headers
    // and query until the request matches, then report the values that worked.
    JSON.stringify(bad.slice(0, 4))
  );

  const verdict = await run(
    `${provider.id}: verdict`,
    `
import glob, json, os
_before = set(${JSON.stringify(filesBefore)})
_schemas = sorted(set(glob.glob("*.schema.json")) - _before)
_instances = sorted(set(glob.glob("*.instance.json")) - _before)
print("NEW schemas " + ",".join(_schemas) + "|")
print("NEW instances " + ",".join(_instances) + "|")
if _schemas:
    _schema = _schemas[0]
    _text = oold_agent.oold_cli("validate", _schema)
    for _line in _text.splitlines():
        _s = _line.strip()
        if _s.startswith(("PASS", "FAIL", "WARN", "Error:", "exit code")):
            print("CLI " + " ".join(_s.split()))
    _r = oold_agent.validate_schema(_schema)
    print("SCHEMA passed " + str(_r.passed))
    print("SCHEMA failures " + ",".join(sorted(c.id for c in _r.failures())) + "|")
    if _instances:
        _instance = _instances[0]
        _vi = oold_agent.validate_instance(_instance, _schema)
        print("INSTANCE passed " + str(_vi.passed))
        print("INSTANCE failures " + ",".join(sorted(c.id for c in _vi.failures())) + "|")
        _nq = oold_agent.to_rdf(
            json.load(open(_instance, encoding="utf-8")),
            json.load(open(_schema, encoding="utf-8")),
            format=oold_agent.NQUADS,
        )
        print("RDF triples " + str(len([l for l in _nq.strip().split("\\n") if l.strip()])))
print("VERDICT DONE")
`
  );
  run_report.verdict = verdict.stdout;
  const schemas = (field(verdict, "NEW schemas") ?? "|").replace(/\|$/, "").split(",").filter(Boolean);
  const instances = (field(verdict, "NEW instances") ?? "|").replace(/\|$/, "").split(",").filter(Boolean);

  check(`${provider.id}: the agent wrote a .schema.json`, schemas.length > 0, JSON.stringify(schemas));
  check(`${provider.id}: the agent wrote an .instance.json`, instances.length > 0, JSON.stringify(instances));
  check(`${provider.id}: oold_cli validate reports exit code 0`, marker(verdict, "CLI exit code 0"), field(verdict, "CLI exit code") ?? "no exit code line");
  check(
    `${provider.id}: the cli names the schema as passing`,
    verdict.stdout.split("\n").some((l) => l.trimEnd() === `CLI PASS ${schemas[0]}`),
    schemas.length ? `expected "CLI PASS ${schemas[0]}"` : "no schema written"
  );
  check(`${provider.id}: validate_schema passes`, field(verdict, "SCHEMA passed") === "True", `failures=${field(verdict, "SCHEMA failures")}`);
  check(`${provider.id}: validate_instance passes`, field(verdict, "INSTANCE passed") === "True", `failures=${field(verdict, "INSTANCE failures")}`);
  check(`${provider.id}: the RDF export is not empty`, Number(field(verdict, "RDF triples") ?? 0) > 0, `triples=${field(verdict, "RDF triples")}`);

  await page.screenshot({ path: `${ARTIFACTS}/live-agent-${provider.id}.png` });

  // Leave the drive as the next provider expects to find it.
  await run(
    `${provider.id}: cleanup`,
    `
import os
for _p in ${JSON.stringify([...schemas, ...instances])}:
    if os.path.exists(_p):
        os.remove(_p)
print("CLEANUP DONE")
`
  );
}

section("DIAGNOSTICS");
report.pageErrors = pageErrors;
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.slice(0, 10).forEach((e) => console.log("  " + e.slice(0, 300)));
check("no uncaught page errors", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 3)));

await browser.close();
finish(failures === 0 ? 0 : 1);
