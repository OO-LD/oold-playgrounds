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

echo "== building schema playground =="
# Panel app converted to a pyodide worker. Independent of the oold dev wheel:
# it installs the released oold from PyPI in the browser.
(cd "$ROOT/schema-playground" && uv sync --quiet && uv run python scripts/build_wasm.py)

echo "== building awl playground =="
# Same shape as the schema playground: a Panel app converted to a pyodide worker. Its
# awl wheel is built from a sibling awl-python checkout, because the editor tracks a
# branch rather than a release.
(cd "$ROOT/awl-playground" && uv sync --quiet && uv run python scripts/build_wasm.py)

echo "== building jupyterlite =="
# PY here points at the landing page venv; jupyterlite needs its own, which its
# build script resolves when PY is absent.
(cd "$ROOT/jupyterlite" && env -u PY bash scripts/build.sh)

echo "== building landing page =="
(cd "$ROOT" && "$PY" -m zensical build)

echo "== assembling $OUT =="
rm -rf "$OUT/ty" "$OUT/jupyterlite" "$OUT/schema" "$OUT/awl"
cp -r "$ROOT/ty-playground/dist" "$OUT/ty"
cp -r "$ROOT/jupyterlite/_output" "$OUT/jupyterlite"
cp -r "$ROOT/schema-playground/dist" "$OUT/schema"
cp -r "$ROOT/awl-playground/dist" "$OUT/awl"

# panel convert names the page after the module. A redirect, not a copy: app.html
# is tens of megabytes and duplicating it would double the deployed artifact.
cat > "$OUT/schema/index.html" <<'HTML'
<!DOCTYPE html>
<meta charset="utf-8">
<title>Schema playground</title>
<meta http-equiv="refresh" content="0; url=app.html">
<link rel="canonical" href="app.html">
<p><a href="app.html">Schema playground</a></p>
HTML

cat > "$OUT/awl/index.html" <<'HTML'
<!DOCTYPE html>
<meta charset="utf-8">
<title>AWL-LD playground</title>
<meta http-equiv="refresh" content="0; url=app.html">
<link rel="canonical" href="app.html">
<p><a href="app.html">AWL-LD playground</a></p>
HTML

# The deployed build reads the pyodide distribution from the CDN, so the partial
# copy vite lifts out of public/ is dead weight. Only the oold wheel is served.
rm -rf "$OUT/ty/pyodide"

# GitHub Pages serves the artifact through Jekyll unless told otherwise, which
# strips paths beginning with an underscore. JupyterLite ships several.
touch "$OUT/.nojekyll"

echo "== done: $OUT =="
