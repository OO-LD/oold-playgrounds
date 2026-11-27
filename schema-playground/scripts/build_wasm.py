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
    "oold==2.3.3",
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
NO_DEPS = ["oold==2.3.3"]

#: The PyPI side of the closure, pinned to a resolution verified in the browser. micropip
#: takes the newest release that satisfies each requirement, and an unbounded requirement
#: anywhere in the tree lets a release nobody here made take the page down. That happened:
#: panel 1.9.4 asks for panel-material-ui>=0.10.0 with no upper bound, panel-material-ui
#: 0.17.0 then required panel>=1.10.0, and the transaction failed as a whole, so panel
#: never installed and the page died with ModuleNotFoundError.
#:
#: Passed as micropip constraints rather than requirements: a constraint pins a package if
#: something asks for it, and adds nothing that nothing asks for. Packages from the Pyodide
#: distribution are absent because the distribution version is already fixed by the pinned
#: Pyodide release.
#:
#: To move: resolve in Pyodide with these lifted, check the app, write the new versions here.
CONSTRAINTS = [
    "arrow==1.4.0",
    "bokeh==3.9.2",
    "fqdn==1.6.0",
    "frozendict==2.5.1",
    "isoduration==20.11.0",
    "jsonschema==4.26.0",
    "lark==1.3.1",
    "linkify-it-py==2.2.0",
    "markdown==3.11.4",
    "markdown-it-py==4.3.0",
    "mdit-py-plugins==0.7.0",
    "mdurl==0.1.2",
    "panel-material-ui==0.16.7",
    "param==2.5.0",
    "pyld==3.3.0",
    "pyviz-comms==3.0.6",
    "rdflib==7.6.0",
    "rfc3339-validator==0.1.4",
    "rfc3986-validator==0.1.1",
    "rfc3987-syntax==1.1.0",
    "uri-template==1.3.0",
    "webcolors==25.10.0",
]

PANELINI_REPO = "https://github.com/opensemanticworld/panelini.git"
#: A commit, not a branch. The branch this used to name was deleted when
#: opensemanticworld/panelini#60 merged, and the build died on the clone. This is the head
#: that shipped and that uv.lock resolves to; it is an ancestor of panelini main, so it
#: stays reachable. Its MonacoEditor loads its JS bundle from its parent, a748bb7.
PANELINI_REF = "38123add1831d68b935dcb12df7ae408f08262ee"


def build_wheel() -> str:
    """Build the app wheel into the output directory and return its filename."""
    subprocess.check_call(["uv", "build", "--wheel", "--out-dir", str(OUT)], cwd=ROOT)  # noqa: S603,S607 - fixed command
    wheels = sorted(OUT.glob("schema_playground-*.whl"), key=lambda p: p.stat().st_mtime)
    if not wheels:
        sys.exit(f"no wheel in {OUT} after the build")
    return wheels[-1].name


def build_panelini_wheel() -> str:
    """Build a panelini wheel from the commit the app depends on, into the output directory.

    Built rather than fetched: the MonacoEditor panel is not in a panelini release yet, and
    micropip cannot install from git.
    """
    import tempfile

    existing = sorted(OUT.glob("panelini-*.whl"), key=lambda p: p.stat().st_mtime)
    if existing:
        return existing[-1].name
    with tempfile.TemporaryDirectory() as workdir:
        # clone --branch takes refs only, so a commit is fetched into an empty repository.
        for command in (
            ["git", "init", "--quiet", workdir],
            ["git", "-C", workdir, "fetch", "--quiet", "--depth", "1", PANELINI_REPO, PANELINI_REF],
            ["git", "-C", workdir, "checkout", "--quiet", "FETCH_HEAD"],
        ):
            subprocess.check_call(command)  # noqa: S603,S607 - fixed command
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

    # The second install is the one that resolves dependencies, so that is where the pins go.
    # Matched on the line the deps=False insert above leaves behind, so a panel release that
    # reshapes the generated worker fails the build instead of shipping unpinned.
    resolving = re.search(r"(      await micropip\.install\(\[(?:(?!deps=False).)*?\])\);", text)
    if resolving is None:
        sys.exit(f"{worker.name}: no dependency-resolving micropip.install to attach constraints to")
    pins = ", ".join(f"'{c}'" for c in CONSTRAINTS)
    text = text.replace(resolving.group(0), f"{resolving.group(1)}, constraints=[{pins}]);", 1)

    worker.write_text(text, encoding="utf-8")
    print(f"patched {worker.name}: app wheel by URL; oold and panelini without their unused dependencies")
    print(f"patched {worker.name}: {len(CONSTRAINTS)} constraints on the resolving install")


#: Inserted after the live document is embedded. ``panel convert`` prerenders the app into
#: the page so something is visible while Pyodide boots; on the worker's render the glue
#: removes the prerendered views and DOM but leaves the prerendered ``Document`` registered.
#: That stale document keeps its whole model graph alive (a second copy of every editor) and
#: sits at ``Bokeh.documents[0]``, where any outside automation finds it first and talks to a
#: document nothing on screen listens to.
_DISPOSE_STALE_DOCS = """\
          const [views] = await Bokeh.embed.embed_items(docs_json, render_items)

          // The prerendered document is not removed with its views; drop it so
          // Bokeh.documents holds exactly the live one and its models can be collected.
          for (const staleDoc of Bokeh.documents.splice(0, Bokeh.documents.length - 1)) {
            staleDoc.clear()
          }
"""

#: A handle for outside automation: the live document, the editor models, and the element a
#: model renders into (Panel keeps model ids out of the DOM, so the view tree is the only
#: path from a model to its pixels).
_PLAYGROUND_HANDLE = """\
          pyodideWorker.jsdoc = jsdoc = [...views.roots.values()][0].model.document

          window.__playground = {
            get document() { return pyodideWorker.jsdoc },
            editors() {
              const out = []
              pyodideWorker.jsdoc._all_models.forEach((m) => {
                if (/Monaco/i.test(m.type || '')) out.push(m)
              })
              return out
            },
            viewOf(model) {
              // Panel ESM components split in two: the wrapper model owns the view, its
              // .data model carries the synced properties (and is what editors() returns).
              const stack = Object.values(Bokeh.index)
              while (stack.length) {
                const view = stack.pop()
                if (view.model === model || view.model?.data === model) return view
                if (view.child_views) stack.push(...view.child_views)
              }
              return null
            },
            elementOf(model) {
              const view = this.viewOf(model)
              return view ? view.el : null
            },
          }
"""


def patch_page(page: Path) -> None:
    """Drop the prerendered document once the live one is embedded, and expose a handle."""
    text = page.read_text(encoding="utf-8")

    embed = "          const [views] = await Bokeh.embed.embed_items(docs_json, render_items)\n"
    if embed not in text:
        sys.exit(f"{page.name}: the embed call the disposal hooks onto was not found")
    text = text.replace(embed, _DISPOSE_STALE_DOCS, 1)

    jsdoc = "          pyodideWorker.jsdoc = jsdoc = [...views.roots.values()][0].model.document\n"
    if jsdoc not in text:
        sys.exit(f"{page.name}: the jsdoc assignment the handle hooks onto was not found")
    text = text.replace(jsdoc, _PLAYGROUND_HANDLE, 1)

    page.write_text(text, encoding="utf-8")
    print(f"patched {page.name}: stale prerender document disposed; window.__playground exposed")


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
    patch_page(OUT / f"{APP.stem}.html")
    print(f"Serve it with: python -m http.server --directory {OUT}")


if __name__ == "__main__":
    main()
