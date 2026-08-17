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

from oold.ui.playground import config as cfg
from oold.ui.playground.graph import graph_data  # noqa: F401 - re-exported for convenience
from oold.ui.playground.terms import terms_table

EDITOR_HEIGHT = 420


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
    ) -> None:
        super().__init__()
        self._state = state
        self._field = field
        self._language = language
        self._writing = False

        self._editor = pn.widgets.CodeEditor(
            value=self._current_text(),
            language=language,
            readonly=readonly,
            sizing_mode="stretch_width",
            height=height,
            theme="github_light_default",
        )
        if not readonly:
            self._editor.param.watch(self._on_edit, "value")
        state.param.watch(self._on_state, field)

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

    def __panel__(self) -> pn.widgets.CodeEditor:
        return self._editor


def error_pane(state: param.Parameterized, field: str) -> pn.viewable.Viewable:
    """The validation report for one editor, directly beneath it."""

    def render(message: str) -> pn.viewable.Viewable:
        if not message:
            return pn.pane.HTML(
                "<div style='color:#2e7d32;font-size:12px'>valid</div>",
                sizing_mode="stretch_width",
                margin=(2, 6),
            )
        body = "<br>".join(line for line in message.splitlines() if line.strip())
        return pn.pane.HTML(
            "<div style='color:#c62828;font-size:12px;white-space:normal'>" + body + "</div>",
            sizing_mode="stretch_width",
            margin=(2, 6),
        )

    return pn.bind(render, state.param[field])


def _tabs(*panels: tuple[str, Any]) -> pn.Tabs:
    return pn.Tabs(*panels, sizing_mode="stretch_both", dynamic=False)


def schema_column(state: param.Parameterized, field: str, error_field: str, chain_of: Callable[[], list]) -> pn.Column:
    """A schema column: the document in two encodings, plus its reading as terms."""
    terms = pn.bind(
        lambda _value: pn.pane.HTML(terms_table(chain_of()), sizing_mode="stretch_width"), state.param[field]
    )
    return pn.Column(
        _tabs(
            ("JSON", DocumentEditor(state, field, "json")),
            ("YAML", DocumentEditor(state, field, "yaml")),
            ("Terms", pn.Column(terms, scroll=True, height=EDITOR_HEIGHT)),
        ),
        error_pane(state, error_field),
        sizing_mode="stretch_both",
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
            "edges": {"font": {"size": 10, "align": "middle"}},
        },
        height=EDITOR_HEIGHT,
        sizing_mode="stretch_width",
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
) -> pn.Column:
    """An instance column: the document, the RDF it exports as, and that graph drawn."""
    rdf_view = pn.widgets.CodeEditor(
        value=getattr(state, rdf_field),
        language="text",
        readonly=True,
        sizing_mode="stretch_width",
        height=EDITOR_HEIGHT,
        theme="github_light_default",
    )
    state.param.watch(lambda event: setattr(rdf_view, "value", event.new), rdf_field)

    rdf_tab: Any = rdf_view if controls is None else pn.Column(controls, rdf_view, sizing_mode="stretch_width")

    panels = [
        ("JSON", DocumentEditor(state, instance_field, "json", readonly=readonly)),
        ("YAML", DocumentEditor(state, instance_field, "yaml", readonly=readonly)),
        ("RDF", rdf_tab),
        ("Graph", graph_pane(state, graph_field)),
    ]
    return pn.Column(
        _tabs(*panels),
        error_pane(state, error_field),
        sizing_mode="stretch_both",
    )
