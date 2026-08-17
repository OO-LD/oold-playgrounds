"""The playground: URL-backed config, the one-directional data flow, and the import boundary."""

import json

import pytest

pytest.importorskip("panel")
pytest.importorskip("param")

import panel as pn  # noqa: F401

from oold.ui import url_config as uc
from oold.ui.playground import config as cfg
from oold.ui.playground.graph import graph_data, shorten
from oold.ui.playground.state import PlaygroundState, schema_filename
from oold.ui.playground.terms import terms_table


class _Location:
    """Stands in for ``pn.state.location``, which only exists inside a served session."""

    def __init__(self, search: str = "") -> None:
        self.search = search


@pytest.fixture
def location(monkeypatch):
    stub = _Location()
    monkeypatch.setattr(uc, "_location", lambda: stub)
    return stub


# -- URL-backed configuration ------------------------------------------------


def test_config_defaults_when_the_url_is_empty(location):
    assert uc.UrlConfig(cfg.PlaygroundConfig).get_config().source_schema == cfg.SOURCE_SCHEMA


def test_config_round_trips_and_always_emits_compressed(location):
    """A generated link stays short, whatever encoding it was written in."""
    manager = uc.UrlConfig(cfg.PlaygroundConfig, param_name="pg")
    original = cfg.PlaygroundConfig(source_instance='{"name": "Jane"}', mapping_set="s:1")

    manager.set_config(original)

    assert "pg=" in location.search
    assert "source_instance" not in location.search, "compressed form must be opaque"
    assert manager.get_config() == original


@pytest.mark.parametrize(
    "mode", [uc.UrlConfigMode.PLAIN_KEYS, uc.UrlConfigMode.JSON, uc.UrlConfigMode.COMPRESSED_BASE64]
)
def test_all_three_encodings_are_read_back(location, mode):
    manager = uc.UrlConfig(cfg.PlaygroundConfig, param_name="pg")
    original = cfg.PlaygroundConfig(mapping_set="https://example.org/sets/a", use_paste=True)

    manager.set_config(original, mode=mode)

    assert manager.has_config()
    assert manager.get_config() == original


def test_config_preserves_unrelated_query_parameters(location):
    location.search = "?theme=dark"
    uc.UrlConfig(cfg.PlaygroundConfig, param_name="pg").set_config(cfg.PlaygroundConfig())
    assert "theme=dark" in location.search


def test_bound_config_writes_on_assignment(location):
    bound = uc.UrlConfig(cfg.PlaygroundConfig, param_name="pg").bind()
    bound.mapping_set = "https://example.org/sets/a"
    assert uc.UrlConfig(cfg.PlaygroundConfig, param_name="pg").get_config().mapping_set == (
        "https://example.org/sets/a"
    )


# -- fields that hold either a URL or a document -----------------------------


@pytest.mark.parametrize(
    ("value", "is_url"),
    [
        ("https://oo-ld.org/latest/schemas/Person.schema.json", True),
        ("http://example.org/a.json", True),
        ('{"title": "Person"}', False),
        ("@prefix schema: <https://schema.org/> .", False),
        ("title: Person\ntype: object", False),
        ("", False),
        ("ftp://example.org/a.json", False),
    ],
)
def test_looks_like_url(value, is_url):
    assert cfg.looks_like_url(value) is is_url


def test_resolve_source_returns_content_unchanged():
    assert cfg.resolve_source('{"a": 1}') == ('{"a": 1}', None)


def test_resolve_source_reports_a_failed_fetch(monkeypatch):
    """A wrong URL must say so rather than quietly rendering the default."""

    def boom(url, timeout=10.0):
        raise OSError("no route to host")

    monkeypatch.setattr(cfg, "fetch", boom)
    text, error = cfg.resolve_source("https://example.invalid/schema.json")

    assert text == ""
    assert "no route to host" in error


def test_parse_document_accepts_json_and_yaml():
    assert cfg.parse_document('{"a": 1}') == ({"a": 1}, None)
    assert cfg.parse_document("a: 1") == ({"a": 1}, None)
    document, error = cfg.parse_document("{not valid")
    assert document is None and "not valid JSON or YAML" in error


def test_schema_filename_follows_the_id():
    assert schema_filename({"$id": "https://oo-ld.org/x/Person.schema.json"}) == "Person.schema.json"
    assert schema_filename({"$id": "Person.schema.json"}) == "Person.schema.json"
    assert schema_filename({}) == "Document.schema.json"


# -- the data flow -----------------------------------------------------------


def test_the_shipped_example_transforms_and_validates():
    state = PlaygroundState()

    assert state.source_schema_error == ""
    assert state.source_instance_error == ""
    assert state.target_schema_error == ""
    assert state.bus_error == ""

    target = json.loads(state.target_instance)
    assert target["full_name"] == "Jane Doe"
    assert target["employer"] == "https://example.org/orgs/acme"
    assert target["mbox"] == "jane@example.org"


def test_selecting_a_mapping_set_changes_the_exported_graph():
    state = PlaygroundState()
    consensus = state.source_rdf

    state.mapping_set = state.available_sets[0]

    assert "schema.org" in consensus
    assert "foaf" in state.source_rdf
    assert state.source_rdf != consensus


def test_the_paste_field_takes_over_the_bus():
    """Pasted RDF replaces the source instance as the input for the right-hand side."""
    state = PlaygroundState()
    from_source = json.loads(state.target_instance)

    state.pasted_rdf = '@prefix schema: <https://schema.org/> .\n<https://example.org/people/bob> schema:name "Bob" .'
    state.use_paste = True

    pasted = json.loads(state.target_instance)
    assert pasted["full_name"] == "Bob"
    assert pasted != from_source

    state.use_paste = False
    assert json.loads(state.target_instance) == from_source


def test_the_right_hand_side_never_writes_back():
    """There is no edge from the transformed instance to the source, so no cycle exists."""
    state = PlaygroundState()
    before = state.source_instance

    state.target_instance = '{"full_name": "Someone Else"}'

    assert state.source_instance == before
    assert "source_instance" not in [
        parameter
        for watcher_list in state.param.watchers.get("target_instance", {}).values()
        for watcher in watcher_list
        for parameter in watcher.parameter_names
    ]


def test_a_broken_document_is_reported_where_it_was_typed():
    state = PlaygroundState()
    state.source_instance = '{"name": '
    assert "not valid JSON or YAML" in state.source_instance_error


def test_unreadable_pasted_rdf_is_reported():
    state = PlaygroundState()
    state.pasted_rdf = "this is not turtle {{{"
    state.use_paste = True
    assert state.bus_error


# -- views -------------------------------------------------------------------


def test_graph_data_makes_edges_from_relations_and_tooltips_from_literals():
    nquads = (
        '<https://example.org/a> <https://schema.org/name> "A" .\n'
        "<https://example.org/a> <https://schema.org/worksFor> <https://example.org/b> .\n"
    )
    data = graph_data(nquads)

    assert {n["id"] for n in data["nodes"]} == {"https://example.org/a", "https://example.org/b"}
    assert len(data["edges"]) == 1, "a literal is a property, not a node"
    assert data["edges"][0]["label"] == "worksFor"
    assert "name: A" in next(n for n in data["nodes"] if n["id"].endswith("/a"))["title"]


def test_graph_data_of_nothing_is_empty():
    assert graph_data("") == {"nodes": [], "edges": []}


@pytest.mark.parametrize(
    ("iri", "expected"),
    [("https://schema.org/name", "name"), ("http://xmlns.com/foaf/0.1/", "0.1"), ("bare", "bare")],
)
def test_shorten(iri, expected):
    assert shorten(iri) == expected


def test_terms_table_shows_the_synonyms():
    """The synonyms are the point: they are invisible in the raw document."""
    schema = json.loads(cfg.SOURCE_SCHEMA)
    table = terms_table([schema])

    assert "foaf:name" in table
    assert "works_for" in table
    assert "foaf" in table, "the mapping set should be named"


def test_terms_table_without_a_context():
    assert "no <code>@context</code>" in terms_table([{"title": "X"}])


# -- the browser build boundary ----------------------------------------------


def test_the_playground_does_not_pull_in_the_heavy_dependencies():
    """Importing oold.model would drag in the backends, which a Pyodide build cannot carry."""
    import subprocess
    import sys

    probe = (
        "import sys; import oold.ui.playground as p;"
        "heavy = [m for m in ('oold.model', 'oold.backend', 'datamodel_code_generator',"
        " 'SPARQLWrapper') if m in sys.modules];"
        "print(','.join(heavy))"
    )
    result = subprocess.run([sys.executable, "-c", probe], capture_output=True, text=True)  # noqa: S603 - the command is built here, not supplied

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "", f"playground imported: {result.stdout.strip()}"
