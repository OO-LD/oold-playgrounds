# oold-jupyterlite

Static JupyterLite deployment demonstrating the `oold` package, built to answer one
question: does in-browser Jupyter give better code intelligence on **runtime-generated**
pydantic models than a static type checker such as the Astral ty playground.

Everything here is serverless. The built site is plain files over HTTP.

## Result

Jupyter completion is answered by the kernel, through `complete_request`, backed by
IPython's `IPCompleter`, which introspects live objects in the kernel namespace. It
therefore sees classes that never existed as source text. Measured, not assumed:

| probe | offers the declared fields | why |
| --- | --- | --- |
| `Person.` (oold model class) | yes | oold installs link descriptors on the class |
| `alice.` (oold model instance) | yes | live instance |
| `alice.employer.` (chained through a resolved link) | yes | the descriptor returns a real object |
| `PlainRuntime.` (exec-created plain class) | yes | control: runtime creation is not the obstacle |
| `instrument.` (instance of an exec-generated model) | yes | live instance |
| `sensor.` (instance of a `create_model` class) | yes | live instance |
| `Instrument.` / `Sensor.` (the generated model **classes**) | no | pydantic v2 keeps fields in `model_fields`, not in the class namespace |

The class-level gap is a pydantic v2 property, not a kernel limitation. `dir(Instrument)`
does not contain the field names either, while `dir(instrument)` does, and the plain
`PlainRuntime` control class created by the same `exec` completes fine.

No static checker can see any of these classes, because none of them exist as source.

## Layout

```
contents/oold_demo.ipynb   generated notebook, the thing that gets shipped
contents/oold_helpers.py   sibling module, proves multi-file imports work
scripts/make_notebook.py   builds the notebook from readable cell sources
scripts/build.sh           wheel build plus jupyter lite build
scripts/serve.py           static file server, counts bytes served
hostpage/                  host pages used to test cross-origin iframe embedding
tests/run.mjs              main Playwright suite: execution, completion, inspection
tests/embed.mjs            URL parameters and iframe embedding
tests/coldload.mjs         cold load timing, payload accounting, anywidget
tests/multifile.mjs        sibling module import and cross-file completion
artifacts/                 logs, screenshots and JSON reports from the last run
```

## Build

Needs a Python 3 on PATH and node. The build environment is a dedicated venv, kept out
of the `oold` checkout.

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install \
  jupyterlite-core jupyterlite-pyodide-kernel jupyter-server build hatchling \
  jupyterlab_widgets anywidget
npm install
npx playwright install chromium

bash scripts/build.sh
```

`scripts/build.sh` builds an `oold` wheel from the local checkout into `wheels/`,
asserts the wheel contains the experimental `oold/model/_notation.py`, regenerates the
notebook, and runs `jupyter lite build`. Set `OOLD_SRC` to point at a different checkout.

`jupyter-server` is required even though nothing is served at runtime: without it the
`contents` addon refuses to add custom content. `jupyterlab_widgets` and `anywidget` are
build-time requirements because their frontend extensions have to be copied into the
site; installing them into the kernel at runtime is not enough.

## Serve

```bash
./.venv/Scripts/python.exe scripts/serve.py --port 8899
./.venv/Scripts/python.exe scripts/serve.py --port 8900 --dir hostpage   # for the iframe tests
```

`GET /__stats` returns bytes served, `GET /__stats?reset` zeroes the counter. That is how
the payload numbers below were measured, because the Pyodide runtime is fetched by a web
worker and never appears in the page's resource timings.

## Test

```bash
node tests/run.mjs        # execution, completion, inspection
node tests/embed.mjs      # URL parameters, cross-origin iframes
node tests/coldload.mjs   # timing, payload, anywidget
node tests/multifile.mjs  # sibling module import, cross-file completion
```

Logs and screenshots land in `artifacts/`.

## What works

- All four previously unverified `oold` runtime dependencies install and import under
  Pyodide: `rdflib` 7.6.0, `pyld`, `sparqlwrapper` 2.0.0, `jsondiff` 2.2.1.
- `examples/notation_example.py` runs unchanged and prints `ALL CHECKS PASSED`.
- Multi-file: the kernel's cwd is `/drive`, the JupyterLite contents drive. A `.py` file
  placed in `contents/` is importable and completion works across the file boundary.
- Embedding: `?path=`, `?kernel=`, `?code=`, `?toolbar=1` and `?execute=1` all work, and
  both apps run in a cross-origin iframe with a live kernel.
- `anywidget` renders, but only if `anywidget` and `jupyterlab_widgets` are installed in
  the **build** environment so their frontend extensions are copied into the site.
  Installing them into the kernel at runtime yields
  `Error: No version of module anywidget is registered`.

## What does not

- No static diagnostics of any kind. Nothing reports an undefined name or a type error
  until the cell runs.
- `jupyterlab-lsp` is not usable here. `jupyter_lsp` spawns language servers with
  `subprocess.Popen`, locates them with `shutil.which`, and serves them over a tornado
  `WebSocketHandler`. A static deployment has no process to do any of that, and the
  built site implements only `/api/drive`, `/api/service-worker-heartbeat` and
  `/api/stdin`; there is no LSP route.
- Two JupyterLite iframes on one host page contend and the second never finishes
  booting. One instance per page.
- Completion on a bare pydantic v2 model **class** does not offer the declared fields.
  Use an instance.

## Payload and timing

Cold, empty browser cache, local server:

| stage | time | bytes from the site |
| --- | --- | --- |
| notebook shell rendered | 2.6 s | 8.44 MB |
| kernel ready and executing `1+1` | 11.6 s | 8.67 MB |
| whole notebook, including pip installs | 9 s more | 8.79 MB |

Grand total for a full cold run, 42.92 MB over 350 requests:

| origin | bytes |
| --- | --- |
| this site | 8.79 MB |
| cdn.jsdelivr.net (Pyodide runtime) | 28.31 MB |
| files.pythonhosted.org and pypi.org (wheels) | 5.82 MB |

The site is 66 MB on disk, 8.79 MB over the wire. The Pyodide distribution is **not**
vendored: it comes from `cdn.jsdelivr.net` at runtime, and the wheels for `rdflib`,
`pyld`, `sparqlwrapper`, `jsondiff`, `datamodel-code-generator` and `anywidget` come from
PyPI. A genuinely offline deployment has to vendor both.

Measured with an empty browser cache each run. The byte counts come from the serving
process, not from page resource timings, because the Pyodide runtime is fetched by a web
worker and never shows up in `performance.getEntriesByType("resource")`.

## Pyodide notes

- Install `typing-extensions>=4.14.0` first, before anything imports it.
- `typing_extensions` has no `__version__`; use `importlib.metadata.version`.
- Use `JsonSchemaParser(...).parse()`, not `generate()`, which writes files.
- In `datamodel-code-generator` 0.51 to 0.54 there is no `Formatter.BUILTIN`. The members
  are `BLACK`, `ISORT`, `RUFF_CHECK`, `RUFF_FORMAT`. Pass `formatters=[]` and
  `parse(format_=False)` to keep all of them, and `subprocess`, out of the path.
- That version range also takes model configuration through `get_data_model_types(...)`
  and `data_model_type=`, not through `output_model_type=`.
- `pyodide_http.patch_all()` patches requests and urllib but never httpx. The notebook
  maps `httpx.get` onto `requests.get`, translating `follow_redirects` to
  `allow_redirects`.
