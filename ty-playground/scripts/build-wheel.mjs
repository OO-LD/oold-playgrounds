import { execFileSync } from "node:child_process";
import { mkdirSync, readdirSync, rmSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const source = resolve(root, "..", "oold-python");
const out = join(root, "public", "wheels");

rmSync(out, { recursive: true, force: true });
mkdirSync(out, { recursive: true });

execFileSync("uv", ["build", "--wheel", "--out-dir", out], {
  cwd: source,
  stdio: "inherit",
});

console.log("built", readdirSync(out).join(", "));
