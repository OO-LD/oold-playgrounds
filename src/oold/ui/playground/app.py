"""The playground, assembled.

Four columns sit in panelini's main frame, with the paste field between the two halves.
Expanding the paste field collapses the source columns, because it takes over their job: it
supplies the RDF the right-hand side reads. That is the only way the bus changes owner, which
is what keeps the data flow one-directional and free of the loop a bidirectional wiring would
create.
"""

from __future__ import annotations

import contextlib
import html
import logging
from typing import Any

import panel as pn

from oold.ui.playground import config as cfg
from oold.ui.playground.columns import (
    EDITOR_HEIGHT,
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


def paste_panel(state: PlaygroundState, split: SplitColumns) -> tuple[pn.Row, pn.Column]:
    """The alternative input: pasted RDF, in either notation, replacing the source columns.

    Returns the toolbar that turns it on and the editor itself. The toolbar belongs above the
    columns because it changes which of them are in play; the editor sits between the two
    halves, where the graph it supplies enters.
    """
    toggle = pn.widgets.Toggle(
        name="Paste RDF instead",
        value=state.use_paste,
        button_type="primary",
        width=200,
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
    body = pn.Column(
        pn.pane.HTML("<b>Paste RDF</b>", margin=(6, 4)),
        tabs,
        error_pane(state, "bus_error"),
        width=320,
        sizing_mode="stretch_height",
        margin=(0, 4),
        visible=state.use_paste,
    )

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

    toolbar = pn.Row(
        toggle,
        pn.pane.HTML(
            "<small style='color:var(--muted-text-color,#666)'>"
            "replaces the source columns as the input for the right-hand side</small>",
            margin=(10, 6),
        ),
        pn.HSpacer(),
        sizing_mode="stretch_width",
        margin=(0, 4),
    )
    return toolbar, body


def rdf_controls(state: PlaygroundState) -> pn.Row:
    """Serialization and mapping-set choice for the exported graph."""
    fmt = pn.widgets.Select(name="", options={"Turtle": TURTLE, "JSON-LD": JSON_LD}, value=state.rdf_format, width=110)
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


class _PaneLogHandler(logging.Handler):
    """Render log records into an HTML pane, newest last."""

    def __init__(self, pane: pn.pane.HTML, limit: int = 200) -> None:
        super().__init__()
        self._pane = pane
        self._limit = limit
        self._lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        colour = {"WARNING": "#b26a00", "ERROR": "#c62828", "CRITICAL": "#c62828"}.get(record.levelname, "inherit")
        self._lines.append(f"<div style='color:{colour}'>{html.escape(self.format(record))}</div>")
        del self._lines[: -self._limit]
        # Suppressed rather than reported: this *is* the reporting path, so logging a failure
        # to log would recurse.
        with contextlib.suppress(Exception):
            self._pane.object = (
                "<div style='font-family:monospace;font-size:12px;line-height:1.4'>" + "".join(self._lines) + "</div>"
            )


def log_panel() -> tuple[pn.Card, logging.Handler]:
    """Application log, collapsed by default, and the handler that feeds it.

    Fed by a logging handler rather than panelini's stdout mirror: a served app has one
    process and several sessions, so mirroring ``sys.stdout`` would show every user every
    other user's activity, and nothing at all when the interesting messages went through
    ``logging`` instead of ``print``.

    Rendered into an HTML pane rather than ``pn.widgets.Terminal``: building the terminal's
    model sets a property that schedules a debounced server callback, and that reaches for
    bokeh's tornado-based server code, which Pyodide does not ship. The browser build dies on
    it, so the terminal is simply not usable here.
    """
    view = pn.pane.HTML("", sizing_mode="stretch_width")
    handler = _PaneLogHandler(view)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))
    handler.setLevel(logging.INFO)
    terminal = pn.Column(view, height=180, scroll=True, sizing_mode="stretch_width")

    app_logger = logging.getLogger("oold")
    app_logger.setLevel(logging.INFO)
    app_logger.propagate = False

    # Attached once the document exists, for the same reason: a log line written while the
    # page is still being assembled would update a widget mid-construction.
    if pn.state.curdoc is not None:
        pn.state.onload(lambda: app_logger.addHandler(handler))
    else:
        app_logger.addHandler(handler)

    # The logger is global but the terminal belongs to one session. Without this the handlers
    # pile up and a later session's messages are written into a closed session's document,
    # which surfaces as a Bokeh "callback already added with this ID" error far from here.
    # Bokeh requires exactly one positional parameter here; a default argument is rejected.
    def detach(session_context: Any) -> None:
        app_logger.removeHandler(handler)

    if pn.state.curdoc is not None:
        pn.state.on_session_destroyed(detach)

    return pn.Card(terminal, title="Log", collapsed=True, sizing_mode="stretch_width"), handler


#: The state fields that make up a shareable session. Derived fields are left out: they are
#: recomputed from these, so putting them in the URL would only make the link longer.
SESSION_FIELDS = (
    "source_schema",
    "source_instance",
    "target_schema",
    "pasted_rdf",
    "use_paste",
    "paste_format",
    "rdf_format",
    "mapping_set",
)


def bind_url(state: PlaygroundState) -> None:
    """Seed the session from the URL and keep the URL in step with it.

    Written compressed, so a link stays short; a hand-written link may use readable
    per-field parameters or plain JSON and is read back just the same.
    """
    from oold.ui.url_config import UrlConfig

    manager = UrlConfig(cfg.PlaygroundConfig, param_name="pg")
    if manager.has_config():
        seeded = manager.get_config()
        for field in SESSION_FIELDS:
            setattr(state, field, getattr(seeded, field))
        logger.info("session restored from the URL")

    def persist(*_events: Any) -> None:
        try:
            manager.set_config(cfg.PlaygroundConfig(**{field: getattr(state, field) for field in SESSION_FIELDS}))
        except Exception as exc:  # pragma: no cover - the URL is a convenience, not the state
            logger.warning("could not write the session to the URL: %s", exc)

    # Only on an actual change. Writing during construction would set a reactive property
    # while the document is still being built, which schedules a debounced server callback -
    # and in a browser (Pyodide) build that pulls in bokeh's tornado-based server code, which
    # is not installed there. Rewriting the URL on load is also pointless: it says what it
    # already said.
    state.param.watch(persist, list(SESSION_FIELDS))


def build(state: PlaygroundState | None = None) -> Any:
    """The assembled application, ready to serve."""
    pn.extension("codeeditor", "terminal", notifications=False)

    log_card, _handler = log_panel()
    state = state or PlaygroundState()
    bind_url(state)

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
    toolbar, paste_body = paste_panel(state, split)

    main = pn.Column(
        toolbar,
        pn.Row(
            pn.Column(split, sizing_mode="stretch_both"),
            paste_body,
            sizing_mode="stretch_both",
        ),
        log_card,
        sizing_mode="stretch_both",
    )

    try:
        from panelini import Panelini

        app = Panelini(title=TITLE, sidebar_enabled=True, sidebar_visible=False)
        app.main_set(objects=[main])
    except Exception as exc:  # pragma: no cover - panelini is optional at import time
        logger.warning("panelini unavailable (%s); falling back to a plain template", exc)
        return pn.template.FastListTemplate(title=TITLE, main=[main])
    return app


def serve(port: int = 5006, show: bool = True) -> None:
    """Run the playground on a local server."""
    pn.serve(build, port=port, show=show)
