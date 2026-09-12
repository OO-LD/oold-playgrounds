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
| Source schema | the document as JSON or YAML, and its **Terms** table: what each term maps to and what else it is known as |
| Source instance | the document, the RDF it exports as, and that graph drawn |
| Target schema | a second schema, in the same three views |
| Transformed instance | the same data read through the target schema |

Any column can be collapsed; the rest take the space. Validation runs on every change with the same checks as `oold validate`, and errors appear directly beneath the editor that produced them.

## Mapping sets

A term carries one primary IRI in `@context` and any number of synonyms in `x-oold-context`. Selecting a mapping set promotes the synonyms tagged with it, so the instance exports as a different graph without the document changing. A promoted fragment may also carry `@nest`, in which case the set decides the *shape* of the graph, not only its vocabulary (see [oold-schema#135](https://github.com/OO-LD/oold-schema/issues/135)).

## Transformation

The right-hand side is not a second editor; it is the same data read through another schema. Import honours every `exactMatch` synonym the target chain declares, so an incoming graph is recognised whichever of the mapped vocabularies it uses. A `@graph` of several nodes is framed against the target schema first, because compaction alone never re-nests a flattened graph.

The centre **Paste RDF** panel (Turtle or JSON-LD) replaces the source columns as the input for the right-hand side, which keeps the data flow one-directional and free of update loops.

Every editable field accepts either a document or a URL to fetch it from, e.g. `https://oo-ld.org/latest/schemas/Person.schema.json`. The session travels compressed in the URL, so a link reproduces what you are looking at.

## In the browser

```bash
uv run python scripts/build_wasm.py
python -m http.server --directory dist
```

converts the app to a static [Pyodide](https://pyodide.org/) page under `dist/`, no Python server needed. `scripts/check_wasm.py` loads the built page in a real (Playwright) browser and reports what broke; run it against the served page after a build.

## Pending upstream migration

Three modules live here until they move into [`oold-python`](https://github.com/OO-LD/oold-python):

- `mappings.py` - mapping-set selection (`promote`, `mapping_sets`, `chain`). oold 0.20.0 knows `x-oold-context` only for validation (`promoted_terms` marks a term as mapped without choosing); the selection itself is not in the library yet.
- `transform.py` - RDF export/import under a selected set, including `@nest` shape bridging and `@reverse` inversion. Replaces the pre-spec `name*` alias notation still shipped as `oold.utils.transform`, so migrating it is a breaking change there.
- `url_config.py` - URL-backed session state, ported from `opensemantic.base`, whose layering (it depends on `oold`) prevents importing it from there.
