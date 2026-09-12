#!/usr/bin/env bash
# Build the landing page and both playgrounds into a single static site.
#
# Everything is emitted with relative URLs, so site/ can be served from the
# domain root or from a project path such as /oold-playgrounds/.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT="$ROOT/site"

if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="${PY:-$ROOT/.venv/Scripts/python.exe}"
else
  PY="${PY:-$ROOT/.venv/bin/python}"
fi

echo "== building ty playground =="
# npm run build does not refresh the wheel, so both playgrounds would otherwise
# ship different builds of oold.
(cd "$ROOT/ty-playground" && npm run wheel && npm run build)

echo "== building jupyterlite =="
# PY here points at the landing page venv; jupyterlite needs its own, which its
# build script resolves when PY is absent.
(cd "$ROOT/jupyterlite" && env -u PY bash scripts/build.sh)

echo "== building landing page =="
(cd "$ROOT" && "$PY" -m zensical build)

echo "== assembling $OUT =="
rm -rf "$OUT/ty" "$OUT/jupyterlite"
cp -r "$ROOT/ty-playground/dist" "$OUT/ty"
cp -r "$ROOT/jupyterlite/_output" "$OUT/jupyterlite"

# The deployed build reads the pyodide distribution from the CDN, so the partial
# copy vite lifts out of public/ is dead weight. Only the oold wheel is served.
rm -rf "$OUT/ty/pyodide"

# GitHub Pages serves the artifact through Jekyll unless told otherwise, which
# strips paths beginning with an underscore. JupyterLite ships several.
touch "$OUT/.nojekyll"

echo "== done: $OUT =="
