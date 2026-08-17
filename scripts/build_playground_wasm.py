"""Convert the playground into a static browser (Pyodide) application.

``panel convert --requirements oold[playground]`` cannot be used directly: an extra marker is
dropped, and the plain name resolves to the release on PyPI, so the browser would install a
package that does not contain this app. Instead the locally built wheel is named by its
filename - served next to the generated page - and the runtime dependencies are listed with
it, because micropip installing a wheel by URL still has to be told what else to fetch.

The heavy parts of ``oold``'s own dependency list (the code generator, the SPARQL client) are
not needed by the playground and are deliberately absent; the app imports only
``oold.validation``, ``oold.utils`` and ``oold.ui``.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist" / "playground"
APP = ROOT / "examples" / "oold_playground.py"

#: What the browser needs beyond the wheel itself. Kept explicit so that adding an import to
#: the playground that needs a new package fails here rather than in someone's browser.
REQUIREMENTS = [
    "panelini",
    "param",
    "pydantic",
    "pyld",
    "rdflib",
    "pyyaml",
    "jsonschema",
    "referencing",
    "pyodide-http",
]


def wheel() -> str:
    """The most recently built wheel in the output directory."""
    wheels = sorted(OUT.glob("oold-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        sys.exit(f"no wheel in {OUT}; run `uv build --wheel --out-dir {OUT}` first")
    return wheels[-1].name


def main() -> None:
    command = [
        sys.executable,
        "-m",
        "panel",
        "convert",
        str(APP),
        "--to",
        "pyodide-worker",
        "--out",
        str(OUT),
        "--requirements",
        wheel(),
        *REQUIREMENTS,
    ]
    print(" ".join(command))
    raise SystemExit(subprocess.call(command))


if __name__ == "__main__":
    main()
