import { copyFileSync, mkdirSync, readdirSync, rmSync, statSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const src = join(root, "node_modules", "pyodide");
const dest = join(root, "public", "pyodide");

const skip = [".d.ts", ".md", ".html", ".map"];

rmSync(dest, { recursive: true, force: true });
mkdirSync(dest, { recursive: true });

let count = 0;
let bytes = 0;
for (const name of readdirSync(src)) {
  if (skip.some((s) => name.endsWith(s))) continue;
  const from = join(src, name);
  if (!statSync(from).isFile()) continue;
  copyFileSync(from, join(dest, name));
  count += 1;
  bytes += statSync(from).size;
}

console.log(`copied ${count} pyodide files, ${(bytes / 1024 / 1024).toFixed(1)} MB -> public/pyodide`);
