# oold AI playground

Static JupyterLite site carrying `jupyterlite-ai` plus a wheel built from the local
`oold` checkout. Everything is plain files over HTTP; there is no server at runtime.

## Layout

```
contents/                  notebook, modules and fixtures on the contents drive
piplite-requirements.txt   pinned wheels vendored into the site
scripts/build.sh           oold wheel build, wheel vendoring, jupyter lite build
scripts/resolve_wheels.py  regenerates and verifies the vendored set
scripts/serve.py           static file server, counts bytes served
tests/run.mjs              tier 1 smoke suite
tests/offline.mjs          cold start with pypi.org and pythonhosted.org blocked
artifacts/                 screenshots and JSON reports from the last run
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

## Vendored wheels

A cold start must not depend on PyPI being reachable. `build.sh` downloads
`piplite-requirements.txt` into `wheels/vendor/` and hands every wheel to
`jupyter lite build --piplite-wheels`, which copies them into `_output/pypi/` and indexes
them in `all.json`. micropip resolves a name against the Pyodide distribution lock first,
then against the piplite indexes, then against PyPI, so the vendored copy wins before
PyPI is ever consulted.

The list holds only what the Pyodide distribution does not already carry. `jsonschema`,
`referencing`, `click`, `idna`, `jsonpointer`, `pydantic`, `pyyaml` and `typing-extensions`
are in the distribution and are deliberately absent: vendoring them would ship bytes the
CDN serves anyway, and anything with a compiled artifact cannot come from PyPI at all.
`pip download` runs with `--only-binary=:all: --platform any`, so a package that has no
pure-python wheel fails the build instead of being shipped broken.

`comm` is in the list for the kernel, not for oold. `jupyterlite-pyodide-kernel` bundles
`ipykernel`, `pyodide-kernel` and `piplite` as wheels but none of their dependencies, and
`pyodide-kernel` requires `comm`, which the distribution does not carry. Without it the
kernel never reaches `kernel_info_reply` once PyPI is unreachable.

Every build runs `scripts/resolve_wheels.py verify`, which walks the closure of the kernel
bootstrap plus `oold_agent.REQUIREMENTS` and fails if a name is neither staged nor in the
Pyodide lock. `REQUIREMENTS` is read from `contents/oold_agent.py` rather than duplicated,
so adding one there breaks the build instead of quietly returning to PyPI at runtime.

Regenerate the pins after changing `REQUIREMENTS` or bumping the pyodide kernel; the
command is in the header of `piplite-requirements.txt`.

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

`tests/offline.mjs` aborts every request to `pypi.org` and `files.pythonhosted.org` in
the browser, then asserts the kernel boots, `oold_agent.setup()` completes and
`oold.validation.formats` imports with its checkers backed by the vendored packages. Its
last test installs a package that is vendored nowhere and requires that to fail: without
that control, a block that never reached micropip would look like a pass.

```bash
node tests/offline.mjs
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
  `oold.validation.formats` raises `KeyError` at import. `piplite` resolves no extras,
  which is why `oold_agent.REQUIREMENTS` spells `format-nongpl` out package by package.
- One JupyterLite instance per page; two contend for the same service worker.
- Everything must use relative URLs so the build serves from a subpath.

## Known noise

`jupyter-mcp-manager` arrives as a dependency of `jupyterlite-ai` and polls
`jupyter-mcp-manager/servers`. A static site has no such route, so that 404 is expected
and `tests/run.mjs` allowlists it. Any other HTTP error is a failure.

`jupyterlite-ai` is pinned in `requirements.txt`. Its provider configuration comes from
docs rather than from a stable reference and the plugin ids asserted on move between
releases, so bump it deliberately and re-run the suite.
