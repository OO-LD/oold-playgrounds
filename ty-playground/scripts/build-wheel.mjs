import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");

/** Checkout to build the wheel from; override with OOLD_SRC. */
function findSource() {
  if (process.env.OOLD_SRC) {
    return resolve(process.env.OOLD_SRC);
  }
  let dir = root;
  for (let up = 0; up < 4; up += 1) {
    const candidate = resolve(dir, "..", "oold-python");
    if (existsSync(join(candidate, "pyproject.toml"))) {
      return candidate;
    }
    dir = resolve(dir, "..");
  }
  throw new Error("oold-python checkout not found; set OOLD_SRC");
}

const source = findSource();
const out = join(root, "public", "wheels");
console.log("building oold wheel from", source);

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

execFileSync("uv", ["build", "--wheel", "--out-dir", out], {
  cwd: source,
  stdio: "inherit",
});

// The filename carries the version, which micropip parses, so it cannot be normalised
// away. A manifest keeps the runtime from hardcoding a version it would have to chase
// on every release.
const wheel = readdirSync(out).find((name) => name.endsWith(".whl"));
if (!wheel) {
  throw new Error(`no wheel produced in ${out}`);
}
writeFileSync(join(out, "manifest.json"), JSON.stringify({ wheel }, null, 2) + "\n");

console.log("built", readdirSync(out).join(", "));
