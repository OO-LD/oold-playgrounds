# schema-playground

An interactive view of what an OO-LD schema *means*, rather than what it says. Two schemas sit side by side with one instance read through each, so that a change of vocabulary or of document shape is something you watch happen instead of something you take on trust.

Built with [Panel](https://panel.holoviz.org/) and [panelini](https://github.com/opensemanticworld/panelini), on the released [`oold`](https://pypi.org/project/oold/) validator.

## Run

```bash
uv sync
uv run panel serve app.py --dev
```

Then open <http://localhost:5006/app>.

## What the columns show

| Column | Content |
| --- | --- |
| Source schemas | schema documents as JSON or YAML, each with its **Terms** table: what each term maps to and what else it is known as |
| Source instances | instance documents, the merged RDF they export as, and that graph drawn |
| Target schemas | the schemas of the other reading, in the same three views |
| Transformed instances | the documents each target schema emits from the graph |

Each column holds any number of documents: a selector shows them by their `$id` (schemas) or `@id` (instances), and `+`/`-` add or remove one. Every source instance is processed by the schema its own `$schema` names; all exports merge into one graph; each target schema frames that graph and may emit several documents, sorted by `@id`.

Any column can be collapsed; the rest take the space. Validation runs twice: the JSON and YAML editors ([panelini MonacoEditor](https://github.com/opensemanticworld/panelini/pull/60)) validate inline while typing, with schema-driven completion - instances against the resolved schema chain, schemas against the OO-LD meta-schema plus the full 2020-12 vocabulary (shipped by `jsonschema`, resolved offline) - and the full `oold validate` pipeline reports beneath the editor that produced the problem. A `$schema` pointing at an external URL also works: known metas resolve from the offline store, anything else is fetched live (CORS permitting).

## Mapping sets

A term carries one primary IRI in `@context` and any number of synonyms in `x-oold-context`. Selecting a mapping set promotes the synonyms tagged with it, so the instance exports as a different graph without the document changing. A promoted fragment may also carry `@nest`, in which case the set decides the *shape* of the graph, not only its vocabulary (see [oold-schema#135](https://github.com/OO-LD/oold-schema/issues/135)).

## Transformation

The right-hand side is not a second editor; it is the same data read through another schema. Import honours every `exactMatch` synonym the target chain declares, so an incoming graph is recognised whichever of the mapped vocabularies it uses. A `@graph` of several nodes is framed against the target schema first, because compaction alone never re-nests a flattened graph.

The centre **Paste RDF** panel (Turtle or JSON-LD) replaces the source columns as the input for the right-hand side, which keeps the data flow one-directional and free of update loops.

## Several entities, one graph

Multiple documents per column are the primary form (see above): separate files, related by IRI references, exactly as they would live in a repository. Alternatively one document can hold several entities: JSON has no document-stream notation (JSON Lines exists, but a stream is not a JSON document, so every JSON tool rejects it), so the JSON-LD container for that is a `@graph` array, whose nodes reference each other by `id`. Both forms flow the same way: each node validates on its own (errors name the node, `@graph[1] ...`), everything exports into the one merged graph, and each target schema frames that graph back into documents - two persons and their organization read equally well as person documents (`works_for` kept as a reference) or as one organization with the persons nested through the inverse relation. In YAML a document stream (`---` separators) is read as a `@graph`.

Every editable field accepts either a document or a URL to fetch it from, e.g. `https://oo-ld.org/latest/schemas/Person.schema.json`. The session travels compressed in the URL, so a link reproduces what you are looking at.

## In the browser

```bash
uv run python scripts/build_wasm.py
python -m http.server --directory dist
```

converts the app to a static [Pyodide](https://pyodide.org/) page under `dist/`, no Python server needed. Playwright keeps the claims honest: `tests/test_editor_integration.py` serves the app itself and asserts that the editors validate inline (JSON and YAML, instances and schemas), complete from the schema while typing, fetch an external `$schema`, and highlight JSON, YAML and Turtle; `scripts/check_wasm.py` loads the built browser page and reports what broke.

## Pending upstream migration

Three modules live here until they move elsewhere; each migration is tracked:

- `mappings.py` and `transform.py` - mapping-set selection and the RDF transform: [OO-LD/oold-python#154](https://github.com/OO-LD/oold-python/pull/154). Once released, delete both here and import from `oold.utils`.
- `url_config.py` - URL-backed session state: [opensemanticworld/panelini#43](https://github.com/opensemanticworld/panelini/issues/43). Ported from `opensemantic.base`, whose layering (it depends on `oold`) prevents importing it from there.

The MonacoEditor dependency tracks the branch of [panelini#60](https://github.com/opensemanticworld/panelini/pull/60) (schema store for in-document `$schema` resolution, YAML and Turtle highlighting) until it merges and releases; the browser build ships a wheel built from the same branch.
