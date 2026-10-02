"""Work out which wheels piplite has to carry so a cold start never reaches PyPI.

micropip resolves a name against the Pyodide distribution lock first, then against the
piplite indexes, then against PyPI. Anything in the lock already sits on the CDN next to
the runtime and must not be vendored. Everything else in the closure of what
``oold_agent.setup()`` installs has to ship with the site.

Two modes:

``resolve``  prints a pinned requirement per line, the content of piplite-requirements.txt.
``verify``   walks the staged wheels and fails if the closure is not covered by the staged
             set plus the Pyodide lock, which is what would send micropip to PyPI at runtime.

Both reject anything that has no pure-python wheel: a compiled artifact can only come from
the Pyodide distribution.
"""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tempfile
import urllib.request
import zipfile
from email.parser import Parser
from pathlib import Path

from packaging.markers import Marker
from packaging.requirements import Requirement
from packaging.utils import parse_wheel_filename
from packaging.version import Version

PYODIDE_ENV = {
    "extra": "",
    "implementation_name": "cpython",
    "implementation_version": "3.14.0",
    "os_name": "posix",
    "platform_machine": "wasm32",
    "platform_release": "4.0.0",
    "platform_system": "Emscripten",
    "platform_version": "#1",
    "python_full_version": "3.14.0",
    "python_version": "3.14",
    "sys_platform": "emscripten",
}

# oold_agent.setup() installs these three ahead of REQUIREMENTS.
EXTRA_SEEDS = ("typing-extensions>=4.14.0", "pyodide-http", "oold")

# The kernel bootstrap counts too: jupyterlite-pyodide-kernel bundles ipykernel,
# pyodide-kernel and piplite as wheels inside its labextension, but not their
# dependencies. pyodide-kernel requires comm, which the Pyodide distribution does not
# carry, so without it in the site index the kernel never reaches kernel_info_reply
# when PyPI is unreachable.
BUNDLED_SEEDS = ("ipykernel", "piplite", "pyodide-kernel")


def normalize(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def pyodide_packages(version: str, cache_dir: Path) -> dict[str, str]:
    cache = cache_dir / f"pyodide-lock-{version}.json"
    cache.parent.mkdir(parents=True, exist_ok=True)
    if not cache.exists():
        url = f"https://cdn.jsdelivr.net/pyodide/v{version}/full/pyodide-lock.json"
        with urllib.request.urlopen(url) as resp:
            cache.write_bytes(resp.read())
    lock = json.loads(cache.read_text(encoding="utf-8"))
    return {normalize(k): v["version"] for k, v in lock["packages"].items()}


def agent_seeds(agent_py: Path) -> list[str]:
    """The REQUIREMENTS tuple of oold_agent, read rather than duplicated.

    A requirement added there has to be vendored too, so this build fails instead of
    quietly falling back to PyPI on the next cold start.
    """
    tree = ast.parse(agent_py.read_text(encoding="utf-8"))
    for node in tree.body:
        targets = getattr(node, "targets", [])
        if any(isinstance(t, ast.Name) and t.id == "REQUIREMENTS" for t in targets):
            return list(ast.literal_eval(node.value))
    raise SystemExit(f"no REQUIREMENTS assignment in {agent_py}")


def wheel_metadata(wheel: Path) -> tuple[str, str, list[str]]:
    with zipfile.ZipFile(wheel) as zf:
        name = next(n for n in zf.namelist() if n.endswith(".dist-info/METADATA"))
        msg = Parser().parsestr(zf.read(name).decode("utf-8", "replace"))
    return msg["Name"], msg["Version"], msg.get_all("Requires-Dist") or []


def runtime_requires(raw_requires: list[str]) -> list[Requirement]:
    out = []
    for raw in raw_requires:
        dep = Requirement(raw)
        if dep.marker is not None and not Marker(str(dep.marker)).evaluate(PYODIDE_ENV):
            continue
        out.append(dep)
    return out


def is_pure(wheel: Path) -> bool:
    _, _, _, tags = parse_wheel_filename(wheel.name)
    return all(t.platform == "any" and t.abi == "none" for t in tags)


def pip_download(python: str, requirement: str, dest: Path) -> list[Path]:
    before = set(dest.glob("*.whl"))
    subprocess.run(
        [
            python, "-m", "pip", "download", requirement,
            "--dest", str(dest),
            "--no-deps",
            "--only-binary=:all:",
            "--platform", "any",
            "--python-version", "3.14",
            "--implementation", "py",
            "--quiet",
        ],
        check=True,
    )
    return sorted(set(dest.glob("*.whl")) - before)


def bundled_wheels(directories: list[str]) -> dict[str, tuple[Version, Path]]:
    out: dict[str, tuple[Version, Path]] = {}
    for directory in directories:
        for wheel in sorted(Path(directory).glob("*.whl")):
            name, version, _ = wheel_metadata(wheel)
            key = normalize(name)
            current = out.get(key)
            if current is None or Version(version) > current[0]:
                out[key] = (Version(version), wheel)
    return out


def cmd_resolve(args: argparse.Namespace) -> int:
    in_pyodide = pyodide_packages(args.pyodide_version, Path(args.cache))
    local = {normalize(n) for n in args.local}
    bundled = bundled_wheels(args.bundled)

    queue = [*BUNDLED_SEEDS, *EXTRA_SEEDS, *agent_seeds(Path(args.agent))]
    seen: set[str] = set()
    resolved: dict[str, str] = {}
    from_pyodide: dict[str, str] = {}

    with tempfile.TemporaryDirectory() as tmp:
        dest = Path(tmp)
        while queue:
            requirement = queue.pop(0)
            req = Requirement(requirement)
            key = normalize(req.name)
            if key in seen:
                continue
            seen.add(key)
            if key in bundled:
                _, _, requires = wheel_metadata(bundled[key][1])
                for dep in runtime_requires(requires):
                    queue.append(f"{dep.name}{dep.specifier}" if dep.specifier else dep.name)
                continue
            if key in in_pyodide:
                from_pyodide[key] = in_pyodide[key]
                continue
            try:
                wheels = pip_download(args.python, requirement, dest)
            except subprocess.CalledProcessError:
                print(f"no pure-python wheel for {requirement}", file=sys.stderr)
                return 1
            if not wheels:
                continue
            name, version, requires = wheel_metadata(wheels[0])
            if key not in local:
                resolved[normalize(name)] = version
            for dep in runtime_requires(requires):
                queue.append(f"{dep.name}{dep.specifier}" if dep.specifier else dep.name)

    for key, version in sorted(from_pyodide.items()):
        print(f"# pyodide distribution: {key} {version}", file=sys.stderr)
    for key, version in sorted(resolved.items()):
        print(f"{key}=={version}")
    return 0


def cmd_verify(args: argparse.Namespace) -> int:
    in_pyodide = pyodide_packages(args.pyodide_version, Path(args.cache))

    staged = bundled_wheels(args.dir)
    problems: list[str] = []
    for key, (_, wheel) in sorted(staged.items()):
        if not is_pure(wheel):
            problems.append(f"{wheel.name} is not a pure-python wheel")
    staged.update(bundled_wheels(args.bundled))

    queue = [*BUNDLED_SEEDS, *EXTRA_SEEDS, *agent_seeds(Path(args.agent))]
    seen: set[str] = set()
    while queue:
        requirement = queue.pop(0)
        req = Requirement(requirement)
        key = normalize(req.name)
        if key in seen:
            continue
        seen.add(key)
        if key in staged:
            version, wheel = staged[key]
            if req.specifier and not req.specifier.contains(version, prereleases=True):
                problems.append(f"{key} {version} staged, but {requirement} was asked for")
            _, _, requires = wheel_metadata(wheel)
            for dep in runtime_requires(requires):
                queue.append(f"{dep.name}{dep.specifier}" if dep.specifier else dep.name)
            continue
        if key in in_pyodide:
            version = Version(in_pyodide[key])
            if req.specifier and not req.specifier.contains(version, prereleases=True):
                problems.append(
                    f"{key} {version} in the pyodide distribution, but {requirement} was asked for"
                )
            continue
        problems.append(f"{key} is neither staged nor in the pyodide distribution")

    print(f"staged {len(staged)} wheels, closure of {len(seen)} distributions")
    if problems:
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print("piplite wheels cover the runtime closure")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["resolve", "verify"])
    ap.add_argument("--python", default=sys.executable)
    ap.add_argument("--pyodide-version", required=True)
    ap.add_argument("--cache", required=True)
    ap.add_argument("--agent", required=True)
    ap.add_argument("--dir", action="append", default=[])
    ap.add_argument("--bundled", action="append", default=[])
    ap.add_argument("--local", action="append", default=["oold"])
    args = ap.parse_args()
    return cmd_resolve(args) if args.mode == "resolve" else cmd_verify(args)


if __name__ == "__main__":
    raise SystemExit(main())
