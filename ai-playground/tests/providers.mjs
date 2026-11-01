import { chromium } from "playwright";
import { writeFileSync, mkdirSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8902";
const PAGE_URL = `${BASE}/lab/index.html`;
const HEADLESS = process.env.HEADED !== "1";
const SUITE_TIMEOUT = Number(process.env.SUITE_TIMEOUT ?? 600000);
const ARTIFACTS = new globalThis.URL("../artifacts/", import.meta.url).pathname.replace(/^\//, "");

const PLUGIN_ID = "oold-azure-providers:plugin";
const DESCRIBE_COMMAND = "oold-azure-providers:describe";
const AZURE_OPENAI = "azure-openai";
const FOUNDRY_ANTHROPIC = "azure-foundry-anthropic";
const REGISTRY_TOKEN = "@jupyternaut/agent:IProviderRegistry";
const SETTINGS_TOKEN = "@jupyterlab/coreutils:ISettingRegistry";

// Shipped in jupyterlite-ai 0.20.1 through @jupyternaut/persona. The provider
// docs also list OpenRouter; 0.20.1 does not ship it.
const SHIPPED_PROVIDERS = ["anthropic", "google", "mistral", "openai", "generic"];

// .invalid never resolves, so a request that escapes the fetch stub fails loudly
// instead of reaching a tenant.
const AZURE_BASE_URL = "https://unit-test.invalid/openai";
const AZURE_DEPLOYMENT = "gpt-4o-playground";
const AZURE_API_VERSION = "2025-04-01-preview";
const FOUNDRY_BASE_URL = "https://unit-test.invalid/anthropic/v1";
// What a tenant's portal shows as the Anthropic endpoint. @ai-sdk/anthropic appends
// "/messages", so without the /v1 the request lands on a path Azure answers with a 404
// and no CORS headers, which the browser then reports as a cross-origin block.
const FOUNDRY_ROOT_URL = "https://unit-test.invalid/anthropic";
const FOUNDRY_MODEL = "claude-sonnet-4-5";
const FOUNDRY_API_VERSION = "2024-12-01-preview";

mkdirSync(ARTIFACTS, { recursive: true });

const report = { url: PAGE_URL, tests: [], providers: {}, settings: {}, requests: {} };
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
  writeFileSync(`${ARTIFACTS}/providers.json`, JSON.stringify(report, null, 2));
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

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await context.newPage();

const consoleErrors = [];
const pageErrors = [];
const badStatuses = [];
const outboundHosts = new Set();

const EXPECTED_404 = [/\/jupyter-mcp-manager\/servers/];

page.on("console", (m) => {
  if (m.type() === "error") consoleErrors.push(m.text());
});
page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));
page.on("response", (r) => {
  if (r.status() >= 400) badStatuses.push(`${r.status()} ${r.url()}`);
});
page.on("request", (r) => {
  try {
    outboundHosts.add(new URL(r.url()).host);
  } catch {
    /* opaque url */
  }
});

// ------------------------------------------------------------- 1. load
section("TEST 1: lab app loads and the Azure provider plugin activates");
const resp = await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
check("HTTP 200 for lab app", resp.status() === 200, `status=${resp.status()}`);
await page.waitForSelector("#main", { timeout: 180000 });

// The provider plugins are autoStart and resolve during startup, but the shell
// renders before every deferred plugin has settled.
await page.waitForFunction(
  (id) => {
    try {
      return window.jupyterapp?.isPluginActivated(id) === true;
    } catch {
      return false;
    }
  },
  PLUGIN_ID,
  { timeout: 120000 }
).catch(() => {});

const pluginState = await page.evaluate((id) => {
  const app = window.jupyterapp;
  const ids = app.listPlugins ? app.listPlugins() : [];
  let activated = false;
  try {
    activated = app.isPluginActivated(id);
  } catch {
    activated = false;
  }
  return { registered: ids.includes(id), activated, total: ids.length };
}, PLUGIN_ID);
report.plugin = pluginState;
console.log(JSON.stringify(pluginState));
check(`${PLUGIN_ID} registered in window.jupyterapp`, pluginState.registered, `${pluginState.total} plugins total`);
check(`${PLUGIN_ID} activated`, pluginState.activated, `activated=${pluginState.activated}`);

// Helpers every later section reuses. Lumino tokens compare by identity, so the
// token object has to come out of the live plugin registry rather than be
// rebuilt from its name.
await page.evaluate(() => {
  window.__token = (name) => {
    const app = window.jupyterapp;
    const registry = app.pluginRegistry ?? app._pluginRegistry;
    if (!registry?._services) {
      throw new Error("plugin registry exposes no service map");
    }
    const found = [...registry._services.keys()].find((t) => t?.name === name);
    if (!found) {
      throw new Error(`no token named ${name}`);
    }
    return found;
  };
  window.__service = (name) => window.jupyterapp.resolveRequiredService(window.__token(name));
});

// ------------------------------------------------- 2. providers at runtime
section("TEST 2: provider registry lists both Azure entries");
const providers = await page.evaluate(async (token) => {
  try {
    const registry = await window.__service(token);
    return {
      ok: true,
      available: registry.getAvailableProviders(),
      info: Object.fromEntries(
        Object.entries(registry.providers).map(([id, p]) => [
          id,
          {
            name: p.name,
            apiKeyRequirement: p.apiKeyRequirement,
            supportsBaseURL: p.supportsBaseURL ?? false,
            defaultModels: p.defaultModels ?? [],
            hasFactory: typeof p.factory === "function",
          },
        ])
      ),
    };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}, REGISTRY_TOKEN);
report.providers = providers;
console.log(`available providers: ${JSON.stringify(providers.available ?? providers.error)}`);
check(
  `${REGISTRY_TOKEN} resolves from the live app`,
  providers.ok === true,
  providers.ok ? `${providers.available.length} providers` : providers.error
);
if (!providers.ok) {
  await page.screenshot({ path: `${ARTIFACTS}/providers-no-registry.png` });
  await browser.close();
  finish(1);
}
check(
  "azure-openai registered",
  providers.available.includes(AZURE_OPENAI),
  `info=${JSON.stringify(providers.info[AZURE_OPENAI] ?? null)}`
);
check(
  "azure-foundry-anthropic registered",
  providers.available.includes(FOUNDRY_ANTHROPIC),
  `info=${JSON.stringify(providers.info[FOUNDRY_ANTHROPIC] ?? null)}`
);

// ------------------------------------------------------- 3. regression
section("TEST 3: shipped providers are still registered");
const missing = SHIPPED_PROVIDERS.filter((id) => !providers.available.includes(id));
for (const id of SHIPPED_PROVIDERS) {
  check(
    `shipped provider ${id} still registered`,
    providers.available.includes(id),
    providers.info[id] ? providers.info[id].name : "absent"
  );
}
check("no shipped provider lost", missing.length === 0, `missing=${JSON.stringify(missing)}`);

// -------------------------------------------------------- 4. settings
section("TEST 4: base URL, model and API version round trip through settings");
const settings = await page.evaluate(
  async ({ token, pluginId, azure, foundry }) => {
    try {
      const registry = await window.__service(token);
      const loaded = await registry.load(pluginId);
      await loaded.set("azureOpenAI", {
        baseUrl: azure.baseUrl,
        deployment: azure.deployment,
        apiVersion: azure.apiVersion,
        apiVersionParam: "api-version",
        apiStyle: "chat",
        useDeploymentBasedUrls: true,
        apiKeyHeader: "api-key",
        dropDefaultAuthHeaders: false,
        models: [],
        headers: { "x-oold-probe": "azure" },
        query: {},
        pathPattern: "",
        pathReplacement: "",
      });
      await loaded.set("azureFoundryAnthropic", {
        baseUrl: foundry.baseUrl,
        model: foundry.model,
        apiVersion: foundry.apiVersion,
        apiVersionParam: "api-version",
        apiKeyHeader: "api-key",
        dropDefaultAuthHeaders: true,
        anthropicVersion: "2023-06-01",
        anthropicBeta: [],
        allowDirectBrowserAccess: true,
        models: [],
        headers: { "x-oold-probe": "foundry" },
        query: {},
        pathPattern: "",
        pathReplacement: "",
      });
      await new Promise((r) => setTimeout(r, 250));
      return {
        ok: true,
        schemaTitle: loaded.schema.title,
        composite: loaded.composite,
      };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },
  {
    token: SETTINGS_TOKEN,
    pluginId: PLUGIN_ID,
    azure: { baseUrl: AZURE_BASE_URL, deployment: AZURE_DEPLOYMENT, apiVersion: AZURE_API_VERSION },
    foundry: { baseUrl: FOUNDRY_BASE_URL, model: FOUNDRY_MODEL, apiVersion: FOUNDRY_API_VERSION },
  }
);
report.settings = settings;
console.log(JSON.stringify(settings, null, 2).slice(0, 2000));
check("plugin settings load and accept writes", settings.ok === true, settings.ok ? settings.schemaTitle : settings.error);
if (settings.ok) {
  const a = settings.composite.azureOpenAI ?? {};
  const f = settings.composite.azureFoundryAnthropic ?? {};
  check("azure base URL read back", a.baseUrl === AZURE_BASE_URL, `baseUrl=${a.baseUrl}`);
  check("azure deployment read back", a.deployment === AZURE_DEPLOYMENT, `deployment=${a.deployment}`);
  check("azure api version read back", a.apiVersion === AZURE_API_VERSION, `apiVersion=${a.apiVersion}`);
  check("foundry base URL read back", f.baseUrl === FOUNDRY_BASE_URL, `baseUrl=${f.baseUrl}`);
  check("foundry model read back", f.model === FOUNDRY_MODEL, `model=${f.model}`);
  check("foundry api version read back", f.apiVersion === FOUNDRY_API_VERSION, `apiVersion=${f.apiVersion}`);
}

// The settings file alone proves nothing about the extension; the describe
// command reports the configuration the provider factories actually hold.
const described = await page.evaluate(async (cmd) => {
  try {
    return { ok: true, value: await window.jupyterapp.commands.execute(cmd) };
  } catch (e) {
    return { ok: false, error: String(e) };
  }
}, DESCRIBE_COMMAND);
report.describe = described;
console.log(JSON.stringify(described, null, 2).slice(0, 2000));
check(`${DESCRIBE_COMMAND} returns the live configuration`, described.ok === true, described.ok ? "" : described.error);
if (described.ok) {
  const cfg = described.value.config;
  check(
    "extension consumed the azure settings",
    cfg.azureOpenAI.baseUrl === AZURE_BASE_URL &&
      cfg.azureOpenAI.deployment === AZURE_DEPLOYMENT &&
      cfg.azureOpenAI.apiVersion === AZURE_API_VERSION,
    JSON.stringify({
      baseUrl: cfg.azureOpenAI.baseUrl,
      deployment: cfg.azureOpenAI.deployment,
      apiVersion: cfg.azureOpenAI.apiVersion,
    })
  );
  check(
    "extension consumed the foundry settings",
    cfg.azureFoundryAnthropic.baseUrl === FOUNDRY_BASE_URL &&
      cfg.azureFoundryAnthropic.model === FOUNDRY_MODEL &&
      cfg.azureFoundryAnthropic.apiVersion === FOUNDRY_API_VERSION,
    JSON.stringify({
      baseUrl: cfg.azureFoundryAnthropic.baseUrl,
      model: cfg.azureFoundryAnthropic.model,
      apiVersion: cfg.azureFoundryAnthropic.apiVersion,
    })
  );
}

const dropdown = await page.evaluate(async (token) => {
  const registry = await window.__service(token);
  const pick = (id) => {
    const info = registry.getProviderInfo(id);
    return info ? { models: info.defaultModels, baseUrls: (info.baseUrls ?? []).map((b) => b.url) } : null;
  };
  return { azure: pick("azure-openai"), foundry: pick("azure-foundry-anthropic") };
}, REGISTRY_TOKEN);
report.dropdown = dropdown;
console.log(`registry view after the settings change: ${JSON.stringify(dropdown)}`);
check(
  "configured deployment offered by the azure provider",
  dropdown.azure?.models?.includes(AZURE_DEPLOYMENT) === true && dropdown.azure?.baseUrls?.includes(AZURE_BASE_URL) === true,
  JSON.stringify(dropdown.azure)
);
check(
  "configured model offered by the foundry provider",
  dropdown.foundry?.models?.includes(FOUNDRY_MODEL) === true && dropdown.foundry?.baseUrls?.includes(FOUNDRY_BASE_URL) === true,
  JSON.stringify(dropdown.foundry)
);

// ---------------------------------------------- 5. request shape, offline
section("TEST 5: request shape, captured through a stubbed fetch");
const shapes = await page.evaluate(
  async ({ token, azure, foundry, foundryRoot }) => {
    const registry = await window.__service(token);
    const original = globalThis.fetch;
    const captured = [];
    globalThis.fetch = async (input, init) => {
      const url = typeof input === "string" ? input : input instanceof URL ? input.href : input.url;
      const headers = {};
      new Headers(init?.headers ?? undefined).forEach((v, k) => {
        headers[k] = k.toLowerCase().includes("key") || k.toLowerCase() === "authorization" ? "<redacted>" : v;
      });
      captured.push({ url, method: init?.method ?? "GET", headers, headerNames: Object.keys(headers), body: typeof init?.body === "string" ? init.body : null });
      throw new Error("fetch stubbed, no network call");
    };

    const drive = async (id, options) => {
      const result = { id, created: false, modelId: null, provider: null, error: null };
      try {
        const model = registry.createChatModel(id, options);
        if (!model) {
          result.error = "createChatModel returned null";
          return result;
        }
        result.created = true;
        result.modelId = model.modelId ?? null;
        result.provider = model.provider ?? null;
        result.specificationVersion = model.specificationVersion ?? null;
        await model.doGenerate({ prompt: [{ role: "user", content: [{ type: "text", text: "ping" }] }], maxOutputTokens: 16 });
      } catch (e) {
        result.error = String(e).slice(0, 300);
      }
      return result;
    };

    const azureResult = await drive("azure-openai", azure);
    const azureCaptured = captured.splice(0, captured.length);
    const foundryResult = await drive("azure-foundry-anthropic", foundry);
    const foundryCaptured = captured.splice(0, captured.length);
    const rootResult = await drive("azure-foundry-anthropic", foundryRoot);
    const rootCaptured = captured.splice(0, captured.length);

    globalThis.fetch = original;
    return {
      azure: { ...azureResult, requests: azureCaptured },
      foundry: { ...foundryResult, requests: foundryCaptured },
      foundryRoot: { ...rootResult, requests: rootCaptured },
      fetchRestored: globalThis.fetch === original,
    };
  },
  {
    token: REGISTRY_TOKEN,
    azure: { provider: "azure-openai", model: AZURE_DEPLOYMENT, apiKey: "test-key", baseURL: AZURE_BASE_URL },
    foundry: { provider: "azure-foundry-anthropic", model: FOUNDRY_MODEL, apiKey: "test-key", baseURL: FOUNDRY_BASE_URL },
    foundryRoot: { provider: "azure-foundry-anthropic", model: FOUNDRY_MODEL, apiKey: "test-key", baseURL: FOUNDRY_ROOT_URL },
  }
);
report.requests = shapes;
console.log(JSON.stringify(shapes, null, 2).slice(0, 4000));

check("window.fetch restored after the probe", shapes.fetchRestored === true, `restored=${shapes.fetchRestored}`);

const azureReq = shapes.azure.requests[0];
check("azure factory built a language model", shapes.azure.created === true, `modelId=${shapes.azure.modelId} provider=${shapes.azure.provider}`);
check("azure model id is the configured deployment", shapes.azure.modelId === AZURE_DEPLOYMENT, `modelId=${shapes.azure.modelId}`);
check("azure request issued through the shaped fetch", !!azureReq, azureReq ? azureReq.url : `error=${shapes.azure.error}`);
if (azureReq) {
  const url = new URL(azureReq.url);
  check("azure request targets the configured base URL", azureReq.url.startsWith(AZURE_BASE_URL), azureReq.url);
  check("azure request uses a deployment path", url.pathname.includes(`/deployments/${AZURE_DEPLOYMENT}`), url.pathname);
  check("azure request carries the configured api-version", url.searchParams.get("api-version") === AZURE_API_VERSION, `api-version=${url.searchParams.get("api-version")}`);
  check("azure request carries an api-key header", azureReq.headerNames.includes("api-key"), JSON.stringify(azureReq.headerNames));
  check("azure request carries the configured extra header", azureReq.headers["x-oold-probe"] === "azure", JSON.stringify(azureReq.headers["x-oold-probe"]));
}

const foundryReq = shapes.foundry.requests[0];
check("foundry factory built a language model", shapes.foundry.created === true, `modelId=${shapes.foundry.modelId} provider=${shapes.foundry.provider}`);
check("foundry model id is the configured model", shapes.foundry.modelId === FOUNDRY_MODEL, `modelId=${shapes.foundry.modelId}`);
check("foundry request issued through the shaped fetch", !!foundryReq, foundryReq ? foundryReq.url : `error=${shapes.foundry.error}`);
if (foundryReq) {
  const url = new URL(foundryReq.url);
  check("foundry request targets the configured base URL", foundryReq.url.startsWith(FOUNDRY_BASE_URL), foundryReq.url);
  check("foundry request uses the anthropic messages path", url.pathname.endsWith("/messages"), url.pathname);
  check("foundry request carries the configured api-version", url.searchParams.get("api-version") === FOUNDRY_API_VERSION, `api-version=${url.searchParams.get("api-version")}`);
  check("foundry request carries an api-key header", foundryReq.headerNames.includes("api-key"), JSON.stringify(foundryReq.headerNames));
  check("foundry request drops x-api-key when configured", !foundryReq.headerNames.includes("x-api-key"), JSON.stringify(foundryReq.headerNames));
  check("foundry request carries the anthropic-version header", foundryReq.headers["anthropic-version"] === "2023-06-01", `anthropic-version=${foundryReq.headers["anthropic-version"]}`);
  let body = null;
  try {
    body = JSON.parse(foundryReq.body ?? "null");
  } catch {
    body = null;
  }
  check("foundry request body names the configured model", body?.model === FOUNDRY_MODEL, `model=${body?.model}`);
}

const rootReq = shapes.foundryRoot.requests[0];
check("foundry accepts the /anthropic root as a base URL", shapes.foundryRoot.created === true, `error=${shapes.foundryRoot.error}`);
check("a base URL without /v1 still issues a request", !!rootReq, rootReq ? rootReq.url : `error=${shapes.foundryRoot.error}`);
if (rootReq) {
  check(
    "a base URL without /v1 is corrected to the route Foundry serves",
    new URL(rootReq.url).pathname === "/anthropic/v1/messages",
    rootReq.url
  );
}

// ------------------------------------------------------ 6. negative control
section("TEST 6: negative control");
// Without this extension the ids are unknown to the registry, createChatModel
// returns null for both and the describe command does not exist. Each of the
// three assertions below flips the moment the labextension is dropped from the
// build, which no assertion over the shipped providers would.
const negative = await page.evaluate(async (token) => {
  const registry = await window.__service(token);
  const unknown = registry.createChatModel("provider-that-does-not-exist", { provider: "x" });
  return {
    unknownIsNull: unknown === null,
    describeExists: window.jupyterapp.commands.hasCommand("oold-azure-providers:describe"),
    azureInfo: registry.getProviderInfo("azure-openai") !== null,
    foundryInfo: registry.getProviderInfo("azure-foundry-anthropic") !== null,
  };
}, REGISTRY_TOKEN);
report.negative = negative;
console.log(JSON.stringify(negative));
check("registry returns null for an unregistered id", negative.unknownIsNull === true, JSON.stringify(negative));
check("describe command exists only because the extension is loaded", negative.describeExists === true, `hasCommand=${negative.describeExists}`);
check("both azure ids resolve to provider info", negative.azureInfo && negative.foundryInfo, JSON.stringify(negative));

// The probe must not have reached a real endpoint.
const liveAzure = [...outboundHosts].filter((h) => /azure\.com|openai\.com|anthropic\.com|microsoft\.com/.test(h));
report.outboundHosts = [...outboundHosts];
check("no request left the browser for a live AI endpoint", liveAzure.length === 0, JSON.stringify(liveAzure));

// ------------------------------------------------------------ diagnostics
section("CONSOLE DIAGNOSTICS");
console.log(`console.error count: ${consoleErrors.length}`);
consoleErrors.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`HTTP >= 400 responses: ${badStatuses.length}`);
badStatuses.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 300)));
report.consoleErrors = consoleErrors;
report.pageErrors = pageErrors;
report.badStatuses = badStatuses;

check("no uncaught page errors", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 5)));
const unexpected = badStatuses.filter((s) => !EXPECTED_404.some((re) => re.test(s)));
check("no unexpected HTTP error responses", unexpected.length === 0, JSON.stringify(unexpected.slice(0, 5)));

await page.screenshot({ path: `${ARTIFACTS}/providers.png`, fullPage: false });
await browser.close();
finish(failures === 0 ? 0 : 1);
