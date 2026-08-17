"""The ``oold`` command line entry point.

A thin group that currently hosts the validation commands and leaves room for future
non-validation subcommands. The implementation lives in :mod:`oold.validation.cli`, which is
also bound directly to ``oold-validate`` for compatibility with the reference harness's
``npx oold-validate <dir>``.

Validation needs the ``validation`` extra, so an import failure is reported as an actionable
message rather than a traceback.
"""

from __future__ import annotations

import sys

INSTALL_HINT = (
    "The validation commands need extra dependencies.\n"
    '  pip install "oold[validation]"\n'
    "  uv sync --all-extras     (in a checkout of this repository)"
)

PLAYGROUND_HINT = (
    "The playground needs extra dependencies.\n"
    '  pip install "oold[playground]"\n'
    "  uv sync --all-extras     (in a checkout of this repository)"
)


def _playground(argv: list[str]) -> None:
    """Serve the playground.

    Handled before the validation group is imported, so neither extra requires the other to
    be installed.
    """
    import argparse

    parser = argparse.ArgumentParser(prog="oold playground", description="Serve the OO-LD playground.")
    parser.add_argument("--port", type=int, default=5006)
    parser.add_argument("--no-show", action="store_true", help="do not open a browser")
    args = parser.parse_args(argv)

    try:
        from oold.ui.playground import serve
    except ImportError as exc:  # pragma: no cover - depends on the install
        print(f"oold: {exc}\n\n{PLAYGROUND_HINT}", file=sys.stderr)
        raise SystemExit(2) from exc

    serve(port=args.port, show=not args.no_show)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "playground":
        _playground(sys.argv[2:])
        return

    try:
        import click  # noqa: F401

        from oold.validation.cli import main as validation_main
    except ImportError as exc:  # pragma: no cover - depends on the install
        print(f"oold: {exc}\n\n{INSTALL_HINT}", file=sys.stderr)
        raise SystemExit(2) from exc

    validation_main()


if __name__ == "__main__":
    main()
