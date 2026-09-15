"""The AWL-LD playground: a procedure as blocks, as source, and run.

The canvas still lives in ``awl.ui.panel_reactflow`` and is re-exported here.
It moves into this package once the branch it is being built on settles; until
then it is imported rather than copied, because two copies of an editor is how
a variant ends up measuring its own reimplementation rather than the model
every variant is supposed to share.
"""

from __future__ import annotations

from typing import Any

from awl.ui.panel_reactflow import open_sample

__all__ = ["build", "open_sample"]


def build() -> Any:
    """Return the playground, ready to be made servable."""
    return open_sample().__panel__()
