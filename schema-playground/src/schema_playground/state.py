"""What the playground computes, and in which direction.

Everything the right-hand side shows is derived from one RDF document, the *bus*. The bus has
exactly one owner: either the source columns export it, or the paste field supplies it. There
is no edge from the right-hand side back to the left, so the dependency graph is acyclic and a
change cannot chase its own tail around the app. That is a property of the wiring rather than
of a guard, which is why nothing here suppresses echoes or compares hashes.

One :meth:`PlaygroundState.recompute` recalculates the derived fields in order. Recomputing
everything is cheap at this size and removes the class of bug where two watchers fire in an
order nobody chose.
"""

from __future__ import annotations

import json
import logging
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import param

from schema_playground import config as cfg
from schema_playground.graph import graph_data
from schema_playground.mappings import chain, mapping_sets, set_name
from schema_playground.transform import JSON_LD, NQUADS, TURTLE, from_rdf, to_rdf

logger = logging.getLogger(__name__)

#: The consensus reading, offered as the first entry of the mapping-set dropdown.
CONSENSUS = "consensus (declared)"


def _triples(nquads: str) -> int:
    return sum(1 for line in (nquads or "").split("\n") if line.strip())


def ref_resolver(base: str | None) -> Callable[[str], dict[str, Any] | None]:
    """Resolve an ``allOf`` ``$ref`` against the URL its schema was loaded from.

    Published schemas reference their neighbours by bare filename, so a chain is only
    followable when the base is known. A schema that was typed in has no base and can only
    follow absolute references; anything else is skipped, which leaves the chain shorter
    rather than failing the whole render - a playground is expected to hold half-written
    input.
    """

    def resolve(ref: str) -> dict[str, Any] | None:
        target = urljoin(base, ref) if base else ref
        if not cfg.looks_like_url(target):
            logger.info("skipping unresolvable $ref %r (no base URL to resolve it against)", ref)
            return None
        try:
            document = json.loads(cfg.fetch(target))
        except Exception as exc:
            logger.warning("could not resolve $ref %s: %s", target, exc)
            return None
        logger.info("resolved $ref %s", target)
        return document

    return resolve


def editor_schema(chain: list[dict[str, Any]]) -> dict[str, Any] | None:
    """A self-contained schema for an editor's inline validation.

    The editor validates against exactly one document and follows no references, so the
    resolved chain is inlined: each member contributes everything but its composition and
    identity keywords, whose work the resolver has already done. JSON-LD and ``x-oold-*``
    keywords ride along; the editor ignores what it does not know.
    """
    members = []
    for member in chain:
        if not isinstance(member, dict):
            continue
        cleaned = {k: v for k, v in member.items() if k not in ("allOf", "$schema", "$id")}
        if cleaned:
            members.append(cleaned)
    if not members:
        return None
    if len(members) == 1:
        return members[0]
    return {"allOf": members}


def meta_schema() -> dict[str, Any] | None:
    """The newest OO-LD meta-schema oold ships, for inline validation of schema documents.

    The base file is used because the entry file is only a reference to it. Its two remote
    references (the 2020-12 meta-schema and the UI keywords) are skipped by the editor,
    which never fetches; the locally defined ``x-oold-*`` keywords still validate.
    """
    try:
        from importlib import resources

        root = resources.files("oold.validation") / "meta"
        latest = sorted(entry.name for entry in root.iterdir() if entry.is_dir())[-1]
        return json.loads((root / latest / "oold-meta-schema-base.json").read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("no meta-schema for editor validation: %s", exc)
        return None


def schema_filename(schema: Any) -> str:
    """The filename a schema expects to be addressed by, taken from its ``$id``."""
    identifier = (schema or {}).get("$id") if isinstance(schema, dict) else None
    if isinstance(identifier, str):
        name = identifier.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".schema.json"):
            return name
    return "Document.schema.json"


def _local_ref(value: Any, schema_file: str) -> Any:
    """Point a document's ``@context`` / ``$schema`` at the local copy of its schema.

    An instance loaded from the web names its schema by URL. The validator runs offline (a
    playground must not fetch on every keystroke, and a browser build often cannot), so the
    reference is redirected to the file written beside it. A context given inline as an
    object is left alone: it needs no resolving.
    """
    if isinstance(value, str):
        return schema_file
    if isinstance(value, list):
        return [_local_ref(item, schema_file) for item in value]
    return value


def validate(document: Any, kind: str, schema: Any = None, chain: list | None = None) -> str:
    """Run the real validator over an in-memory document and report what failed.

    The pipeline reads from disk - it inspects sibling schemas, because a reference cycle is a
    property of the graph rather than of one document - so the whole resolved chain is written
    to a temporary directory, each member under the filename its ``$id`` claims. Writing only
    the schema under test would report its own base as a missing file. This works unchanged in
    a browser, whose filesystem is in memory anyway.
    """
    if not isinstance(document, dict):
        return ""
    try:
        from oold.validation import Options, validate_instance, validate_schema
    except ImportError:
        return ""

    try:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            options = Options(offline=True)

            for member in chain or []:
                if isinstance(member, dict):
                    (base / schema_filename(member)).write_text(json.dumps(member), encoding="utf-8")

            if kind == "schema":
                target = base / schema_filename(document)
                target.write_text(json.dumps(document), encoding="utf-8")
                report = validate_schema(target, options)
            else:
                schema_path = base / schema_filename(schema)
                if not schema_path.exists():
                    schema_path.write_text(json.dumps(schema or {}), encoding="utf-8")
                target = base / "Document.instance.json"
                payload = dict(document)
                payload["$schema"] = schema_path.name
                if "@context" in payload:
                    payload["@context"] = _local_ref(payload["@context"], schema_path.name)
                target.write_text(json.dumps(payload), encoding="utf-8")
                report = validate_instance(target, schema_path, options)
    except Exception as exc:
        return f"validator error: {exc}"

    if report.fatal_error:
        return report.fatal_error
    failures = [c for c in report.checks if c.failed]
    if not failures:
        return ""
    return "\n".join(f"{c.id}: {c.message or c.status}" for c in failures)


class PlaygroundState(param.Parameterized):
    """The session: four documents, a bus, and the views derived from them."""

    # -- inputs. Each may hold a URL or the document itself.
    source_schema = param.String(default=cfg.SOURCE_SCHEMA)
    source_instance = param.String(default=cfg.SOURCE_INSTANCE)
    target_schema = param.String(default=cfg.TARGET_SCHEMA)
    pasted_rdf = param.String(default="")
    use_paste = param.Boolean(default=False)
    paste_format = param.String(default=TURTLE)
    rdf_format = param.String(default=TURTLE)
    mapping_set = param.String(default="")

    # -- derived. Never appear in the dependency list above, which is what keeps this acyclic.
    source_rdf = param.String(default="")
    target_instance = param.String(default="")
    target_rdf = param.String(default="")
    available_sets = param.List(default=[])
    source_graph = param.Dict(default={"nodes": [], "edges": []})
    target_graph = param.Dict(default={"nodes": [], "edges": []})
    #: Self-contained schemas for the editors' inline (Monaco) validation, merged from the
    #: resolved chain because the editor cannot follow references itself.
    source_editor_schema = param.Dict(default=None, allow_None=True)
    target_editor_schema = param.Dict(default=None, allow_None=True)
    source_schema_error = param.String(default="")
    source_instance_error = param.String(default="")
    target_schema_error = param.String(default="")
    bus_error = param.String(default="")

    def __init__(self, **params: Any) -> None:
        super().__init__(**params)
        self.recompute()

    def _load(self, value: str) -> tuple[Any, str]:
        """A field's document and the first problem with it, if any."""
        text, error = cfg.resolve_source(value)
        if error:
            return None, error
        document, parse_error = cfg.parse_document(text)
        return document, parse_error or ""

    def _chain(self, schema: Any, field: str) -> list[dict[str, Any]]:
        if not isinstance(schema, dict):
            return []
        return chain(schema, ref_resolver(cfg.source_base(getattr(self, field))))

    @param.depends(
        "source_schema",
        "source_instance",
        "target_schema",
        "pasted_rdf",
        "use_paste",
        "paste_format",
        "rdf_format",
        "mapping_set",
        watch=True,
    )
    def recompute(self) -> None:
        """Recalculate every derived field, left to right."""
        source_schema, schema_error = self._load(self.source_schema)
        source_instance, instance_error = self._load(self.source_instance)
        target_schema, target_error = self._load(self.target_schema)

        source_chain = self._chain(source_schema, "source_schema")
        target_chain = self._chain(target_schema, "target_schema")

        sets = mapping_sets(source_chain) if source_chain else []
        set_id = self.mapping_set or None
        if set_id and set_id not in sets:
            set_id = None

        # -- the left-hand export
        left_nquads = ""
        source_rdf = ""
        export_error = ""
        if source_chain and isinstance(source_instance, dict) and not instance_error:
            try:
                left_nquads = to_rdf(source_instance, source_chain, set_id=set_id, format=NQUADS)
                source_rdf = to_rdf(source_instance, source_chain, set_id=set_id, format=self.rdf_format)
                logger.info(
                    "exported %d triples under %s",
                    _triples(left_nquads),
                    set_name(set_id) if set_id else "the consensus reading",
                )
            except Exception as exc:
                export_error = f"export failed: {exc}"
                logger.warning("export failed: %s", exc)

        # -- whoever owns the bus supplies it; the paste field replaces the left-hand export
        bus_error = ""
        if self.use_paste:
            if (self.pasted_rdf or "").strip():
                try:
                    bus = _to_nquads(self.pasted_rdf, self.paste_format)
                    logger.info("read %d pasted triples", _triples(bus))
                except Exception as exc:
                    bus, bus_error = "", f"could not read pasted RDF: {exc}"
                    logger.warning("could not read pasted RDF: %s", exc)
            else:
                bus = ""
        else:
            bus = left_nquads
            bus_error = export_error

        # -- the right-hand import, the same code path for both bus owners
        target_instance: Any = None
        target_rdf = ""
        if bus and target_chain:
            try:
                target_instance = from_rdf(bus, target_chain, format=NQUADS)
                target_rdf = _reserialize(bus, self.rdf_format, target_chain)
                logger.info(
                    "imported into %s",
                    target_schema.get("title", "the target schema")
                    if isinstance(target_schema, dict)
                    else "the target schema",
                )
            except Exception as exc:
                bus_error = bus_error or f"import failed: {exc}"
                logger.warning("import failed: %s", exc)

        with param.parameterized.batch_call_watchers(self):
            self.available_sets = sets
            self.source_editor_schema = editor_schema(source_chain)
            self.target_editor_schema = editor_schema(target_chain)
            self.source_rdf = source_rdf
            self.target_rdf = target_rdf
            self.target_instance = _as_instance(target_instance, target_schema)
            self.source_graph = graph_data(left_nquads)
            self.target_graph = graph_data(bus)
            self.source_schema_error = schema_error or validate(source_schema, "schema", chain=source_chain)
            self.source_instance_error = instance_error or validate(
                source_instance, "instance", source_schema, chain=source_chain
            )
            self.target_schema_error = target_error or validate(target_schema, "schema", chain=target_chain)
            self.bus_error = bus_error


def _as_instance(document: Any, schema: Any) -> str:
    """Render an imported document the way an OO-LD instance is normally written.

    The inline ``@context`` the importer produces is the target schema's own, so repeating it
    beside the schema that declares it is noise; an instance names its schema instead.
    """
    if document is None:
        return ""
    if isinstance(document, dict):
        document = {k: v for k, v in document.items() if k != "@context"}
        identifier = schema.get("$id") if isinstance(schema, dict) else None
        if isinstance(identifier, str):
            document = {"$schema": identifier, **document}
    return json.dumps(document, indent=2)


def _to_nquads(text: str, format: str) -> str:
    """Serialized RDF in any accepted notation, as n-quads."""
    if format == NQUADS:
        return text
    if format == JSON_LD:
        from pyld import jsonld

        return jsonld.to_rdf(json.loads(text), {"format": NQUADS})
    from rdflib import Dataset

    dataset = Dataset()
    dataset.parse(data=text, format="turtle")
    return dataset.serialize(format="nquads")


def _reserialize(nquads: str, format: str, schemas: list[dict[str, Any]]) -> str:
    """The bus in the notation the user asked to see."""
    if format == NQUADS:
        return nquads
    if format == JSON_LD:
        from pyld import jsonld

        from schema_playground.mappings import declared_context

        document = jsonld.from_rdf(nquads, {"format": NQUADS, "useNativeTypes": True})
        return json.dumps(jsonld.compact(document, declared_context(schemas)), indent=2)
    from rdflib import Dataset

    dataset = Dataset()
    dataset.parse(data=nquads, format="nquads")
    return dataset.serialize(format="turtle")
