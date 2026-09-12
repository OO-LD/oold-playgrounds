"""An interactive OO-LD playground.

Shows a schema and an instance side by side with a second schema and the same instance read
through it, so that what a mapping set changes - and what survives a transformation - is
visible rather than described.

Deliberately narrow imports: from ``oold`` this package uses :mod:`oold.validation` only,
never :mod:`oold.model`, so the whole app stays installable in a browser through Pyodide.

Run it with::

    uv run panel serve app.py --dev
"""

from schema_playground.app import build, serve
from schema_playground.config import PlaygroundConfig
from schema_playground.state import PlaygroundState

__all__ = ["PlaygroundConfig", "PlaygroundState", "build", "serve"]
