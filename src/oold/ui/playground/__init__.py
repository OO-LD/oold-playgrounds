"""An interactive OO-LD playground.

Shows a schema and an instance side by side with a second schema and the same instance read
through it, so that what a mapping set changes - and what survives a transformation - is
visible rather than described.

Deliberately narrow imports: this package uses :mod:`oold.validation`, :mod:`oold.utils` and
:mod:`oold.ui.url_config` only, never :mod:`oold.model`, so the whole app stays installable in
a browser through Pyodide.

Run it with ``oold playground``, or::

    panel serve examples/oold_playground.py --dev
"""

from oold.ui.playground.app import build, serve
from oold.ui.playground.config import PlaygroundConfig
from oold.ui.playground.state import PlaygroundState

__all__ = ["PlaygroundConfig", "PlaygroundState", "build", "serve"]
