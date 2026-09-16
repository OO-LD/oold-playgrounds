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
    # The canvas. Installed by name because it is a pure-Python wheel on PyPI,
    # and required because the app imports it: without it the page boots, the
    # worker raises ModuleNotFoundError and the console says only
    # "Environment loaded!".
    "panel-reactflow>=0.4.1",
    # The editor reads and rewrites source by span, which is what asttokens is for.
    "asttokens",
    "pyodide-http",
]

#: Installed without their dependency lists. Local wheels are added by the
#: patcher. ``awl`` is here because its declared dependencies cover the whole
#: pipeline and the editor imports none of them: ``rdflib``, ``pyld`` and
#: ``oold`` are reached only inside functions this app never calls.
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
    subprocess.check_call(["uv", "build", "--wheel", "--out-dir", str(OUT)], cwd=source)
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
        without_deps.append(
            f"'${{new URL(\"{wheel_name}\", self.location.href).href}}'"
        )

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

    broken = re.search(
        r"'https://cdn\.holoviz\.org/panel/wheels/bokeh-([0-9][^']*)-py3-none-any\.whl'",
        text,
    )
    if broken:
        text = text.replace(broken.group(0), f"'bokeh=={broken.group(1)}'", 1)
        print(
            f"patched {worker.name}: bokeh {broken.group(1)} taken from PyPI, not the CDN"
        )

    worker.write_text(text, encoding="utf-8")
    print(
        f"patched {worker.name}: local wheels by URL, installed without their unused dependencies"
    )


def main() -> None:
    source = find_source()
    print(f"building the awl wheel from {source}")
    wheel_names = [
        build_wheel(source, "awl-*.whl"),
        build_wheel(ROOT, "awl_playground-*.whl"),
    ]

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
    code = subprocess.call(command)
    if code:
        raise SystemExit(code)
    # `panel convert` prerenders the app by importing it, and reports an import
    # failure as "does not publish any Panel contents" while exiting 0. Without
    # this the script died twenty lines later on a missing app.js and named the
    # wrong cause.
    if not (OUT / f"{APP.stem}.js").exists():
        sys.exit(
            f"panel convert wrote no {APP.stem}.js; the app most likely failed to import"
        )

    patch_worker(OUT / f"{APP.stem}.js", wheel_names)
    copy_extension_assets()
    copy_type_checker()
    print(f"Serve it with: python -m http.server --directory {OUT}")


def copy_type_checker() -> None:
    """Copy ty's WebAssembly next to the page, when a build of it is at hand.

    The editor mounts it at ``/ty-wasm/`` and degrades to no type checking when
    it is absent, which is the right failure: 17.8 MB is six times the rest of
    the app and not every deployment wants to carry it. Taken from the sibling
    ``ty-playground``, which is where it is built, rather than rebuilt here:
    that needs Docker and a checkout of ruff.
    """
    import shutil

    override = os.environ.get("AWL_TY_WASM")
    source = (
        Path(override).resolve()
        if override
        else (ROOT / ".." / "ty-playground" / "ty_wasm").resolve()
    )
    if not (source / "ty_wasm.js").exists():
        print(
            f"no ty_wasm at {source}; the pane will edit and highlight without type checking"
        )
        return
    target = OUT / "ty-wasm"
    target.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target, dirs_exist_ok=True)
    carried = sum(path.stat().st_size for path in target.rglob("*") if path.is_file())
    print(f"copied ty into {target.relative_to(OUT)} ({carried / 1_000_000:.1f} MB)")


def copy_extension_assets() -> None:
    """Copy the canvas's stylesheets next to the page that asks for them.

    ``panel convert`` writes ``<link>`` tags pointing at
    ``static/extensions/<package>/`` and emits nothing there, because a Panel
    *server* serves those from the installed package and a static build has no
    server. Without this the page 404s on them and the canvas renders unstyled,
    which reads as a broken layout rather than as a missing file.
    """
    import re
    import shutil

    page = (OUT / f"{APP.stem}.html").read_text(encoding="utf-8")
    wanted = set(re.findall(r"static/extensions/([^/]+)/", page))
    for package in sorted(wanted):
        try:
            source = (
                Path(__import__(package.replace("-", "_")).__file__).parent / "dist"
            )
        except (ImportError, AttributeError, TypeError):
            print(f"no installed package for {package}; its assets are not copied")
            continue
        if not source.is_dir():
            print(f"{package} ships no dist/; its assets are not copied")
            continue
        target = OUT / "static" / "extensions" / package
        target.mkdir(parents=True, exist_ok=True)
        shutil.copytree(source, target, dirs_exist_ok=True)
        print(f"copied {package} assets into {target.relative_to(OUT)}")


if __name__ == "__main__":
    main()
