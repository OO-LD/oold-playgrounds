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

The container is a flex box rather than a row: below the width the open panes need, they wrap
onto a second line instead of pushing the rightmost pane off the screen, where a user on a
laptop would never discover it.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

import panel as pn
import param

#: Width of the strip a collapsed pane leaves behind.
COLLAPSED_WIDTH = 38
#: Height of that strip: roughly one editor, so the layout does not jump on collapse.
STRIP_HEIGHT = 420
#: Below this width an open pane wraps onto the next line rather than shrinking further.
PANE_MIN_WIDTH = 320


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

        collapse = pn.widgets.Button(
            name="«",
            width=32,
            button_type="light",
            description=f"Collapse {pane.title}",
            description_delay=800,
        )
        collapse.on_click(lambda _event: on_toggle(pane))

        expand = pn.widgets.Button(
            name="»",
            width=COLLAPSED_WIDTH - 8,
            button_type="light",
            description=f"Expand {pane.title}",
            description_delay=800,
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
            sizing_mode="stretch_width",
            margin=(0, 2),
            # In the flex container: share the line with the other open panes, wrap rather
            # than shrink below a readable editor width.
            styles={"flex": f"1 1 {PANE_MIN_WIDTH}px", "min-width": f"{PANE_MIN_WIDTH}px"},
            visible=not pane.collapsed,
        )

        # writing-mode rather than a transform rotation: a rotated element keeps its
        # horizontal box in the layout, writing-mode gives the strip its real narrow one.
        self.strip = pn.Column(
            expand,
            pn.pane.HTML(
                "<div style='writing-mode:vertical-rl;font-size:12px;"
                "color:var(--muted-text-color,#666);white-space:nowrap;overflow:hidden;"
                f"text-overflow:ellipsis;max-height:{STRIP_HEIGHT - 60}px;margin:6px auto'>"
                f"{pane.title}</div>",
                sizing_mode="stretch_height",
            ),
            width=COLLAPSED_WIDTH,
            height=STRIP_HEIGHT,
            margin=(0, 2),
            styles={"flex": f"0 0 {COLLAPSED_WIDTH}px"},
            visible=pane.collapsed,
        )

    def sync(self) -> None:
        """Show whichever of the two states the pane is now in."""
        self.open.visible = not self.pane.collapsed
        self.strip.visible = self.pane.collapsed


class SplitColumns(pn.viewable.Viewer):
    """Panes side by side, each collapsible; the open ones share the width.

    ``on_change`` is called after any collapse or expand, however triggered; it may also be
    assigned after construction.
    """

    def __init__(
        self,
        panes: Iterable[Pane],
        on_change: Callable[[], None] | None = None,
        **params: Any,
    ) -> None:
        super().__init__(**params)
        self._panes = list(panes)
        self.on_change = on_change
        self._views = [_PaneView(pane, self._toggle) for pane in self._panes]

        for pane, view in zip(self._panes, self._views, strict=True):
            pane.param.watch(lambda _event, view=view: self._sync(view), "collapsed")

        objects: list[Any] = []
        for view in self._views:
            objects.extend((view.open, view.strip))
        self._row = pn.FlexBox(
            *objects,
            sizing_mode="stretch_width",
            styles={"gap": "4px", "align-items": "flex-start"},
        )

    def _sync(self, view: _PaneView) -> None:
        view.sync()
        if self.on_change is not None:
            self.on_change()

    def _toggle(self, pane: Pane) -> None:
        pane.collapsed = not pane.collapsed

    def collapsed_titles(self) -> list[str]:
        """The currently collapsed panes, by title."""
        return [pane.title for pane in self._panes if pane.collapsed]

    def collapse(self, *titles: str, collapsed: bool = True) -> None:
        """Collapse or expand panes by title, for callers that own the layout."""
        for pane in self._panes:
            if pane.title in titles:
                pane.collapsed = collapsed

    def __panel__(self) -> pn.FlexBox:
        return self._row
