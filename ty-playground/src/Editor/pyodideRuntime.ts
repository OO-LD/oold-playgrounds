import { loadPyodide, type PyodideInterface } from "pyodide";

/**
 * Resolved against the document, not the origin, so the same bundle works at the
 * domain root and under a project path such as /oold-playgrounds/ty/.
 */
const asset = (path: string) => new URL(path, document.baseURI).href;

const PYODIDE_VERSION = "0.27.7";

/**
 * npm ships only part of the pyodide distribution. The dev server redirects the
 * remainder to the CDN, but a static build has no middleware to do that, so the
 * deployed site reads the whole distribution from the CDN instead.
 */
export const PYODIDE_INDEX_URL = import.meta.env.DEV
  ? asset("pyodide/")
  : `https://cdn.jsdelivr.net/pyodide/v${PYODIDE_VERSION}/full/`;

/** Written by scripts/build-wheel.mjs; the wheel filename carries a version that moves. */
export const OOLD_WHEEL_MANIFEST_URL = asset("wheels/manifest.json");

async function oldWheelUrl(): Promise<string> {
  const response = await fetch(OOLD_WHEEL_MANIFEST_URL);
  if (!response.ok) {
    throw new Error(
      `failed to fetch ${OOLD_WHEEL_MANIFEST_URL}: ${response.status}; run npm run wheel`,
    );
  }
  const { wheel } = (await response.json()) as { wheel: string };
  return asset(`wheels/${wheel}`);
}

export type ProgressFn = (message: string) => void;

export type InjectedSource = { path: string; source: string };

export type PyodideRuntime = {
  pyodide: PyodideInterface;
  sitePackages: string;
  pythonVersion: string;
};

const RUNTIME_DEPS = [
  "pydantic",
  "pyyaml",
  "rdflib",
  "pyld",
  "sparqlwrapper",
  "jsondiff",
];

const INIT_CODE = `
import micropip

await micropip.install("typing-extensions>=4.14.0")
await micropip.install(${JSON.stringify(RUNTIME_DEPS)})
await micropip.install("pyodide-http")

import pyodide_http
pyodide_http.patch_all()

try:
    import requests
    import httpx

    def patch_get(url, headers=None, verify=None, follow_redirects=None, params=None):
        return requests.get(
            url,
            headers=headers,
            verify=verify,
            allow_redirects=follow_redirects,
            params=params,
        )

    httpx.get = patch_get
except ImportError:
    pass
`;

export async function bootPyodide(
  progress: ProgressFn,
  onOutput: (line: string) => void,
): Promise<PyodideRuntime> {
  progress("loading pyodide runtime");
  const pyodide = await loadPyodide({ indexURL: PYODIDE_INDEX_URL });

  pyodide.setStdout({ batched: onOutput });
  pyodide.setStderr({ batched: onOutput });

  progress("loading micropip and sqlite3");
  await pyodide.loadPackage(["micropip", "sqlite3"], {
    messageCallback: () => {},
    errorCallback: (m: string) => onOutput(m),
  });

  progress("installing runtime dependencies");
  await pyodide.runPythonAsync(INIT_CODE);

  progress("installing oold wheel");
  const wheelUrl = await oldWheelUrl();
  const wheelResponse = await fetch(wheelUrl);
  if (!wheelResponse.ok) {
    throw new Error(`failed to fetch ${wheelUrl}: ${wheelResponse.status}`);
  }
  const wheelBytes = new Uint8Array(await wheelResponse.arrayBuffer());
  const wheelName = wheelUrl.split("/").pop() as string;
  pyodide.FS.mkdirTree("/wheels");
  pyodide.FS.writeFile(`/wheels/${wheelName}`, wheelBytes);

  await pyodide.runPythonAsync(`
import micropip
await micropip.install("emfs:/wheels/${wheelName}", deps=False)
`);

  progress("importing oold");
  const info = await pyodide.runPythonAsync(`
import json, site, sys
import oold
from oold.model._notation import Link, OoldField, OoldModel
json.dumps({
    "sitePackages": site.getsitepackages()[0],
    "pythonVersion": "%d.%d" % (sys.version_info[0], sys.version_info[1]),
})
`);

  const parsed = JSON.parse(info) as {
    sitePackages: string;
    pythonVersion: string;
  };

  return {
    pyodide,
    sitePackages: parsed.sitePackages,
    pythonVersion: parsed.pythonVersion,
  };
}

export async function probeImports(
  pyodide: PyodideInterface,
  modules: string[],
): Promise<Record<string, { ok: boolean; detail: string }>> {
  const code = `
import json, importlib
result = {}
for name in ${JSON.stringify(modules)}:
    try:
        mod = importlib.import_module(name)
        result[name] = {"ok": True, "detail": str(getattr(mod, "__version__", "n/a"))}
    except Exception as exc:
        result[name] = {"ok": False, "detail": "%s: %s" % (type(exc).__name__, exc)}
json.dumps(result)
`;
  return JSON.parse(await pyodide.runPythonAsync(code));
}

export async function collectSitePackageSources(
  pyodide: PyodideInterface,
  topLevel: string[],
): Promise<InjectedSource[]> {
  const code = `
import json, os, site

site_packages = site.getsitepackages()[0]
wanted = ${JSON.stringify(topLevel)}
collected = []

for name in wanted:
    target = os.path.join(site_packages, name)
    if os.path.isfile(target):
        with open(target, "r", encoding="utf-8", errors="replace") as handle:
            collected.append({"path": name, "source": handle.read()})
        continue
    if not os.path.isdir(target):
        continue
    for root, _dirs, files in os.walk(target):
        for file_name in files:
            if not (file_name.endswith(".py") or file_name.endswith(".pyi")):
                continue
            full = os.path.join(root, file_name)
            rel = os.path.relpath(full, site_packages).replace(os.sep, "/")
            with open(full, "r", encoding="utf-8", errors="replace") as handle:
                collected.append({"path": rel, "source": handle.read()})

json.dumps(collected)
`;
  return JSON.parse(await pyodide.runPythonAsync(code));
}

export async function runUserScript(
  pyodide: PyodideInterface,
  files: Record<string, string>,
  entry: string,
): Promise<void> {
  const base = "/playground/";
  pyodide.FS.mkdirTree(base);
  for (const [name, content] of Object.entries(files)) {
    const separator = name.lastIndexOf("/");
    if (separator !== -1) {
      pyodide.FS.mkdirTree(base + name.slice(0, separator));
    }
    pyodide.FS.writeFile(base + name, content);
  }

  await pyodide.runPythonAsync(`
import sys
if ${JSON.stringify(base)} not in sys.path:
    sys.path.insert(0, ${JSON.stringify(base)})
import runpy
runpy.run_path(${JSON.stringify(base + entry)}, run_name="__main__")
`);
}
