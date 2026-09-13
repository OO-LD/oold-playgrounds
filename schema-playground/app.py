"""Serve the OO-LD playground.

    uv run panel serve app.py --dev

Everything the app does lives in :mod:`schema_playground`; this file only makes it servable,
so the same code runs under ``panel serve`` and under ``panel convert`` for the browser build.
"""

from schema_playground import build

build().servable()
