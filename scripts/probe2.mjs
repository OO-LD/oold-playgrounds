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
await py.runPythonAsync(`
import micropip
await micropip.install("typing-extensions>=4.14.0")
await micropip.install("emfs:/wheels/${wheelName}")
from oold.model._notation import Link, OoldField, OoldModel
print("NOTATION IMPORT OK")
`);
const src = readFileSync(join(root, "..", "oold-python", "examples", "notation_example.py"), "utf8");
py.FS.writeFile("/notation_example.py", src);
try {
  await py.runPythonAsync(`
import runpy
runpy.run_path("/notation_example.py", run_name="__main__")
`);
  console.log("EXAMPLE RAN OK");
} catch (e) {
  console.log("EXAMPLE FAILED");
  console.log(String(e).split("\n").slice(-25).join("\n"));
}
// list site-packages footprint of oold + pydantic
const stats = await py.runPythonAsync(`
import os, json
def walk(p):
    n=0; b=0
    for root,_,files in os.walk(p):
        for f in files:
            if f.endswith(".py"):
                n+=1; b+=os.path.getsize(os.path.join(root,f))
    return n,b
sp="/lib/python3.12/site-packages"
out={}
for pkg in ["oold","pydantic","typing_extensions.py","annotated_types"]:
    p=os.path.join(sp,pkg)
    if os.path.isdir(p): out[pkg]=walk(p)
    elif os.path.isfile(p): out[pkg]=(1,os.path.getsize(p))
json.dumps(out)
`);
console.log("PY FILE FOOTPRINT (files, bytes):", stats);
