"""Transforming instances between schemas through RDF.

These are the cases the previous alias-notation implementation covered, re-encoded in the
form the specification defines. Where the old context wrote a sibling key ``"name*"`` to say
"this term is also known by that IRI", the schema now says so in ``x-oold-context``, keyed by
the synonym IRI and carrying an SSSOM predicate. The inputs and expected outputs are the same,
so the change of notation is not allowed to change what the transformation produces.
"""

import json

import pytest

from oold.utils.mappings import declared_context, mapping_sets, promote
from oold.utils.transform import JSON_LD, NQUADS, TURTLE, from_rdf, to_rdf, transform

SCHEMA_ORG = "http://schema.org/"
DEMO = "https://oo-ld.github.io/demo/"


def _schema(context, synonyms=None, **extra):
    """A minimal OO-LD schema carrying a context and optional synonyms."""
    schema = {"type": "object", "@context": context}
    if synonyms:
        schema["x-oold-context"] = synonyms
    schema.update(extra)
    return schema


# -- a term and a class, each with one synonym -------------------------------


def _person_schema():
    """`name` is `schema:name`, also known as `rdfs:label`; `Person` likewise `ex:Human`."""
    return _schema(
        {
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "schema": "https://schema.org/",
            "ex": "https://another-example.org/",
            "type": "@type",
            "name": "schema:name",
            "Person": "schema:Person",
        },
        {
            "name": {"rdfs:label": {"x-oold-sssom": {"predicate_id": "skos:exactMatch"}}},
            "Person": {"ex:Human": {"x-oold-sssom": {"predicate_id": "skos:exactMatch"}}},
        },
    )


def _test_simple_json():
    """A document written in the synonym vocabulary reads as the primary one."""
    source = _schema({
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "ex": "https://another-example.org/",
        "type": "@type",
        "label": "rdfs:label",
        "Human": "ex:Human",
    })
    instance = {"type": "Human", "label": "Jane Doe"}

    result = transform(instance, [source], [_person_schema()])
    result.pop("@context", None)

    assert result == {"type": "Person", "name": "Jane Doe"}, result


@pytest.mark.benchmark(group="transform")
def test_simple_json(benchmark):
    if benchmark is not None:
        benchmark(_test_simple_json)
    else:
        _test_simple_json()


def test_non_exact_match_is_not_rewritten():
    """A `closeMatch` is not a licence to treat two predicates as the same."""
    target = _schema(
        {
            "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
            "schema": "https://schema.org/",
            "type": "@type",
            "name": "schema:name",
        },
        {"name": {"rdfs:label": {"x-oold-sssom": {"predicate_id": "skos:closeMatch"}}}},
    )
    source = _schema({"rdfs": "http://www.w3.org/2000/01/rdf-schema#", "label": "rdfs:label"})

    result = transform({"label": "Jane Doe"}, [source], [target])

    assert result.get("name") is None, f"closeMatch must not be promoted: {result}"


# -- a graph, including an inverted relation ---------------------------------


def _organization_schema():
    """`employes` is `schema:employes`, also reachable as the inverse of `schema:worksFor`."""
    return _schema(
        {
            "schema": "http://schema.org/",
            "demo": DEMO,
            "type": "@type",
            "id": "@id",
            "name": "schema:name",
            "employes": {"@id": "schema:employes", "@type": "@id"},
        },
        {
            "name": {"demo:full_name": {"x-oold-sssom": {"predicate_id": "skos:exactMatch"}}},
            "employes": {
                "schema:worksFor": {
                    "@reverse": "schema:worksFor",
                    "@type": "@id",
                    "x-oold-sssom": {"predicate_id": "skos:exactMatch"},
                },
                "demo:is_employed_by": {
                    "@reverse": "demo:is_employed_by",
                    "@type": "@id",
                    "x-oold-sssom": {"predicate_id": "skos:exactMatch"},
                },
            },
        },
    )


def _source_graph():
    """Three people and an organization, related in three different ways."""
    return {
        "@context": {
            "schema": "http://schema.org/",
            "demo": DEMO,
            "name": "schema:name",
            "full_name": "demo:full_name",
            "works_for": {"@id": "schema:worksFor", "@type": "@id"},
            "is_employed_by": {"@id": "demo:is_employed_by", "@type": "@id"},
            "employes": {"@id": "schema:employes", "@type": "@id"},
            "type": "@type",
            "id": "@id",
        },
        "@graph": [
            {"id": "demo:person1", "type": "schema:Person", "name": "Person1", "works_for": "demo:organizationA"},
            {
                "id": "demo:person2",
                "type": "schema:Person",
                "full_name": "Person2",
                "is_employed_by": "demo:organizationA",
            },
            {"id": "demo:person3", "type": "schema:Person", "name": "Person3"},
            {
                "id": "demo:organizationA",
                "type": "schema:Organization",
                "name": "organizationA",
                "employes": "demo:person3",
            },
        ],
    }


def _by_id(document):
    nodes = document.get("@graph", [document])
    return {node["id"]: node for node in nodes if isinstance(node, dict) and "id" in node}


def _test_complex_graph():
    """Three spellings of one relation collapse onto the single primary term."""
    graph = _source_graph()

    result = from_rdf(graph, [_organization_schema()], format=JSON_LD)
    nodes = _by_id(result)

    org = nodes["demo:organizationA"]
    employed = org["employes"]
    if isinstance(employed, str):
        employed = [employed]
    employed = sorted(item["id"] if isinstance(item, dict) else item for item in employed)

    assert employed == ["demo:person1", "demo:person2", "demo:person3"], org
    # demo:full_name was a synonym of name, so person2 is named like the others.
    assert nodes["demo:person2"]["name"] == "Person2", nodes["demo:person2"]
    assert nodes["demo:person1"]["name"] == "Person1"


@pytest.mark.benchmark(group="transform")
def test_complex_graph(benchmark):
    if benchmark is not None:
        benchmark(_test_complex_graph)
    else:
        _test_complex_graph()


def test_graph_input_is_framed():
    """A flat graph re-nests under the target schema rather than staying a node list.

    Compaction alone never re-nests, so without a frame the embedded object would surface as
    a sibling of its parent.
    """
    context = {
        "schema": "http://schema.org/",
        "demo": DEMO,
        "type": "@type",
        "id": "@id",
        "name": "schema:name",
        "address": {"@id": "schema:address", "@type": "@id"},
    }
    target = _schema(
        context,
        properties={
            "name": {"type": "string"},
            "address": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
        **{"x-oold-instance-rdf-type": ["schema:Person"]},
    )
    graph = {
        "@context": context,
        "@graph": [
            {"id": "demo:p1", "type": "schema:Person", "name": "Jane", "address": "demo:a1"},
            {"id": "demo:a1", "name": "Somewhere"},
        ],
    }

    result = from_rdf(graph, [target], format=JSON_LD)

    assert result.get("id") == "demo:p1", result
    assert isinstance(result.get("address"), dict), f"address should be embedded: {result}"
    assert result["address"]["name"] == "Somewhere"


# -- export ------------------------------------------------------------------


def test_mapping_set_selects_the_reading():
    """The same instance exports as a different graph once a set is promoted."""
    schema = _schema(
        {"schema": "https://schema.org/", "demo": DEMO, "name": "schema:name"},
        {
            "name": {
                "demo:full_name": {
                    "x-oold-sssom": {
                        "predicate_id": "skos:exactMatch",
                        "mapping_set_id": "https://example.org/sets/demo",
                    }
                }
            }
        },
    )
    instance = {"name": "Jane Doe"}

    assert mapping_sets([schema]) == ["https://example.org/sets/demo"]

    # n-quads rather than turtle: the predicate is written out in full, so the assertion is
    # about the graph and not about which prefixes the serializer chose.
    consensus = to_rdf(instance, [schema], format=NQUADS)
    promoted = to_rdf(instance, [schema], set_id="https://example.org/sets/demo", format=NQUADS)

    assert "https://schema.org/name" in consensus, consensus
    assert f"{DEMO}full_name" in promoted, promoted
    assert "https://schema.org/name" not in promoted, promoted


def test_export_round_trips_under_the_same_context():
    """Instance -> RDF -> instance is lossless for every declared mapping set.

    The property `effective_views.py` proves in oold-reference-schemas: a mapping that drops
    a term or coerces a value fails here rather than shipping quietly.
    """
    schema = _schema(
        {
            "schema": "https://schema.org/",
            "demo": DEMO,
            "id": "@id",
            "name": "schema:name",
        },
        {
            "name": {
                "demo:full_name": {
                    "x-oold-sssom": {
                        "predicate_id": "skos:exactMatch",
                        "mapping_set_id": "https://example.org/sets/demo",
                    }
                }
            }
        },
    )
    instance = {"id": "demo:jane", "name": "Jane Doe"}

    for set_id in [None, *mapping_sets([schema])]:
        context = promote(declared_context([schema]), [schema], set_id)
        reading = _schema(context)
        nquads = to_rdf(instance, [schema], set_id=set_id, format="application/n-quads")
        restored = from_rdf(nquads, [reading], format="application/n-quads")
        restored.pop("@context", None)
        assert restored == instance, f"set={set_id}: {restored} != {instance}"


def test_turtle_and_jsonld_inputs_agree():
    """The two accepted serializations are the same document to the importer."""
    schema = _schema({"schema": "https://schema.org/", "id": "@id", "name": "schema:name"})
    instance = {"id": "https://example.org/jane", "name": "Jane Doe"}

    turtle = to_rdf(instance, [schema], format=TURTLE)
    jsonld_text = to_rdf(instance, [schema], format=JSON_LD)

    from_turtle = from_rdf(turtle, [schema], format=TURTLE)
    from_jsonld = from_rdf(jsonld_text, [schema], format=JSON_LD)

    from_turtle.pop("@context", None)
    from_jsonld.pop("@context", None)
    assert from_turtle == from_jsonld == instance, (from_turtle, from_jsonld)


if __name__ == "__main__":
    test_simple_json(None)
    test_complex_graph(None)
    print(json.dumps({"ok": True}))
