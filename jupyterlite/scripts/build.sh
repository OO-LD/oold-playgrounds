#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OOLD_SRC="${OOLD_SRC:-C:/Users/Stier/ownCloud/Git/OO-LD/oold-python}"
PY="$ROOT/.venv/Scripts/python.exe"

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
(cd "$ROOT" && "$ROOT/.venv/Scripts/jupyter.exe" lite build \
  --debug \
  --contents "$ROOT/contents" \
  --piplite-wheels "$WHEEL" \
  --no-libarchive \
  --apps notebooks --apps repl \
  --no-unused-shared-packages \
  --output-dir "$ROOT/_output")

echo "== done: $ROOT/_output =="
