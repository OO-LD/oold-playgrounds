import { chromium } from "playwright";
import { spawn } from "node:child_process";
import { existsSync, mkdirSync, writeFileSync } from "node:fs";

const BASE = process.env.BASE ?? "http://127.0.0.1:8902";
const NB = process.env.NB ?? "start.ipynb";
const PAGE_URL = `${BASE}/lab/index.html?path=${NB}`;
const HEADLESS = process.env.HEADED !== "1";
const KERNEL_TIMEOUT = Number(process.env.KERNEL_TIMEOUT ?? 180000);
const EXEC_TIMEOUT = Number(process.env.EXEC_TIMEOUT ?? 300000);
const SUITE_TIMEOUT = Number(process.env.SUITE_TIMEOUT ?? 1800000);

// 8899 to 8902 belong to the playground site servers, so the fixture sits well
// clear of them.
const FIXTURE_PORT = Number(process.env.FIXTURE_PORT ?? 8913);
const FIXTURE = `http://127.0.0.1:${FIXTURE_PORT}`;
const LIVE = process.env.LIVE !== "0";

const ROOT = new globalThis.URL("../", import.meta.url).pathname.replace(/^\//, "");
const ARTIFACTS = `${ROOT}artifacts`;
const FIXTURE_SCRIPT = `${ROOT}tests/fixtures/github_api.py`;
const PY = existsSync(`${ROOT}.venv/Scripts/python.exe`)
  ? `${ROOT}.venv/Scripts/python.exe`
  : `${ROOT}.venv/bin/python`;

// Never a real credential. The suite asserts this string reaches the fixture as
// an Authorization header and never reaches any value the agent can read back.
const TOKEN = "ghp_fixtureTOKEN_do_not_use_0123456789";
const REPO = "fixture/demo";
const LIVE_REPO = "SINTEF/physmet-data-documentation-templates";

mkdirSync(ARTIFACTS, { recursive: true });

const report = { url: PAGE_URL, fixture: FIXTURE, live: LIVE, tests: [], cells: {} };
let failures = 0;
let fixtureProc = null;

function check(name, ok, detail) {
  report.tests.push({ name, ok, detail });
  if (!ok) failures += 1;
  console.log(`[${ok ? "PASS" : "FAIL"}] ${name}${detail ? ` :: ${detail}` : ""}`);
}

function section(title) {
  console.log(`\n${"=".repeat(72)}\n${title}\n${"=".repeat(72)}`);
}

function finish(code) {
  writeFileSync(`${ARTIFACTS}/git-report.json`, JSON.stringify(report, null, 2));
  if (fixtureProc) fixtureProc.kill();
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

// A traceback echoes the failing source line, so a substring search over all
// output matches the print() call itself. Markers are line-exact over stream
// output only.
const marker = (out, line) => out.stdout.split("\n").some((l) => l.trim() === line);
const field = (out, key) => {
  const hit = out.stdout
    .split("\n")
    .map((l) => l.trim())
    .find((l) => l === key || l.startsWith(`${key} `));
  return hit === undefined ? null : hit.slice(key.length).trim();
};
const sameSet = (a, b) => a.length === b.length && a.every((v, i) => v === b[i]);

// ------------------------------------------------------------ fixture server

async function fixtureState() {
  const resp = await fetch(`${FIXTURE}/__state`);
  if (!resp.ok) throw new Error(`fixture /__state -> ${resp.status}`);
  return resp.json();
}

async function fixtureRequests() {
  const resp = await fetch(`${FIXTURE}/__requests`);
  if (!resp.ok) throw new Error(`fixture /__requests -> ${resp.status}`);
  return resp.json();
}

async function resetRecording() {
  await fetch(`${FIXTURE}/__reset?recording=1`, { method: "POST" });
}

async function fixtureAlive() {
  try {
    const state = await fixtureState();
    return Boolean(state && state.refs && state.refs[`${REPO}#main`]);
  } catch {
    return false;
  }
}

section(`FIXTURE: github api stand-in on ${FIXTURE}`);
if (await fixtureAlive()) {
  console.log("a fixture is already listening, reusing it");
  await fetch(`${FIXTURE}/__reset`, { method: "POST" });
} else {
  fixtureProc = spawn(PY, [FIXTURE_SCRIPT, "--port", String(FIXTURE_PORT)], {
    cwd: ROOT,
    stdio: ["ignore", "pipe", "pipe"],
  });
  fixtureProc.stdout.on("data", (b) => console.log(`fixture: ${String(b).trim()}`));
  fixtureProc.stderr.on("data", (b) => console.log(`fixture!: ${String(b).trim()}`));
  let up = false;
  for (let i = 0; i < 40 && !up; i += 1) {
    await new Promise((r) => setTimeout(r, 250));
    up = await fixtureAlive();
  }
  // Windows lets a second socket bind an occupied port and silently lose the
  // traffic, so reachability of the right server is the thing to assert.
  check("fixture server answers with its seeded state", up, `${FIXTURE}/__state`);
  if (!up) finish(1);
}

// ------------------------------------------------------------------- browser

const browser = await chromium.launch({ headless: HEADLESS });
const context = await browser.newContext({ viewport: { width: 1500, height: 1000 } });
const page = await context.newPage();

const pageErrors = [];
page.on("pageerror", (e) => pageErrors.push(`${e.name}: ${e.message}`));

section("KERNEL: boot and install the http stack");
await page.goto(PAGE_URL, { waitUntil: "domcontentloaded" });
await page.waitForSelector(".jp-Notebook", { timeout: KERNEL_TIMEOUT });

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
    return { error: e.message, status: k.status };
  }
  while (k.status !== "idle" && Date.now() < deadline) await new Promise((r) => setTimeout(r, 250));
  return { status: k.status };
}, KERNEL_TIMEOUT);
check("kernel reached idle", kernelInfo.status === "idle", kernelInfo.error ?? kernelInfo.status);
if (kernelInfo.error) finish(1);

await page.evaluate((defaultTimeout) => {
  window.__kernel = () => window.jupyterapp.shell.currentWidget.sessionContext.session.kernel;
  window.__exec = async (code, timeoutMs) => {
    const future = window.__kernel().requestExecute({ code, stop_on_error: false });
    const out = { stdout: "", results: [], errors: [] };
    future.onIOPub = (msg) => {
      const t = msg.header.msg_type;
      if (t === "stream") out.stdout += msg.content.text;
      else if (t === "execute_result" || t === "display_data")
        out.results.push(msg.content.data["text/plain"] ?? "");
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

async function run(label, code) {
  const out = await page.evaluate((c) => window.__exec(c), code);
  report.cells[label] = { stdout: out.stdout, errors: out.errors, status: out.status };
  console.log(`--- ${label} ---`);
  console.log(out.stdout.trimEnd());
  if (out.errors.length) console.log(out.errors.join("\n").slice(0, 2000));
  return out;
}

// ------------------------------------------------------------ 1. the module
section("TEST 1: oold_git imports off the contents drive");
const boot = await run(
  "boot",
  `
import json, os, sys
import piplite
await piplite.install(["requests", "pyodide-http"], keep_going=True)
import oold_git

print("MODULE FILE " + str(oold_git.__file__))
print("MODULE API " + " ".join(sorted(oold_git.__all__)))
print("MODULE REPOS " + ",".join(oold_git.default_repos()))
print("MODULE BASE " + oold_git.status(remote=False)["api_base"])
print("BOOT OK")
`
);
check("bootstrap cell raised nothing", boot.errors.length === 0, boot.errors.join(" ").slice(0, 300));
check("oold_git imports without a sys.path tweak", marker(boot, "BOOT OK"), `status=${boot.status}`);
check(
  "oold_git lives on the contents drive",
  field(boot, "MODULE FILE") === "/drive/oold_git.py",
  `MODULE FILE ${field(boot, "MODULE FILE")}`
);
check(
  "public api is the six documented calls",
  field(boot, "MODULE API") === "commit configure default_repos open_pr pull push status",
  `MODULE API ${field(boot, "MODULE API")}`
);
check(
  "default clone list is configuration with the two public repos",
  field(boot, "MODULE REPOS") ===
    "OO-LD/oold-reference-schemas,SINTEF/physmet-data-documentation-templates",
  `MODULE REPOS ${field(boot, "MODULE REPOS")}`
);
check(
  "api_base defaults to github",
  field(boot, "MODULE BASE") === "https://api.github.com",
  `MODULE BASE ${field(boot, "MODULE BASE")}`
);

// -------------------------------------------------------------- 2. live pull
section("TEST 2: unauthenticated pull from a real public repo");
if (!LIVE) {
  console.log("LIVE=0, skipping the network check");
} else {
  const live = await run(
    "live-pull",
    `
import csv
r = oold_git.pull("${LIVE_REPO}", into="gh",
                  paths=["shared/people.csv", "shared/projects.csv", "context/context.json"])
print("LIVE OK " + str(r["ok"]))
print("LIVE REASON " + str(r.get("reason")))
print("LIVE MESSAGE " + str(r.get("message")))
print("LIVE COUNT " + str(r.get("count")))
print("LIVE INTREE " + str(r.get("in_tree")))
print("LIVE RATE " + str(r.get("rate", {}).get("remaining")) + "/" + str(r.get("rate", {}).get("limit")))
if r["ok"]:
    print("LIVE ROOTS " + ",".join(sorted(os.listdir("gh"))))
    print("LIVE SHARED " + ",".join(sorted(os.listdir("gh/shared"))))
    with open("gh/shared/people.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.reader(fh))
    print("LIVE PEOPLE ROWS " + str(len(rows)))
    print("LIVE PEOPLE HEADER " + ",".join(rows[0][:3]))
    ctx = json.load(open("gh/context/context.json", encoding="utf-8"))
    print("LIVE CONTEXT HAS @context " + str("@context" in ctx))
print("LIVE DONE")
`
  );
  check("live pull cell raised nothing", live.errors.length === 0, live.errors.join(" ").slice(0, 300));
  const liveOk = field(live, "LIVE OK") === "True";
  check(
    "pull from a public repo works with no token",
    liveOk,
    liveOk ? `rate ${field(live, "LIVE RATE")}` : `${field(live, "LIVE REASON")} ${field(live, "LIVE MESSAGE")}`
  );
  check(
    "only the three requested paths were written",
    field(live, "LIVE COUNT") === "3" &&
      field(live, "LIVE SHARED") === "people.csv,projects.csv" &&
      field(live, "LIVE ROOTS") === "context,shared",
    `count=${field(live, "LIVE COUNT")} shared=${field(live, "LIVE SHARED")} roots=${field(live, "LIVE ROOTS")}`
  );
  check(
    "the tree listing saw the whole repo, not just the filter",
    Number(field(live, "LIVE INTREE")) > 100,
    `in_tree=${field(live, "LIVE INTREE")}`
  );
  check(
    "the pulled csv is readable from python",
    Number(field(live, "LIVE PEOPLE ROWS")) > 1 &&
      field(live, "LIVE PEOPLE HEADER") === "@id,@type,name",
    `rows=${field(live, "LIVE PEOPLE ROWS")} header=${field(live, "LIVE PEOPLE HEADER")}`
  );
  check(
    "context/context.json parses as json-ld",
    marker(live, "LIVE CONTEXT HAS @context True"),
    field(live, "LIVE CONTEXT HAS @context")
  );
  report.rateAfterLive = field(live, "LIVE RATE");
}

// --------------------------------------------------- 3. paths filter, exact
section("TEST 3: paths filter fetches only what was asked");
await resetRecording();
const filtered = await run(
  "paths-filter",
  `
oold_git.configure(api_base="${FIXTURE}", token="")
r = oold_git.pull("${REPO}", into="fx", paths=["data/*.csv"])
print("FIX OK " + str(r["ok"]))
print("FIX COUNT " + str(r["count"]) + " of " + str(r["in_tree"]))
print("FIX FILES " + ",".join(f["path"] for f in r["files"]))
print("FIX ROOT " + ",".join(sorted(os.listdir("fx"))))
print("FIX DATA " + ",".join(sorted(os.listdir("fx/data"))))
print("FIX BODY " + open("fx/data/sample_a.csv", encoding="utf-8").read().replace("\\n", "|"))
print("FILTER DONE")
`
);
check("paths filter cell raised nothing", filtered.errors.length === 0, filtered.errors.join(" ").slice(0, 300));
check(
  "two of four blobs selected",
  field(filtered, "FIX COUNT") === "2 of 4" && field(filtered, "FIX FILES") === "data/sample_a.csv,data/sample_b.csv",
  `${field(filtered, "FIX COUNT")} :: ${field(filtered, "FIX FILES")}`
);
check(
  "nothing outside the filter reached the drive",
  field(filtered, "FIX ROOT") === "data" && field(filtered, "FIX DATA") === "sample_a.csv,sample_b.csv",
  `root=${field(filtered, "FIX ROOT")} data=${field(filtered, "FIX DATA")}`
);
check(
  "file content survived the base64 round trip",
  field(filtered, "FIX BODY") === "id,value|1,2|",
  `FIX BODY ${field(filtered, "FIX BODY")}`
);

const filterLog = await fixtureRequests();
const filterPaths = filterLog.map((r) => `${r.method} ${r.path}`);
report.filterRequests = filterLog;
check(
  "the server was asked for the tree and the two files, nothing else",
  sameSet(filterPaths, [
    `GET /repos/${REPO}/git/trees/main`,
    `GET /repos/${REPO}/contents/data/sample_a.csv`,
    `GET /repos/${REPO}/contents/data/sample_b.csv`,
  ]),
  JSON.stringify(filterPaths)
);
check(
  "the tree was requested recursively and the blobs pinned to the ref",
  filterLog[0]?.query?.recursive === "1" && filterLog.slice(1).every((r) => r.query.ref === "main"),
  JSON.stringify(filterLog.map((r) => r.query))
);
check(
  "an unauthenticated read sends no Authorization header",
  filterLog.every((r) => r.authorization === false),
  JSON.stringify(filterLog.map((r) => r.authorization))
);

// ------------------------------------------------------- 4. commit and push
section("TEST 4: commit and push against the fixture");
await resetRecording();
const headBefore = (await fixtureState()).refs[`${REPO}#main`];
const wrote = await run(
  "commit-push",
  `
oold_git.configure(token="${TOKEN}")
open("fx/data/sample_a.csv", "w", encoding="utf-8").write("id,value\\n7,8\\n")
os.makedirs("fx/schemas", exist_ok=True)
open("fx/schemas/thing.json", "w", encoding="utf-8").write('{"title": "Thing"}')

c = oold_git.commit(["fx/data/sample_a.csv", "fx/schemas/thing.json"], "feat: add thing schema")
print("COMMIT OK " + str(c["ok"]))
print("COMMIT REASON " + str(c.get("reason")))
print("COMMIT SHA " + str(c.get("commit")))
print("COMMIT PARENT " + str(c.get("parent")))
print("COMMIT PATHS " + ",".join(f["path"] for f in c.get("files", [])))
print("COMMIT PUSHED " + str(c.get("pushed")))

s = oold_git.status()
print("STAGED " + ",".join(s["pending"]["paths"]))
print("STAGED MESSAGE " + s["pending"]["message"])
print("REMOTE STILL AT PARENT " + str(s["remote_head"] == c["parent"]))
print("AUTHENTICATED " + str(s["authenticated"]))

p = oold_git.push()
print("PUSH OK " + str(p["ok"]))
print("PUSH REF " + str(p.get("ref")))
print("PUSH MOVED TO COMMIT " + str(p.get("commit") == c.get("commit")))

s2 = oold_git.status()
print("PENDING AFTER PUSH " + str(s2["pending"]))
print("REMOTE AT COMMIT " + str(s2["remote_head"] == c["commit"]))
print("WRITE DONE")
`
);
check("commit cell raised nothing", wrote.errors.length === 0, wrote.errors.join(" ").slice(0, 300));
check("commit succeeded", field(wrote, "COMMIT OK") === "True", `reason=${field(wrote, "COMMIT REASON")}`);
check(
  "commit does not move the ref on its own",
  field(wrote, "COMMIT PUSHED") === "False" && field(wrote, "REMOTE STILL AT PARENT") === "True",
  `pushed=${field(wrote, "COMMIT PUSHED")} remoteAtParent=${field(wrote, "REMOTE STILL AT PARENT")}`
);
check(
  "status reports what is staged",
  field(wrote, "STAGED") === "data/sample_a.csv,schemas/thing.json" &&
    field(wrote, "STAGED MESSAGE") === "feat: add thing schema",
  `${field(wrote, "STAGED")} :: ${field(wrote, "STAGED MESSAGE")}`
);
check(
  "the pull directory is stripped from the repository paths",
  field(wrote, "COMMIT PATHS") === "data/sample_a.csv,schemas/thing.json",
  `COMMIT PATHS ${field(wrote, "COMMIT PATHS")}`
);
check(
  "the parent is the branch head the fixture held before the commit",
  field(wrote, "COMMIT PARENT") === headBefore,
  `parent=${field(wrote, "COMMIT PARENT")} headBefore=${headBefore}`
);
check(
  "push moves the ref to the commit",
  field(wrote, "PUSH OK") === "True" &&
    field(wrote, "PUSH REF") === "refs/heads/main" &&
    field(wrote, "PUSH MOVED TO COMMIT") === "True",
  `${field(wrote, "PUSH OK")} ${field(wrote, "PUSH REF")} ${field(wrote, "PUSH MOVED TO COMMIT")}`
);
check(
  "nothing is staged after a push and the remote head caught up",
  field(wrote, "PENDING AFTER PUSH") === "None" && field(wrote, "REMOTE AT COMMIT") === "True",
  `pending=${field(wrote, "PENDING AFTER PUSH")} head=${field(wrote, "REMOTE AT COMMIT")}`
);

// What the server actually received, rather than what the client claims.
const writeLog = await fixtureRequests();
const state = await fixtureState();
report.writeRequests = writeLog;

const blobPosts = writeLog.filter((r) => r.method === "POST" && r.path.endsWith("/git/blobs"));
const treePost = writeLog.find((r) => r.method === "POST" && r.path.endsWith("/git/trees"));
const commitPost = writeLog.find((r) => r.method === "POST" && r.path.endsWith("/git/commits"));
const refPatch = writeLog.find((r) => r.method === "PATCH" && r.path.includes("/git/refs/heads/"));

check(
  "the blob bodies that arrived carry the file contents",
  blobPosts.length === 2 &&
    blobPosts.every((r) => r.json.encoding === "utf-8") &&
    sameSet(
      blobPosts.map((r) => r.json.content).sort(),
      ['{"title": "Thing"}', "id,value\n7,8\n"].sort()
    ),
  JSON.stringify(blobPosts.map((r) => r.json))
);
check(
  "the tree that arrived lists both paths as blobs with the blob shas",
  Boolean(treePost) &&
    treePost.json.base_tree &&
    sameSet(
      treePost.json.tree.map((e) => `${e.path} ${e.mode} ${e.type}`),
      ["data/sample_a.csv 100644 blob", "schemas/thing.json 100644 blob"]
    ) &&
    treePost.json.tree.every((e) => Object.keys(state.blobs).includes(e.sha)),
  JSON.stringify(treePost?.json)
);
const committedTree = commitPost ? state.trees[commitPost.json.tree] ?? [] : [];
check(
  "the commit that arrived carries the message, the parent and a tree holding both files",
  Boolean(commitPost) &&
    commitPost.json.message === "feat: add thing schema" &&
    sameSet(commitPost.json.parents, [headBefore]) &&
    ["data/sample_a.csv", "schemas/thing.json"].every((p) =>
      committedTree.some((e) => e.path === p)
    ),
  JSON.stringify(commitPost?.json)
);
check(
  "the ref patch that arrived points at the new commit",
  Boolean(refPatch) &&
    refPatch.path === `/repos/${REPO}/git/refs/heads/main` &&
    refPatch.json.sha === field(wrote, "COMMIT SHA") &&
    state.refs[`${REPO}#main`] === field(wrote, "COMMIT SHA"),
  `${refPatch?.path} sha=${refPatch?.json?.sha} ref=${state.refs[`${REPO}#main`]}`
);
check(
  "every write carried an Authorization header",
  [...blobPosts, treePost, refPatch, commitPost].every((r) => r && r.authorization === true),
  JSON.stringify(writeLog.map((r) => `${r.method} ${r.authorization}`))
);
check(
  "the file the fixture now stores is the edited one",
  Object.values(state.blobs).includes("id,value\n7,8\n") &&
    Object.values(state.blobs).includes('{"title": "Thing"}'),
  JSON.stringify(Object.values(state.blobs))
);

// ----------------------------------------------------------- 5. pull request
section("TEST 5: open_pr sends head and base");
await resetRecording();
const pr = await run(
  "open-pr",
  `
pr = oold_git.open_pr("Add thing schema", "generated from data/sample_a.csv",
                      head="bot/schemas", base="main")
print("PR OK " + str(pr["ok"]))
print("PR REASON " + str(pr.get("reason")))
print("PR NUMBER " + str(pr.get("number")))
print("PR HEAD " + str(pr.get("head")))
print("PR BASE " + str(pr.get("base")))
print("PR URL " + str(pr.get("url")))
print("PR DONE")
`
);
check("open_pr cell raised nothing", pr.errors.length === 0, pr.errors.join(" ").slice(0, 300));
check(
  "open_pr reports the created pull request",
  field(pr, "PR OK") === "True" &&
    field(pr, "PR NUMBER") === "1" &&
    field(pr, "PR URL") === `https://github.com/${REPO}/pull/1`,
  `${field(pr, "PR OK")} ${field(pr, "PR NUMBER")} ${field(pr, "PR URL")}`
);
const prPost = (await fixtureRequests()).find((r) => r.path.endsWith("/pulls"));
report.prRequest = prPost;
check(
  "the pull request body that arrived has the right head and base",
  Boolean(prPost) &&
    prPost.json.head === "bot/schemas" &&
    prPost.json.base === "main" &&
    prPost.json.title === "Add thing schema" &&
    prPost.json.body === "generated from data/sample_a.csv",
  JSON.stringify(prPost?.json)
);

// -------------------------------------------------------- 6. errors as data
section("TEST 6: 401 and rate limit come back as data");
await resetRecording();
const errs = await run(
  "errors",
  `
a = oold_git.pull("fixture/unauthorized")
print("E401 OK " + str(a["ok"]))
print("E401 REASON " + a["reason"])
print("E401 STATUS " + str(a["status"]))
print("E401 MESSAGE " + a["message"])
print("E401 HINT " + a["hint"])

b = oold_git.pull("fixture/ratelimited")
print("E403 REASON " + b["reason"])
print("E403 STATUS " + str(b["status"]))
print("E403 REMAINING " + str(b["rate"]["remaining"]))
print("E403 LIMIT " + str(b["rate"]["limit"]))
print("E403 RETRY IS INT " + str(isinstance(b["retry_in_seconds"], int)))
print("E403 MESSAGE STARTS " + b["message"][:23])

c = oold_git.pull("fixture/does-not-exist")
print("E404 REASON " + c["reason"] + " " + str(c["status"]))

everything = json.dumps([a, b, c, oold_git.status(remote=False), repr(oold_git._state)])
print("TOKEN IN RESULTS " + str("${TOKEN}" in everything))
print("STATUS KEYS " + ",".join(sorted(oold_git.status(remote=False).keys())))
print("REDACT " + oold_git._redact("sent header: Bearer ${TOKEN} ok"))
print("ERRORS DONE")
`
);
check("error cell raised nothing", errs.errors.length === 0, errs.errors.join(" ").slice(0, 300));
check(
  "a 401 comes back as data, not an exception",
  field(errs, "E401 OK") === "False" &&
    field(errs, "E401 REASON") === "auth" &&
    field(errs, "E401 STATUS") === "401" &&
    field(errs, "E401 MESSAGE") === "Bad credentials",
  `${field(errs, "E401 REASON")} ${field(errs, "E401 STATUS")} ${field(errs, "E401 MESSAGE")}`
);
check(
  "the 401 says what to do about it",
  field(errs, "E401 HINT") === "the token was rejected, check that it is current and has the needed scope",
  `E401 HINT ${field(errs, "E401 HINT")}`
);
check(
  "a rate limit comes back with the budget and a retry window",
  field(errs, "E403 REASON") === "rate_limit" &&
    field(errs, "E403 STATUS") === "403" &&
    field(errs, "E403 REMAINING") === "0" &&
    field(errs, "E403 LIMIT") === "60" &&
    field(errs, "E403 RETRY IS INT") === "True",
  `${field(errs, "E403 REASON")} remaining=${field(errs, "E403 REMAINING")} retryInt=${field(errs, "E403 RETRY IS INT")}`
);
check(
  "the rate limit message is the one github sends",
  field(errs, "E403 MESSAGE STARTS") === "API rate limit exceeded",
  `E403 MESSAGE STARTS ${field(errs, "E403 MESSAGE STARTS")}`
);
check(
  "a missing repository is not_found",
  field(errs, "E404 REASON") === "not_found 404",
  `E404 REASON ${field(errs, "E404 REASON")}`
);
check(
  "no return value and no module state carries the token, while one is configured",
  field(errs, "TOKEN IN RESULTS") === "False" &&
    field(errs, "STATUS KEYS") ===
      "api_base,authenticated,branch,default_repos,last_pull,ok,pending,remote_head,repo",
  `inResults=${field(errs, "TOKEN IN RESULTS")} keys=${field(errs, "STATUS KEYS")}`
);
check(
  "an echoed credential is redacted on the way out",
  marker(errs, "REDACT sent header: Bearer <token> ok"),
  `REDACT ${field(errs, "REDACT")}`
);
check(
  "the token never reaches the kernel output of this cell",
  !errs.stdout.includes(TOKEN),
  "stdout scanned for the literal token"
);

// ------------------------------------------------ 7. unauthenticated writes
section("TEST 7: writing without a token fails before the network");
const staged = await run(
  "stage-then-drop-token",
  `
open("fx/schemas/thing.json", "w", encoding="utf-8").write('{"title": "Thing 2"}')
c = oold_git.commit(["fx/schemas/thing.json"], "chore: stage for the no token check")
print("STAGE OK " + str(c["ok"]))
print("STAGE DONE")
`
);
check("staging for the no-token check succeeded", field(staged, "STAGE OK") === "True", `status=${staged.status}`);

await resetRecording();
const noAuth = await run(
  "no-token",
  `
oold_git.configure(token="")
print("AUTH NOW " + str(oold_git.status(remote=False)["authenticated"]))

c = oold_git.commit(["fx/schemas/thing.json"], "feat: must not reach the server")
print("NOCOMMIT OK " + str(c["ok"]))
print("NOCOMMIT REASON " + c["reason"])
print("NOCOMMIT NAMES THE CALL " + str("configure(token=" in c["message"]))
print("NOCOMMIT NAMES THE SCOPE " + str("Contents: read and write" in c["message"]))

p = oold_git.push()
print("NOPUSH REASON " + p["reason"])

pr = oold_git.open_pr("x", "y", head="bot/schemas")
print("NOPR REASON " + pr["reason"])
print("NOAUTH DONE")
`
);
check("no-token cell raised nothing", noAuth.errors.length === 0, noAuth.errors.join(" ").slice(0, 300));
check(
  "configure(token=\"\") drops the credential",
  field(noAuth, "AUTH NOW") === "False",
  `AUTH NOW ${field(noAuth, "AUTH NOW")}`
);
check(
  "commit without a token fails cleanly and names what is missing",
  field(noAuth, "NOCOMMIT OK") === "False" &&
    field(noAuth, "NOCOMMIT REASON") === "no_token" &&
    field(noAuth, "NOCOMMIT NAMES THE CALL") === "True" &&
    field(noAuth, "NOCOMMIT NAMES THE SCOPE") === "True",
  `${field(noAuth, "NOCOMMIT REASON")} call=${field(noAuth, "NOCOMMIT NAMES THE CALL")} scope=${field(noAuth, "NOCOMMIT NAMES THE SCOPE")}`
);
check(
  "push and open_pr refuse the same way",
  field(noAuth, "NOPUSH REASON") === "no_token" && field(noAuth, "NOPR REASON") === "no_token",
  `push=${field(noAuth, "NOPUSH REASON")} pr=${field(noAuth, "NOPR REASON")}`
);
const afterNoAuth = await fixtureRequests();
check(
  "no request was sent at all, the refusal is client side",
  afterNoAuth.length === 0,
  JSON.stringify(afterNoAuth.map((r) => `${r.method} ${r.path}`))
);

// ------------------------------------------------------------- diagnostics
section("DIAGNOSTICS");
report.pageErrors = pageErrors;
console.log(`pageerror count: ${pageErrors.length}`);
pageErrors.forEach((e) => console.log("  " + e.slice(0, 300)));
check("no uncaught page errors", pageErrors.length === 0, JSON.stringify(pageErrors.slice(0, 3)));

await page.screenshot({ path: `${ARTIFACTS}/lab-git.png`, fullPage: false });
await browser.close();
finish(failures === 0 ? 0 : 1);
