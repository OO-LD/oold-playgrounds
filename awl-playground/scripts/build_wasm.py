"""Convert the playground into a static browser (Pyodide) application.

Builds the ``awl`` wheel from a sibling checkout and the ``awl_playground`` wheel from
here, converts ``app.py`` with ``panel convert``, and patches the generated worker the way
``schema-playground/scripts/build_wasm.py`` does, for the same reasons: micropip treats a
bare wheel filename as a package name and tries to unzip the HTML error page an index hands
back, and ``panel convert`` points at a bokeh wheel on the Holoviz CDN that answers 403.

``awl`` is installed with ``deps=False``. Its declared dependencies cover the whole
pipeline, and the editor imports none of them: importing ``awl.ui.panel_reactflow`` pulls
Panel and nothing from ``rdflib``, ``pyld`` or ``oold``. Listing what the browser needs
explicitly means an import that needs a new package fails here rather than in someone's
browser.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "dist"
APP = ROOT / "app.py"

#: What the browser needs beyond the two local wheels.
REQUIREMENTS = [
    "param",
    "panel",
    # The editor reads and rewrites source by span, which is what asttokens is for.
    "asttokens",
    "pyodide-http",
]

#: Installed without their dependency lists. Local wheels are added by the patcher.
NO_DEPS: list[str] = []


def find_source() -> Path:
    """Return the ``awl-python`` checkout to build the wheel from.

    Searched for rather than configured, so a fresh clone beside this one just works.
    ``AWL_SRC`` overrides it, which is what a worktree needs: the editor variant lives on
    a branch until it lands.
    """
    override = os.environ.get("AWL_SRC")
    if override:
        return Path(override).resolve()
    here = ROOT
    for _ in range(4):
        candidate = (here / ".." / "awl-python").resolve()
        if (candidate / "pyproject.toml").exists():
            return candidate
        here = (here / "..").resolve()
    sys.exit("awl-python checkout not found; set AWL_SRC")


def build_wheel(source: Path, pattern: str) -> str:
    """Build a wheel from *source* into the output directory and return its filename."""
    subprocess.check_call(["uv", "build", "--wheel", "--out-dir", str(OUT)], cwd=source)  # noqa: S603,S607
    wheels = sorted(OUT.glob(pattern), key=lambda path: path.stat().st_mtime)
    if not wheels:
        sys.exit(f"no wheel matching {pattern} in {OUT} after the build")
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
        # Addressed relative to the worker's own location, so the build is portable to
        # whatever path it is deployed under.
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
    print(f"patched {worker.name}: local wheels by URL, installed without their unused dependencies")


def main() -> None:
    source = find_source()
    print(f"building the awl wheel from {source}")
    wheel_names = [build_wheel(source, "awl-*.whl"), build_wheel(ROOT, "awl_playground-*.whl")]

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
