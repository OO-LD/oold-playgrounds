import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, readdirSync, rmSync } from "node:fs";
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

console.log("built", readdirSync(out).join(", "));
