# oold AI playground

Static JupyterLite site carrying `jupyterlite-ai` plus a wheel built from the local
`oold` checkout. Everything is plain files over HTTP; there is no server at runtime.

## Layout

```
contents/start.ipynb       placeholder notebook on the contents drive
contents/sample.csv        placeholder data file
scripts/build.sh           oold wheel build plus jupyter lite build
scripts/serve.py           static file server, counts bytes served
tests/run.mjs              tier 1 smoke suite
artifacts/                 screenshots and JSON report from the last run
```

## Bootstrap

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -r requirements.txt
npm install
npx playwright install chromium
```

## Build

```bash
bash scripts/build.sh
```

Builds an `oold` wheel from the sibling `oold-python` checkout into `wheels/`, asserts
the wheel carries `oold/cli.py` and `oold/validation/formats.py`, then runs
`jupyter lite build` into `_output/`. Set `OOLD_SRC` for a different checkout and `PY`
for a different interpreter.

`build.sh` puts the venv script directory first on `PATH`. `jupyter lite` is dispatched
by `jupyter_core` to the first `jupyter-lite` on `PATH`, not to the interpreter running
it, so a sibling playground venv on `PATH` otherwise builds with its own
`jupyterlite-core` and without the labextensions installed here.

There is no working incremental mode. `build.sh` removes `_output/` and
`.jupyterlite.doit.db` on every run.

Do not pipe `build.sh` into `head`. The early pipe close kills the build mid-run and
leaves a half-written `_output/` that still serves `lab/index.html` with HTTP 200 while
`build/schemas/all_federated.json` and the contents index are missing. Redirect to a
file instead.

## Serve

```bash
./.venv/Scripts/python.exe scripts/serve.py --port 8902
```

`GET /__stats` returns bytes and requests served, `GET /__stats?reset` zeroes them. The
Pyodide runtime is fetched by a web worker and never shows up in the page's resource
timings, so this is the only way to account for the real payload.

## Test

```bash
node tests/run.mjs
```

| variable | default | meaning |
| --- | --- | --- |
| `BASE` | `http://127.0.0.1:8902` | site root |
| `NB` | `start.ipynb` | notebook opened through `?path=` |
| `HEADED` | unset | `1` runs with a visible browser |
| `KERNEL_TIMEOUT` | 180000 | ms until a kernel that never boots is a failure |
| `EXEC_TIMEOUT` | 300000 | ms until `window.__exec` gives up on a cell |
| `SUITE_TIMEOUT` | 1800000 | ms until the watchdog writes the report and exits 1 |

Results land in `artifacts/report.json`, the exit code is non-zero on any failure.

The site must also work from a subpath, which is what GitHub Pages serves:

```bash
./.venv/Scripts/python.exe scripts/serve.py --port 8904 --dir ../..
BASE="http://127.0.0.1:8904/ai-playground/_output" node tests/run.mjs
```

### Kernel helpers

`tests/run.mjs` installs two helpers on the page that every later suite reuses:

```js
window.__kernel()                  // the live IKernelConnection of the current widget
window.__exec(code, timeoutMs?)    // -> { stdout, results, errors, status }
```

`__exec` drives `requestExecute` and splits IOPub into stdout, results and errors.
`status` is `"timeout"` if the reply never arrives, because a dead kernel leaves
`future.done` pending forever and `page.evaluate` honours no timeout.

Assert on `stdout` line-exact. A traceback echoes the failing source line, so a substring
search over all output matches the `print()` call itself and passes while the cell raised.

## Pyodide constraints

- No subprocess under Emscripten. `!oold ...` does not work; the CLI has to run
  in-process.
- Install `typing-extensions>=4.14.0` before anything else pulls an older one, and drop
  the stale `sys.modules["typing_extensions"]` entry afterwards.
- Set `os.environ.setdefault("OOLD_DESCRIPTOR_BINDING", "1")` before anything imports
  `oold.model`; the binding is selected at import time.
- `pyodide_http.patch_all()` patches requests and urllib, never httpx.
- `jsonschema>=4.25` with its format extras named individually, otherwise
  `oold.validation.formats` raises `KeyError` at import.
- One JupyterLite instance per page; two contend for the same service worker.
- Everything must use relative URLs so the build serves from a subpath.

## Known noise

`jupyter-mcp-manager` arrives as a dependency of `jupyterlite-ai` and polls
`jupyter-mcp-manager/servers`. A static site has no such route, so that 404 is expected
and `tests/run.mjs` allowlists it. Any other HTTP error is a failure.

`jupyterlite-ai` is pinned in `requirements.txt`. Its provider configuration comes from
docs rather than from a stable reference and the plugin ids asserted on move between
releases, so bump it deliberately and re-run the suite.
