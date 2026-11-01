#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Sibling checkout by default; the layout differs in CI, so allow an override.
OOLD_SRC="${OOLD_SRC:-$(cd "$ROOT/../.." && pwd)/oold-python}"

if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="${PY:-$ROOT/.venv/Scripts/python.exe}"
else
  PY="${PY:-$ROOT/.venv/bin/python}"
fi

# `jupyter lite` is dispatched by jupyter_core to whatever `jupyter-lite` script
# comes first on PATH, not to the interpreter running it. A sibling playground
# venv on PATH silently builds with its own jupyterlite-core and without the
# labextensions installed here.
export PATH="$(dirname "$PY"):$PATH"

# extension/pyproject.toml has no build hook: pip copies whatever extension/labextension
# holds and never runs tsc. The bundle is tracked, so CI ships the committed one. An edit
# under extension/src reaches the site only if something rebuilds it first, which is what
# this does. Commit the rebuilt bundle with the sources or CI keeps serving the old one.
EXT="$ROOT/extension"
if [ -d "$EXT/node_modules" ] && [ -n "$(find "$EXT/src" "$EXT/package.json" -newer "$EXT/labextension" 2>/dev/null)" ]; then
  echo "== rebuilding the provider extension =="
  (cd "$EXT" && npm run build)
  "$PY" -m pip install --quiet --force-reinstall --no-deps "$EXT"
fi

echo "== building oold wheel from $OOLD_SRC =="
rm -f "$ROOT"/wheels/oold-*.whl
(cd "$OOLD_SRC" && "$PY" -m build --wheel --no-isolation --outdir "$ROOT/wheels")

WHEEL="$(ls "$ROOT"/wheels/oold-*.whl | head -1)"
"$PY" - "$WHEEL" <<'EOF'
import sys, zipfile

names = zipfile.ZipFile(sys.argv[1]).namelist()
# Emscripten has no subprocess, so the playground drives the CLI in-process and
# validation imports its format checkers eagerly. Both have to be in the wheel.
for required in ("oold/cli.py", "oold/validation/formats.py"):
    assert required in names, f"wheel is missing {required}"
print("wheel ok:", sys.argv[1])
EOF

PYODIDE_VERSION="$("$PY" -c 'from jupyterlite_pyodide_kernel.constants import PYODIDE_VERSION; print(PYODIDE_VERSION)')"

echo "== vendoring piplite wheels for pyodide $PYODIDE_VERSION =="
VENDOR="$ROOT/wheels/vendor"
rm -rf "$VENDOR"
mkdir -p "$VENDOR"
# --platform any with --only-binary rejects anything that is not pure python, which is
# what we want: a compiled artifact has to come from the Pyodide distribution.
"$PY" -m pip download -r "$ROOT/piplite-requirements.txt" \
  --dest "$VENDOR" \
  --no-deps \
  --only-binary=:all: \
  --platform any \
  --python-version 3.14 \
  --implementation py \
  --quiet

# The kernel labextension ships ipykernel, pyodide-kernel and piplite but none of their
# dependencies, so the closure has to start there too.
BUNDLED="$("$PY" -c 'import sys, pathlib; print(pathlib.Path(sys.prefix, "share/jupyter/labextensions/@jupyterlite/pyodide-kernel-extension/static/pypi"))')"

"$PY" "$ROOT/scripts/resolve_wheels.py" verify \
  --pyodide-version "$PYODIDE_VERSION" \
  --cache "$ROOT/.cache" \
  --agent "$ROOT/contents/oold_agent.py" \
  --bundled "$BUNDLED" \
  --dir "$ROOT/wheels" \
  --dir "$VENDOR"

PIPLITE_ARGS=(--piplite-wheels "$WHEEL")
for vendored in "$VENDOR"/*.whl; do
  PIPLITE_ARGS+=(--piplite-wheels "$vendored")
done
echo "indexing $(( ${#PIPLITE_ARGS[@]} / 2 )) wheels into the site"

echo "== settings overrides =="
# The settings addon picks up overrides.json from the lite dir, which is $ROOT, and
# patches it into the settingsOverrides of _output/jupyter-lite.json. There is no
# flag to pass: jupyterlite-core 0.8.5 still accepts --settings-overrides but no
# addon reads the trait any more, so the flag builds the same site either way.
"$PY" "$ROOT/scripts/make_overrides.py"

echo "== jupyter lite build =="
# No working incremental mode; a stale doit db silently reuses old output.
rm -rf "$ROOT/_output" "$ROOT/.jupyterlite.doit.db"
(cd "$ROOT" && "$PY" -m jupyter lite build \
  --debug \
  --contents "$ROOT/contents" \
  "${PIPLITE_ARGS[@]}" \
  --no-libarchive \
  --apps lab \
  --no-unused-shared-packages \
  --output-dir "$ROOT/_output")

echo "== done: $ROOT/_output =="
