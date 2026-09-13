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
import sys
from typing import Any

import panel as pn
import param

from schema_playground import config as cfg
from schema_playground.columns import (
    CONTROLS_HEIGHT,
    EDITOR_HEIGHT,
    EDITOR_OPTIONS,
    error_pane,
    instance_column,
    schema_column,
)
from schema_playground.split import Pane, SplitColumns
from schema_playground.state import (
    CONSENSUS,
    PlaygroundState,
    document_label,
    meta_schema,
    meta_store,
    ref_resolver,
)
from schema_playground.mappings import chain, set_name
from schema_playground.transform import JSON_LD, TURTLE

logger = logging.getLogger(__name__)

TITLE = "OO-LD Schema Playground"
SOURCE_PANES = ("Source schemas", "Source instances")
ALL_PANES = ("Source schemas", "Source instances", "Target schemas", "Transformed instances")

#: The state fields that make up a shareable session, taken from the config model so the two
#: cannot drift apart: a field added there is persisted, applied and matched automatically.
#: Derived state fields are recomputed from these, so the URL never carries them.
SESSION_FIELDS: tuple[str, ...] = tuple(cfg.PlaygroundConfig.model_fields)


def _chain_of(state: PlaygroundState, field: str) -> list[dict[str, Any]]:
    raw = getattr(state, field)
    text, error = cfg.resolve_source(raw)
    if error:
        return []
    document, parse_error = cfg.parse_document(text)
    if parse_error or not isinstance(document, dict):
        return []
    # The same resolver the pipeline uses, so the Terms table shows the inherited terms of a
    # URL-loaded chain rather than a shorter reading than the transform's.
    return chain(document, ref_resolver(cfg.source_base(raw)))


def paste_panel(state: PlaygroundState, split: SplitColumns) -> tuple[pn.Row, pn.Column]:
    """The alternative input: pasted RDF, in either notation, replacing the source columns.

    Returns the toolbar that turns it on and the editor itself. The toolbar belongs above the
    columns because it changes which of them are in play; the editor sits between the two
    halves, where the graph it supplies enters.
    """
    toggle = pn.widgets.Toggle(name="Paste RDF instead", value=state.use_paste, width=200)
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

    # The state owns the mode; the widgets only reflect it. Examples and URL-restored
    # sessions set ``use_paste`` directly, and every visual (label, colour, visibility, the
    # folded source columns) must follow those paths exactly as it follows a click.
    def sync(*_events: Any) -> None:
        active = state.use_paste
        if toggle.value != active:
            toggle.value = active
        toggle.button_type = "primary" if active else "default"
        # Both labels name the action a click performs, not the current state: a toggle that
        # reads as a status gives no hint that it is the way back.
        toggle.name = "Back to schema input" if active else "Paste RDF instead"
        body.visible = active
        # The source columns no longer feed anything, so they fold away to give the pasted
        # graph and its readings the width. Expressed through collapsed_panes rather than by
        # driving the split directly: the collapse state has exactly one owner, so this
        # cannot clobber a collapse list an example or a URL is applying at the same moment.
        wanted = [
            title
            for title in ALL_PANES
            if (title in SOURCE_PANES and active)
            or (title not in SOURCE_PANES and title in (state.collapsed_panes or []))
        ]
        if wanted != list(state.collapsed_panes or []):
            state.collapsed_panes = wanted

    def on_toggle(event: Any) -> None:
        wanted = bool(event.new)
        if wanted != state.use_paste:
            state.use_paste = wanted
            if wanted:
                apply()

    toggle.param.watch(on_toggle, "value")
    state.param.watch(sync, "use_paste")
    sync()

    toolbar = pn.Row(
        toggle,
        pn.pane.HTML(
            "<small style='color:var(--muted-text-color,#666)'>"
            "provide Turtle or JSON-LD directly; it replaces the source columns as the input"
            "</small>",
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

    Labels are a schema's ``$id`` or an instance's ``@id``, shortened; a document that no
    longer parses mid-edit keeps its last good name rather than turning into "instance 0"
    under the user's cursor. ``template`` supplies the content of an added document (a
    callable receives the state), and without one the list is read-only (the transformed
    side, whose documents are emitted, not authored). The buttons carry no hover tooltips:
    a tooltip next to a small button covers its neighbour and steals the next click.
    """
    select = pn.widgets.Select(name="", options={f"{kind} 0": 0}, value=0, width=200)
    position = pn.pane.HTML("", margin=(10, 2), visible=False)
    add = pn.widgets.Button(name="+", width=32, button_type="light")
    remove = pn.widgets.Button(name="-", width=32, button_type="light")
    last_labels: dict[int, str] = {}

    def refresh(*_events: Any) -> None:
        documents = list(getattr(state, list_field) or [])
        options: dict[str, int] = {}
        for index, text in enumerate(documents):
            label = document_label(text, "")
            if label:
                last_labels[index] = label
            else:
                label = last_labels.get(index, f"{kind} {index}")
            while label in options:
                label += " "
            options[label] = index
        select.options = options or {f"{kind} 0": 0}
        current = getattr(state, index_field)
        values = list(select.options.values())
        select.value = current if current in values else (values[0] if values else 0)
        remove.disabled = len(documents) <= 1
        position.object = f"<small>{min(current, len(documents) - 1) + 1} of {len(documents)}</small>"
        position.visible = len(documents) > 1

    def on_select(event: Any) -> None:
        if event.new is not None and event.new != getattr(state, index_field):
            setattr(state, index_field, event.new)

    def on_add(_event: Any) -> None:
        documents = list(getattr(state, list_field) or [])
        content = template(state) if callable(template) else template
        documents.append(content)
        last_labels.clear()
        setattr(state, list_field, documents)
        setattr(state, index_field, len(documents) - 1)

    def on_remove(_event: Any) -> None:
        documents = list(getattr(state, list_field) or [])
        if len(documents) <= 1:
            return
        index = min(getattr(state, index_field), len(documents) - 1)
        del documents[index]
        last_labels.clear()
        setattr(state, index_field, max(0, index - 1))
        setattr(state, list_field, documents)

    refresh()
    state.param.watch(refresh, [list_field, index_field])
    select.param.watch(on_select, "value")
    add.on_click(on_add)
    remove.on_click(on_remove)

    widgets: list[Any] = [select, position]
    if template is not None:
        widgets += [add, remove]
    return pn.Row(*widgets, sizing_mode="stretch_width", margin=(0, 2))


def rdf_controls(state: PlaygroundState) -> pn.Row:
    """Serialization and mapping-set choice for the exported graph."""
    fmt = pn.widgets.Select(name="", options={"Turtle": TURTLE, "JSON-LD": JSON_LD}, value=state.rdf_format, width=100)
    fmt.param.watch(lambda event: setattr(state, "rdf_format", event.new), "value")

    mapping = pn.widgets.Select(name="", options={CONSENSUS: ""}, value="", width=180)

    def refresh_options(*_events: Any) -> None:
        options = {CONSENSUS: ""}
        for set_id in state.available_sets:
            options[set_name(set_id)] = set_id
        mapping.options = options
        if state.mapping_set not in options.values():
            mapping.value = ""

    refresh_options()
    state.param.watch(refresh_options, "available_sets")
    mapping.param.watch(lambda event: setattr(state, "mapping_set", event.new or ""), "value")

    return pn.Row(
        pn.pane.HTML("<small>RDF format</small>", margin=(8, 2)),
        fmt,
        pn.pane.HTML("<small>Vocabulary mapping</small>", margin=(8, 2)),
        mapping,
        sizing_mode="stretch_width",
        margin=0,
        height=CONTROLS_HEIGHT,
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


def apply_config(state: PlaygroundState, config: cfg.PlaygroundConfig) -> None:
    """Apply a complete session in one step.

    Picking an example and loading a shared URL go through here, so the two are the same
    operation by construction. One batch: applying field by field would run the pipeline once
    per field and pass through half-applied sessions on the way.
    """
    with param.parameterized.batch_call_watchers(state):
        for field in SESSION_FIELDS:
            value = getattr(config, field)
            setattr(state, field, list(value) if isinstance(value, list) else value)
        state.target_instance_idx = 0


def apply_example(state: PlaygroundState, name: str) -> None:
    """Load a reference example: the same fields a shared URL of that config would seed."""
    apply_config(state, cfg.EXAMPLES[name])
    logger.info("loaded the %s example", name)


def active_example(state: PlaygroundState) -> str | None:
    """The example the current session equals, if it equals any.

    Compared as full configs: after any edit the session is nobody's example any more, and
    highlighting one anyway would claim content the user is no longer looking at.
    """
    try:
        snapshot = cfg.PlaygroundConfig(**{field: getattr(state, field) for field in SESSION_FIELDS})
    except Exception:
        return None
    for name, example in cfg.EXAMPLES.items():
        if example == snapshot:
            return name
    return None


def example_switcher(state: PlaygroundState, busy: dict[str, Any] | None = None) -> pn.Row:
    """A dropdown of the reference examples, showing the one the session equals.

    The blank entry is the resting position for a session that matches no example (anything
    edited or URL-restored); selecting it does nothing, it only exists to be shown.

    ``busy["target"]`` (filled by the caller once the layout exists) gets a loading overlay
    while an example applies. The pipeline runs for seconds, and inside the selection handler
    nothing would repaint until it finishes - so the work is deferred one tick, after the
    overlay has reached the browser.
    """

    select = pn.widgets.Select(
        name="Select example",
        options=["", *cfg.EXAMPLES],
        value=active_example(state) or "",
        width=180,
    )

    def on_select(event: Any) -> None:
        if not (event.new and event.new != active_example(state)):
            return
        name = event.new
        target = (busy or {}).get("target")
        doc = pn.state.curdoc
        if target is None or doc is None:
            apply_example(state, name)
            return
        target.loading = True

        def work() -> None:
            try:
                apply_example(state, name)
            finally:
                target.loading = False

        if sys.platform == "emscripten":
            # Under Pyodide a bokeh timeout callback applies the state changes but its
            # loading flip never reaches the DOM; a change made from the asyncio loop does.
            import asyncio

            asyncio.get_event_loop().call_later(0.1, work)
        else:
            doc.add_timeout_callback(work, 50)

    def refresh(*_events: Any) -> None:
        wanted = active_example(state) or ""
        if select.value != wanted:
            select.value = wanted

    select.param.watch(on_select, "value")
    state.param.watch(refresh, list(SESSION_FIELDS))
    return pn.Row(select, margin=(0, 12, 0, 8))


def share_button() -> pn.widgets.Button:
    """Copy the page URL, which always carries the whole session.

    Client-side only: the clipboard is the browser's, and the URL to share is whatever the
    address bar says right now - the persist watcher keeps it current.
    """
    button = pn.widgets.Button(name="Copy shareable URL", width=160)
    button.js_on_click(
        args={"btn": button},
        code=(
            "navigator.clipboard.writeText(window.location.href);"
            "const before = btn.label; btn.label = 'Copied';"
            "setTimeout(() => { btn.label = before; }, 1500);"
        ),
    )
    return button


def sync_collapse(state: PlaygroundState, split: SplitColumns) -> None:
    """Keep the split layout and the session's collapse list mirrored, both ways.

    While the state is being applied to the layout, the layout's own change callback is
    ignored: each pane flips individually, and echoing a half-applied layout back into the
    state would overwrite the list currently being applied.
    """
    applying = {"on": False}

    def from_state(*_events: Any) -> None:
        if applying["on"]:
            return
        applying["on"] = True
        try:
            wanted = set(state.collapsed_panes or [])
            for title in ALL_PANES:
                split.collapse(title, collapsed=title in wanted)
        finally:
            applying["on"] = False
        from_split()

    def from_split() -> None:
        if applying["on"]:
            return
        current = split.collapsed_titles()
        if current != list(state.collapsed_panes or []):
            state.collapsed_panes = current

    state.param.watch(from_state, "collapsed_panes")
    split.on_change = from_split
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


def bind_url(state: PlaygroundState) -> None:
    """Seed the session from the URL and keep the URL in step with it.

    Written compressed, so a link stays short; a hand-written link may use readable
    per-field parameters or plain JSON and is read back just the same.
    """
    from schema_playground.url_config import UrlConfig

    manager = UrlConfig(cfg.PlaygroundConfig, param_name="pg")
    if manager.has_config():
        apply_config(state, manager.get_config())
        logger.info("session restored from the URL")

    def persist(*_events: Any) -> None:
        try:
            manager.set_config(cfg.PlaygroundConfig(**{field: getattr(state, field) for field in SESSION_FIELDS}))
        except Exception as exc:  # pragma: no cover - the URL is a convenience, not the state
            logger.warning("could not write the session to the URL: %s", exc)

    # Never during construction: writing then would set a reactive property while the
    # document is still being built, which schedules a debounced server callback - and in a
    # browser (Pyodide) build that pulls in bokeh's tornado-based server code, which is not
    # installed there. Once per session after load, though, so the address bar is a complete
    # shareable link even before the first edit - the copy button copies whatever it says.
    state.param.watch(persist, list(SESSION_FIELDS))
    if pn.state.curdoc is not None:
        pn.state.onload(persist)


def about_panel() -> pn.viewable.Viewable:
    """What the app is and how to read it, behind the sidebar toggle."""
    return pn.pane.Markdown(
        """## About

This playground demonstrates **OO-LD** (Object-Oriented Linked Data): JSON documents whose schemas are a JSON Schema and a JSON-LD context at once.

**How to read the page:** the source documents on the left are validated against their schemas and exported into one RDF graph. Each target schema on the right reads that graph back and emits the transformed documents. Edit anything on the left and the right side follows.

The URL always carries your whole session - share the link to share the state.

- [OO-LD specification](https://oo-ld.github.io/oold-schema/latest/spec/)
- [OO-LD documentation](https://oo-ld.github.io/oold-schema/)
- [Playground source](https://github.com/OO-LD/oold-playgrounds)
""",
        sizing_mode="stretch_width",
        margin=(0, 10),
    )


def build(state: PlaygroundState | None = None) -> Any:
    """The assembled application, ready to serve."""
    pn.extension(notifications=False)

    log_card, _handler = log_panel()
    deferred = state is None and pn.state.curdoc is not None
    if state is None:
        # The first recompute runs after the page is delivered: the pipeline costs seconds,
        # and paying them before the document exists means seconds of blank page.
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
                sizing_mode="stretch_width",
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
                sizing_mode="stretch_width",
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
                sizing_mode="stretch_width",
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
                sizing_mode="stretch_width",
            ),
        ),
    ]

    split = SplitColumns(panes)
    sync_collapse(state, split)
    paste_toolbar, paste_body = paste_panel(state, split)

    subtitle = pn.pane.HTML(
        "<div style='font-size:13px;color:var(--muted-text-color,#555)'>"
        "Source documents (left) are validated against their schemas and exported into "
        "<b>one RDF graph</b>; every target schema reads that graph back into the transformed "
        "documents (right). Edit anything on the left - the right side follows."
        "</div>",
        sizing_mode="stretch_width",
        margin=(0, 8, 6, 8),
    )

    # the paste editor sits on the left, where the source columns it replaces collapse to
    workspace = pn.Row(
        paste_body,
        pn.Column(split, sizing_mode="stretch_width"),
        sizing_mode="stretch_width",
    )
    busy: dict[str, Any] = {"target": workspace}
    main = pn.Column(
        pn.Row(
            example_switcher(state, busy),
            # aligned with the dropdown, which carries a label above itself
            pn.Column(share_button(), margin=(19, 6, 0, 0)),
            sizing_mode="stretch_width",
        ),
        subtitle,
        # directly under the sentence it belongs to: the toggle switches away from exactly
        # the source-to-graph flow the subtitle describes
        paste_toolbar,
        workspace,
        log_card,
        sizing_mode="stretch_width",
    )

    try:
        from panelini import Panelini

        app = Panelini(title=TITLE, sidebar_enabled=True, sidebar_visible=False)
        app.main_set(objects=[main])
        app.sidebar_set(objects=[about_panel()])
    except Exception as exc:  # pragma: no cover - panelini is optional at import time
        logger.warning("panelini unavailable (%s); falling back to a plain template", exc)
        return pn.template.FastListTemplate(title=TITLE, main=[main])
    return app


def serve(port: int = 5006, show: bool = True) -> None:
    """Run the playground on a local server."""
    pn.serve(build, port=port, show=show)
