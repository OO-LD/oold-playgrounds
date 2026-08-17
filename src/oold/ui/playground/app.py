"""The playground, assembled.

Four columns sit in panelini's main frame, with the paste field between the two halves.
Expanding the paste field collapses the source columns, because it takes over their job: it
supplies the RDF the right-hand side reads. That is the only way the bus changes owner, which
is what keeps the data flow one-directional and free of the loop a bidirectional wiring would
create.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import panel as pn

from oold.ui.playground import config as cfg
from oold.ui.playground.columns import (
    EDITOR_HEIGHT,
    DocumentEditor,
    error_pane,
    instance_column,
    schema_column,
)
from oold.ui.playground.split import Pane, SplitColumns
from oold.ui.playground.state import PlaygroundState
from oold.utils.mappings import chain, set_name
from oold.utils.transform import JSON_LD, TURTLE

logger = logging.getLogger(__name__)

TITLE = "OO-LD Playground"
SOURCE_PANES = ("Source schema", "Source instance")


def _chain_of(state: PlaygroundState, field: str) -> list[dict[str, Any]]:
    text, error = cfg.resolve_source(getattr(state, field))
    if error:
        return []
    document, parse_error = cfg.parse_document(text)
    if parse_error or not isinstance(document, dict):
        return []
    return chain(document, None)


def paste_panel(state: PlaygroundState, split: SplitColumns) -> pn.Column:
    """The centre input: pasted RDF, in either notation, replacing the source columns."""
    toggle = pn.widgets.Toggle(
        name="Paste RDF instead",
        value=state.use_paste,
        button_type="primary",
        sizing_mode="stretch_width",
    )
    turtle_editor = pn.widgets.CodeEditor(
        value=state.pasted_rdf,
        language="text",
        sizing_mode="stretch_width",
        height=EDITOR_HEIGHT - 60,
        theme="github_light_default",
    )
    jsonld_editor = pn.widgets.CodeEditor(
        value="",
        language="json",
        sizing_mode="stretch_width",
        height=EDITOR_HEIGHT - 60,
        theme="github_light_default",
    )
    tabs = pn.Tabs(("Turtle", turtle_editor), ("JSON-LD", jsonld_editor), sizing_mode="stretch_width")
    body = pn.Column(tabs, error_pane(state, "bus_error"), sizing_mode="stretch_width", visible=state.use_paste)

    def apply(*_events: Any) -> None:
        if tabs.active == 0:
            state.paste_format, state.pasted_rdf = TURTLE, turtle_editor.value
        else:
            state.paste_format, state.pasted_rdf = JSON_LD, jsonld_editor.value

    turtle_editor.param.watch(apply, "value")
    jsonld_editor.param.watch(apply, "value")
    tabs.param.watch(apply, "active")

    def on_toggle(event: Any) -> None:
        state.use_paste = bool(event.new)
        body.visible = state.use_paste
        # The source columns no longer feed anything, so they fold away to give the pasted
        # graph and its readings the width.
        split.collapse(*SOURCE_PANES, collapsed=state.use_paste)
        if state.use_paste:
            apply()

    toggle.param.watch(on_toggle, "value")

    return pn.Column(
        toggle,
        body,
        width=320,
        sizing_mode="stretch_height",
        margin=(0, 4),
    )


def rdf_controls(state: PlaygroundState) -> pn.Row:
    """Serialization and mapping-set choice for the exported graph."""
    fmt = pn.widgets.Select(
        name="", options={"Turtle": TURTLE, "JSON-LD": JSON_LD}, value=state.rdf_format, width=110
    )
    fmt.param.watch(lambda event: setattr(state, "rdf_format", event.new), "value")

    mapping = pn.widgets.Select(name="", options={"consensus (declared)": ""}, value="", width=210)

    def refresh_options(*_events: Any) -> None:
        options = {"consensus (declared)": ""}
        for set_id in state.available_sets:
            options[set_name(set_id)] = set_id
        mapping.options = options
        if state.mapping_set not in options.values():
            mapping.value = ""

    refresh_options()
    state.param.watch(refresh_options, "available_sets")
    mapping.param.watch(lambda event: setattr(state, "mapping_set", event.new or ""), "value")

    return pn.Row(
        pn.pane.HTML("<small>Serialization</small>", margin=(8, 2)),
        fmt,
        pn.pane.HTML("<small>Mapping set</small>", margin=(8, 2)),
        mapping,
        sizing_mode="stretch_width",
        margin=0,
    )


def log_panel() -> pn.Card:
    """Application log, collapsed by default."""
    try:
        from panelini.panels.terminalmirror import TerminalMirror

        body: Any = TerminalMirror(mirror=True)
    except Exception:  # pragma: no cover - the terminal is a convenience, not a requirement
        body = pn.pane.HTML("<small>Log unavailable.</small>")
    return pn.Card(body, title="Log", collapsed=True, sizing_mode="stretch_width")


def build(state: PlaygroundState | None = None) -> Any:
    """The assembled application, ready to serve."""
    pn.extension("codeeditor", "terminal", notifications=False)

    state = state or PlaygroundState()

    panes = [
        Pane(
            "Source schema",
            schema_column(state, "source_schema", "source_schema_error", lambda: _chain_of(state, "source_schema")),
        ),
        Pane(
            "Source instance",
            instance_column(
                state,
                "source_instance",
                "source_rdf",
                "source_graph",
                "source_instance_error",
                controls=rdf_controls(state),
            ),
        ),
        Pane(
            "Target schema",
            schema_column(state, "target_schema", "target_schema_error", lambda: _chain_of(state, "target_schema")),
        ),
        Pane(
            "Transformed instance",
            instance_column(
                state,
                "target_instance",
                "target_rdf",
                "target_graph",
                "bus_error",
                readonly=True,
            ),
        ),
    ]

    split = SplitColumns(panes)
    centre = paste_panel(state, split)

    main = pn.Column(
        pn.Row(
            pn.Column(split, sizing_mode="stretch_both"),
            centre,
            sizing_mode="stretch_both",
        ),
        log_panel(),
        sizing_mode="stretch_both",
    )

    try:
        from panelini import Panelini

        app = Panelini(title=TITLE, sidebar_enabled=True, sidebar_visible=False)
        app.main_set(objects=[main])
        return app
    except Exception as exc:  # pragma: no cover - panelini is optional at import time
        logger.warning("panelini unavailable (%s); falling back to a plain template", exc)
        return pn.template.FastListTemplate(title=TITLE, main=[main])


def serve(port: int = 5006, show: bool = True) -> None:
    """Run the playground on a local server."""
    pn.serve(build, port=port, show=show)
