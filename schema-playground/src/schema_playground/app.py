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
import param

from schema_playground import config as cfg
from schema_playground.columns import (
    EDITOR_HEIGHT,
    EDITOR_OPTIONS,
    error_pane,
    instance_column,
    schema_column,
)
from schema_playground.split import Pane, SplitColumns
from schema_playground.state import PlaygroundState, document_label, meta_schema, meta_store
from schema_playground.mappings import chain, set_name
from schema_playground.transform import JSON_LD, TURTLE

logger = logging.getLogger(__name__)

TITLE = "OO-LD Playground"
SOURCE_PANES = ("Source schemas", "Source instances")


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
        button_type="primary" if state.use_paste else "default",
        width=200,
    )
    from panelini.panels.monacoeditor import MonacoEditor

    turtle_editor = MonacoEditor(
        value=state.pasted_rdf,
        language="turtle",
        schema_request="ignore",
        options=EDITOR_OPTIONS,
        sizing_mode="stretch_width",
        height=EDITOR_HEIGHT - 60,
    )
    jsonld_editor = MonacoEditor(
        value="",
        language="json",
        schema_request="ignore",
        options=EDITOR_OPTIONS,
        sizing_mode="stretch_width",
        height=EDITOR_HEIGHT - 60,
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
        toggle.button_type = "primary" if state.use_paste else "default"
        toggle.name = "Pasted RDF is the input" if state.use_paste else "Paste RDF instead"
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


NEW_SCHEMA_TEMPLATE = """{
  "$schema": "https://oo-ld.org/latest/meta/oold-meta-schema.json",
  "$id": "New.schema.json",
  "title": "New",
  "type": "object",
  "@context": {
    "schema": "https://schema.org/",
    "id": "@id",
    "name": "schema:name"
  },
  "properties": {
    "id": {"type": "string", "format": "iri"},
    "name": {"type": "string"}
  }
}"""


def _new_instance_template(state: PlaygroundState) -> str:
    """A stub pointing at the currently selected schema, so it processes immediately."""
    import json as _json

    schema_text, _ = cfg.resolve_source(state.source_schema)
    document, _ = cfg.parse_document(schema_text)
    identifier = document.get("$id") if isinstance(document, dict) else None
    identifier = identifier if isinstance(identifier, str) else "New.schema.json"
    return _json.dumps(
        {"@context": identifier, "$schema": identifier, "id": "https://example.org/things/new"},
        indent=2,
    )


def document_selector(
    state: PlaygroundState,
    list_field: str,
    index_field: str,
    kind: str,
    template: Any = None,
) -> pn.Row:
    """The document chooser above a column: label per document, plus add and remove.

    Labels are a schema's ``$id`` or an instance's ``@id``, shortened; ``template`` supplies
    the content of an added document (a callable receives the state), and without one the
    list is read-only (the transformed side, whose documents are emitted, not authored).
    """
    select = pn.widgets.Select(name="", options={f"{kind} 0": 0}, value=0, width=220)
    add = pn.widgets.Button(name="+", width=32, button_type="light", description=f"Add a {kind}")
    remove = pn.widgets.Button(name="-", width=32, button_type="light", description=f"Remove this {kind}")

    def refresh(*_events: Any) -> None:
        documents = list(getattr(state, list_field) or [])
        options: dict[str, int] = {}
        for index, text in enumerate(documents):
            label = document_label(text, f"{kind} {index}")
            while label in options:
                label += " "
            options[label] = index
        select.options = options or {f"{kind} 0": 0}
        current = getattr(state, index_field)
        values = list(select.options.values())
        select.value = current if current in values else (values[0] if values else 0)
        remove.disabled = len(documents) <= 1

    def on_select(event: Any) -> None:
        if event.new is not None and event.new != getattr(state, index_field):
            setattr(state, index_field, event.new)

    def on_add(_event: Any) -> None:
        documents = list(getattr(state, list_field) or [])
        content = template(state) if callable(template) else template
        documents.append(content)
        setattr(state, list_field, documents)
        setattr(state, index_field, len(documents) - 1)

    def on_remove(_event: Any) -> None:
        documents = list(getattr(state, list_field) or [])
        if len(documents) <= 1:
            return
        index = min(getattr(state, index_field), len(documents) - 1)
        del documents[index]
        setattr(state, index_field, max(0, index - 1))
        setattr(state, list_field, documents)

    refresh()
    state.param.watch(refresh, [list_field, index_field])
    select.param.watch(on_select, "value")
    add.on_click(on_add)
    remove.on_click(on_remove)

    widgets: list[Any] = [select]
    if template is not None:
        widgets += [add, remove]
    return pn.Row(*widgets, sizing_mode="stretch_width", margin=(0, 2))


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


ALL_PANES = ("Source schemas", "Source instances", "Target schemas", "Transformed instances")


def apply_example(state: PlaygroundState, name: str) -> None:
    """Load a reference example: the same fields a shared URL of that config would seed."""
    example = cfg.EXAMPLES[name]
    with param.parameterized.batch_call_watchers(state):
        state.source_schemas = list(example.source_schemas)
        state.source_instances = list(example.source_instances)
        state.target_schemas = list(example.target_schemas)
        state.source_schema_idx = 0
        state.source_instance_idx = 0
        state.target_schema_idx = 0
        state.target_instance_idx = 0
        state.mapping_set = ""
        state.use_paste = False
        state.pasted_rdf = ""
        state.collapsed_panes = list(example.collapsed_panes)
    logger.info("loaded the %s example", name)


def example_switcher(state: PlaygroundState) -> pn.Row:
    buttons = pn.widgets.RadioButtonGroup(
        options=list(cfg.EXAMPLES),
        # the default session IS the Transform example; starting anywhere else would either
        # mislabel the content or (with the first option preselected) make its button a no-op
        value="Transform",
        button_type="light",
        button_style="outline",
    )
    buttons.param.watch(lambda event: event.new and apply_example(state, event.new), "value")
    return pn.Row(
        pn.pane.HTML("<small>Examples</small>", margin=(10, 2)),
        buttons,
        margin=(0, 12, 0, 2),
    )


def sync_collapse(state: PlaygroundState, split: SplitColumns) -> None:
    """Keep the split layout and the session's collapse list mirrored, both ways."""

    def from_state(*_events: Any) -> None:
        wanted = set(state.collapsed_panes or [])
        for title in ALL_PANES:
            split.collapse(title, collapsed=title in wanted)

    def from_split() -> None:
        current = split.collapsed_titles()
        if current != list(state.collapsed_panes or []):
            state.collapsed_panes = current

    state.param.watch(from_state, "collapsed_panes")
    split._on_change = from_split
    from_state()


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

    # Both trees: the app logs under its own package name, the library under "oold".
    app_loggers = [logging.getLogger("schema_playground"), logging.getLogger("oold")]
    for app_logger in app_loggers:
        app_logger.setLevel(logging.INFO)
        app_logger.propagate = False

    def attach() -> None:
        for app_logger in app_loggers:
            app_logger.addHandler(handler)

    # Attached once the document exists, for the same reason: a log line written while the
    # page is still being assembled would update a widget mid-construction.
    if pn.state.curdoc is not None:
        pn.state.onload(attach)
    else:
        attach()

    # The logger is global but the terminal belongs to one session. Without this the handlers
    # pile up and a later session's messages are written into a closed session's document,
    # which surfaces as a Bokeh "callback already added with this ID" error far from here.
    # Bokeh requires exactly one positional parameter here; a default argument is rejected.
    def detach(session_context: Any) -> None:
        for app_logger in app_loggers:
            app_logger.removeHandler(handler)

    if pn.state.curdoc is not None:
        pn.state.on_session_destroyed(detach)

    return pn.Card(terminal, title="Log", collapsed=True, sizing_mode="stretch_width"), handler


#: The state fields that make up a shareable session. Derived fields are left out: they are
#: recomputed from these, so putting them in the URL would only make the link longer.
SESSION_FIELDS = (
    "source_schemas",
    "source_instances",
    "target_schemas",
    "source_schema_idx",
    "source_instance_idx",
    "target_schema_idx",
    "pasted_rdf",
    "use_paste",
    "paste_format",
    "rdf_format",
    "mapping_set",
    "collapsed_panes",
)


def bind_url(state: PlaygroundState) -> None:
    """Seed the session from the URL and keep the URL in step with it.

    Written compressed, so a link stays short; a hand-written link may use readable
    per-field parameters or plain JSON and is read back just the same.
    """
    from schema_playground.url_config import UrlConfig

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


def _spin_until_ready(main: pn.Column, hidden: pn.viewable.Viewable, in_session: bool | None = None) -> None:
    """Overlay a loading spinner on the main area until the editors exist in the browser.

    The page's own load event fires long before Monaco does (the editor bundle is large), so
    readiness is counted from each visible editor's ``ready`` flag. Editors inside ``hidden``
    (the collapsed paste panel) do not render until shown and are not waited for. A fallback
    timeout clears the spinner regardless: a stuck overlay is worse than a page still
    settling.
    """
    from panelini.panels.monacoeditor import MonacoEditor

    if in_session is None:
        in_session = pn.state.curdoc is not None
    hidden_editors = {id(editor) for editor in hidden.select(MonacoEditor)}
    editors = [
        editor
        for editor in main.select(MonacoEditor)
        if id(editor) not in hidden_editors and not editor.ready
    ]
    if not editors:
        return
    main.loading = True
    pending = {id(editor) for editor in editors}
    doc = pn.state.curdoc
    # Both must hold before the overlay may clear. The editors often report ready while the
    # session is still initialising, and a property change made in that window never reaches
    # the browser (verified: the server ends at css_classes=["main-object"] while the client
    # keeps the loading classes forever) - whereas any change after the session's load event
    # propagates reliably. So the flip waits for whichever comes last.
    gates = {"loaded": not in_session, "finished": False}

    def flip() -> None:
        main.loading = False

    def try_clear() -> None:
        if not (gates["loaded"] and gates["finished"]):
            return
        if doc is not None:
            try:
                doc.add_next_tick_callback(flip)
                return
            except Exception:  # pragma: no cover - no running session
                pass
        flip()

    def done(*_events: Any) -> None:
        gates["finished"] = True
        try_clear()

    def session_loaded() -> None:
        gates["loaded"] = True
        try_clear()

    def on_ready(event: Any) -> None:
        pending.discard(id(event.obj))
        if not pending:
            done()

    for editor in editors:
        editor.param.watch(on_ready, "ready")

    if in_session:
        # Registered only with a real document: without one panel runs the callback
        # immediately, which would open the gate before anything is served (and makes the
        # gate untestable).
        if doc is not None:
            pn.state.onload(session_loaded)
        # The safety net: readiness normally clears the overlay, but when the editor module
        # cannot load at all (a blocked or unreachable bundle CDN), no editor ever reports
        # and only this timer stands between the user and a spinner that never leaves. A
        # plain thread timer rather than a document timeout: those have failed silently
        # before, and next_tick from a thread is the documented safe entry point.
        import threading

        def give_up() -> None:
            # A session whose editors never load may also never fire its load event (bokeh
            # only reports idle once the modules settle), so the timer opens both gates: by
            # now the initialisation race the loaded-gate protects against is long over.
            gates["loaded"] = True
            done()

        try:
            timer = threading.Timer(30.0, give_up)
            timer.daemon = True
            timer.start()
        except Exception:  # pragma: no cover - no threads (browser build)
            pass
    else:
        done()
    return session_loaded


def build(state: PlaygroundState | None = None) -> Any:
    """The assembled application, ready to serve."""
    pn.extension("codeeditor", "terminal", notifications=False)

    log_card, _handler = log_panel()
    deferred = state is None and pn.state.curdoc is not None
    if state is None:
        # The first recompute runs after the page is delivered (see _spin_until_ready), so
        # the user sees the app shell and a spinner instead of seconds of blank page.
        state = PlaygroundState(compute=not deferred)
    bind_url(state)
    if deferred:
        pn.state.onload(state.recompute)
    meta = meta_schema()
    store = meta_store()

    panes = [
        Pane(
            "Source schemas",
            pn.Column(
                document_selector(state, "source_schemas", "source_schema_idx", "schema", NEW_SCHEMA_TEMPLATE),
                schema_column(
                    state,
                    "source_schema",
                    "source_schema_error",
                    lambda: _chain_of(state, "source_schema"),
                    meta=meta,
                    store=store,
                ),
                sizing_mode="stretch_both",
            ),
        ),
        Pane(
            "Source instances",
            pn.Column(
                document_selector(
                    state, "source_instances", "source_instance_idx", "instance", _new_instance_template
                ),
                instance_column(
                    state,
                    "source_instance",
                    "source_rdf",
                    "source_graph",
                    "source_instance_error",
                    controls=rdf_controls(state),
                    schema_field="source_editor_schema",
                ),
                sizing_mode="stretch_both",
            ),
        ),
        Pane(
            "Target schemas",
            pn.Column(
                document_selector(state, "target_schemas", "target_schema_idx", "schema", NEW_SCHEMA_TEMPLATE),
                schema_column(
                    state,
                    "target_schema",
                    "target_schema_error",
                    lambda: _chain_of(state, "target_schema"),
                    meta=meta,
                    store=store,
                ),
                sizing_mode="stretch_both",
            ),
        ),
        Pane(
            "Transformed instances",
            pn.Column(
                document_selector(state, "target_instances", "target_instance_idx", "document"),
                instance_column(
                    state,
                    "target_instance",
                    "target_rdf",
                    "target_graph",
                    "bus_error",
                    readonly=True,
                    schema_field="target_editor_schema",
                ),
                sizing_mode="stretch_both",
            ),
        ),
    ]

    split = SplitColumns(panes)
    sync_collapse(state, split)
    toolbar, paste_body = paste_panel(state, split)
    toolbar.insert(0, example_switcher(state))

    main = pn.Column(
        toolbar,
        # the paste editor sits on the left, where the source columns it replaces collapse to
        pn.Row(
            paste_body,
            pn.Column(split, sizing_mode="stretch_both"),
            sizing_mode="stretch_both",
        ),
        log_card,
        sizing_mode="stretch_both",
    )
    _spin_until_ready(main, paste_body)

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
