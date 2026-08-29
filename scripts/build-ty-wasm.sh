#!/usr/bin/env bash
# Runs inside a rust:latest container. Mount the destination at /out.
set -euo pipefail

rustc --version
curl -sSf https://rustwasm.github.io/wasm-pack/installer/init.sh | sh
wasm-pack --version

git clone --depth 1 https://github.com/astral-sh/ruff /ruff
cd /ruff
echo "COMMIT=$(git rev-parse HEAD)"

rustup target add wasm32-unknown-unknown
time wasm-pack build crates/ty_wasm --target web --out-dir /out/ty_wasm

ls -la /out/ty_wasm
