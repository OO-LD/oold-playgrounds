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

echo "== building oold wheel from $OOLD_SRC =="
rm -f "$ROOT"/wheels/oold-*.whl
(cd "$OOLD_SRC" && "$PY" -m build --wheel --no-isolation --outdir "$ROOT/wheels")

WHEEL="$(ls "$ROOT"/wheels/oold-*.whl | head -1)"
"$PY" - "$WHEEL" <<'EOF'
import sys, zipfile
names = zipfile.ZipFile(sys.argv[1]).namelist()
assert "oold/model/_notation.py" in names, "wheel is missing the experimental notation module"
print("wheel ok:", sys.argv[1])
EOF

echo "== generating notebook =="
"$PY" "$ROOT/scripts/make_notebook.py"

echo "== jupyter lite build =="
rm -rf "$ROOT/_output" "$ROOT/.jupyterlite.doit.db"
(cd "$ROOT" && "$PY" -m jupyter lite build \
  --debug \
  --contents "$ROOT/contents" \
  --piplite-wheels "$WHEEL" \
  --no-libarchive \
  --apps notebooks --apps repl \
  --no-unused-shared-packages \
  --output-dir "$ROOT/_output")

echo "== done: $ROOT/_output =="
