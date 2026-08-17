"""A row of panes, any of which can be collapsed so the rest take the space.

Panel has no collapsible *column*: ``Accordion`` and ``Card`` collapse vertically, which is
the wrong axis for side-by-side documents. This is the missing piece, kept free of anything
playground-specific so it can move into panelini later.

Every view is built once and collapsing only flips ``visible``. Rebuilding the row's objects
instead would re-instantiate each pane's whole component tree - editors, tabs, a network
canvas - on every click, which is slow enough to feel broken. Nothing here reassigns
``objects`` after construction.

A collapsed pane keeps a narrow strip carrying its title and the button that brings it back,
because a pane that disappears completely leaves the user with no way to find it again.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import panel as pn
import param

#: Width of the strip a collapsed pane leaves behind.
COLLAPSED_WIDTH = 38


class Pane(param.Parameterized):
    """One collapsible column: a title, a body, and whether it is open."""

    title = param.String(default="")
    collapsed = param.Boolean(default=False)

    def __init__(self, title: str, body: Any, collapsed: bool = False, **params: Any) -> None:
        super().__init__(title=title, collapsed=collapsed, **params)
        self.body = body


class _PaneView:
    """The two states of one pane, both built once and shown by turns."""

    def __init__(self, pane: Pane, on_toggle: Callable[[Pane], None]) -> None:
        self.pane = pane

        collapse = pn.widgets.Button(name="«", width=32, button_type="light", description=f"Collapse {pane.title}")
        collapse.on_click(lambda _event: on_toggle(pane))

        expand = pn.widgets.Button(
            name="»",
            width=COLLAPSED_WIDTH - 8,
            button_type="light",
            description=f"Expand {pane.title}",
        )
        expand.on_click(lambda _event: on_toggle(pane))

        header = pn.Row(
            pn.pane.HTML(f"<b>{pane.title}</b>", margin=(6, 4)),
            pn.HSpacer(),
            collapse,
            sizing_mode="stretch_width",
            margin=0,
        )
        self.open = pn.Column(
            header,
            pane.body,
            sizing_mode="stretch_both",
            margin=(0, 2),
            styles={"min-width": "220px"},
            visible=not pane.collapsed,
        )

        # One letter per line rather than a rotated label: the CSS for rotation behaves
        # differently across the themes panelini ships.
        letters = "<br>".join(pane.title[:18])
        self.strip = pn.Column(
            expand,
            pn.pane.HTML(
                "<div style='text-align:center;font-size:11px;"
                f"color:var(--muted-text-color,#666);line-height:1.05'>{letters}</div>",
                sizing_mode="stretch_height",
            ),
            width=COLLAPSED_WIDTH,
            sizing_mode="stretch_height",
            margin=(0, 2),
            visible=pane.collapsed,
        )

    def sync(self) -> None:
        """Show whichever of the two states the pane is now in."""
        self.open.visible = not self.pane.collapsed
        self.strip.visible = self.pane.collapsed


class SplitColumns(pn.viewable.Viewer):
    """Panes side by side, each collapsible; the open ones share the width."""

    def __init__(
        self,
        panes: Iterable[Pane],
        on_change: Callable[[], None] | None = None,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self._panes = list(panes)
        self._on_change = on_change
        self._views = [_PaneView(pane, self._toggle) for pane in self._panes]

        for pane, view in zip(self._panes, self._views, strict=True):
            pane.param.watch(lambda _event, view=view: self._sync(view), "collapsed")

        objects: list[Any] = []
        for view in self._views:
            objects.extend((view.open, view.strip))
        self._row = pn.Row(*objects, sizing_mode="stretch_both", scroll=False)

    def _sync(self, view: _PaneView) -> None:
        view.sync()
        if self._on_change is not None:
            self._on_change()

    def _toggle(self, pane: Pane) -> None:
        pane.collapsed = not pane.collapsed

    def collapse(self, *titles: str, collapsed: bool = True) -> None:
        """Collapse or expand panes by title, for callers that own the layout."""
        for pane in self._panes:
            if pane.title in titles:
                pane.collapsed = collapsed

    def __panel__(self) -> pn.Row:
        return self._row
