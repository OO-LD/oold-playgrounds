"""Serve the AWL-LD playground.

    uv run panel serve app.py --dev

Everything the app does lives in :mod:`awl.ui.panel_reactflow`; this file only makes it
servable, so the same code runs under ``panel serve`` and under ``panel convert`` for the
browser build.
"""

from awl_playground import build

build().servable()
