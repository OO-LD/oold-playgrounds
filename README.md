# oold playgrounds

Browser playgrounds for the [`oold`](https://github.com/OO-LD/oold-python) and
[`awl`](https://github.com/OO-LD/awl-python) packages. All run fully client side and are deployable as static sites
(`schema-playground` also runs as an ordinary Panel server).

| Directory | What it is | Answers |
| --- | --- | --- |
| `ty-playground/` | Monaco + `ty_wasm` + Pyodide, forked from Astral's ty playground | static type checking, completion and hover over injected `oold` sources |
| `jupyterlite/` | JupyterLite with a Pyodide kernel | completion over **live objects**, including classes generated at runtime |
| `schema-playground/` | Panel + panelini app on the released `oold` validator | what a schema *means*: mapping-set selection, RDF export, and instance transformation between two schemas |
| `awl-playground/` | Panel + React Flow + Monaco on [`awl`](https://github.com/OO-LD/awl-python). **Highly experimental** | a Python procedure as blocks, as source and as a traced run, all three live at once |

The completion playgrounds are complementary rather than competing. A static checker analyses
source and cannot see a class created by `exec()` or `create_model()`;
`IPCompleter` introspects the running kernel namespace and can. Conversely
JupyterLite has no diagnostics at all, and cannot gain them in a serverless
deployment because `jupyter_lsp` needs a subprocess and a websocket route.

## Prerequisites

`ty-playground` and `jupyterlite` build a wheel from a local `oold-python` checkout rather than PyPI,
following its `main` branch. The checkout is found by searching upward for a
sibling `oold-python/`; override with `OOLD_SRC`. `awl-playground` does the same
against `awl-python`, overridden with `AWL_SRC`.

`ty-playground` additionally needs Docker to build `ty_wasm`, and `jupyterlite`
needs a Python environment in `.venv`. Neither is rebuilt on a normal run.

## Run

    cd ty-playground && npm install && npm run dev        # http://localhost:5173

    cd jupyterlite && bash scripts/build.sh

    python scripts/serve.py --port 8899                   # the site
    python scripts/serve.py --port 8900 --dir hostpage    # second origin, iframe tests

    cd schema-playground && uv sync && uv run panel serve app.py --dev    # http://localhost:5006/app

    cd awl-playground    && uv sync && uv run panel serve app.py --dev    # http://localhost:5006/app

Chromium and Firefox both work. Firefox shipped JSPI, which the Pyodide kernel
needs, so the `WebAssembly stack switching not supported` failure this used to
warn about no longer happens.

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
