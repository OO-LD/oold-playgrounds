# oold playgrounds

Browser playgrounds demonstrating the [`oold`](https://github.com/OO-LD/oold-python)
package. Both run fully client side and are deployable as static sites.

| Directory | What it is | Answers |
| --- | --- | --- |
| `ty-playground/` | Monaco + `ty_wasm` + Pyodide, forked from Astral's ty playground | static type checking, completion and hover over injected `oold` sources |
| `jupyterlite/` | JupyterLite with a Pyodide kernel | completion over **live objects**, including classes generated at runtime |

The two are complementary rather than competing. A static checker analyses
source and cannot see a class created by `exec()` or `create_model()`;
`IPCompleter` introspects the running kernel namespace and can. Conversely
JupyterLite has no diagnostics at all, and cannot gain them in a serverless
deployment because `jupyter_lsp` needs a subprocess and a websocket route.

## Prerequisites

Both build a wheel from a local `oold-python` checkout rather than PyPI, because
the demos track an unreleased branch. The checkout is found by searching upward
for a sibling `oold-python/`; override with `OOLD_SRC`.

`ty-playground` additionally needs Docker to build `ty_wasm`, and `jupyterlite`
needs a Python environment in `.venv`. Neither is rebuilt on a normal run.

## Run

    cd ty-playground && npm install && npm run dev        # http://localhost:5173

    cd jupyterlite && bash scripts/build.sh
    python scripts/serve.py --port 8899                   # the site
    python scripts/serve.py --port 8900 --dir hostpage    # second origin, iframe tests

Use a Chromium based browser. Firefox does not implement JSPI, which the Pyodide
kernel needs, and fails with `WebAssembly stack switching not supported`.

## Test

    cd ty-playground && npm test
    cd jupyterlite   && node tests/run.mjs   # also embed, coldload, multifile

Both suites drive a real browser through Playwright and assert on rendered
output rather than internal state.

## Keeping the samples current

The Python sources are copies of `oold-python/examples/`, and the notebook model
is a third copy inside `jupyterlite/scripts/make_notebook.py`. They go stale
whenever the branch moves and are resynced by hand today. Never edit
`jupyterlite/contents/oold_demo.ipynb`: it is regenerated on every build.
