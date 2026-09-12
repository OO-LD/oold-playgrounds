import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";

const here = dirname(fileURLToPath(import.meta.url));
const wheelDir = join(here, "..", "public", "wheels");
const wheelName = readdirSync(wheelDir).find((f) => f.endsWith(".whl"));

const py = await loadPyodide();
py.setStdout({ batched: (s) => console.log("[py]", s) });
py.setStderr({ batched: (s) => console.log("[py:err]", s) });

py.FS.mkdirTree("/wheels");
py.FS.writeFile(`/wheels/${wheelName}`, readFileSync(join(wheelDir, wheelName)));

await py.loadPackage("micropip");

const probe = async (label, code) => {
  const t0 = Date.now();
  try {
    await py.runPythonAsync(code);
    console.log(`PASS ${label} (${Date.now() - t0}ms)`);
    return true;
  } catch (e) {
    console.log(`FAIL ${label} (${Date.now() - t0}ms)`);
    console.log(String(e).split("\n").slice(-14).join("\n"));
    return false;
  }
};

console.log("pyodide version:", py.version);

await probe("typing-extensions", `
import micropip
await micropip.install("typing-extensions>=4.14.0")
`);

const pkgs = ["rdflib", "pyld", "sparqlwrapper", "jsondiff", "pyyaml", "pydantic"];
const results = {};
for (const p of pkgs) {
  results[p] = { install: await probe(`install ${p}`, `
import micropip
await micropip.install("${p}")
`) };
}

const importNames = {
  rdflib: "rdflib",
  pyld: "pyld",
  sparqlwrapper: "SPARQLWrapper",
  jsondiff: "jsondiff",
  pyyaml: "yaml",
  pydantic: "pydantic",
};
for (const p of pkgs) {
  if (!results[p].install) {
    results[p].import = false;
    continue;
  }
  results[p].import = await probe(`import ${importNames[p]}`, `
import ${importNames[p]}
print("  ${importNames[p]} version:", getattr(${importNames[p]}, "__version__", "n/a"))
`);
}

await probe("datamodel-code-generator", `
import micropip
await micropip.install("datamodel-code-generator>=0.51.0,<0.55.0")
`);

await probe("install oold wheel", `
import micropip
await micropip.install("emfs:/wheels/${wheelName}")
`);

await probe("import oold", `
import oold
print("  oold ok")
`);

await probe("import oold.model._notation", `
from oold.model._notation import Link, OoldField, OoldModel
from oold.backend.document_store import SimpleDictDocumentStore
from oold.backend.interface import set_resolver
print("  _notation ok")
`);

console.log("\n=== SUMMARY ===");
console.log(JSON.stringify(results, null, 2));
