// Tier 1: the agent loop, driven by a scripted model. The chat is pointed at
// tests/fixtures/llm_stub.py through the generic OpenAI-compatible provider, so
// every reply is fixed and the test asserts on what the agent did rather than on
// what a model happened to say.
//
// Every claim is checked against what the stub received, not against the chat UI:
// a message bubble proves nothing about the request that produced it. Assertions
// over kernel output are line-exact, because a traceback echoes the failing source
// line and a substring search would match the print() call itself.

import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";

const BASE = process.env.BASE ?? "http://127.0.0.1:8902";
const NB = process.env.NB ?? "start.ipynb";
const PAGE_URL = `${BASE}/lab/index.html?path=${NB}`;
const HEADLESS = process.env.HEADED !== "1";
const KERNEL_TIMEOUT = Number(process.env.KERNEL_TIMEOUT ?? 180000);
const EXEC_TIMEOUT = Number(process.env.EXEC_TIMEOUT ?? 300000);
const SETUP_TIMEOUT = Number(process.env.SETUP_TIMEOUT ?? 600000);
const TURN_TIMEOUT = Number(process.env.TURN_TIMEOUT ?? 180000);
const SUITE_TIMEOUT = Number(process.env.SUITE_TIMEOUT ?? 1800000);

// 8899 to 8913 belong to the playground site servers and to the github fixture,
// so the model stub sits well clear of them.
const STUB_PORT = Number(process.env.STUB_PORT ?? 8921);
const STUB = `http://127.0.0.1:${STUB_PORT}`;

const ROOT = new globalThis.URL("../", import.meta.url).pathname.replace(/^\//, "");
const ARTIFACTS = `${ROOT}artifacts`;
const STUB_SCRIPT = `${ROOT}tests/fixtures/llm_stub.py`;
const BRIEF_PATH = `${ROOT}contents/OOLD-AGENT.md`;
const PY = existsSync(`${ROOT}.venv/Scripts/python.exe`)
  ? `${ROOT}.venv/Scripts/python.exe`
  : `${ROOT}.venv/bin/python`;

const CHAT_PANEL = "@jupyterlite/ai:chat-panel";
const SETTINGS_TOKEN = "@jupyternaut/agent:IAISettingsModel";
const REGISTRY_TOKEN = "@jupyternaut/agent:IProviderRegistry";
const EXECUTE_IN_KERNEL = "jupyterlab-ai-commands:execute-in-kernel";

// Planted in the kernel before the run and never told to the stub, so it can only
// reach a later request by travelling through the kernel and back.
const NONCE = `nonce-${randomUUID().slice(0, 13)}`;

const BRIEF = readFileSync(BRIEF_PATH, "utf-8");

mkdirSync(ARTIFACTS, { recursive: true });

const report = { url: PAGE_URL, stub: STUB, nonce: NONCE, tests: [], cells: {}, transcripts: {} };
let failures = 0;
let stubProc = null;

function check(name, ok, detail) {
  report.tests.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` :: ${detail}` : ""}`);
}

function section(title) {
  console.log(`\n${"=".repeat(72)}\n${title}\n${"=".repeat(72)}`);
}

function finish(code) {
  writeFileSync(`${ARTIFACTS}/agent-loop.json`, JSON.stringify(report, null, 2));
  if (stubProc) stubProc.kill();
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

const marker = (out, line) => out.stdout.split("\n").some((l) => l.trimEnd() === line);

// --------------------------------------------------------------- stub helpers

const stubState = async () => (await fetch(`${STUB}/__state`)).json();
const stubRequests = async () => (await fetch(`${STUB}/__requests`)).json();
const stubSelect = async (name) =>
  (await fetch(`${STUB}/__script?name=${encodeURIComponent(name)}`, { method: "POST" })).json();

async function stubAlive() {
  try {
    const state = await stubState();
    return typeof state.turn === "number";
  } catch {
    return false;
  }
}

/** The stream output of the last execute-in-kernel result a request carried. */
function kernelOutput(request) {
  const results = (request?.tool_results ?? []).filter(
    (r) => r.parsed?.commandId === EXECUTE_IN_KERNEL
  );
  const last = results[results.length - 1];
  const result = last?.parsed?.result;
  if (!result) return null;
  const stdout = (result.outputs ?? [])
    .filter((o) => o.output_type === "stream")
    .map((o) => o.text ?? "")
    .join("");
  return { ...result, stdout };
}

const hasLine = (text, line) => (text ?? "").split("\n").some((l) => l.trimEnd() === line);

function nearest(text, line) {
  const head = line.split(" ").slice(0, 2).join(" ");
  const candidates = (text ?? "").split("\n").map((l) => l.trimEnd()).filter((l) => l.startsWith(head));
  return candidates.length ? `got: ${candidates.map((c) => JSON.stringify(c)).join(" | ").slice(0, 400)}` : "no line with that prefix";
}

function expectLine(name, text, line) {
  check(`${name}: "${line.length > 100 ? `${line.slice(0, 97)}...` : line}"`, hasLine(text, line), hasLine(text, line) ? "" : nearest(text, line));
}

// --------------------------------------------------------------- stub process

section(`STUB: scripted OpenAI-compatible model on ${STUB}/v1`);
if (await stubAlive()) {
  console.log("a stub is already listening, reusing it");
} else {
  stubProc = spawn(PY, [STUB_SCRIPT, "--port", String(STUB_PORT)], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  stubProc.stdout.on("data", (b) => console.log(`stub: ${String(b).trim()}`));
  stubProc.stderr.on("data", (b) => console.log(`stub!: ${String(b).trim()}`));
  let up = false;
  for (let i = 0; i < 40 && !up; i += 1) {
    await new Promise((r) => setTimeout(r, 250));
    up = await stubAlive();
  }
  // Windows lets a second socket bind an occupied port and silently lose the
  // traffic, so reachability of the right server is the thing to assert.
  check("stub answers on its own port", up, `${STUB}/__state`);
  if (!up) finish(1);
}

// ------------------------------------------------------------------- browser

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await context.newPage();

const pageErrors = [];
const badStatuses = [];
const EXPECTED_404 = [/\/jupyter-mcp-manager\/servers/];

page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));
page.on("response", (r) => {
  if (r.status() >= 400) badStatuses.push(`${r.status()} ${r.url()}`);
});

// ------------------------------------------------------------- 1. the kernel
section("TEST 1: kernel comes up and the agent api is installed in it");
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
  return { id: k.id, status: k.status };
}, KERNEL_TIMEOUT);
report.kernel = kernelInfo;
check("kernel reached idle", kernelInfo.status === "idle", kernelInfo.error ?? kernelInfo.id);
if (kernelInfo.error) {
  await page.screenshot({ path: `${ARTIFACTS}/agent-loop-no-kernel.png` });
  await browser.close();
  finish(1);
}

// Same driver the sibling suites install. The deadline matters: a dead kernel
// leaves future.done pending forever and page.evaluate honours no timeout.
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
  window.__token = (name) => {
    const app = window.jupyterapp;
    const registry = app.pluginRegistry ?? app._pluginRegistry;
    if (!registry?._services) throw new Error("plugin registry exposes no service map");
    const found = [...registry._services.keys()].find((t) => t?.name === name);
    if (!found) throw new Error(`no token named ${name}`);
    return found;
  };
  window.__service = (name) => window.jupyterapp.resolveRequiredService(window.__token(name));
}, EXEC_TIMEOUT);

const run = async (name, code, timeoutMs) => {
  const out = await page.evaluate(([c, t]) => window.__exec(c, t), [code, timeoutMs ?? EXEC_TIMEOUT]);
  report.cells[name] = { status: out.status, stdout: out.stdout, errors: out.errors };
  return out;
};

const setup = await run(
  "setup",
  `
import oold_agent
OOLD_LOOP_NONCE = "${NONCE}"
_info = await oold_agent.setup(verbose=False)
print("SETUP oold " + str(_info["oold"]))
print("SETUP validation " + str(_info["validation"]))
print("SETUP failed " + str(len(_info["failed"])))
print("SETUP nonce " + OOLD_LOOP_NONCE)
`,
  SETUP_TIMEOUT
);
console.log(setup.stdout);
if (setup.errors.length) console.log(setup.errors.join("\n").slice(0, 2000));
check("setup cell raised nothing", setup.errors.length === 0, setup.errors.join(" ").slice(0, 300));
check("oold installed in the notebook kernel", marker(setup, "SETUP oold 2.2.0"), `status=${setup.status}`);
check("validation extra imported", marker(setup, "SETUP validation True"), "");
check("the nonce is in the kernel the agent will reach", marker(setup, `SETUP nonce ${NONCE}`), NONCE);
if (!marker(setup, "SETUP validation True")) {
  await browser.close();
  finish(1);
}

// ------------------------------------------------------- 2. the stub route
section("TEST 2: the generic provider is pointed at the stub");
const providerInfo = await page.evaluate(async (token) => {
  const registry = await window.__service(token);
  const info = registry.getProviderInfo("generic");
  return info
    ? { present: true, supportsBaseURL: info.supportsBaseURL === true, supportsToolCalling: info.supportsToolCalling === true, apiKeyRequirement: info.apiKeyRequirement }
    : { present: false };
}, REGISTRY_TOKEN);
report.providerInfo = providerInfo;
check("jupyterlite-ai ships a generic provider", providerInfo.present === true, JSON.stringify(providerInfo));
check("the generic provider accepts a base URL", providerInfo.supportsBaseURL === true, JSON.stringify(providerInfo));
check("the generic provider declares tool calling", providerInfo.supportsToolCalling === true, JSON.stringify(providerInfo));

const wiring = await page.evaluate(
  async ({ token, baseURL, systemPrompt }) => {
    try {
      const settings = await window.__service(token);
      const id = await settings.addProvider({
        name: "oold-stub",
        provider: "generic",
        model: "oold-stub",
        baseURL,
        apiKey: "stub-key",
      });
      await settings.setActiveProvider(id);
      // The brief is what the model is told. Everything the suite asserts about
      // the system prompt comes from contents/OOLD-AGENT.md, nothing is inlined.
      await settings.updateConfig({ systemPrompt });
      await new Promise((r) => setTimeout(r, 300));
      const entry = settings.getProvider(id);
      return {
        ok: true,
        id,
        baseURL: entry?.baseURL ?? null,
        model: entry?.model ?? null,
        provider: entry?.provider ?? null,
        defaultProvider: settings.config.defaultProvider,
        toolsEnabled: settings.config.toolsEnabled,
        promptLength: settings.config.systemPrompt.length,
      };
    } catch (e) {
      return { ok: false, error: String(e) };
    }
  },
  { token: SETTINGS_TOKEN, baseURL: `${STUB}/v1`, systemPrompt: BRIEF }
);
report.wiring = wiring;
console.log(JSON.stringify(wiring));
check("a provider configuration pointing at the stub was added", wiring.ok === true, wiring.ok ? wiring.id : wiring.error);
if (!wiring.ok) {
  await browser.close();
  finish(1);
}
check("the configuration carries the stub base URL", wiring.baseURL === `${STUB}/v1`, `baseURL=${wiring.baseURL}`);
check("it became the default chat provider", wiring.defaultProvider === wiring.id, `defaultProvider=${wiring.defaultProvider}`);
check("the brief is the system prompt", wiring.promptLength === BRIEF.length, `${wiring.promptLength} of ${BRIEF.length} characters`);

// -------------------------------------------------------------- the chat
section("CHAT: open the panel");
await page.evaluate(async (id) => {
  await window.jupyterapp.commands.execute("@jupyterlite/ai:open-chat");
  window.jupyterapp.shell.activateById(id);
}, CHAT_PANEL);
await page.waitForSelector(".jp-chat-add jp-button", { timeout: 60000 });
check("chat panel is on screen", true, CHAT_PANEL);

const COMPOSER = ".jp-chat-sidepanel-widget textarea:visible";

/**
 * Select a transcript, send one message in a fresh chat and wait until the stub
 * has replayed every step of it.
 */
async function runTranscript(name, prompt) {
  const selected = await stubSelect(name);
  // A new chat starts the agent with an empty history, so the requests recorded
  // for one transcript are not polluted by the previous one.
  await page.click(".jp-chat-add jp-button");
  await page.waitForSelector(COMPOSER, { timeout: 60000 });
  await page.fill(COMPOSER, prompt);
  await page.keyboard.press("Enter");

  const deadline = Date.now() + TURN_TIMEOUT;
  let state = await stubState();
  while (state.turn < selected.steps && Date.now() < deadline) {
    await new Promise((r) => setTimeout(r, 500));
    state = await stubState();
  }
  // The last reply still has to travel back before the recording is final.
  await new Promise((r) => setTimeout(r, 1500));
  const requests = await stubRequests();
  report.transcripts[name] = { steps: selected.steps, state, requests };
  console.log(`transcript ${name}: ${state.turn}/${selected.steps} steps, ${requests.length} requests`);
  for (const r of requests) {
    console.log(`  turn=${r.turn} roles=${JSON.stringify(r.roles)} emitted=${JSON.stringify(r.emitted).slice(0, 160)}`);
  }
  check(`${name}: the stub replayed every step`, state.turn === selected.steps, `${state.turn} of ${selected.steps}`);
  return requests.filter((r) => r.scripted);
}

// ------------------------------------------------- 3. the loop, good schema
section("TEST 3: the agent runs python in the kernel and gets the answer back");
const ok = await runTranscript("schema-ok", "Validate fixture_Measurement.schema.json and report what the checks said.");

check("schema-ok: three model requests, one per turn", ok.length === 3, `${ok.length} requests`);
if (ok.length === 3) {
  check(
    "the first request is the brief plus the user message",
    JSON.stringify(ok[0].roles) === JSON.stringify(["system", "user"]),
    JSON.stringify(ok[0].roles)
  );
  check(
    "execute_command is offered to the model",
    ok[0].tool_names.includes("execute_command"),
    JSON.stringify(ok[0].tool_names)
  );

  // 1. the agent calls execute-in-kernel and the code reaches oold_agent
  const call = ok[1].emitted;
  check(
    "the second turn is an execute-in-kernel call",
    call?.tool === "execute_command" && call?.args?.commandId === EXECUTE_IN_KERNEL,
    JSON.stringify(call).slice(0, 200)
  );
  check(
    "it targets the kernel of the open notebook",
    call?.args?.args?.kernelId === kernelInfo.id,
    `called=${call?.args?.args?.kernelId} notebook=${kernelInfo.id}`
  );
  check(
    "the code it runs calls oold_agent",
    (call?.args?.args?.code ?? "").includes("oold_agent.validate_schema"),
    (call?.args?.args?.code ?? "").split("\n")[1] ?? ""
  );

  // 2. the kernel result is fed back into the next request to the model
  const out = kernelOutput(ok[2]);
  report.transcripts["schema-ok"].kernelOutput = out;
  console.log("--- kernel output as the model received it ---");
  console.log(out?.stdout ?? "(none)");
  check("the third request carries an execute-in-kernel result", out !== null, out ? `status=${out.status}` : "no tool result");
  check("the kernel reported success", out?.success === true, `status=${out?.status} ${out?.errorValue ?? ""}`);
  expectLine("the model received the kernel stdout", out?.stdout, `AGENT nonce ${NONCE}`);
  expectLine("the model received the kernel stdout", out?.stdout, "AGENT passed True");
  expectLine("the model received the kernel stdout", out?.stdout, "AGENT ok 32");
  expectLine("the model received the kernel stdout", out?.stdout, "AGENT failures |");
}

// ------------------------------------------- 4. the loop, failing document
section("TEST 4: a validation failure arrives as a readable report");
const broken = await runTranscript(
  "schema-broken",
  "Validate fixture_Broken.schema.json and fixture_bad.instance.json and name the checks that failed."
);
check("schema-broken: three model requests", broken.length === 3, `${broken.length} requests`);
if (broken.length === 3) {
  const out = kernelOutput(broken[2]);
  report.transcripts["schema-broken"].kernelOutput = out;
  console.log("--- kernel output as the model received it ---");
  console.log(out?.stdout ?? "(none)");
  check("the failing run still came back as a successful execution", out?.success === true, `status=${out?.status}`);
  expectLine("the report says the document failed", out?.stdout, "AGENT passed False");
  expectLine("the model receives the check id", out?.stdout, "AGENT check schema.meta -");
  expectLine("the model receives the check id and its rule", out?.stdout, "AGENT check context.predicates OOLD-EXT-2b61");
  expectLine("the instance failed too", out?.stdout, "AGENT instance passed False");
  expectLine(
    "the model receives the instance check id and the violation",
    out?.stdout,
    "AGENT check instance.schema at /value: 'twenty-two' is not of type 'number'"
  );
}

// ------------------------------------------------------- 5. negative control
section("TEST 5: negative control");

// (a) The nonce was planted in the kernel by this run and is unknown to the stub
// and to the notebook. If execute-in-kernel reached some other kernel, or if the
// tool result were not fed back, it could not appear in a later request.
const okRequests = report.transcripts["schema-ok"].requests.filter((r) => r.scripted);
const beforeRun = JSON.stringify(okRequests[0] ?? {});
const afterRun = JSON.stringify(okRequests[2] ?? {});
check(
  "the nonce is absent from the first request",
  !beforeRun.includes(NONCE),
  `first request ${beforeRun.length} characters`
);
check(
  "the nonce reaches the model only after the kernel ran",
  afterRun.includes(NONCE),
  `third request ${afterRun.length} characters`
);

// (b) The brief, not the stock prompt. Without the wiring in TEST 2 the model is
// told to be Jupyternaut and nothing about oold reaches it.
const prompts = okRequests.map((r) => r.system ?? "");
check(
  "every request carries the brief",
  prompts.every((p) => p.startsWith("# OO-LD agent brief")),
  JSON.stringify(prompts.map((p) => p.slice(0, 24)))
);
for (const line of [
  "Import `oold_agent` before anything touches `oold`",
  "`!oold ...` does not work. Emscripten has no subprocess.",
  "`fault` is a bug in oold: the question was never answered, so do not read the result as",
]) {
  check(
    `the brief tells the model: "${line.slice(0, 60)}..."`,
    prompts.every((p) => p.includes(line)),
    prompts[0]?.includes(line) ? "" : "missing from the system prompt"
  );
}
check(
  "the stock jupyternaut prompt was replaced",
  prompts.every((p) => !p.includes("You are Jupyternaut")),
  "system prompt scanned for the shipped default"
);

// (c) A tool call the environment cannot satisfy must come back as a failure.
// Without this the first two tests would pass just as well against a tool layer
// that reported success without looking.
const unknown = await runTranscript("unknown-command", "Run the command that does not exist.");
check("unknown-command: two model requests", unknown.length === 2, `${unknown.length} requests`);
if (unknown.length === 2) {
  const result = unknown[1].tool_results[unknown[1].tool_results.length - 1]?.parsed;
  report.transcripts["unknown-command"].result = result;
  console.log(`tool result: ${JSON.stringify(result)}`);
  check("an unknown command id comes back as a failed tool result", result?.success === false, JSON.stringify(result));
  check(
    "the failure names the command that does not exist",
    (result?.error ?? "").includes("jupyterlab-ai-commands:no-such-command"),
    result?.error ?? ""
  );
}

// (d) A raising cell must reach the model as an error, not as an empty success.
const raised = await runTranscript("kernel-error", "Call an entry point that does not exist.");
check("kernel-error: three model requests", raised.length === 3, `${raised.length} requests`);
if (raised.length === 3) {
  const out = kernelOutput(raised[2]);
  report.transcripts["kernel-error"].kernelOutput = out;
  console.log(`kernel error as the model received it: ${out?.errorName} ${String(out?.errorValue).slice(0, 120)}`);
  check("a raising cell is reported as an error", out?.success === false && out?.status === "error", `success=${out?.success} status=${out?.status}`);
  check(
    "the model is told which name was missing",
    String(out?.errorValue ?? "").includes("no_such_entry_point"),
    String(out?.errorValue ?? "").slice(0, 200)
  );
}

// Nothing may have reached a real model endpoint.
const live = badStatuses.filter((s) => /openai\.com|anthropic\.com|azure\.com|mistral\.ai|googleapis\.com/.test(s));
check("no request left the browser for a live AI endpoint", live.length === 0, JSON.stringify(live));

// ------------------------------------------------------------ diagnostics
section("DIAGNOSTICS");
report.pageErrors = pageErrors;
report.badStatuses = badStatuses;
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.forEach((e) => console.log("  " + e.slice(0, 300)));
console.log(`HTTP >= 400 responses: ${badStatuses.length}`);
badStatuses.slice(0, 20).forEach((e) => console.log("  " + e.slice(0, 200)));

check("no uncaught page errors", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 5)));
const unexpected = badStatuses.filter((s) => !EXPECTED_404.some((re) => re.test(s)));
check("no unexpected HTTP error responses", unexpected.length === 0, JSON.stringify(unexpected.slice(0, 5)));

await page.screenshot({ path: `${ARTIFACTS}/agent-loop.png`, fullPage: false });
await browser.close();
finish(failures === 0 ? 0 : 1);
