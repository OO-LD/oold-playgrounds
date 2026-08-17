"""Serve the OO-LD playground.

    panel serve examples/oold_playground.py --dev

Everything the app does lives in :mod:`oold.ui.playground`; this file only makes it servable,
so the same code runs under ``panel serve`` and under ``panel convert`` for the browser build.
"""

from oold.ui.playground import build

build().servable()
