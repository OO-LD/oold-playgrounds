import { loadPyodide } from "pyodide";
import { readFileSync, readdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { dirname, join } from "node:path";
const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, "..");
const wheelDir = join(root, "public", "wheels");
const wheelName = readdirSync(wheelDir).find((f) => f.endsWith(".whl"));
const py = await loadPyodide();
py.setStdout({ batched: (s) => console.log(s) });
py.setStderr({ batched: (s) => console.log("[err]", s) });
py.FS.mkdirTree("/wheels");
py.FS.writeFile(`/wheels/${wheelName}`, readFileSync(join(wheelDir, wheelName)));
await py.loadPackage(["micropip", "sqlite3"]);
const t0 = Date.now();
await py.runPythonAsync(`
import micropip
await micropip.install("typing-extensions>=4.14.0")
await micropip.install(["pydantic","pyyaml","rdflib","pyld","sparqlwrapper","jsondiff"])
await micropip.install("emfs:/wheels/${wheelName}", deps=False)
`);
console.log("LEAN INSTALL ms:", Date.now() - t0);
const src = readFileSync(join(root, "..", "oold-python", "examples", "notation_example.py"), "utf8");
py.FS.writeFile("/notation_example.py", src);
try {
  await py.runPythonAsync(`
import runpy
runpy.run_path("/notation_example.py", run_name="__main__")
`);
  console.log("LEAN EXAMPLE RAN OK (datamodel-code-generator NOT installed)");
} catch (e) {
  console.log("LEAN EXAMPLE FAILED");
  console.log(String(e).split("\n").slice(-20).join("\n"));
}
const dcg = await py.runPythonAsync(`
import importlib.util
str(importlib.util.find_spec("datamodel_code_generator") is not None)
`);
console.log("datamodel_code_generator present:", dcg);
const total = await py.runPythonAsync(`
import os, json
sp="/lib/python3.12/site-packages"
n=0; b=0
for root,_,files in os.walk(sp):
    for f in files:
        if f.endswith(".py"):
            n+=1; b+=os.path.getsize(os.path.join(root,f))
json.dumps({"py_files":n,"bytes":b,"top":sorted(os.listdir(sp))})
`);
console.log("SITE-PACKAGES:", total);
