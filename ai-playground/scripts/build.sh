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

echo "== jupyter lite build =="
# No working incremental mode; a stale doit db silently reuses old output.
rm -rf "$ROOT/_output" "$ROOT/.jupyterlite.doit.db"
(cd "$ROOT" && "$PY" -m jupyter lite build \
  --debug \
  --contents "$ROOT/contents" \
  --piplite-wheels "$WHEEL" \
  --no-libarchive \
  --apps lab \
  --no-unused-shared-packages \
  --output-dir "$ROOT/_output")

echo "== done: $ROOT/_output =="
