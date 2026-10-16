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
contents/OOLD-AGENT.md     the system prompt the agent is given
scripts/make_overrides.py  turns that brief into the shipped settings override
scripts/make_xlsx_fixtures.py  writes the spreadsheet fixtures the suites profile
scripts/make_doc_fixtures.py   writes the docx, pptx and pdf fixtures
tests/run.mjs              tier 1 smoke suite
tests/offline.mjs          cold start with pypi.org and pythonhosted.org blocked
tests/kernel-api.mjs       every entry point of contents/oold_agent.py
tests/git.mjs              contents/oold_git.py against a github stand-in
tests/providers.mjs        the azure provider labextension
tests/agent-loop.mjs       the agent loop against a scripted model
tests/live-agent.mjs       tier 2, opt-in: the loop against a real model
tests/live-git.mjs         tier 2, opt-in: a real repository and a real PAT
tests/fixtures/            the github stand-in and the scripted model
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

`python-calamine` reads the spreadsheets and is in the distribution as a compiled wasm
wheel, so it is installed by `setup()` and never vendored. `openpyxl` and `et_xmlfile`
are in no distribution, are pure python and are vendored: without them nothing can write
an xlsx, and `DataFrame.to_excel` fails with `ModuleNotFoundError`. `xlrd` is in the
distribution but is 2.x, reads `.xls` only and is not used.

`python-docx` and `python-pptx` read the documents, are in no distribution, are pure
python and are vendored. `XlsxWriter` is vendored for `python-pptx`, which requires it
and is the only thing that does; micropip resolves the whole dependency set or none of
it. `pymupdf` reads pdf text and locates pdf tables, and is in the distribution as a
compiled wasm wheel, so it is installed by `setup()` like `python-calamine`.

`pymupdf` is the largest single thing a cold start pulls: 17.3 MB from
`cdn.jsdelivr.net/pyodide/v314.0.6/full/`, measured at 2.2 s of the 9.8 s `setup()` takes
against a local server. The CDN serves it with `cache-control: public, max-age=31536000`,
so a returning visitor with a warm HTTP cache pays nothing. `pypdf` would vendor as pure
python at a fraction of that, and has no table extraction at all.

## Spreadsheet fixtures

```bash
./.venv/Scripts/python.exe scripts/make_xlsx_fixtures.py
```

Writes `fixture_measurements.xlsx`, `fixture_workbook.xlsx`, `fixture_merged_header.xlsx`,
`fixture_legacy.xls` and `fixture_corrupt.xlsx` into `contents/`. They are binary, so a
diff says nothing about what a test asserts against; the script is the readable copy.
`fixture_measurements.xlsx` holds the same table as `fixture_measurements.csv`, which is
what lets `kernel-api.mjs` assert that a sheet and a CSV of one table profile alike.

## Document fixtures

```bash
./.venv/Scripts/python.exe scripts/make_doc_fixtures.py
```

Writes thirteen files into `contents/`: a clean docx, pptx and pdf, one of each with no
table, one of each truncated, three mislabelled copies and one file that is no document at
all. `fixture_protocol.docx` and `fixture_overview.pptx` carry the same reading table, which
is what lets `kernel-api.mjs` assert that one table profiles alike whatever carried it.

Two details the fixtures encode rather than assume. A truncated zip loses the central
directory at its end, so a third of a docx no longer opens; MuPDF rebuilds a damaged
cross-reference table instead, and a third of a pdf still yields two pages, so
`fixture_corrupt.pdf` is the header line alone. And a pdf table is reconstructed from where
the glyphs sit: an underscore sits far enough below the other characters that `sensor_id`
comes back as `sensor id` and a second line holding `_`, so the pdf fixture spells that
column with a space and the brief tells the model to read column names out of the profile.

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
the browser, then asserts the kernel boots, `oold_agent.setup()` completes,
`oold.validation.formats` imports with its checkers backed by the vendored packages, and a
spreadsheet, a docx, a pptx and a pdf all parse. Its last test installs a package that is
vendored nowhere and requires that to fail: without that control, a block that never
reached micropip would look like a pass.

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

## Agent brief

`contents/OOLD-AGENT.md` is what the model is told: the Python API, what a check id means,
and what does not work under Pyodide. It is also the default system prompt of the site, so
a visitor adds a provider and nothing else.

`scripts/build.sh` runs `scripts/make_overrides.py`, which reads the brief and writes
`overrides.json` at the root of the lite directory as

```json
{ "@jupyternaut/persona:settings-model": { "systemPrompt": "# OO-LD agent brief\n..." } }
```

The settings addon copies that file into `_output/` and patches it into the
`settingsOverrides` of `_output/jupyter-lite.json`. At runtime the JupyterLite setting
manager writes each override over the matching schema property default, so
`IAISettingsModel.config.systemPrompt` is the brief before anything is loaded and the
user settings stay empty. `tests/agent-loop.mjs` asserts all four steps of that chain.

The override is generated rather than written by hand because the brief is markdown and
would drift the moment either copy were edited. `overrides.json` is therefore build output
and is gitignored. `make_overrides.py` fails the build if the brief stops starting with
`# OO-LD agent brief`, which is the line the suite pins.

`jupyterlite-core` 0.8.5 still accepts `--settings-overrides` but no addon reads the
trait, so the file is picked up by its location and passing the flag changes nothing.

## Known noise

`jupyter-mcp-manager` arrives as a dependency of `jupyterlite-ai` and polls
`jupyter-mcp-manager/servers`. A static site has no such route, so that 404 is expected
and `tests/run.mjs` allowlists it. Any other HTTP error is a failure.

`jupyterlite-ai` is pinned in `requirements.txt`. Its provider configuration comes from
docs rather than from a stable reference and the plugin ids asserted on move between
releases, so bump it deliberately and re-run the suite.

## Suites

Serve the build first; every suite drives a browser against it.

```bash
./.venv/Scripts/python.exe scripts/serve.py --port 8902
```

Tier 1 is deterministic and has to pass before a merge. No suite needs a credential
and none of them reaches a model provider.

```bash
node tests/run.mjs          # 22 checks
node tests/offline.mjs      # 79 checks
node tests/kernel-api.mjs   # 236 checks
node tests/providers.mjs    # 49 checks
node tests/git.mjs          # 54 checks
node tests/agent-loop.mjs   # 59 checks
```

`agent-loop.mjs` starts `tests/fixtures/llm_stub.py` on port 8921 and registers it as a
`generic` OpenAI-compatible provider. The provider is the only setting it writes: the
system prompt is read, not set, so the checks on what the model was told are claims about
the built site. The stub replays a fixed transcript and records every request, so the
suite asserts what the model was sent rather than what the chat shows. A transcript is
selected with `POST /__script?name=...`; `GET /__transcripts` lists them.

## Live suites

Tier 2 is opt-in, costs tokens or writes to a repository, and is never required for a
merge. Without the environment variables both suites print a skip banner and exit 0.

```bash
OPENAI_API_KEY=... node tests/live-agent.mjs
OOLD_GIT_TOKEN=... OOLD_GIT_REPO=owner/scratch node tests/live-git.mjs
```

`live-agent.mjs` runs once per configured provider, because provider differences in the
shape of a tool call are what it exists to catch:

| provider | variables |
| --- | --- |
| `openai` | `OPENAI_API_KEY`, `OPENAI_MODEL` |
| `anthropic` | `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL` |
| `generic` | `GENERIC_API_KEY`, `GENERIC_BASE_URL`, `GENERIC_MODEL` |
| `azure-openai` | `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_BASE_URL`, `AZURE_OPENAI_DEPLOYMENT`, `AZURE_OPENAI_API_VERSION` |
| `azure-foundry-anthropic` | `AZURE_FOUNDRY_API_KEY`, `AZURE_FOUNDRY_BASE_URL`, `AZURE_FOUNDRY_MODEL`, `AZURE_FOUNDRY_API_VERSION` |

It pulls the SINTEF CSVs, asks for a schema and an instance for one of them, and checks
that `oold_cli("validate", ...)` reports `exit code 0`, that `validate_schema` and
`validate_instance` pass, and that the RDF export carries at least one triple.

A 401 or a 404 from an Azure endpoint is a settings question, not a TypeScript one.
`AZURE_OPENAI_SETTINGS` and `AZURE_FOUNDRY_SETTINGS` take a JSON object merged over the
plugin settings, so `apiKeyHeader`, `dropDefaultAuthHeaders`, `apiVersion`, `pathPattern`,
`pathReplacement`, `headers` and `query` can be turned until the request matches. The
resolved configuration is printed per provider from `oold-azure-providers:describe`.

`live-git.mjs` writes one file under `oold-playground-live/`, reads it back through a
fresh pull and deletes it again, so the scratch repository is left as it was found. Set
`OOLD_GIT_API_BASE` to `http://127.0.0.1:8913` to dry run the steps against
`tests/fixtures/github_api.py`; that stand-in serves no rate limit headers and no
contents DELETE, so two checks fail there by construction.
