"""Convert the playground into a static browser (Pyodide) application.

Builds the ``schema_playground`` wheel, converts ``app.py`` with ``panel convert``, and then
patches the generated worker, because the install list it writes fails in three ways that are
each fatal (micropip raises, nothing is installed, the app dies on ``import panel``):

* a bare wheel filename is treated as a package name, so micropip asks an index and tries to
  unzip the HTML error page it gets back (``BadZipFile``); the wheel is addressed relative to
  the worker's own location instead, which keeps the build portable to any deployment path;
* ``panel convert`` points at a bokeh wheel on its own CDN that is not published there (the
  URL answers 403); the same version is on PyPI, so it is requested by name;
* installing ``oold`` and ``panelini`` with their dependency lists pulls packages a browser
  cannot carry - ``oold`` drags in the code generator and the SPARQL client the playground
  never imports, ``panelini`` requires ``watchfiles``, a Rust extension with no pure-Python
  wheel. Both are installed with ``deps=False``; what the app actually needs is listed in
  :data:`REQUIREMENTS`.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist"
APP = ROOT / "app.py"

#: What the browser needs. Kept explicit so that adding an import that needs a new package
#: fails here rather than in someone's browser. panelini is absent: it comes from a git
#: branch (see pyproject.toml), which micropip cannot install, so its wheel is built locally
#: and shipped beside the app's.
REQUIREMENTS = [
    "oold>=0.20",
    "param",
    "pydantic",
    "pyld",
    "rdflib",
    "pyyaml",
    # Pinned past the copy Pyodide ships (4.23): the iri checker only registers through the
    # non-GPL rfc3987-syntax from 4.25 on, and micropip would otherwise consider the shipped
    # version satisfying and skip the upgrade.
    "jsonschema>=4.25",
    # jsonschema's format checkers register only when these are importable, and
    # oold.validation copies them out of the 2020-12 checker at import time, dying with a
    # KeyError without them. Listed explicitly: micropip skips the extras of a requirement it
    # considers already satisfied, so jsonschema[format-nongpl] installs nothing.
    "isoduration",
    "rfc3339-validator",
    "rfc3986-validator",
    "rfc3987-syntax",
    "uri-template",
    "fqdn",
    "idna",
    "webcolors",
    "jsonpointer",
    "referencing",
    "click",
    "pyodide-http",
]

#: Installed without their dependency lists (see the module docstring). Local wheels are
#: added to this group by the patcher.
NO_DEPS = ["oold>=0.20"]

PANELINI_REPO = "https://github.com/opensemanticworld/panelini.git"
PANELINI_REF = "monaco-editor-panel"


def build_wheel() -> str:
    """Build the app wheel into the output directory and return its filename."""
    subprocess.check_call(["uv", "build", "--wheel", "--out-dir", str(OUT)], cwd=ROOT)  # noqa: S603,S607 - fixed command
    wheels = sorted(OUT.glob("schema_playground-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        sys.exit(f"no wheel in {OUT} after the build")
    return wheels[-1].name


def build_panelini_wheel() -> str:
    """Build a panelini wheel from the branch the app depends on, into the output directory.

    Built rather than fetched: the MonacoEditor panel is not in a panelini release yet, and
    micropip cannot install from git.
    """
    import tempfile

    existing = sorted(OUT.glob("panelini-*.whl"), key=lambda p: p.stat().st_mtime)
    if existing:
        return existing[-1].name
    with tempfile.TemporaryDirectory() as workdir:
        subprocess.check_call(  # noqa: S603,S607 - fixed command
            ["git", "clone", "--depth", "1", "--branch", PANELINI_REF, PANELINI_REPO, workdir]
        )
        subprocess.check_call(["uv", "build", "--wheel", "--out-dir", str(OUT)], cwd=workdir)  # noqa: S603,S607
    wheels = sorted(OUT.glob("panelini-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        sys.exit(f"no panelini wheel in {OUT} after the build")
    return wheels[-1].name


def patch_worker(worker: Path, wheel_names: list[str]) -> None:
    """Make the generated worker install the local wheels and the trimmed dependency set."""
    text = worker.read_text(encoding="utf-8")

    without_deps = []
    for wheel_name in wheel_names:
        listed = f"'{wheel_name}', "
        if listed not in text:
            sys.exit(f"{worker.name}: expected {wheel_name} in the install list")
        text = text.replace(listed, "", 1)
        without_deps.append(f"'${{new URL(\"{wheel_name}\", self.location.href).href}}'")

    for name in NO_DEPS:
        if f"'{name}', " in text:
            text = text.replace(f"'{name}', ", "", 1)
        elif f", '{name}'" in text:
            text = text.replace(f", '{name}'", "", 1)
        without_deps.append(f"'{name}'")

    text = text.replace(
        "      await micropip.install([",
        f"      await micropip.install([{', '.join(without_deps)}], deps=False);\n"
        "      await micropip.install([",
        1,
    )

    broken = re.search(r"'https://cdn\.holoviz\.org/panel/wheels/bokeh-([0-9][^']*)-py3-none-any\.whl'", text)
    if broken:
        text = text.replace(broken.group(0), f"'bokeh=={broken.group(1)}'", 1)
        print(f"patched {worker.name}: bokeh {broken.group(1)} taken from PyPI, not the CDN")

    worker.write_text(text, encoding="utf-8")
    print(f"patched {worker.name}: app wheel by URL; oold and panelini without their unused dependencies")


def main() -> None:
    wheel_names = [build_wheel(), build_panelini_wheel()]
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
        *wheel_names,
        *REQUIREMENTS,
    ]
    print(" ".join(command))
    code = subprocess.call(command)  # noqa: S603 - the command is built here, not supplied
    if code:
        raise SystemExit(code)

    patch_worker(OUT / f"{APP.stem}.js", wheel_names)
    print(f"Serve it with: python -m http.server --directory {OUT}")


if __name__ == "__main__":
    main()
