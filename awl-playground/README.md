# AWL-LD playground

One Python procedure, edited three ways at once: as blocks on a canvas, as
source in an editor, and as a run you can watch.

| It shows | How |
| --- | --- |
| A procedure as a flow | React Flow through Panel, with branches in parallel lanes and a loop drawn as a region |
| Typed parameters | The form lives *inside* the node, taken from the class declaration rather than guessed from the value |
| The same file as source | Monaco, with the diagnostics and hover a type checker gives |
| What actually ran | A real execution, traced, laid over the blocks it went through |

Both directions are live. Editing a block rewrites the source; editing the
source redraws the blocks. A value edit patches the span it covers, so comments
survive it; a structural edit regenerates the module and says so, because they
do not.

## Run

    uv sync && uv run panel serve app.py --dev    # http://localhost:5006/app

## Build the static site

    uv run python scripts/build_wasm.py

`awl` is built from a sibling `awl-python/` checkout rather than taken from
PyPI, because the editor tracks an unreleased branch. Override the location
with `AWL_SRC`.
