"""Turning a graph into something vis-network can draw.

Only relations between resources become edges. A literal is a property *of* a node, not a
thing in its own right, so drawing it as a node would double the size of the picture and bury
the structure the view exists to show; literals are collected into the node's tooltip instead.
"""

from __future__ import annotations

from typing import Any

_SPLIT_ON = ("#", "/")


def shorten(iri: str) -> str:
    """The last meaningful segment of an IRI, for a label that fits in a node."""
    text = iri.rstrip("/")
    for separator in _SPLIT_ON:
        if separator in text:
            candidate = text.rsplit(separator, 1)[-1]
            if candidate:
                return candidate
    return text


def graph_data(nquads: str) -> dict[str, list[dict[str, Any]]]:
    """``{"nodes": [...], "edges": [...]}`` for :class:`panelini.panels.visnetwork.VisNetwork`."""
    if not (nquads or "").strip():
        return {"nodes": [], "edges": []}

    from rdflib import Dataset, Literal, URIRef

    dataset = Dataset()
    dataset.parse(data=nquads, format="nquads")

    literals: dict[str, list[str]] = {}
    resources: set[str] = set()
    edges: list[dict[str, Any]] = []

    for graph in dataset.graphs():
        for subject, predicate, obj in graph:
            source = str(subject)
            resources.add(source)
            if isinstance(obj, Literal):
                literals.setdefault(source, []).append(f"{shorten(str(predicate))}: {obj}")
            elif isinstance(obj, URIRef) or hasattr(obj, "n3"):
                target = str(obj)
                resources.add(target)
                edges.append({
                    "from": source,
                    "to": target,
                    "label": shorten(str(predicate)),
                    "arrows": "to",
                    "title": str(predicate),
                })

    nodes = []
    for iri in sorted(resources):
        detail = literals.get(iri, [])
        nodes.append({
            "id": iri,
            "label": shorten(iri),
            "title": "\n".join([iri, *detail]),
            "shape": "box" if detail else "ellipse",
        })
    return {"nodes": nodes, "edges": edges}
