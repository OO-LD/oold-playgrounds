"""The AWL-LD playground: a procedure as blocks, as source, and run.

Only the deployment lives here. The editor is ``awl.ui.panel_reactflow`` and
stays in ``awl-python`` beside the model it is built on, because a copy of an
editor is how one ends up measuring its own reimplementation rather than the
model. What this package adds is what a browser build needs and a library
should not carry: an entry point, the wheel set, and the patches that make
``panel convert`` produce something that boots.
"""

from __future__ import annotations

from typing import Any

from awl.ui.panel_reactflow import open_sample

__all__ = ["build", "open_sample"]


def build() -> Any:
    """Return the playground, ready to be made servable."""
    return open_sample().__panel__()
