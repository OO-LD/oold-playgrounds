"""A row of panes, any of which can be collapsed so the rest take the space.

Panel has no collapsible *column*: ``Accordion`` and ``Card`` collapse vertically, which is
the wrong axis for side-by-side documents. This is the missing piece, kept free of anything
playground-specific so it can move into panelini later.

A collapsed pane keeps a narrow strip carrying its title and the button that brings it back,
because a pane that disappears completely leaves the user with no way to find it again.
"""

from __future__ import annotations

from typing import Any, Callable, Iterable

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
        self._row = pn.Row(sizing_mode="stretch_both", scroll=False)
        for pane in self._panes:
            pane.param.watch(lambda *_: self._rebuild(), "collapsed")
        self._rebuild()

    def _toggle(self, pane: Pane) -> Callable[[Any], None]:
        def handler(_event: Any) -> None:
            pane.collapsed = not pane.collapsed
            if self._on_change is not None:
                self._on_change()

        return handler

    def _render(self, pane: Pane) -> pn.Column:
        if pane.collapsed:
            button = pn.widgets.Button(
                name="»",
                width=COLLAPSED_WIDTH - 8,
                button_type="light",
                description=f"Expand {pane.title}",
            )
            button.on_click(self._toggle(pane))
            # The title is written one letter per line: a rotated label needs CSS that
            # behaves differently across the themes panelini ships.
            letters = "<br>".join(pane.title[:18])
            return pn.Column(
                button,
                pn.pane.HTML(
                    f"<div style='writing-mode:horizontal-tb;text-align:center;"
                    f"font-size:11px;color:var(--muted-text-color,#666);line-height:1.05'>"
                    f"{letters}</div>",
                    sizing_mode="stretch_height",
                ),
                width=COLLAPSED_WIDTH,
                sizing_mode="stretch_height",
                margin=(0, 2),
            )

        button = pn.widgets.Button(
            name="«", width=32, button_type="light", description=f"Collapse {pane.title}"
        )
        button.on_click(self._toggle(pane))
        header = pn.Row(
            pn.pane.HTML(f"<b>{pane.title}</b>", margin=(6, 4)),
            pn.HSpacer(),
            button,
            sizing_mode="stretch_width",
            margin=0,
        )
        return pn.Column(
            header,
            pane.body,
            sizing_mode="stretch_both",
            margin=(0, 2),
            styles={"min-width": "220px"},
        )

    def _rebuild(self) -> None:
        self._row.objects = [self._render(pane) for pane in self._panes]

    def collapse(self, *titles: str, collapsed: bool = True) -> None:
        """Collapse or expand panes by title, for callers that own the layout."""
        for pane in self._panes:
            if pane.title in titles:
                pane.collapsed = collapsed

    def __panel__(self) -> pn.Row:
        return self._row
