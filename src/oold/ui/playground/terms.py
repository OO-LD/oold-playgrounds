"""Reading a schema as a table of terms.

The JSON tab shows what the schema *is*; this shows what it *means*: for each term, the IRI it
maps to, and the other IRIs it is also known by. That second column is the whole point of
``x-oold-context`` and is invisible in the raw document, where the synonyms sit in a separate
block from the terms they belong to.

Ports the reading in ``oold-reference-schemas/scripts/macros.py`` (``oold_schema_terms``)
without its repository-specific parts: that version resolves links into a generated
documentation site, which a playground has nothing to link to.
"""

from __future__ import annotations

import html
from typing import Any

from oold.utils.mappings import (
    context_of,
    declared_context,
    is_exact_match,
    set_name,
    synonyms_of,
)

#: Context entries that alias a JSON-LD keyword rather than naming a vocabulary term.
_KEYWORDS = {"@id", "@type", "@value", "@language", "@graph", "@list", "@set", "@none"}


def _is_prefix(value: Any) -> bool:
    """Whether an entry defines a namespace prefix rather than a term."""
    return isinstance(value, str) and value.endswith(("/", "#", ":"))


def _iri_of(definition: Any) -> str:
    """The IRI a term definition points at, whichever slot carries it."""
    if isinstance(definition, str):
        return definition
    if isinstance(definition, dict):
        for slot in ("@id", "@reverse"):
            if isinstance(definition.get(slot), str):
                return definition[slot]
    return ""


def _kind(term: str, definition: Any, required: set[str], properties: dict[str, Any]) -> str:
    if _is_prefix(definition):
        return "prefix"
    if _iri_of(definition) in _KEYWORDS:
        return "keyword alias"
    if isinstance(definition, dict) and "@reverse" in definition:
        return "reverse property"
    if term in properties:
        return "property*" if term in required else "property"
    if term and term[:1].isupper():
        return "class"
    return "term"


def _alternatives(term: str, synonyms: dict[str, dict[str, Any]]) -> str:
    """The synonym column: each other IRI, its mapping predicate and its set."""
    entries = synonyms.get(term) or {}
    if not entries:
        return "<span style='color:#999'>-</span>"
    parts = []
    for iri, fragment in entries.items():
        sssom = fragment.get("x-oold-sssom") or {}
        predicate = sssom.get("predicate_id", "skos:exactMatch")
        marker = "" if is_exact_match(fragment) else f" <em>{html.escape(str(predicate))}</em>"
        raw_sets = sssom.get("mapping_set_id")
        sets = [raw_sets] if isinstance(raw_sets, str) else list(raw_sets or [])
        labels = ", ".join(set_name(s) for s in sets)
        suffix = f" <small>({html.escape(labels)})</small>" if labels else ""
        parts.append(f"<code>{html.escape(iri)}</code>{marker}{suffix}")
    return "<br>".join(parts)


def terms_table(schemas: list[dict[str, Any]]) -> str:
    """An HTML table of the terms of a schema and everything it extends."""
    if not schemas:
        return "<p style='color:#999'>No schema to read.</p>"

    merged = declared_context(schemas)
    sections = []

    for schema in schemas:
        own_context = context_of(schema)
        if not own_context:
            continue
        synonyms = synonyms_of(schema)
        properties = schema.get("properties") or {}
        required = set(schema.get("required") or [])
        title = schema.get("title") or schema.get("$id") or "schema"

        rows = []
        for term, definition in own_context.items():
            if term.startswith("@"):
                continue
            kind = _kind(term, definition, required, properties)
            iri = _iri_of(definition) or (definition if isinstance(definition, str) else "")
            description = ""
            if term in properties and isinstance(properties[term], dict):
                description = properties[term].get("description") or ""
            label = html.escape(term)
            if kind == "property*":
                label += " <sup title='required'>*</sup>"
            rows.append(
                "<tr>"
                f"<td><code>{label}</code>"
                + (f"<br><small>{html.escape(description)}</small>" if description else "")
                + "</td>"
                f"<td><small>{html.escape(kind.rstrip('*'))}</small></td>"
                f"<td><code>{html.escape(str(iri))}</code></td>"
                f"<td>{_alternatives(term, synonyms)}</td>"
                "</tr>"
            )

        if rows:
            sections.append(
                f"<tr><th colspan='4' style='text-align:left;background:rgba(127,127,127,.12)'>"
                f"{html.escape(str(title))}</th></tr>" + "".join(rows)
            )

    if not sections:
        return "<p style='color:#999'>This schema declares no <code>@context</code>.</p>"

    unmapped = [
        name
        for name in (schemas[-1].get("properties") or {})
        if name not in merged
    ]
    note = ""
    if unmapped:
        note = (
            "<p><small>Structural only, with no term mapping: "
            + ", ".join(f"<code>{html.escape(n)}</code>" for n in unmapped)
            + "</small></p>"
        )

    return (
        "<table style='width:100%;border-collapse:collapse;font-size:12px'>"
        "<thead><tr>"
        "<th style='text-align:left'>Term</th>"
        "<th style='text-align:left'>Kind</th>"
        "<th style='text-align:left'>Maps to</th>"
        "<th style='text-align:left'>Also known as</th>"
        "</tr></thead><tbody>" + "".join(sections) + "</tbody></table>"
        "<p><small><code>*</code> marks a required property.</small></p>" + note
    )
