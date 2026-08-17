# Playground

An interactive view of what a schema *means*, rather than what it says. Two schemas sit side
by side with one instance read through each, so that a change of vocabulary is something you
watch happen instead of something you take on trust.

```bash
pip install "oold[playground]"
oold playground
```

or, from a checkout, `make playground`.

## What the columns show

| Column | Content |
|---|---|
| Source schema | the document as JSON or YAML, and its **Terms** table: what each term maps to and what else it is known as |
| Source instance | the document, the RDF it exports as, and that graph drawn |
| Target schema | a second schema, in the same three views |
| Transformed instance | the same data read through the target schema |

Any column can be collapsed; the rest take the space.

## Mapping sets

A term carries one primary IRI in `@context` and any number of synonyms in `x-oold-context`.
Selecting a mapping set promotes the synonyms tagged with it, so the instance exports as a
different graph without the document changing. The dropdown above the RDF view lists the sets
the source schema chain declares, with the consensus (declared) reading first.

The shipped example maps `name` to `schema:name` and, in the `foaf` set, to `foaf:name`.
Switching the dropdown swaps the predicates in the exported graph and leaves the instance
untouched.

## Transformation

The right-hand side is not a second editor; it is the same data read through another schema.
Import honours every `exactMatch` synonym the target chain declares, so an incoming graph is
recognised whichever of the mapped vocabularies it uses, and may mix them. It needs no mapping
set for that reason - a set chooses one reading for *export*, where a document can only have
one.

When the incoming document is a `@graph` of several nodes it is framed against the target
schema, because compaction alone never re-nests a flattened graph.

## Pasting RDF

The centre panel accepts Turtle or JSON-LD directly. Expanding it collapses the two source
columns, because it replaces them: the pasted graph becomes the input the right-hand side
reads. That is also why the flow only runs left to right. A transformed instance never writes
back to the source, so there is no cycle for an edit to chase around the app.

## Loading your own documents

Every editable field takes either a document or a URL to fetch it from:

```
https://oo-ld.org/latest/schemas/Person.schema.json
```

A failed fetch is reported in place rather than falling back to the default, so a wrong URL
looks like a wrong URL.

Validation runs on every change, and errors appear directly beneath the editor that produced
them, using the same checks as `oold validate`.

## Sharing a session

The state travels in the URL, so a link reproduces what you are looking at. It is written
compressed; a hand-written link may also use readable per-field parameters or plain JSON, both
of which are read back and re-emitted compressed.

## In the browser

`make playground-wasm` converts the app to a static [Pyodide](https://pyodide.org/) page under
`dist/playground`, which needs no Python server:

```bash
make playground-wasm
python -m http.server --directory dist/playground
```

The browser installs the locally built wheel, so the build is of the working tree rather than
of the last release.
