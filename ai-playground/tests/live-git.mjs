// Tier 2, opt-in: oold_git against real GitHub with a real token. It writes to a
// repository, so it is never required for a merge. Without a token and a scratch
// repository in the environment it skips loudly and exits 0.
//
//   OOLD_GIT_TOKEN    fine grained PAT with Contents: read and write
//   OOLD_GIT_REPO     owner/name of a scratch repository you are willing to commit to
//   OOLD_GIT_BRANCH   optional, defaults to main
//   OOLD_GIT_API_BASE optional, defaults to https://api.github.com. Point it at
//                     tests/fixtures/github_api.py to dry run the steps without a
//                     real repository; the fixture serves no rate limit headers
//                     and no contents DELETE, so those two checks fail there.
//
// The run writes one file under a unique directory, reads it back through a fresh
// pull and deletes it again, so the repository is left as it was found. The token
// is passed into the kernel and never printed; every assertion that could echo it
// scans the output for the literal value first.

import { chromium } from "playwright";
import { mkdirSync, writeFileSync } from "node:fs";
import { randomUUID } from "node:crypto";

const BASE = process.env.BASE ?? "http://127.0.0.1:8902";
const NB = process.env.NB ?? "start.ipynb";
const PAGE_URL = `${BASE}/lab/index.html?path=${NB}`;
const HEADLESS = process.env.HEADED !== "1";
const KERNEL_TIMEOUT = Number(process.env.KERNEL_TIMEOUT ?? 180000);
const EXEC_TIMEOUT = Number(process.env.EXEC_TIMEOUT ?? 300000);
const SUITE_TIMEOUT = Number(process.env.SUITE_TIMEOUT ?? 1800000);

const ROOT = new globalThis.URL("../", import.meta.url).pathname.replace(/^\//, "");
const ARTIFACTS = `${ROOT}artifacts`;

const TOKEN = process.env.OOLD_GIT_TOKEN ?? "";
const REPO = process.env.OOLD_GIT_REPO ?? "";
const BRANCH = process.env.OOLD_GIT_BRANCH ?? "main";
const API_BASE = process.env.OOLD_GIT_API_BASE ?? "https://api.github.com";
const RUN_ID = randomUUID().slice(0, 8);
const REPO_PATH = `oold-playground-live/run-${RUN_ID}.json`;

mkdirSync(ARTIFACTS, { recursive: true });

const report = { url: PAGE_URL, skipped: false, repo: REPO, branch: BRANCH, apiBase: API_BASE, runId: RUN_ID, tests: [], cells: {} };
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
  writeFileSync(`${ARTIFACTS}/live-git.json`, JSON.stringify(report, null, 2));
  section("SUMMARY");
  for (const t of report.tests) console.log(`${t.ok ? "PASS" : "FAIL"}  ${t.name}`);
  console.log(`\n${report.tests.length - failures}/${report.tests.length} passed, ${failures} failed`);
  process.exit(code);
}

if (!TOKEN || !REPO) {
  const bar = "#".repeat(72);
  console.log(bar);
  console.log("# live-git.mjs SKIPPED: no token or no scratch repository in the environment");
  console.log("#");
  console.log("# This suite commits to a real repository. It is opt-in and never required");
  console.log("# for a merge. Nothing was verified by this run.");
  console.log("#");
  console.log("# Set both of:");
  console.log("#   OOLD_GIT_TOKEN   fine grained PAT with Contents: read and write");
  console.log("#   OOLD_GIT_REPO    owner/name of a scratch repository");
  console.log(`# Missing: ${[!TOKEN && "OOLD_GIT_TOKEN", !REPO && "OOLD_GIT_REPO"].filter(Boolean).join(", ")}`);
  console.log(bar);
  report.skipped = true;
  report.reason = "no token or no scratch repository in the environment";
  writeFileSync(`${ARTIFACTS}/live-git.json`, JSON.stringify(report, null, 2));
  process.exit(0);
}

const watchdog = setTimeout(() => {
  check("suite completed within SUITE_TIMEOUT", false, `${SUITE_TIMEOUT} ms elapsed`);
  finish(1);
}, SUITE_TIMEOUT);
watchdog.unref();

section(`LIVE GIT: ${REPO}#${BRANCH} on ${API_BASE}, run ${RUN_ID}`);

const marker = (out, line) => out.stdout.split("\n").some((l) => l.trimEnd() === line);
const field = (out, key) => {
  const hit = out.stdout.split("\n").map((l) => l.trimEnd()).find((l) => l === key || l.startsWith(`${key} `));
  return hit === undefined ? null : hit.slice(key.length).trim();
};

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1400, height: 900 } });
const page = await context.newPage();

const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));

await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
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
  return { status: k.status };
}, KERNEL_TIMEOUT);
check("kernel reached idle", kernelInfo.status === "idle", kernelInfo.error ?? kernelInfo.status);
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
      else if (t === "error") out.errors.push(`${msg.content.ename}: ${msg.content.evalue}`);
    };
    const timedOut = Symbol("timeout");
    const reply = await Promise.race([future.done, new Promise((r) => setTimeout(() => r(timedOut), timeoutMs ?? defaultTimeout))]);
    out.status = reply === timedOut ? "timeout" : reply.content.status;
    return out;
  };
}, EXEC_TIMEOUT);

// The token travels in the cell source. Nothing here prints the source, and every
// recorded output is scanned for the literal value before it reaches the report.
const run = async (label, code, timeoutMs) => {
  const out = await page.evaluate(([c, t]) => window.__exec(c, t), [code, timeoutMs ?? EXEC_TIMEOUT]);
  const leaked = out.stdout.includes(TOKEN) || out.errors.join(" ").includes(TOKEN);
  if (leaked) {
    out.stdout = out.stdout.split(TOKEN).join("<token>");
    out.errors = out.errors.map((e) => e.split(TOKEN).join("<token>"));
  }
  report.cells[label] = { status: out.status, stdout: out.stdout, errors: out.errors, leaked };
  console.log(`--- ${label} ---`);
  console.log(out.stdout.trimEnd());
  if (out.errors.length) console.log(out.errors.join("\n").slice(0, 1500));
  check(`${label}: the token did not reach the kernel output`, !leaked, leaked ? "output was redacted before being written" : "");
  return out;
};

// -------------------------------------------------------------- 1. the pull
section("TEST 1: authenticated pull");
const pulled = await run(
  "pull",
  `
import json, os
import piplite
await piplite.install(["requests", "pyodide-http"], keep_going=True)
import oold_git
oold_git.configure(token=${JSON.stringify(TOKEN)}, repo=${JSON.stringify(REPO)}, branch=${JSON.stringify(BRANCH)}, api_base=${JSON.stringify(API_BASE)})
_s = oold_git.status()
print("STATUS authenticated " + str(_s["authenticated"]))
print("STATUS head " + str(_s["remote_head"]))
_p = oold_git.pull(${JSON.stringify(REPO)}, ref=${JSON.stringify(BRANCH)}, into="live", paths=["oold-playground-live/*"])
print("PULL ok " + str(_p["ok"]) + " " + str(_p.get("reason")))
print("PULL count " + str(_p.get("count")))
print("PULL in_tree " + str(_p.get("in_tree")))
print("PULL rate " + str(_p.get("rate", {}).get("remaining")) + "/" + str(_p.get("rate", {}).get("limit")))
print("PULL DONE")
`
);
check("pull cell raised nothing", pulled.errors.length === 0, pulled.errors.join(" ").slice(0, 300));
check("the token is accepted", field(pulled, "STATUS authenticated") === "True", field(pulled, "STATUS authenticated") ?? "");
check("the branch head is readable", (field(pulled, "STATUS head") ?? "").length === 40, `head=${field(pulled, "STATUS head")}`);
check("pull succeeded", field(pulled, "PULL ok")?.startsWith("True") === true, field(pulled, "PULL ok") ?? "");
check("an authenticated read has the larger rate budget", Number((field(pulled, "PULL rate") ?? "0/0").split("/")[1]) > 60, `rate=${field(pulled, "PULL rate")}`);
if (!field(pulled, "PULL ok")?.startsWith("True")) {
  await browser.close();
  finish(1);
}

// ------------------------------------------------- 2. commit, push, read back
section("TEST 2: commit, push and read back");
const payload = JSON.stringify({ run: RUN_ID, written_by: "tests/live-git.mjs" });
const pushed = await run(
  "commit-push",
  `
os.makedirs("live/oold-playground-live", exist_ok=True)
with open("live/${REPO_PATH}", "w", encoding="utf-8") as _fh:
    _fh.write(${JSON.stringify(payload)})

_c = oold_git.commit(["live/${REPO_PATH}"], "chore: live suite run ${RUN_ID}")
print("COMMIT ok " + str(_c["ok"]) + " " + str(_c.get("reason")) + " " + str(_c.get("message") if not _c["ok"] else ""))
print("COMMIT sha " + str(_c.get("commit")))
print("COMMIT paths " + ",".join(f["path"] for f in _c.get("files", [])))
print("COMMIT pushed " + str(_c.get("pushed")))

_mid = oold_git.status()
print("STAGED " + ",".join(_mid["pending"]["paths"]) if _mid.get("pending") else "STAGED none")
print("STALE " + str(_mid.get("stale")))

_push = oold_git.push()
print("PUSH ok " + str(_push["ok"]) + " " + str(_push.get("reason")))
print("PUSH ref " + str(_push.get("ref")))
print("PUSH moved " + str(_push.get("commit") == _c.get("commit")))

_after = oold_git.status()
print("PENDING after push " + str(_after["pending"]))
print("REMOTE at commit " + str(_after["remote_head"] == _c.get("commit")))

_back = oold_git.pull(${JSON.stringify(REPO)}, ref=${JSON.stringify(BRANCH)}, into="verify", paths=["${REPO_PATH}"])
print("READBACK ok " + str(_back["ok"]))
print("READBACK count " + str(_back.get("count")))
print("READBACK body " + open("verify/${REPO_PATH}", encoding="utf-8").read())
print("WRITE DONE")
`
);
check("commit cell raised nothing", pushed.errors.length === 0, pushed.errors.join(" ").slice(0, 300));
check("commit succeeded", field(pushed, "COMMIT ok")?.startsWith("True") === true, field(pushed, "COMMIT ok") ?? "");
check("commit does not move the ref on its own", field(pushed, "COMMIT pushed") === "False", field(pushed, "COMMIT pushed") ?? "");
check("the staged path is the repository path", field(pushed, "COMMIT paths") === REPO_PATH, field(pushed, "COMMIT paths") ?? "");
check("push moved the branch", field(pushed, "PUSH ok")?.startsWith("True") === true && field(pushed, "PUSH moved") === "True", `${field(pushed, "PUSH ok")} ${field(pushed, "PUSH ref")}`);
check("nothing is staged after the push", field(pushed, "PENDING after push") === "None", field(pushed, "PENDING after push") ?? "");
check("the remote head is the new commit", field(pushed, "REMOTE at commit") === "True", field(pushed, "REMOTE at commit") ?? "");
check("the file reads back from the remote", field(pushed, "READBACK count") === "1", field(pushed, "READBACK count") ?? "");
check("the bytes survived the round trip", field(pushed, "READBACK body") === payload, field(pushed, "READBACK body") ?? "");

// ---------------------------------------------------------------- 3. cleanup
section("TEST 3: delete the file again");
// oold_git writes but does not delete, so cleanup goes through the contents API
// directly. This is housekeeping, not a feature under test: the suite must not
// leave anything behind in the scratch repository.
const deleted = await run(
  "delete",
  `
_path = "/repos/${REPO}/contents/${REPO_PATH}"
_got = oold_git._request("GET", _path, params={"ref": ${JSON.stringify(BRANCH)}})
print("DELETE lookup " + str(_got["status"]))
_sha = (_got.get("data") or {}).get("sha") if _got["ok"] else None
if _sha:
    _gone = oold_git._request("DELETE", _path, payload={
        "message": "chore: drop live suite run ${RUN_ID}",
        "sha": _sha,
        "branch": ${JSON.stringify(BRANCH)},
    })
    print("DELETE status " + str(_gone["status"]))
_check = oold_git._request("GET", _path, params={"ref": ${JSON.stringify(BRANCH)}})
print("DELETE gone " + str(_check["status"] == 404))
print("DELETE DONE")
`
);
check("delete cell raised nothing", deleted.errors.length === 0, deleted.errors.join(" ").slice(0, 300));
check("the file was deleted", field(deleted, "DELETE status") === "200", field(deleted, "DELETE status") ?? "");
check("the repository no longer carries it", field(deleted, "DELETE gone") === "True", field(deleted, "DELETE gone") ?? "");

// ------------------------------------------------------------- 4. the token
section("TEST 4: the token stays out of everything that is readable");
const secrecy = await run(
  "secrecy",
  `
import json
_everything = json.dumps([oold_git.status(remote=False), repr(oold_git._state)])
print("TOKEN in state " + str(${JSON.stringify(TOKEN)} in _everything))
print("REDACT " + oold_git._redact("header: Bearer " + ${JSON.stringify(TOKEN)} + " ok"))
print("SECRECY DONE")
`
);
check("no return value and no module state carries the token", field(secrecy, "TOKEN in state") === "False", field(secrecy, "TOKEN in state") ?? "");
check("an echoed credential is redacted on the way out", marker(secrecy, "REDACT header: Bearer <token> ok"), field(secrecy, "REDACT") ?? "");

section("DIAGNOSTICS");
report.pageErrors = pageErrors;
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.slice(0, 10).forEach((e) => console.log("  " + e.slice(0, 300)));
check("no uncaught page errors", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 3)));

await browser.close();
finish(failures === 0 ? 0 : 1);
