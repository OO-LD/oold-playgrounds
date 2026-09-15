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

#: The consensus reading (no mapping set applied), the first entry of the mapping dropdown.
CONSENSUS = "as declared (default)"


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

    The base file is used because the entry file is only a reference to it.
    """
    try:
        from importlib import resources

        root = resources.files("oold.validation") / "meta"
        latest = sorted(entry.name for entry in root.iterdir() if entry.is_dir())[-1]
        base = json.loads((root / latest / "oold-meta-schema-base.json").read_text(encoding="utf-8"))
        return with_explicit_recursion(base)
    except Exception as exc:
        logger.warning("no meta-schema for editor validation: %s", exc)
        return None


#: Where a subschema sits inside its parent, per the 2020-12 applicator vocabulary.
_SUBSCHEMA_MAPS = ("properties", "$defs", "definitions", "patternProperties")
_SUBSCHEMA_VALUES = ("items", "additionalProperties", "contains", "propertyNames", "not")
_SUBSCHEMA_LISTS = ("allOf", "anyOf", "oneOf", "prefixItems")


def with_explicit_recursion(base: dict[str, Any]) -> dict[str, Any]:
    """The dialect with its recursion into subschemas spelled out as ``$ref``.

    2020-12 reaches a nested subschema through the core applicator's ``$dynamicRef``, which
    resolves back to the dialect because the base carries ``$dynamicAnchor: "meta"``. Monaco's
    JSON language service implements ``$ref`` but not ``$dynamicRef``, so it offers the
    ``x-oold-*`` keywords at the document root and nowhere else - and ``x-oold-range``, the
    one that constrains what an IRI-valued property points at, is only ever written inside a
    property. The YAML service does implement it, which is why the same document completes
    there and not here.

    Naming each subschema position with a plain ``$ref`` back to the dialect says the same
    thing in the subset Monaco understands. A self-referential ``$ref`` is ordinary: the
    2020-12 meta-schema is recursive in exactly this way.
    """
    identifier = base.get("$id")
    if not isinstance(identifier, str) or not identifier:
        return base

    schema = dict(base)
    properties = dict(schema.get("properties") or {})
    self_ref = {"$ref": identifier}

    for keyword in _SUBSCHEMA_MAPS:
        entry = dict(properties.get(keyword) or {})
        entry.setdefault("type", "object")
        entry["additionalProperties"] = self_ref
        properties[keyword] = entry

    for keyword in _SUBSCHEMA_VALUES:
        if keyword not in properties:
            properties[keyword] = self_ref

    for keyword in _SUBSCHEMA_LISTS:
        entry = dict(properties.get(keyword) or {})
        entry.setdefault("type", "array")
        entry["items"] = self_ref
        properties[keyword] = entry

    schema["properties"] = properties
    return schema


def meta_store() -> dict[str, Any]:
    """Everything a schema editor's references resolve against, offline.

    The meta-schema base defines only the ``x-oold-*`` keywords itself; the core vocabulary
    (``type``, ``properties``, ...) comes from its ``$ref`` to json-schema.org. Without those
    resolvable, a core-keyword mistake is exactly the kind of error that goes silently
    unflagged. jsonschema ships the 2020-12 meta-schemas, so they are registered under their
    real URIs; the OO-LD meta and UI meta likewise, under every version path a document may
    name.
    """
    store: dict[str, Any] = {}
    try:
        from jsonschema_specifications import REGISTRY

        for uri in REGISTRY:
            if "2020-12" in uri:
                store[uri] = REGISTRY.contents(uri)
    except Exception as exc:
        logger.warning("no 2020-12 meta-schemas for editor validation: %s", exc)

    try:
        from importlib import resources

        root = resources.files("oold.validation") / "meta"
        versions = sorted(entry.name for entry in root.iterdir() if entry.is_dir())
        latest = versions[-1]
        # The same rewrite the editor's own schema gets: a $ref landing on the raw base would
        # stop recursing after one level, so only a first-level property would complete.
        base = with_explicit_recursion(
            json.loads((root / latest / "oold-meta-schema-base.json").read_text(encoding="utf-8"))
        )
        ui = json.loads((root / latest / "oold-ui-meta-schema.json").read_text(encoding="utf-8"))
        for version in ("latest", "dev", latest):
            store[f"https://oo-ld.org/{version}/meta/oold-meta-schema.json"] = base
            store[f"https://oo-ld.org/{version}/meta/oold-meta-schema-base.json"] = base
            store[f"https://oo-ld.org/{version}/meta/oold-ui-meta-schema.json"] = ui
    except Exception as exc:
        logger.warning("no OO-LD meta-schemas for editor validation: %s", exc)
    return store


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

                def _prepare(node: dict) -> dict:
                    payload = dict(node)
                    payload["$schema"] = schema_path.name
                    payload["@context"] = (
                        _local_ref(payload["@context"], schema_path.name)
                        if "@context" in payload
                        else schema_path.name
                    )
                    return payload

                graph = document.get("@graph")
                if isinstance(graph, list):
                    # A graph document is a container: each node is an instance of the
                    # schema, so each is validated on its own.
                    failures = []
                    for index, node in enumerate(graph):
                        if not isinstance(node, dict):
                            failures.append(f"@graph[{index}]: not an object")
                            continue
                        target = base / f"Node{index}.instance.json"
                        target.write_text(json.dumps(_prepare(node)), encoding="utf-8")
                        node_report = validate_instance(target, schema_path, options)
                        if node_report.fatal_error:
                            failures.append(f"@graph[{index}]: {node_report.fatal_error}")
                        failures.extend(
                            f"@graph[{index}] {c.id}: {c.message or c.status}"
                            for c in node_report.checks
                            if c.failed
                        )
                    return "\n".join(failures)
                target = base / "Document.instance.json"
                target.write_text(json.dumps(_prepare(document)), encoding="utf-8")
                report = validate_instance(target, schema_path, options)
    except Exception as exc:
        return f"validator error: {exc}"

    if report.fatal_error:
        return report.fatal_error
    failures = [c for c in report.checks if c.failed]
    if not failures:
        return ""
    return "\n".join(f"{c.id}: {c.message or c.status}" for c in failures)


def document_label(text: str, fallback: str) -> str:
    """What a document is called in a selector: a schema's ``$id``, an instance's ``@id``."""
    from schema_playground.graph import shorten

    resolved, error = cfg.resolve_source(text)
    document, parse_error = cfg.parse_document(resolved) if not error else (None, error)
    if isinstance(document, dict):
        for key in ("$id", "@id", "id"):
            value = document.get(key)
            if isinstance(value, str) and value.strip():
                return shorten(value)[:40]
    return fallback


def match_schema(instance: Any, schema_texts: list[str]) -> int:
    """The index of the schema an instance's ``$schema`` names, first schema as fallback.

    Matched by the tail of the schema's ``$id``: instances name their schema relatively
    (``Person.schema.json``) or absolutely, and the ``$id`` may be either too.
    """
    declared = instance.get("$schema") if isinstance(instance, dict) else None
    if isinstance(declared, str) and declared.strip():
        tail = declared.rstrip("/").rsplit("/", 1)[-1]
        for index, text in enumerate(schema_texts):
            resolved, error = cfg.resolve_source(text)
            document, _ = cfg.parse_document(resolved) if not error else (None, None)
            identifier = document.get("$id") if isinstance(document, dict) else None
            if isinstance(identifier, str) and identifier.rstrip("/").rsplit("/", 1)[-1] == tail:
                return index
    return 0


class PlaygroundState(param.Parameterized):
    """The session: documents on both sides, a bus, and the views derived from them.

    Documents live in lists; the scalar params (``source_schema`` and friends) are the
    *selected* document's view, kept in sync both ways so the editors bind to a stable
    field while ``+``/``-``/selection manage the lists.
    """

    # -- the documents. Each entry may hold a URL or the document itself.
    source_schemas = param.List(default=None, item_type=str)
    source_instances = param.List(default=None, item_type=str)
    target_schemas = param.List(default=None, item_type=str)
    source_schema_idx = param.Integer(default=0)
    source_instance_idx = param.Integer(default=0)
    target_schema_idx = param.Integer(default=0)
    target_instance_idx = param.Integer(default=0)

    # -- the selected document, as the editors see it
    source_schema = param.String(default=cfg.SOURCE_SCHEMA)
    source_instance = param.String(default=cfg.SOURCE_INSTANCE)
    target_schema = param.String(default=cfg.TARGET_SCHEMA)
    pasted_rdf = param.String(default="")
    use_paste = param.Boolean(default=False)
    paste_format = param.String(default=TURTLE)
    rdf_format = param.String(default=TURTLE)
    mapping_set = param.String(default="")
    #: Pane titles currently collapsed; mirrored with the split layout by the app.
    collapsed_panes = param.List(default=[], item_type=str)

    # -- derived. Never appear in the dependency list above, which is what keeps this acyclic.
    target_instances = param.List(default=[], item_type=str)
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

    def __init__(self, compute: bool = True, **params: Any) -> None:
        # Both guards must exist before any watcher can fire. _ready suppresses recompute
        # until construction is complete: the seeding below assigns the watched list params,
        # and without the gate each assignment would run the multi-second pipeline once.
        self._ready = False
        self._syncing = False
        # Per-document results of the last recompute, read back by _apply_selection: a
        # dropdown click must not pay for the whole pipeline again.
        self._source_chains: list[list[dict[str, Any]]] = []
        self._target_chains: list[list[dict[str, Any]]] = []
        self._schema_errors: list[str] = []
        self._target_errors: list[str] = []
        self._instance_errors: list[str] = []
        self._instance_labels: list[str] = []
        self._logged_problems = ""
        super().__init__(**params)
        # The scalar params seed the lists (and vice versa), so both single-document use -
        # every existing caller - and list-based construction end up consistent. Guarded:
        # assigning one list fires _sync_views, which would wipe the scalars the *other*
        # lists have not been seeded from yet.
        self._syncing = True
        try:
            if self.source_schemas is None:
                self.source_schemas = [self.source_schema]
            if self.source_instances is None:
                self.source_instances = [self.source_instance]
            if self.target_schemas is None:
                self.target_schemas = [self.target_schema]
        finally:
            self._syncing = False
        self._sync_views()
        self.param.watch(
            self._on_selection, ["source_schema_idx", "source_instance_idx", "target_schema_idx"]
        )
        self._ready = True
        # A server session can defer the first pass to after the page is delivered: the
        # validators and the RDF pipeline cost seconds, and running them before the document
        # exists means seconds of blank page instead of a loading indicator.
        if compute:
            self.recompute()

    @staticmethod
    def _view(documents: list[str], index: int) -> str:
        if not documents:
            return ""
        return documents[max(0, min(index, len(documents) - 1))]

    def _schema_chains(self, texts: list[str]) -> list[list[dict[str, Any]]]:
        chains: list[list[dict[str, Any]]] = []
        for text in texts or []:
            document, error = self._load(text)
            if isinstance(document, dict) and not error:
                chains.append(chain(document, ref_resolver(cfg.source_base(text))))
            else:
                chains.append([])
        return chains

    @param.depends(
        "source_schemas",
        "source_instances",
        "target_schemas",
        "source_schema_idx",
        "source_instance_idx",
        "target_schema_idx",
        "target_instance_idx",
        watch=True,
    )
    def _sync_views(self) -> None:
        """The selected documents, mirrored into the scalar params the editors bind to."""
        if self._syncing:
            return
        self._syncing = True
        try:
            with param.parameterized.batch_call_watchers(self):
                self.source_schema = self._view(self.source_schemas, self.source_schema_idx)
                self.source_instance = self._view(self.source_instances, self.source_instance_idx)
                self.target_schema = self._view(self.target_schemas, self.target_schema_idx)
                self.target_instance = self._view(self.target_instances, self.target_instance_idx)
        finally:
            self._syncing = False

    @param.depends("source_schema", "source_instance", "target_schema", watch=True)
    def _sync_lists(self) -> None:
        """An edit in an editor lands in the selected slot of its list."""
        if self._syncing:
            return
        self._syncing = True
        try:
            for list_name, index_name, value in (
                ("source_schemas", "source_schema_idx", self.source_schema),
                ("source_instances", "source_instance_idx", self.source_instance),
                ("target_schemas", "target_schema_idx", self.target_schema),
            ):
                documents = list(getattr(self, list_name) or [])
                index = max(0, min(getattr(self, index_name), len(documents) - 1)) if documents else 0
                if not documents:
                    documents = [value]
                elif documents[index] != value:
                    documents[index] = value
                else:
                    continue
                setattr(self, list_name, documents)
        finally:
            self._syncing = False

    def _load(self, value: str) -> tuple[Any, str]:
        """A field's document and the first problem with it, if any."""
        text, error = cfg.resolve_source(value)
        if error:
            return None, error
        document, parse_error = cfg.parse_document(text)
        return document, parse_error or ""

    @param.depends(
        "source_schemas",
        "source_instances",
        "target_schemas",
        "pasted_rdf",
        "use_paste",
        "paste_format",
        "rdf_format",
        "mapping_set",
        watch=True,
    )
    def recompute(self) -> None:
        """Recalculate every derived field, left to right, over every document."""
        if not self._ready:
            return
        schema_texts = list(self.source_schemas or [])
        instance_texts = list(self.source_instances or [])
        target_texts = list(self.target_schemas or [])

        source_chains = self._schema_chains(schema_texts)
        target_chains = self._schema_chains(target_texts)

        sets = sorted({s for one_chain in source_chains for s in mapping_sets(one_chain)})
        set_id = self.mapping_set or None
        if set_id and set_id not in sets:
            set_id = None

        # -- every schema is validated, so a later selection change only has to pick
        schema_errors: list[str] = []
        for index, text in enumerate(schema_texts):
            document, load_error = self._load(text)
            schema_errors.append(load_error or validate(document, "schema", chain=source_chains[index]))
        target_errors: list[str] = []
        for index, text in enumerate(target_texts):
            document, load_error = self._load(text)
            target_errors.append(load_error or validate(document, "schema", chain=target_chains[index]))

        # -- the left-hand export: every instance through the schema its $schema names
        left_nquads = ""
        export_errors: list[str] = []
        instance_errors: list[str] = []
        instance_labels: list[str] = []
        for index, text in enumerate(instance_texts):
            instance, error = self._load(text)
            label = document_label(text, f"instance {index}")
            instance_labels.append(label)
            schema_index = match_schema(instance, schema_texts)
            one_chain = source_chains[schema_index] if source_chains else []
            doc_error = error or ""
            if not doc_error and one_chain and isinstance(instance, dict):
                try:
                    left_nquads += to_rdf(instance, one_chain, set_id=set_id, format=NQUADS)
                except Exception as exc:
                    export_errors.append(f"{label}: export failed: {exc}")
                    logger.warning("export of %s failed: %s", label, exc)
                schema_doc, _ = self._load(schema_texts[schema_index]) if schema_texts else (None, "")
                doc_error = doc_error or validate(instance, "instance", schema_doc, chain=one_chain)
            instance_errors.append(doc_error)
        if left_nquads:
            logger.info(
                "exported %d triples from %d document(s) under %s",
                _triples(left_nquads),
                len(instance_texts),
                set_name(set_id) if set_id else "the consensus reading",
            )

        # the RDF pane shows the merged export in the chosen serialization
        source_rdf = ""
        if left_nquads:
            try:
                source_rdf = _reserialize(left_nquads, self.rdf_format, source_chains[0] if source_chains else [])
            except Exception as exc:
                export_errors.append(f"serialization failed: {exc}")

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
            if not bus and not bus_error:
                bus_error = "hint: nothing to read yet - paste Turtle or JSON-LD on the left"
        else:
            bus = left_nquads
            bus_error = "; ".join(export_errors)
            # A "valid" label under an empty output would claim more than is true, so the
            # transformed column says why it is empty instead.
            if not bus and not bus_error:
                if any(instance_errors) or any(schema_errors):
                    bus_error = "hint: no output - fix the source documents first"
                else:
                    bus_error = "hint: the source documents export no RDF (no mapped properties yet)"

        # -- the right-hand import: each target schema frames the bus into its documents
        emitted: list[str] = []
        target_rdf = ""
        if bus:
            for target_index, one_chain in enumerate(target_chains):
                if not one_chain:
                    continue
                target_doc = one_chain[-1]
                try:
                    result = from_rdf(bus, one_chain, format=NQUADS)
                except Exception as exc:
                    bus_error = bus_error or f"import failed: {exc}"
                    logger.warning("import via target %d failed: %s", target_index, exc)
                    continue
                nodes = result.get("@graph") if isinstance(result, dict) else None
                if not isinstance(nodes, list):
                    nodes = [result] if isinstance(result, dict) else []
                nodes = [n for n in nodes if isinstance(n, dict) and set(n) - {"@context"}]
                nodes.sort(key=lambda n: str(n.get("id", n.get("@id", ""))))
                for node in nodes:
                    emitted.append(_as_instance(node, target_doc))
                logger.info(
                    "imported %d document(s) into %s",
                    len(nodes),
                    target_doc.get("title", f"target {target_index}"),
                )
            if target_chains and target_chains[0]:
                try:
                    target_rdf = _reserialize(bus, self.rdf_format, target_chains[0])
                except Exception:
                    target_rdf = bus

        self._log_problems(instance_labels, instance_errors, schema_errors, target_errors, bus_error)

        with param.parameterized.batch_call_watchers(self):
            self.available_sets = sets
            self._source_chains = source_chains
            self._target_chains = target_chains
            self._schema_errors = schema_errors
            self._target_errors = target_errors
            self._instance_errors = instance_errors
            self._instance_labels = instance_labels
            self.source_rdf = source_rdf
            self.target_rdf = target_rdf
            self.target_instances = emitted
            self.target_instance_idx = min(self.target_instance_idx, max(0, len(emitted) - 1))
            self.target_instance = self._view(emitted, self.target_instance_idx)
            self.source_graph = graph_data(left_nquads)
            self.target_graph = graph_data(bus)
            self.bus_error = bus_error
            self._apply_selection(follow=True)

    def _log_problems(self, labels: list[str], *error_groups: Any) -> None:
        """Every current problem, in the log exactly once per change.

        Logged only on change: recompute runs on every keystroke, and repeating an unchanged
        message a hundred times would bury the log's actual content.
        """
        flat: list[str] = []
        instance_errors = error_groups[0]
        for label, error in zip(labels, instance_errors):
            if error:
                flat.append(f"{label}: {error.splitlines()[0]}")
        for group in error_groups[1:]:
            values = group if isinstance(group, list) else [group]
            flat.extend(value.splitlines()[0] for value in values if value and not value.startswith("hint:"))
        combined = "; ".join(flat)
        if combined and combined != self._logged_problems:
            logger.warning("validation: %s", combined)
        self._logged_problems = combined

    def _on_selection(self, *_events: Any) -> None:
        """A dropdown changed: re-pick the per-selection fields from the cached results."""
        if not self._ready:
            return
        follow = any(event.name == "source_instance_idx" for event in _events)
        with param.parameterized.batch_call_watchers(self):
            self._apply_selection(follow=follow)

    def _apply_selection(self, follow: bool = False) -> None:
        """The derived fields that depend on which documents are selected.

        ``follow`` also moves the transformed-side selection to the document answering the
        selected source instance, so the two sides read as one story by default.
        """

        def pick(values: list[str], index: int) -> str:
            return values[max(0, min(index, len(values) - 1))] if values else ""

        self.source_schema_error = pick(self._schema_errors, self.source_schema_idx)
        self.target_schema_error = pick(self._target_errors, self.target_schema_idx)
        selected = pick(self._instance_errors, self.source_instance_idx)
        selected_at = min(self.source_instance_idx, len(self._instance_errors) - 1)
        others = [
            f"{label}: {error}"
            for position, (label, error) in enumerate(zip(self._instance_labels, self._instance_errors))
            if error and position != selected_at
        ]
        self.source_instance_error = selected or ("; ".join(others) if others else "")

        # inline validation follows the schema the selected instance is processed by
        instance, _ = self._load(self._view(list(self.source_instances or []), self.source_instance_idx))
        matched = match_schema(instance, list(self.source_schemas or []))
        self.source_editor_schema = editor_schema(self._source_chains[matched] if self._source_chains else [])
        target_chain = (
            self._target_chains[min(self.target_schema_idx, len(self._target_chains) - 1)]
            if self._target_chains
            else []
        )
        self.target_editor_schema = editor_schema(target_chain)

        if follow and isinstance(instance, dict):
            wanted = instance.get("id") or instance.get("@id")
            if isinstance(wanted, str):
                for index, text in enumerate(self.target_instances or []):
                    document, parse_error = cfg.parse_document(text)
                    if parse_error or not isinstance(document, dict):
                        continue
                    if wanted in (document.get("id"), document.get("@id")):
                        self.target_instance_idx = index
                        self.target_instance = self._view(self.target_instances, index)
                        break


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
