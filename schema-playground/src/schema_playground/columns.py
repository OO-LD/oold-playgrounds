"""The four columns, and how an editor is tied to the state.

Two encodings of one document are editable side by side (JSON and YAML). The rule that keeps
them from fighting each other is in :class:`DocumentEditor`: an editor is rewritten only when
the *document* it shows has changed, never merely because the text differs. Re-encoding on
every keystroke would reformat what the user is in the middle of typing, and comparing text
rather than meaning is what makes that happen.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import panel as pn
import param

from panelini.panels.monacoeditor import MonacoEditor

from schema_playground import config as cfg
from schema_playground.terms import terms_table
from schema_playground.transform import JSON_LD

EDITOR_HEIGHT = 420
#: Height of the format/mapping control row inside an RDF tab.
CONTROLS_HEIGHT = 44

#: Merged last into every editor. The font: monaco's default runs large for four columns.
#: The quick suggestions: monaco disables them inside strings by default, and in a JSON
#: document everything is a string, so schema completion would otherwise only ever appear on
#: an explicit trigger.
EDITOR_OPTIONS = {
    "fontSize": 12,
    "quickSuggestions": {"other": True, "comments": False, "strings": True},
    # Editors cover most of the page; monaco consuming every wheel event would make the page
    # unscrollable wherever the pointer rests on one.
    "scrollbar": {"alwaysConsumeMouseWheel": False},
}


def _to_yaml(document: Any) -> str:
    import yaml

    return yaml.safe_dump(document, sort_keys=False, allow_unicode=True)


def _encode(document: Any, language: str) -> str:
    if document is None:
        return ""
    return _to_yaml(document) if language == "yaml" else json.dumps(document, indent=2)


class DocumentEditor(pn.viewable.Viewer):
    """A code editor bound to one text field of the state, in one encoding."""

    def __init__(
        self,
        state: param.Parameterized,
        field: str,
        language: str = "json",
        readonly: bool = False,
        height: int = EDITOR_HEIGHT,
        schema_field: str | None = None,
        json_schema: dict | None = None,
        extra_store: dict | None = None,
        lock_on_paste: bool = False,
    ) -> None:
        super().__init__()
        self._state = state
        self._field = field
        self._language = language
        self._writing = False

        if schema_field is not None and json_schema is None:
            json_schema = getattr(state, schema_field)
        self._json_schema = json_schema
        self._extra_store = extra_store or {}
        # schema_request="ignore": a $schema pointer that resolves neither from the store nor
        # over the network should not put a marker on every buffer. enable_schema_request
        # lets truly external pointers and refs fetch live (CORS permitting).
        self._editor = MonacoEditor(
            value=self._current_text(),
            language=language,
            read_only=readonly,
            json_schema=self._shaped_schema(),
            schema_store=self._schema_store(),
            schema_request="ignore",
            enable_schema_request=True,
            options=EDITOR_OPTIONS,
            sizing_mode="stretch_width",
            height=height,
        )
        if not readonly:
            self._editor.param.watch(self._on_edit, "value")

        # In paste mode the pasted graph is the input, so these documents no longer feed
        # anything. They stay on screen - reading them is still the point of comparing the
        # two sides - but editing them would suggest an effect they do not have.
        if lock_on_paste and hasattr(state, "use_paste"):

            def _follow_paste(*_events: Any) -> None:
                self._editor.read_only = readonly or bool(state.use_paste)

            _follow_paste()
            state.param.watch(_follow_paste, "use_paste")

        state.param.watch(self._on_state, field)
        if schema_field is not None:
            state.param.watch(self._on_schema, schema_field)

    def _declared_pointer(self) -> str | None:
        """The `$schema` the current buffer names, if any."""
        document = self._document()
        pointer = document.get("$schema") if isinstance(document, dict) else None
        return pointer if isinstance(pointer, str) and pointer.strip() else None

    def _schema_store(self) -> dict | None:
        """The schemas this buffer's references resolve against.

        The buffer's own `$schema` pointer bypasses the fileMatch association entirely (it
        wins in Monaco's JSON service), so the validation schema is registered under that URI
        too. `extra_store` carries fixed entries such as the 2020-12 and OO-LD meta-schemas.
        """
        store = dict(self._extra_store)
        pointer = self._declared_pointer()
        if pointer and self._json_schema and pointer not in store:
            store[pointer] = self._json_schema
        return store or None

    def _shaped_schema(self) -> dict | None:
        """The validation schema, wrapped when the buffer is a ``@graph`` of entities.

        A graph document is a container of nodes; validating the container against a node
        schema would flag every key. Each node validates against the chain instead.
        """
        if not self._json_schema:
            return None
        document = self._document()
        if isinstance(document, dict) and isinstance(document.get("@graph"), list):
            return {
                "type": "object",
                "properties": {"@graph": {"type": "array", "items": self._json_schema}},
            }
        return self._json_schema

    def _sync_schema(self) -> None:
        self._editor.json_schema = self._shaped_schema()
        self._editor.schema_store = self._schema_store()

    def _on_schema(self, event: Any) -> None:
        self._json_schema = event.new or None
        self._sync_schema()

    def _document(self) -> Any:
        text, _ = cfg.resolve_source(getattr(self._state, self._field))
        document, _ = cfg.parse_document(text)
        return document

    def _current_text(self) -> str:
        raw = getattr(self._state, self._field)
        # A field holding a URL is shown as the URL: that is what the user typed, and
        # replacing it with the fetched body would lose the reference.
        if cfg.looks_like_url(raw):
            return raw
        if self._language == "json":
            return raw
        document, error = cfg.parse_document(raw)
        return _encode(document, "yaml") if not error else raw

    def _on_edit(self, event: Any) -> None:
        if self._writing:
            return
        text = event.new
        if self._language == "yaml" and not cfg.looks_like_url(text):
            document, error = cfg.parse_document(text)
            if error is None and document is not None:
                text = json.dumps(document, indent=2)
        setattr(self._state, self._field, text)
        if self._json_schema:
            self._sync_schema()

    def _on_state(self, event: Any) -> None:
        wanted = self._current_text()
        if wanted == self._editor.value:
            return
        # Only rewrite when the meaning changed; otherwise a round-trip through the other
        # encoding would reformat text the user is still editing.
        current, _ = cfg.parse_document(self._editor.value)
        incoming, _ = cfg.parse_document(wanted)
        if current == incoming and current is not None:
            return
        self._writing = True
        try:
            self._editor.value = wanted
        finally:
            self._writing = False

    def __panel__(self) -> MonacoEditor:
        return self._editor


#: Validator wording translated for readers who do not know the pipeline's vocabulary.
_FRIENDLY = {
    "produced no triples": "produced no triples (none of its properties are mapped to RDF)",
}


def error_pane(state: param.Parameterized, field: str) -> pn.viewable.Viewable:
    """The validation report for one editor, directly beneath it.

    A message starting with ``hint:`` is not a failure but an explanation of an empty result,
    so it renders amber rather than red.
    """

    def render(message: str) -> pn.viewable.Viewable:
        if not message:
            return pn.pane.HTML(
                "<div style='color:#2e7d32;font-size:12px'>valid</div>",
                sizing_mode="stretch_width",
                margin=(2, 6),
            )
        colour = "#c62828"
        if message.startswith("hint:"):
            colour = "#b26a00"
            message = message[len("hint:") :].strip()
        for phrase, friendly in _FRIENDLY.items():
            message = message.replace(phrase, friendly)
        body = "<br>".join(line for line in message.splitlines() if line.strip())
        return pn.pane.HTML(
            f"<div style='color:{colour};font-size:12px;white-space:normal'>" + body + "</div>",
            sizing_mode="stretch_width",
            margin=(2, 6),
        )

    return pn.bind(render, state.param[field])


def _tabs(*panels: tuple[str, Any]) -> pn.Tabs:
    # stretch_width, not stretch_both: a column stretched to the viewport would push the
    # validation report to the bottom of the page, far from the editor it belongs to.
    return pn.Tabs(*panels, sizing_mode="stretch_width", dynamic=False)


def schema_column(
    state: param.Parameterized,
    field: str,
    error_field: str,
    chain_of: Callable[[], list],
    meta: dict | None = None,
    store: dict | None = None,
    lock_on_paste: bool = False,
) -> pn.Column:
    """A schema column: the document in two encodings, plus its reading as terms.

    ``meta`` is the OO-LD meta-schema; the JSON editor validates the schema document against
    it inline, so a misplaced keyword is flagged while typing rather than by the pipeline.
    """
    terms = pn.bind(
        lambda _value: pn.pane.HTML(terms_table(chain_of()), sizing_mode="stretch_width"), state.param[field]
    )
    return pn.Column(
        _tabs(
            ("JSON", DocumentEditor(state, field, "json", json_schema=meta, extra_store=store, lock_on_paste=lock_on_paste)),
            ("YAML", DocumentEditor(state, field, "yaml", json_schema=meta, extra_store=store, lock_on_paste=lock_on_paste)),
            ("Terms", pn.Column(terms, scroll=True, height=EDITOR_HEIGHT)),
        ),
        error_pane(state, error_field),
        sizing_mode="stretch_width",
    )


def graph_pane(state: param.Parameterized, field: str) -> pn.viewable.Viewable:
    """The RDF graph, drawn with panelini's vis-network component."""
    from panelini.panels.visnetwork import VisNetwork

    network = VisNetwork(
        nodes=[],
        edges=[],
        options={
            "physics": {"stabilization": True},
            "interaction": {"hover": True},
            "edges": {"font": {"size": 10, "align": "horizontal"}},
        },
        height=EDITOR_HEIGHT,
        sizing_mode="stretch_width",
        # An opaque canvas: the page background is decorated, and nodes floating over the
        # artwork read as a rendering glitch.
        styles={"background": "#ffffff", "border": "1px solid #e0e0e0", "border-radius": "4px"},
    )

    def update(data: dict) -> None:
        network.set_nodes(list(data.get("nodes") or []))
        network.set_edges(list(data.get("edges") or []))

    update(getattr(state, field))
    state.param.watch(lambda event: update(event.new), field)
    return network


def instance_column(
    state: param.Parameterized,
    instance_field: str,
    rdf_field: str,
    graph_field: str,
    error_field: str,
    readonly: bool = False,
    controls: pn.viewable.Viewable | None = None,
    schema_field: str | None = None,
    lock_on_paste: bool = False,
) -> pn.Column:
    """An instance column: the document, the RDF it exports as, and that graph drawn.

    ``schema_field`` names the state field carrying the chain's merged schema; the JSON
    editor validates the instance against it inline, live with every schema edit.
    """
    # With controls the RDF tab stacks two elements; the editor gives up their height so
    # every tab panel is equally tall - the tabs render the tallest panel's height, and a
    # taller RDF tab would push this column's status line below its neighbours'.
    rdf_view = MonacoEditor(
        value=getattr(state, rdf_field),
        language="turtle",
        read_only=True,
        schema_request="ignore",
        options=EDITOR_OPTIONS,
        sizing_mode="stretch_width",
        height=EDITOR_HEIGHT if controls is None else EDITOR_HEIGHT - CONTROLS_HEIGHT,
    )
    state.param.watch(lambda event: setattr(rdf_view, "value", event.new), rdf_field)
    state.param.watch(
        lambda event: setattr(rdf_view, "language", "json" if event.new == JSON_LD else "turtle"),
        "rdf_format",
    )

    rdf_tab: Any = rdf_view if controls is None else pn.Column(controls, rdf_view, sizing_mode="stretch_width")

    panels = [
        ("JSON", DocumentEditor(state, instance_field, "json", readonly=readonly, schema_field=schema_field, lock_on_paste=lock_on_paste)),
        ("YAML", DocumentEditor(state, instance_field, "yaml", readonly=readonly, schema_field=schema_field, lock_on_paste=lock_on_paste)),
        ("RDF", rdf_tab),
        ("Graph", graph_pane(state, graph_field)),
    ]
    return pn.Column(
        _tabs(*panels),
        error_pane(state, error_field),
        sizing_mode="stretch_width",
    )
