#!/usr/bin/env bash
# Build the playground. What it contains is in oold-lite.toml; how it is built is in
# OO-LD/oold-jupyterlite-ai, resolved as a sibling checkout.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -x "$ROOT/.venv/Scripts/python.exe" ]; then
  PY="${PY:-$ROOT/.venv/Scripts/python.exe}"
else
  PY="${PY:-$ROOT/.venv/bin/python}"
fi

exec "$PY" -m oold_jupyterlite_ai.cli build \
  --config "$ROOT/oold-lite.toml" \
  --output "$ROOT/_output" "$@"
