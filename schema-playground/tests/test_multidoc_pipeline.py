"""Can the pipeline carry several documents through one RDF graph, in every cardinality?

The planned multi-document UI (one editor per document, each processed by the schema its
``$schema`` names, one merged graph, per-target-schema framing back into documents) only
makes sense if the pipeline underneath supports it. These are the experiments for exactly
that, on the example of two persons working at one organization:

* many to many: person docs and an org doc merge into one graph and come back as documents;
* many to one: the flat graph reads as a single org document with the persons nested, over
  the inverse relation (``employees`` is the ``@reverse`` of ``works_for``);
* one to many: a single nested org document unrolls into person documents.

Two findings shaped the transform and are pinned here:

* a type-filtering frame embeds any referenced node it finds, so a reference-valued property
  (``works_for``) would absorb the organization; ``reference_preserving_frame`` derives
  ``"@embed": "@never"`` from ``format: iri-reference`` and is the import default now;
* a node nested under an embedded property is invisible to a type-filtered frame without an
  ``rdf:type``; export derives it from the property schema's ``x-oold-instance-rdf-type``,
  and where the property schema declares none, an untyped nested node stays invisible -
  authored types win either way.
"""

from __future__ import annotations

import json

from schema_playground.mappings import chain
from schema_playground.transform import NQUADS, from_rdf, to_rdf

PERSON_SCHEMA = {
    "$id": "Person.schema.json",
    "title": "Person",
    "type": "object",
    "@context": {
        "schema": "https://schema.org/",
        "id": "@id",
        "type": "@type",
        "name": "schema:name",
        "works_for": {"@id": "schema:worksFor", "@type": "@id"},
    },
    "x-oold-instance-rdf-type": ["schema:Person"],
    "properties": {
        "id": {"type": "string", "format": "iri"},
        "name": {"type": "string"},
        "works_for": {"type": "string", "format": "iri-reference"},
    },
}

#: The organization seen from the other end of the same relation: `employees` is the
#: @reverse of schema:worksFor and embeds Person objects.
ORG_NESTED_SCHEMA = {
    "$id": "Organization.schema.json",
    "title": "Organization",
    "type": "object",
    "@context": {
        "schema": "https://schema.org/",
        "id": "@id",
        "type": "@type",
        "name": "schema:name",
        "employees": {"@reverse": "schema:worksFor", "@type": "@id"},
        "works_for": {"@id": "schema:worksFor", "@type": "@id"},
    },
    "x-oold-instance-rdf-type": ["schema:Organization"],
    "properties": {
        "id": {"type": "string", "format": "iri"},
        "name": {"type": "string"},
        "employees": {
            "type": "array",
            "items": {"type": "object", "properties": {"name": {"type": "string"}}},
        },
    },
}

JANE = {"id": "https://example.org/people/jane", "name": "Jane Doe", "works_for": "https://example.org/orgs/acme"}
JOE = {"id": "https://example.org/people/joe", "name": "Joe Bloggs", "works_for": "https://example.org/orgs/acme"}
ACME = {"id": "https://example.org/orgs/acme", "name": "ACME"}

PERSON_CHAIN = chain(PERSON_SCHEMA, None)
ORG_CHAIN = chain(ORG_NESTED_SCHEMA, None)


def merged_graph() -> str:
    """Three source documents, each exported by its own schema, merged into one graph."""
    parts = [
        to_rdf(JANE, PERSON_CHAIN, format=NQUADS),
        to_rdf(JOE, PERSON_CHAIN, format=NQUADS),
        to_rdf(ACME, ORG_CHAIN, format=NQUADS),
    ]
    return "".join(parts)


def _nodes(document) -> list[dict]:
    if isinstance(document, list):
        return [n for n in document if isinstance(n, dict)]
    graph = document.get("@graph")
    if isinstance(graph, list):
        return [n for n in graph if isinstance(n, dict)]
    return [document]


def test_many_source_documents_merge_into_one_graph():
    graph = merged_graph()
    lines = sorted(line for line in graph.splitlines() if line.strip())
    assert len(lines) == len(set(lines)) == 8, lines  # 3 names + 2 worksFor + 3 types
    assert sum("worksFor" in line for line in lines) == 2


def test_many_to_many_the_person_reading_returns_each_person():
    """Framing by the Person schema fans the graph out into one node per person."""
    result = from_rdf(merged_graph(), PERSON_CHAIN, format=NQUADS)
    persons = sorted(_nodes(result), key=lambda n: str(n.get("id", "")))

    assert len(persons) == 2, result
    assert [p["name"] for p in persons] == ["Jane Doe", "Joe Bloggs"]
    # by default the relation survives as a reference: format iri-reference becomes
    # @embed @never in the frame, so the org node is not absorbed into each person
    assert all(p["works_for"] == "https://example.org/orgs/acme" for p in persons), persons


def test_many_to_one_the_org_reading_nests_the_persons():
    """The same graph read by the nested-organization schema: one document, persons inside.

    `employees` is the @reverse of the very relation the person documents wrote, so this is
    the direction inversion the UI design depends on.
    """
    result = from_rdf(merged_graph(), ORG_CHAIN, format=NQUADS)
    orgs = [n for n in _nodes(result) if n.get("id") == "https://example.org/orgs/acme"]

    assert len(orgs) == 1, result
    org = orgs[0]
    employees = org.get("employees")
    assert isinstance(employees, list) and len(employees) == 2, org
    names = sorted(e.get("name", "") for e in employees if isinstance(e, dict))
    assert names == ["Jane Doe", "Joe Bloggs"], org


def test_one_to_many_a_nested_org_document_unrolls_into_persons():
    """A single authored document with nested employees produces the person documents."""
    nested = {
        "id": "https://example.org/orgs/acme",
        "name": "ACME",
        "employees": [
            # explicit types: export materializes the declared rdf:type on the root only, so
            # a nested node without its own type would be invisible to a type-filtered frame
            {"id": "https://example.org/people/jane", "type": "schema:Person", "name": "Jane Doe"},
            {"id": "https://example.org/people/joe", "type": "schema:Person", "name": "Joe Bloggs"},
        ],
    }
    graph = to_rdf(nested, ORG_CHAIN, format=NQUADS)
    # the @reverse in the context turns nesting into the persons' own worksFor triples
    assert sum("worksFor" in line for line in graph.splitlines()) == 2, graph

    persons = sorted(_nodes(from_rdf(graph, PERSON_CHAIN, format=NQUADS)), key=lambda n: str(n.get("id", "")))
    assert [p.get("name") for p in persons] == ["Jane Doe", "Joe Bloggs"], persons
    assert all(p.get("works_for") == "https://example.org/orgs/acme" for p in persons), persons


def test_nested_types_derive_from_the_property_schema():
    """With `x-oold-instance-rdf-type` on the embedded property, authored types are optional."""
    org_schema = json.loads(json.dumps(ORG_NESTED_SCHEMA))
    org_schema["properties"]["employees"]["items"]["x-oold-instance-rdf-type"] = ["schema:Person"]
    org_chain = chain(org_schema, None)

    nested = {
        "id": "https://example.org/orgs/acme",
        "name": "ACME",
        "employees": [{"id": "https://example.org/people/jane", "name": "Jane Doe"}],
    }
    graph = to_rdf(nested, org_chain, format=NQUADS)
    assert "schema.org/Person" in graph.replace("<", "").replace(">", ""), graph

    persons = [n for n in _nodes(from_rdf(graph, PERSON_CHAIN, format=NQUADS)) if n.get("name") == "Jane Doe"]
    assert len(persons) == 1, persons


def test_a_nested_node_without_a_type_is_invisible_to_the_person_frame():
    """The other finding: nested employees carry no materialized rdf:type, so the
    type-filtered Person reading returns nothing for them."""
    untyped = {
        "id": "https://example.org/orgs/acme",
        "name": "ACME",
        "employees": [{"id": "https://example.org/people/jane", "name": "Jane Doe"}],
    }
    graph = to_rdf(untyped, ORG_CHAIN, format=NQUADS)
    persons = [n for n in _nodes(from_rdf(graph, PERSON_CHAIN, format=NQUADS)) if n.get("name") == "Jane Doe"]
    assert not persons, "nested nodes gained a type; update the docs and drop this pin"


def test_roundtrip_many_to_one_to_many():
    """Documents -> graph -> nested org -> graph -> person documents, ends where it began."""
    once = from_rdf(merged_graph(), ORG_CHAIN, format=NQUADS)
    org = [n for n in _nodes(once) if n.get("id") == "https://example.org/orgs/acme"][0]
    org.pop("@context", None)

    graph_again = to_rdf(org, ORG_CHAIN, format=NQUADS)
    persons = sorted(_nodes(from_rdf(graph_again, PERSON_CHAIN, format=NQUADS)), key=lambda n: str(n.get("id", "")))
    assert [p.get("name") for p in persons] == ["Jane Doe", "Joe Bloggs"], persons
