# oold ty playground

Fork of `astral-sh/ruff` `playground/ty` adapted to demonstrate the [`oold`](https://github.com/OO-LD/oold-python)
Python package. It runs Astral's `ty` type checker as WebAssembly for code intelligence and
Pyodide for execution, both in the browser, with no server component.

Two things are demonstrated that a normal Python playground cannot do:

1. **Static code intelligence over an installed package.** Pyodide's `site-packages` is walked
   after `micropip` finishes and each source file is injected into ty's in-memory filesystem,
   so completion, hover and diagnostics resolve `oold` and `pydantic` symbols.
2. **Inline link resolution overlay.** oold dereferences links lazily by calling a backend.
   The fetched attribute values exist nowhere in the source. Enabling "link overlay" hooks the
   resolution boundary and renders those values inline next to the line that triggered them,
   together with batching and cache-hit counts.

## Requirements

- Node 20 or newer
- Python 3.10 or newer with `uv` (only to rebuild the oold wheel)
- Docker (only to rebuild `ty_wasm` from source)

## Setup

```sh
npm install
npm run setup        # copies the pyodide distribution into public/pyodide
```

`public/wheels/oold-<version>-py3-none-any.whl` and `ty_wasm/` are build outputs and are not
tracked. Regenerate them with:

```sh
npm run wheel        # builds the oold wheel from ../oold-python
npm run wasm         # builds ty_wasm from astral-sh/ruff inside a docker container
```

## Run

```sh
npm run dev          # http://localhost:5173
```

Pick a file from the tabs:

- `notation_example.py` copied verbatim from `oold-python/examples/notation_example.py`
- `link_overlay_demo.py` batching and cache behaviour of link resolution
- `broken.py` deliberate type errors, proving the checker is live

Press **Run** to execute in Pyodide. Tick **link overlay** before running to see resolved
values inline.

## Test

```sh
npx playwright install chromium   # once
npm test
```

The suite boots the real page, waits for Pyodide, executes the examples, and asserts on ty
diagnostics, completions, hover and the overlay decorations. Artifacts including screenshots
land in `test-results/artifacts/`.

## Layout

| Path | Purpose |
| --- | --- |
| `src/Playground.tsx` | workspace lifecycle, Pyodide boot, site-packages injection |
| `src/Editor/Editor.tsx` | the 15 synchronous `monaco.languages.register*Provider` bindings |
| `src/Editor/pyodideRuntime.ts` | Pyodide boot, dependency install, source collection |
| `src/Editor/linkOverlay.py` | instrumentation of the oold resolution boundary |
| `src/Editor/linkOverlay.ts` | overlay transport and formatting |
| `tests/playground.spec.ts` | Playwright suite |

## Notes

- Pyodide is pinned to 0.27.7 (CPython 3.12), the line verified against this dependency set.
- `sqlite3` is unvendored in Pyodide and must be loaded explicitly; `oold.backend.document_store`
  imports it at module level.
- `datamodel-code-generator` is an oold dependency but is not imported at runtime by these
  examples, so the wheel is installed with `deps=False` plus an explicit runtime list. That
  avoids pulling `black` and `isort`.
- ty reports argument-type diagnostics on `notation_example.py`. They are correct statically:
  a link field is declared `list[Person] | None` and the example assigns IRI strings, which
  oold coerces at runtime. The checker cannot know that.
